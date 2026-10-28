"""
조례 개정 이력 → ordin/history.json

시군 도시·군계획 조례의 과거 판(법제처 연혁)을 받아, 태양광 조문·별표가 바뀐 판만 골라
주거·도로 이격 거리를 자동으로 뽑는다. 9.18 시행령 이전에 시군이 스스로 완화·강화한 흐름을 보려는 것.

  python ordin/history.py            목록·본문은 ordin/hist/, ordin/body/ 에 저장 (다시 돌리면 새 판만 받음)
  python ordin/history.py --offline  받아 둔 것만으로 history.json 다시 만들기

자동 추출이라 틀릴 수 있다 → 판마다 원문 발췌(text)와 원문 링크를 남긴다.
지금 판은 사람이 검토한 setback_rules.json 값과 비교해 맞는지 표시(checked)한다.
"""
import hashlib
import html
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from collect import LAW, body_html, get, html_text, solar_annexes, solar_part  # noqa: E402
from extract import HOUSE, METER, classify  # noqa: E402

HIST = os.path.join(BASE, "hist")
SINCE = "2016-01-01"  # 태양광 이격 조례는 2017년께부터. 그 전 판은 받지 않음
OFFLINE = "--offline" in sys.argv


def iso(d):
    m = re.match(r"\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", d or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def versions(seq):
    """연혁 목록 [{seq, enforce, kind}] (최신순). ordin/hist/<seq>.json 에 저장."""
    p = os.path.join(HIST, f"list_{seq}.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    if OFFLINE:
        return []
    raw = body_html(seq)
    oid = re.search(r'id="ordinId"[^>]*value="(\d+)"', raw) or re.search(r'name="ordinId" value="(\d+)"', raw)
    if not oid:
        return []
    h = get(LAW + "ordinHstListR.do", {"ordinSeq": seq, "ordinId": oid.group(1)}, xhr=True).decode("utf-8", "replace")
    out = []
    for vs, meta in re.findall(r"ordinViewOrdinHst\('(\d+)'.*?<br\s*/?>\s*(\[시행[^<]*)</a>", h, re.S):
        meta = html.unescape(meta)
        d = re.search(r"시행\s*([\d. ]+?)\.?\]", meta)
        k = re.search(r",\s*([^,\]]+)\]\s*$", meta.strip())
        out.append({"seq": vs, "enforce": iso(d.group(1)) if d else "", "kind": k.group(1).strip() if k else ""})
    os.makedirs(HIST, exist_ok=True)
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def solar_state(seq):
    """그 판의 태양광 조문 + 관련 별표 글자. ordin/hist/state_<seq>.txt 에 저장."""
    p = os.path.join(HIST, f"state_{seq}.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read()
    if OFFLINE and not os.path.exists(os.path.join(BASE, "body", seq + ".html")):
        return None
    raw = body_html(seq)
    part = solar_part(html_text(raw))
    ann = solar_annexes(raw, part) if (part or re.search(r"발전시설|태양광", raw)) and not OFFLINE else ""
    txt = (part + ("\n\n" + ann if ann else "")).strip()
    open(p, "w", encoding="utf-8").write(txt)
    return txt


def norm(t):
    """판끼리 비교용: 띄어쓰기·조 번호·별표 받기 실패 문구 차이는 무시"""
    t = re.sub(r"\(별표 받기 실패[^)]*\)", "", t)
    t = re.sub(r"제\s*\d+\s*조(의\s*\d+)?", "제N조", t)
    return re.sub(r"\s+", "", t)


def measure(txt):
    """태양광 조문·별표 글자 → 주거·도로 이격 (자동 추출)"""
    res, road, lines = [], [], []
    for line in re.split(r"\n", txt):
        line = line.strip()
        ms = [int(x.replace(",", "")) for x in METER.findall(line)]
        ms = [m for m in ms if 10 <= m <= 3000]  # 폭 6m 같은 작은 값·면적 오인 제외
        if not ms:
            continue
        kind = classify(line)
        if re.search(r"폭\s*\d+\s*(미터|m)\s*(이상|↑)", line) and kind == "도로" and len(ms) == 1:
            continue  # '폭 6미터 이상 도로' 처럼 도로 폭만 있는 줄
        if kind == "주거":
            hs = [int(n) for n, _ in HOUSE.findall(line)]
            res.append({"m": max(ms), "houses": min(hs) if hs else None})
            lines.append(line[:160])
        elif kind == "도로":
            road.append(max(ms))
            lines.append(line[:160])
    return {"resMax": max((r["m"] for r in res), default=0), "roadMax": max(road, default=0),
            "resTiers": sorted({(r["houses"] or 1, r["m"]) for r in res}), "lines": lines[:6]}


def changes(vs):
    """판 목록(오래된 순) → 태양광 조항이 바뀐 판만. 처음·끝 판을 비교해 다르면 가운데 판을 열어 보는 식(이분 탐색)이라
    모든 판을 받지 않는다. 같은 문구로 되돌아간 경우(A→B→A)는 놓칠 수 있다."""
    memo = {}

    def key(i):
        if i not in memo:
            t = solar_state(vs[i]["seq"])
            memo[i] = (None if t is None else hashlib.md5(norm(t).encode()).hexdigest(), t)
        return memo[i][0]

    points = {0}

    def seg(i, j):
        if j - i < 1 or key(i) == key(j):
            return
        if j == i + 1:
            points.add(j)
            return
        m = (i + j) // 2
        seg(i, m)
        seg(m, j)
    seg(0, len(vs) - 1)
    out = []
    for i in sorted(points):
        key(i)
        txt = memo[i][1]
        if txt is None:
            continue
        v = vs[i]
        m = measure(txt) if txt else {"resMax": 0, "roadMax": 0, "resTiers": [], "lines": []}
        out.append({"seq": v["seq"], "date": v["enforce"], "kind": v["kind"], "has": bool(txt), **m,
                    "url": f"https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq={v['seq']}"})
    return out, len(memo)


def one(who, o, rules):
    vs = [v for v in versions(o["seq"]) if v["enforce"] >= SINCE] or          [{"seq": o["seq"], "enforce": iso(o["enforce"]), "kind": ""}]
    vs.sort(key=lambda v: v["enforce"])
    states, opened = changes(vs)
    # 지금 판의 자동 추출이 사람이 검토한 값과 맞는지 (맞으면 과거 판 추출도 믿을 만함)
    r = rules.get(who) or {}
    if states and r.get("tiers") is not None:
        cur = states[-1]
        res_ok = cur["resMax"] == max((int(t["m"]) for t in r["tiers"]), default=0)
        road_ok = cur["roadMax"] == max((x["m"] or 0 for x in r.get("roads") or []), default=0)
        return {"states": states, "autoMatches": res_ok and road_ok, "versions": len(vs), "opened": opened}
    return {"states": states, "autoMatches": None, "versions": len(vs), "opened": opened}


def main():
    from concurrent.futures import ThreadPoolExecutor
    lst = json.load(open(os.path.join(BASE, "list.json"), encoding="utf-8"))
    rules = json.load(open(os.path.join(os.path.dirname(BASE), "setback_rules.json"), encoding="utf-8"))
    out, done = {}, 0
    # 법제처 부담을 줄이려고 동시에 3곳까지만 (각자 요청 사이 1.2초)
    with ThreadPoolExecutor(3) as ex:
        futs = {ex.submit(one, who, o, rules): who for who, o in sorted(lst.items())}
        for f in futs:
            who = futs[f]
            try:
                out[who] = f.result()
            except Exception as e:
                print("실패", who, e, flush=True)
                continue
            done += 1
            if done % 10 == 0:
                print(f"{done}/{len(lst)} {who} 판 {out[who]['versions']} (열어 본 판 {out[who]['opened']}) → 태양광 변경 {len(out[who]['states'])}", flush=True)
    json.dump(out, open(os.path.join(BASE, "history.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    print("판", sum(v["versions"] for v in out.values()), "열어 본 판", sum(v["opened"] for v in out.values()),
          "/ 태양광 조항이 바뀐 적 있는 곳", sum(1 for v in out.values() if len(v["states"]) > 1),
          "/ 지금 판 자동추출 일치", Counter(v["autoMatches"] for v in out.values()))


if __name__ == "__main__":
    main()
