"""
주소 하나를 받아 태양광 적합성을 조사해 JSON으로 출력 (research 페이지용).

  python research.py "경기도 화성시 송산면 고포리 74-40"

판정 항목: 지목(토지 종류) · 한전 계통 여유 · 용도지역(1차 스크리닝) · 주거지 이격(전국 조례+시행령)
기존 모듈(kepco, landuse, setback)을 그대로 쓴다. 모두 추정치이며 최종 확인은 원문·담당 부서에서.
"""
import json
import os
import re
import sys

import kepco
import landuse
import onbid_alert as oa
import setback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 지번 뒤에 붙는 지목 약자 → 이름 (연속지적도 jibun 값 예: "175과", "74-40답")
JIMOK = {"전": "전", "답": "답", "과": "과수원", "목": "목장용지", "임": "임야", "광": "광천지", "염": "염전",
         "대": "대지", "장": "공장용지", "학": "학교용지", "차": "주차장", "주": "주유소용지", "창": "창고용지",
         "도": "도로", "철": "철도용지", "제": "제방", "천": "하천", "구": "구거", "유": "유지", "양": "양어장",
         "수": "수도용지", "공": "공원", "체": "체육용지", "원": "유원지", "종": "종교용지", "사": "사적지",
         "묘": "묘지", "잡": "잡종지"}
GOOD_JIMOK = {"전", "답", "과수원", "목장용지", "임야", "대지", "공장용지", "창고용지", "잡종지", "염전"}


def parcel_info(lu, pnu):
    """연속지적도에서 지목·공시지가·주소."""
    d = lu._get(setback.DATA_URL, {"service": "data", "request": "GetFeature", "format": "json", "errorformat": "json",
                                   "data": "LP_PA_CBND_BUBUN", "attrFilter": f"pnu:=:{pnu}", "geometry": "false",
                                   "attribute": "true", "crs": "EPSG:4326", "size": 1, "page": 1})
    r = d.get("response") or {}
    feats = ((r.get("result") or {}).get("featureCollection") or {}).get("features") or []
    if not feats:
        return {}
    p = feats[0].get("properties") or {}
    jibun = str(p.get("jibun") or "")
    code = jibun[-1:] if jibun and not jibun[-1].isdigit() else ""
    return {"addr": p.get("addr"), "jibun": jibun, "jimok": JIMOK.get(code, code or "-"),
            "jiga": p.get("jiga"), "jigaYear": p.get("gosi_year")}


def search(lu, q):
    """주소 검색 → 후보 목록 [{pnu, addr, x, y}]. 지번 주소 우선, 없으면 도로명."""
    out = []
    for cat in ("parcel", "road"):
        d = lu._get(landuse.SEARCH_URL, {"service": "search", "request": "search", "version": "2.0", "size": 6,
                                         "page": 1, "query": q, "type": "address", "category": cat,
                                         "format": "json", "errorformat": "json", "crs": "EPSG:4326"})
        r = d.get("response") or {}
        for it in ((r.get("result") or {}).get("items") or []) if r.get("status") == "OK" else []:
            a = it.get("address") or {}
            pt = it.get("point") or {}
            pnu = it.get("id") if cat == "parcel" else None
            out.append({"pnu": pnu, "addr": a.get("parcel") or a.get("road"), "road": a.get("road"),
                        "x": pt.get("x"), "y": pt.get("y"), "cat": cat})
        if out:
            break
    return out


def split_addr(addr):
    """'경기도 화성시 만세구 송산면 고포리 74-40' → (시도, 시군구, 읍면동, 리, 지번)"""
    toks = (addr or "").split()
    sido = toks[0] if toks else ""
    rest = toks[1:]
    sgg = []
    while rest and re.search(r"(시|군|구)$", rest[0]) and not re.search(r"(읍|면|동|가|리)$", rest[0]):
        sgg.append(rest.pop(0))
    emd, li, jibun = kepco.parse_address(addr)
    return sido, " ".join(sgg), emd, li, jibun


def verdict(parts):
    bad, cond = [], []
    j = parts["parcel"].get("jimok")
    if j and j != "-" and j not in GOOD_JIMOK:
        bad.append(f"지목이 {j}")
    g = parts["grid"].get("grade")
    if g == "fail":
        bad.append("계통 여유 부족")
    elif g in ("none", "error", None):
        cond.append("계통 정보 없음")
    lu = parts["landuse"].get("grade")
    if lu == "exclude":
        bad.append("용도지역 1차 제외")
    elif lu in ("cond", "none", "error", None):
        cond.append("용도지역 확인 필요")
    sb = parts["setback"].get("grade")
    if sb == "violate":
        bad.append("주거지 이격 미달 추정")
    elif sb in ("none", "error"):
        cond.append("이격 측정 못 함")
    if bad:
        return {"grade": "bad", "label": "부적합 가능성 높음", "why": bad + cond}
    if cond:
        return {"grade": "cond", "label": "조건부 검토 필요", "why": cond}
    return {"grade": "good", "label": "1차 검토상 적합 가능", "why": ["계통·용도·이격 1차 기준 통과"]}


def run(q, pnu_pick=None):
    env, _ = oa.load_settings()
    lu = landuse.Landuse(env["VWORLD_API_KEY"], env.get("VWORLD_DOMAIN") or "coldankim.github.io")
    cands = search(lu, q)
    if not cands:
        return {"ok": False, "error": "주소를 찾지 못했습니다. 시군구·읍면동·지번까지 입력해 보세요."}
    c = next((x for x in cands if pnu_pick and x["pnu"] == pnu_pick), cands[0])
    if not c["pnu"]:  # 도로명으로 찾은 경우: 그 좌표의 필지 PNU를 지번 검색으로 다시 찾음
        c["pnu"] = lu.pnu_by_address(c["addr"])
    if not c["pnu"]:
        return {"ok": False, "error": "필지 번호를 찾지 못했습니다. 지번 주소로 입력해 보세요.", "cands": cands}
    pnu = c["pnu"]
    parcel = parcel_info(lu, pnu)
    addr = parcel.get("addr") or c["addr"]
    sido, sgg, emd, li, jibun = split_addr(addr)

    # 한전 계통 (PNU 앞 5자리 = 법정동 시도·시군구 코드, 산 여부는 한전 조회에 영향 없음)
    kp = kepco.Kepco(env["KEPCO_API_KEY"])
    min_kw, good_kw = int(env.get("SOLAR_MIN_KW") or 100), int(env.get("SOLAR_GOOD_KW") or 300)
    try:
        level, rows = kp.lookup(pnu[:2], pnu[2:5], emd, li, jibun if pnu[10] == "1" else None)
        grid = kepco.summarize(level, rows, min_kw, good_kw)
        grid["lineList"] = sorted(({"subst": r.get("substNm"), "dl": r.get("dlNm"),
                                    "avail": min(kepco.to_kw(r.get("vol1")), kepco.to_kw(r.get("vol2")), kepco.to_kw(r.get("vol3"))),
                                    "vol": [kepco.to_kw(r.get("vol1")), kepco.to_kw(r.get("vol2")), kepco.to_kw(r.get("vol3"))]}
                                   for r in rows), key=lambda x: -x["avail"])[:8]
    except kepco.KepcoError as e:
        grid = {"grade": "error", "label": "계통 조회 실패", "error": str(e)}
    kp.save()

    # 용도지역
    try:
        zones = lu.zones(pnu)
        land = landuse.screen(zones)
    except landuse.LanduseError as e:
        land = {"grade": "error", "label": "용도 조회 실패", "error": str(e), "zones": []}

    # 주거지 이격 (전국 조례 + 시행령)
    sb_tool = setback.Setback(lu)
    rule = setback.rule_for(sb_tool.rules, sido, sgg)
    _, r = setback.find_rule(sb_tool.rules, sido, sgg)
    ordn = {"key": rule["key"], "status": (r or {}).get("status") or "조례 미확인", "others": (r or {}).get("others", ""),
            "note": (r or {}).get("note", ""), "url": (r or {}).get("source"), "enforce": (r or {}).get("enforce")}
    if not rule["limit"]:
        sb = {"grade": "norule", "label": "이격 기준 없음", "basis": rule["basis"]}
    else:
        try:
            sb = sb_tool.measure(pnu, rule)
        except setback.SetbackError as e:
            sb = {"grade": "error", "label": "이격 측정 실패", "error": str(e)}
    sb["ord"] = ordn
    sb_tool.save()
    lu.save()

    parts = {"parcel": {**parcel, "pnu": pnu}, "grid": grid, "landuse": land, "setback": sb}
    return {"ok": True, "query": q, "addr": addr, "road": c.get("road"), "x": c.get("x"), "y": c.get("y"),
            "sido": sido, "sgg": sgg, "emd": emd, "li": li, "jibun": jibun, "minKw": min_kw, "goodKw": good_kw,
            **parts, "verdict": verdict(parts),
            "cands": [{"pnu": x["pnu"], "addr": x["addr"]} for x in cands if x["pnu"]][:6]}


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    pick = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        res = run(q.strip()[:120], pick)
    except Exception as e:  # 화면에 원인을 보여주기 위해 JSON으로 돌려준다
        res = {"ok": False, "error": f"조사 중 오류: {type(e).__name__}: {e}"}
    print(json.dumps(res, ensure_ascii=False))
