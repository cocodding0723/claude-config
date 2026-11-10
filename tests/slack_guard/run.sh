#!/usr/bin/env bash
# slack-format-guard.sh 시험. 훅 입력 JSON 만 만들어 넘기며 전송·네트워크·HOME 은 건드리지 않음.
# GUARD_SH=<경로> 로 변형 사본을 시험할 수 있음.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
GUARD="${GUARD_SH:-$HERE/../../claude/hooks/slack-format-guard.sh}"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

run() { jq -n --arg c "$1" '{tool_input:{command:$c}}' | bash "$GUARD" 2>/dev/null; }
denied() { run "$1" | jq -e '.hookSpecificOutput.permissionDecision == "deny"' > /dev/null 2>&1; }
allow() { # 이름 명령: 통과(출력 없음)
  if denied "$2"; then ko "$1" "차단됨: $(run "$2" | jq -r '.hookSpecificOutput.permissionDecisionReason')"; else ok "$1"; fi
}
block() { # 이름 명령 [사유 일부]
  if denied "$2" && { [ -z "${3:-}" ] || run "$2" | jq -r '.hookSpecificOutput.permissionDecisionReason' | grep -qF -- "$3"; }; then ok "$1"; else ko "$1" "통과하거나 사유 불일치: $(run "$2")"; fi
}

[ -x "$GUARD" ] && ok "실행 권한" || ko "실행 권한" "$GUARD"
bash -n "$GUARD" && ok "bash -n 통과" || ko "bash -n" "문법 오류"

# ---- 오탐: 실행이 아닌 언급은 통과 ----
allow "grep 로 이름 검색" 'grep -n slack_notify.sh hubs/docs/slack-format.md'
allow "rg 로 이름 검색" 'rg "slack_notify.sh --no-check" hubs'
allow "ls·cat 경로 인자" 'ls -la hubs/scripts/slack_notify.sh && cat hubs/scripts/slack_notify.sh'
allow "heredoc 본문에 호출 예" $'cat <<\'EOF\'\nslack_notify.sh "x"\nslack_notify.sh --no-check --kind done x\nEOF'
allow "heredoc 뒤 명령도 정상 판정" $'cat <<EOF > /tmp/x\nslack_notify.sh x\nEOF\necho done'
allow "에이전트 지시문 인용" 'echo "작업 끝나면 slack_notify.sh 로 보고하되 --kind 를 붙일 것"'
allow "작은따옴표 인용 안의 세미콜론" "echo 'a; slack_notify.sh x'"
allow "커밋 메시지에 이름" 'git commit -m "slack_notify.sh 색 띠 추가"'
allow "sed·shellcheck 대상 파일" 'shellcheck hubs/scripts/slack_notify.sh; sed -n 1,5p hubs/scripts/slack_notify.sh'
allow "bash -n 문법 검사" 'bash -n hubs/scripts/slack_notify.sh'
allow "시험 스크립트 실행(경로에 이름 포함, 대상은 run.sh)" 'bash tests/slack_notify/run.sh'
allow "python -c 안의 문자열" 'python3 -c "print(\"slack_notify.sh\")"'
allow "주석" $'echo hi # slack_notify.sh x'
allow "herestring 은 heredoc 아님" $'cat <<< "x"\nslack_notify.sh --kind done "y"'
allow "이름 없는 명령" 'git status'

# ---- 정탐: 실행 형태는 검사 ----
block "첫 토큰 --kind 누락" 'slack_notify.sh "x"' "--kind"
block "경로 실행 --kind 누락" '~/orca/claude-config/hubs/scripts/slack_notify.sh "x"' "--kind"
block "변수 경로 실행" '"$H/slack_notify.sh" "x"' "--kind"
block "bash 로 실행" 'bash hubs/scripts/slack_notify.sh "x"' "--kind"
block "bash -x 옵션 뒤 실행" 'bash -x hubs/scripts/slack_notify.sh "x"' "--kind"
block "sh 로 실행" 'sh hubs/scripts/slack_notify.sh "x"' "--kind"
block "source 로 실행" 'source hubs/scripts/slack_notify.sh "x"' "--kind"
block "bash -c 안쪽 실행" "bash -c 'slack_notify.sh \"x\"'" "--kind"
block "bash -lc 안쪽 실행" "bash -lc 'cd /tmp && slack_notify.sh \"x\"'" "--kind"
block "앞 명령 뒤 세미콜론" 'echo hi; slack_notify.sh "x"' "--kind"
block "&& 뒤 실행" 'cd hubs && slack_notify.sh "x"' "--kind"
block "파이프 뒤 실행" 'echo a | slack_notify.sh "x"' "--kind"
block "줄바꿈 뒤 실행" $'echo hi\nslack_notify.sh "x"' "--kind"
block "\$( ) 안 실행" 'R=$(slack_notify.sh "x")' "--kind"
block "큰따옴표 안 \$( ) 실행" 'echo "$(slack_notify.sh x)"' "--kind"
block "백틱 안 실행" 'echo `slack_notify.sh x`' "--kind"
block "env 접두 실행" 'env FOO=1 slack_notify.sh "x"' "--kind"
block "--no-check" 'slack_notify.sh --no-check --kind done "x"' "--no-check"
block "LRM_FORMAT=off 접두" 'LRM_FORMAT=off slack_notify.sh --kind done "x"' "LRM_FORMAT"
block "LRM_FORMAT=warn 접두" 'LRM_FORMAT=warn slack_notify.sh --kind done "x"' "LRM_FORMAT"
block "export LRM_FORMAT=off 뒤 실행" 'export LRM_FORMAT=off; slack_notify.sh --kind done "x"' "LRM_FORMAT"
block "decide 에 choices·options 없음" 'slack_notify.sh --kind decide "x"' "--choices"

# ---- 통과해야 하는 정상 호출 ----
allow "--kind done" 'slack_notify.sh --kind done "x"'
allow "경로 + --kind todo" './hubs/scripts/slack_notify.sh --tag t --kind todo --todo-id T-1 "x"'
allow "LRM_FORMAT=strict" 'LRM_FORMAT=strict slack_notify.sh --kind done "x"'
allow "decide + --choices" 'slack_notify.sh --kind decide --choices 3 "x"'
allow "decide + --options" "slack_notify.sh --kind decide --options '1=a,2=b' --recommend 1 --decision-id D-1 \"x\""
allow "--help" 'slack_notify.sh --help'
allow "-h" 'slack_notify.sh -h'
allow "정상 호출 뒤 2>&1 | tail" 'slack_notify.sh --kind done "x" 2>&1 | tail -n 3'
allow "bash 로 정상 호출" 'bash hubs/scripts/slack_notify.sh --kind warn "x"'
allow "여러 호출 모두 정상" $'slack_notify.sh --kind done "a"\nslack_notify.sh --kind warn "b"'
block "여러 호출 중 하나가 위반" $'slack_notify.sh --kind done "a"\nslack_notify.sh "b"' "--kind"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
