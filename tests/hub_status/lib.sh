#!/usr/bin/env bash
# hub_status.py·resume_all.sh 시험 공용 준비(run.sh·resume.sh 가 source). 임시 폴더·가짜 orca(PATH 앞)·환경 변수만 씀. 실제 ~/.config/lrm-hubs·터미널·허브 기동은 건드리지 않음.

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
HS="$REPO/hubs/scripts/hub_status.py"
RA="$REPO/hubs/scripts/resume_all.sh"
TSV="$REPO/hubs/hubs.tsv"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-hubstatus.XXXXXX")
trap 'rm -rf "${SB:?}"' EXIT
export LRM_HUB_STATUS_DIR="$SB/status"; mkdir -p "$SB/status"
BIN="$SB/bin"; mkdir -p "$BIN"
# worker-list 는 FAKE_WORKERS 파일이 있을 때만 응답(없으면 실패), 그 밖 호출은 터미널 목록 응답
cat > "$BIN/orca" <<'FAKE'
#!/bin/bash
echo "$*" >> "${FAKE_LOG:-/dev/null}"
[ "${FAKE_ORCA_FAIL:-}" = 1 ] && exit 1
if [ "$1 $2" = "orchestration worker-list" ]; then cat "${FAKE_WORKERS:-/nonexistent}" 2>/dev/null || exit 1; exit 0; fi
cat "${FAKE_TERMINALS:?}"
FAKE
chmod +x "$BIN/orca"
echo '{"result":{"terminals":[]}}' > "$SB/terms-empty.json"
# 생존 판정은 프로세스 표 기준(실제 lsof·ps 를 쓰지 않음). 기본은 빈 표 = 살아 있는 claude 없음
: > "$SB/procs-empty.txt"; export HUB_PROC_TABLE="$SB/procs-empty.txt"
export FAKE_LOG="$SB/orca.log" FAKE_TERMINALS="$SB/terms-empty.json" PATH="$BIN:$PATH"
NOW_MS=$(python3 -c "import time;print(int(time.time()*1000))")
# 터미널 JSON 한 개: tm <경로> <agent> <connected> <writable> <orphaned> <출력 경과 분> [handle]
tm() { printf '{"handle":"%s","worktreePath":"%s","agentIdentity":"%s","connected":%s,"writable":%s,"orphaned":%s,"lastOutputAt":%s,"title":"t"}' "${7:-term_$RANDOM}" "$1" "$2" "$3" "$4" "$5" "$((NOW_MS - $6 * 60000))"; }
FAKE_TOKEN="xo""xb-1234""56789012-abcdef"  # 비밀 패턴 검사기에 안 걸리게 조립
hs() { python3 "$HS" "$@"; }
OLDT=$(python3 -c "from datetime import datetime,timedelta,timezone;print((datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(timespec='seconds'))")
NEWT=$(python3 -c "from datetime import datetime,timedelta,timezone;print((datetime.now(timezone.utc)-timedelta(hours=23)).isoformat(timespec='seconds'))")
mkfile() { printf '{"slug":"%s","updatedAt":"%s","runId":"run_old","tasks":["옛 작업"],"branches":[],"workers":[],"next":"옛 다음","blockers":[]}\n' "$1" "$2" > "$SB/status/$1.json"; }
mkfile old25 "$OLDT"; mkfile new23 "$NEWT"
NASTY=$'따옴표 " \' 백틱 `id` $(touch '"$SB"'/PWNED) ; rm -rf / \n둘째 줄 $HOME \\n'
