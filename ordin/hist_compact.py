"""검토용 요약: 시군마다 이격 숫자가 바뀐 판만, 주거·도로 거리 줄을 짧게 → ordin/hist_compact.txt"""
import collections
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from hist_diff import lines_of  # noqa: E402
from hist_build import OTHER  # noqa: E402

NUM = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(미터|m|ｍ|호)")
HW = re.compile(r"주거|주택|취락|인가|마을|가옥|민가|\d\s*호")
RW = re.compile(r"도로|국도|지방도|군도|고속")


def rel(seq):
    out = []
    for ln in lines_of(seq):
        bare = re.sub(r"\([^()]*\)", "", ln)
        if OTHER.search(bare) or not (HW.search(ln) or RW.search(ln)):
            continue
        out.append(ln)
    return out


def sig(seq):
    return collections.Counter((bool(HW.search(l)), bool(RW.search(l)),
                                tuple(n.replace(",", "") + u[:1] for n, u in NUM.findall(l))) for l in rel(seq))


def short(l):
    l = re.sub(r"「[^」]*」|\([^()]{25,}\)", "", l)
    return l[:150]


def main():
    hist = json.load(open(os.path.join(BASE, "history.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(os.path.dirname(BASE), "setback_rules.json"), encoding="utf-8"))
    buf, n = [], 0
    for who, h in sorted(hist.items()):
        keep, prev = [], None
        for s in h["states"]:
            k = sig(s["seq"])
            if k != prev:
                keep.append(s)
                prev = k
        if len(keep) < 2:
            continue
        r = rules.get(who) or {}
        buf.append(f"\n## {who} | 지금검토: 주거 {[(t.get('houses') or 1, t['m']) for t in (r.get('tiers') or [])]} 도로 {[x['m'] for x in r.get('roads') or []]}")
        prev_l = []
        for s in keep:
            ls = [short(l) for l in rel(s["seq"])]
            buf.append(f"[{s['date']} {s['seq']}]" + ("" if ls else " (이격 줄 없음)"))
            buf += [f" -{t}" for t in prev_l if t not in ls] + [f" +{t}" for t in ls if t not in prev_l]
            prev_l = ls
            n += 1
    open(os.path.join(BASE, "hist_compact.txt"), "w", encoding="utf-8").write("\n".join(buf))
    print(n, "판", len(buf), "줄")


if __name__ == "__main__":
    main()
