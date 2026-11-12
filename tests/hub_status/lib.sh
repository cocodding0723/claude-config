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
printf '#!/bin/bash\n[ "${FAKE_ORCA_FAIL:-}" = 1 ] && exit 1\ncat "${FAKE_TERMINALS:?}"\n' > "$BIN/orca"; chmod +x "$BIN/orca"
echo '{"result":{"terminals":[]}}' > "$SB/terms-empty.json"
export FAKE_TERMINALS="$SB/terms-empty.json" PATH="$BIN:$PATH"
FAKE_TOKEN="xo""xb-1234""56789012-abcdef"  # 비밀 패턴 검사기에 안 걸리게 조립
hs() { python3 "$HS" "$@"; }
OLDT=$(python3 -c "from datetime import datetime,timedelta,timezone;print((datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(timespec='seconds'))")
NEWT=$(python3 -c "from datetime import datetime,timedelta,timezone;print((datetime.now(timezone.utc)-timedelta(hours=23)).isoformat(timespec='seconds'))")
mkfile() { printf '{"slug":"%s","updatedAt":"%s","runId":"run_old","tasks":["옛 작업"],"branches":[],"workers":[],"next":"옛 다음","blockers":[]}\n' "$1" "$2" > "$SB/status/$1.json"; }
mkfile old25 "$OLDT"; mkfile new23 "$NEWT"
NASTY=$'따옴표 " \' 백틱 `id` $(touch '"$SB"'/PWNED) ; rm -rf / \n둘째 줄 $HOME \\n'
