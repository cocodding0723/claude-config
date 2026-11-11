#!/bin/bash
# 가짜 gh. `gh pr list --head <브랜치> ...` 에 FAKE_DIR/pr/<브랜치의 / 를 _ 로>.json 을 돌려줌(없으면 []). FAKE_GH_FAIL="브랜치 …" 이면 실패.
D="${FAKE_DIR:?}"
[ "$1 $2" = "pr list" ] || { echo "미지원: $*" >&2; exit 1; }
shift 2; B=""; while [ $# -gt 0 ]; do [ "$1" = --head ] && B="$2"; shift; done
case " ${FAKE_GH_FAIL:-} " in *" $B "*) echo "gh: 인증 필요" >&2; exit 1 ;; esac
f="$D/pr/$(printf '%s' "$B" | tr / _).json"
if [ -f "$f" ]; then cat "$f"; else echo '[]'; fi
