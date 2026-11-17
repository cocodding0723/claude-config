#!/usr/bin/env python3
"""terminal show 를 더한 가짜 orca. $FAKE_DIR/preview_<핸들>.txt 가 있으면 그 내용을 화면 끝(preview)으로 돌려주고 없으면 실패.
나머지 호출은 $FAKE_ORCA_BASE(stall_watch 가짜 orca)에 그대로 넘김."""
import json
import os
import sys

a = sys.argv[1:]
if a[:2] == ["terminal", "show"]:
    h = a[a.index("--terminal") + 1]
    f = os.path.join(os.environ["FAKE_DIR"], f"preview_{h}.txt")
    if not os.path.exists(f):
        sys.exit(2)
    print(json.dumps({"ok": True, "result": {"terminal": {"handle": h, "preview": open(f, encoding="utf-8").read()}}}, ensure_ascii=False))
    sys.exit(0)
os.execv(sys.executable, [sys.executable, os.environ["FAKE_ORCA_BASE"], *a])
