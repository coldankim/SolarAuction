"""
전국 도시·군계획 조례 수집 → 태양광 조문 + 태양광 관련 별표(한글) 글자까지 모아 ordin/review/<지자체>.txt 로 저장.

  python ordin/collect.py          목록 갱신 + 바뀐 조례만 다시 받기
결과: ordin/list.json (지자체별 조례), ordin/review/*.txt (검토용 원문 발췌)
"""
import html
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hwptext import hwp_text  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
LAW = "https://www.law.go.kr/LSW/"
H = {"User-Agent": "Mozilla/5.0", "Referer": LAW + "ordinSc.do"}
GAP = 1.2


def get(url, data=None, xhr=False):
    hd = dict(H)
    if data is not None:
        hd["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        data = urllib.parse.urlencode(data).encode()
    if xhr:
        hd["X-Requested-With"] = "XMLHttpRequest"
    for attempt in range(3):
        try:
            time.sleep(GAP)
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=hd), timeout=60) as r:
                return r.read()
        except Exception:
            time.sleep(5)
    raise RuntimeError("fail " + url)


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ")).strip()


def search(q):
    rows = []
    pg, pages = 1, 1
    while pg <= pages:
        h = get(LAW + "ordinScListR.do?menuId=3&subMenuId=27&tabMenuId=139",
                {"q": q, "outmax": "150", "p3": "3", "idxList": "LsKwdNm_idx,OrdinNm_idx", "pg": pg,
                 "section": "ordinNm", "dtlYn": "N"}, xhr=True).decode("utf-8", "replace")
        m = re.search(r'id="listCnt">(\d+)', h)
        pages = math.ceil(int(m.group(1)) / 150) if m else 1
        for seq, tx, tx2 in re.findall(r"ordinViewAll\('(\d+)'[^>]*>\s*<span class=\"tx\">(.*?)</span>\s*<span class=\"tx2\">(.*?)</span>", h, re.S):
            rows.append((seq, re.sub(r"^\d+\.\s*", "", clean(tx)), clean(tx2)))
        pg += 1
    return rows


TITLE = re.compile(r"^(?:.+?)\s*(?:도시계획|군계획|도시·군계획|도시ㆍ군계획|관리계획|계획)\s*조례(?:\s*\[.*\])?$")
SKIP = re.compile(r"재정|특별회계|시설|위원회|먹거리|공원|경관|규칙")


def issuer_of(meta):
    m = re.search(r"\]\[(.+?)(?:조례|규칙)\s*제", meta)
    if not m:
        return None
    s = m.group(1)
    # '경상남도고성군' → '경상남도 고성군' (시도와 시군구 사이 띄우기)
    m2 = re.match(r"^(.+?(?:특별시|광역시|특별자치시|특별자치도|통합특별시|도))(.+)$", s)
    return f"{m2.group(1)} {m2.group(2)}" if m2 and m2.group(2) else s


def build_list():
    cands = {}
    for q in ("도시계획 조례", "군계획 조례", "계획 조례", "관리계획 조례"):
        for seq, title, meta in search(q):
            if not TITLE.match(title) or SKIP.search(title):
                continue
            who = issuer_of(meta)
            if not who:
                continue
            d = re.search(r"시행\s*([\d. ]+?)\.?\]", meta)
            cands.setdefault(who, []).append({"seq": seq, "title": title, "meta": meta,
                                              "enforce": d.group(1).strip() if d else ""})
    lst = {}
    for who, arr in cands.items():
        # 같은 지자체에 여러 개면 제목이 '도시계획/군계획 조례'인 것 우선, 그다음 최신 시행일
        arr.sort(key=lambda o: (bool(re.search(r"(도시|군|도시·군)계획\s*조례", o["title"])), o["enforce"]), reverse=True)
        lst[who] = arr[0]
    json.dump(lst, open(os.path.join(BASE, "list.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("지자체", len(lst))
    return lst


def body_html(seq):
    p = os.path.join(BASE, "body", seq + ".html")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read()
    raw = get(LAW + f"ordinInfoR.do?ordinSeq={seq}").decode("utf-8", "replace")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(raw)
    return raw


def html_text(raw):
    t = html.unescape(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S))
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t)


ART = re.compile(r"\n\s*제\s*\d+\s*조(?:의\s*\d+)?\s*[\(（]")


def solar_part(text):
    starts = [m.start() for m in ART.finditer(text)] + [len(text)]
    arts = [text[starts[i]:starts[i + 1]].strip() for i in range(len(starts) - 1) if "태양광" in text[starts[i]:starts[i + 1]]]
    # 부칙의 경과조치 같은 짧은 문장은 판단에 필요 없어 줄임
    arts = [a for a in arts if not re.match(r"제\s*\d+\s*조\s*[\(（][^)）]*(경과|적용례)", a)]
    return "\n".join(arts)


def solar_annexes(raw, part):
    """태양광·발전시설 관련 별표 → 한글 파일을 받아 글자로."""
    out = []
    for m in re.finditer(r"\[별표\s*([\d의 ]+)\]\s*([^<]{0,80})</a>.*?href=\"(flDownload\.do\?[^\"]+)\"", raw, re.S):
        no, title, link = m.group(1).strip(), m.group(2).strip(), html.unescape(m.group(3))
        mentioned = re.search(rf"별표\s*{re.escape(no)}(?!\d)", part)
        if not (re.search(r"태양광|발전시설|이격", title) or mentioned):
            continue
        if re.search(r"축사|가축|도축|고물상|자원순환|폐기물|동물|버섯|입목|경사도 산정|표고", title):
            continue
        try:
            b = get(LAW + link)
            if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                txt = hwp_text(b)
            else:
                txt = "(한글 파일이 아님: 직접 확인 필요)"
        except Exception as e:
            txt = f"(별표 받기 실패: {e})"
        out.append(f"[별표 {no}] {title}\n{txt.strip()}")
    return "\n\n".join(out)


def main():
    lst = build_list()
    os.makedirs(os.path.join(BASE, "review"), exist_ok=True)
    meta = {}
    for who, o in lst.items():
        rp = os.path.join(BASE, "review", who.replace(" ", "_") + ".txt")
        stamp = f"{o['seq']}|{o['enforce']}"
        meta[who] = {**o, "review": os.path.basename(rp)}
        if os.path.exists(rp) and open(rp, encoding="utf-8").readline().strip() == stamp:
            continue  # 바뀌지 않은 조례
        raw = body_html(o["seq"])
        part = solar_part(html_text(raw))
        ann = solar_annexes(raw, part) if (part or re.search(r"발전시설|태양광", raw)) else ""
        open(rp, "w", encoding="utf-8").write(f"{stamp}\n# {who} · {o['title']} · 시행 {o['enforce']}\n"
                                              f"# https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq={o['seq']}\n\n"
                                              f"{part}\n\n{ann}\n")
        print("·", who, len(part), len(ann))
    json.dump(meta, open(os.path.join(BASE, "list.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
