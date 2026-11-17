#!/bin/bash
# board-merge-check 시험. 가짜 bd·임시 git 저장소만 사용, 실제 작업판은 건드리지 않음
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CHK="$ROOT/board/bin/board-merge-check"
T="$(mktemp -d)"; trap 'rm -rf "${T:?}"' EXIT
PASS=0 FAIL=0

mkdir -p "$T/bin" "$T/board"
cat > "$T/bin/bd" <<'BD'
#!/bin/bash
case "$1" in
  show) case "$2" in
    c1) echo '[{"id":"c1","status":"in_progress","labels":["file:src/a/","base:dev"]}]' ;;
    c3) echo '[{"id":"c3","status":"in_progress","parent":"e1","labels":["file:./src/a"]}]' ;;
    c4) echo '[{"id":"c4","status":"in_progress","labels":["file:src/a"]}]' ;;
    c5) echo "[{\"id\":\"c5\",\"status\":\"in_progress\",\"labels\":[\"file:$REPO_ABS/src/a/\",\"base:dev\"]}]" ;;
    e1) echo '[{"id":"e1","issue_type":"epic","labels":["branch:dev"]}]' ;;
    e2) echo '[{"id":"e2","issue_type":"epic","status":"open","labels":["branch:feat2"]}]' ;;
    c6) echo '[{"id":"c6","status":"closed","parent":"e2","labels":["file:src/a/"]}]' ;;
  esac ;;
  children) [ "$2" = e2 ] && echo '[{"id":"c6","status":"closed","parent":"e2","labels":["file:src/a/"]},{"id":"c7","status":"closed","parent":"e2","labels":["file:src/b/"]}]' || echo '[]' ;;
  list) echo '[{"id":"c1","status":"in_progress","labels":["file:src/a/"]},{"id":"c2","status":"closed","labels":["file:src/a/shared.cs"]}]' ;;
esac
BD
chmod +x "$T/bin/bd"
export PATH="$T/bin:$PATH" BOARD_DIR="$T/board" REPO_ABS="$T/repo"

R="$T/repo"; git init -q -b dev "$R"; cd "$R"
git -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
c() { git -c user.name=t -c user.email=t@t "$@" -q; }

expect() { # expect <이름> <종료코드> <명령...>
  local name="$1" want="$2"; shift 2
  "$@" >/dev/null 2>&1; local got=$?
  if [ "$got" = "$want" ]; then PASS=$((PASS+1)); echo "ok  $name"; else FAIL=$((FAIL+1)); echo "FAIL $name (want $want got $got)"; fi
}

git checkout -q -b ok dev; mkdir -p src/a; echo 1 > src/a/x.cs; echo m > src/a/x.cs.meta; git add .; c commit -m ok
expect "범위 안·meta 통과" 0 "$CHK" c1 ok --repo "$R"

git checkout -q -b meta dev; mkdir -p src; echo m > src/a.meta; git add .; c commit -m meta
expect "폴더 meta(src/a.meta) 통과" 0 "$CHK" c1 meta --repo "$R"

git checkout -q -b out dev; mkdir -p src/b; echo 1 > src/b/y.cs; git add .; c commit -m out
expect "범위 밖 거부" 4 "$CHK" c1 out --repo "$R"
"$CHK" c1 out --repo "$R" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outside"]==["src/b/y.cs"]' \
  && { PASS=$((PASS+1)); echo "ok  JSON outside 목록"; } || { FAIL=$((FAIL+1)); echo "FAIL JSON outside 목록"; }

git checkout -q -b rv ok; git -c user.name=t -c user.email=t@t revert --no-edit HEAD >/dev/null
expect "되돌림 경고 기본 0" 0 "$CHK" c1 rv --repo "$R"
expect "되돌림 경고 strict 5" 5 "$CHK" c1 rv --repo "$R" --strict

git checkout -q -b other dev; mkdir -p src/a; echo 1 > src/a/shared.cs; git add .; c commit -m other
expect "다른 카드 범위 strict 5" 5 "$CHK" c1 other --repo "$R" --strict
"$CHK" c1 other --repo "$R" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["warnings"][0]["card"]=="c2"' \
  && { PASS=$((PASS+1)); echo "ok  다른 카드 경고 내용"; } || { FAIL=$((FAIL+1)); echo "FAIL 다른 카드 경고 내용"; }

expect "기준 생략: 상위 epic branch: label 사용" 0 "$CHK" c3 ok --repo "$R"
expect "기준 생략: label 없으면 2" 2 "$CHK" c4 ok --repo "$R"
expect "절대 경로 file: label 도 같은 범위" 0 "$CHK" c5 ok --repo "$R"
git checkout -q -b ab dev; mkdir -p src/ab; echo 1 > src/ab/z.cs; git add .; c commit -m ab
expect "file:src/a/ 는 src/ab 를 덮지 않음" 4 "$CHK" c1 ab --repo "$R"
git checkout -q -b mvbase dev; mkdir -p src/b; echo keep > src/b/keep.cs; git add .; c commit -m mvbase
git checkout -q -b mv mvbase; mkdir -p src/a; git mv src/b/keep.cs src/a/keep.cs; c commit -m mv
expect "이름 바꾸기로 범위 밖 파일 삭제도 거부" 4 "$CHK" c1 mv --base mvbase --repo "$R"
# 기능 통합 브랜치 → dev: 카드 여러 장의 범위 합집합으로 검사
git checkout -q -b feat2 dev; mkdir -p src/a src/b; echo 1 > src/a/e.cs; echo 1 > src/b/e.cs; git add .; c commit -m feat2
expect "통합 브랜치를 카드 1장 범위로 dev 대비 검사하면 4" 4 "$CHK" c6 feat2 --base dev --repo "$R"
expect "--epic: 하위 카드 범위 합집합이면 통과" 0 "$CHK" --epic e2 feat2 --base dev --repo "$R"
git checkout -q -b feat3 feat2; mkdir -p src/c; echo 1 > src/c/z.cs; git add .; c commit -m feat3
expect "--epic: 어느 카드에도 없는 파일은 4" 4 "$CHK" --epic e2 feat3 --base dev --repo "$R"
expect "--epic 에 epic 아닌 카드는 2" 2 "$CHK" --epic c6 feat2 --base dev --repo "$R"
expect "인자 없음 2" 2 "$CHK"
expect "--help 0" 0 "$CHK" --help

echo "pass=$PASS fail=$FAIL"; [ "$FAIL" = 0 ]
