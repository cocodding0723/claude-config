#!/usr/bin/env bash
# 메인 대상 자동 알림 → 작업판 카드 시험. mktemp -d 임시 작업판·작업 폴더만 씀.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
SCR="$HERE/../../hubs/scripts"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/board-notify.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export BOARD_DIR="$SB/board" LRM_HUB_DIR="$SB/hub" BOARD_ID_RE='t-[0-9a-z]{3,8}'
mkdir -p "$BOARD_DIR" "$LRM_HUB_DIR"
(cd "$BOARD_DIR" && git init -q && bd init --prefix t -q >/dev/null 2>&1) || { echo "임시 작업판 준비 실패"; exit 1; }
b() { (cd "$BOARD_DIR" && bd "$@"); }
id_of() { jq -r 'if type=="array" then .[0].id else .id end'; }
last() { b comments "$1" --json | jq -r '.[-1].text // ""'; }
cnt() { b comments "$1" --json | jq 'length'; }
MI=(python3 -I "$SCR/main_inbox.py")
BOX="$LRM_HUB_DIR/hub-inbox/main.jsonl"

DEC=$(b create "색 정할까?" -l kind:decision,slack:D-Btabc --json | id_of)
PR=$(b create "메일함 PR" -l pr:812 --json | id_of)
WORK=$(b create "메일함 목록 화면" --json | id_of)

"${MI[@]}" append --kind ci --source ci_watch "꺼진 상태 알림" >/dev/null
[ "$(wc -l < "$BOX" | tr -d ' ')" = 1 ] && ok "main-route 없으면 옛 경로(main.jsonl) 그대로" || ko "옛 경로" ""
echo board > "$LRM_HUB_DIR/main-route"

"${MI[@]}" append --kind answer --source slack_socket "[Slack→메인] 결정 D-Btabc = 선택 2(빨강) (버튼)" >/dev/null
[ "$(last "$DEC")" = "[slack_socket·answer] [Slack→메인] 결정 D-Btabc = 선택 2(빨강) (버튼)" ] && ok "결정 번호 → slack:<번호> 카드에 원문 코멘트" || ko "결정 카드" "$(last "$DEC")"
"${MI[@]}" append --kind ci --source ci_watch --ref "#812" "[CI→메인] CI 실패 PR#812 · 원인 컴파일" >/dev/null
last "$PR" | grep -q 'CI 실패 PR#812' && last "$PR" | grep -q '(ref #812)' && ok "PR 번호 → pr:<n> 카드" || ko "PR 카드" "$(last "$PR")"
"${MI[@]}" append --kind review --source ci_review "$WORK 리뷰 지적 2건" >/dev/null
last "$WORK" | grep -q '리뷰 지적 2건' && ok "본문의 카드 id → 그 카드" || ko "id 카드" "$(last "$WORK")"
"${MI[@]}" append --kind restart --source restart_watch "허브 vfx 재기동 완료" >/dev/null
"${MI[@]}" append --kind restart --source restart_watch "허브 ui 재기동 완료" >/dev/null
AL=$(b list --label alert:restart --json --limit 0)
[ "$(echo "$AL" | jq 'length')" = 1 ] && [ "$(cnt "$(echo "$AL" | jq -r '.[0].id')")" = 2 ] && echo "$AL" | jq -e '.[0].labels|index("hub:main") and index("kind:alert")' >/dev/null && ok "못 찾으면 종류별 모음 카드 1장(재사용)" || ko "모음 카드" "$AL"
[ "$(wc -l < "$BOX" | tr -d ' ')" = 1 ] && ok "켜진 뒤 main.jsonl 에 새 줄 없음(메인 입력은 사용자만)" || ko "main.jsonl 증가" "$(tail -2 "$BOX")"
"${MI[@]}" append --kind ci --source ci_watch --ref "#812" "[CI→메인] CI 실패 PR#812 · 원인 컴파일" >/dev/null
[ "$(cnt "$PR")" = 1 ] && ok "같은 알림 재전송 → 코멘트 1개 그대로" || ko "중복" "$(cnt "$PR")"
R=$("${MI[@]}" append --kind other --source demo --json "dry 확인 $WORK" --dry-run)
[ "$(cnt "$WORK")" = 1 ] && ok "--dry-run 은 작업판을 바꾸지 않음" || ko "dry-run" "$R"
python3 -I - "$SCR" <<'PY' && ok "main_inbox.sender(기존 호출처)도 작업판으로, 성공 True" || ko "sender" ""
import sys
sys.path.insert(0, sys.argv[1])
import main_inbox
sys.exit(0 if main_inbox.sender("card_actions", "stall")("워커 멈춤 30분") else 1)
PY
[ "$(b list --label alert:stall --json --limit 0 | jq 'length')" = 1 ] && ok "멈춤 알림 → 모음 카드" || ko "stall 카드" ""
BOARD_DIR="$SB/없음" "${MI[@]}" append --kind ci --source ci_watch "작업판 없음 시 예비" >/dev/null
tail -1 "$BOX" | jq -r .text | grep -q '작업판 없음 시 예비' && ok "작업판 쓰기 실패 → main.jsonl 로 떨어져 알림을 잃지 않음" || ko "예비 경로" "$(tail -1 "$BOX")"
python3 -I "$SCR/main_inbox.py" append --kind feedback --source x --box feat-mail "기능 상자는 그대로" >/dev/null
[ -s "$LRM_HUB_DIR/hub-inbox/feat-mail.jsonl" ] && ok "기능 상자(feat-*)는 작업판 경로와 무관" || ko "기능 상자" ""
python3 -I "$SCR/board_notify.py" post --kind ci --source t --json "직접 호출 $WORK" | jq -e '.status=="board"' >/dev/null && ok "board_notify.py post CLI" || ko "CLI" ""
python3 -I "$SCR/board_notify.py" post --kind ci "x" >/dev/null 2>&1; [ $? = 2 ] && ok "CLI 인자 빠짐 → 종료 2" || ko "CLI 인자" ""
# ---- Slack 답 → 부모 메시지로 보낸 기록 → 원래 카드 ----
SENT="$LRM_HUB_DIR/slack-sent.jsonl" INB="$LRM_HUB_DIR/slack-inbox.jsonl"
CARD=$(b create "봇 메시지가 붙은 작업" --json | id_of)
{
  jq -nc --arg c "$CARD" '{ts:"1700000100.001",thread_ts:"",title:"카드 칸 있는 보고",card:$c}'
  jq -nc '{ts:"1700000100.002",thread_ts:"",title:"옛 기록 결정 D-Btabc 확인"}'
  jq -nc --arg w "$WORK" '{ts:"1700000100.003",thread_ts:"",title:("옛 기록 \($w) 진행")}'
  jq -nc '{ts:"1700000100.004",thread_ts:"",title:"카드 단서 없는 보고"}'
} > "$SENT"
{
  jq -nc '{type:"thread_reply",ts:"1700000200.001",parent_ts:"1700000100.001",text:"이미지 첨부해서 ㄱㄱ"}'
  jq -nc '{type:"thread_reply",ts:"1700000200.002",parent_ts:"1700000100.002",text:"응 그걸로"}'
  jq -nc '{type:"thread_reply",ts:"1700000200.003",parent_ts:"1700000100.003",text:"계속 해"}'
  jq -nc '{type:"thread_reply",ts:"1700000200.004",parent_ts:"1700000100.004",text:"이건 뭐야"}'
  jq -nc '{type:"thread_reply",ts:"1700000200.005",parent_ts:"1700000999.999",text:"기록에 없는 부모"}'
  jq -nc '{type:"note",ts:"1700000200.006",text:"스레드 아님"}'
} > "$INB"
bn() { python3 -I "$SCR/board_notify.py" post --kind slack --source t --json --dry-run --ref "slack $1" "${2:-본문}"; }
R=$(bn 1700000200.001); [ "$(echo "$R" | jq -r '.card+" "+.how')" = "$CARD parent-card" ] && ok "부모 기록의 card 칸 → 그 카드" || ko "card 칸" "$R"
R=$(bn 1700000200.002); [ "$(echo "$R" | jq -r '.card+" "+.how')" = "$DEC parent-text" ] && ok "card 칸 없는 옛 기록 → 제목의 결정 번호로 카드" || ko "옛 기록 결정" "$R"
R=$(bn 1700000200.003); [ "$(echo "$R" | jq -r '.card+" "+.how')" = "$WORK parent-text" ] && ok "옛 기록 제목의 카드 id → 그 카드" || ko "옛 기록 id" "$R"
R=$(bn 1700000200.004); [ "$(echo "$R" | jq -r .how)" = confirm ] && ok "부모 기록에 단서 없음 → 확인 카드" || ko "확인 카드" "$R"
R=$(bn 1700000200.005); [ "$(echo "$R" | jq -r .how)" = confirm ] && ok "부모 기록 자체가 없음 → 확인 카드" || ko "기록 없음" "$R"
R=$(bn 1700000200.006); [ "$(echo "$R" | jq -r .how)" = alert ] && ok "스레드 답이 아닌 메시지 → 기존 모음 카드" || ko "비답" "$R"
R=$(bn 1700000200.004 "본문에 $WORK 있음"); [ "$(echo "$R" | jq -r '.card+" "+.how')" = "$WORK id" ] && ok "부모 기록에 없어도 본문의 카드 id 는 그대로 찾음" || ko "본문 id" "$R"
python3 -I "$SCR/board_notify.py" post --kind slack --source t --ref "slack 1700000200.004" "확인 카드 실제 기록" >/dev/null
CF=$(b list --label alert:confirm --json --limit 0)
[ "$(echo "$CF" | jq length)" = 1 ] && echo "$CF" | jq -e '.[0].labels|index("kind:confirm") and index("hub:main")' >/dev/null \
  && [ "$(cnt "$(echo "$CF" | jq -r '.[0].id')")" = 1 ] && ok "확인 카드 1장 생성(kind:confirm)·코멘트 1개" || ko "확인 카드 생성" "$CF"
printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
