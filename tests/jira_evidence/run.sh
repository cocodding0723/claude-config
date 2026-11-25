#!/usr/bin/env bash
# jira_evidence_guard 시험. 훅 입력만 넣고 네트워크는 쓰지 않음
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P); G="$HERE/../../claude/hooks/lib/jira_evidence_guard.py"
PASS=0 FAIL=0 EMPTY='{}'
t() { # 설명 기대 본문 [티켓] [추가 JSON] [도구]
  local out got
  out=$(jq -cn --arg b "$3" --arg k "${4:-LRM-1}" --argjson x "${5:-$EMPTY}" --arg t "${6:-mcp__atlassian__addOrEditJiraIssueComment}" \
    '{tool_name:$t,tool_input:({issueIdOrKey:$k,commentBody:$b}+$x)}' | python3 -I "$G")
  got=$(printf '%s' "${out:-$EMPTY}" | jq -r '.hookSpecificOutput | if . == null then "pass" elif .permissionDecision then .permissionDecision else "warn" end')
  if [ "$got" = "$2" ]; then PASS=$((PASS+1)); echo "PASS  $1"; else FAIL=$((FAIL+1)); echo "FAIL  $1 — 기대 $2, 실제 $got"; fi
}
t "자료 없는 구조안 완료 통과" pass $'구조안 완료 — 사용자 결정 대기\n\n* 지도를 만들었다'
t "자료 없는 수정 완료 통과" pass '**수정 완료, 확인 진행 중**'
t "녹화 확인 글 통과" pass '* 확인: 녹화 프레임에서 넘어간 것을 확인했다'
t "PR 링크만 있어도 통과" pass $'구현을 마쳤다\nhttps://github.com/a/b/pull/1'
t "선택 첨부 통과" pass '<p>수정 완료</p><div data-type="media" data-id="a"></div>'
t "inlineFileId 통과" pass '수정 완료' LRM-1 '{"inlineFileId":["a"]}'
t "로컬 경로 거부" deny '자료: /Users/a/b.png'
t "HTML 본문의 로컬 경로 거부" deny '<p>수정 완료</p><p>~/Documents/a.html</p>'
t "임시 폴더 경로 거부" deny '/private/tmp/a.mp4'
t "다른 프로젝트는 안내만" warn '/Users/a/b.png' ABC-3
t "티켓 주소도 인식" deny '/Users/a/b.png' 'https://x.atlassian.net/browse/LRM-3'
t "커넥터 도구도 검사" deny '/Users/a/b.png' LRM-1 '{}' mcp__claude_ai_Atlassian_MCP__addOrEditJiraIssueComment
t "다른 도구 통과" pass '/Users/a/b.png' LRM-1 '{}' mcp__atlassian__editJiraIssue
t "executeWrite 경유 거부" deny '' LRM-1 '{"name":"addOrEditJiraIssueComment","inputs":{"issueIdOrKey":"LRM-1","commentBody":"/Users/a/b.png"}}' mcp__atlassian__executeWrite
[ -z "$(echo '깨진 입력' | python3 -I "$G")" ] && { PASS=$((PASS+1)); echo "PASS  깨진 입력 통과"; } || { FAIL=$((FAIL+1)); echo "FAIL  깨진 입력"; }
echo "통과 $PASS / 실패 $FAIL"; [ "$FAIL" = 0 ]
