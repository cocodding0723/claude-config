#!/bin/bash
# 워커 작업 공간 반납: worker-release 로 터미널을 닫은 뒤 ws_gc 와 같은 판정으로 그 작업 공간만 정리함.
# 판정·보관·색인·삭제는 ws_gc.sh --path 에 맡기고 여기서는 복사 구현하지 않음.
set -uo pipefail
export PATH="$PATH:/Applications/Orca.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin"

CFG_GC="${LRM_WS_GC:-$(cd "$(dirname "$0")" && pwd -P)/ws_gc.sh}"
CFG_MAX_PAGES=50   # worker-list 쪽 수 상한
HERE=$(cd "$(dirname "$0")" && pwd -P)
CFG_BOARD="${LRM_BOARD:-$HOME/orca/board}"                 # 작업 공간 카드를 찾는 작업판
CFG_HUB_REPORT="${LRM_HUB_REPORT:-$HERE/hub_report.py}"     # 카드 스레드에 끝 한 줄을 예약하는 CLI
CFG_THREAD_ROUTE="${LRM_FEEDBACK_ROUTE:-$HERE/feedback_route.py}"  # 스레드 종료 표시(end-thread)
CFG_END_TEXT="끝. 이후 답은 기능 코디네이터가 받음"
# ws_gc 건너뜀 이유 중 "내 것이 아닌 곳"(허브·다른 워커·하위 보유)은 보존과 구분해 5 로 돌려줌
CFG_REFUSE_REASONS="hub live children current protected"

usage() {
  cat <<'USAGE'
사용: release_ws.sh --dispatch <id> [--dry-run] [--released] [--json]
워커를 반납(orca orchestration worker-release)하고, 그 워커의 작업 공간이 깨끗하거나 결과물만 남았으면
보관·허브 색인 뒤 orca worktree rm 으로 지움. 코드 변경이 있으면 남기고 이유를 출력함.
판정은 ws_gc.sh 와 같음(--days 0: 최근 활동은 따지지 않음). 허브 폴더·다른 워커가 쓰는 곳은 지우지 않음.

옵션:
  --dispatch <id>  반납할 워커의 dispatch id(필수)
  --dry-run        worker-release·삭제 없이 판정만(이 워커 터미널은 쉬는 중으로 침)
  --released       이미 worker-release 한 워커(session_gc 자동 호출용). worker-release 를 다시 부르지 않음
  --json           결과를 JSON 한 줄로 출력
반납하면 그 작업 공간 카드(label ws:<경로>)의 Slack 스레드에 "끝. 이후 답은 기능 코디네이터가 받음" 한 줄을 예약하고
카드에 label slack-thread-end 를 붙임(이후 스레드 답은 기능 코디네이터로 감). 실제로 삭제했을 때만, 그 작업 공간 경로 라벨이 붙은 카드 전부에 한다
(거부·남김·환경 오류에서는 작업 공간이 살아 있으므로 하지 않음). 카드·작업판이 없거나 실패해도 반납 결과는 그대로(경고만).
환경: LRM_WS_GC(ws_gc.sh 위치), WS_GC_* (ws_gc.sh 설정), LRM_BOARD(작업판), LRM_HUB_REPORT, LRM_FEEDBACK_ROUTE
종료 코드: 0 삭제함(--dry-run 은 삭제 가능), 1 삭제 단계 실패, 2 인자 오류·알 수 없는 dispatch,
          3 환경 오류(orca·jq 없음, orca 응답 없음, 정리기 실행 중), 4 남김(코드 변경·열린 PR·미병합 등),
          5 거부(허브 폴더·다른 워커 또는 터미널이 쓰는 곳·하위 작업 공간 보유)
예:
  release_ws.sh --dispatch ctx_7432dc4961f6 --dry-run
  release_ws.sh --dispatch ctx_7432dc4961f6
USAGE
}
die() { echo "$1" >&2; exit "${2:-2}"; }

DISPATCH="" DRY=0 JSON=0 RELEASED=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --dispatch) [ $# -ge 2 ] || die "--dispatch 값 필요 — 예 --dispatch ctx_abc123"; DISPATCH="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --json) JSON=1; shift ;;
    --released) RELEASED=1; shift ;;
    *) die "알 수 없는 인자: $1 — --help 참고" ;;
  esac
done
[ -n "$DISPATCH" ] || die "--dispatch 필요 — 예 release_ws.sh --dispatch ctx_abc123"
for c in jq orca; do command -v "$c" >/dev/null 2>&1 || die "$c 없음 — 설치 필요. 아무것도 지우지 않음" 3; done
[ -f "$CFG_GC" ] || die "ws_gc.sh 없음: $CFG_GC — LRM_WS_GC 로 지정" 3

# dispatch → 작업 공간 경로
WSP="" page=0 cursor=""
while :; do
  if [ -n "$cursor" ]; then out=$(orca orchestration worker-list --full --limit 100 --cursor "$cursor" --json 2>/dev/null); else out=$(orca orchestration worker-list --full --limit 100 --json 2>/dev/null); fi
  echo "$out" | jq -e '.ok' >/dev/null 2>&1 || die "orca orchestration worker-list 실패 — Orca 실행 여부 확인(orca status). 아무것도 지우지 않음" 3
  hit=$(echo "$out" | jq -r --arg d "$DISPATCH" '[.result.workers[] | select(.dispatchId == $d)][0] | if . then ((.resource.worktreeId // "") | sub("^.*::"; "")) else "NOTFOUND" end')
  [ "$hit" = NOTFOUND ] || { WSP="$hit"; break; }
  [ "$(echo "$out" | jq -r '.result.page.hasMore // false')" = true ] || break
  cursor=$(echo "$out" | jq -r '.result.page.nextCursor // empty'); page=$((page + 1))
  [ -n "$cursor" ] && [ "$page" -lt "$CFG_MAX_PAGES" ] || die "worker-list 쪽 수 상한 초과 — 중단" 3
done
[ -n "$hit" ] && [ "$hit" != NOTFOUND ] || { [ "$hit" = NOTFOUND ] && die "dispatch 를 찾지 못함: $DISPATCH — orca orchestration worker-list 로 id 확인" || die "이 워커에 연결된 작업 공간이 없음: $DISPATCH" 4; }

thread_end() { # 작업 공간을 실제로 지운 뒤, 그 경로 라벨이 붙은 카드 전부의 스레드에 끝 표시. 카드 없음·실패는 경고만(반납 결과에 영향 없음)
  [ "$DRY" = 0 ] && command -v bd >/dev/null 2>&1 && [ -d "$CFG_BOARD" ] || return 0
  local rows row card slug hub
  rows=$(cd "$CFG_BOARD" && bd list --all --label "ws:$WSP" --json 2>/dev/null | jq -c '.[] | select(.issue_type != "epic") | select((.labels // []) | index("slack-thread-end") | not)' 2>/dev/null) || return 0
  while IFS= read -r row; do
    [ -n "$row" ] || continue
    card=$(echo "$row" | jq -r .id); slug=$(echo "$row" | jq -r '[.labels[]? | select(startswith("feature:")) | sub("^feature:"; "")][0] // empty')
    hub=ws; [ -z "$slug" ] || hub="feat-$slug"
    python3 "$CFG_HUB_REPORT" add --hub "$hub" --task 없음 --kind info --card "$card" --text "$CFG_END_TEXT" >/dev/null 2>&1 \
      || echo "⚠️ 카드 $card 스레드 끝 한 줄 예약 실패 — hub_report.py add --card $card 로 직접 기록" >&2
    BOARD_DIR="$CFG_BOARD" python3 -I "$CFG_THREAD_ROUTE" end-thread --card "$card" >/dev/null 2>&1 \
      || echo "⚠️ 카드 $card 스레드 종료 표시 실패 — feedback_route.py end-thread --card $card 로 직접 실행" >&2
  done <<< "$rows"
}

result() { # 코드 상태 메시지 [종료코드]. 스레드는 실제로 삭제했을 때만 끝냄(거부·환경 오류·건너뜀·남김은 작업 공간이 그대로 살아 있음)
  [ "$1" = deleted ] && thread_end
  if [ "$JSON" = 1 ]; then jq -nc --arg s "$1" --arg m "$2" --arg p "$WSP" --arg d "$DISPATCH" '{status:$s, message:$m, workspace:$p, dispatch:$d}'; else echo "$2"; fi
  exit "$3"
}

if [ "$DRY" = 0 ] && [ "$RELEASED" = 0 ]; then
  orca orchestration worker-release --dispatch "$DISPATCH" --json >/dev/null 2>&1 || echo "worker-release 실패 — 터미널이 열려 있으면 아래 판정이 '사용 중'으로 나옴. orca orchestration worker-show 로 상태 확인" >&2
fi

GC_ARGS=(--path "$WSP" --days 0 --json --no-slack)
[ "$DRY" = 1 ] && GC_ARGS+=(--dry-run --assume-idle "$DISPATCH") || GC_ARGS+=(--apply)
OUT=$(bash "$CFG_GC" "${GC_ARGS[@]}" 2>"${TMPDIR:-/tmp}/release_ws.$$.err"); GC_RC=$?
ERR=$(cat "${TMPDIR:-/tmp}/release_ws.$$.err" 2>/dev/null); rm -f "${TMPDIR:-/tmp}/release_ws.$$.err"
[ "$GC_RC" != 3 ] || result env_error "정리기 환경 오류: $(printf '%s' "$ERR" | tail -1) — orca status 확인" 3
echo "$OUT" | jq -e '.items' >/dev/null 2>&1 || result busy "정리기가 이미 실행 중이거나 응답 이상 — 1분 뒤 다시 실행. 출력: $(printf '%s' "$OUT" | tail -1)" 3
N=$(echo "$OUT" | jq '.items | length')
[ "$N" -ge 1 ] || result skipped "정리 대상 루트 밖이거나 Orca 에 등록되지 않은 곳: $WSP — 직접 확인" 4
V=$(echo "$OUT" | jq -r '.items[0].verdict'); R=$(echo "$OUT" | jq -r '.items[0].reason'); D=$(echo "$OUT" | jq -r '.items[0].detail')

case "$V" in
  deleted) result deleted "삭제함: $WSP ($D). 브랜치는 유지" 0 ;;
  candidate) result candidate "삭제 가능(시험): $WSP ($D)" 0 ;;
  failed) result failed "삭제 실패($R): $WSP — ws_gc 로그 확인(~/orca/archive/ws_gc.log). 보관본은 남음" 1 ;;
esac
case " $CFG_REFUSE_REASONS " in
  *" $R "*) hint=""; [ "$R" = live ] && hint=" 터미널이 아직 닫히는 중일 수 있음 — 잠시 뒤 다시 실행"
            result refused "거부($R): $WSP ${D:+— $D}.$hint 허브·다른 워커가 쓰는 곳은 지우지 않음" 5 ;;
esac
KEEP=$(echo "$OUT" | jq -r '.keep_code[0] | if . then "추적 수정 \(.tracked)·미확인 \(.unverified): \(.files | join(", "))" else empty end')
result kept "남김($R): $WSP ${D:+— $D}${KEEP:+. $KEEP}. 조치: 커밋하거나 직접 정리한 뒤 다시 실행" 4
