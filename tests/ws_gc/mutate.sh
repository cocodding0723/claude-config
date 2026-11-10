#!/usr/bin/env bash
# 변이 확인: ws_gc.sh 사본에 결함을 하나씩 심고 tests/ws_gc/run.sh 가 실패하는지 본다. 놓친 변이가 있으면 종료 코드 1.
# 원본은 바꾸지 않고 임시 사본(mktemp -d)에서만 실행함.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SRC="$HERE/../../hubs/scripts/ws_gc.sh"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/cc-wsgc-mut.XXXXXX")
trap 'rm -rf "${WORK:?}"' EXIT
MISSED=0

mutant() { # id 설명 perl치환식
  local id="$1" desc="$2" expr="$3" out
  cp "$SRC" "$WORK/ws_gc.sh"; perl -0pi -e "$expr" "$WORK/ws_gc.sh"
  if cmp -s "$SRC" "$WORK/ws_gc.sh"; then echo "$id 변이 정의 오류: 사본이 바뀌지 않음" >&2; MISSED=1; return; fi
  out=$(GC_SCRIPT="$WORK/ws_gc.sh" bash "$HERE/run.sh" 2>&1); local rc=$?
  if [ "$rc" != 0 ]; then echo "검출 $id — $desc ($(printf '%s\n' "$out" | grep -c '^FAIL')건 실패)"; else echo "놓침 $id — $desc"; MISSED=1; fi
}

mutant M1 "미커밋 변경 검사 제거(조건 2)"        's/\[ "\$N_REAL" -gt 0 \]/[ "\$N_REAL" -gt 99999 ]/'
mutant M2 "기본 모드를 --apply 로 변경(dry-run 무삭제)" 's/APPLY=0 DAYS=/APPLY=1 DAYS=/'
mutant M3 "살아 있는 터미널 검사 제거"          's/grep -Fxq -- "\$p" "\$TMP\/live" && \{ REASON=live; return 1; \}//'
mutant M4 "PR OPEN 검사 제거"                    's/then REASON=pr_open; DETAIL="\$PR_LABEL"; return 1; fi/then :; fi/'
exit "$MISSED"
