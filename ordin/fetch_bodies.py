# 조례 본문 수집: ordin/list.json 의 조례마다 본문 텍스트를 ordin/body/<seq>.txt 로 저장 (이미 있으면 건너뜀)
import json, os, re, time, html, urllib.request
BASE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(BASE, "body"), exist_ok=True)
lst = json.load(open(os.path.join(BASE, "list.json"), encoding="utf-8"))
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.law.go.kr/LSW/ordinSc.do"}
n = 0
for name, arr in lst.items():
    for o in arr:
        p = os.path.join(BASE, "body", o["seq"] + ".txt")
        if os.path.exists(p):
            continue
        for attempt in range(3):
            try:
                raw = urllib.request.urlopen(urllib.request.Request(
                    f"https://www.law.go.kr/LSW/ordinInfoR.do?ordinSeq={o['seq']}", headers=H), timeout=40).read().decode("utf-8", "replace")
                break
            except Exception as e:
                time.sleep(5)
        else:
            print("fail", name, o["seq"]); continue
        # 별표 첨부 링크 정보 보존용으로 원문 HTML도 저장
        open(os.path.join(BASE, "body", o["seq"] + ".html"), "w", encoding="utf-8").write(raw)
        txt = html.unescape(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S))
        txt = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", txt)
        txt = re.sub(r"<[^>]+>", " ", txt).replace("\xa0", " ")
        txt = re.sub(r"[ \t]+", " ", txt)
        txt = re.sub(r"\n\s*\n+", "\n", txt)
        open(p, "w", encoding="utf-8").write(txt)
        n += 1
        time.sleep(1.5)
print("fetched", n)
