#!/usr/bin/env bash
# board-size 시험: judge 판정 표 + 임시 beads 작업판에서 CLI.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
TOP="$HERE/../.."
BIN="$TOP/board/bin/board-size"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

# 판정 표: 기대 크기 · 이유에 들어갈 글 · 경로(공백 구분)
while IFS='|' read -r want why paths; do
  [ -z "$want" ] && continue
  got=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import board_size as b
s, r = b.judge(sys.argv[3:], b.load_rules()); print(s + "\t" + " / ".join(r))' "$TOP/board/lib" x $paths)
  [ "${got%%	*}" = "$want" ] && [[ "$got" == *"$why"* ]] && ok "judge $want: $paths" || ko "judge $paths" "$got"
done <<'EOF'
small|에셋·위험 영역 없음|Assets/Scripts/UI/MailView.cs Assets/Scripts/UI/MailPresenter.cs
normal|화면·데이터 에셋|Assets/Scripts/UI/MailView.cs Assets/Prefabs/Mail.prefab
normal|화면·데이터 에셋(*sheet*)|Assets/Sheets/mail.xlsx
normal|위험 영역: *save*|Assets/Scripts/Save/SaveStore.cs
normal|위험 영역: *iap*|Assets/Scripts/Shop/IapService.cs
normal|파일 3개|a/x.cs a/y.cs a/z.cs
large|파일 11개|a/1.cs a/2.cs a/3.cs a/4.cs a/5.cs a/6.cs a/7.cs a/8.cs a/9.cs a/10.cs a/11.cs
large|모듈 3개|Assets/Scripts/Mail/a.cs Assets/Scripts/Shop/b.cs Assets/Scripts/Quest/c.cs
normal|범위가 넓음|Assets/Scripts/UI/
large|모듈 3개|Assets/Scripts/Save/a.cs Assets/Scripts/Shop/b.cs Assets/Scripts/Quest/c.cs
EOF

SB=$(mktemp -d "${TMPDIR:-/tmp}/bsize-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" BD_LAST_TOUCHED_FALLBACK=0
mkdir -p "$BOARD_DIR" && cd "$BOARD_DIR" && git init -q && bd init --quiet >/dev/null 2>&1 || { echo "bd init 실패"; exit 1; }
id() { python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }
expect() { local name=$1 want=$2; shift 2; "$@" >"$SB/out" 2>&1; local got=$?; [ "$got" = "$want" ] && ok "$name" || ko "$name" "종료 $got(기대 $want): $(head -c 300 "$SB/out")"; }
field() { python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$SB/out" "$1"; }

ep=$(bd create 기능 -t epic --labels "size:normal,repo:$SB/r" --json | id)
bd create a --parent "$ep" --labels "file:$SB/r/Assets/Scripts/UI/A.cs,repo:$SB/r" --json >/dev/null
bd create b --parent "$ep" --labels "file:Assets/Scripts/UI/B.cs" --json >/dev/null

expect "카드 file: 표시로 판정 0" 0 "$BIN" "$ep" --json
[ "$(field size)" = small ] && ok "하위 카드 file 2개(절대 경로 포함)는 small" || ko "카드 판정" "$(cat "$SB/out")"
field command | grep -q "board-stage $ep <단계> <이름> --size small" && ok "표시가 다르면 board-stage 명령 안내" || ko "안내" "$(cat "$SB/out")"
bd show "$ep" --json | grep -q '"size:normal"' && ok "표시는 직접 바꾸지 않음" || ko "표시 변경됨" ""

expect "--paths 로 판정 0" 0 "$BIN" "$ep" --paths Assets/Scripts/Save/S.cs --json
[ "$(field size)" = normal ] && [ -z "$(field command)" ] && ok "--paths 판정·같으면 안내 없음" || ko "--paths" "$(cat "$SB/out")"

expect "사람용 출력 0" 0 "$BIN" "$ep"
grep -q "판정 small" "$SB/out" && grep -q -- "--size small" "$SB/out" && ok "사람용 출력에 판정·명령" || ko "사람용" "$(cat "$SB/out")"

expect "카드 없음 2" 2 "$BIN" no-such-1
grep -q '거부:.*조치:' "$SB/out" && ok "거부 문구에 조치" || ko "거부 문구" "$(cat "$SB/out")"
expect "인자 없음 2" 2 "$BIN"
expect "--help 0" 0 "$BIN" --help
grep -q "예:" "$SB/out" && ok "--help 에 예시" || ko "--help" ""
BOARD_DIR="$SB/none" expect "작업판 폴더 없음 4" 4 "$BIN" "$ep"

echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
