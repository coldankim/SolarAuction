# 특정 조례의 특정 별표(한글)를 받아 글자로 출력: python ordin/fetch_annex.py "지자체" 번호
import json, re, sys, html, os, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hwptext import hwp_text, any_text
lst = json.load(open("ordin/list.json", encoding="utf-8"))
who, no = sys.argv[1], sys.argv[2]
raw = open(f"ordin/body/{lst[who]['seq']}.html", encoding="utf-8").read()
pat = re.compile(r"[\[［【]\s*별표\s*" + re.escape(no) + r"\s*[\]］】]")
hits = [m.start() for m in pat.finditer(raw)]
link = None
for h in hits:
    m = re.search(r'href="(flDownload\.do\?[^"]+)"', raw[h:h + 3000])
    if m:
        link = html.unescape(m.group(1)); break
if not link:
    print("링크 없음", len(hits)); sys.exit()
b = urllib.request.urlopen(urllib.request.Request("https://www.law.go.kr/LSW/" + link, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.law.go.kr/LSW/ordinInfoP.do"}), timeout=60).read()
print(any_text(b))
