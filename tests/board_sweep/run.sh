#!/usr/bin/env bash
# board_sweep 시험: 가짜 bd·gh 와 임시 폴더만 씀. 규칙 5개·닫기 막힘·dry-run·잠금·gh 호출 상한·실패 격리.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
ENTRY="$REPO/hubs/scripts/board_sweep.sh"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/bsweep-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT

export BOARD_DIR="$SB/board" BOARD_BD="$HERE/fake_bd.py" BOARD_GH="$HERE/fake_gh.py"
export LRM_BOARD_SWEEP_POLICY="$REPO/hubs/board_sweep.tsv" LRM_BOARD_SWEEP_LOCAL="$SB/local.tsv"
export FAKE_BD_STATE="$SB/state.json" FAKE_BD_LOG="$SB/bd.log" FAKE_GH_PRS="$SB/prs.json" FAKE_GH_LOG="$SB/gh.log"
export BOARD_SWEEP_NOW="2026-11-17T12:00:00+09:00" LRM_HUB_DIR="$SB/hub"
mkdir -p "$LRM_HUB_DIR"
mkdir -p "$BOARD_DIR"

# 시각 기준: 지금 = 11-17 03:00Z. 카드는 11-16 00:00Z(27시간 전) 또는 11-17 02:00Z(1시간 전)에 만듦
OLD="2026-11-16T00:00:00Z" NEW="2026-11-17T02:00:00Z"
reset() {  # reset <카드 JSON 배열> [PR 표 JSON] [로컬 덮어쓰기 표 내용]
  local cm=${COMMENTS:-} prs=${2:-}
  [ -n "$cm" ] || cm='{}'; [ -n "$prs" ] || prs='{}'
  printf '{"cards": %s, "comments": %s}' "$1" "$cm" > "$FAKE_BD_STATE"
  printf '%s' "$prs" > "$FAKE_GH_PRS"
  printf 'log_path\t%s\nlock_path\t%s\nmerge_note_state\t%s\n%s' "$SB/sweep.log" "$SB/sweep.lock" "$SB/note.json" "${3:-}" > "$LRM_BOARD_SWEEP_LOCAL"
  rm -f "$SB/note.json"; : > "$FAKE_BD_LOG"; : > "$FAKE_GH_LOG"; : > "$SB/sweep.log"
}
card() {  # card <id> <status> <labels 쉼표> <created> [title] [description] [assignee] [comment_count] [issue_type]
  python3 -c 'import json,sys; a=sys.argv[1:]; print(json.dumps({"id":a[0],"status":a[1],"labels":[l for l in a[2].split(",") if l],
    "created_at":a[3],"updated_at":a[3],"title":a[4] or a[0],"description":a[5],"assignee":a[6],"comment_count":int(a[7] or 0),"issue_type":a[8]}, ensure_ascii=False))' \
    "$1" "$2" "$3" "$4" "${5:-}" "${6:-}" "${7:-}" "${8:-0}" "${9:-task}"
}
assignee_of() { python3 -c 'import json,sys; print({c["id"]:c.get("assignee","") for c in json.load(open(sys.argv[1]))["cards"]}[sys.argv[2]])' "$FAKE_BD_STATE" "$1"; }
status_of() { python3 -c 'import json,sys; print({c["id"]:c["status"] for c in json.load(open(sys.argv[1]))["cards"]}[sys.argv[2]])' "$FAKE_BD_STATE" "$1"; }
labels_of() { python3 -c 'import json,sys; print(",".join({c["id"]:c["labels"] for c in json.load(open(sys.argv[1]))["cards"]}[sys.argv[2]]))' "$FAKE_BD_STATE" "$1"; }
sweep() { bash "$ENTRY" "$@" >"$SB/out" 2>&1; echo $? > "$SB/rc"; }
rc() { cat "$SB/rc"; }
check() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-$(head -c 400 "$SB/out")}"; fi; }

MERGED_LATE='{"state":"MERGED","mergedAt":"2026-11-17T01:00:00Z","closedAt":"2026-11-17T01:00:00Z"}'
MERGED_EARLY='{"state":"MERGED","mergedAt":"2026-11-15T01:00:00Z","closedAt":"2026-11-15T01:00:00Z"}'
CLOSED_LATE='{"state":"CLOSED","mergedAt":null,"closedAt":"2026-11-17T01:30:00Z"}'
OPEN='{"state":"OPEN","mergedAt":null,"closedAt":null}'

# ── merge_note ──
mpr() { printf '{"number":%s,"headRefName":"%s","mergedAt":"%s","mergedBy":{"login":"me"},"url":"u"}' "$1" "$2" "$3"; }
noted() { grep -c "\"comment\", \"$1\", \"\[gh-watch\] PR #$2 병합됨" "$FAKE_BD_LOG"; }
COMMENTS='{"m2":[{"text":"[gh-watch] PR #202 병합됨(병합 me) · feat/b u"}]}'
reset "[$(card m1 open branch:feat/a $OLD),$(card m2 open branch:feat/b $OLD),$(card m3 closed branch:feat/c $OLD),$(card m4 open branch:feat/d $OLD),$(card m5 open branch:feat/e,sweep:skip $OLD)]" \
  "{\"merged\":[$(mpr 201 feat/a $NEW),$(mpr 202 feat/b $NEW),$(mpr 203 feat/c $NEW),$(mpr 204 feat/d 2026-11-17T02:50:00Z),$(mpr 205 feat/e $NEW)]}"
COMMENTS=
sweep --dry-run
check "merge_note: dry-run 은 쓰지 않음" '[ "$(noted m1 201)" = 0 ] && [ ! -e "$SB/note.json" ] && grep -q "	dry	merge_note	m1	note" "$SB/sweep.log"'
sweep
check "merge_note: 빠진 병합 기록을 채움" '[ "$(noted m1 201)" = 1 ]'
check "merge_note: 이미 있으면 다시 쓰지 않음" '[ "$(noted m2 202)" = 0 ]'
check "merge_note: 닫힌 카드도 채움" '[ "$(noted m3 203)" = 1 ]'
check "merge_note: 방금 병합된 것은 gh-watch 에 맡김" '[ "$(noted m4 204)" = 0 ]'
check "merge_note: 제외 라벨 카드는 둠" '[ "$(noted m5 205)" = 0 ]'
: > "$FAKE_BD_LOG"; sweep
check "merge_note: 확인한 짝은 다시 조회하지 않음" '! grep -q "\"comments\", \"m[123]\"" "$FAKE_BD_LOG"' "$(cat "$FAKE_BD_LOG")"
reset "[$(card m1 open branch:feat/a $OLD)]" "{\"merged\":[$(mpr 201 feat/a $NEW)]}"
FAKE_BD_DOWN=comments sweep
check "merge_note: 코멘트 조회 실패면 남겨 둠" '[ "$(noted m1 201)" = 0 ]'
sweep
check "merge_note: 다음 회차에 다시 채움" '[ "$(noted m1 201)" = 1 ]'

# ── pr_done ──
reset "[$(card a open kind:decision,pr:101 $OLD),$(card b open kind:user-todo $OLD 'PR #102 확인'),$(card c open kind:decision $OLD 'PR #101·#103 병합 승인'),
$(card d open kind:task $OLD 'PR #101 후속'),$(card e open kind:decision $OLD '#104 뒤 시험 허락' '#104 는 병합됨'),$(card f open kind:approval $OLD '목록 #7 정리')]" \
  "{\"101\":$MERGED_LATE,\"102\":$CLOSED_LATE,\"103\":$OPEN,\"104\":$MERGED_EARLY}"
sweep
check "pr_done: 라벨 pr:N 이 카드 생성 뒤 병합되면 닫음" '[ "$(status_of a)" = closed ]'
check "pr_done: 제목 #N 이 닫힘이어도 닫음" '[ "$(status_of b)" = closed ]'
check "pr_done: 묶인 PR 중 하나라도 열려 있으면 둠" '[ "$(status_of c)" = open ]'
check "pr_done: kind:task 는 대상 아님" '[ "$(status_of d)" = open ]'
check "pr_done: 카드 만들 때 이미 끝난 PR 은 참고 번호로 보고 둠" '[ "$(status_of e)" = open ]'
check "pr_done: 3자리 미만 #번호는 PR 로 안 봄(gh 호출 없음)" '! grep -q "pr view 7 " "$FAKE_GH_LOG"'
check "pr_done: 닫기 전에 근거 댓글" 'grep -q "\"comment\", \"a\", \"정리(자동): PR #101 병합" "$FAKE_BD_LOG"'
check "pr_done: 같은 PR 은 실행당 1번만 조회" '[ "$(grep -c "pr view 101 " "$FAKE_GH_LOG")" = 1 ]'
check "sweep.log 에 닫은 카드마다 한 줄" '[ "$(grep -c "	apply	pr_done	.	close	" "$SB/sweep.log")" = 2 ]' "$(cat "$SB/sweep.log")"
check "정상 종료 0" '[ "$(rc)" = 0 ]'
check "sweep.log 회차 끝 요약 한 줄" '[ "$(grep -c "	apply	run	-	summary	cards=6 close=2 " "$SB/sweep.log")" = 1 ]' "$(cat "$SB/sweep.log")"

# ── slack_answered ──
INBOX="$LRM_HUB_DIR/slack-inbox.jsonl"
printf '%s\n' \
  '{"type":"button","parent_ts":"1.1","decision_id":"D-TEST1","kind":"decide","option_text":"1. 엔진 설정 유지","received_at":1794807054,"action":"pick","pick":1,"source":"slack"}' \
  '{"type":"button","parent_ts":"2.2","decision_id":"D-TEST2","kind":"decide","option_text":"직접 입력: 이거 어떻게 할까?","received_at":1794807189,"action":"custom","text":"이거 어떻게 할까?","source":"slack"}' \
  '{"type":"thread_reply","ts":"3.3","parent_ts":"3.0","decision_id":"D-TEST3","kind":"decide","text":"1","action":"free","received_at":1794807200}' \
  '{"type":"button","parent_ts":"4.4","decision_id":"D-BTEST4","kind":"decide","option_text":"2. 유지","received_at":1794807300,"action":"pick","pick":2,"source":"slack"}' > "$INBOX"
reset "[$(card sa1 open kind:decision,slack:D-TEST1 $OLD '엔진 설정 결정'),$(card sa2 open kind:decision $OLD '옮긴 카드' '배경: D-TEST1 에서 옮김'),
$(card sa3 open kind:decision,slack:D-TEST2 $OLD),$(card sa4 open kind:decision,slack:D-TEST3 $OLD),$(card sa5 open kind:task,slack:D-TEST1 $OLD),
$(card sa6 open kind:decision $NEW '번호 없음'),$(card sa9 open kind:decision $OLD '전용 줄 카드' $'배경: 설명\n결정 번호: D-TEST1'),
$(card sa10 open kind:decision,decision:D-TEST1 $OLD),$(card sa11 open kind:decision,slack:D-TEST1 $NEW),$(card sa12 open kind:decision $OLD 'PROD-TEST1 확인'),
$(card sa13 open kind:decision $OLD 'D-TEST1 결정'),$(card sa14 open kind:decision $OLD '인용만 한 카드' $'근거: 옛 결정 D-TEST1 에서 정함\nID-TEST1 은 다른 것'),$(card sa7 open kind:decision,slack:D-BTEST4,sweep:skip $OLD),$(card sa8 open kind:decision,slack:D-BTEST4 $OLD)]"
cp "$FAKE_BD_STATE" "$SB/before.json"
sweep --dry-run
check "slack_answered dry-run: 작업판을 바꾸지 않고 mode=dry 로 기록" 'cmp -s "$FAKE_BD_STATE" "$SB/before.json" && grep -q "	dry	slack_answered	sa1	close	" "$SB/sweep.log"' "$(cat "$SB/sweep.log")"
sweep
check "slack_answered: 라벨 slack:<번호> 의 버튼 답이 있으면 닫음" '[ "$(status_of sa1)" = closed ]'
check "slack_answered: 본문에 인용만 한 다른 결정 번호의 옛 답으로는 안 닫힘" '[ "$(status_of sa2)" = open ] && [ "$(status_of sa14)" = open ]'
check "slack_answered: 본문 '결정 번호:' 전용 줄·decision: 라벨·제목의 자기 번호는 닫음" '[ "$(status_of sa9)" = closed ] && [ "$(status_of sa10)" = closed ] && [ "$(status_of sa13)" = closed ]'
check "slack_answered: 카드를 만들기 전에 온 답은 안 닫음" '[ "$(status_of sa11)" = open ]'
check "slack_answered: 낱말 속 번호(PROD-TEST1)는 안 잡음" '[ "$(status_of sa12)" = open ]'
check "slack_answered: 답 원문을 댓글에 남김" 'grep -q "\"comment\", \"sa1\", \"정리(자동): Slack 에서 답함(D-TEST1, .*사용자 답: 1. 엔진 설정 유지" "$FAKE_BD_LOG"'
check "slack_answered: 직접 입력(질문)·스레드 글은 답으로 안 봄" '[ "$(status_of sa3)" = open ] && [ "$(status_of sa4)" = open ]'
check "slack_answered: 결정 카드가 아니거나 번호가 없으면 둠" '[ "$(status_of sa5)" = open ] && [ "$(status_of sa6)" = open ]'
check "slack_answered: skip 라벨은 제외, 같은 번호의 다른 카드는 닫음" '[ "$(status_of sa7)" = open ] && [ "$(status_of sa8)" = closed ]'
check "slack_answered: 카드마다 닫기 한 번·기록 한 줄" '[ "$(grep -c "\"close\", \"sa1\"" "$FAKE_BD_LOG")" = 1 ] && [ "$(grep -c "	apply	slack_answered	sa1	close	" "$SB/sweep.log")" = 1 ]'
sweep
check "slack_answered: 다시 실행해도 이미 닫힌 카드에 아무것도 안 함(반복 안전)" '[ "$(grep -c "\"comment\", \"sa1\"" "$FAKE_BD_LOG")" = 1 ]'
reset "[$(card sa1 open kind:decision,slack:D-TEST1 $OLD)]"
FAKE_BD_BLOCK=sa1 sweep
check "slack_answered: 닫기가 게이트로 막히면 우회 없이 닫기 후보 라벨" '[ "$(status_of sa1)" = open ] && [[ "$(labels_of sa1)" == *sweep:close-candidate* ]] && ! grep -q -- "--force" "$FAKE_BD_LOG"'
rm -f "$INBOX"
reset "[$(card sa1 open kind:decision,slack:D-TEST1 $OLD)]"
sweep
check "slack_answered: 답 기록 파일이 없으면 아무것도 닫지 않음" '[ "$(status_of sa1)" = open ] && [ "$(rc)" = 0 ]'

# ── expired ──
reset "[$(card x1 open kind:task,expires:2026-11-17T10:00:00+09:00 $NEW),$(card x2 open kind:task,expires:2026-11-18 $NEW),
$(card x3 open kind:decision,expires:2026-11-16 $NEW),$(card x4 open kind:task,expires:엉터리 $NEW)]"
sweep
check "expired: 지난 시각 라벨은 닫음" '[ "$(status_of x1)" = closed ]'
check "expired: 오지 않은 날짜는 둠" '[ "$(status_of x2)" = open ]'
check "expired: 날짜만 쓴 라벨은 그날 끝 기준(지난 날짜 닫음)" '[ "$(status_of x3)" = closed ]'
check "expired: 해석 못 하는 라벨은 기록만 하고 둠" '[ "$(status_of x4)" = open ] && grep -q "bad-label" "$SB/sweep.log"'

# ── stale ──
reset "[$(card s1 open kind:decision $OLD),$(card s2 open kind:decision $NEW),$(card s3 open kind:user-todo $OLD),$(card s4 open kind:decision,stale $OLD)]"
sweep
check "stale: 24시간 넘은 결정에 stale 라벨" '[ "$(labels_of s1)" = "kind:decision,stale" ]'
check "stale: 새 결정은 라벨 없음" '[ "$(labels_of s2)" = "kind:decision" ]'
check "stale: 결정이 아니면 라벨 없음" '[ "$(labels_of s3)" = "kind:user-todo" ]'
check "stale: 이미 있으면 다시 안 붙임" '! grep -q "\"label\", \"add\", \"s4\"" "$FAKE_BD_LOG"'
check "stale: 닫지 않음" '[ "$(status_of s1)" = open ]'

# ── duplicate ──
reset "[$(card u1 open kind:user-todo $OLD '원격 브랜치 삭제'),$(card u2 open kind:user-todo $NEW '원격  브랜치 삭제 '),$(card u3 open kind:user-todo $OLD '다른 일')]"
sweep
check "duplicate: 오래된 쪽에 닫기 후보 라벨" '[ "$(labels_of u1)" = "kind:user-todo,sweep:close-candidate" ]'
check "duplicate: 오래된 쪽에 새 카드 id 댓글" 'grep -q "\"comment\", \"u1\", \"정리(자동): 같은 제목의 새 카드 u2" "$FAKE_BD_LOG"'
check "duplicate: 새 쪽·다른 제목은 그대로, 아무것도 닫지 않음" '[ "$(labels_of u2)" = kind:user-todo ] && [ "$(labels_of u3)" = kind:user-todo ] && [ "$(status_of u1)" = open ]'
sweep
check "duplicate: 다음 회차에 같은 댓글을 다시 안 씀" '[ "$(grep -c "\"comment\", \"u1\"" "$FAKE_BD_LOG")" = 1 ]'

# ── orphan ──
COMMENTS='{"o2":[{"text":"진행","created_at":"2026-11-17T02:30:00Z"}]}' \
reset "[$(card o1 in_progress kind:task 2026-11-17T00:00:00Z),$(card o2 in_progress kind:task 2026-11-17T00:00:00Z '' '' '' 1),
$(card o3 in_progress kind:task 2026-11-17T00:00:00Z '' '' worker-a),$(card o4 in_progress kind:task 2026-11-17T02:30:00Z),
$(card lrm-run.1 in_progress kind:task 2026-11-17T00:00:00Z),$(card o6 in_progress kind:task,sweep:skip 2026-11-17T00:00:00Z)]" '{}' \
  "$(printf 'protect_ids\tlrm-run.*\n')"
sweep
check "orphan: 담당 없고 3시간 활동 없으면 open" '[ "$(status_of o1)" = open ] && grep -q "\"comment\", \"o1\", \"정리(자동): 담당 없음" "$FAKE_BD_LOG"'
check "orphan: 최근 댓글이 있으면 둠" '[ "$(status_of o2)" = in_progress ]'
check "orphan: 담당이 있으면 둠" '[ "$(status_of o3)" = in_progress ]'
check "orphan: 최근 갱신이면 둠" '[ "$(status_of o4)" = in_progress ]'
check "protect_ids 접두 일치·skip 라벨 카드는 제외" '[ "$(status_of lrm-run.1)" = in_progress ] && [ "$(status_of o6)" = in_progress ]'

# ── ws_gone: 작업 공간이 사라진 카드 빼기 ──
WSR="$SB/workspaces"; mkdir -p "$WSR/game/alive"
WSCFG="$(printf 'ws_root\t%s\n' "$WSR")"
MERGED_PR='[{"number":901,"state":"MERGED"}]'
ws_cards() {
  reset "[$(card w1 in_progress "kind:task,ws:$WSR/game/gone1,branch:fix/a" $OLD '' '' worker-a),
$(card w2 in_progress "kind:task,ws:$WSR/game/gone2,branch:fix/b" $OLD '' '' worker-b),
$(card w3 in_progress "kind:task,ws:$WSR/game/alive,branch:fix/a" $OLD '' '' worker-c),
$(card w4 open "kind:task,repo:$WSR/game/gone4,branch:fix/b" $OLD),
$(card w5 in_progress "kind:task,repo:$SB/elsewhere/gone5" $OLD '' '' worker-e),
$(card w6 in_progress "kind:task,ws:$WSR/game/gone6" $OLD '' '' worker-f 0 epic),
$(card w7 in_progress "kind:task,ws:$WSR/game/gone7" 2026-11-17T02:50:00Z '' '' worker-g),
$(card w8 open "kind:task,ws:$WSR/game/gone8,branch:fix/a" $OLD)]" \
    "{\"head:fix/a\":$MERGED_PR,\"head:fix/b\":[{\"number\":902,\"state\":\"OPEN\"}]}" "$WSCFG"
}
ws_cards; sweep
check "ws_gone: 작업 공간이 없고 PR 이 병합된 카드는 닫음" '[ "$(status_of w1)" = closed ] && grep -q "\"comment\", \"w1\", \"정리(자동): 작업 공간 gone1 없음·PR #901 병합" "$FAKE_BD_LOG"' "$(cat "$SB/sweep.log")"
check "ws_gone: 병합 전이면 진행 중에서 내려놓고 담당을 비움" '[ "$(status_of w2)" = open ] && [ -z "$(assignee_of w2)" ] && grep -q "\"comment\", \"w2\", \"정리(자동): 작업 공간 gone2 없음" "$FAKE_BD_LOG"' "$(cat "$SB/sweep.log")"
check "ws_gone: 작업 공간이 살아 있으면 둠" '[ "$(status_of w3)" = in_progress ] && [ "$(assignee_of w3)" = worker-c ]'
check "ws_gone: 병합 전인 할 일 카드는 그대로 둠(댓글도 없음)" '[ "$(status_of w4)" = open ] && ! grep -q "\"comment\", \"w4\"" "$FAKE_BD_LOG"'
check "ws_gone: 작업 공간 폴더 밖 경로는 보지 않음" '[ "$(status_of w5)" = in_progress ]'
check "ws_gone: 기능 카드(epic)는 건드리지 않음" '[ "$(status_of w6)" = in_progress ]'
check "ws_gone: 방금 활동한 카드는 한 회차 기다림(막 만든 작업 공간 보호)" '[ "$(status_of w7)" = in_progress ]'
check "ws_gone: 할 일 카드도 PR 이 병합됐으면 닫음" '[ "$(status_of w8)" = closed ]'
ws_cards; FAKE_BD_BLOCK=w1 sweep
check "ws_gone: 닫기가 막히면 닫기 후보 라벨을 붙이고 진행 중에서는 내려놓음" '[ "$(status_of w1)" = open ] && [[ "$(labels_of w1)" == *sweep:close-candidate* ]] && [ -z "$(assignee_of w1)" ]' "$(cat "$SB/sweep.log")"
ws_cards; FAKE_GH_FAIL=fix/a sweep
check "ws_gone: PR 조회가 실패하면 닫지 않고 내려놓기만" '[ "$(status_of w1)" = open ] && [ "$(status_of w8)" = open ]' "$(cat "$SB/sweep.log")"
ws_cards; printf 'log_path\t%s\nlock_path\t%s\nws_root\t%s\n' "$SB/sweep.log" "$SB/sweep.lock" "$SB/no-such-root" > "$LRM_BOARD_SWEEP_LOCAL"; sweep
check "ws_gone: 작업 공간 폴더 자체가 없으면(디스크 미연결) 아무 카드도 건드리지 않음" '[ "$(status_of w1)" = in_progress ] && [ "$(status_of w2)" = in_progress ] && grep -q "root-missing" "$SB/sweep.log"' "$(cat "$SB/sweep.log")"
ws_cards; cp "$FAKE_BD_STATE" "$SB/before.json"; sweep --dry-run
check "ws_gone dry-run: 상태를 바꾸지 않고 바뀔 동작만 기록" 'cmp -s "$FAKE_BD_STATE" "$SB/before.json" && [ "$(grep -cE "	dry	ws_gone	w[128]	" "$SB/sweep.log")" = 3 ]' "$(cat "$SB/sweep.log")"

# ── 닫기 막힘 ──
reset "[$(card g1 open kind:decision,pr:101 $OLD)]" "{\"101\":$MERGED_LATE}"
FAKE_BD_BLOCK=g1 sweep
check "닫기가 게이트로 막히면 우회 없이 닫기 후보 라벨" '[ "$(status_of g1)" = open ] && grep -q "close-blocked" "$SB/sweep.log" && [[ "$(labels_of g1)" == *sweep:close-candidate* ]]'
check "닫기 막힘에 --force 를 쓰지 않음" '! grep -q -- "--force" "$FAKE_BD_LOG"'
FAKE_BD_BLOCK=g1 sweep
check "닫기 후보가 된 카드는 다음 회차에 다시 댓글 안 씀" '[ "$(grep -c "\"comment\", \"g1\"" "$FAKE_BD_LOG")" = 1 ]'

# ── dry-run ──
reset "[$(card a open kind:decision,pr:101 $OLD),$(card s1 open kind:decision $OLD),$(card o1 in_progress kind:task 2026-11-17T00:00:00Z)]" "{\"101\":$MERGED_LATE}"
cp "$FAKE_BD_STATE" "$SB/before.json"
sweep --dry-run
check "dry-run: 작업판 상태를 바꾸지 않음" 'cmp -s "$FAKE_BD_STATE" "$SB/before.json"'
check "dry-run: 쓰기 명령을 bd 에 보내지 않음" '! grep -qE "^\[\"(comment|close|label|update)\"" "$FAKE_BD_LOG"' "$(cat "$FAKE_BD_LOG")"
check "dry-run: 바뀔 동작을 mode=dry 로 기록(닫기·라벨·되돌림)" '[ "$(grep -cE "	dry	(pr_done|stale|orphan)	" "$SB/sweep.log")" = 3 ] && grep -q "	dry	run	-	summary	" "$SB/sweep.log"' "$(cat "$SB/sweep.log")"

# ── gh 호출 상한·실패 격리 ──
reset "[$(card p1 open kind:decision,pr:201 $OLD),$(card p2 open kind:decision,pr:202 $OLD),$(card p3 open kind:decision,pr:203 $OLD),$(card p4 open kind:decision,pr:204 $OLD)]" \
  "{\"201\":$MERGED_LATE,\"202\":$MERGED_LATE,\"203\":$MERGED_LATE,\"204\":$MERGED_LATE}" "$(printf 'gh_calls_max\t3\n')"
sweep
check "gh 호출 상한: 실행당 설정값까지만 호출(병합 목록 1회 포함)" '[ "$(wc -l < "$FAKE_GH_LOG" | tr -d " ")" = 3 ]' "$(cat "$FAKE_GH_LOG")"
check "gh 호출 상한: 기록 한 줄 남기고 남은 카드는 둠" '[ "$(grep -c gh-cap "$SB/sweep.log")" = 1 ] && [ "$(status_of p4)" = open ]'
check "gh 호출 상한: 상한 안의 카드는 닫음" '[ "$(status_of p1)" = closed ] && [ "$(status_of p2)" = closed ]'
check "gh 호출 상한 뒤에도 다른 규칙 실행(stale)" '[[ "$(labels_of p3)" == *stale* ]]'
reset "[$(card q1 open kind:decision,pr:301 $OLD),$(card q2 open kind:decision,pr:302 $OLD)]" "{\"302\":$MERGED_LATE}"
FAKE_GH_FAIL=301 sweep
check "gh 실패: 그 카드만 두고 기록, 나머지 계속" '[ "$(status_of q1)" = open ] && grep -q gh-error "$SB/sweep.log" && [ "$(status_of q2)" = closed ] && [ "$(rc)" = 0 ]'

# ── 잠금 ──
reset "[$(card a open kind:decision,pr:101 $OLD)]" "{\"101\":$MERGED_LATE}"
python3 -c 'import fcntl,sys,time; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX); open(sys.argv[2],"w").close(); time.sleep(5)' "$SB/sweep.lock" "$SB/locked" &
HOLDER=$!
for _ in $(seq 50); do [ -e "$SB/locked" ] && break; sleep 0.1; done
sweep
check "잠금: 다른 실행이 잡고 있으면 종료 4·아무것도 안 함" '[ "$(rc)" = 4 ] && [ ! -s "$FAKE_BD_LOG" ] && [ "$(status_of a)" = open ]' "rc $(rc) $(cat "$SB/out")"
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null
sweep
check "잠금: 풀린 뒤에는 정상 실행" '[ "$(rc)" = 0 ] && [ "$(status_of a)" = closed ]'

# ── 설정·인자 ──
reset "[]"
LRM_BOARD_SWEEP_POLICY="$SB/none.tsv" sweep
check "규칙 표가 없으면 종료 2·원인 안내" '[ "$(rc)" = 2 ] && grep -q "규칙 표 읽기 실패" "$SB/out"'
printf 'rules\tpr_done,nope\n' >> "$LRM_BOARD_SWEEP_LOCAL"
sweep --json
check "모르는 규칙 이름은 기록하고 나머지 실행, --json 요약" '[ "$(rc)" = 0 ] && grep -q unknown-rule "$SB/sweep.log" && tail -1 "$SB/out" | python3 -c "import json,sys; json.load(sys.stdin)"'
sweep --help
check "--help 종료 0" '[ "$(rc)" = 0 ] && grep -q "board_sweep.tsv" "$SB/out"'

printf '통과 %d · 실패 %d\n' "$PASS" "$FAIL"
python3 "$HERE/test_feature_close.py" || FAIL=$((FAIL + 1))
python3 "$HERE/test_feature_merged.py" || FAIL=$((FAIL + 1))
[ "$FAIL" = 0 ]
