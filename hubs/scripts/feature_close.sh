#!/bin/bash
# 기능 코디네이터 종료: 남은 카드·미머지 브랜치 점검 → (--yes) 작업자 반납·정리 → 코디네이터 터미널 종료 → epic 닫기. 브랜치는 지우지 않음
set -uo pipefail
export PATH="$PATH:/Applications/Orca.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin"

SRC="${BASH_SOURCE[0]}"
while [ -L "$SRC" ]; do T=$(readlink "$SRC"); case "$T" in /*) SRC="$T" ;; *) SRC="$(dirname "$SRC")/$T" ;; esac; done
HERE=$(cd "$(dirname "$SRC")" && pwd -P)
CFG_REPO="${REPO:-$HOME/orca/LoveRevolutionMaker}"
CFG_WS_ROOT="${WS_ROOT:-$HOME/orca/workspaces/LoveRevolutionMaker}"
CFG_BOARD="${LRM_BOARD:-$HOME/orca/board}"
CFG_RELEASE="${LRM_RELEASE_WS:-$HERE/release_ws.sh}"
CFG_GC="${LRM_WS_GC:-$HERE/ws_gc.sh}"
CFG_HUB_REPORT="${LRM_HUB_REPORT:-$HERE/hub_report.py}"   # 기능 스레드에 마무리 보고를 예약하는 CLI

usage() {
  cat <<'USAGE'
사용: feature_close.sh --slug <slug> [--result <결과 문서>] [--yes] [--dry-run] [--json]
기능 코디네이터를 끝낸다. 기본은 점검만 하고 멈춘다(아무것도 바꾸지 않음).
점검: 작업판의 진행 중 카드(label feature:<slug>, epic 하위)와 기능 통합 브랜치(기능 카드의 label branch:, 없으면 feature/<slug>)에 머지되지 않은 작업자 브랜치.
--yes 를 주면 실행:
  1 허브 하위 작업자마다 release_ws.sh 로 반납(결과물 보관 규칙 동일)
  2 남은 하위 작업 공간을 ws_gc.sh --parent 로 정리(코드 변경이 있는 곳은 남김)
  3 허브 코디네이터 터미널 종료(orca terminal close --worktree ... --all)
  4 기능 Slack 스레드에 결과 문서를 붙이고 마무리 한 줄 보고 예약(hub_report --card <epic>). 실패는 경고만, 종료 코드에 영향 없음
  5 epic 닫기
점검에서 남은 카드·미머지 브랜치가 있어도 --yes 면 진행한다(목록은 출력). 브랜치·허브 폴더는 지우지 않는다.
다시 실행해도 안전하다(이미 닫힌 epic·없는 터미널은 건너뜀).

옵션:
  --slug slug  feature_open.sh 에 쓴 slug(필수)
  --result 파일  스레드에 붙일 결과 문서. 생략하면 epic 의 "기능 완료" 코멘트에 적힌 .html 경로를 씀(없으면 문서 없이 한 줄만)
  --yes        점검 뒤 실제로 실행
  --dry-run    --yes 와 함께 쓰면 실행할 명령만 출력
  --json       결과를 JSON 한 줄로 출력
종료 코드: 0 완료(또는 점검 결과 남은 것 없음), 1 실행 단계 일부 실패, 2 인자 오류,
          3 환경 오류(bd·orca·jq·git 없음, 허브 없음), 4 점검에서 남은 것 있음(--yes 없이 멈춤)
예:
  feature_close.sh --slug mailbox
  feature_close.sh --slug mailbox --yes --dry-run --json
  feature_close.sh --slug mailbox --yes
USAGE
}
die() { echo "$1" >&2; exit "${2:-2}"; }

SLUG="" YES=0 DRY=0 JSON=0 RESULT=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --slug) [ $# -ge 2 ] || die "--slug 값 필요 — 예 --slug mailbox"; SLUG="$2"; shift 2 ;;
    --result) [ $# -ge 2 ] || die "--result 값 필요 — 예 --result ~/Documents/result.html"; RESULT="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --json) JSON=1; shift ;;
    *) die "알 수 없는 인자: $1 — --help 참고" ;;
  esac
done
[ -n "$SLUG" ] || die "--slug 필요 — 예 feature_close.sh --slug mailbox"
for c in bd orca jq git; do command -v "$c" >/dev/null 2>&1 || die "$c 없음 — 설치하거나 PATH 확인. 아무것도 바꾸지 않음" 3; done

BRANCH="feature/$SLUG"
HUB="$CFG_WS_ROOT/coord-feat-${SLUG}-hub"
LABEL="feature:$SLUG"
[ -d "$HUB" ] || die "허브 없음: $HUB — slug 확인(ls $CFG_WS_ROOT | grep coord-feat-)" 3

# epic·카드
ALL=$(cd "$CFG_BOARD" && bd list --all -l "$LABEL" --json 2>/dev/null) || ALL="[]"
EPIC=$(echo "$ALL" | jq -r '[.[] | select(.issue_type == "epic")] | (map(select(.status != "closed"))[0] // .[0]) | .id // empty')
EPIC_STATUS=$(echo "$ALL" | jq -r --arg e "$EPIC" '.[] | select(.id == $e) | .status' | head -1)
# 통합 브랜치 이름은 열 때 기능 카드에 적어 둔 것이 기준(스프린트 날짜가 든 이름이라 slug 만으로는 못 만듦). 옛 기능은 feature/<slug>
KNOWN=$(echo "$ALL" | jq -r --arg e "$EPIC" '[.[] | select(.id == $e) | (.labels // [])[] | select(startswith("branch:"))][0] // "" | sub("^branch:"; "")')
[ -z "$KNOWN" ] || BRANCH="$KNOWN"
KIDS="[]"; [ -n "$EPIC" ] && KIDS=$(cd "$CFG_BOARD" && bd list --all --parent "$EPIC" --json 2>/dev/null) || KIDS="[]"
OPEN_CARDS=$(jq -nc --argjson a "$ALL" --argjson k "${KIDS:-[]}" \
  '[$a[], $k[]] | unique_by(.id) | map(select(.issue_type != "epic" and (.status == "in_progress" or .status == "open" or .status == "blocked"))) | map({id, title, status, assignee})')

# 하위 작업 공간 → 브랜치 미머지 판정
WT=$(orca worktree list --limit 1000 --json 2>/dev/null) || die "orca worktree list 실패 — Orca 실행 여부 확인(orca status). 아무것도 바꾸지 않음" 3
CHILDREN=$(echo "$WT" | jq -c --arg h "$HUB" '[.result.worktrees[] | select(.path == $h) | .id] as $hid
  | [.result.worktrees[] | select((.parentWorktreeId // "") as $p | $hid | index($p)) | {path, branch:(.branch // "" | sub("^refs/heads/"; ""))}]')
UNMERGED="[]"
while IFS=$'\t' read -r p b; do
  [ -n "$b" ] || continue
  if ! git -C "$CFG_REPO" merge-base --is-ancestor "$b" "$BRANCH" 2>/dev/null; then
    UNMERGED=$(echo "$UNMERGED" | jq -c --arg p "$p" --arg b "$b" '. + [{path:$p, branch:$b}]')
  fi
done < <(echo "$CHILDREN" | jq -r '.[] | [.path, .branch] | @tsv')

N_OPEN=$(echo "$OPEN_CARDS" | jq length); N_UNM=$(echo "$UNMERGED" | jq length)
STEPS="[]"
step() { STEPS=$(echo "$STEPS" | jq -c --arg n "$1" --arg s "$2" --arg m "$3" '. + [{step:$n, status:$s, message:$m}]'); }
emit() { # 상태 종료코드
  if [ "$JSON" = 1 ]; then
    jq -nc --arg s "$1" --arg slug "$SLUG" --arg hub "$HUB" --arg b "$BRANCH" --arg e "$EPIC" --arg es "${EPIC_STATUS:-}" \
      --argjson oc "$OPEN_CARDS" --argjson um "$UNMERGED" --argjson st "$STEPS" \
      '{status:$s, slug:$slug, hub:$hub, branch:$b, epic:$e, epic_status:$es, open_cards:$oc, unmerged_branches:$um, steps:$st}'
  else
    echo "기능: $SLUG  허브: $HUB  통합 브랜치: $BRANCH  epic: ${EPIC:-없음}(${EPIC_STATUS:-})"
    echo "남은 카드 ${N_OPEN}개:"; echo "$OPEN_CARDS" | jq -r '.[] | "  \(.id) [\(.status)] \(.title) \(.assignee // "")"'
    echo "미머지 작업자 브랜치 ${N_UNM}개:"; echo "$UNMERGED" | jq -r '.[] | "  \(.branch)  (\(.path))"'
    echo "$STEPS" | jq -r '.[] | "단계 \(.step): \(.status) — \(.message)"'
    echo "결과: $1"
  fi
  exit "$2"
}

if [ "$YES" = 0 ]; then
  [ "$N_OPEN" = 0 ] && [ "$N_UNM" = 0 ] && emit ready_to_close 0
  emit pending 4
fi

FAIL=0
run() { # 단계 설명 명령...
  local n="$1" d="$2"; shift 2
  if [ "$DRY" = 1 ]; then step "$n" planned "$d: $*"; return 0; fi
  local out; out=$("$@" 2>&1); local rc=$?
  if [ "$rc" = 0 ]; then step "$n" ok "$d"; else step "$n" failed "$d (종료 $rc): $(printf '%s' "$out" | tail -1)"; return "$rc"; fi
}

# 1 작업자 반납: 하위 작업 공간에 붙은 dispatch
DISPATCHES=$(orca orchestration worker-list --full --limit 100 --json 2>/dev/null | jq -r --argjson c "$CHILDREN" \
  '($c | map(.path)) as $ps | .result.workers[]? | select(((.resource.worktreeId // "") | sub("^.*::"; "")) as $p | $ps | index($p)) | .dispatchId' 2>/dev/null)
if [ -z "$DISPATCHES" ]; then step release skipped "반납할 작업자 없음"; fi
for d in $DISPATCHES; do
  # release_ws.sh 4·5(남김·거부)는 실패가 아니라 보존. 1·3 만 실패로 침
  if [ "$DRY" = 1 ]; then step release planned "bash $CFG_RELEASE --dispatch $d"; continue; fi
  o=$(bash "$CFG_RELEASE" --dispatch "$d" --json 2>&1); rc=$?
  m=$(echo "$o" | jq -r '.message // empty' 2>/dev/null); m="${m:-$(printf '%s' "$o" | tail -1)}"
  case "$rc" in 0|4|5) step release ok "$d: $m" ;; *) step release failed "$d (종료 $rc): $m"; FAIL=1 ;; esac
done

# 2 남은 하위 작업 공간 정리(ws_gc 판정 그대로, 코드 변경 있는 곳은 남김)
run cleanup "하위 작업 공간 정리" bash "$CFG_GC" --parent "$HUB" --days 0 --apply --quiet --no-slack || FAIL=1

# 3 코디네이터 터미널 종료
LIVE=$(orca terminal list --worktree "path:$HUB" --json 2>/dev/null | jq '[.result.terminals[]? | select(.orphaned|not)] | length' 2>/dev/null)
if [ "${LIVE:-0}" -gt 0 ]; then run terminal "코디네이터 터미널 ${LIVE}개 종료" orca terminal close --worktree "path:$HUB" --all --json || FAIL=1
else step terminal skipped "살아 있는 터미널 없음"; fi

# 4 기능 스레드 마무리 보고. 결과 문서는 --result, 없으면 epic 코멘트("기능 완료" + 결과 HTML 경로)에서 찾음
if [ -n "$EPIC" ] && [ "$EPIC_STATUS" != closed ]; then
  if [ -z "$RESULT" ]; then
    RESULT=$(cd "$CFG_BOARD" && bd comments "$EPIC" --json 2>/dev/null | jq -r '[.[]? | select(.text | test("기능 완료"))] | last | .text // empty' 2>/dev/null \
      | grep -oE '(~|/)[^[:space:]"]+\.html' | tail -1)
    RESULT="${RESULT/#\~/$HOME}"
  fi
  CLOSE_ARGS=(add --hub "feat-$SLUG" --task 없음 --kind done --card "$EPIC" --text "기능 마무리: 남은 카드 ${N_OPEN}개·미머지 브랜치 ${N_UNM}개. 통합 브랜치 $BRANCH 유지")
  if [ -n "$RESULT" ] && [ ! -f "$RESULT" ]; then echo "⚠️ 결과 문서 없음: $RESULT — 문서 없이 한 줄만 보고" >&2; RESULT=""; fi
  [ -n "$RESULT" ] && CLOSE_ARGS+=(--file "$RESULT")
  run thread "기능 스레드 마무리 보고 예약${RESULT:+ (결과 문서 $(basename "$RESULT"))}" python3 "$CFG_HUB_REPORT" "${CLOSE_ARGS[@]}" || :
fi

# 5 epic 닫기
if [ -z "$EPIC" ]; then step epic skipped "epic 없음(label $LABEL)"
elif [ "$EPIC_STATUS" = closed ]; then step epic skipped "이미 닫힘: $EPIC"
else
  REASON="기능 종료(feature_close.sh). 남은 카드 ${N_OPEN}개·미머지 브랜치 ${N_UNM}개. 통합 브랜치 $BRANCH 유지"
  if [ "$DRY" = 1 ]; then step epic planned "bd close $EPIC --reason \"$REASON\""
  else (cd "$CFG_BOARD" && bd close "$EPIC" --reason "$REASON" >/dev/null 2>&1) && step epic ok "닫음: $EPIC" || { step epic failed "bd close $EPIC 실패 — cd $CFG_BOARD && bd close $EPIC 직접 실행"; FAIL=1; }
  fi
fi

[ "$DRY" = 1 ] && emit dry_run 0
[ "$FAIL" = 0 ] && emit closed 0
emit partial 1
