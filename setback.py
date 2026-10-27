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
import re
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


# 행정구역 이름이 바뀐 곳: 물건 주소의 시도명 → 조례를 낸 시도명
SIDO_ALIAS = {"전라남도": "전남광주통합특별시", "광주광역시": "전남광주통합특별시",
              "강원도": "강원특별자치도", "전라북도": "전북특별자치도"}


def find_rule(rules, sido, sgg):
    """물건 주소(시도, 시군구) → 조례 기준표 항목. 시군 조례 → (광역시 자치구 등) 시도 조례 순."""
    sg = (sgg or "").split(" ")[0]
    for sd in (sido, SIDO_ALIAS.get(sido)):
        if sd and sg and f"{sd} {sg}" in rules:
            return f"{sd} {sg}", rules[f"{sd} {sg}"]
    if sg and not sg.endswith("구"):  # 시군 이름이 전국에서 하나뿐이면 시도 표기가 달라도 찾는다
        # (고성군처럼 겹치면 안 함. 서구·남구 같은 자치구는 다른 광역시 것과 섞이므로 제외)
        hits = [k for k in rules if k.split(" ")[-1] == sg and " " in k]
        if len(hits) == 1:
            return hits[0], rules[hits[0]]
    sds = [sido, SIDO_ALIAS.get(sido)]
    if SIDO_ALIAS.get(sido, sido) == "전남광주통합특별시":  # 통합 전 조례: 광주 자치구는 옛 광주광역시, 그 밖은 옛 전라남도
        sds.append("(구)광주광역시" if sido == "광주광역시" or sg.endswith("구") else "(구)전라남도")
    for sd in sds:
        if sd in rules:  # 광역시·특별시 자치구는 시 조례를 따름
            return sd, rules[sd]
    return f"{sido} {sg}".strip(), None


def rule_for(rules, sido, sgg):
    """시군 조례 기준 → 실제 적용 기준(시행령 상한 반영).
    tiers: [{houses, m}] — 'houses호 이상 모인 주거지에서 m 미만이면 미달'. 시행령: houses≥5, m≤200."""
    key, r = find_rule(rules, sido, sgg)
    if r is None or r.get("tiers") is None:
        return {"limit": NATIONAL_MAX_M, "houses": NATIONAL_MIN_HOUSES,
                "tiers": [{"houses": NATIONAL_MIN_HOUSES, "m": NATIONAL_MAX_M}],
                "basis": "조례 미확인 → 시행령 상한(5호 이상 200m) 적용", "key": key}
    eff = {}
    for t in r["tiers"]:
        h = max(int(t.get("houses") or 1), NATIONAL_MIN_HOUSES)
        m = min(int(t["m"]), NATIONAL_MAX_M)
        eff[h] = max(eff.get(h, 0), m)
    # 적은 호수 기준의 거리가 더 크면, 많은 호수 무리에도 그 거리가 적용됨
    tiers = []
    for h in sorted(eff):
        m = max(v for hh, v in eff.items() if hh <= h)
        if not tiers or m > tiers[-1]["m"]:  # 거리가 같으면 앞 단계에 이미 포함
            tiers.append({"houses": h, "m": m})
    if not tiers:
        return {"limit": 0, "houses": None, "tiers": [], "basis": r.get("status") or "조례에 주거 이격 기준 없음", "key": key}
    raw = " / ".join(f"{t.get('houses') or 1}호↑ {t['m']}m" for t in r["tiers"])
    capped = " / ".join(f"{t['houses']}호↑ {t['m']}m" for t in tiers)
    basis = f"조례 {raw}" + ("" if raw == capped else f" → 시행령 적용 {capped}")
    return {"limit": max(t["m"] for t in tiers), "houses": min(t["houses"] for t in tiers), "tiers": tiers,
            "basis": basis, "key": key}


def rule_before(rules, sido, sgg):
    """9.18 시행령 이전 기준 (조례 원문 그대로): 주거 tiers(1호부터·200m 초과 포함) + 도로 이격.
    '이전엔 불가, 지금은 가능' 비교용. 조례를 확인 못 했으면 None."""
    key, r = find_rule(rules, sido, sgg)
    if r is None or r.get("tiers") is None:
        return None
    tiers = [{"houses": max(int(t.get("houses") or 1), 1), "m": int(t["m"])} for t in r["tiers"]]
    roads = r.get("roads") or []
    return {"limit": max((t["m"] for t in tiers), default=0), "houses": min((t["houses"] for t in tiers), default=None),
            "tiers": tiers, "roads": roads, "key": key,
            "basis": "9.18 이전 조례 원문: " + (" / ".join(f"{t['houses']}호↑ {t['m']}m" for t in tiers) or "주거 이격 없음")
                     + ("" if not roads else " · 도로 " + ", ".join(f"{x['kind']} {x['m']}m" if x["m"] else x["kind"] for x in roads)),
            "uncertain": bool(r.get("amendedAfterDecree"))}


def ord_summary(rules, sido, sgg):
    """화면용 조례 요약 (출처·기타 기준 + 9.18 시행령으로 바뀐 점)."""
    key, r = find_rule(rules, sido, sgg)
    r = r or {}
    return {"key": key, "status": r.get("status") or "조례 미확인", "others": r.get("others", ""),
            "note": r.get("note", ""), "url": r.get("source"), "enforce": r.get("enforce"),
            "change": (r.get("change") or {}).get("items") or [], "relaxed": (r.get("change") or {}).get("relaxed"),
            "amendedAfterDecree": bool(r.get("amendedAfterDecree"))}


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

    def parcel(self, pnu):
        """필지 경계 → (외곽 링 목록, 중심 lon, lat). 중심은 지도 표시용으로 따로 캐시."""
        parcel = self._features({"data": "LP_PA_CBND_BUBUN", "attrFilter": f"pnu:=:{pnu}", "geometry": "true",
                                 "attribute": "false"})
        rings = [ring for f in parcel for ring in _rings(f.get("geometry"))]
        if not rings:
            return [], None, None
        lon0 = sum(p[0] for ring in rings for p in ring) / sum(len(r) for r in rings)
        lat0 = sum(p[1] for ring in rings for p in ring) / sum(len(r) for r in rings)
        self.cache[f"loc|{pnu}"] = {"t": time.time(), "r": [round(lat0, 6), round(lon0, 6)]}
        return rings, lon0, lat0

    def locate(self, pnu):
        """필지 중심 [lat, lon] (지도에 물건 점 찍기용). 경계가 없으면 None."""
        hit = self.cache.get(f"loc|{pnu}")
        if hit and time.time() - hit["t"] < CACHE_TTL:
            return hit["r"]
        _, lon0, lat0 = self.parcel(pnu)
        return [round(lat0, 6), round(lon0, 6)] if lon0 is not None else None

    def _houses(self, lon0, lat0, proj, prings, buf):
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
        return houses, len(blds) >= 3000  # 3,000동에서 잘리면 먼 쪽 주택이 빠졌을 수 있음

    def _roads(self, lon0, lat0, proj, prings, reach, roads):
        """조례 도로 이격 항목별로 가장 가까운 해당 등급 도로까지 거리 (표준노드링크 = 도로 중심선)."""
        want = [(r, road_match(r["kind"])) for r in roads]
        far = max((r["m"] or 0 for r, (ok, _) in want if ok), default=0)
        links = []
        if far:
            feats = self._features({"data": ROAD_LAYER, "geomFilter": f"POINT({lon0} {lat0})",
                                    "buffer": int(min(far + reach + 50, 2000)), "geometry": "true",
                                    "attribute": "true"}, pages=3)
            for f in feats:
                rank = str((f.get("properties") or {}).get("rd_rank_h") or "")
                g = f.get("geometry") or {}
                lines = [g.get("coordinates") or []] if g.get("type") == "LineString" else g.get("coordinates") or []
                links.append((rank, [[proj(p) for p in ln] for ln in lines]))
        out = []
        for r, (ok, missing) in want:
            row = {"kind": r["kind"], "m": r["m"]}
            if not r["m"] or not ok:
                row.update(d=None, grade="unknown",
                           why="거리 확인 필요" if not r["m"] else "농어촌도로·면도 등은 지도 데이터에 없어 측정 못 함")
                out.append(row)
                continue
            d = min((_line_to_parcel(ln, prings) for rank, lines in links if ok(rank) for ln in lines), default=None)
            row["d"] = None if d is None else round(d)
            row["grade"] = "violate" if d is not None and d < r["m"] else "ok"
            if missing:
                row["why"] = "농어촌도로·면도는 측정 못 함 (나머지 도로만 잼)"
            out.append(row)
        return out

    def measure(self, pnu, rule, before=None):
        """필지(pnu) 주변 주거지 측정. 반환: 판정 dict.
        before(rule_before 결과)를 주면 9.18 이전 조례 원문 기준 판정(주거 1호부터·200m 초과·도로 이격)과
        전후 비교(change.code: new 새로 가능 / ok 원래 가능 / still 여전히 미달 / unknown)도 붙인다."""
        tiers = rule.get("tiers") or [{"houses": rule["houses"], "m": rule["limit"]}]
        key = f"{pnu}|" + ",".join(f"{t['houses']}:{t['m']}" for t in tiers)
        if before:
            key += "|b" + ",".join(f"{t['houses']}:{t['m']}" for t in before["tiers"]) + "|" + \
                   ",".join(f"{r['kind']}:{r['m']}" for r in before["roads"])
        hit = self.cache.get(key)
        if hit and time.time() - hit["t"] < CACHE_TTL:
            return hit["r"]
        rings, lon0, lat0 = self.parcel(pnu)
        if not rings:
            res = {"grade": "none", "label": "이격 측정불가", "why": "필지 경계 없음"}
        else:
            proj = _projector(lon0, lat0)
            prings = [[proj(p) for p in ring] for ring in rings]
            reach = max(math.hypot(x, y) for ring in prings for x, y in ring)
            # 주거지 무리를 제대로 세려고 기준거리보다 넉넉히(＋300m) 가져온다
            buf = int(min(rule["limit"] + reach + 300, 2000))
            far = int(min(max(buf, ((before or {}).get("limit") or 0) + reach + 300), 2000))
            houses, cut = self._houses(lon0, lat0, proj, prings, far)
            near = houses
            if cut and far > buf:  # 넓게 받다 잘렸으면 지금 기준은 가까운 범위로 다시 받아 정확히
                near, _ = self._houses(lon0, lat0, proj, prings, buf)
            res = _judge(near, rule) if rule["limit"] else \
                {"grade": "norule", "label": "이격 기준 없음", "basis": rule["basis"]}
            res["loc"] = [round(lat0, 6), round(lon0, 6)]
            if before:
                b = _judge(houses, before) if before["tiers"] else {"grade": "ok", "why": "주거 이격 없음"}
                b = {k: b[k] for k in ("grade", "why", "nearest", "cluster") if k in b}
                b["roads"] = self._roads(lon0, lat0, proj, prings, reach, before["roads"])
                b["basis"] = before["basis"]
                if cut:
                    b["cut"] = True
                res["before"] = b
                res["change"] = change_of(res, b, before.get("uncertain"))
        self.cache[key] = {"t": time.time(), "r": res}
        return res


ROAD_LAYER = "LT_L_MOCTLINK"  # 브이월드 표준노드링크 (rd_rank_h: 고속도로·일반국도·지방도·특별·광역시도·시·군도 …)


def road_match(kind):
    """조례 도로 종류 → (도로등급 판별 함수 또는 None, 측정 못 하는 종류가 섞였는지).
    '도로', '주요도로', '도로법 도로', '2차로 이상 도로'처럼 일반적인 말은 표준노드링크 도로 전체로 본다."""
    missing = bool(re.search(r"농어촌도로|면도|리도|농도", kind))
    if re.search(r"(?<!농어촌)(?<!고속)도로|교통시설", kind):
        return (lambda rank: True), missing
    spec = []
    if "고속" in kind:
        spec.append("고속")
    if re.search(r"(?<!고속)국도", kind):
        spec.append("국도")
    if "지방도" in kind:
        spec += ["지방도", "광역시도"]
    if re.search(r"군도|(?<!광역)시도", kind):
        spec.append("군도")
    if not spec:
        return None, missing
    return (lambda rank: any(s in rank for s in spec)), missing


def change_of(now, before, uncertain=False):
    """9.18 시행령 전후 비교 → {code, label, why}"""
    roads_bad = [r for r in before.get("roads") or [] if r.get("grade") == "violate"]
    roads_unk = [r for r in before.get("roads") or [] if r.get("grade") == "unknown" or r.get("why")]
    n = now.get("grade")
    if n not in ("ok", "violate", "norule"):
        return {"code": "unknown", "label": "전후 비교 불가"}
    reasons = (["주거 " + (before.get("why") or "")] if before.get("grade") == "violate" else []) + \
        [f"{r['kind']} {r['d']}m < {r['m']}m" for r in roads_bad]
    if n == "violate":
        return {"code": "still", "label": "여전히 이격 미달", "why": " · ".join(reasons)}
    if reasons:
        return {"code": "new", "label": "9.18로 새로 가능", "why": "이전 기준 미달: " + " · ".join(reasons),
                "uncertain": bool(uncertain)}
    if roads_unk or before.get("cut"):
        return {"code": "ok", "label": "원래 가능 추정", "why": "일부 도로는 측정 못 함", "partial": True}
    return {"code": "ok", "label": "원래 가능"}


def _judge(houses, rule):
    tiers = rule.get("tiers") or [{"houses": rule["houses"], "m": rule["limit"]}]
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
    clusters = [(min(houses[i][2] for i in idx), len(idx)) for idx in groups.values()]  # (거리, 호수)
    near_any = min((h[2] for h in houses), default=None)
    base = {"limit": limit, "houses": need, "basis": rule["basis"],
            "nearestHouse": round(near_any) if near_any is not None else None}
    worst = None  # 기준을 가장 크게 어기는 (부족한 거리, 거리, 호수, 기준m)
    best = None   # 기준 호수 이상인 주거지 중 가장 가까운 것 (설명용)
    for d, cnt in clusters:
        for t in tiers:
            if cnt >= t["houses"]:
                if best is None or d < best[0]:
                    best = (d, cnt, t["m"])
                if d < t["m"] and (worst is None or t["m"] - d > worst[0]):
                    worst = (t["m"] - d, d, cnt, t["m"])
    if worst:
        _, d, cnt, m = worst
        return {**base, "grade": "violate", "label": "이격 미달 추정", "nearest": round(d), "cluster": cnt,
                "why": f"{cnt}호 주거지 {round(d)}m < 기준 {m}m"}
    if best is None:
        return {**base, "grade": "ok", "label": "이격 충족 추정", "why": f"주변에 {need}호 이상 주거지 없음"}
    d, cnt, m = best
    return {**base, "grade": "ok", "label": "이격 충족 추정", "nearest": round(d), "cluster": cnt,
            "why": f"{cnt}호 주거지 {round(d)}m ≥ 기준 {m}m"}


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


def _line_to_parcel(line, rings):
    """도로 선 ↔ 필지 경계 최단 거리 (선이 필지를 지나면 0에 가깝다)"""
    if not line:
        return float("inf")
    d = min(_dist_to_parcel(p, rings) for p in line)
    for r in rings:
        for q in r:
            for i in range(len(line) - 1):
                d = min(d, _seg_dist(q, line[i], line[i + 1]))
    return d
