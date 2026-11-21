#!/bin/bash
# 기능 코디네이터 프로젝트 등록: hubs/feature_projects.tsv 를 손으로 고치지 않고 명령으로 보기·추가·바꾸기
set -euo pipefail

SRC="${BASH_SOURCE[0]}"
while [ -L "$SRC" ]; do T=$(readlink "$SRC"); case "$T" in /*) SRC="$T" ;; *) SRC="$(dirname "$SRC")/$T" ;; esac; done
HERE=$(cd "$(dirname "$SRC")" && pwd -P)
TSV="${LRM_FEATURE_PROJECTS:-$HERE/../feature_projects.tsv}"

usage() {
  cat <<'USAGE'
사용: feature_project.sh list [--json]
      feature_project.sh add <키> --repo <저장소> --jira required|optional|none [--ticket-re <정규식>]
                             [--branch sprint|feature|type] [--base <브랜치>] [--ws-root <폴더>] [--replace] [--dry-run]
feature_open.sh 가 읽는 프로젝트 표(hubs/feature_projects.tsv)를 보고 고친다.

add 는 처음 셋업할 때 쓴다. Jira 연동은 --jira 로 반드시 고른다(기본값 없음):
  required  티켓 없이는 기능을 열지 않음(게임 저장소처럼 Jira 로 일을 관리하는 곳)
  optional  티켓이 있으면 씀
  none      Jira 를 쓰지 않음(개인·설정 저장소). 티켓을 주면 feature_open 이 거부
나머지 기본값:
  --ticket-re  --jira 가 none 이 아니면 필수. 예 '^LRM-[0-9]+$'
  --branch     none 이면 type(<타입>/<작업명>), 그 밖은 feature(feature/<작업명>). sprint 는 스프린트 폴더 규칙
  --base       저장소의 origin 기본 브랜치(origin/HEAD), 없으면 현재 브랜치
  --ws-root    ~/orca/workspaces/<저장소 폴더 이름>
  --replace    같은 키가 있으면 바꿈(없으면 거부)
종료 코드: 0 성공 · 2 인자 오류 · 3 표·저장소 없음 · 4 같은 키가 이미 있음
예:
  feature_project.sh add claude-config --repo ~/orca/claude-config --jira none
  feature_project.sh add jobmaker --repo ~/orca/JobMaker --jira optional --ticket-re '^JM-[0-9]+$' --dry-run
USAGE
}
die() { echo "$1" >&2; exit "${2:-2}"; }
rows() { awk -F'\t' '!/^#/ && NF >= 7' "$TSV"; }

[ -f "$TSV" ] || die "프로젝트 표 없음: $TSV — LRM_FEATURE_PROJECTS 로 지정" 3
CMD="${1:-}"; [ $# -gt 0 ] && shift
case "$CMD" in
  -h|--help|"") usage; exit 0 ;;
  list)
    if [ "${1:-}" = --json ]; then
      rows | jq -Rnc '[inputs | split("\t") | {key:.[0], repo:.[1], ws_root:.[2], jira:.[3], ticket_re:.[4], branch:.[5], base:.[6]}]'
    else
      { printf '키\t저장소\tJira\t브랜치\t기준\n'; rows | awk -F'\t' '{print $1"\t"$2"\t"$4"\t"$6"\t"$7}'; } | column -t -s$'\t'
    fi
    exit 0 ;;
  add) ;;
  *) die "알 수 없는 명령: $CMD — list | add (--help 참고)" ;;
esac

KEY="${1:-}"; [ $# -gt 0 ] && shift
[[ "$KEY" =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "키 형식 오류: '$KEY' — 영문 소문자·숫자·- (예 claude-config)"
REPO="" JIRA="" RE="" BRANCH="" BASE="" WS="" REPLACE=0 DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo|--jira|--ticket-re|--branch|--base|--ws-root) [ $# -ge 2 ] || die "$1 값 필요"
      case "$1" in --repo) REPO="$2";; --jira) JIRA="$2";; --ticket-re) RE="$2";; --branch) BRANCH="$2";; --base) BASE="$2";; --ws-root) WS="$2";; esac; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "알 수 없는 인자: $1 — --help 참고" ;;
  esac
done
[ -n "$REPO" ] || die "--repo 필요"
[[ "$JIRA" =~ ^(required|optional|none)$ ]] || die "--jira 필요: required | optional | none 중 하나를 고름(기본값 없음)"
REPO_ABS="${REPO/#\~/$HOME}"
git -C "$REPO_ABS" rev-parse --git-dir >/dev/null 2>&1 || die "git 저장소가 아님: $REPO" 3
if [ "$JIRA" = none ]; then
  [ -z "$RE" ] || die "--jira none 에는 --ticket-re 를 주지 않음"; RE="-"
else
  [ -n "$RE" ] || die "--jira $JIRA 는 --ticket-re 필요 — 예 '^LRM-[0-9]+\$'"
  RC=0; grep -E "$RE" </dev/null >/dev/null 2>&1 || RC=$?; [ "$RC" -le 1 ] || die "--ticket-re 정규식 오류: $RE"
fi
BRANCH="${BRANCH:-$([ "$JIRA" = none ] && echo type || echo feature)}"
[[ "$BRANCH" =~ ^(sprint|feature|type)$ ]] || die "--branch 오류: $BRANCH — sprint | feature | type"
[ "$BRANCH" != sprint ] || [ "$JIRA" != none ] || die "--branch sprint 는 브랜치에 티켓을 넣으므로 --jira none 과 함께 쓸 수 없음"
if [ -z "$BASE" ]; then
  BASE=$(git -C "$REPO_ABS" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||') || true
  [ -n "$BASE" ] || BASE=$(git -C "$REPO_ABS" branch --show-current)
fi
WS="${WS:-~/orca/workspaces/$(basename "$REPO_ABS")}"
home_rel() { case "$1" in "$HOME"/*) printf '~/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac; }
LINE=$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s' "$KEY" "$(home_rel "$REPO_ABS")" "$(home_rel "${WS/#\~/$HOME}")" "$JIRA" "$RE" "$BRANCH" "$BASE")

EXISTS=0; rows | cut -f1 | grep -qx "$KEY" && EXISTS=1
[ "$EXISTS" = 0 ] || [ "$REPLACE" = 1 ] || die "이미 있는 키: $KEY — 바꾸려면 --replace" 4
echo "$([ "$EXISTS" = 1 ] && echo 바꿈 || echo 추가): $LINE"
[ "$DRY" = 0 ] || exit 0
TMP="$TSV.tmp.$$"
if [ "$EXISTS" = 1 ]; then
  awk -F'\t' -v k="$KEY" -v line="$LINE" '!/^#/ && $1 == k { print line; next } { print }' "$TSV" > "$TMP"
else
  { cat "$TSV"; printf '%s\n' "$LINE"; } > "$TMP"
fi
mv "$TMP" "$TSV"
