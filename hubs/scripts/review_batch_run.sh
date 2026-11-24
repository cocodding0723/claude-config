#!/bin/bash
# 모아 받기 검수 정기 실행(launchd 18:00). 답 처리 → 모아 올리기. 실패·고칠 점은 경고 보고로 알림(로그만 남으면 아무도 모름)
set -u
BOARD_DONE="${LRM_BOARD_DONE:-$HOME/orca/board/bin/board-done}"
HUB_REPORT="${LRM_HUB_REPORT:-$HOME/.config/lrm-hubs/hub_report.py}"
LOCK="${LRM_HUB_DIR:-$HOME/.config/lrm-hubs}/review-batch.lock"
PY="${LRM_PYTHON3:-python3}"

# 손으로 돌린 --review-batch 와 겹치면 같은 기능을 두 장에 실을 수 있어 잠금
if ! mkdir "$LOCK" 2>/dev/null; then echo "다른 실행 중($LOCK) — 건너뜀"; exit 0; fi
trap 'rmdir "$LOCK"' EXIT

warn() {
  "$PY" "$HUB_REPORT" add --auto --hub main --task 없음 --kind warn --text "$1" >/dev/null 2>&1 \
    || echo "경고 보고 실패: $1" >&2
}

ans=$("$BOARD_DONE" --review-batch-answers auto --json 2>&1); rc1=$?
echo "$ans"
if [ "$rc1" != 0 ]; then
  warn "모아 받기 답 처리 실패(종료 $rc1)
- 원인: $(printf '%s' "$ans" | tail -1 | cut -c1-150)
- 할 일: 로그 review-batch.launchd.log 확인 뒤 다시 실행"
else
  fixes=$(printf '%s' "$ans" | "$PY" -c 'import json,sys
try: d=json.loads(sys.stdin.read())
except ValueError: sys.exit()
print(" ".join(x["id"] for x in d.get("answers",[]) if x.get("verdict")=="fix"))')
  [ -n "$fixes" ] && warn "모아 받기 고칠 점 답 — 기능 $fixes 를 검수 단계로 되돌림
- 할 일: 이 기능 코디네이터를 다시 열어 고칠 점을 수정 카드로 처리
- 참고: board-done --epic <기능> <이름> --ask-review 로 다시 검수"
fi

out=$("$BOARD_DONE" --review-batch auto 2>&1); rc2=$?
echo "$out"
if [ "$rc2" != 0 ]; then
  warn "모아 받기 카드 올리기 실패(종료 $rc2)
- 원인: $(printf '%s' "$out" | grep -m1 -v '^$' | cut -c1-150)
- 할 일: 원인을 고친 뒤 다시 실행. 표시는 그대로라 다음 회차에 다시 모음"
fi
[ "$rc1" = 0 ] && [ "$rc2" = 0 ]
