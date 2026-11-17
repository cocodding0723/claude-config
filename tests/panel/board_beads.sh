#!/usr/bin/env bash
# 작업판(bd) 원료와 board-ask·board-answer 시험. mktemp -d 임시 작업판만 씀(실제 ~/orca/board 는 열지 않음).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SCRIPTS="${COLLECT_DIR:-$HERE/../../hubs/scripts}"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-board.XXXXXX")
trap '[ -n "${KEEP:-}" ] || rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" PANEL_BOARD_DIR="$SB/board" HOME="$SB/home"
mkdir -p "$BOARD_DIR" "$HOME"
(cd "$BOARD_DIR" && git init -q && bd init --prefix t -q >/dev/null 2>&1) || { echo "임시 작업판 준비 실패"; exit 1; }
b() { (cd "$BOARD_DIR" && bd "$@"); }
id_of() { python3 -c 'import json,sys;d=json.load(sys.stdin);d=d[0] if isinstance(d,list) else d;print(d["id"])'; }

# 최근 기록 시험이 DONE 을 맨 앞으로 보도록 먼저 닫음
CEP=$(b create "끝난 기능" -t epic --json | id_of); CK=$(b create "끝난 기능의 일" --parent "$CEP" --json | id_of); b close "$CK" >/dev/null; b close "$CEP" >/dev/null
EPIC=$(b create "구조 전환" -t epic -p 0 --json | id_of)
DONE=$(b create "끝난 일" --parent "$EPIC" --json | id_of); b close "$DONE" --reason "커밋 abc" >/dev/null
WORK=$(b create "진행 일" --parent "$EPIC" --json | id_of); BD_ACTOR=worker-1 b update "$WORK" --claim >/dev/null
STUCK=$(b create "막힌 일" --json | id_of); b update "$STUCK" --status blocked >/dev/null

# board-ask. 결정 카드는 배경·용어·선택지 결과·추천·HTML 설명서가 필수라 시험용 설명서와 공통 칸을 둠
DOC="$SB/doc.html"
printf '<h2>배경</h2><p>%s</p><svg><rect/><rect/><rect/></svg><h2>선택지별 결과</h2><ul><li>가</li><li>나</li></ul><h2>추천 이유</h2><p>%s</p>' "$(printf '배경설명입니다%.0s' 1 2 3 4 5 6 7 8 9 10 11 12)" "$(printf '추천이유입니다%.0s' 1 2 3 4)" > "$DOC"
FILL=(--background "패널 칸이 손 입력과 카드 두 곳이라 어긋남" --terms 없음 --doc "$DOC")
OPTS="옮김 => 카드 하나로 관리|유지 => 손 칸을 같이 둠"
D=$("$SCRIPTS/board-ask" --runnable no "패널 정본 옮길까?" --options "$OPTS" --recommend 옮김 --why "손 칸 낡음" --parent "$EPIC" --priority 0 "${FILL[@]}"); rc=$?
[ "$rc" = 0 ] && [ -n "$D" ] && ok "board-ask: 결정 카드 생성, id 출력" || ko "board-ask 생성" "rc=$rc"
D2=$("$SCRIPTS/board-ask" --runnable no "패널 정본 옮길까?" --options "$OPTS" --recommend 옮김 "${FILL[@]}" --json)
[ "$(echo "$D2" | jq -r '.id + "/" + (.created|tostring)')" = "$D/false" ] && ok "board-ask: 같은 제목 열린 카드면 새로 안 만듦" || ko "board-ask 반복" "$D2"
"$SCRIPTS/board-ask" --runnable no "선택지 하나" --options "A" >/dev/null 2>"$SB/err"; [ $? = 2 ] && grep -q '조치' "$SB/err" && ok "board-ask: 선택지 1개 → 종료 2, 원인·조치" || ko "선택지 검사" ""
"$SCRIPTS/board-ask" --runnable no "x" --options "A|B" --recommend C >/dev/null 2>&1; [ $? = 2 ] && ok "board-ask: 추천이 선택지 밖 → 종료 2" || ko "추천 검사" ""
"$SCRIPTS/board-ask" --help >/dev/null; [ $? = 0 ] && ok "board-ask --help 종료 0" || ko "--help" ""
DRY=$("$SCRIPTS/board-ask" --runnable no "건조" --options "A => 가|B => 나" --recommend A "${FILL[@]}" --dry-run); [ "$(b list --json --limit 0 | jq '[.[]|select(.title=="건조")]|length')" = 0 ] && ok "board-ask --dry-run: 작업판 그대로" || ko "dry-run" "$DRY"
"$SCRIPTS/board-ask" "칸 없는 결정" --options "A|B" >/dev/null 2>"$SB/err"; [ $? = 2 ] && grep -q '돌려 보면 알 수 있나' "$SB/err" && grep -q '조치' "$SB/err" && ok "board-ask: 결정 카드에 '돌려 보면 알 수 있나' 칸 없으면 종료 2" || ko "runnable 필수" "$(cat "$SB/err")"
"$SCRIPTS/board-ask" "버튼 위치 어디가 잘 보일까?" --options "위|아래" --runnable yes >/dev/null 2>"$SB/err"; rc=$?
[ "$rc" = 5 ] && grep -q '시제품' "$SB/err" && [ "$(b list --json --limit 0 | jq '[.[]|select(.title=="버튼 위치 어디가 잘 보일까?")]|length')" = 0 ] && ok "board-ask --runnable yes → 종료 5, 카드 안 만듦, 시제품으로 판정 안내" || ko "runnable yes" "rc=$rc $(cat "$SB/err")"
b show "$D" --json | jq -r 'if type=="array" then .[0] else . end | .description' | grep -qx '돌려 보면 알 수 있나: 아니오' && ok "board-ask --runnable no → 카드 설명에 판정 기록" || ko "runnable 기록" ""
T=$("$SCRIPTS/board-ask" "키스토어 만들기" --kind user-todo)
A=$("$SCRIPTS/board-ask" "PR 게시" --gate approval --recommend 승인 "${FILL[@]}")
[ "$(b show "$A" --json | jq -r 'if type=="array" then .[0] else . end | (.labels|sort|join(","))')" = "gate:approval,kind:decision" ] && b show "$A" --json | jq -r 'if type=="array" then .[0] else . end | .description' | grep -q -- '- 승인' && ok "board-ask --gate approval: kind:decision + gate:approval, 선택지 기본 승인|보류" || ko "gate approval" "$(b show "$A" --json | jq -c 'if type=="array" then .[0] else . end | .labels')"
L=$("$SCRIPTS/board-ask" "옛 승인 호출" --kind approval --recommend 승인 "${FILL[@]}" 2>"$SB/err")
grep -q '폐지' "$SB/err" && [ "$(b show "$L" --json | jq -r 'if type=="array" then .[0] else . end | (.labels|sort|join(","))')" = "gate:approval,kind:decision" ] && ok "board-ask --kind approval(옛 호출): 폐지 안내 + 결정 카드로" || ko "옛 approval" "$(cat "$SB/err")"
"$SCRIPTS/board-ask" "할 일에 게이트" --kind user-todo --gate approval >/dev/null 2>&1; [ $? = 2 ] && ok "board-ask: --gate 는 결정 카드에만(종료 2)" || ko "gate todo" ""
O=$(b create "예전 승인 카드" -l kind:approval --json | id_of)
AL=$(b create "📥 자동 알림: slack" -l kind:alert,alert:slack,hub:main --json | id_of)
CF=$(b create "❓ 확인 필요: 답" -l kind:confirm,alert:confirm,hub:main --json | id_of)

SUB=$(b create "손자 일" --parent "$WORK" --json | id_of)

# 수집기 원료
mkdir -p "$SB/hub/ai-state"; printf '{"cwd":"/ws/feat-mail","state":"needs_input","kind":"permission_prompt","at":%s}\n' "$(date +%s)" > "$SB/hub/ai-state/s1.json"
printf '{"cwd":"/ws/old","state":"working","at":1}\n' > "$SB/hub/ai-state/s2.json"; printf 'broken' > "$SB/hub/ai-state/s3.json"
P=$(env -u PANEL_GH_REPOS PANEL_ORCA=/usr/bin/false LRM_HUB_DIR="$SB/hub" bash "$SCRIPTS/panel_collect.sh" --print 2>/dev/null)
v() { echo "$P" | jq -r ".sources.board.value$1"; }
[ "$(echo "$P" | jq -r .sources.board.ok)" = true ] && ok "수집기: board 원료 ok" || ko "board ok" "$(echo "$P" | jq -c .sources.board | head -c 200)"
[ "$(v '.decisions[0].id')" = "$D" ] && v '.decisions[0].desc' | grep -q '추천: 옮김' && ok "결정 대기 = kind:decision 열린 카드, 설명에 선택지·추천" || ko "decisions" "$(v '.decisions' | head -c 200)"
[ "$(v '.todos[0].id')" = "$T" ] && ok "할 일 = kind:user-todo" || ko "todos" ""
[ "$(echo "$P" | jq -r --arg a "$AL" --arg c "$CF" '[.sources.board.value.todos[].id | select(.==$a or .==$c)] | length')" = 2 ] \
  && [ "$(echo "$P" | jq -r --arg a "$AL" --arg c "$CF" '[.sources.board.value.doing[]?.id | select(.==$a or .==$c)] | length')" = 0 ] \
  && ok "모음 카드(kind:alert)·확인 카드(kind:confirm) → 답할 것(todos)에 보임, 진행 줄엔 중복 없음" || ko "모음·확인 카드 todos" "$(v '.todos' | head -c 300)"
[ "$(v '.approvals')" = null ] && [ "$(echo "$P" | jq -r --arg a "$A" --arg o "$O" '[.sources.board.value.decisions[] | select(.id==$a or .id==$o) | .gate] | join(",")')" = "approval,approval" ] && ok "승인 칸 없음: gate:approval·옛 kind:approval 카드도 결정 대기에 gate 배지로" || ko "gate 결정" "$(v '.decisions' | head -c 300)"
[ "$(v '.decisions[0].gate')" = null ] && ok "일반 결정 카드는 gate null" || ko "gate null" "$(v '.decisions[0]')"
[ "$(v '.doing[0].id')" = "$WORK" ] && [ "$(v '.doing[0].who')" = worker-1 ] && [ "$(v '.doing[0].beatAt|type')" = number ] && [ "$(v '.doing[0].startedAt|type')" = number ] && ok "진행 = in_progress 카드, 담당·시작·마지막 신호" || ko "doing" "$(v '.doing' | head -c 200)"
[ "$(v '.blocked[0].id')" = "$STUCK" ] && ok "막힘 = blocked 카드" || ko "blocked" ""
[ "$(v '.headline.id')/$(v '.headline.done')/$(v '.headline.total')" = "$EPIC/1/3" ] && ok "헤드라인 = 열린 epic, 진척 닫힌/전체" || ko "headline" "$(v '.headline')"
[ "$(v '.recent[0].id')" = "$DONE" ] && [ "$(v '.recent[0].reason')" = "커밋 abc" ] && ok "최근 기록 = 닫힌 카드" || ko "recent" "$(v '.recent')"
[ "$(echo "$P" | jq --arg d "$D" '[.sources.board.value.doing[].id] | index($d)')" = null ] && ok "kind 카드는 진행 줄에 중복 안 나옴" || ko "중복" ""

F=$(echo "$P" | jq -c --arg e "$EPIC" '.sources.board.value.features[] | select(.id==$e)')
[ "$(echo "$F" | jq -r '"\(.done)/\(.total)/\(.doingCount)/\(.asks)/\(.doing[0].who)/\(.closedAt)"')" = "1/2/1/1/worker-1/null" ] && [ "$(echo "$F" | jq -r '.lastAt|type')" = number ] && ok "기능 줄 원료: 진척(작업 카드만)·진행 수·답 대기·담당·마지막 변화" || ko "features" "$F"
[ "$(echo "$F" | jq -r '[.kids[] | "\(.st)/\(.subs)"] | join(",")')" = "doing/1,done/0" ] && [ "$(echo "$F" | jq -r '.kids[0] | "\(.who)/\(.at|type)"')" = "worker-1/number" ] && ok "기능 하위 카드: 진행 중 먼저·상태·담당·마지막 갱신, 손자는 개수(subs)만, 결정 카드 제외" || ko "kids" "$(echo "$F" | jq -c .kids)"
[ "$(echo "$P" | jq -r --arg e "$CEP" '.sources.board.value.features[] | select(.id==$e) | "\(.kids|length)/\(.done)/\(.total)"')" = "0/1/1" ] && ok "닫힌 기능은 하위 목록 없음(진행률 수치만)" || ko "closed kids" ""
[ "$(echo "$P" | jq -r --arg e "$EPIC" '.sources.board.value.features | map(select(.id==$e)) | length')" = 1 ] && ok "하위 카드 목록은 에픽 줄 안에만(별도 카드 호출 없이 bd list 한 번)" || ko "한 번" ""
[ "$(echo "$P" | jq -r --arg d "$D" '.sources.board.value.decisions[] | select(.id==$d) | "\(.parent)/\(.waiting)"')" = "$EPIC/1" ] && ok "결정 카드: 부모 epic·막는 작업 수" || ko "waiting" "$(v '.decisions[0]')"
[ "$(echo "$P" | jq -r '.sources.ai.value.sessions | length')" = 1 ] && [ "$(echo "$P" | jq -r '.sources.ai.value.sessions[0].state')" = needs_input ] && ok "세션 상태 파일: 최근 것만, 깨진 파일 건너뜀" || ko "ai 원료" "$(echo "$P" | jq -c .sources.ai)"
[ "$(echo "$P" | jq -r '.sources.ghState.ok')" = false ] && ok "gh-state.json 없음 → ghState 원료 missing(패널은 기존 gh 사용)" || ko "ghState" "$(echo "$P" | jq -c .sources.ghState)"

# heartbeat·증거 상태: 진행 중 하위 카드만 beatAt·ev 를 가짐. 브랜치 끝과 증거 커밋 비교. 카드마다 bd 를 부르지 않고 같은 목록 JSON 에서 읽음
GR="$SB/gitrepo"; git init -q -b main "$GR" && git -C "$GR" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init && git -C "$GR" branch wk
TIP=$(git -C "$GR" rev-parse wk)
E2=$(b create "증거 기능" -t epic --json | id_of)
mk() { b create "$1" --parent "$E2" --labels "branch:wk,repo:$GR" --json | id_of; }
K0=$(mk "증거 없는 일"); K1=$(mk "증거 맞는 일"); K2=$(mk "증거 낡은 일"); K3=$(mk "브랜치 모르는 일"); K4=$(mk "열린 일")
for k in $K0 $K1 $K2 $K3; do BD_ACTOR=w-beat b update "$k" --claim >/dev/null; done
BD_ACTOR=w-beat b update "$K1" --set-metadata "evidence_test=$TIP" >/dev/null
BD_ACTOR=w-beat b update "$K2" --set-metadata "evidence_test=0000000000000000000000000000000000000000" >/dev/null
b label remove "$K3" branch:wk >/dev/null; b label add "$K3" branch:nope >/dev/null
BD_ACTOR=w-beat b update "$K3" --set-metadata "evidence_test=$TIP" >/dev/null
P2=$(env -u PANEL_GH_REPOS PANEL_ORCA=/usr/bin/false LRM_HUB_DIR="$SB/hub" bash "$SCRIPTS/panel_collect.sh" --print 2>/dev/null)
KS=$(echo "$P2" | jq -c --arg e "$E2" '.sources.board.value.features[] | select(.id==$e) | .kids')
ev_of() { echo "$KS" | jq -r --arg i "$1" '.[] | select(.id==$i) | .ev // "-"'; }
[ "$(ev_of "$K0")/$(ev_of "$K1")/$(ev_of "$K2")/$(ev_of "$K3")/$(ev_of "$K4")" = "none/ok/stale/unk/-" ] && ok "하위 카드 증거 상태: 없음·브랜치 끝과 맞음·낡음·비교 불가, 열린 카드는 칸 없음" || ko "ev" "$KS"
[ "$(echo "$KS" | jq -r --arg i "$K1" '.[] | select(.id==$i) | .beatAt|type')" = number ] && [ "$(echo "$KS" | jq -r --arg i "$K4" '.[] | select(.id==$i) | has("beatAt")')" = false ] && ok "진행 중 카드만 마지막 heartbeat(beatAt)를 실음" || ko "beatAt" "$KS"
OLD=$(echo "$P2" | jq -r --arg e "$E2" '.sources.board.value.features[] | select(.id==$e) | .kids[] | select(.id=="'"$K1"'") | .beatAt')
sleep 1; BD_ACTOR=w-beat b heartbeat "$K1" >/dev/null
P3=$(env -u PANEL_GH_REPOS PANEL_ORCA=/usr/bin/false LRM_HUB_DIR="$SB/hub" bash "$SCRIPTS/panel_collect.sh" --print 2>/dev/null)
NEW=$(echo "$P3" | jq -r --arg e "$E2" '.sources.board.value.features[] | select(.id==$e) | .kids[] | select(.id=="'"$K1"'") | .beatAt')
[ "$NEW" -gt "$OLD" ] && ok "heartbeat 가 beatAt 을 앞으로 보냄(updated_at 이 안 변해도)" || ko "beat 반영" "$OLD → $NEW"
[ "$(echo "$P3" | jq -r --arg w "$WORK" '.sources.board.value.doing[] | select(.id==$w) | .beatAt|type')" = number ] && ok "진행 줄 beatAt 도 heartbeat 반영 값" || ko "doing beatAt" ""

# board-answer
R=$("$SCRIPTS/board-answer" "$D" "옮김. Slack 칸은 숨겨" --json)
[ "$(echo "$R" | jq -r .closed)" = true ] && [ "$(b show "$D" --json | jq -r 'if type=="array" then .[0] else . end | .status')" = closed ] && ok "board-answer: 코멘트 + 닫음" || ko "board-answer" "$R"
b comments "$D" --json | jq -r '.[].text' | grep -q '사용자 답: 옮김. Slack 칸은 숨겨' && ok "board-answer: 원문 그대로 코멘트" || ko "코멘트 원문" ""
R2=$("$SCRIPTS/board-answer" "$D" "다시" --json); [ "$(echo "$R2" | jq -r .already)" = true ] && [ "$(b comments "$D" --json | jq length)" = 1 ] && ok "board-answer: 이미 닫힌 카드는 그대로(반복 안전)" || ko "반복" "$R2"
"$SCRIPTS/board-answer" t-nope "x" >/dev/null 2>&1; [ $? = 4 ] && ok "board-answer: 없는 카드 → 종료 4" || ko "없는 카드" ""
"$SCRIPTS/board-answer" "$T" "내일" --keep-open >/dev/null; [ "$(b show "$T" --json | jq -r 'if type=="array" then .[0] else . end | .status')" = open ] && ok "board-answer --keep-open: 코멘트만" || ko "keep-open" ""

# 작업판 없음 → 원료 실패, 다른 칸은 그대로
P=$(env -u PANEL_GH_REPOS PANEL_BOARD_DIR="$SB/none" PANEL_ORCA=/usr/bin/false LRM_HUB_DIR="$SB/hub" bash "$SCRIPTS/panel_collect.sh" --print 2>/dev/null)
[ "$(echo "$P" | jq -r .sources.board.err)" = missing ] && ok "작업판 폴더 없음 → board 원료 missing" || ko "missing" "$(echo "$P" | jq -c .sources.board | head -c 120)"

echo "작업판 결과: 통과 $PASS, 실패 $FAIL"
[ "$FAIL" = 0 ]
