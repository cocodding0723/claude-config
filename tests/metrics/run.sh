#!/usr/bin/env bash
# 지표 재측정 시험: 가짜 HOME 의 세션 기록(메인·허브·워커)으로 measure.sh 를 돌려 분류·전송·자리 집계를, 임시 작업판·장부·git 으로 결과 지표를 확인.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
M="$HERE/../../tools/metrics/measure.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/metrics-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
P="$SB/home/.claude/projects"
mkdir -p "$P/-x-orca-LoveRevolutionMaker" "$P/-x-orca-workspaces-LoveRevolutionMaker-coord-ui-hub" "$P/-x-orca-workspaces-LoveRevolutionMaker-coord-ui-hub-w1"
line() { printf '{"timestamp":"%s","message":{"content":[{"type":"tool_use","id":"%s","name":"Bash","input":{"command":"%s"}}]}}\n{"timestamp":"%s","message":{"content":[{"type":"tool_result","tool_use_id":"%s","content":"ok"}]}}\n' "$1" "$2" "$3" "$4" "$2"; }
line 2026-11-10T01:00:00 a1 'orca terminal send --terminal term_x 안녕하세요 허브 작업 지시' 2026-11-10T01:00:05 > "$P/-x-orca-LoveRevolutionMaker/m.jsonl"
line 2026-11-10T02:00:00 b1 'unity-run --name ui:render -- bash render.sh' 2026-11-10T02:03:00 > "$P/-x-orca-workspaces-LoveRevolutionMaker-coord-ui-hub/h.jsonl"
line 2026-11-10T03:00:00 c1 'echo work' 2026-11-10T03:00:01 > "$P/-x-orca-workspaces-LoveRevolutionMaker-coord-ui-hub-w1/w.jsonl"
line 2026-10-01T03:00:00 d1 'echo old' 2026-10-01T03:00:01 >> "$P/-x-orca-workspaces-LoveRevolutionMaker-coord-ui-hub-w1/w.jsonl"
U="$P/-x-orca-LoveRevolutionMaker/m.jsonl"
printf '%s\n' '{"type":"user","uuid":"u1","timestamp":"2026-11-10T01:10:00","promptSource":"typed","turnOrigin":"human","message":{"role":"user","content":"우편함 먼저 해"}}' \
  '{"type":"user","uuid":"u2","timestamp":"2026-11-10T01:11:00","promptSource":"queued","turnOrigin":"human","message":{"role":"user","content":"색은 파랑"}}' \
  '{"type":"user","uuid":"u3","timestamp":"2026-11-10T01:12:00","promptSource":"typed","turnOrigin":"human","message":{"role":"user","content":"[ui→메인] 보고"}}' \
  '{"type":"user","uuid":"u4","timestamp":"2026-11-10T01:13:00","isMeta":true,"promptSource":"system","turnOrigin":"peer","message":{"role":"user","content":"A peer"}}' \
  '{"type":"user","uuid":"u5","timestamp":"2026-11-10T01:14:00","turnOrigin":"human","message":{"role":"user","content":"<command-name>/x</command-name>"}}' \
  '{"type":"system","subtype":"compact_boundary","timestamp":"2026-11-10T01:20:00"}' >> "$U"
printf '%s\n' '{"type":"user","uuid":"u1","timestamp":"2026-11-10T01:10:00","promptSource":"typed","turnOrigin":"human","message":{"role":"user","content":"우편함 먼저 해"}}' > "$P/-x-orca-LoveRevolutionMaker/m2.jsonl"
HOME="$SB/home" bash "$M" --since 2026-11-10 --until 2026-11-10 --out "$SB/out" >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 0 ] && ok "measure.sh 4단계 완료" || ko "measure.sh" "rc=$rc $(cat "$SB/err")"
R="$SB/out/report.txt"
grep -q "세션 4" "$R" && ok "기간 안 세션 4개 수집" || ko "세션 수" "$(head -3 "$R")"
grep -q "'main': 2" "$R" && grep -q "'hub': 1" "$R" && grep -q "'worker': 1" "$R" && ok "메인·허브·워커 분류" || ko "분류" "$(grep 분류 "$R")"
grep -q "^전송 1" "$R" && ok "메인→허브 전송 1건" || ko "전송" "$(grep 전송 "$R")"
grep -q "^Unity 자리 요청 1" "$R" && grep -q "^자리 명령 1 중앙(초) 180" "$R" && ok "unity-run 자리 명령·보유 시간" || ko "자리" "$(grep 자리 "$R")"
grep -q "^메인 접점 1일 · 사용자→메인 메시지 일평균 2.0 · 자동 압축 일평균 1.0" "$R" && ok "메인 접점: 사람 입력만(허브 붙여넣기·시스템 주입·같은 uuid 제외), 압축 수" || ko "메인 접점" "$(grep '메인 접점' "$R")"
grep -q "^재작업률 " "$R" && grep -q "^검수 대기 " "$R" && ok "결과 지표 단계가 같은 보고서에 붙음" || ko "outcome 단계" "$(tail -5 "$R")"

# 토큰: 같은 message.id 3줄은 한 번, 기간 밖 응답은 0, 입력·출력·캐시 읽기·캐시 쓰기 분리
HT="$P/-x-orca-workspaces-LoveRevolutionMaker-coord-tok-hub"; mkdir -p "$HT"
au() { printf '{"type":"assistant","timestamp":"%s","message":{"id":"%s","usage":{"input_tokens":%s,"output_tokens":%s,"cache_read_input_tokens":%s,"cache_creation_input_tokens":%s},"content":[{"type":"text","text":"x"}]}}\n' "$@"; }
{ au 2026-11-10T04:00:00 msg_a 1000000 2000000 3000000 4000000; au 2026-11-10T04:00:01 msg_a 1000000 2000000 3000000 4000000; au 2026-11-10T04:00:02 msg_a 1000000 2000000 3000000 4000000
  au 2026-10-01T04:00:00 msg_old 9000000 9000000 9000000 9000000; } > "$HT/t.jsonl"
HOME="$SB/home" bash "$M" --since 2026-11-10 --until 2026-11-10 --out "$SB/tok" >/dev/null 2>&1
grep -q "^tok-hub 1 [0-9]* [0-9]* 1.0 2.0 3.0 4.0$" "$SB/tok/report.txt" && ok "토큰: 같은 message.id 한 번·기간 밖 0·네 항목 분리" || ko "토큰" "$(grep -A5 '허브 세션' "$SB/tok/report.txt")"

# 분류: 메인 폴더의 메모리 플러그인 자동 호출은 main 이 아니라 auto
PA="$SB/home2/.claude/projects"; mkdir -p "$PA/-x-orca-LoveRevolutionMaker"
printf '%s\n' '{"type":"user","timestamp":"2026-11-10T01:00:00","promptSource":"sdk","message":{"role":"user","content":"You are a memory relevance compressor utility. 입력"}}' > "$PA/-x-orca-LoveRevolutionMaker/auto.jsonl"
printf '%s\n' '{"type":"user","timestamp":"2026-11-10T01:00:00","promptSource":"typed","message":{"role":"user","content":"우편함 먼저 해"}}' > "$PA/-x-orca-LoveRevolutionMaker/human.jsonl"
HOME="$SB/home2" bash "$M" --since 2026-11-10 --until 2026-11-10 --out "$SB/auto" >/dev/null 2>&1
grep -q "'main': 1" "$SB/auto/report.txt" && grep -q "'auto': 1" "$SB/auto/report.txt" && ok "분류: 자동 호출은 main 에서 빠져 auto 로" || ko "auto 분류" "$(grep 분류 "$SB/auto/report.txt")"
grep -q "^메인 접점 1일 · 사용자→메인 메시지 일평균 1.0" "$SB/auto/report.txt" && ok "메인 접점이 같은 분류를 따름(auto 입력 제외)" || ko "메인 접점 분류" "$(grep '메인 접점' "$SB/auto/report.txt")"

# 결과 지표: 임시 작업판·작업 장부·되돌림 커밋. 작업판 시각은 지금이라 오늘 날짜 기간으로 따로 돌림
O="$HERE/../../tools/metrics/outcome.py"
TODAY=$(TZ=Asia/Seoul date +%Y-%m-%d)
BD="$SB/board"; mkdir -p "$BD"
(cd "$BD" && git init -q && bd init --prefix t -q >/dev/null 2>&1) || ko "임시 작업판 준비" ""
b() { (cd "$BD" && bd "$@"); }
idof() { python3 -c 'import json,sys;d=json.load(sys.stdin);d=d[0] if isinstance(d,list) else d;print(d["id"])'; }
EP=$(b create "우편함" -t epic -l feature:mail --json | idof)
for n in 1 2; do K=$(b create "작업 $n" --parent "$EP" --json | idof); [ "$n" = 1 ] && b update "$K" --status in_progress >/dev/null 2>&1; b close "$K" --reason "커밋 abc$n" >/dev/null; done
RE=$(b create "다시 연 일" --json | idof); b close "$RE" --reason x >/dev/null; b reopen "$RE" >/dev/null 2>&1
DC=$(b create "색 정할까?" --parent "$EP" -l kind:decision --json | idof); b close "$DC" --reason "답: 파랑" >/dev/null
b create "PR 게시" -l kind:decision,gate:approval --json >/dev/null
HD="$SB/hubdir"; mkdir -p "$HD"
printf '%s\n' '{"id":"T1","state":"완료","received_at":"'"$TODAY"'T09:00:00+09:00","done_at":"'"$TODAY"'T11:00:00+09:00","history":[{"at":"'"$TODAY"'T09:00:00+09:00","state":"완료"},{"at":"'"$TODAY"'T10:00:00+09:00","state":"진행"},{"at":"'"$TODAY"'T11:00:00+09:00","state":"완료"}]}' > "$HD/tasks.jsonl"
NOW=$(date +%s); printf '{"kind":"decide","hub":"ui","decision_id":"D-1","at":%s}\n' "$NOW" > "$HD/hub-reports.jsonl"
printf '{"decision_id":"D-1","state":"hub","at":%s}\n' "$NOW" > "$HD/answer-delivery.jsonl"
RP="$SB/repo"; git init -q "$RP"; git -C "$RP" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "feat: 무엇" >/dev/null 2>&1
git -C "$RP" -c user.email=t@t -c user.name=t commit -q --allow-empty -m 'Revert "feat: 무엇"' >/dev/null 2>&1
git -C "$RP" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "docs: 되돌리기 명령 정리" >/dev/null 2>&1
OUT=$(HOME="$SB/home" METRICS_BOARD_DIR="$BD" LRM_HUB_DIR="$HD" METRICS_REPOS="$RP" python3 "$O" --since "$TODAY" --until "$TODAY" --work "$SB/o2" 2>&1); rc=$?
J="$SB/o2/outcome.json"; OUT_LIFE="$OUT"
[ "$rc" = 0 ] && [ -f "$J" ] && ok "outcome.py 단독 실행·outcome.json" || ko "outcome 실행" "rc=$rc $OUT"
[ "$(jq '.board.life.n' "$J")/$(jq '.board.done' "$J")" = "2/2" ] && ok "카드 수명: 닫힌 작업 카드만(epic·결정 카드 제외)" || ko "카드 수명" "$(jq -c .board.life "$J") done=$(jq .board.done "$J")"
[ "$(jq '.board.life.within15m' "$J")" = 2 ] && grep -q "^카드 수명.*15분 안에 닫힌 카드 2/2" <<<"$OUT_LIFE" && ok "카드 수명: 15분 안에 닫힌 카드 수가 보고서에 나옴" || ko "15분 안" "$(jq -c .board.life "$J") $OUT_LIFE"
[ "$(jq '.board.run.n' "$J")" = 1 ] && [ "$(jq '.board.run.median_h < 0.25' "$J")" = true ] && ok "시작→닫힘: started_at 있는 카드만 중앙값" || ko "시작→닫힘" "$(jq -c .board.run "$J")"
grep -q "지시→완료" <<<"$OUT_LIFE" && ko "보고서에 '지시→완료' 가 남음" "$OUT_LIFE" || ok "보고서에 '지시→완료' 없음"
[ "$(jq '.ledger.lead.median_h == 2' "$J")" = true ] && ok "접수→완료: 작업 장부 received_at→done_at 2시간" || ko "장부 리드타임" "$(jq -c .ledger "$J")"
[ "$(jq -r '.board.touch["feature:mail"]' "$J")" = 2 ] && ok "기능당 사용자 접점: 기능 epic 의 결정 1 + 답 1" || ko "기능 접점" "$(jq -c .board.touch "$J")"
[ "$(jq -r '.ledger.touch.ui' "$J")" = 2 ] && ok "옛 허브 접점: 결정 요청 1 + 전달된 답 1" || ko "허브 접점" "$(jq -c .ledger.touch "$J")"
[ "$(jq -c '[.rework.reverts,.rework.reopened,.rework.redo,.rework.done]' "$J")" = "[1,1,1,3]" ] && ok "재작업률: 되돌림 커밋 1(문서 '되돌리기' 제외)·다시 연 카드 1·재지시 1 / 완료 3(작업판 2 + 장부 1)" || ko "재작업률" "$(jq -c .rework "$J")"
[ "$(jq '.board.approvalWait|length' "$J")" = 1 ] && ok "검수 대기: 승인 성격(gate:approval) 결정 카드" || ko "검수 대기" "$(jq -c '[.board.review,.board.approvalWait]' "$J")"
EP2=$(b create "끝난 기능" -t epic --json | idof); K=$(b create "마지막" --parent "$EP2" --json | idof); b close "$K" --reason ok >/dev/null
HOME="$SB/home" METRICS_BOARD_DIR="$BD" LRM_HUB_DIR="$HD" METRICS_REPOS="$RP" python3 "$O" --since "$TODAY" --until "$TODAY" --work "$SB/o3" >/dev/null 2>&1
[ "$(jq -r '.board.review[0]' "$SB/o3/outcome.json")" = "$EP2" ] && ok "검수 대기: 하위 카드가 다 닫힌 열린 epic" || ko "epic 검수 대기" "$(jq -c .board.review "$SB/o3/outcome.json")"
OUT=$(HOME="$SB/home" METRICS_BOARD_DIR="$SB/none" LRM_HUB_DIR="$HD" METRICS_REPOS="$RP" python3 "$O" --since "$TODAY" --until "$TODAY" --work "$SB/o4" 2>&1); rc=$?
[ "$rc" = 0 ] && echo "$OUT" | grep -q "작업판 읽기 실패: 작업판 폴더 없음" && ok "작업판 없음 → 실패 표시, 나머지 지표는 냄" || ko "작업판 없음" "rc=$rc $OUT"
echo "$OUT" | grep -q "판단 보류(n<10)" && ok "표본 10 미만은 판단 보류" || ko "판단 보류" "$OUT"

bash "$M" --since 2026-11-10 >/dev/null 2>&1; [ $? = 2 ] && ok "인자 부족 2" || ko "인자 부족"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
