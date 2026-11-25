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
t "구조안 완료, 자료 없음 거부" deny $'구조안 완료 — 사용자 결정 대기\n\n* 지도를 만들었다'
t "수정 완료 거부" deny '**수정 완료, 확인 진행 중**'
t "녹화 확인 거부" deny '* 확인: 녹화 프레임에서 넘어간 것을 확인했다'
t "PR 링크만으로는 거부" deny $'구현을 마쳤다\nhttps://github.com/a/b/pull/1'
t "티켓 링크만으로는 거부" deny $'수정 완료\n[LRM-2](https://x.atlassian.net/browse/LRM-2)'
t "HTML 본문도 거부" deny '<p><strong>dev 병합 완료</strong></p><p>결과</p>'
t "착수 글 통과" pass '**착수** 원인 코드를 확인한다'
t "남은 일의 녹화 통과" pass '* 남은 일: 녹화 확인 → 구현 완료'
t "하지 못한 확인 통과" pass '* 게임 화면을 직접 녹화하지 못했다'
t "컨플루언스 링크 통과" pass $'구조안 완료\nhttps://x.atlassian.net/wiki/spaces/LRM/pages/1'
t "자료 줄의 파일 통과" pass $'수정 완료\n**자료**: 시연.mp4(17초, 버튼 위치)'
t "자료 줄에 파일 없으면 거부" deny $'수정 완료\n자료: 있음'
t "이유 있는 자료 없음 통과" pass $'구현 완료\n자료 없음: 화면 변화 없는 CI 수정'
t "이유 없는 자료 없음 거부" deny $'구현 완료\n자료 없음: -'
t "본문 그림 통과" pass '<p>수정 완료</p><div data-type="media" data-id="a"></div>'
t "inlineFileId 통과" pass '수정 완료' LRM-1 '{"inlineFileId":["a"]}'
t "로컬 경로 거부" deny '자료: /Users/a/b.png'
t "다른 프로젝트는 안내만" warn '수정 완료' ABC-3
t "티켓 주소도 인식" deny '수정 완료' 'https://x.atlassian.net/browse/LRM-3'
t "커넥터 도구도 검사" deny '수정 완료' LRM-1 '{}' mcp__claude_ai_Atlassian_MCP__addOrEditJiraIssueComment
t "다른 도구 통과" pass '수정 완료' LRM-1 '{}' mcp__atlassian__editJiraIssue
t "executeWrite 경유 거부" deny '' LRM-1 '{"name":"addOrEditJiraIssueComment","inputs":{"issueIdOrKey":"LRM-1","commentBody":"수정 완료"}}' mcp__atlassian__executeWrite
[ -z "$(echo '깨진 입력' | python3 -I "$G")" ] && { PASS=$((PASS+1)); echo "PASS  깨진 입력 통과"; } || { FAIL=$((FAIL+1)); echo "FAIL  깨진 입력"; }
echo "통과 $PASS / 실패 $FAIL"; [ "$FAIL" = 0 ]
