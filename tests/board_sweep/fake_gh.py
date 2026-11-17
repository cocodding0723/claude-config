#!/usr/bin/env python3
"""시험용 가짜 gh. `gh pr view N --repo R --json ...` 만 지원. PR 표는 FAKE_GH_PRS(JSON), 호출은 FAKE_GH_LOG. FAKE_GH_FAIL 의 번호는 실패"""
import json
import os
import sys

with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
a = sys.argv[1:]
if a[:2] != ["pr", "view"]:
    sys.exit(2)
if a[2] in os.environ.get("FAKE_GH_FAIL", "").split(","):
    print("HTTP 502 (시험)", file=sys.stderr)
    sys.exit(1)
with open(os.environ["FAKE_GH_PRS"], encoding="utf-8") as f:
    prs = json.load(f)
if a[2] not in prs:
    print("no pull requests found", file=sys.stderr)
    sys.exit(1)
print(json.dumps(prs[a[2]]))
