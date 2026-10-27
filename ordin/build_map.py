"""시군 지도 데이터: 브이월드 시군구 경계(LT_C_ADSIGG_INFO) + setback_rules.json → sgg_map.json

  python ordin/build_map.py           경계는 ordin/sgg_raw.json 이 있으면 그대로 씀
  python ordin/build_map.py --fetch   브이월드에서 경계를 새로 받음 (행정구역이 바뀌었을 때)

결과(sgg_map.json)는 서버 앱 폴더에 올리고 map.php 가 비밀번호 확인 뒤 내보낸다.
- features: 시군구 경계(간략화) + 적용 조례 키
- rules: 조례 키별 9.18 시행령 전후 비교 (주거 이격 이전/현재, 도로 이격, 바뀐 점)
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, ROOT)
import setback  # noqa: E402

RAW = os.path.join(BASE, "sgg_raw.json")
OUT = os.path.join(ROOT, "sgg_map.json")
TOL = 0.0012  # 간략화 허용 오차(도). 약 100m — 전국·시도 단위로 볼 때 티가 안 나는 정도


def fetch():
    from deploy_server import read_env
    import landuse
    env = read_env(os.path.join(ROOT, "API_key.env"))
    lu = landuse.Landuse(env["VWORLD_API_KEY"], env.get("VWORLD_DOMAIN") or "coldankim.github.io")
    d = lu._get(setback.DATA_URL, {"service": "data", "request": "GetFeature", "format": "json",
                                   "data": "LT_C_ADSIGG_INFO", "geomFilter": "BOX(124,33,132,39)", "size": 1000,
                                   "page": 1, "geometry": "true", "attribute": "true", "crs": "EPSG:4326"})
    fs = d["response"]["result"]["featureCollection"]["features"]
    json.dump(fs, open(RAW, "w", encoding="utf-8"), ensure_ascii=False)
    return fs


def _dp(pts, tol):
    """Douglas-Peucker (반복형)"""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = (dx * dx + dy * dy) or 1e-18
        best, bi = 0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
            d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best:
                best, bi = d, i
        if bi > 0 and best > tol * tol:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def _area(r):
    return abs(sum(r[i][0] * r[i - 1][1] - r[i - 1][0] * r[i][1] for i in range(len(r)))) / 2


def simplify(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    out = []
    for poly in polys:
        rings = []
        for j, ring in enumerate(poly):
            r = [[round(x, 4), round(y, 4)] for x, y in _dp(ring, TOL)]
            if len(r) >= 4 and _area(r) > TOL * TOL * 4:
                rings.append(r)
            elif j == 0:
                break  # 바깥 테두리가 너무 작으면 (작은 섬) 통째로 뺀다
        if rings:
            out.append(rings)
    if not out:  # 섬 하나뿐인 곳(울릉군 등)도 남게 가장 큰 조각은 간략화만 약하게
        big = max(polys, key=lambda p: _area(p[0]))
        out = [[[[round(x, 4), round(y, 4)] for x, y in _dp(big[0], TOL / 4)]]]
    return {"type": "MultiPolygon", "coordinates": out}


def rule_info(key, r):
    """화면용 조례 요약 + 9.18 전후 값"""
    if r is None:
        return {"status": "조례 미확인", "relaxed": None, "items": []}
    ch = r.get("change") or {}
    tiers = r.get("tiers")
    return {"title": r.get("title"), "status": r.get("status"), "enforce": r.get("enforce"), "url": r.get("source"),
            "tiers": tiers, "tiersAfter": None if tiers is None else _capped(tiers), "roads": r.get("roads") or [],
            "others": r.get("others", ""), "note": r.get("note", ""), "relaxed": ch.get("relaxed"),
            "items": ch.get("items") or [], "resBefore": ch.get("resBefore"), "resAfter": ch.get("resAfter"),
            "roadBefore": ch.get("roadBefore"), "amended": bool(r.get("amendedAfterDecree"))}


def _capped(tiers):
    sys.path.insert(0, BASE)
    from build_rules import capped_tiers
    return capped_tiers(tiers)


def main():
    fs = fetch() if "--fetch" in sys.argv or not os.path.exists(RAW) else json.load(open(RAW, encoding="utf-8"))
    rules = setback.load_rules()
    feats, used = [], {}
    for f in sorted(fs, key=lambda f: f["properties"]["sig_cd"]):
        p = f["properties"]
        name = p["full_nm"]
        sido, sgg = name.split(" ", 1) if " " in name else (name, "")
        key, r = setback.find_rule(rules, sido, sgg)
        used[key] = rule_info(key, r)
        feats.append({"type": "Feature", "properties": {"cd": p["sig_cd"], "n": name, "k": key},
                      "geometry": simplify(f["geometry"])})
    out = {"type": "FeatureCollection", "features": feats, "rules": used,
           "decree": rules.get("_시행령", {}), "checked": rules.get("_시행령", {}).get("checked")}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    pts = sum(len(r) for f in feats for poly in f["geometry"]["coordinates"] for r in poly)
    print(f"{len(feats)}개 시군구, 조례 {len(used)}개, 점 {pts:,}개, {os.path.getsize(OUT) / 1e6:.2f}MB → {OUT}")


if __name__ == "__main__":
    main()
