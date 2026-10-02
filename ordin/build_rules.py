"""검토 결과(ordin/rules_reviewed.json) + 전국 조례 목록(ordin/list.json) → setback_rules.json

- 검토한 곳: 조례 원문 기준 tiers(호수별 거리), 기타 이격 기준(others), 비고(note)
- 태양광 조항이 없는 곳: tiers [] (주거 이격 없음)
- 확인 못 한 곳 / 목록에 없는 곳: tiers null → 판정 시 시행령 상한(5호 이상 200m)을 보수적으로 적용
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
CHECKED = "2026-10-02"

lst = json.load(open(os.path.join(BASE, "list.json"), encoding="utf-8"))
rev = json.load(open(os.path.join(BASE, "rules_reviewed.json"), encoding="utf-8"))
out = {"_시행령": {
    "note": "2026.9.18 시행 재생에너지법 시행령: 태양광은 주거지(주택 5호 이상)로부터 최대 200m까지만, 도로 이격은 정할 수 없음. "
            "지붕형·자가소비·주민참여형 제외. 이보다 엄격한 조례는 완화해야 함",
    "source": "https://mcee.go.kr/home/web/board/read.do?menuId=10598&boardId=1883230&boardMasterId=939",
    "checked": CHECKED}}
for who, o in sorted(lst.items()):
    url = f"https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq={o['seq']}"
    base = {"title": o["title"], "enforce": o["enforce"], "source": url, "checked": CHECKED}
    if who in rev:
        r = rev[who]
        status = "확인 못 함" if r["tiers"] is None else ("주거 이격 없음" if not r["tiers"] else "조례 기준")
        out[who] = {**base, "tiers": r["tiers"], "others": r.get("others", ""), "note": r.get("note", ""), "status": status}
    else:
        out[who] = {**base, "tiers": [], "others": "", "note": "도시·군계획 조례에 태양광 이격 조항 없음",
                    "status": "조항 없음"}
json.dump(out, open(os.path.join(ROOT, "setback_rules.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
from collections import Counter
print(len(out) - 1, Counter(v["status"] for k, v in out.items() if not k.startswith("_")))
