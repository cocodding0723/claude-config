#!/usr/bin/env bash
# jira-approval-guard 시험(사용자 11-17): 발급·링크·에픽 지정은 무승인 통과, 상태 변경은 계속 승인 필요. 임시 HOME 만 씀.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
H="$HERE/../../claude/hooks"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-jiraguard.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
mkdir -p "$SB/home/.config/lrm-hubs"  # 승인 기록 폴더가 없으면 판정 불가(ask)라 빈 폴더를 둠

# jexp <설명> <hook JSON> [pass|ask|deny, 기본 pass]
jexp() {
  local out got
  out=$(printf '%s' "$2" | HOME="$SB/home" bash "$H/jira-approval-guard.sh" 2>/dev/null)
  if [ -z "$out" ]; then got=pass; else got=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision'); fi
  [ "$got" = "${3:-pass}" ] && ok "$1" || ko "$1" "기대 ${3:-pass}, 실제 $got"
}

# 발급·링크·에픽 지정: 승인 기록이 전혀 없어도 통과
jexp "발급: 승인 기록 없이 통과" '{"tool_name":"mcp__atlassian__createJiraIssue","tool_input":{"projectKey":"LRM","summary":"버튼 안 눌림"}}'
jexp "발급: 요약이 비어 있어도 훅은 막지 않음" '{"tool_name":"mcp__atlassian__createJiraIssue","tool_input":{"projectKey":"LRM"}}'
jexp "발급: executeWrite 경유도 통과" '{"tool_name":"mcp__atlassian__executeWrite","tool_input":{"name":"createJiraIssue","cloudId":"c","inputs":{"summary":"버튼 안 눌림"}}}'
jexp "발급: 상위(에픽) 지정 포함도 통과" '{"tool_name":"mcp__atlassian__createJiraIssue","tool_input":{"projectKey":"LRM","summary":"하위 작업","parent":"LRM-10"}}'
jexp "링크: 이슈 링크 생성 통과" '{"tool_name":"mcp__atlassian__executeWrite","tool_input":{"name":"createIssueLink","cloudId":"c","inputs":{"inwardIssue":"LRM-1","outwardIssue":"LRM-2","type":"Relates"}}}'
jexp "에픽 지정: edit 의 parent 필드 통과" '{"tool_name":"mcp__atlassian__editJiraIssue","tool_input":{"issueIdOrKey":"LRM-12","fields":{"parent":{"key":"LRM-10"}}}}'
jexp "edit: 일반 필드 통과" '{"tool_name":"mcp__atlassian__editJiraIssue","tool_input":{"issueIdOrKey":"LRM-12","fields":{"summary":"x"}}}'
jexp "읽기 도구 통과" '{"tool_name":"mcp__atlassian__getJiraIssue","tool_input":{"issueIdOrKey":"LRM-12"}}'

# 상태 변경: 승인 없으면 계속 거부
jexp "상태 변경: transition 승인 없음 거부" '{"tool_name":"mcp__atlassian__transitionJiraIssue","tool_input":{"issueIdOrKey":"LRM-12","transition":{"id":"31"}}}' deny
jexp "상태 변경: executeWrite 경유 transition 거부" '{"tool_name":"mcp__atlassian__executeWrite","tool_input":{"name":"transitionJiraIssue","cloudId":"c","inputs":{"issueIdOrKey":"LRM-12","transition":{"id":"31"}}}}' deny
jexp "상태 변경: edit 의 status 필드 거부" '{"tool_name":"mcp__atlassian__editJiraIssue","tool_input":{"issueIdOrKey":"LRM-12","fields":{"status":{"name":"완료"}}}}' deny
# 되돌리기 쉬운 전환은 무승인(11-20). 표: hubs/jira_free_transitions.tsv
T='{"tool_name":"mcp__atlassian__transitionJiraIssue","tool_input":{"cloudId":"c","issueIdOrKey":"%s",%s}}'
jexp "무승인 전환: 진행 중(id 21) 통과" "$(printf "$T" LRM-12 '"transitionId":"21"')"
jexp "무승인 전환: 해야 할 일(id 11) 통과" "$(printf "$T" LRM-12 '"transitionId":"11"')"
jexp "무승인 전환: 이름만 줘도 통과" "$(printf "$T" LRM-12 '"transitionName":"진행 중"')"
jexp "무승인 전환: 옛 입력 형식(transition.id)도 통과" "$(printf "$T" LRM-12 '"transition":{"id":"21"}')"
jexp "무승인 전환: executeWrite 경유도 통과" '{"tool_name":"mcp__atlassian__executeWrite","tool_input":{"name":"transitionJiraIssue","cloudId":"c","inputs":{"issueIdOrKey":"LRM-12","transitionId":"21"}}}'
jexp "무승인 전환: 완료(id 41)는 거부" "$(printf "$T" LRM-12 '"transitionId":"41"')" deny
jexp "무승인 전환: 확인 대기(id 31)는 거부" "$(printf "$T" LRM-12 '"transitionId":"31"')" deny
jexp "무승인 전환: 이름은 진행 중인데 id 가 완료면 거부" "$(printf "$T" LRM-12 '"transitionId":"41","transitionName":"진행 중"')" deny
jexp "무승인 전환: 표에 없는 프로젝트는 거부" "$(printf "$T" ABC-3 '"transitionId":"21"')" deny
jexp "무승인 전환: 전환 없이 스프린트만 옮기면 기존대로 거부" "$(printf "$T" LRM-12 '"sprintId":5')" deny
jexp "상태 변경: 이슈 키 없으면 ask" '{"tool_name":"mcp__atlassian__transitionJiraIssue","tool_input":{"transition":{"id":"31"}}}' ask

msg=$(printf '%s' '{"tool_name":"mcp__atlassian__transitionJiraIssue","tool_input":{"issueIdOrKey":"LRM-12","transition":{"id":"31"}}}' | HOME="$SB/home" bash "$H/jira-approval-guard.sh" | jq -r '.hookSpecificOutput.permissionDecisionReason')
case "$msg" in *D-JSTLRM12*) ok "거부 메시지에 D-JST 번호 안내";; *) ko "거부 메시지" "$msg";; esac
case "$msg" in *D-JIRA*) ko "거부 메시지에 옛 D-JIRA 안내가 남음" "$msg";; *) ok "거부 메시지에 D-JIRA 없음";; esac

printf '통과 %s, 실패 %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
