"""
조례 개정 이력 타임라인 → ordin/history_timeline.json (시군 지도에 붙임)

- 과거 판: ordin/hist_reviewed.json (hist_compact.txt 를 읽고 사람이 적은 값)
- 지금 판: setback_rules.json (검토한 조례 기준)
- 판 원문 링크: history.json (법제처 연혁에서 받은 판 번호)

자동 추출은 다른 시설(축사·풍력 등) 거리와 섞여 믿기 어려워서 쓰지 않는다.

  python ordin/hist_build.py
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
DECREE = "2026-09-18"
LAW = "https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq="


def iso(d):
    import re
    m = re.match(r"\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", d or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def main():
    hist = json.load(open(os.path.join(BASE, "history.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(os.path.dirname(BASE), "setback_rules.json"), encoding="utf-8"))
    rev = json.load(open(os.path.join(BASE, "hist_reviewed.json"), encoding="utf-8"))
    lst = json.load(open(os.path.join(BASE, "list.json"), encoding="utf-8"))
    out = {}
    for who, r in rules.items():
        if who.startswith("_") or r.get("tiers") is None:
            continue
        seqs = {s["date"]: s["seq"] for s in (hist.get(who) or {}).get("states", [])}
        rows = []
        for e in rev.get(who, []):
            date, res, road = e[0], e[1], e[2]
            rows.append({"date": date, "res": res, "road": road, "note": e[3] if len(e) > 3 else "",
                         "url": LAW + seqs[date] if date in seqs else None})
        cur = {"date": iso(r.get("enforce")), "res": max((int(t["m"]) for t in r["tiers"]), default=0),
               "road": max((x["m"] or 0 for x in r.get("roads") or []), default=0), "note": "지금 조례",
               "url": r.get("source"), "current": True}
        if rows and rows[-1]["date"] >= cur["date"]:
            rows[-1].update(current=True, url=rows[-1]["url"] or cur["url"])  # 검토값이 지금 판까지 포함
        elif not rows or (rows[-1]["res"], rows[-1]["road"]) != (cur["res"], cur["road"]):
            rows.append(cur)
        else:
            rows[-1]["current"] = True
        if len(rows) < 2:
            continue  # 바뀐 적 없음
        # 9.18 시행령 직전 기준 (9.18 이후 고친 조례는 지금 원문이 이미 새 기준이라 이걸로 비교해야 함)
        pre = [x for x in rows if x["date"] < DECREE and x["res"] is not None]
        peak_res = max((x["res"] or 0 for x in rows), default=0)
        peak_road = max((x["road"] or 0 for x in rows), default=0)
        out[who] = {"timeline": rows, "preDecree": pre[-1] if pre else None, "peakRes": peak_res, "peakRoad": peak_road,
                    "versions": (hist.get(who) or {}).get("versions"), "title": (lst.get(who) or {}).get("title")}
    json.dump(out, open(os.path.join(BASE, "history_timeline.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    eased = [w for w, v in out.items() if v["peakRes"] > (v["timeline"][-1]["res"] or 0)]
    print(f"개정 이력이 있는 곳 {len(out)} / 9.18 전에 주거 이격을 스스로 줄인 곳 {len(eased)}: {', '.join(eased)}")


if __name__ == "__main__":
    main()
