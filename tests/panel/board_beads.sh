#!/usr/bin/env bash
# 작업판(bd) 원료와 board-ask·board-answer 시험. mktemp -d 임시 작업판만 씀(실제 ~/orca/board 는 열지 않음).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS="${COLLECT_DIR:-$HERE/../../hubs/scripts}"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-board.XXXXXX")
trap '[ -n "${KEEP:-}" ] || rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" PANEL_BOARD_DIR="$SB/board" HOME="$SB/home"
mkdir -p "$BOARD_DIR" "$HOME"
(cd "$BOARD_DIR" && git init -q && bd init --prefix t -q >/dev/null 2>&1) || { echo "임시 작업판 준비 실패"; exit 1; }
b() { (cd "$BOARD_DIR" && bd "$@"); }
id_of() { python3 -c 'import json,sys;d=json.load(sys.stdin);d=d[0] if isinstance(d,list) else d;print(d["id"])'; }

EPIC=$(b create "구조 전환" -t epic -p 0 --json | id_of)
DONE=$(b create "끝난 일" --parent "$EPIC" --json | id_of); b close "$DONE" --reason "커밋 abc" >/dev/null
WORK=$(b create "진행 일" --parent "$EPIC" --json | id_of); BD_ACTOR=worker-1 b update "$WORK" --claim >/dev/null
STUCK=$(b create "막힌 일" --json | id_of); b update "$STUCK" --status blocked >/dev/null

# board-ask
D=$("$SCRIPTS/board-ask" "패널 정본 옮길까?" --options "옮김|유지" --recommend 옮김 --why "손 칸 낡음" --parent "$EPIC" --priority 0); rc=$?
[ "$rc" = 0 ] && [ -n "$D" ] && ok "board-ask: 결정 카드 생성, id 출력" || ko "board-ask 생성" "rc=$rc"
D2=$("$SCRIPTS/board-ask" "패널 정본 옮길까?" --options "옮김|유지" --json)
[ "$(echo "$D2" | jq -r '.id + "/" + (.created|tostring)')" = "$D/false" ] && ok "board-ask: 같은 제목 열린 카드면 새로 안 만듦" || ko "board-ask 반복" "$D2"
"$SCRIPTS/board-ask" "선택지 하나" --options "A" >/dev/null 2>"$SB/err"; [ $? = 2 ] && grep -q '조치' "$SB/err" && ok "board-ask: 선택지 1개 → 종료 2, 원인·조치" || ko "선택지 검사" ""
"$SCRIPTS/board-ask" "x" --options "A|B" --recommend C >/dev/null 2>&1; [ $? = 2 ] && ok "board-ask: 추천이 선택지 밖 → 종료 2" || ko "추천 검사" ""
"$SCRIPTS/board-ask" --help >/dev/null; [ $? = 0 ] && ok "board-ask --help 종료 0" || ko "--help" ""
DRY=$("$SCRIPTS/board-ask" "건조" --options "A|B" --dry-run); [ "$(b list --json --limit 0 | jq '[.[]|select(.title=="건조")]|length')" = 0 ] && ok "board-ask --dry-run: 작업판 그대로" || ko "dry-run" "$DRY"
T=$("$SCRIPTS/board-ask" "키스토어 만들기" --kind user-todo)
A=$("$SCRIPTS/board-ask" "PR 게시" --kind approval)

# 수집기 원료
P=$(env -u PANEL_GH_REPOS PANEL_ORCA=/usr/bin/false LRM_HUB_DIR="$SB/hub" bash "$SCRIPTS/panel_collect.sh" --print 2>/dev/null)
v() { echo "$P" | jq -r ".sources.board.value$1"; }
[ "$(echo "$P" | jq -r .sources.board.ok)" = true ] && ok "수집기: board 원료 ok" || ko "board ok" "$(echo "$P" | jq -c .sources.board | head -c 200)"
[ "$(v '.decisions[0].id')" = "$D" ] && v '.decisions[0].desc' | grep -q '추천: 옮김' && ok "결정 대기 = kind:decision 열린 카드, 설명에 선택지·추천" || ko "decisions" "$(v '.decisions' | head -c 200)"
[ "$(v '.todos[0].id')" = "$T" ] && [ "$(v '.approvals[0].id')" = "$A" ] && ok "할 일 = kind:user-todo, 승인 = kind:approval" || ko "todos/approvals" ""
[ "$(v '.doing[0].id')" = "$WORK" ] && [ "$(v '.doing[0].who')" = worker-1 ] && [ "$(v '.doing[0].beatAt|type')" = number ] && [ "$(v '.doing[0].startedAt|type')" = number ] && ok "진행 = in_progress 카드, 담당·시작·마지막 신호" || ko "doing" "$(v '.doing' | head -c 200)"
[ "$(v '.blocked[0].id')" = "$STUCK" ] && ok "막힘 = blocked 카드" || ko "blocked" ""
[ "$(v '.headline.id')/$(v '.headline.done')/$(v '.headline.total')" = "$EPIC/1/3" ] && ok "헤드라인 = 열린 epic, 진척 닫힌/전체" || ko "headline" "$(v '.headline')"
[ "$(v '.recent[0].id')" = "$DONE" ] && [ "$(v '.recent[0].reason')" = "커밋 abc" ] && ok "최근 기록 = 닫힌 카드" || ko "recent" "$(v '.recent')"
[ "$(echo "$P" | jq --arg d "$D" '[.sources.board.value.doing[].id] | index($d)')" = null ] && ok "kind 카드는 진행 줄에 중복 안 나옴" || ko "중복" ""

# board-answer
R=$("$SCRIPTS/board-answer" "$D" "옮김. Slack 칸은 숨겨" --json)
[ "$(echo "$R" | jq -r .closed)" = true ] && [ "$(b show "$D" --json | jq -r 'if type=="array" then .[0] else . end | .status')" = closed ] && ok "board-answer: 코멘트 + 닫음" || ko "board-answer" "$R"
b comments "$D" --json | jq -r '.[].text' | grep -q '사용자 답: 옮김. Slack 칸은 숨겨' && ok "board-answer: 원문 그대로 코멘트" || ko "코멘트 원문" ""
R2=$("$SCRIPTS/board-answer" "$D" "다시" --json); [ "$(echo "$R2" | jq -r .already)" = true ] && [ "$(b comments "$D" --json | jq length)" = 1 ] && ok "board-answer: 이미 닫힌 카드는 그대로(반복 안전)" || ko "반복" "$R2"
"$SCRIPTS/board-answer" t-nope "x" >/dev/null 2>&1; [ $? = 4 ] && ok "board-answer: 없는 카드 → 종료 4" || ko "없는 카드" ""
"$SCRIPTS/board-answer" "$T" "내일" --keep-open >/dev/null; [ "$(b show "$T" --json | jq -r 'if type=="array" then .[0] else . end | .status')" = open ] && ok "board-answer --keep-open: 코멘트만" || ko "keep-open" ""

# 작업판 없음 → 원료 실패, 다른 칸은 그대로
P=$(env -u PANEL_GH_REPOS PANEL_BOARD_DIR="$SB/none" PANEL_ORCA=/usr/bin/false LRM_HUB_DIR="$SB/hub" bash "$SCRIPTS/panel_collect.sh" --print 2>/dev/null)
[ "$(echo "$P" | jq -r .sources.board.err)" = missing ] && ok "작업판 폴더 없음 → board 원료 missing" || ko "missing" "$(echo "$P" | jq -c .sources.board | head -c 120)"

echo "작업판 결과: 통과 $PASS, 실패 $FAIL"
[ "$FAIL" = 0 ]
