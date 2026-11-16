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
  show) echo '[{"id":"c1","status":"in_progress","labels":["file:src/a/"]}]' ;;
  list) echo '[{"id":"c1","status":"in_progress","labels":["file:src/a/"]},{"id":"c2","status":"closed","labels":["file:src/a/shared"]}]' ;;
esac
BD
chmod +x "$T/bin/bd"
export PATH="$T/bin:$PATH" BOARD_DIR="$T/board"

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

expect "인자 없음 2" 2 "$CHK"
expect "--help 0" 0 "$CHK" --help

echo "pass=$PASS fail=$FAIL"; [ "$FAIL" = 0 ]
