#!/usr/bin/env python3
"""시험용 가짜 gh. `gh pr view N --repo R --json ...` 과 `gh pr list --head B …` 만 지원. PR 표는 FAKE_GH_PRS(JSON, 브랜치는 키 "head:<브랜치>" 에 목록), 호출은 FAKE_GH_LOG. FAKE_GH_FAIL 의 번호·브랜치는 실패"""
import json
import os
import sys

with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
a = sys.argv[1:]
if a[:2] == ["pr", "list"]:
    if "--head" not in a:  # 병합 목록(merge_note). 표의 "merged" 키
        if os.environ.get("FAKE_GH_LIST_FAIL"):
            print("HTTP 401 (시험)", file=sys.stderr)
            sys.exit(1)
        with open(os.environ["FAKE_GH_PRS"], encoding="utf-8") as f:
            print(json.dumps(json.load(f).get("merged", [])))
        sys.exit(0)
    head = a[a.index("--head") + 1]
    if head in os.environ.get("FAKE_GH_FAIL", "").split(","):
        print("HTTP 502 (시험)", file=sys.stderr)
        sys.exit(1)
    with open(os.environ["FAKE_GH_PRS"], encoding="utf-8") as f:
        print(json.dumps(json.load(f).get("head:" + head, [])))
    sys.exit(0)
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
