# 별표가 한 파일로 묶인 조례: 모든 별표 파일을 받아 '태양광' 주변만 출력
import json, re, sys, html, os, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hwptext import hwp_text, any_text
lst = json.load(open("ordin/list.json", encoding="utf-8"))
who = sys.argv[1]
raw = open(f"ordin/body/{lst[who]['seq']}.html", encoding="utf-8").read()
links = list(dict.fromkeys(html.unescape(l) for l in re.findall(r'href="(flDownload\.do\?[^"]+)"', raw)))
for link in links:
    b = urllib.request.urlopen(urllib.request.Request("https://www.law.go.kr/LSW/" + link, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.law.go.kr/LSW/ordinInfoP.do"}), timeout=90).read()
    if b[:4] != b"\xd0\xcf\x11\xe0":
        continue
    t = any_text(b)
    for m in re.finditer("태양광", t):
        pass
    i = t.find("태양광")
    if i >= 0:
        print(t[max(0, i - 400): i + 2600])
        break
