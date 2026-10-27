"""검토 결과(ordin/rules_reviewed.json) + 전국 조례 목록(ordin/list.json) → setback_rules.json

- 검토한 곳: 조례 원문 기준 tiers(호수별 거리), 기타 이격 기준(others), 비고(note)
- 태양광 조항이 없는 곳: tiers [] (주거 이격 없음)
- 확인 못 한 곳 / 목록에 없는 곳: tiers null → 판정 시 시행령 상한(5호 이상 200m)을 보수적으로 적용

9.18 시행령 전후 비교용 (지도·"새로 가능" 판정)
- roads: 조례의 도로 이격 [{kind, m, extra}] — 시행령 이전에는 적용, 지금은 지자체가 정할 수 없음
- change: 시행령으로 바뀐 점 (주거 이격 상한 200m, 5호 미만 주택 제외, 도로 이격 폐지)
- 9.18 이후에 개정·시행된 조례는 지금 원문이 이미 바뀐 기준일 수 있어 amendedAfterDecree 표시
"""
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
CHECKED = "2026-10-02"
DECREE_DATE = "2026-09-18"
NATIONAL_MAX_M, NATIONAL_MIN_HOUSES = 200, 5

ROAD_WORD = re.compile(r"도로|국도|지방도|군도|면도|리도|농도|고속|교통시설")
# 자동으로 읽기 어려운 곳은 직접 적음 (조례 원문 확인)
ROAD_OVERRIDE = {
    "전북특별자치도 무주군": [{"kind": "주요도로", "m": None, "extra": "별표 29 표에 따름 (거리 직접 확인 필요)"}],
}


def iso(d):
    """'2026. 9. 18' → '2026-09-18'"""
    m = re.match(r"\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", d or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def parse_roads(others):
    """'도로(왕복2차로↑) 200m · 관광지 200m' → [{'kind': '도로(왕복2차로↑)', 'm': 200, 'extra': ''}]"""
    out = []
    for seg in (others or "").split(" · "):
        if not ROAD_WORD.search(seg):
            continue
        m = re.search(r"(\d[\d,]*)\s*m(?!\s*↑)", seg)  # '폭 6m↑'처럼 ↑ 붙은 건 도로 폭이지 이격거리가 아님
        if not m:
            out.append({"kind": seg.strip(), "m": None, "extra": "거리 확인 필요"})
            continue
        kind = seg[:m.start()].strip()
        if "(" not in kind:  # '도로법 도로·관광지·공공체육시설' → 도로 항목만
            kind = "·".join(t for t in kind.split("·") if ROAD_WORD.search(t)) or kind
        extra = seg[m.end():].strip().strip("()")
        out.append({"kind": kind, "m": int(m.group(1).replace(",", "")), "extra": extra})
    return out


def capped_tiers(tiers):
    """시행령 적용 후 기준 (setback.rule_for 와 같은 계산)"""
    eff = {}
    for t in tiers:
        h = max(int(t.get("houses") or 1), NATIONAL_MIN_HOUSES)
        eff[h] = max(eff.get(h, 0), min(int(t["m"]), NATIONAL_MAX_M))
    res = []
    for h in sorted(eff):
        m = max(v for hh, v in eff.items() if hh <= h)
        if not res or m > res[-1]["m"]:
            res.append({"houses": h, "m": m})
    return res


def change_of(tiers, roads):
    """시행령 전후 차이 요약. tiers None(확인 못 함)이면 알 수 없음."""
    if tiers is None:
        return {"relaxed": None, "items": ["조례 기준 확인 못 함"]}
    after = capped_tiers(tiers)
    items = []
    res_before = max((int(t["m"]) for t in tiers), default=0)
    res_after = max((t["m"] for t in after), default=0)
    if res_before > res_after:
        items.append(f"주거 이격 최대 {res_before:,}m → {res_after}m")
    small = [t for t in tiers if int(t.get("houses") or 1) < NATIONAL_MIN_HOUSES]
    if small:
        h = min(int(t.get("houses") or 1) for t in small)
        items.append(f"{h}~{NATIONAL_MIN_HOUSES - 1}호 주택 이격 폐지 (5호 이상 주거지만)")
    road_ms = [r["m"] for r in roads if r["m"]]
    if roads:
        items.append(f"도로 이격 폐지" + (f" (최대 {max(road_ms):,}m)" if road_ms else ""))
    return {"relaxed": bool(items), "items": items, "resBefore": res_before, "resAfter": res_after,
            "roadBefore": max(road_ms, default=0)}


def main():
    lst = json.load(open(os.path.join(BASE, "list.json"), encoding="utf-8"))
    rev = json.load(open(os.path.join(BASE, "rules_reviewed.json"), encoding="utf-8"))
    out = {"_시행령": {
        "note": "2026.9.18 시행 재생에너지법 시행령: 태양광은 주거지(주택 5호 이상)로부터 최대 200m까지만, 도로 이격은 정할 수 없음. "
                "지붕형·자가소비·주민참여형 제외. 이보다 엄격한 조례는 완화해야 함",
        "date": DECREE_DATE,
        "source": "https://mcee.go.kr/home/web/board/read.do?menuId=10598&boardId=1883230&boardMasterId=939",
        "checked": CHECKED}}
    for who, o in sorted(lst.items()):
        url = f"https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq={o['seq']}"
        base = {"title": o["title"], "enforce": o["enforce"], "source": url, "checked": CHECKED}
        if iso(o["enforce"]) >= DECREE_DATE:
            base["amendedAfterDecree"] = True  # 지금 원문이 이미 9.18 이후 기준일 수 있음
        if who in rev:
            r = rev[who]
            status = "확인 못 함" if r["tiers"] is None else ("주거 이격 없음" if not r["tiers"] else "조례 기준")
            roads = ROAD_OVERRIDE.get(who, parse_roads(r.get("others", "")))
            out[who] = {**base, "tiers": r["tiers"], "roads": roads, "others": r.get("others", ""),
                        "note": r.get("note", ""), "status": status, "change": change_of(r["tiers"], roads)}
        else:
            out[who] = {**base, "tiers": [], "roads": [], "others": "", "note": "도시·군계획 조례에 태양광 이격 조항 없음",
                        "status": "조항 없음", "change": {"relaxed": False, "items": []}}
    json.dump(out, open(os.path.join(ROOT, "setback_rules.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    items = {k: v for k, v in out.items() if not k.startswith("_")}
    print(len(items), Counter(v["status"] for v in items.values()))
    print("시행령으로 완화된 곳", sum(1 for v in items.values() if v["change"]["relaxed"]),
          "/ 9.18 이후 개정 시행", sum(1 for v in items.values() if v.get("amendedAfterDecree")))


if __name__ == "__main__":
    main()
