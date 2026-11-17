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
export BOARD_SWEEP_NOW="2026-11-17T12:00:00+09:00"
mkdir -p "$BOARD_DIR"

# 시각 기준: 지금 = 11-17 03:00Z. 카드는 11-16 00:00Z(27시간 전) 또는 11-17 02:00Z(1시간 전)에 만듦
OLD="2026-11-16T00:00:00Z" NEW="2026-11-17T02:00:00Z"
reset() {  # reset <카드 JSON 배열> [PR 표 JSON] [로컬 덮어쓰기 표 내용]
  local cm=${COMMENTS:-} prs=${2:-}
  [ -n "$cm" ] || cm='{}'; [ -n "$prs" ] || prs='{}'
  printf '{"cards": %s, "comments": %s}' "$1" "$cm" > "$FAKE_BD_STATE"
  printf '%s' "$prs" > "$FAKE_GH_PRS"
  printf 'log_path\t%s\nlock_path\t%s\n%s' "$SB/sweep.log" "$SB/sweep.lock" "${3:-}" > "$LRM_BOARD_SWEEP_LOCAL"
  : > "$FAKE_BD_LOG"; : > "$FAKE_GH_LOG"; : > "$SB/sweep.log"
}
card() {  # card <id> <status> <labels 쉼표> <created> [title] [description] [assignee] [comment_count]
  python3 -c 'import json,sys; a=sys.argv[1:]; print(json.dumps({"id":a[0],"status":a[1],"labels":[l for l in a[2].split(",") if l],
    "created_at":a[3],"updated_at":a[3],"title":a[4] or a[0],"description":a[5],"assignee":a[6],"comment_count":int(a[7] or 0)}, ensure_ascii=False))' \
    "$1" "$2" "$3" "$4" "${5:-}" "${6:-}" "${7:-}" "${8:-0}"
}
status_of() { python3 -c 'import json,sys; print({c["id"]:c["status"] for c in json.load(open(sys.argv[1]))["cards"]}[sys.argv[2]])' "$FAKE_BD_STATE" "$1"; }
labels_of() { python3 -c 'import json,sys; print(",".join({c["id"]:c["labels"] for c in json.load(open(sys.argv[1]))["cards"]}[sys.argv[2]]))' "$FAKE_BD_STATE" "$1"; }
sweep() { bash "$ENTRY" "$@" >"$SB/out" 2>&1; echo $? > "$SB/rc"; }
rc() { cat "$SB/rc"; }
check() { if eval "$2"; then ok "$1"; else ko "$1" "${3:-$(head -c 400 "$SB/out")}"; fi; }

MERGED_LATE='{"state":"MERGED","mergedAt":"2026-11-17T01:00:00Z","closedAt":"2026-11-17T01:00:00Z"}'
MERGED_EARLY='{"state":"MERGED","mergedAt":"2026-11-15T01:00:00Z","closedAt":"2026-11-15T01:00:00Z"}'
CLOSED_LATE='{"state":"CLOSED","mergedAt":null,"closedAt":"2026-11-17T01:30:00Z"}'
OPEN='{"state":"OPEN","mergedAt":null,"closedAt":null}'

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
  "{\"201\":$MERGED_LATE,\"202\":$MERGED_LATE,\"203\":$MERGED_LATE,\"204\":$MERGED_LATE}" "$(printf 'gh_calls_max\t2\n')"
sweep
check "gh 호출 상한: 실행당 설정값까지만 호출" '[ "$(wc -l < "$FAKE_GH_LOG" | tr -d " ")" = 2 ]' "$(cat "$FAKE_GH_LOG")"
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
[ "$FAIL" = 0 ]
