#!/usr/bin/env python3
"""시험용 가짜 bd. 상태는 FAKE_BD_STATE(JSON), 호출은 FAKE_BD_LOG 에 한 줄씩. FAKE_BD_BLOCK 에 든 id 는 close 가 게이트로 실패"""
import json
import os
import sys

state_path = os.environ["FAKE_BD_STATE"]
with open(state_path, encoding="utf-8") as f:
    st = json.load(f)
with open(os.environ["FAKE_BD_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
cards = {c["id"]: c for c in st["cards"]}
a = sys.argv[1:]


def save():
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


if a[:1] == ["list"]:
    print(json.dumps([c for c in st["cards"] if c["status"] != "closed"], ensure_ascii=False))
elif a[:1] == ["comments"]:
    print(json.dumps(st.get("comments", {}).get(a[1], []), ensure_ascii=False))
elif a[:1] == ["comment"]:
    st.setdefault("comments", {}).setdefault(a[1], []).append({"text": a[2], "created_at": "2026-11-17T03:00:00Z"})
    save()
elif a[:1] == ["close"]:
    if a[1] in os.environ.get("FAKE_BD_BLOCK", "").split(","):
        print("gate: 닫기 거부(시험)", file=sys.stderr)
        sys.exit(1)
    cards[a[1]]["status"] = "closed"
    save()
elif a[:2] == ["label", "add"]:
    cards[a[2]].setdefault("labels", []).append(a[3])
    save()
elif a[:1] == ["update"] and a[2:4] == ["--status", "open"]:
    cards[a[1]]["status"] = "open"
    save()
elif a[:1] == ["update"] and a[2:3] == ["--assignee"]:
    cards[a[1]]["assignee"] = a[3]
    save()
else:
    print(f"가짜 bd: 모르는 명령 {a}", file=sys.stderr)
    sys.exit(2)
