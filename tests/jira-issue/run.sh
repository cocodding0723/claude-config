#!/usr/bin/env bash
# jira-issue 검사기 단위 시험: 상위 없음 거부·상태 불일치 경고·점검 표. 임시 폴더만 사용.
set -uo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
CK="$REPO/skills/jira-issue/scripts/issue_check.py"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
W=$(mktemp -d "${TMPDIR:-/tmp}/cc-jiraissue.XXXXXX"); trap 'rm -rf "${W:?}"' EXIT
run() { python3 -I "$CK" "$@"; }

BASE='"type":"작업","summary":"[매점] 새로고침 개선","assignee":"acc1","due":"2026-11-20","due_reason":"작은 작업 1일","status":"해야 할 일"'
w() { printf '{%s}' "$2" > "$W/$1.json"; }

w ok "$BASE,\"parent\":{\"key\":\"LRM-10\",\"reason\":\"제목·하위가 같은 매점 에픽\"},\"evidence\":{}"
out=$(run pre "$W/ok.json"); [ $? = 0 ] && ok "정상 입력 통과" || ko "정상 입력" "$out"

w noparent "$BASE,\"evidence\":{}"
out=$(run pre "$W/noparent.json"); rc=$?
[ "$rc" = 1 ] && echo "$out" | grep -q '상위 없음' && ok "상위 없으면 거부" || ko "상위 없음 거부" "rc=$rc $out"

w badkey "$BASE,\"parent\":{\"key\":\"abc\",\"reason\":\"x\"},\"evidence\":{}"
run pre "$W/badkey.json" >/dev/null; [ $? = 1 ] && ok "상위 키 형식 오류 거부" || ko "상위 키 형식" ""

w noreason "$BASE,\"parent\":{\"key\":\"LRM-10\"},\"evidence\":{}"
run pre "$W/noreason.json" >/dev/null; [ $? = 1 ] && ok "상위 근거 없으면 거부" || ko "상위 근거" ""

w epic0 "$BASE,\"new_epic\":{\"description\":\"\",\"children\":[]},\"evidence\":{}"
out=$(run pre "$W/epic0.json"); [ $? = 1 ] && echo "$out" | grep -q '설명' && echo "$out" | grep -q '하위' && ok "새 에픽은 설명·하위 필수" || ko "새 에픽" "$out"

w epic1 "$BASE,\"new_epic\":{\"description\":\"목표·범위·완료\",\"children\":[\"a\"]},\"evidence\":{}"
run pre "$W/epic1.json" >/dev/null; [ $? = 0 ] && ok "새 에픽 설명·하위 있으면 통과" || ko "새 에픽 통과" ""

for miss in assignee due due_reason; do
  python3 -c "import json,sys;d=json.load(open('$W/ok.json'));d['$miss']='';json.dump(d,open('$W/m.json','w'))"
  run pre "$W/m.json" >/dev/null; [ $? = 1 ] && ok "$miss 비면 거부" || ko "$miss 거부" ""
done

w stale "$BASE,\"parent\":{\"key\":\"LRM-10\",\"reason\":\"r\"},\"evidence\":{\"branch\":\"feat/x\",\"commits\":[\"abc1234\"]}"
out=$(run pre "$W/stale.json"); rc=$?
[ "$rc" = 0 ] && echo "$out" | grep -q '경고: 상태 불일치' && ok "커밋 있는데 해야 할 일 → 경고" || ko "상태 불일치 경고" "rc=$rc $out"
run pre --strict "$W/stale.json" >/dev/null; [ $? = 1 ] && ok "--strict 는 경고도 실패" || ko "strict" ""

w pr "$BASE,\"status\":\"확인 대기\",\"parent\":{\"key\":\"LRM-10\",\"reason\":\"r\"},\"evidence\":{\"pr\":\"#12\"}"
out=$(run pre "$W/pr.json"); echo "$out" | grep -q '통과' && ok "PR 있고 확인 대기 → 일치" || ko "PR 단계" "$out"

w hold "$BASE,\"status\":\"보류\",\"parent\":{\"key\":\"LRM-10\",\"reason\":\"r\"},\"evidence\":{\"commits\":[\"a\"]}"
out=$(run pre "$W/hold.json"); echo "$out" | grep -q '통과' && ok "보류는 단계와 무관하게 허용" || ko "보류" "$out"
w rv "$BASE,\"status\":\"리뷰 중\",\"parent\":{\"key\":\"LRM-10\",\"reason\":\"r\"},\"evidence\":{\"pr\":\"#12\"}"
out=$(run pre "$W/rv.json"); echo "$out" | grep -q '경고: 상태 불일치.*확인 대기' && ok "없는 상태 '리뷰 중' → 경고, 전환 안내는 확인 대기" || ko "리뷰 중" "$out"
w mg "$BASE,\"status\":\"확인 대기\",\"parent\":{\"key\":\"LRM-10\",\"reason\":\"r\"},\"evidence\":{\"pr\":\"#12\",\"merged\":true}"
out=$(run pre "$W/mg.json"); echo "$out" | grep -q '통과' && ok "병합 직후 확인 대기 허용(완료는 사람 확인 뒤)" || ko "병합 확인 대기" "$out"

# 점검 표
cat > "$W/post.json" <<'J'
[{"key":"LRM-1","type":"작업","parent":"LRM-10","status":"진행 중","due":"2026-11-20","assignee":"acc1","evidence":{"branch":"feat/x"}},
 {"key":"LRM-2","type":"작업","parent":"","status":"해야 할 일","due":"2026-11-20","assignee":"acc1","evidence":{"commits":["abc"]}},
 {"key":"LRM-3","type":"에픽","status":"해야 할 일","due":"2026-11-30","assignee":"acc1","evidence":{"note":"새 에픽"}}]
J
out=$(run post "$W/post.json"); rc=$?
[ "$rc" = 1 ] && ok "점검 표: 빈 칸 있으면 종료 1" || ko "점검 표 종료코드" "rc=$rc"
echo "$out" | grep -q '| LRM-1 .*통과 |' && ok "점검 표: 정상 행 통과" || ko "정상 행" "$out"
echo "$out" | grep -q 'LRM-2 .*비어 있음.*고칠 것: parent' && echo "$out" | grep -q '상태(doing 단계와 불일치)' && ok "점검 표: 상위 비어 있음·상태 불일치 지적" || ko "문제 행" "$out"
echo "$out" | grep -q 'LRM-3 .*고칠 것: 설명, 하위' && ok "점검 표: 에픽 설명·하위 0 지적" || ko "에픽 행" "$out"
head -1 <<<"$out" | grep -q '키 | 상위 | 상태 | 기한 | 담당 | 근거' && ok "점검 표 헤더 칸 순서" || ko "헤더" ""

printf '[]' > "$W/empty.json"; run post "$W/empty.json" >/dev/null; [ $? = 0 ] && ok "빈 목록 통과" || ko "빈 목록" ""
run pre "$W/없음.json" >/dev/null 2>&1; [ $? = 2 ] && ok "입력 파일 없으면 종료 2" || ko "파일 없음" ""

printf '\n%d 통과, %d 실패\n' "$PASS" "$FAIL"; [ "$FAIL" = 0 ]
