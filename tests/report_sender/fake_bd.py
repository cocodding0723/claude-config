#!/usr/bin/env python3
"""bd 대역. 실제 작업판에 접속하지 않음. `bd -C <board> show <card> --json` / `list --parent <card> ... --json` 만 흉내.
카드는 $FAKE_DIR/cards.json({카드: {title, description, children: [상태…]}}). 없는 카드는 종료 코드 1. 호출은 bd_calls.jsonl 에 기록"""
import json
import os
import sys

d = os.environ["FAKE_DIR"]
a = sys.argv[1:]
with open(os.path.join(d, "bd_calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(a, ensure_ascii=False) + "\n")
try:
    cards = json.load(open(os.path.join(d, "cards.json"), encoding="utf-8"))
except OSError:
    sys.exit(1)
sys.stderr.write("warning: beads.role not configured\n")  # 실제 bd 처럼 stderr 경고가 섞임
a = a[2:]
if a[0] == "show" and a[1] in cards:
    c = cards[a[1]]
    print(json.dumps([{"id": a[1], "title": c["title"], "description": c.get("description", ""), "status": "open"}], ensure_ascii=False))
elif a[0] == "list" and a[a.index("--parent") + 1] in cards:
    print(json.dumps([{"id": "k%d" % i, "status": s} for i, s in enumerate(cards[a[a.index("--parent") + 1]].get("children", []))]))
else:
    sys.exit(1)
