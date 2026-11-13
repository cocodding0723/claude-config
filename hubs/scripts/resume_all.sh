#!/usr/bin/env bash
# Orca 재시작 뒤 허브 코디네이터 전체 재기동(--resume). 허브 목록은 hubs.tsv, 이어받기 문구는 hub_status.py prompt 가 만든다.
set -uo pipefail

# 링크로 설치돼도 실제 위치 기준으로 hubs.tsv·hub_status.py 를 찾음
SRC="${BASH_SOURCE[0]}"
while [ -L "$SRC" ]; do T=$(readlink "$SRC"); case "$T" in /*) SRC="$T" ;; *) SRC="$(dirname "$SRC")/$T" ;; esac; done
HERE=$(cd "$(dirname "$SRC")" && pwd -P)
TSV="${HUBS_TSV:-$HERE/../hubs.tsv}"
[ -f "$TSV" ] || TSV="$HERE/hubs.tsv"
STATUS_PY="${HUB_STATUS_PY:-$HERE/hub_status.py}"
DUPS_PY="${HUB_DUPS_PY:-$HERE/hub_dups.py}"
CREATE="${CREATE_HUB:-$HOME/.claude/skills/orca-hub-create/scripts/create_hub.sh}"
DRY=0 ONLY=()

usage() {
  cat <<'E'
사용: resume_all.sh [--dry-run] [--only <slug>]... [--help]

  hubs.tsv 의 허브마다 코디네이터를 --resume 으로 다시 띄운다. 이어받기 문구는
  hub_status.py prompt(상태 파일 ~/.config/lrm-hubs/hub-status/<slug>.json)가 만든다.
  살아 있는 claude 코디네이터(연결·쓰기 가능·고아 아님·마지막 출력 HUB_ALIVE_MIN 분 이내, 워커 제외)가 있는 허브는 건너뛴다.
  한 허브에 코디네이터 터미널이 여럿이면 중복 경고 1줄과 정리 제안(hub_dups.py clean)을 낸다. 닫지는 않는다.
  --dry-run       띄우지 않고 허브별 실행할 명령·문구만 출력(터미널 목록은 읽기만)
  --only <slug>   지정한 허브만(반복 가능)

환경: HUB_ALIVE_MIN 살아 있음 기준 분(기본 240), HUB_DUPS_PY hub_dups.py 경로, HUBS_TSV 허브 목록 파일, LRM_HUB_STATUS_DIR 상태 폴더, CREATE_HUB create_hub.sh 경로
종료 코드: 0 성공, 1 허브 기동 실패 있음, 2 인자·목록 오류
예: resume_all.sh --dry-run
E
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --only) [ -n "${2:-}" ] || die "--only 에 slug 가 필요함"; ONLY+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'ERROR: 알 수 없는 인자: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done
[ -f "$TSV" ] || die "허브 목록 없음: $TSV — HUBS_TSV 로 경로 지정"
[ -f "$STATUS_PY" ] || die "hub_status.py 없음: $STATUS_PY"
[ -f "$DUPS_PY" ] || die "hub_dups.py 없음: $DUPS_PY — HUB_DUPS_PY 로 경로 지정"

# 터미널 목록은 한 번만 조회. 조회 실패를 "허브 없음"으로 착각해 살아 있는 허브를 중복 기동하지 않게 실패 시 중단
TERMS=$(mktemp "${TMPDIR:-/tmp}/resume-terms.XXXXXX") || die "임시 파일 생성 실패 — TMPDIR 확인"
trap 'rm -f "${TERMS:?}"' EXIT
CHECK_PY='
import json, sys
ts = json.load(open(sys.argv[1]))["result"]["terminals"]
if not isinstance(ts, list):
    sys.exit(1)
for t in ts:
    if not isinstance(t, dict) or not isinstance(t.get("worktreePath"), str):
        sys.exit(1)
    if not isinstance(t.get("agentIdentity"), (str, type(None))) or not isinstance(t.get("connected", False), bool):
        sys.exit(1)
'
if ! orca terminal list --json > "$TERMS" 2>"$TERMS.err" || ! python3 -c "$CHECK_PY" "$TERMS" 2>/dev/null; then
  printf 'ERROR: orca terminal list 조회 실패(%s) — Orca 실행 상태·응답 형식을 확인하고 재시도\n' "$(head -c 200 "$TERMS.err" 2>/dev/null | tr '\n' ' ')" >&2
  rm -f "$TERMS.err"; exit 1
fi
rm -f "$TERMS.err"

# 살아 있는 claude 코디네이터 여부(연결·쓰기 가능·고아 아님·마지막 출력 HUB_ALIVE_MIN 분 이내, 워커 터미널 제외). 판정은 hub_dups.py alive
# 종료 코드: 0 살아 있음, 1 없음, 2 예상 밖 실패(없음으로 해석하면 중복 기동 위험). 출력: "<살아 있음> <전체> <유지 후보 핸들>"
alive() { python3 "$DUPS_PY" alive --slug "$1" --terms "$TERMS" 2>/dev/null; }

# 명령 한 줄 출력용 셸 인용. printf %q 는 C 로케일에서 한글을 바이트로 깨뜨려 파이썬 사용. 줄바꿈·탭 값은 $'..' 로 한 줄 유지
quote_cmd() {
  python3 -c '
import re, shlex, sys
def q(a):
    if re.fullmatch(r"[\w@%+=:,./-]+", a):
        return a
    if re.search(r"[\n\t\r]", a):
        return "$\x27" + a.replace("\\", "\\\\").replace("\x27", "\\\x27").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r") + "\x27"
    return shlex.quote(a)
print("$ " + " ".join(q(a) for a in sys.argv[1:]))
' "$@"
}

expand() { case "$1" in "~/"*) printf '%s' "$HOME/${1#\~/}" ;; -) printf '' ;; *) printf '%s' "$1" ;; esac; }

# --only 로 지정한 slug 가 목록에 있는지 먼저 확인
for o in ${ONLY[@]+"${ONLY[@]}"}; do
  grep -v '^[[:space:]]*#' "$TSV" | cut -f1 | grep -Fxq -- "$o" || die "slug '$o' 가 $TSV 에 없음 — 목록의 첫 칸 중 하나를 지정"
done

FAILED=0
# 부모 칸(여섯째)이 빈 허브를 먼저 띄움. 하위 허브는 한 층뿐이라 두 번 훑으면 순서가 맞음
ordered() { awk -F'\t' '/^[[:space:]]*(#|$)/ { next } ($6 == "" || $6 == "-")' "$TSV"; awk -F'\t' '/^[[:space:]]*(#|$)/ { next } !($6 == "" || $6 == "-")' "$TSV"; }
while IFS=$'\t' read -r SLUG NAME ROLE WSROOT REPO _PARENT; do
  case "$SLUG" in ''|'#'*) continue ;; esac
  if [ ${#ONLY[@]} -gt 0 ]; then
    hit=0; for o in "${ONLY[@]}"; do [ "$o" = "$SLUG" ] && hit=1; done
    [ "$hit" = 1 ] || continue
  fi
  AOUT=$(alive "$SLUG"); A=$?
  [ "$A" -le 1 ] || { echo "ERROR: $NAME 생존 판정 실패 — orca terminal list 응답을 해석하지 못함. 중복 기동을 막으려 중단, Orca 상태 확인 후 재시도" >&2; exit 1; }
  read -r N_LIVE N_ALL KEEP <<< "$AOUT"
  if [ "${N_ALL:-0}" -gt 1 ]; then
    echo "경고: $NAME 코디네이터 ${N_ALL}개(살아 있음 ${N_LIVE}) 중복 — 정리 제안: python3 $DUPS_PY clean --slug $SLUG --keep ${KEEP:--} --dry-run (확인 뒤 --dry-run 제거)"
  fi
  if [ "$A" = 0 ]; then echo "== $NAME: 살아 있음, 건너뜀"; continue; fi
  echo "== $NAME"
  PROMPT=$(python3 "$STATUS_PY" prompt --slug "$SLUG") || PROMPT="상태 파일 확인 실패 — HANDOFF.md 와 run-list 로 이어받기"
  CMD=(env)
  W=$(expand "$WSROOT"); [ -n "$W" ] && CMD+=("WS_ROOT=$W")
  R=$(expand "$REPO"); [ -n "$R" ] && CMD+=("REPO=$R")
  CMD+=(bash "$CREATE" --slug "$SLUG" --name "$NAME" --role-file "$(expand "$ROLE")" --resume "$PROMPT")
  if [ "$DRY" = 1 ]; then
    quote_cmd "${CMD[@]}"
    continue
  fi
  "${CMD[@]}" 2>&1 | tail -1
  [ "${PIPESTATUS[0]}" = 0 ] || { FAILED=$((FAILED + 1)); echo "ERROR: $NAME 기동 실패 — 위 출력 확인 후 --only $SLUG 로 재시도" >&2; }
done < <(ordered)

echo "확인: Codex 실행 옵션에 workspace-write 가 없어야 함 → ps -axww -o command | grep '^codex ' | grep -c workspace-write"
[ "$FAILED" = 0 ]
