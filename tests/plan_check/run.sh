#!/usr/bin/env bash
# 계획서 필수 칸 검사 시험: 양식 그대로·채운 Markdown·HTML·n/a 이유·순서 값. mktemp -d 임시 폴더만 씀.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
ROOT="$HERE/../.."
C="$ROOT/hubs/scripts/plan_check.py"
T="$ROOT/board/templates/plan.md"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/plan-check.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT

OUT=$(python3 "$C" "$T" --json); rc=$?
[ "$rc" = 1 ] && [ "$(echo "$OUT" | jq '.problems|length')" = 10 ] && ok "양식 그대로 → 9칸 자리표시 + 카드 없음, 종료 1" || ko "양식 그대로" "rc=$rc $OUT"

sed -e 's/<클래스·프리팹·시트 키 이름 규칙과 새로 만들 이름 목록>/Mailbox* 접두, MailboxPopup 새로/' \
    -e 's/<쓸 기존 부품(팝업·버튼·Presenter·스크립트) 경로. 새로 만들지 않을 것>/DefaultDialog 재사용/' \
    -e 's/<색 기준(시안 값·기존 팔레트 경로)>/메인 메뉴 팔레트/' -e 's/<글꼴·크기 기준>/KoreanFallback 28/' \
    -e 's/<쓸 시트 탭·키·enum, 새로 추가할 키>/Mail 탭 MailId/' \
    -e 's/<다른 카드보다 먼저 끝나야 하는 단계. 없으면 n\/a: 이유>/데이터 키 추가/' -e 's/<동시에 띄울 카드 id 목록>/팝업, 목록/' \
    -e 's/<여러 카드가 함께 만지는 파일·프리팹·시트·설정>/n\/a: 공유 파일 없음/' -e 's/<첫 카드 1장을 끝까지 돌릴 가장 작은 단위>/목록 한 줄/' \
    -e 's/| <카드 제목> | <경로 접두> | 독립 | <확인 가능한 결과> |/| 목록 | Assets\/UI\/Mail | 독립 | 목록 3줄 보임 |/' \
    -e 's/| <카드 제목> | <경로 접두> | 순차(<앞 카드>) | <확인 가능한 결과> |/| 팝업 | Assets\/UI\/MailPopup | 순차(목록) | 눌러 열림 |/' "$T" > "$SB/ok.md"
OUT=$(python3 "$C" "$SB/ok.md"); rc=$?
[ "$rc" = 0 ] && ok "다 채운 Markdown 통과(n/a: 이유 허용)" || ko "채운 md" "rc=$rc $(python3 "$C" "$SB/ok.md" --json)"

sed 's/| 팝업 | Assets\/UI\/MailPopup | 순차(목록) |/| 팝업 | Assets\/UI\/MailPopup | 나중에 |/' "$SB/ok.md" > "$SB/order.md"
python3 "$C" "$SB/order.md" >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 1 ] && grep -q "순서 '나중에'" "$SB/err" && grep -q '조치' "$SB/err" && ok "순서가 독립·순차가 아니면 거부, 원인·조치" || ko "순서 값" "rc=$rc $(cat "$SB/err")"

sed 's/n\/a: 공유 파일 없음/n\/a:/' "$SB/ok.md" > "$SB/na.md"
OUT=$(python3 "$C" "$SB/na.md" --json); [ "$(echo "$OUT" | jq -r '.problems[0]')" = "병렬 점검 '공유 상태': n/a 이유 없음" ] && ok "n/a 뒤 이유 없으면 거부" || ko "n/a 이유" "$OUT"

grep -v '재사용 부품' "$SB/ok.md" > "$SB/miss.md"
OUT=$(python3 "$C" "$SB/miss.md" --json); [ "$(echo "$OUT" | jq -r '.problems[0]')" = "공통 결정 '재사용 부품': 칸 없음" ] && ok "칸 자체가 없으면 거부" || ko "칸 없음" "$OUT"

cat > "$SB/plan.html" <<'H'
<html><body><h2>공통 결정</h2><ul><li><b>이름</b>: Mailbox* 접두</li><li><b>재사용 부품</b>: DefaultDialog</li><li><b>색</b>: 메인 메뉴 팔레트</li>
<li><b>글꼴</b>: KoreanFallback</li><li><b>데이터 키</b>: Mail 탭</li></ul><h2>병렬 점검</h2><p><b>먼저 막는 것</b>: 데이터 키</p><p><b>독립</b>: 목록·팝업</p>
<p><b>공유 상태</b>: n/a: 없음</p><p><b>최소 분해</b>: 목록 한 줄</p>
<table><tr><th>카드</th><th>고칠 파일(file:)</th><th>순서</th><th>완료 기준</th></tr><tr><td>목록</td><td>Assets/UI</td><td>독립</td><td>보임</td></tr>
<tr><td>팝업</td><td>Assets/UI/P</td><td>순차(목록)</td><td>열림</td></tr></table></body></html>
H
OUT=$(python3 "$C" "$SB/plan.html" --json); rc=$?
[ "$rc" = 0 ] && ok "doc-kit HTML 계획서도 같은 칸으로 통과" || ko "HTML" "rc=$rc $OUT"
python3 "$C" "$SB/none.md" >/dev/null 2>&1; [ $? = 2 ] && ok "없는 파일 → 종료 2" || ko "없는 파일" ""
python3 "$C" --help >/dev/null; [ $? = 0 ] && ok "--help 종료 0" || ko "--help" ""
grep -q "순서: 독립" "$ROOT/board/templates/card-task.md" && grep -q "순서: 독립" "$ROOT/board/templates/card-mockup.md" && ok "카드 양식 2개에 독립/순차 줄" || ko "카드 양식" ""
grep -q "plan.md" "$ROOT/hubs/roles/role-feature.txt" && grep -q "순차 카드는 앞 카드를 맡은 작업자" "$ROOT/hubs/roles/role-feature.txt" && ok "기능 코디네이터 역할에 양식·순차 규칙" || ko "역할 파일" ""
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
