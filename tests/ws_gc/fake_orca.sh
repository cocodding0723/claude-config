#!/bin/bash
# 가짜 orca. FAKE_DIR 의 worktrees·terminals·workers JSON 을 돌려주고 rm·worker-release 호출을 orca.calls 에 기록함.
# worktree rm 은 FAKE_ROOT(임시 작업 폴더) 아래 경로만 지움. FAKE_RM_REFUSE="이름 …" 이면 그 폴더는 --force 없이 거부(서브모듈 거부 흉내).
# FAKE_ORCA_FAIL=1 이면 모든 호출 실패(Orca 꺼짐 흉내).
D="${FAKE_DIR:?}"
echo "$*" >> "$D/orca.calls"
[ "${FAKE_ORCA_FAIL:-}" = 1 ] && { echo "orca 응답 없음" >&2; exit 1; }
case "$*" in
  "worktree list --limit 1000 --json") cat "$D/worktrees.json" ;;
  "terminal list --limit 1000 --json") cat "$D/terminals.json" ;;
  "orchestration worker-list --full --limit 100 --json") cat "$D/workers.json" ;;
  "orchestration worker-release --dispatch "*) echo '{"ok":true,"result":{}}' ;;
  "worktree rm --worktree path:"*)
    P="${4#path:}"; FORCE=0; case " $* " in *" --force "*) FORCE=1 ;; esac
    case "$P" in "${FAKE_ROOT:?}"/*) ;; *) echo "시험 폴더 밖 경로 거부: $P" >&2; exit 9 ;; esac
    case " ${FAKE_RM_REFUSE:-} " in *" $(basename "$P") "*) [ "$FORCE" = 1 ] || { echo "fatal: working trees containing submodules cannot be removed" >&2; exit 1; } ;; esac
    rm -rf "${P:?}"; echo '{"ok":true,"result":{}}' ;;
  *) echo '{"ok":false}'; exit 1 ;;
esac
