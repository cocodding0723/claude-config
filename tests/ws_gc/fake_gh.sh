#!/bin/bash
# FAKE_GH_SLEEP=초 이면 호출을 그만큼 늦춤(전체 실행 도중 끼어들기 시험).
# 가짜 gh. `gh pr list --head <브랜치> ...` 에 FAKE_DIR/pr/<브랜치의 / 를 _ 로>.json 을 돌려줌(없으면 []). FAKE_GH_FAIL="브랜치 …" 이면 실패.
D="${FAKE_DIR:?}"
[ -z "${FAKE_GH_SLEEP:-}" ] || sleep "$FAKE_GH_SLEEP"
# `gh api repos/{owner}/{repo}/compare/<HEAD>...<oid> --jq .status` 는 FAKE_DIR/compare/<oid> 내용(ahead·diverged 등)을 돌려줌. 없으면 404 처럼 실패
if [ "$1" = api ]; then
  c="${2##*...}"; [ -f "$D/compare/$c" ] && { cat "$D/compare/$c"; exit 0; }
  echo "gh: Not Found (HTTP 404)" >&2; exit 1
fi
[ "$1 $2" = "pr list" ] || { echo "미지원: $*" >&2; exit 1; }
shift 2; B=""; while [ $# -gt 0 ]; do [ "$1" = --head ] && B="$2"; shift; done
case " ${FAKE_GH_FAIL:-} " in *" $B "*) echo "gh: 인증 필요" >&2; exit 1 ;; esac
f="$D/pr/$(printf '%s' "$B" | tr / _).json"
if [ -f "$f" ]; then cat "$f"; else echo '[]'; fi
