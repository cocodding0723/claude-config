#!/usr/bin/env python3
"""github-issue 발급 전후 검사. jira-issue 검사기를 그대로 쓰고 키 형식·담당 안내만 GitHub 기준으로 바꿈.

pre  <입력.json>   발급 직전 입력 검사. 오류 있으면 종료 1, 경고만이면 0 (--strict 는 경고도 1)
post <결과.json>   발급 직후 다시 읽은 이슈들의 점검 표 출력. 칸이 비면 종료 1
입력·결과 칸은 jira-issue/scripts/issue_check.py 머리말과 같음. key·parent.key 는 OWNER/REPO#번호, 에픽은 type "에픽"(라벨 epic).
상태 이름은 Project 보드 Status 와 같음: 해야 할 일·진행 중·확인 대기·보류·완료.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "jira-issue", "scripts"))
import issue_check as base  # noqa: E402

base.KEY_RE = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")
base.KEY_HINT = "OWNER/REPO#번호"
base.ASSIGNEE_HINT = "GitHub 로그인"

if __name__ == "__main__":
    sys.exit(base.main(sys.argv))
