#!/usr/bin/env bash
# hub_status.py 시험. 임시 폴더·환경 변수만 씀. 실제 ~/.config/lrm-hubs 는 건드리지 않음.
set -uo pipefail
source "$(dirname "$0")/lib.sh"

# ---- 쓰기·읽기 왕복 -------------------------------------------------------------------------
hs write --slug t1 --run run_x1 --task "작업 A" --task "작업 B" --branch feat/a --worker "ctx_1 /ws/a" --next "리뷰 배정" --blocker "결정 대기 1" >/dev/null
J=$(hs show --slug t1 --json)
chk "왕복: 필드 전부 보존" 'python3 - "$J" <<PY
import json,sys
d=json.loads(sys.argv[1])
assert d["slug"]=="t1" and d["runId"]=="run_x1" and d["tasks"]==["작업 A","작업 B"], d
assert d["branches"]==["feat/a"] and d["workers"]==["ctx_1 /ws/a"] and d["next"]=="리뷰 배정" and d["blockers"]==["결정 대기 1"], d
assert d["updatedAt"].startswith("20") and "T" in d["updatedAt"], d
PY'
chk "show 사람용 출력에 작업·다음" 'hs show --slug t1 | grep -q "진행 작업: 작업 B" && hs show --slug t1 | grep -q "다음: 리뷰 배정"'
chk "show 없는 허브: 종료 코드 1 + 조치 안내" 'hs show --slug none 2>"$SB/e"; [ $? = 1 ] && grep -q "write 로 먼저 기록" "$SB/e"'
chk "slug 형식 오류: 종료 코드 2(경로 이탈 차단)" 'hs write --slug "../x" 2>/dev/null; [ $? = 2 ] && [ ! -e "$SB/x.json" ]'
chk "--help 에 예시·종료 코드" 'hs --help | grep -q "종료 코드" && hs write --help | grep -q -- "--task"'
chk "인자 없이 실행해도 대화형 대기 없음(오류 종료)" 'hs write </dev/null >/dev/null 2>&1; [ $? = 2 ]'
chk "비밀 값 거부 + 기존 파일 유지" 'before=$(cat "$SB/status/t1.json"); hs write --slug t1 --task "$FAKE_TOKEN" 2>"$SB/e"; [ $? = 2 ] && grep -q "비밀 값" "$SB/e" && [ "$before" = "$(cat "$SB/status/t1.json")" ]'
chk "write --json 결과" 'hs write --slug t2 --json | python3 -c "import json,sys;d=json.load(sys.stdin);assert d[\"ok\"] and d[\"path\"].endswith(\"t2.json\")"'

# ---- 막힘 근거 -----------------------------------------------------------------------------
hs write --slug ev --blocker "병합 대기 [pr:Basakansoft/LoveRevolutionMaker#746]" --blocker "옛 형식 막힘" >/dev/null
chk "근거 달린 막힘은 {text, refs}, 없는 막힘은 문자열" 'hs show --slug ev --json | python3 -c "import json,sys;d=json.load(sys.stdin)[\"blockers\"];assert d[0]=={\"text\":\"병합 대기\",\"refs\":[{\"kind\":\"pr\",\"id\":\"Basakansoft/LoveRevolutionMaker#746\"}]} and d[1]==\"옛 형식 막힘\", d"'
chk "show·prompt 가 근거를 되살려 표시" 'hs show --slug ev | grep -q "차단: 병합 대기 \[pr:Basakansoft/LoveRevolutionMaker#746\]" && hs prompt --slug ev | grep -q "차단: 병합 대기 \[pr:"'
chk "근거 형식 오류 거부: 종료 코드 2 + 기존 파일 유지" 'before=$(cat "$SB/status/ev.json"); hs write --slug ev --blocker "x [pr:746]" 2>"$SB/e"; [ $? = 2 ] && grep -q "형식 오류" "$SB/e" && [ "$before" = "$(cat "$SB/status/ev.json")" ]'
chk "알 수 없는 근거 종류 거부" 'hs write --slug ev --blocker "x [foo:bar]" 2>"$SB/e"; [ $? = 2 ] && grep -q "미지원" "$SB/e"'

# ---- 원자적 쓰기 ----------------------------------------------------------------------------
chk "임시 파일 잔여 없음" '[ -z "$(ls -A "$SB/status" | grep "^\.tmp-")" ]'
chk "replace 실패 시 원본 유지·임시 파일 정리" 'python3 - "$HS" "$SB/status" <<PY
import importlib.util,os,sys
spec=importlib.util.spec_from_file_location("hs",sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
p=os.path.join(sys.argv[2],"t1.json"); old=open(p).read()
def boom(*a): raise OSError("강제 실패")
m.os.replace=boom
try: m.atomic_write(p,"{깨진")
except OSError: pass
assert open(p).read()==old
assert not [f for f in os.listdir(sys.argv[2]) if f.startswith(".tmp-")]
PY'
# 쓰는 동안 읽기: 중간 상태(빈 파일·잘린 JSON)가 보이면 실패
BIG=$(python3 -c "print('x'*20000)")
( for i in $(seq 1 40); do hs write --slug race --task "$BIG$i" >/dev/null; done ) &
WPID=$!; BAD=0; READS=0
while kill -0 "$WPID" 2>/dev/null; do
  if [ -f "$SB/status/race.json" ]; then READS=$((READS + 1)); python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$SB/status/race.json" 2>/dev/null || BAD=$((BAD + 1)); fi
done
wait "$WPID"
chk "쓰는 중 읽기 ${READS}회 모두 완전한 JSON" '[ "$BAD" = 0 ]' "손상 읽기 ${BAD}회"

# ---- prompt: 없음·정상·낡음·손상 ------------------------------------------------------------
chk "상태 없음: 일반 문구" '[ "$(hs prompt --slug nope)" = "상태 파일 없음 — HANDOFF.md 와 run-list 로 이어받기" ]'
P=$(hs prompt --slug t1)
chk "정상: 작업·다음·교차 확인 문구, 낡음 표시 없음" 'printf "%s" "$P" | grep -q "진행 작업: 작업 A" && printf "%s" "$P" | grep -q "다음 할 일: 리뷰 배정" && ! printf "%s" "$P" | grep -q "오래된 상태"'
chk "25시간 전: '오래된 상태(시각)' 로 시작" 'hs prompt --slug old25 | head -1 | grep -q "^오래된 상태($OLDT)"'
chk "23시간 전: 낡음 표시 없음" '! hs prompt --slug new23 | grep -q "오래된 상태"'
chk "기준 시간 환경 변수(1시간)로 23시간 전도 낡음" 'LRM_HUB_STATUS_STALE_HOURS=1 hs prompt --slug new23 | head -1 | grep -q "^오래된 상태"'
mkfile badts "어제쯤"
chk "시각 파싱 불가: 낡음 처리" 'hs prompt --slug badts | head -1 | grep -q "^오래된 상태(시각 불명"'
printf '{깨진 json' > "$SB/status/broken.json"
chk "손상 파일: prompt 는 일반 문구로 종료 코드 0" 'out=$(hs prompt --slug broken); [ $? = 0 ] && printf "%s" "$out" | grep -q "상태 파일 읽기 실패" && printf "%s" "$out" | grep -q "HANDOFF.md 와 run-list"'
chk "손상 파일: show 는 종료 코드 1" 'hs show --slug broken 2>/dev/null; [ $? = 1 ]'
chk "prompt --json" 'hs prompt --slug nope --json | python3 -c "import json,sys;d=json.load(sys.stdin);assert d[\"slug\"]==\"nope\" and \"HANDOFF.md\" in d[\"prompt\"]"'

# ---- 따옴표·줄바꿈 안전 인용 -----------------------------------------------------------------
hs write --slug nasty --run 'run_"q"' --task "$NASTY" --next "다음 '값' \"x\"" >/dev/null
chk "특수 문자 JSON 왕복 동일" 'python3 - "$(hs show --slug nasty --json)" "$NASTY" <<PY
import json,sys
assert json.loads(sys.argv[1])["tasks"][0]==sys.argv[2]
PY'
chk "prompt 는 줄바꿈을 한 줄로 접고 값 보존" 'hs prompt --slug nasty | grep -qF "진행 작업: 따옴표 \" '"'"' 백틱 \`id\` \$(touch" && hs prompt --slug nasty | grep -q "둘째 줄 \\\$HOME"'
chk "시험 중 명령 치환이 실행되지 않음" '[ ! -e "$SB/PWNED" ]'


# ---- 필드 타입 손상 --------------------------------------------------------------------------
for bad in '{"slug":"b","updatedAt":"2026-01-01T00:00:00+00:00","tasks":1}' '{"tasks":["a",2]}' '{"next":5}' '{"blockers":"x"}' '[1,2]'; do
  printf '%s\n' "$bad" > "$SB/status/typ.json"
  chk "타입 손상 prompt traceback 없음·일반 문구: $bad" 'out=$(hs prompt --slug typ 2>&1); [ $? = 0 ] && ! printf "%s" "$out" | grep -q Traceback && printf "%s" "$out" | grep -q "상태 파일 읽기 실패" && printf "%s" "$out" | grep -q "HANDOFF.md 와 run-list"'
  chk "타입 손상 show 종료 코드 1·traceback 없음: $bad" 'out=$(hs show --slug typ 2>&1); [ $? = 1 ] && ! printf "%s" "$out" | grep -q Traceback && printf "%s" "$out" | grep -q "상태 파일 손상"'
done

printf '\n%s\n' "결과: PASS $PASS / FAIL $FAIL"
[ "$FAIL" = 0 ]
