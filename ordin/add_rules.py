"""검토한 조례 기준을 ordin/rules_reviewed.json 에 더한다.
사용: python ordin/add_rules.py <<'EOF'  (JSON 객체: {"지자체": {"tiers": [[호수, m], ...], "others": "...", "note": "..."}})
tiers: [[5, 400], [1, 200]] = 5호 이상 주거지 400m, 1호(주택 1채)부터 200m. 주거 기준이 없으면 [], 확인 못 했으면 null.
"""
import json
import os
import sys

P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules_reviewed.json")
cur = json.load(open(P, encoding="utf-8")) if os.path.exists(P) else {}
new = json.loads(sys.stdin.read())
for k, v in new.items():
    t = v.get("tiers")
    v["tiers"] = None if t is None else [{"houses": h, "m": m} for h, m in t]
    cur[k] = v
json.dump(cur, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("저장", len(new), "누적", len(cur))
