#!/usr/bin/env bash
# doc_archive.py 시험. 임시 폴더만 쓰고 실제 정리기록 폴더·Slack 은 건드리지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
TOOL="$HERE/../../hubs/scripts/doc_archive.py"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export LRM_ARCHIVE_ROOT="$T/root" LRM_TICKET_URL_BASE="https://jira.example/browse/" HOME="$T/home"
mkdir -p "$T/src" "$HOME"
fail=0; n=0
ok()  { n=$((n+1)); }
bad() { n=$((n+1)); fail=$((fail+1)); echo "FAIL: $1"; }
check() { if eval "$2"; then ok; else bad "$1"; fi; }
run() { python3 "$TOOL" "$@"; }
lines() { wc -l < "$LRM_ARCHIVE_ROOT/archive-index.jsonl" | tr -d ' '; }

printf '<html><title>A</title></html>' > "$T/src/a.html"
printf 'PDF-A' > "$T/src/a.pdf"
printf 'note' > "$T/src/b.txt"

# 1 보관 뿌리 없음 → 만들고 새 보관
out=$(run add --file "$T/src/a.html" --title 우편함 --card lrm-1 --ticket LRM-9 --kind plan --thread 1700.01 --date 2026-11-23 --json); rc=$?
check "새 보관 종료 0" "[ $rc = 0 ]"
D="$LRM_ARCHIVE_ROOT/2026-11-23-우편함"
check "HTML 복사" "[ -f '$D/a.html' ]"
check "옆 PDF 함께" "[ -f '$D/a.pdf' ]"
check "색인 1줄" "[ \$(lines) = 1 ]"
check "티켓 주소 조립" "grep -q 'https://jira.example/browse/LRM-9' '$LRM_ARCHIVE_ROOT/archive-index.jsonl'"
check "json 출력" "echo '$out' | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d[\"copied\"]==2'"

# 2 다시 실행 → 줄 안 늘어남, 다시 복사 안 함
out=$(run add --file "$T/src/a.html" --title 우편함 --card lrm-1 --ticket LRM-9 --kind plan --thread 1700.01 --date 2026-11-23 --json); rc=$?
check "재실행 종료 0" "[ $rc = 0 ]"
check "재실행 줄 유지" "[ \$(lines) = 1 ]"
check "재실행 복사 0" "echo '$out' | python3 -c 'import sys,json;assert json.load(sys.stdin)[\"copied\"]==0'"

# 3 같은 이름 다른 내용 → 덮어쓰지 않고 구분 이름
printf '<html>changed</html>' > "$T/src/a.html"
run add --file "$T/src/a.html" --title 우편함 --date 2026-11-23 >/dev/null; rc=$?
check "충돌 종료 0" "[ $rc = 0 ]"
check "원본 보존" "grep -q 'title>A' '$D/a.html'"
check "새 이름 생성" "[ \$(ls '$D'/a-*.html 2>/dev/null | wc -l | tr -d ' ') = 1 ]"
check "충돌 후 색인 2줄" "[ \$(lines) = 2 ]"
run add --file "$T/src/a.html" --title 우편함 --date 2026-11-23 >/dev/null
check "충돌 재실행 파일 안 늘음" "[ \$(ls '$D'/a-*.html | wc -l | tr -d ' ') = 1 ] && [ \$(lines) = 2 ]"

# 4 PDF 없는 HTML, 일반 파일
printf '<html>x</html>' > "$T/src/c.html"
run add --file "$T/src/c.html" --file "$T/src/b.txt" --title 단독 --date 2026-11-23 >/dev/null
check "PDF 없으면 PDF 안 만듦" "[ ! -e '$LRM_ARCHIVE_ROOT/2026-11-23-단독/c.pdf' ] && [ -f '$LRM_ARCHIVE_ROOT/2026-11-23-단독/b.txt' ]"

# 5 없는 파일 → 종료 3, 아무것도 쓰지 않음
before=$(lines)
run add --file "$T/src/none.html" --file "$T/src/b.txt" --title 없음 --date 2026-11-23 >/dev/null 2>"$T/err"; rc=$?
check "없는 파일 종료 3" "[ $rc = 3 ]"
check "오류에 조치 안내" "grep -q '조치' '$T/err'"
check "없는 파일이면 폴더 안 만듦" "[ ! -e '$LRM_ARCHIVE_ROOT/2026-11-23-없음' ] && [ \$(lines) = $before ]"

# 6 한글·공백·금지 문자 작업 이름
run add --file "$T/src/b.txt" --title "우편함 개편: 1/2차" --date 2026-11-24 >/dev/null; rc=$?
check "특수 이름 종료 0" "[ $rc = 0 ]"
check "금지 문자 치환" "[ -d '$LRM_ARCHIVE_ROOT/2026-11-24-우편함 개편- 1-2차' ]"

# 7 날짜 형식 오류
run add --file "$T/src/b.txt" --title x --date 11-24 >/dev/null 2>&1; rc=$?
check "날짜 오류 종료 3" "[ $rc = 3 ]"

# 8 dry-run 은 아무것도 쓰지 않음
rm -rf "$LRM_ARCHIVE_ROOT"
out=$(run add --file "$T/src/a.html" --title 드라이 --date 2026-11-23 --dry-run); rc=$?
check "dry-run 종료 0" "[ $rc = 0 ]"
check "dry-run 은 뿌리도 안 만듦" "[ ! -e '$LRM_ARCHIVE_ROOT' ]"
check "dry-run 은 계획 출력" "echo \"\$out\" | grep -q 'a.html'"

# 9 티켓 주소 설정 없으면 빈 칸
unset LRM_TICKET_URL_BASE
run add --file "$T/src/b.txt" --title 주소없음 --ticket LRM-1 --date 2026-11-23 >/dev/null
check "주소 설정 없으면 빈 칸" "grep -q '\"ticket_url\": \"\"' '$LRM_ARCHIVE_ROOT/archive-index.jsonl'"

# 10 list
export LRM_TICKET_URL_BASE="https://jira.example/browse/"
run add --file "$T/src/b.txt" --title 다른일 --card lrm-2 --ticket LRM-2 --date 2026-11-20 >/dev/null
check "list --card" "[ \$(run list --card lrm-2 --json | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))') = 1 ]"
check "list --since" "[ \$(run list --since 2026-11-22 --json | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))') = 1 ]"
check "list --ticket" "run list --ticket LRM-2 | grep -q 다른일"
run list --since bad >/dev/null 2>&1; check "list 날짜 오류 종료 3" "[ $? = 3 ]"
check "색인 없으면 list 빈 결과" "LRM_ARCHIVE_ROOT='$T/none' python3 '$TOOL' list --json | grep -q '^\[\]$'"

# 11 한 번의 add 에 이름이 같은 파일 둘(내용 다름) → 서로 덮어쓰지 않음
mkdir -p "$T/src/x" "$T/src/y" "$T/src/z"
printf 'X' > "$T/src/x/index.html"; printf 'Y' > "$T/src/y/index.html"; printf 'X' > "$T/src/z/index.html"
run add --file "$T/src/x/index.html" --file "$T/src/y/index.html" --title 이름겹침 --date 2026-11-23 >/dev/null; rc=$?
D2="$LRM_ARCHIVE_ROOT/2026-11-23-이름겹침"
check "같은 실행 이름 겹침 종료 0" "[ $rc = 0 ]"
check "두 내용 모두 보존" "[ \$(ls '$D2' | wc -l | tr -d ' ') = 2 ] && cat '$D2'/* | grep -q X && cat '$D2'/* | grep -q Y"
check "원래 이름은 앞 파일" "[ \"\$(cat '$D2/index.html')\" = X ]"
# 같은 이름·같은 내용 둘 → 한 번만 보관
run add --file "$T/src/x/index.html" --file "$T/src/z/index.html" --title 같은내용 --date 2026-11-23 >/dev/null
check "같은 내용이면 한 번만" "[ \$(ls '$LRM_ARCHIVE_ROOT/2026-11-23-같은내용' | wc -l | tr -d ' ') = 1 ]"
check "같은 내용 색인 파일 1개" "run list --json | python3 -c 'import sys,json;r=[x for x in json.load(sys.stdin) if x[\"title\"]==\"같은내용\"];assert len(r[0][\"files\"])==1'"
# dry-run 계획도 같은 규칙
out=$(run add --file "$T/src/x/index.html" --file "$T/src/y/index.html" --title 계획 --date 2026-11-23 --dry-run --json)
check "dry-run 도 겹침 구분" "echo '$out' | python3 -c 'import sys,json;d=json.load(sys.stdin);assert len(set(d[\"files\"]))==2'"

# 12 같은 폴더로 동시 보관 → 덮어쓰기 없이 파일 2개, 색인 줄 2개
mkdir -p "$T/src/p" "$T/src/q"
printf 'P' > "$T/src/p/doc.html"; printf 'Q' > "$T/src/q/doc.html"
before=$(lines)
run add --file "$T/src/p/doc.html" --title 동시 --date 2026-11-23 >/dev/null &
run add --file "$T/src/q/doc.html" --title 동시 --date 2026-11-23 >/dev/null &
wait
check "동시 실행 파일 2개" "[ \$(ls '$LRM_ARCHIVE_ROOT/2026-11-23-동시' | wc -l | tr -d ' ') = 2 ]"
check "동시 실행 색인 2줄 증가" "[ \$(lines) = \$((before+2)) ]"

echo "$n 항목, 실패 $fail"
[ "$fail" = 0 ]
