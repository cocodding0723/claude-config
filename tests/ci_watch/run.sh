#!/usr/bin/env bash
# ci_watch.py·bot_alive.py 시험. 가짜 gh·알림과 임시 폴더만 씀(네트워크·Slack 없음)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P); S="$HERE/../../hubs/scripts"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); echo "PASS  $1"; }
ko() { FAIL=$((FAIL + 1)); echo "FAIL  $1 — ${2:-}"; }
D=$(mktemp -d "${TMPDIR:-/tmp}/cc-ciwatch.XXXXXX"); trap 'rm -rf "$D"' EXIT
export FAKE_DIR="$D" LRM_HUB_DIR="$D" LRM_GH="$HERE/fake_gh.sh" LRM_NOTIFY="$HERE/fake_send.sh" LRM_ORCA="$HERE/fake_orca.sh" LRM_MAIN_ENV="$D/main.env"
printf 'OTHER=x\nMAIN_TERMINAL=term_abc\n' > "$D/main.env"
chmod +x "$HERE"/fake_*.sh
echo '[{"number":679,"title":"[LRM-474] fix: 게이트"}]' > "$D/prs.json"
chk() { printf '[{"name":"Run Unity validation / Unity validation","workflow":"PR Test Pipeline","bucket":"%s","link":"https://x/actions/runs/11/job/22"},{"name":"AI Review Gate (%s/5)","workflow":"Preview Stack Events","bucket":"%s","link":""},{"name":"mention-author","workflow":"PR Author Mention","bucket":"fail","link":""}]' "$1" "$2" "$3" > "$D/checks-679.json"; }
printf 'Unity validation\t2026-11-11T05:00:00.1Z ##[group]x\nUnity validation\t2026-11-11T05:00:01.2Z 시트 스냅숏이 없음: Locale.csv\n' > "$D/log.txt"
sent() { [ -f "$D/sent.log" ] && grep -c '^===$' "$D/sent.log" || echo 0; }
run() { python3 "$S/ci_watch.py" "$@" > "$D/out" 2>&1; }

chk pending 3 pending; run; [ "$(sent)" = 0 ] && ok "첫 실행은 기준만 기록, 전송 0" || ko "첫 실행" "$(sent)"
chk fail 5 pass; run; [ "$(sent)" = 1 ] && ok "결론 변화 → 1건 전송" || ko "변화 전송" "$(sent)"
grep -q -- "--kind warn" "$D/sent.log" && grep -q "시트 스냅숏이 없음: Locale.csv" "$D/sent.log" && ok "실패면 warn + 원인 한 줄" || ko "원인 줄" "$(cat "$D/sent.log")"
! grep -q "AI Review Gate" "$D/sent.log" && ! grep -q "mention-author" "$D/sent.log" && ok "실패한 감시 대상만 보냄" || ko "감시 대상"
! grep -q "LRM-474" "$D/sent.log" && ok "티켓 번호 본문 제외" || ko "티켓"
run; [ "$(sent)" = 1 ] && ok "같은 결론 재전송 없음" || ko "중복 방지" "$(sent)"
chk pending 5 pass; run; [ "$(sent)" = 1 ] && ok "재실행 진행 중은 무음" || ko "진행 무음" "$(sent)"
chk pass 5 pass; run; [ "$(sent)" = 2 ] && tail -5 "$D/sent.log" | grep -q -- "--kind done" && grep -q "참고: PR #679" "$D/sent.log" && ok "전부 통과 → done 1건(PR 번호는 참고 줄)" || ko "통과 알림" "$(sent)"
run; [ "$(sent)" = 2 ] && ok "통과 재전송 없음" || ko "통과 중복" "$(sent)"
chk fail 5 pass; LRM_NOTIFY=/usr/bin/false run; chk fail 5 pass; run; [ "$(sent)" = 3 ] && ok "전송 실패 시 다음 주기 재시도" || ko "재시도" "$(sent)"
echo 680 > "$D/ci-watch.txt"; echo '[]' > "$D/checks-680.json"; run; grep -q "감시 2건" "$D/out" && ok "ci-watch.txt PR 목록 사용" || ko "목록 파일" "$(cat "$D/out")"
rm "$D/ci-watch.txt"

# 봇 생존
now() { date '+%m-%d %H:%M:%S'; }
old() { date -v-20M '+%m-%d %H:%M:%S'; }
echo "$(now) [socket] 생존 연결=예 마지막이벤트=3분 전 재연결=0회" > "$D/slack-socket.log"
out=$(python3 "$S/bot_alive.py"); [ "$out" = "봇: 연결됨 · 마지막 수신 3분 전" ] && ok "생존 줄" || ko "생존 줄" "$out"
: > "$D/sent.log"
echo "$(old) [socket] 생존 연결=예 마지막이벤트=3분 전 재연결=0회" > "$D/slack-socket.log"
python3 "$S/bot_alive.py" --warn-once; python3 "$S/bot_alive.py" --warn-once
[ "$(sent)" = 1 ] && grep -q -- "--kind warn" "$D/sent.log" && ok "끊김 warn 1회만" || ko "끊김 1회" "$(sent)"
echo "$(now) [socket] 생존 연결=예 마지막이벤트=1분 전 재연결=0회" >> "$D/slack-socket.log"
python3 "$S/bot_alive.py" --warn-once; [ ! -e "$D/bot-alive.warned" ] && ok "복구 시 표시 초기화" || ko "초기화"

# ---- AI 리뷰 지적 전달 ----
rm -f "$D/ci-watch.state.json"; : > "$D/sent.log"; : > "$D/orca.log"; cp "$HERE/../../hubs/ci-review-hubs.tsv" "$D/"
echo '[{"number":700,"title":"[LRM-400] feat: 눈","headRefName":"feat/LRM-400-eye"}]' > "$D/prs.json"
rv() { printf '[{"name":"Review / correctness","workflow":"Claude Code Review","bucket":"%s","link":""}]' "$1" > "$D/checks-700.json"; }
body() { python3 -c 'import json,sys;print(json.dumps([{"id":int(a.split(":")[0]),"body":"<!-- lrm-ai-review:correctness -->\n## x — PASS\n\n`abcdef1234567` · y\n\n### 아쉬운 점 · 권장 개선\n\n"+"".join("**무엇:** %s · [a.py:1](u)  \n**이유:** r\n\n" % t for t in a.split(":")[1].split(",") if t)+"**반드시 수정해야 하는 점:** "+(a.split(":")[2] or "없음")+".\n\n**팀 규칙:** 없음."} for a in sys.argv[1:]]))' "$@" > "$D/comments-700.json"; }
body "1:옛 지적:"; rv pass; run; [ ! -s "$D/orca.log" ] && ok "리뷰 전달 첫 실행은 기존 지적 기준만" || ko "리뷰 기준" "$(cat "$D/orca.log")"
body "1:옛 지적,새 지적 A:널 검사 빠짐"; rv pending; run; [ ! -s "$D/orca.log" ] && ok "리뷰 진행 중이면 수집 안 함" || ko "진행 중 수집"
rv pass; run; grep -q "\[CI→메인\] AI 리뷰 지적 2건 PR#700 · 담당 허브 VFX 허브(vfx)" "$D/orca.log" && grep -q -- "--terminal term_abc" "$D/orca.log" && ok "새 지적 2건(권장+필수) → 메인 터미널, VFX 허브 매핑" || ko "메인 전달" "$(cat "$D/orca.log")"
f=$(ls "$D"/ci-review/PR-700-*.md 2>/dev/null | head -1); grep -q "\[필수·correctness\] 널 검사 빠짐" "$f" && grep -q "강제 push·dev 병합 금지" "$f" && ! grep -q "옛 지적" "$f" && ok "요약 파일: 새 지적만, 처리 규칙 포함" || ko "요약 파일" "$f"
: > "$D/orca.log"; run; [ ! -s "$D/orca.log" ] && ok "같은 지적 재전달 없음" || ko "중복 전달"
body "1:옛 지적,새 지적 A,셋째:널 검사 빠짐"; LRM_ORCA=/usr/bin/false run; grep -q "메인 전달 실패" "$D/sent.log" && ok "메인 전송 실패 → Slack 경고" || ko "실패 경고"
run; grep -q "지적 1건" "$D/orca.log" && ok "실패분 다음 주기 재전달" || ko "재전달" "$(cat "$D/orca.log")"
python3 -c '
import sys;sys.path.insert(0,sys.argv[1]);import ci_review as r
t=r.load_table(sys.argv[2])
assert r.hub_for("sprint/묶음-C-x","",t).startswith("구조"), "묶음"
assert r.hub_for("feat/LRM-4000","",t).startswith("미배정"), "경계"
' "$S" "$D/ci-review-hubs.tsv" && ok "허브 표: 묶음→구조, LRM-4000 은 미배정" || ko "허브 표"

# ---- 리뷰어 오류는 지적과 분리 ----
echo '[{"number":703,"title":"[LRM-264] x","headRefName":"b"}]' > "$D/prs.json"; rm -f "$D/ci-watch.state.json"; : > "$D/orca.log"
echo '[{"name":"Review / x","workflow":"Claude Code Review","bucket":"pending","link":""}]' > "$D/checks-703.json"; echo '[]' > "$D/comments-703.json"; run
python3 -c 'import json;print(json.dumps([{"id":9,"body":"<!-- lrm-ai-review:security -->\n## Claude AI · 보안 — ERROR\n\n**아쉬운 점 · 권장 개선:** 평가 결과 없음.\n\n**반드시 수정해야 하는 점:** 평가 결과 없음.\n"},{"id":10,"body":"<!-- lrm-ai-review:readability -->\n## Claude AI · 가독성 — PASS\n\n**반드시 수정해야 하는 점:** 평가 결과 없음.\n"}]))' > "$D/comments-703.json"
echo '[{"name":"Review / x","workflow":"Claude Code Review","bucket":"pass","link":""}]' > "$D/checks-703.json"; run
grep -q "AI 리뷰 오류 1명 PR#703 · 역할 security" "$D/orca.log" && ! grep -q "AI 리뷰 지적" "$D/orca.log" && ok "ERROR·평가 결과 없음은 지적 아님 → 오류로 별도 전달" || ko "오류 분리" "$(cat "$D/orca.log")"
: > "$D/orca.log"; run; [ ! -s "$D/orca.log" ] && ok "오류 재전달 없음" || ko "오류 중복"
python3 -c '
import sys;sys.path.insert(0,sys.argv[1]);import ci_review as r
assert r.hub_for("feat/LRM-279-x","",r.load_table(sys.argv[2])).startswith("UI 허브")' "$S" "$HERE/../../hubs/ci-review-hubs.tsv" && ok "LRM-279 → UI 허브" || ko "UI 매핑"

# ---- 게이트 실패 원인이 리뷰어 오류뿐이면 합침 ----
: > "$D/sent.log"; : > "$D/orca.log"
echo '[{"number":703,"title":"x","headRefName":"b","headRefOid":"e1"}]' > "$D/prs.json"
echo '[{"name":"AI Review Gate (0/5)","workflow":"Preview Stack Events","bucket":"pending","link":""}]' > "$D/checks-703.json"; run
echo '[{"name":"AI Review Gate (0/5)","workflow":"Preview Stack Events","bucket":"fail","link":""}]' > "$D/checks-703.json"; run
grep -q "AI Review Gate: 리뷰어 오류(재실행 대상)" "$D/sent.log" && ! grep -q "CI 실패 PR#703" "$D/orca.log" && ok "리뷰어 오류뿐인 게이트 실패: Slack 원인 1줄, 메인 CI 실패 전달 없음" || ko "게이트 합침" "$(cat "$D/sent.log") | $(cat "$D/orca.log")"

# ---- 원인 줄: Shader error 우선, FAILED 0 무시, xml 대체 ----
echo '[{"number":679,"title":"t"}]' > "$D/prs.json"; rm -f "$D/ci-watch.state.json"; : > "$D/sent.log"
chk pending 1 pending; run
printf 'x\t2026-11-11T05:00:00.1Z FAILED 0\nx\t2026-11-11T05:00:01.1Z Shader error in Foo: unrecognized identifier\n' > "$D/log.txt"
chk fail 5 pass; run; grep -q "Shader error in Foo" "$D/sent.log" && ok "Shader error 원인 우선" || ko "Shader" "$(cat "$D/sent.log")"
rm -f "$D/ci-watch.state.json"; : > "$D/sent.log"; chk pending 1 pending; run
printf 'x\t2026-11-11T05:00:00.1Z FAILED 0\n' > "$D/log.txt"; mkdir -p "$D/artifact/t"
printf '<test-run><test-case name="EyeTest" result="Failed"><failure><message>Expected 2 but was 1</message></failure></test-case></test-run>' > "$D/artifact/t/r.xml"
chk fail 5 pass; run; grep -q "EyeTest: Expected 2 but was 1" "$D/sent.log" && ok "로그에 원인 없으면 결과 xml 실패 메시지" || ko "xml" "$(cat "$D/sent.log")"


# ---- CI 실패 메인 전달 ----
echo '[{"number":701,"title":"[LRM-397] x","headRefName":"b","headRefOid":"1111111111111111"}]' > "$D/prs.json"; rm -f "$D/ci-watch.state.json"; : > "$D/orca.log"
printf 'x\t2026-11-11T05:00:01.1Z not ok 3 - eye\n' > "$D/log.txt"; rm -rf "$D/artifact"
c701() { printf '[{"name":"Run Unity validation / Unity validation","workflow":"PR Test Pipeline","bucket":"%s","link":"https://x/actions/runs/11/job/22"}]' "$1" > "$D/checks-701.json"; }
c701 pending; run; c701 fail; run
grep -q "\[CI→메인\] CI 실패 PR#701 · 담당 허브 VFX 허브(vfx) · 원인 Run Unity validation / Unity validation: not ok 3 - eye" "$D/orca.log" && [ -f "$D/ci-review/PR-701-111111111111-fail.md" ] && ok "CI 실패 → 메인 터미널 + fail.md" || ko "실패 전달" "$(cat "$D/orca.log")"
grep -q -- "--thread 1700000000.000100" "$D/ci-review/PR-701-111111111111-fail.md" && ok "warn ts → fail.md 스레드 답 명령" || ko "스레드 ts" "$(cat "$D"/ci-review/PR-701-*-fail.md)"
c701 pending; run; c701 fail; run; [ "$(grep -c 'CI 실패 PR#701' "$D/orca.log")" = 1 ] && ok "같은 커밋 실패는 1회" || ko "커밋 중복"
sed -i '' 's/1111111111111111/2222222222222222/' "$D/prs.json"; c701 pending; run; c701 fail; run; [ "$(grep -c 'CI 실패 PR#701' "$D/orca.log")" = 2 ] && ok "새 커밋 실패는 다시 전달" || ko "새 커밋"

echo "통과 $PASS / 실패 $FAIL"; [ "$FAIL" = 0 ]
