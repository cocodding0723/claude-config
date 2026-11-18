#!/usr/bin/env bash
# tracker_of.py 시험: 저장소별 표 → values.env TRACKER → jira 순서, 표 형식 오류 안내. 임시 git 저장소만 씀.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
T="$REPO/hubs/scripts/tracker_of.py"
PY=${PY:-python3}
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-tracker.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export CLAUDE_CONFIG_TRACKERS="$SB/t.tsv" CLAUDE_CONFIG_VALUES="$SB/v.env"
mk() { mkdir -p "$SB/$1"; git -C "$SB/$1" init -q; git -C "$SB/$1" remote add origin "$2"; }
mk lrm git@github.com:Basakansoft/LoveRevolutionMaker.git
mk studio https://github.com/Cocodding-Studio/legacy-frontier
mk mine https://github.com/cocodding0723/claude-config.git
mkdir -p "$SB/plain"
cp "$REPO/trackers.example.tsv" "$SB/t.tsv"; printf 'Cocodding-Studio/*\tgithub\n' >> "$SB/t.tsv"
printf 'CHAT=discord\nTRACKER=github\n' > "$SB/v.env"

[ "$("$PY" "$T" "$SB/lrm")" = "jira LRM" ] && ok "표: 회사 게임 저장소(ssh 주소) → jira LRM" || ko "표 jira" "$("$PY" "$T" "$SB/lrm")"
[ "$("$PY" "$T" "$SB/studio")" = "github" ] && ok "표: owner/* 패턴(https 주소) → github" || ko "표 패턴" ""
[ "$("$PY" "$T" "$SB/mine")" = "github" ] && [ "$("$PY" "$T" "$SB/mine" --json | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["source"])')" = values.env ] \
  && ok "표에 없음 → values.env TRACKER(github)" || ko "기본값" ""
printf 'TRACKER=jira\n' > "$SB/v.env"
[ "$("$PY" "$T" "$SB/mine")" = "jira" ] && ok "표에 없음·TRACKER=jira → 키 없는 jira(스킬이 물음)" || ko "jira 기본" ""
rm "$SB/v.env"
[ "$("$PY" "$T" "$SB/plain" 2>/dev/null)" = "jira" ] && ok "git 아님·값 파일 없음 → jira" || ko "아무것도 없음" ""
"$PY" "$T" "$SB/plain" 2>&1 >/dev/null | grep -q '저장소(origin)를 못 찾음' && ok "저장소 밖: stderr 경고" || ko "저장소 밖 경고" ""
[ -z "$("$PY" "$T" "$SB/lrm" 2>&1 >/dev/null)" ] && ok "저장소 안: 경고 없음" || ko "경고 과다" ""
[ "$("$PY" "$T" "$SB/lrm" --json | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print(d["repo"],d["key"])')" = "Basakansoft/LoveRevolutionMaker LRM" ] \
  && ok "--json: 저장소·키" || ko "json" ""
printf 'Basakansoft/LoveRevolutionMaker\tjira:lrm\n' > "$SB/t.tsv"
out=$("$PY" "$T" "$SB/lrm" 2>&1); rc=$?
[ "$rc" = 2 ] && echo "$out" | grep -q 't.tsv:1' && ok "표 값 오류(소문자 키): 종료 코드 2·줄 번호" || ko "값 오류" "rc=$rc $out"
printf 'Basakansoft/LoveRevolutionMaker jira:LRM\n' > "$SB/t.tsv"
"$PY" "$T" "$SB/lrm" >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "표 칸 오류(탭 아님): 종료 코드 2" || ko "칸 오류" "rc=$rc"

printf '결과: PASS %d, FAIL %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
