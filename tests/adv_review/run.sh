#!/usr/bin/env bash
# adv_review.py 시험. 가짜 claude·gh·알림·orca, 임시 폴더만 사용
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P); S="$HERE/../../hubs/scripts"; CW="$HERE/../ci_watch"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); echo "PASS  $1"; }
ko() { FAIL=$((FAIL + 1)); echo "FAIL  $1 — ${2:-}"; }
D=$(mktemp -d "${TMPDIR:-/tmp}/cc-adv.XXXXXX"); trap 'rm -rf "$D"' EXIT
chmod +x "$HERE"/fake_*.sh "$CW"/fake_*.sh
export FAKE_DIR="$D" LRM_HUB_DIR="$D" LRM_GH="$HERE/fake_gh.sh" LRM_CLAUDE="$HERE/fake_claude.sh" LRM_NOTIFY="$CW/fake_send.sh" LRM_ORCA="$CW/fake_orca.sh" LRM_MAIN_ENV="$D/main.env"
echo "MAIN_TERMINAL=term_abc" > "$D/main.env"; cp "$HERE/../../hubs/ci-review-hubs.tsv" "$D/"
mk() { : > "$D/diff.txt"; for i in $(seq 1 "$1"); do printf 'diff --git a/f%s b/f%s\n' "$i" "$i" >> "$D/diff.txt"; seq 1 "$2" | sed 's/^/+/' >> "$D/diff.txt"; done; }
adv() { python3 "$S/adv_review.py" --pr 700 --head 1234567890abcdef "$@" > "$D/out" 2>&1; RC=$?; }

mk 2 10; adv
f="$D/ci-review/PR-700-1234567890ab-adv.md"
[ "$RC" = 0 ] && grep -q "확인 지적 1건" "$f" && grep -q "널 참조" "$f" && ! grep -q "가짜" "$f" && ok "검증 통과 지적만 남김" || ko "검증 필터" "$(cat "$D/out")"
grep -q "claude-fable-5-2" "$D/models.log" && ! grep -q opus "$D/models.log" && ok "기본 모델 Fable 5.2" || ko "모델" "$(cat "$D/models.log")"
grep -q "적대적 리뷰 지적 1건(high 1·medium 0) PR#700 · 담당 허브 VFX" "$D/orca.log" && grep -q -- "--kind warn" "$D/sent.log" && ok "메인 전달 + Slack warn" || ko "전달" "$(cat "$D/orca.log")"
! grep -rq "gh pr comment\|issues/.*/comments" "$D/out" && ok "PR 에 직접 쓰지 않음" || ko "PR 쓰기"

: > "$D/models.log"; FAKE_FAIL_MODEL=claude-fable-5-2 adv
grep -q "claude-opus-5-5" "$D/models.log" && [ "$RC" = 0 ] && ok "Fable 실패 → Opus 대체" || ko "대체" "$(cat "$D/models.log")"

: > "$D/models.log"; printf 'ADV_CHUNK_LINES=25\n' > "$D/adv-review.env"; mk 4 10; adv
[ "$(grep -c . "$D/models.log")" = 3 ] && ok "큰 diff 파일 단위 분할(덩어리 2 + 검증 1)" || ko "분할" "$(grep -c . "$D/models.log")"

printf 'ADV_CHUNK_LINES=25\nADV_MAX_CHUNKS=1\n' > "$D/adv-review.env"; : > "$D/models.log"; : > "$D/sent.log"; adv
[ ! -s "$D/models.log" ] && grep -q "덩어리 2개" "$D/sent.log" && ok "덩어리 상한 초과 → 건너뜀 + 알림" || ko "덩어리 상한"

printf 'ADV_DAILY_MAX=1\n' > "$D/adv-review.env"; rm -f "$D/adv-review.state.json"; mk 1 5; : > "$D/sent.log"
adv; : > "$D/models.log"; adv; adv
[ ! -s "$D/models.log" ] && [ "$(grep -c '하루 상한' "$D/sent.log")" = 1 ] && ok "하루 상한: 초과 건너뜀, 알림 1회" || ko "하루 상한" "$(cat "$D/sent.log")"

rm -f "$D/adv-review.env" "$D/adv-review.state.json"; mkdir "$D/adv-review.lock"; adv; [ "$RC" = 3 ] && ok "동시 실행 잠금" || ko "잠금" "$RC"; rmdir "$D/adv-review.lock"

# ci_watch 트리거
printf '#!/bin/bash\necho "$*" >> "%s/adv.log"\n' "$D" > "$D/fake_adv.sh"; chmod +x "$D/fake_adv.sh"
printf 'import subprocess,sys\nopen(sys.argv[0]+".log","a")\nopen("%s/adv.log","a").write(" ".join(sys.argv[1:])+"\\n")\n' "$D" > "$D/fake_adv.py"
export LRM_ADV="$D/fake_adv.py" LRM_GH="$CW/fake_gh.sh"
pr() { echo "[{\"number\":702,\"title\":\"t\",\"headRefName\":\"${3:-sprint/bundle-1}\",\"headRefOid\":\"$1\",\"isDraft\":$2,\"labels\":${4:-[]}}]" > "$D/prs.json"; echo '[]' > "$D/checks-702.json"; }
w() { python3 "$S/ci_watch.py" > /dev/null 2>&1; sleep 1; }
rm -f "$D/ci-watch.state.json"; pr aaaa false; w; [ ! -s "$D/adv.log" ] && ok "트리거: 처음 켤 때 기존 head 기록만" || ko "트리거 기준"
rm -f "$D/ci-watch.state.json"; pr aaaa true; w
pr bbbb true; w; [ ! -s "$D/adv.log" ] && ok "draft 묶음은 실행 안 함" || ko "draft"
pr bbbb false; w; grep -q -- "--pr 702 --head bbbb" "$D/adv.log" && ok "묶음 PR 게시(ready) → 1회 실행" || ko "게시" "$(cat "$D/adv.log" 2>/dev/null)"
pr cccc false; w; [ "$(grep -c -- '--pr 702' "$D/adv.log")" = 1 ] && ok "fixup 커밋(새 head)에도 재실행 없음" || ko "fixup 재실행"
rm -f "$D/ci-watch.state.json" "$D/adv.log"; pr x1 true feat/LRM-1; w; pr x2 false feat/LRM-1; w; grep -q x2 "$D/adv.log" && ok "개별 PR 도 ready 시 1회(기본 all)" || ko "개별 PR"
export LRM_ADV_SCOPE=bundle; rm -f "$D/ci-watch.state.json" "$D/adv.log"; pr y1 true feat/LRM-2; w; pr y2 false feat/LRM-2; w; [ ! -s "$D/adv.log" ] && ok "SCOPE=bundle: 개별 PR 제외" || ko "bundle 범위"
pr y3 false feat/LRM-2 '[{"name":"bundle"}]'; w; grep -q y3 "$D/adv.log" && ok "SCOPE=bundle: 라벨 bundle 은 대상" || ko "라벨"; unset LRM_ADV_SCOPE
echo "ADV_SCOPE=bundle" > "$D/adv-review.env"; rm -f "$D/ci-watch.state.json" "$D/adv.log"; pr z1 true feat/LRM-3; w; pr z2 false feat/LRM-3; w; [ ! -s "$D/adv.log" ] && ok "설정 파일 ADV_SCOPE=bundle 적용" || ko "설정 파일 범위"; rm -f "$D/adv-review.env"
echo "통과 $PASS / 실패 $FAIL"; [ "$FAIL" = 0 ]
