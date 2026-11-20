#!/usr/bin/env bash
# hook_stats.py 검증. 세션 기록은 임시 폴더의 가짜 파일, 보고는 가짜 hub_report. 실제 세션 기록·Slack 은 쓰지 않음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
HS="$HERE/../../hubs/scripts/hook_stats.py"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
chk() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-조건 불일치}"; fi; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-hookstats.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
NOW=1795000000
export LRM_TRANSCRIPT_DIRS="$SB/p1:$SB/p2" LRM_HOOK_STATS_NOW=$NOW LRM_HUB_REPORT="$SB/fake_report.py"
mkdir -p "$SB/p1/a" "$SB/p2/b"
use() { printf '{"type":"assistant","timestamp":"%s","message":{"content":[{"type":"tool_use","id":"t","name":"Bash","input":{}}]}}\n' "$1"; }
deny() { jq -cn --arg ts "$1" --arg tool "$2" --arg msg "$3" '{type:"user",timestamp:$ts,message:{content:[{type:"tool_result",is_error:true,tool_use_id:"t",content:("PreToolUse:"+$tool+" hook error: "+$msg)}]}}'; }
IN=2026-11-19T00:00:00Z; OUT=2026-10-01T00:00:00Z   # NOW(2026-11-20 무렵) 기준 7일 안·밖
{ use $IN; use $IN; use $IN; use $OUT
  deny $IN Bash "git reset --hard 차단됨. 작업 내용이 영구 삭제됩니다."
  deny $IN Bash "Jira 상태 변경는 사용자 승인 뒤에만(사용자 11-16) — 결정 카드 D-JSTLRM717"
  deny $IN Bash "Jira 상태 변경는 사용자 승인 뒤에만(사용자 11-16) — 결정 카드 D-JSTLRM900"
  deny $OUT Bash "기간 밖 거부"
  jq -cn --arg ts $IN '{type:"user",timestamp:$ts,message:{content:[{type:"tool_result",is_error:false,content:"PreToolUse:Bash hook error: 출력에 인용된 글"}]}}'
  jq -cn --arg ts $IN '{type:"user",timestamp:$ts,message:{content:[{type:"tool_result",is_error:true,content:"보통 오류: PreToolUse:Bash hook error: 중간에 나온 글"}]}}'
  echo '{깨진 줄'
} > "$SB/p1/a/s1.jsonl"
{ use $IN; deny $IN mcp__x__y "Jira 상태 변경는 사용자 승인 뒤에만(사용자 11-16) — 결정 카드 D-JSTLRM1"; } > "$SB/p2/b/s2.jsonl"
touch -t 202611190000 "$SB/p1/a/s1.jsonl" "$SB/p2/b/s2.jsonl"
{ use $IN; deny $IN Bash "오래된 파일의 거부"; } > "$SB/p2/b/old.jsonl"; touch -t 202609010000 "$SB/p2/b/old.jsonl"

J=$(python3 "$HS" --days 7 --json)
chk "기간 안 도구 호출만 셈" '[ "$(jq .calls <<<"$J")" = 4 ]' "$(jq .calls <<<"$J")"
chk "훅 거부만 셈(성공 출력·보통 오류·기간 밖·오래된 파일 제외)" '[ "$(jq .denies <<<"$J")" = 4 ]' "$(jq -c .reasons <<<"$J")"
chk "같은 사유는 번호가 달라도 한 묶음, 많은 순" '[ "$(jq -r ".reasons[0] | \"\(.reason)|\(.count)\"" <<<"$J")" = "Jira 상태 변경는 사용자 승인 뒤에만|3" ]' "$(jq -c '.reasons[0]' <<<"$J")"
chk "사유별 도구 이름 모음" '[ "$(jq -c ".reasons[0].tools" <<<"$J")" = "[\"Bash\",\"mcp__x__y\"]" ]'
chk "거부 있는 세션 수·비율" '[ "$(jq -c "[.sessions_with_deny,.rate_pct]" <<<"$J")" = "[2,100.0]" ]' "$(jq -c '[.sessions_with_deny,.rate_pct]' <<<"$J")"
T=$(python3 "$HS" --days 7 --top 1)
chk "글 출력: 요약 줄과 나머지 사유 안내" 'grep -q "거부 4번" <<<"$T" && grep -q "사유 1개 더" <<<"$T"' "$T"
chk "글 출력: 세지 않는 것 안내" 'grep -q "확인 창(ask)" <<<"$T"'

printf '#!/usr/bin/env python3\nimport sys,os\nopen(os.environ["CALLS"],"a").write("\\x1f".join(sys.argv[1:])+"\\n")\nsys.exit(int(os.environ.get("REPORT_RC","0")))\n' > "$SB/fake_report.py"
export CALLS="$SB/calls"
python3 "$HS" --days 7 --report >/dev/null; rc=$?
chk "--report: hub_report add 를 info 로 부름" '[ $rc = 0 ] && grep -q "^add.*--kind.info" <(tr "\037" " " < "$CALLS")' "$(cat "$CALLS" 2>/dev/null)"
chk "--report: 본문은 3줄이고 빗금으로 사실을 잇지 않음" '[ "$(python3 -c "import sys;a=open(sys.argv[1]).read().split(chr(31));t=a[a.index(\"--text\")+1];print(len(t.strip().splitlines()), \" / \" in t)" "$CALLS")" = "3 False" ]'
: > "$CALLS"; python3 "$HS" --days 7 --report --dry-run >/dev/null
chk "--report --dry-run 은 그대로 넘김" 'grep -q -- "--dry-run" "$CALLS"'
REPORT_RC=1 python3 "$HS" --days 7 --report >/dev/null 2>"$SB/err"; rc=$?
chk "보고 기록 실패는 종료 1 + 원인" '[ $rc = 1 ] && grep -q "보고 기록 실패" "$SB/err"' "rc=$rc"
LRM_TRANSCRIPT_DIRS="$SB/없음" python3 "$HS" >/dev/null 2>"$SB/err"; rc=$?
chk "세션 기록 폴더 없음은 종료 1 + 조치" '[ $rc = 1 ] && grep -q "LRM_TRANSCRIPT_DIRS" "$SB/err"' "rc=$rc"
python3 "$HS" --days 0 >/dev/null 2>&1; chk "--days 0 은 종료 2" '[ $? = 2 ]'
mkdir -p "$SB/empty"; E=$(LRM_TRANSCRIPT_DIRS="$SB/empty" python3 "$HS" --json); chk "기록이 없으면 0건으로 성공" '[ "$(jq -c "[.denies,.rate_pct]" <<<"$E")" = "[0,0.0]" ]'
python3 "$HS" --help | grep -q "종료 코드" && ok "--help 에 예시·종료 코드" || ko "--help"

echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
