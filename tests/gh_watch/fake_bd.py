#!/usr/bin/env python3
"""가짜 bd. list → FAKE_DIR/cards.json, comment <id> <글> → FAKE_DIR/bd-comments.log 에 'id<TAB>글<TAB>작성자'"""
import os
import sys

D = os.environ["FAKE_DIR"]
a = sys.argv[1:]
if a[:1] == ["list"]:
    print(open(os.path.join(D, "cards.json")).read())
elif a[:1] == ["comment"]:
    with open(os.path.join(D, "bd-comments.log"), "a") as f:
        f.write("%s\t%s\t%s\n" % (a[1], a[2], os.environ.get("BD_ACTOR", "")))
else:
    sys.exit(2)
