"""
조례 본문(ordin/body/*.txt)에서 태양광 발전시설 입지 기준을 뽑아 ordin/extracted.json 으로 저장.

- 태양광이 언급된 조문(제N조 단위)만 잘라서, 줄(호·목) 단위로 거리·호수·경사도를 찾는다.
- 분류: 주거 / 도로 / 하천 / 철도 / 관광 / 문화유산 / 공공시설 / 경사도 / 기타
- 자동 추출이라 틀릴 수 있다 → 원문 줄(text)을 함께 남기고, 애매하면 review=True
"""
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
KINDS = [
    ("주거", r"주거|주택|취락|마을|가옥|민가|거주|호 이상|호 미만"),
    ("도로", r"도로|고속국도|국도|지방도|군도|시도|농어촌도로"),
    ("하천", r"하천|저수지|호소|댐|수변"),
    ("철도", r"철도"),
    ("관광", r"관광"),
    ("문화유산", r"문화재|문화유산|국가유산|전통사찰|사적"),
    ("공공시설", r"공공시설|학교|공원|의료|요양"),
]
METER = re.compile(r"(\d{1,3}(?:,\d{3})*|\d+)\s*(?:미터|m|ｍ)(?![a-zA-Z²㎡])")
HOUSE = re.compile(r"(\d+)\s*호\s*(이상|미만|이하|초과)?")
SLOPE = re.compile(r"(?:평균\s*)?경사도[^\d\n]{0,15}(\d+(?:\.\d+)?)\s*(?:도|°)")
ART = re.compile(r"\n\s*제\s*\d+\s*조(?:의\s*\d+)?\s*[\(（]")


def solar_articles(text):
    """태양광이 들어간 조문 덩어리들."""
    starts = [m.start() for m in ART.finditer(text)] + [len(text)]
    out = []
    for i in range(len(starts) - 1):
        seg = text[starts[i]:starts[i + 1]]
        if "태양광" in seg:
            out.append(seg.strip())
    if not out and "태양광" in text:  # 조문 구분을 못 찾으면 언급 주변만
        for m in re.finditer("태양광", text):
            out.append(text[max(0, m.start() - 300): m.start() + 1500])
    return out


def classify(line):
    for name, pat in KINDS:
        if re.search(pat, line):
            return name
    return "기타"


def analyze(text):
    arts = solar_articles(text)
    items = []
    for art in arts:
        for line in re.split(r"\n", art):
            line = line.strip()
            if not line or len(line) < 6:
                continue
            ms = [int(x.replace(",", "")) for x in METER.findall(line)]
            sl = SLOPE.findall(line)
            if not ms and not sl:
                continue
            ent = {"text": line[:300]}
            if sl:
                ent.update(kind="경사도", degree=float(sl[0]))
            if ms:
                ent.update(kind=ent.get("kind") or classify(line), meters=ms)
                hs = HOUSE.findall(line)
                if hs:
                    ent["houses"] = [{"n": int(n), "op": op or "이상"} for n, op in hs]
            items.append(ent)
    return arts, items


def summarize(items, arts):
    res = [x for x in items if x.get("kind") == "주거"]
    s = {"주거": None, "도로": None, "기타": {}}
    review = []
    if res:
        tiers = []
        for x in res:
            m = max(x["meters"]) if len(x["meters"]) == 1 else max(x["meters"])
            hs = x.get("houses") or []
            if hs:
                for h in hs:
                    tiers.append({"m": m, "n": h["n"], "op": h["op"]})
            else:
                tiers.append({"m": m, "n": None, "op": None})
        s["주거"] = tiers
        if any(len(x["meters"]) > 1 for x in res):
            review.append("주거 조항에 거리가 여러 개")
    for k in ("도로", "하천", "철도", "관광", "문화유산", "공공시설", "기타"):
        vals = sorted({m for x in items if x.get("kind") == k for m in x["meters"]})
        if vals:
            if k == "도로":
                s["도로"] = vals
            else:
                s["기타"][k] = vals
    slopes = sorted({x["degree"] for x in items if x.get("kind") == "경사도"})
    if slopes:
        s["기타"]["경사도(도)"] = slopes
    if arts and not items:
        review.append("태양광 조항은 있으나 거리 기준을 못 찾음")
    return s, review


def main():
    lst = json.load(open(os.path.join(BASE, "list.json"), encoding="utf-8"))
    out = {}
    for name, arr in lst.items():
        o = arr[0]
        p = os.path.join(BASE, "body", o["seq"] + ".txt")
        if not os.path.exists(p):
            continue
        text = open(p, encoding="utf-8").read()
        issuer = re.match(r"\[[^\]]*\]\[([^\]\d]+?)(?:조례|규칙)", o.get("meta", ""))
        arts, items = analyze(text)
        summ, review = summarize(items, arts)
        byeol = bool(re.search(r"발전시설[^\n]{0,80}별표|별표[^\n]{0,30}발전시설|태양광[^\n]{0,80}별표", text))
        status = "조문" if arts else ("별표" if byeol else "없음")
        if status == "별표":
            review.append("기준이 첨부 별표에 있음")
        out[name] = {"issuer": issuer.group(1) if issuer else name, "title": o["title"], "seq": o["seq"],
                     "enforce": o["enforce"], "status": status, "summary": summ, "items": items,
                     "review": review, "url": f"https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq={o['seq']}"}
    json.dump(out, open(os.path.join(BASE, "extracted.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    print(Counter(v["status"] for v in out.values()), "검토 필요", sum(1 for v in out.values() if v["review"]))


if __name__ == "__main__":
    main()
