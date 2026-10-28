"""
검토용: 조례 개정 이력(history.json)에서 태양광 조항이 바뀐 곳만, 판끼리 바뀐 줄을 뽑아 ordin/hist_diff.txt 로.
사람(또는 Claude)이 이걸 읽고 판별 주거·도로 이격을 ordin/hist_reviewed.json 에 적는다.

  python ordin/hist_diff.py [시군 이름 일부]
"""
import difflib
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
# 이격 기준이 담긴 줄만: 거리(미터) + 주거·도로 낱말. 표고·진입로 폭·면적 같은 일반 개발행위 기준은 뺀다
DIST = re.compile(r"\d\s*(미터|m|ｍ)(?![²㎡])")
TOPIC = re.compile(r"주거|주택|취락|인가|마을|가옥|민가|호\s*(이상|미만|이하)|도로|국도|지방도|군도|이격|태양광|발전")
NOISE = re.compile(r"표고|기준지반고|차도폭|진출입|진입로|도로폭|폭\s*\d|제곱미터|경사|높이|옹벽|절토|성토|축사|가축|분뇨|폐기물")


def lines_of(seq):
    p = os.path.join(BASE, "hist", f"state_{seq}.txt")
    t = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    out = []
    for ln in t.split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln and DIST.search(ln) and TOPIC.search(ln) and not NOISE.search(ln):
            out.append(re.sub(r"<[^>]*>|\(개정[^)]*\)", "", ln)[:240])
    return out


def collapse(states):
    """이격 줄이 같은 판은 하나로 (오래된 판을 남김)"""
    out, prev = [], None
    for s in states:
        k = "|".join(sorted(re.sub(r"\s|제\d+조|[①-⑳]|^\S{1,4}[.)]", "", x) for x in lines_of(s["seq"])))
        if k != prev:
            out.append(s)
            prev = k
    return out


def main():
    hist = json.load(open(os.path.join(BASE, "history.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(os.path.dirname(BASE), "setback_rules.json"), encoding="utf-8"))
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    buf = []
    for who, h in sorted(hist.items()):
        st = collapse(h["states"])
        if len(st) < 2 or q not in who:
            continue
        r = rules.get(who) or {}
        buf.append(f"\n######## {who}  (지금 검토값: 주거 {r.get('tiers')} / 도로 {[(x['kind'], x['m']) for x in r.get('roads') or []]})")
        for a, b in zip(st, st[1:]):
            la, lb = lines_of(a["seq"]), lines_of(b["seq"])
            d = [x for x in difflib.unified_diff(la, lb, lineterm="", n=0) if x[:1] in "+-" and x[:3] not in ("+++", "---")]
            buf.append(f"--- {a['date']} → {b['date']} ({b['kind']}) seq {a['seq']}→{b['seq']}  자동: 주거 {a['resMax']}→{b['resMax']} 도로 {a['roadMax']}→{b['roadMax']}")
            buf += d[:30] or ["  (거리 줄 변화 없음: 문구만 바뀜)"]
    open(os.path.join(BASE, "hist_diff.txt"), "w", encoding="utf-8").write("\n".join(buf))
    print(len(buf), "줄 →", os.path.join(BASE, "hist_diff.txt"))


if __name__ == "__main__":
    main()
