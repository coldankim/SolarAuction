"""
태양광 이격거리 추정 (주거지 기준)

2026.9.18 시행 「신재생에너지법 시행령」 개정(기후에너지환경부 보도자료 2026.8.11)에 따라
 - 도로 이격거리는 지자체가 정할 수 없음 → 적용하지 않음
 - 주거지(주택 5호 이상)로부터 최대 200m 까지만 정할 수 있음 → 조례가 더 엄격해도 200m로 낮춰 적용
 - 건물지붕형·자가소비·주민참여형은 적용 제외
시군별 조례 기준은 setback_rules.json 에 둔다 (조사일·출처 포함, 기준이 없으면 상한 200m/5호를 보수적으로 적용).

측정: 브이월드 연속지적도(필지 경계)와 건물정보(주택 용도)로 필지 경계 ↔ 주택 사이 직선거리를 계산하고,
주택끼리 CLUSTER_LINK_M 이내로 이어진 무리를 '주거지'로 보아 호수를 센다. 어디까지나 추정치다.
"""
import json
import math
import os
import time

import landuse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_FILE = os.path.join(BASE_DIR, "setback_rules.json")
CACHE_FILE = os.path.join(BASE_DIR, "setback_cache.json")
CACHE_TTL = 30 * 86400
DATA_URL = "https://api.vworld.kr/req/data"

NATIONAL_MAX_M = 200   # 시행령 상한 (주거지)
NATIONAL_MIN_HOUSES = 5
CLUSTER_LINK_M = 70    # 주택 중심 간 이 거리 이내면 같은 주거지로 봄 (조례의 '부지 경계 50m 이내 연결'을 근사)
HOUSE_USE_PREFIX = ("01", "02")  # 건축물 주용도: 01 단독주택, 02 공동주택


def load_rules():
    if not os.path.exists(RULES_FILE):
        return {}
    with open(RULES_FILE, encoding="utf-8") as f:
        return json.load(f)


def rule_for(rules, sido, sgg):
    """시군 조례 기준 → 실제 적용 기준 (시행령 상한 반영)."""
    key = f"{sido} {(sgg or '').split(' ')[0]}".strip()
    r = rules.get(key)
    if r is None or r.get("residence_m") is None:
        return {"limit": NATIONAL_MAX_M, "houses": NATIONAL_MIN_HOUSES, "basis": "조례 미확인 → 시행령 상한 적용",
                "key": key}
    if not r["residence_m"]:
        return {"limit": 0, "houses": None, "basis": r.get("status") or "조례 이격 기준 없음", "key": key}
    limit = min(int(r["residence_m"]), NATIONAL_MAX_M)
    houses = max(int(r.get("min_houses") or NATIONAL_MIN_HOUSES), NATIONAL_MIN_HOUSES)
    basis = f"조례 {r['residence_m']}m" + (f" → 상한 {NATIONAL_MAX_M}m" if r["residence_m"] > NATIONAL_MAX_M else "")
    return {"limit": limit, "houses": houses, "basis": basis, "key": key}


class SetbackError(Exception):
    pass


class Setback:
    def __init__(self, lu):
        """lu: landuse.Landuse (브이월드 호출·중계·재시도 재사용)"""
        self.lu = lu
        self.cache = {}
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, encoding="utf-8") as f:
                self.cache = json.load(f)
        self.rules = load_rules()

    def save(self):
        now = time.time()
        self.cache = {k: v for k, v in self.cache.items() if now - v["t"] < CACHE_TTL}
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)

    def _features(self, params, pages=1):
        feats = []
        for page in range(1, pages + 1):
            try:
                d = self.lu._get(DATA_URL, {"service": "data", "request": "GetFeature", "format": "json",
                                            "errorformat": "json", "crs": "EPSG:4326", "size": 1000,
                                            "page": page, **params})
            except landuse.LanduseError as e:
                raise SetbackError(str(e))
            r = d.get("response") or {}
            if r.get("status") == "NOT_FOUND":
                break
            if r.get("status") != "OK":
                raise SetbackError(f"브이월드 데이터 오류: {r.get('status')} {r.get('error')}")
            got = ((r.get("result") or {}).get("featureCollection") or {}).get("features") or []
            feats.extend(got)
            if len(got) < 1000:
                break
        return feats

    def measure(self, pnu, rule):
        """필지(pnu) 주변 주거지 측정. 반환: 판정 dict."""
        key = f"{pnu}|{rule['limit']}|{rule['houses']}"
        hit = self.cache.get(key)
        if hit and time.time() - hit["t"] < CACHE_TTL:
            return hit["r"]
        parcel = self._features({"data": "LP_PA_CBND_BUBUN", "attrFilter": f"pnu:=:{pnu}", "geometry": "true",
                                 "attribute": "false"})
        rings = [ring for f in parcel for ring in _rings(f.get("geometry"))]
        if not rings:
            res = {"grade": "none", "label": "이격 측정불가", "why": "필지 경계 없음"}
        else:
            lon0 = sum(p[0] for ring in rings for p in ring) / sum(len(r) for r in rings)
            lat0 = sum(p[1] for ring in rings for p in ring) / sum(len(r) for r in rings)
            proj = _projector(lon0, lat0)
            prings = [[proj(p) for p in ring] for ring in rings]
            reach = max(math.hypot(x, y) for ring in prings for x, y in ring)
            # 주거지 무리를 제대로 세려고 기준거리보다 넉넉히(＋300m) 가져온다
            buf = int(min(rule["limit"] + reach + 300, 2000))
            blds = self._features({"data": "LT_C_BLDGINFO", "geomFilter": f"POINT({lon0} {lat0})",
                                   "buffer": buf, "geometry": "true", "attribute": "true"}, pages=3)
            houses = []
            for b in blds:
                use = str((b.get("properties") or {}).get("usability") or "")
                if not use.startswith(HOUSE_USE_PREFIX):
                    continue
                pts = [proj(p) for ring in _rings(b.get("geometry")) for p in ring]
                if not pts:
                    continue
                cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
                dist = min(_dist_to_parcel(p, prings) for p in pts)
                houses.append((cx, cy, dist))
            res = _judge(houses, rule)
        self.cache[key] = {"t": time.time(), "r": res}
        return res


def _judge(houses, rule):
    limit, need = rule["limit"], rule["houses"]
    # 주택 무리(주거지) 묶기: 단일 연결
    n = len(houses)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            if math.hypot(houses[i][0] - houses[j][0], houses[i][1] - houses[j][1]) <= CLUSTER_LINK_M:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    best = None  # (거리, 호수) : 기준 호수 이상인 주거지 중 가장 가까운 것
    for idx in groups.values():
        if len(idx) >= need:
            d = min(houses[i][2] for i in idx)
            if best is None or d < best[0]:
                best = (d, len(idx))
    near_any = min((h[2] for h in houses), default=None)
    base = {"limit": limit, "houses": need, "basis": rule["basis"],
            "nearestHouse": round(near_any) if near_any is not None else None}
    if best is None:
        return {**base, "grade": "ok", "label": "이격 충족 추정",
                "why": f"주변에 {need}호 이상 주거지 없음"}
    dist, cnt = round(best[0]), best[1]
    if dist < limit:
        return {**base, "grade": "violate", "label": "이격 미달 추정", "nearest": dist, "cluster": cnt,
                "why": f"{cnt}호 주거지 {dist}m < 기준 {limit}m"}
    return {**base, "grade": "ok", "label": "이격 충족 추정", "nearest": dist, "cluster": cnt,
            "why": f"{cnt}호 주거지 {dist}m ≥ 기준 {limit}m"}


def _rings(geom):
    """GeoJSON Polygon/MultiPolygon → 외곽 링 목록 [[(lon,lat),...], ...]"""
    if not geom:
        return []
    t, c = geom.get("type"), geom.get("coordinates") or []
    if t == "Polygon":
        return [c[0]] if c else []
    if t == "MultiPolygon":
        return [poly[0] for poly in c if poly]
    return []


def _projector(lon0, lat0):
    kx = 111320 * math.cos(math.radians(lat0))
    ky = 110540
    return lambda p: ((p[0] - lon0) * kx, (p[1] - lat0) * ky)


def _seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _inside(p, ring):
    x, y = p
    inside = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % len(ring)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


def _dist_to_parcel(p, rings):
    if any(_inside(p, r) for r in rings):
        return 0.0
    return min(_seg_dist(p, r[i], r[(i + 1) % len(r)]) for r in rings for i in range(len(r)))
