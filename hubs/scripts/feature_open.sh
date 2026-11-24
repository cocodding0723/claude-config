#!/bin/bash
# 기능 코디네이터 생성: 기능 통합 브랜치·허브 작업 공간·작업판 epic·Opus 코디네이터. 허브 생성·기동은 create_hub.sh 에 맡김
set -euo pipefail
export PATH="$PATH:/Applications/Orca.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin"

SRC="${BASH_SOURCE[0]}"
while [ -L "$SRC" ]; do T=$(readlink "$SRC"); case "$T" in /*) SRC="$T" ;; *) SRC="$(dirname "$SRC")/$T" ;; esac; done
HERE=$(cd "$(dirname "$SRC")" && pwd -P)
CFG_PROJECTS="${LRM_FEATURE_PROJECTS:-$HERE/../feature_projects.tsv}"   # 프로젝트별 저장소·티켓·브랜치 규칙
CFG_BOARD="${LRM_BOARD:-$HOME/orca/board}"
CFG_CREATE_HUB="${LRM_CREATE_HUB:-$HERE/../../skills/orca-hub-create/scripts/create_hub.sh}"
CFG_ROLE_TMPL="${LRM_FEATURE_ROLE:-$HERE/../roles/role-feature.txt}"
CFG_ROLE_OUT="${LRM_ROLES_DIR:-$HOME/.config/lrm-hubs/roles}"   # restart_hub.sh 가 role-<이름>.txt 로 읽는 곳
# 기능 코디네이터는 대기·배정·보고가 대부분이라 Sonnet·medium(11-24 사용자 승인, 토큰 절약 규칙 11-06). 설계·결정은 Opus 작업자
CFG_MODEL="${LRM_FEATURE_MODEL:-claude-sonnet-5-5}"
CFG_EFFORT="${LRM_FEATURE_EFFORT:-medium}"
CFG_SPRINT_FILE="${LRM_SPRINT_FILE:-$HOME/.config/lrm-hubs/sprint-start}"   # 이 스프린트의 시작일 한 줄(YYYYMMDD). 스프린트가 바뀔 때 한 번 고침
CFG_HUB_REPORT="${LRM_HUB_REPORT:-$HERE/hub_report.py}"   # 기능 Slack 스레드 시작 보고를 예약하는 CLI

usage() {
  cat <<'USAGE'
사용: feature_open.sh [--project <키>] [--ticket <티켓>] --name "<기능 한글>" [--base <브랜치>] [--slug <영문 작업명>] [--type <타입>] [--long]
                       [--goal "<목표 한 문장>" --verify "<확인 방법 한 문장>" [--out "<범위 밖>"]] [--skip 급함] [--dry-run] [--json]
기능 하나를 맡을 코디네이터를 만든다. 다시 실행해도 있는 것은 재사용하고 빠진 것만 만든다.
프로젝트별 값(저장소·티켓 필요 여부·브랜치 규칙·기준 브랜치)은 hubs/feature_projects.tsv 한 곳에서 읽음. 아래 브랜치 설명은 lrm(기본) 기준
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
  --project 키    feature_projects.tsv 의 key. 생략하면 티켓 형식에 맞는 행, 그것도 없으면 첫 행(lrm)
  --ticket 티켓   Jira 티켓. 프로젝트의 ticket 칸이 required 면 필수, none 이면 주지 않음
  --name 이름     기능 이름, 한글 가능(필수)
  --base 브랜치   통합 브랜치를 만들 기준. 생략하면 프로젝트의 base 칸. 예 dev → origin/dev 가 있으면 그것을 씀
  --slug 영문     영문 작업명(소문자·숫자·-). 브랜치·허브 이름에 씀. 생략하면 허브 이름은 티켓 소문자(lrm-n), 브랜치는 티켓 번호까지만(경고)
  --type 타입     feat|fix|chore|refactor|docs|design (기본 feat). 버그 수정은 fix
  --long          여러 스프린트에 걸치는 모듈 작업. 브랜치를 feature/<작업명> 으로 만듦
  --sprint 날짜   이 스프린트의 시작일 YYYYMMDD. 생략하면 LRM_SPRINT_DATE, 그다음 설정 파일
  --goal 문장     접수: 만들 것 한 문장(사용자 원문 그대로). 기능 카드 설명의 "- 목표:" 칸에 적음
  --verify 문장   접수: 끝났는지 확인할 방법 한 문장. "- 확인 방법:" 칸에 적음
  --out 문장      접수: 이번에 하지 않는 것. "- 범위 밖:" 칸에 적음
                  세 칸은 새 기능 카드를 만들 때만 적음. 안 주면 빈 칸으로 남고 코디네이터가 채워야 접수 단계를 떠날 수 있음(board-stage)
  --skip 항목     급한 일: 준비를 건너뜀. 급함(이해·계획 둘 다) | 이해 | 계획, 쉼표로 여럿. 사용자가 그렇게 하라고 했을 때만 줌
                  기능 카드에 label skip:understand·skip:plan 이 붙어 계획 승인 없이 구현으로 감. 검증·검수·병합 승인은 그대로 거침
                  새 기능 카드를 만들 때만 적음. 이미 연 기능은 board-skip 으로 걸거나 품
  --dry-run       아무것도 만들지 않고 할 일만 출력
  --json          결과를 JSON 한 줄로 출력
환경: REPO, WS_ROOT, LRM_BOARD(작업판 폴더), LRM_CREATE_HUB, LRM_FEATURE_ROLE, LRM_ROLES_DIR, LRM_SPRINT_DATE(스프린트 시작일), LRM_SPRINT_FILE(스프린트 시작일 설정 파일, 기본 ~/.config/lrm-hubs/sprint-start)
종료 코드: 0 성공, 1 단계 실패, 2 인자 오류, 3 환경 오류(bd·orca·jq·git 없음, 기준 브랜치 없음)
예:
  feature_open.sh --ticket LRM-612 --name "우편함" --base dev --dry-run
  feature_open.sh --ticket LRM-612 --name "우편함" --base dev --slug mailbox --json
  feature_open.sh --project claude-config --name "리마인드 정확도" --slug remind-accuracy --type fix --dry-run
USAGE
}
die() { echo "$1" >&2; exit "${2:-2}"; }
oneline() { printf '%s' "$1" | tr '\n' ' '; }  # 접수 칸은 한 줄(줄이 갈리면 칸 이름 줄에서 떨어짐)

PROJECT="" TICKET="" NAME="" BASE="" SLUG="" TYPE="feat" LONG=0 DRY=0 JSON=0 GOAL="" VERIFY="" OUT_OF="" SPRINT_ARG="" SKIP_ARG=""
BRANCH_TYPES="feat|fix|chore|refactor|docs|design"  # 게임 저장소 CLAUDE.md 의 타입 접두
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --project|--ticket|--name|--base|--slug|--type|--goal|--verify|--out|--sprint|--skip) [ $# -ge 2 ] || die "$1 값 필요 — --help 참고"
      case "$1" in --project) PROJECT="$2";; --ticket) TICKET="$2";; --name) NAME="$2";; --base) BASE="$2";; --slug) SLUG="$2";; --type) TYPE="$2";;
        --goal) GOAL="$2";; --verify) VERIFY="$2";; --out) OUT_OF="$2";; --sprint) SPRINT_ARG="$2";; --skip) SKIP_ARG="$2";; esac; shift 2 ;;
    --long) LONG=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --json) JSON=1; shift ;;
    *) die "알 수 없는 인자: $1 — --help 참고" ;;
  esac
done
[ -n "$NAME" ] || die "--name 필요 — 예 feature_open.sh --ticket LRM-612 --name \"우편함\" --base dev"
[ -f "$CFG_PROJECTS" ] || die "프로젝트 표 없음: $CFG_PROJECTS — LRM_FEATURE_PROJECTS 로 지정" 3
# 프로젝트 행 고르기: --project > 티켓 형식에 맞는 첫 행 > 첫 행
ROW=$(awk -F'\t' -v want="$PROJECT" -v t="$TICKET" '
  /^#/ || NF < 7 { next }
  !first { first = $0 }
  want != "" { if ($1 == want) { print; exit } ; next }
  t != "" && $5 != "-" && t ~ $5 && !hit { hit = $0 }
  END { if (want == "") print (hit != "" ? hit : first) }' "$CFG_PROJECTS")
[ -n "$ROW" ] || die "프로젝트 없음: $PROJECT — $(awk -F'\t' '!/^#/ && NF>=7 {printf "%s ", $1}' "$CFG_PROJECTS")중 하나, 또는 $CFG_PROJECTS 에 행 추가"
IFS=$'\t' read -r P_KEY P_REPO P_WS P_TICKET P_TICKET_RE P_BRANCH P_BASE <<< "$ROW"
CFG_REPO="${REPO:-${P_REPO/#\~/$HOME}}"
CFG_WS_ROOT="${WS_ROOT:-${P_WS/#\~/$HOME}}"
BASE="${BASE:-$P_BASE}"
case "$P_TICKET" in
  required) [ -n "$TICKET" ] || die "$P_KEY 프로젝트는 티켓 필요 — --ticket <티켓> 을 주거나 jira-issue 로 먼저 발급" ;;
  none) [ -z "$TICKET" ] || die "$P_KEY 프로젝트는 Jira 를 쓰지 않음 — --ticket 빼고 다시 실행" ;;
  optional) ;;
  *) die "$CFG_PROJECTS 의 $P_KEY ticket 칸 오류: $P_TICKET — required|optional|none" 3 ;;
esac
[ -z "$TICKET" ] || [ "$P_TICKET_RE" = - ] || [[ "$TICKET" =~ $P_TICKET_RE ]] || die "티켓 형식 오류: $TICKET — $P_KEY 프로젝트 형식 $P_TICKET_RE"
[ -n "$BASE" ] && [ "$BASE" != - ] || die "--base 필요 — $P_KEY 행에 base 칸이 없음"
[[ "$TYPE" =~ ^($BRANCH_TYPES)$ ]] || die "타입 오류: $TYPE — $BRANCH_TYPES 중 하나"
# 건너뛰기 항목 → label. 이름은 board/lib/board_flow.py 의 SKIP_ITEMS·SKIP_BUNDLES 와 같아야 함
SKIP_LABELS="" SKIP_NAMES=""
if [ -n "$SKIP_ARG" ]; then
  IFS=',' read -r -a SKIP_WORDS <<< "$SKIP_ARG"
  WANT_U=0 WANT_P=0
  for w in "${SKIP_WORDS[@]}"; do
    case "$(printf '%s' "$w" | tr -d '[:space:]')" in
      급함) WANT_U=1; WANT_P=1 ;; 이해|understand) WANT_U=1 ;; 계획|plan) WANT_P=1 ;;
      *) die "--skip 값 오류: $w — 급함 | 이해 | 계획 중에서(쉼표로 여럿). 검증·검수·병합 승인은 건너뛸 수 없음" ;;
    esac
  done
  [ "$WANT_U" = 1 ] && { SKIP_LABELS=",skip:understand"; SKIP_NAMES="이해"; }
  [ "$WANT_P" = 1 ] && { SKIP_LABELS="$SKIP_LABELS,skip:plan"; SKIP_NAMES="${SKIP_NAMES:+$SKIP_NAMES, }계획"; }
fi
NAMED=1; [ -n "$SLUG" ] || NAMED=0
[ -n "$SLUG" ] || [ -n "$TICKET" ] || die "티켓 없는 기능은 --slug <영문 작업명> 필요(허브·브랜치 이름)"
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
elif [ "$P_BRANCH" = type ]; then BRANCH="$TYPE/$SLUG"
elif [ "$P_BRANCH" = feature ]; then BRANCH="feature/$SLUG"
elif [ "$P_BRANCH" != sprint ]; then die "$CFG_PROJECTS 의 $P_KEY branch 칸 오류: $P_BRANCH — sprint|feature|type" 3
elif [ -z "$TICKET" ]; then BRANCH="feature/$SLUG"
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

# epic: 같은 label 의 열린 epic 이 있으면 재사용
EPIC=$(cd "$CFG_BOARD" && bd list -t epic -l "$LABEL" --json 2>/dev/null | jq -r '[.[] | select(.status != "closed")][0].id // empty') || EPIC=""
EPIC_STATE=exists
if [ -z "$EPIC" ]; then
  EPIC_STATE=new
  if [ "$DRY" = 1 ]; then EPIC="(생성 예정)"
  else
    EPIC=$(cd "$CFG_BOARD" && bd create "[${TICKET:-$P_KEY}] $NAME" -t epic -l "$LABEL,project:$P_KEY,flow:v1,said:v1,stage:intake,branch:$BRANCH,repo:$CFG_REPO,base:$BASE_REF$SKIP_LABELS" ${TICKET:+--external-ref "jira-$TICKET"} \
      -d "$(printf -- '- 목표: %s\n- 확인 방법: %s\n- 범위 밖: %s\n- 기능 코디네이터 허브: %s\n- 통합 브랜치: %s%s' "$(oneline "$GOAL")" "$(oneline "$VERIFY")" "$(oneline "$OUT_OF")" "$HUB" "$BRANCH" "${SKIP_NAMES:+$(printf '\n- 건너뛰기: %s (열 때 사용자 지정)' "$SKIP_NAMES")}")" --json | jq -r '.id // empty') || true
    [ -n "$EPIC" ] || die "epic 생성 실패 — cd $CFG_BOARD && bd create ... 를 직접 실행해 오류 확인" 1
  fi
fi

SKIP_APPLIED=""; [ "$EPIC_STATE" = new ] && SKIP_APPLIED="$SKIP_NAMES"  # 건너뛰기는 새 기능 카드를 만들 때만 적음
render_role() { # 템플릿 → 역할 파일
  sed -e "s|{{NAME}}|$NAME|g" -e "s|{{TICKET}}|${TICKET:-티켓 없음}|g" -e "s|{{BRANCH}}|$BRANCH|g" -e "s|{{BASE}}|$BASE_REF|g" \
      -e "s|{{EPIC}}|$EPIC|g" -e "s|{{SLUG}}|$SLUG|g" "$CFG_ROLE_TMPL"
  [ -n "$TICKET" ] || printf '\n티켓 없는 기능(프로젝트 %s): Jira 발급·상태 전환·댓글 단계와 계획서의 Jira 칸은 건너뛴다. 브랜치 규칙은 이 저장소(%s)의 CLAUDE.md 를 따르고, 작업자 브랜치는 위 규칙대로 {{BRANCH}}-<조각 이름> 으로 만든다.\n' "$P_KEY" "$CFG_REPO" | sed "s|{{BRANCH}}|$BRANCH|g"
}
HUB_ARGS=(--slug "$HUB_SLUG" --name "$NAME 기능" --role-file "$ROLE_OUT" --model "$CFG_MODEL" --effort "$CFG_EFFORT" --base "$BASE_REF" --branch "$BRANCH")

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
    --text "기능 시작: $NAME (${TICKET:+$TICKET, }통합 브랜치 $BRANCH)" >/dev/null 2>&1 || echo "⚠️ 기능 스레드 시작 예약 실패 — hub_report.py add --hub $HUB_SLUG --card $EPIC 로 직접 기록" >&2
fi

if [ "$JSON" = 1 ]; then
  jq -nc --arg s "$STATUS" --arg slug "$SLUG" --arg t "$TICKET" --arg p "$P_KEY" --arg b "$BRANCH" --arg bs "$BRANCH_STATE" --arg base "$BASE_REF" \
    --arg hub "$HUB" --arg e "$EPIC" --arg es "$EPIC_STATE" --arg role "$ROLE_OUT" --arg out "$HUB_OUT" --arg skip "$SKIP_APPLIED" \
    '{status:$s, project:$p, slug:$slug, ticket:$t, branch:$b, branch_state:$bs, base:$base, hub:$hub, epic:$e, epic_state:$es, role_file:$role, skips:$skip, hub_output:$out}'
else
  echo "상태: $STATUS"
  echo "프로젝트: $P_KEY (${TICKET:-티켓 없음})"
  echo "브랜치: $BRANCH ($BRANCH_STATE, 기준 $BASE_REF)"
  echo "허브: $HUB"
  echo "epic: $EPIC ($EPIC_STATE, label $LABEL)"
  [ -z "$SKIP_NAMES" ] || echo "건너뛰기: ${SKIP_APPLIED:-적용 안 함(이미 연 기능 — board-skip $EPIC <이름> 급함 --user-said \"<원문>\" 으로 검)}"
  echo "역할 파일: $ROLE_OUT"
  printf '%s\n' "$HUB_OUT"
fi
