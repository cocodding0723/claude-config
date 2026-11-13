#!/bin/bash
# 가짜 orca. FAKE_DIR 의 worktrees·terminals·workers JSON 을 돌려주고 rm·worker-release 호출을 orca.calls 에 기록함.
# worktree rm 은 FAKE_ROOT(임시 작업 폴더) 아래 경로만 지움. FAKE_RM_REFUSE="이름 …" 이면 그 폴더는 --force 없이 거부(서브모듈 거부 흉내).
# FAKE_RM_REASON="문구" 이면 force 없는 rm 을 그 문구로 거부(잠금 등 다른 사유 흉내).
# 문구 안의 @P@ 는 거부된 경로로 치환(실제 Orca 는 경로를 문구에 넣음).
# FAKE_SLEEP=초 이면 worktree list 를 그만큼 늦춤(동시 실행·잠금 시험).
# FAKE_ORCA_FAIL=1 이면 모든 호출 실패(Orca 꺼짐 흉내).
D="${FAKE_DIR:?}"
echo "$*" >> "$D/orca.calls"
[ "${FAKE_ORCA_FAIL:-}" = 1 ] && { echo "orca 응답 없음" >&2; exit 1; }
case "$*" in
  "worktree list --limit 1000 --json") [ -z "${FAKE_SLEEP:-}" ] || sleep "$FAKE_SLEEP"; cat "$D/worktrees.json" ;;
  "terminal list --limit 1000 --json") cat "$D/terminals.json" ;;
  "orchestration worker-list --full --limit 100 --json") cat "$D/workers.json" ;;
  "orchestration worker-release --dispatch "*) echo '{"ok":true,"result":{}}' ;;
  "worktree set --worktree path:"*) [ "${FAKE_SET_FAIL:-}" != 1 ] || { echo '{"ok":false}'; exit 1; }; echo '{"ok":true,"result":{}}' ;;
  "worktree rm --worktree path:"*)
    P="${4#path:}"; FORCE=0; case " $* " in *" --force "*) FORCE=1 ;; esac
    case "$P" in "${FAKE_ROOT:?}"/*) ;; *) echo "시험 폴더 밖 경로 거부: $P" >&2; exit 9 ;; esac
    case " ${FAKE_RM_REFUSE:-} " in *" $(basename "$P") "*) [ "$FORCE" = 1 ] || { echo "fatal: working trees containing submodules cannot be removed" >&2; exit 1; } ;; esac
    [ -z "${FAKE_RM_REASON:-}" ] || [ "$FORCE" = 1 ] || { echo "${FAKE_RM_REASON//@P@/$P}" >&2; exit 1; }
    if [ "${FAKE_RM_CHECK_DIRTY:-}" = 1 ] && [ "$FORCE" = 0 ]; then
      [ "${FAKE_RM_ADD_EDIT:-}" != 1 ] || echo late-edit >> "$P/README.md"
      [ "${FAKE_RM_ADD_NOISE:-}" != 1 ] || echo meta > "$P/late.meta"
      [ -z "${FAKE_RM_BREAK_ARCHIVE:-}" ] || echo extra > "$FAKE_RM_BREAK_ARCHIVE/TestResults/extra"
      [ -z "$(git -C "$P" status --porcelain --untracked-files=all --ignore-submodules=all)" ] || { echo "fatal: worktree contains modified or untracked files" >&2; exit 1; }
    fi
    rm -rf "${P:?}"; echo '{"ok":true,"result":{}}' ;;
  *) echo '{"ok":false}'; exit 1 ;;
esac
