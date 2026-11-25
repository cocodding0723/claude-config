#!/usr/bin/env python3
"""가짜 bd. FAKE_DIR/bd-mode 가 down 이면 모두 실패, late 면 comment 를 쓰고 나서 실패. list → FAKE_DIR/cards.json, comment <id> <글> → FAKE_DIR/bd-comments.log 에 'id<TAB>글<TAB>작성자'"""
import json
import os
import sys

D = os.environ["FAKE_DIR"]
a = sys.argv[1:]
LOG = os.path.join(D, "bd-comments.log")
try:
    mode = open(os.path.join(D, "bd-mode")).read().strip()
except OSError:
    mode = ""
if mode == "down":
    sys.exit(1)
if a[:1] == ["list"]:
    print(open(os.path.join(D, "cards.json")).read())
elif a[:1] == ["comment"]:
    with open(LOG, "a") as f:
        f.write("%s\t%s\t%s\n" % (a[1], a[2], os.environ.get("BD_ACTOR", "")))
    if mode == "late":
        sys.exit(1)
elif a[:1] == ["comments"]:
    rows = [x.split("\t") for x in open(LOG).read().splitlines()] if os.path.exists(LOG) else []
    print(json.dumps([{"text": r[1]} for r in rows if r[0] == a[1]]))
else:
    sys.exit(2)
