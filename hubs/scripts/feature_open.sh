#!/bin/bash
# 기능 코디네이터 생성: 기능 통합 브랜치·허브 작업 공간·작업판 epic·Opus 코디네이터. 허브 생성·기동은 create_hub.sh 에 맡김
set -euo pipefail
export PATH="$PATH:/Applications/Orca.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin"

SRC="${BASH_SOURCE[0]}"
while [ -L "$SRC" ]; do T=$(readlink "$SRC"); case "$T" in /*) SRC="$T" ;; *) SRC="$(dirname "$SRC")/$T" ;; esac; done
HERE=$(cd "$(dirname "$SRC")" && pwd -P)
CFG_REPO="${REPO:-$HOME/orca/LoveRevolutionMaker}"
CFG_WS_ROOT="${WS_ROOT:-$HOME/orca/workspaces/LoveRevolutionMaker}"
CFG_BOARD="${LRM_BOARD:-$HOME/orca/board}"
CFG_CREATE_HUB="${LRM_CREATE_HUB:-$HERE/../../skills/orca-hub-create/scripts/create_hub.sh}"
CFG_ROLE_TMPL="${LRM_FEATURE_ROLE:-$HERE/../roles/role-feature.txt}"
CFG_ROLE_OUT="${LRM_ROLES_DIR:-$HOME/.config/lrm-hubs/roles}"   # restart_hub.sh 가 role-<이름>.txt 로 읽는 곳
CFG_MODEL="claude-opus-5-5"
CFG_SPRINT_FILE="${LRM_SPRINT_FILE:-$HOME/.config/lrm-hubs/sprint-start}"   # 이 스프린트의 시작일 한 줄(YYYYMMDD). 스프린트가 바뀔 때 한 번 고침
CFG_HUB_REPORT="${LRM_HUB_REPORT:-$HERE/hub_report.py}"   # 기능 Slack 스레드 시작 보고를 예약하는 CLI

usage() {
  cat <<'USAGE'
사용: feature_open.sh --ticket LRM-n --name "<기능 한글>" --base <브랜치> [--slug <영문 작업명>] [--type <타입>] [--long]
                       [--goal "<목표 한 문장>" --verify "<확인 방법 한 문장>" [--out "<범위 밖>"]] [--dry-run] [--json]
기능 하나를 맡을 코디네이터를 만든다. 다시 실행해도 있는 것은 재사용하고 빠진 것만 만든다.
  1 기능 통합 브랜치(없으면 --base 에서 생성). 이름은 게임 저장소 규칙(CLAUDE.md Branch Convention)을 따름:
      스프린트 안에서 끝나는 작업  sprint-<스프린트 시작일 YYYYMMDD>/<타입>-<티켓>-<작업명>   예 sprint-20261107/feat-LRM-612-mailbox
      여러 스프린트에 걸치는 모듈(--long)  feature/<작업명>                    예 feature/localization
    스프린트 시작일은 오늘 날짜가 아님. --sprint > 환경 LRM_SPRINT_DATE > 설정 파일(~/.config/lrm-hubs/sprint-start, 한 줄 YYYYMMDD) 순으로 읽고, 없으면 만들지 않고 멈춤
    다시 실행하면 처음 만든 이름을 기능 카드(label branch:)에서 읽어 그대로 씀(스프린트가 바뀌어도 같은 브랜치)
  2 허브 작업 공간 <WS_ROOT>/coord-feat-<slug>-hub (그 브랜치 체크아웃)
  3 작업판 epic 카드 (bd create -t epic, label feature:<slug>·branch:<통합 브랜치>·repo:<저장소>·base:<기준>. 범위 검사 기준·완료 증거 커밋 묶기·병합 기록 대조에 씀)
  4 역할 파일 ~/.config/lrm-hubs/roles/role-feat-<slug>.txt 와 Opus 코디네이터 기동
    (살아 있는 코디네이터가 있으면 기동하지 않음)
  5 기능 Slack 스레드 시작 예약: hub_report 에 epic 주제 "시작" 한 줄(같은 내용은 중복 무시라 재실행 안전).
    스레드를 열고 epic 에 label slack-thread:<ts> 를 붙이는 일은 report_sender 가 함. 예약 실패는 경고만 하고 종료 코드에 영향 없음

옵션:
  --ticket LRM-n  Jira 티켓(필수)
  --name 이름     기능 이름, 한글 가능(필수)
  --base 브랜치   통합 브랜치를 만들 기준(필수). 예 dev → origin/dev 가 있으면 그것을 씀
  --slug 영문     영문 작업명(소문자·숫자·-). 브랜치·허브 이름에 씀. 생략하면 허브 이름은 티켓 소문자(lrm-n), 브랜치는 티켓 번호까지만(경고)
  --type 타입     feat|fix|chore|refactor|docs|design (기본 feat). 버그 수정은 fix
  --long          여러 스프린트에 걸치는 모듈 작업. 브랜치를 feature/<작업명> 으로 만듦
  --sprint 날짜   이 스프린트의 시작일 YYYYMMDD. 생략하면 LRM_SPRINT_DATE, 그다음 설정 파일
  --goal 문장     접수: 만들 것 한 문장(사용자 원문 그대로). 기능 카드 설명의 "- 목표:" 칸에 적음
  --verify 문장   접수: 끝났는지 확인할 방법 한 문장. "- 확인 방법:" 칸에 적음
  --out 문장      접수: 이번에 하지 않는 것. "- 범위 밖:" 칸에 적음
                  세 칸은 새 기능 카드를 만들 때만 적음. 안 주면 빈 칸으로 남고 코디네이터가 채워야 접수 단계를 떠날 수 있음(board-stage)
  --dry-run       아무것도 만들지 않고 할 일만 출력
  --json          결과를 JSON 한 줄로 출력
환경: REPO, WS_ROOT, LRM_BOARD(작업판 폴더), LRM_CREATE_HUB, LRM_FEATURE_ROLE, LRM_ROLES_DIR, LRM_SPRINT_DATE(스프린트 시작일), LRM_SPRINT_FILE(스프린트 시작일 설정 파일, 기본 ~/.config/lrm-hubs/sprint-start)
종료 코드: 0 성공, 1 단계 실패, 2 인자 오류, 3 환경 오류(bd·orca·jq·git 없음, 기준 브랜치 없음), 5 사용자 검수 대기 상한(REVIEW_WIP_MAX, 기본 3)
예:
  feature_open.sh --ticket LRM-612 --name "우편함" --base dev --dry-run
  feature_open.sh --ticket LRM-612 --name "우편함" --base dev --slug mailbox --json
USAGE
}
die() { echo "$1" >&2; exit "${2:-2}"; }
oneline() { printf '%s' "$1" | tr '\n' ' '; }  # 접수 칸은 한 줄(줄이 갈리면 칸 이름 줄에서 떨어짐)

TICKET="" NAME="" BASE="" SLUG="" TYPE="feat" LONG=0 DRY=0 JSON=0 GOAL="" VERIFY="" OUT_OF="" SPRINT_ARG=""
BRANCH_TYPES="feat|fix|chore|refactor|docs|design"  # 게임 저장소 CLAUDE.md 의 타입 접두
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --ticket|--name|--base|--slug|--type|--goal|--verify|--out|--sprint) [ $# -ge 2 ] || die "$1 값 필요 — --help 참고"
      case "$1" in --ticket) TICKET="$2";; --name) NAME="$2";; --base) BASE="$2";; --slug) SLUG="$2";; --type) TYPE="$2";;
        --goal) GOAL="$2";; --verify) VERIFY="$2";; --out) OUT_OF="$2";; --sprint) SPRINT_ARG="$2";; esac; shift 2 ;;
    --long) LONG=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --json) JSON=1; shift ;;
    *) die "알 수 없는 인자: $1 — --help 참고" ;;
  esac
done
[ -n "$TICKET" ] && [ -n "$NAME" ] && [ -n "$BASE" ] || die "--ticket·--name·--base 필요 — 예 feature_open.sh --ticket LRM-612 --name \"우편함\" --base dev"
[[ "$TICKET" =~ ^LRM-[0-9]+$ ]] || die "티켓 형식 오류: $TICKET — LRM-숫자 로 지정"
[[ "$TYPE" =~ ^($BRANCH_TYPES)$ ]] || die "타입 오류: $TYPE — $BRANCH_TYPES 중 하나"
NAMED=1; [ -n "$SLUG" ] || NAMED=0
SLUG="${SLUG:-$(echo "$TICKET" | tr 'A-Z' 'a-z')}"
[[ "$SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "slug 형식 오류: $SLUG — 영문 소문자·숫자·- 만"
for c in bd orca jq git; do command -v "$c" >/dev/null 2>&1 || die "$c 없음 — 설치하거나 PATH 확인. 아무것도 만들지 않음" 3; done
[ -f "$CFG_CREATE_HUB" ] || die "create_hub.sh 없음: $CFG_CREATE_HUB — LRM_CREATE_HUB 로 지정" 3
[ -f "$CFG_ROLE_TMPL" ] || die "역할 템플릿 없음: $CFG_ROLE_TMPL — LRM_FEATURE_ROLE 로 지정" 3
[ -d "$CFG_BOARD" ] || die "작업판 폴더 없음: $CFG_BOARD — LRM_BOARD 로 지정" 3

HUB_SLUG="feat-$SLUG"
HUB="$CFG_WS_ROOT/coord-${HUB_SLUG}-hub"
LABEL="feature:$SLUG"
# 통합 브랜치 이름. 이미 연 기능이면 기능 카드에 적힌 이름을 그대로 씀: 스프린트 날짜로 만든 이름이라 다시 계산하면 날이 바뀔 때 다른 브랜치가 됨
sprint_start() { # 스프린트 시작일. 오늘 날짜로 지어내지 않음: 폴더 이름이 스프린트마다 하나여야 스프린트가 끝날 때 한 덩어리로 정리됨
  local v="${SPRINT_ARG:-${LRM_SPRINT_DATE:-}}"
  [ -n "$v" ] || { [ -f "$CFG_SPRINT_FILE" ] && v=$(tr -d '[:space:]' < "$CFG_SPRINT_FILE"); }
  [ -n "$v" ] || die "스프린트 시작일을 모름 — --sprint <YYYYMMDD> 로 주거나 $CFG_SPRINT_FILE 에 한 줄로 적은 뒤 다시 실행(여러 스프린트에 걸치는 모듈이면 --long)"
  [[ "$v" =~ ^20[0-9]{6}$ ]] || die "스프린트 시작일 형식 오류: $v — YYYYMMDD 여덟 자리(예 20261107)"
  printf '%s' "$v"
}
KNOWN=$(cd "$CFG_BOARD" && bd list -t epic -l "$LABEL" --json 2>/dev/null | jq -r '[.[] | select(.status != "closed")][0].labels // [] | map(select(startswith("branch:")))[0] // "" | sub("^branch:"; "")' 2>/dev/null) || KNOWN=""
if [ -n "$KNOWN" ]; then BRANCH="$KNOWN"
elif [ "$NAMED" = 0 ]; then
  echo "⚠️ 영문 작업명 없음 — 브랜치를 티켓 번호까지만으로 만듦. --slug <작업명> 을 주면 규칙대로 <타입>-<티켓>-<작업명> 이 됨" >&2
  if [ "$LONG" = 1 ]; then BRANCH="feature/$SLUG"; else SPRINT=$(sprint_start) || exit $?; BRANCH="sprint-$SPRINT/$TYPE-$TICKET"; fi
elif [ "$LONG" = 1 ]; then BRANCH="feature/$SLUG"
else SPRINT=$(sprint_start) || exit $?; BRANCH="sprint-$SPRINT/$TYPE-$TICKET-$SLUG"; fi
ROLE_OUT="$CFG_ROLE_OUT/role-${HUB_SLUG}.txt"

# 기준: origin/<base> 우선, 없으면 로컬
if git -C "$CFG_REPO" rev-parse -q --verify "refs/remotes/origin/$BASE" >/dev/null; then BASE_REF="origin/$BASE"
elif git -C "$CFG_REPO" rev-parse -q --verify "$BASE" >/dev/null; then BASE_REF="$BASE"
else die "기준 브랜치 없음: $BASE — git -C $CFG_REPO fetch origin 뒤 다시 실행" 3; fi
BRANCH_STATE=new; git -C "$CFG_REPO" show-ref -q --verify "refs/heads/$BRANCH" && BRANCH_STATE=exists

# 사용자 검수 대기 상한: 새 기능 팀은 검수 대기(label stage:review)가 REVIEW_WIP_MAX(3) 미만일 때만
WAITING=$(cd "$CFG_BOARD" && bd list --label stage:review --all --limit 0 --json 2>/dev/null | jq -r '[.[] | select(.issue_type == "epic" and ((.labels // []) | index("stage:review")))] | length' 2>/dev/null || echo 0)

# epic: 같은 label 의 열린 epic 이 있으면 재사용
EPIC=$(cd "$CFG_BOARD" && bd list -t epic -l "$LABEL" --json 2>/dev/null | jq -r '[.[] | select(.status != "closed")][0].id // empty') || EPIC=""
EPIC_STATE=exists
if [ -z "$EPIC" ]; then
  [ "${WAITING:-0}" -lt "${REVIEW_WIP_MAX:-3}" ] || die "사용자 검수 대기 기능 ${WAITING}개(상한 ${REVIEW_WIP_MAX:-3}) — 새 기능 팀을 열지 않음. 검수가 끝난 기능을 board-done --epic <id> <이름> --user-ok \"<원문>\" 로 정리한 뒤 다시 실행" 5
  EPIC_STATE=new
  if [ "$DRY" = 1 ]; then EPIC="(생성 예정)"
  else
    EPIC=$(cd "$CFG_BOARD" && bd create "[$TICKET] $NAME" -t epic -l "$LABEL,flow:v1,stage:intake,branch:$BRANCH,repo:$CFG_REPO,base:$BASE_REF" --external-ref "jira-$TICKET" \
      -d "$(printf -- '- 목표: %s\n- 확인 방법: %s\n- 범위 밖: %s\n- 기능 코디네이터 허브: %s\n- 통합 브랜치: %s' "$(oneline "$GOAL")" "$(oneline "$VERIFY")" "$(oneline "$OUT_OF")" "$HUB" "$BRANCH")" --json | jq -r '.id // empty') || true
    [ -n "$EPIC" ] || die "epic 생성 실패 — cd $CFG_BOARD && bd create ... 를 직접 실행해 오류 확인" 1
  fi
fi

render_role() { # 템플릿 → 역할 파일
  sed -e "s|{{NAME}}|$NAME|g" -e "s|{{TICKET}}|$TICKET|g" -e "s|{{BRANCH}}|$BRANCH|g" -e "s|{{BASE}}|$BASE_REF|g" \
      -e "s|{{EPIC}}|$EPIC|g" -e "s|{{SLUG}}|$SLUG|g" "$CFG_ROLE_TMPL"
}
HUB_ARGS=(--slug "$HUB_SLUG" --name "$NAME 기능" --role-file "$ROLE_OUT" --model "$CFG_MODEL" --base "$BASE_REF" --branch "$BRANCH")

if [ "$DRY" = 1 ]; then
  PLAN=$(REPO="$CFG_REPO" WS_ROOT="$CFG_WS_ROOT" bash "$CFG_CREATE_HUB" "${HUB_ARGS[@]}" --dry-run 2>&1) || die "create_hub.sh --dry-run 실패: $PLAN" 1
  STATUS=dry_run HUB_OUT="$PLAN"
else
  mkdir -p "$CFG_ROLE_OUT" && render_role > "$ROLE_OUT"
  set +e
  HUB_OUT=$(REPO="$CFG_REPO" WS_ROOT="$CFG_WS_ROOT" bash "$CFG_CREATE_HUB" "${HUB_ARGS[@]}" 2>&1); RC=$?
  set -e
  case "$RC" in
    0) STATUS=opened ;;
    3) STATUS=already_running ;;   # 살아 있는 코디네이터 있음 → 재실행 안전
    *) die "허브 생성·기동 실패(종료 $RC): $(printf '%s' "$HUB_OUT" | tail -3) — epic $EPIC 은 남음. 원인 해결 뒤 같은 명령 재실행" 1 ;;
  esac
fi

if [ "$DRY" = 0 ] && [ -n "$EPIC" ]; then
  python3 "$CFG_HUB_REPORT" add --hub "$HUB_SLUG" --task 없음 --kind info --card "$EPIC" \
    --text "기능 시작: $NAME ($TICKET, 통합 브랜치 $BRANCH)" >/dev/null 2>&1 || echo "⚠️ 기능 스레드 시작 예약 실패 — hub_report.py add --hub $HUB_SLUG --card $EPIC 로 직접 기록" >&2
fi

if [ "$JSON" = 1 ]; then
  jq -nc --arg s "$STATUS" --arg slug "$SLUG" --arg t "$TICKET" --arg b "$BRANCH" --arg bs "$BRANCH_STATE" --arg base "$BASE_REF" \
    --arg hub "$HUB" --arg e "$EPIC" --arg es "$EPIC_STATE" --arg role "$ROLE_OUT" --arg out "$HUB_OUT" \
    '{status:$s, slug:$slug, ticket:$t, branch:$b, branch_state:$bs, base:$base, hub:$hub, epic:$e, epic_state:$es, role_file:$role, hub_output:$out}'
else
  echo "상태: $STATUS"
  echo "브랜치: $BRANCH ($BRANCH_STATE, 기준 $BASE_REF)"
  echo "허브: $HUB"
  echo "epic: $EPIC ($EPIC_STATE, label $LABEL)"
  echo "역할 파일: $ROLE_OUT"
  printf '%s\n' "$HUB_OUT"
fi
