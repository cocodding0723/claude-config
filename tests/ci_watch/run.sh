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
grep -q "\[CI→메인\] CI 실패 PR#701 · 담당 허브 VFX 허브(vfx) (근거: 허브 표(ci-review-hubs.tsv) 일치) · 원인 Run Unity validation / Unity validation: not ok 3 - eye" "$D/orca.log" && [ -f "$D/ci-review/PR-701-111111111111-fail.md" ] && ok "CI 실패 → 메인 터미널 + fail.md" || ko "실패 전달" "$(cat "$D/orca.log")"
grep -q -- "--thread 1700000000.000100" "$D/ci-review/PR-701-111111111111-fail.md" && ok "warn ts → fail.md 스레드 답 명령" || ko "스레드 ts" "$(cat "$D"/ci-review/PR-701-*-fail.md)"
c701 pending; run; c701 fail; run; [ "$(grep -c 'CI 실패 PR#701' "$D/orca.log")" = 1 ] && ok "같은 커밋 실패는 1회" || ko "커밋 중복"
sed -i '' 's/1111111111111111/2222222222222222/' "$D/prs.json"; c701 pending; run; c701 fail; run; [ "$(grep -c 'CI 실패 PR#701' "$D/orca.log")" = 2 ] && ok "새 커밋 실패는 다시 전달" || ko "새 커밋"

# ---- CI 실패 담당 허브 자동 지정 ----
cp "$HERE/../../hubs/hubs.tsv" "$D/hubs.tsv"
PJ=github:basakansoft/loverevolutionmaker
echo '{"result":{"repos":[{"id":"r1","path":"/p/OtherRepo","displayName":"OtherRepo"},{"id":"r2","path":"/p/LoveRevolutionMaker","displayName":"LoveRevolutionMaker"}]}}' > "$D/repos.json"
# wt <브랜치> [projectId] [truncated] [omitted호스트]: 허브 폴더 + 하위 작업 공간 1개
wt() { printf '{"result":{"truncated":%s,"hostScope":{"hostIds":["local"],"omittedHostIds":[%s]},"worktrees":[{"id":"r::/w/coord-ui-layer-hub","path":"/w/coord-ui-layer-hub","branch":"refs/heads/main","projectId":"%s"},{"id":"r::/w/x","path":"/w/x","branch":"refs/heads/%s","projectId":"%s","parentWorktreeId":"r::/w/coord-ui-layer-hub"}]}}' "${3:-false}" "${4:-}" "$PJ" "$1" "${2:-$PJ}" > "$D/worktrees.json"; }
prs() { echo "[$1]" > "$D/prs.json"; }
pj() { printf '{"number":%s,"title":"[LRM-1] t","headRefName":"%s","headRefOid":"%s"%s}' "$1" "$2" "$3" "${4:-}"; }
fresh() { rm -f "$D/ci-watch.state.json" "$D/orca.log" "$D/worktrees.json"; : > "$D/orca.log"; }
# 검사 결과·로그: 710 계열은 같은 검사, 로그 마지막 줄만 다름
ck() { printf '[{"name":"Run Unity validation / Unity validation","workflow":"PR Test Pipeline","bucket":"%s","link":"https://x/actions/runs/%s/job/22"}]' "$1" "$2" > "$D/checks-$3.json"; }
cycle() { for n in "$@"; do ck pending 11 $n; done; run; for n in "$@"; do ck fail 11 $n; done; run; }
log() { printf 'x\t2026-11-11T05:00:01.1Z %s\n' "$1" > "$D/log.txt"; }
fresh; log "not ok 3 - eye"; prs "$(pj 710 feat/mine aaaaaaaaaaaaaaaa)"; wt feat/mine; cycle 710
grep -q "담당 허브 UI 계층 통합 설계 (근거: PR 브랜치의 작업 공간 허브)" "$D/orca.log" && ok "브랜치 → 부모 허브(작업 공간 순위)" || ko "부모 허브" "$(cat "$D/orca.log")"
fresh; prs "$(pj 711 feat/LRM-397-x bbbbbbbbbbbbbbbb)"; wt feat/other; cycle 711
grep -q "담당 허브 VFX 허브(vfx) (근거: 허브 표" "$D/orca.log" && ok "작업 공간 없음 → 표" || ko "표 대체" "$(cat "$D/orca.log")"
fresh; prs "$(pj 712 feat/none cccccccccccccccc)"; cycle 712
grep -q "담당 허브 미배정(메인 판단) (근거: 작업 공간·표·공통 원인 모두 없음)" "$D/orca.log" && ok "조회 실패+표 없음 → 미배정" || ko "미배정" "$(cat "$D/orca.log")"
fresh; prs "$(pj 713 feat/LRM-397-y dddddddddddddddd)"; cycle 713
grep -q "담당 허브 VFX 허브(vfx) (근거: 허브 표" "$D/orca.log" && ok "조회 실패 → 표로 안전하게" || ko "조회 실패 표" "$(cat "$D/orca.log")"
fresh; prs "$(pj 714 feat/LRM-397-z eeeeeeeeeeeeeeee)"; wt feat/LRM-397-z github:basakansoft/other-repo; cycle 714
grep -q "담당 허브 VFX 허브(vfx) (근거: 허브 표" "$D/orca.log" && ok "다른 저장소 같은 브랜치 → 소유 허브 미지정(표)" || ko "다른 저장소" "$(cat "$D/orca.log")"
fresh; prs "$(pj 715 feat/mine ffffffffffffffff)"; wt feat/mine "$PJ" true; cycle 715
grep -q "담당 허브 미배정(메인 판단)" "$D/orca.log" && ok "truncated → 조회 불완전, 표로(없으면 미배정)" || ko "truncated" "$(cat "$D/orca.log")"
fresh; prs "$(pj 716 feat/mine 1010101010101010)"; wt feat/mine "$PJ" false '"runtime:x"'; cycle 716
grep -q "담당 허브 미배정(메인 판단)" "$D/orca.log" && ok "원격 호스트 누락 → 조회 불완전" || ko "호스트 누락" "$(cat "$D/orca.log")"
fresh; prs "$(pj 717 coord/ui 2020202020202020)"; printf '{"result":{"truncated":false,"worktrees":[{"id":"r::/w/coord-ui-layer-hub","path":"/w/coord-ui-layer-hub","branch":"refs/heads/coord/ui","projectId":"%s"}]}}' "$PJ" > "$D/worktrees.json"; cycle 717
grep -q "담당 허브 UI 계층 통합 설계 (근거: PR 브랜치의 작업 공간 허브)" "$D/orca.log" && ok "허브 폴더 자체 브랜치 → 그 허브" || ko "허브 폴더" "$(cat "$D/orca.log")"
fresh; prs "$(pj 718 feat/dup 3030303030303030)"; wt feat/dup; python3 - "$D/worktrees.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d["result"]["worktrees"].append(dict(d["result"]["worktrees"][1],id="r::/w/y",path="/w/y")); json.dump(d,open(sys.argv[1],"w"))
PY
cycle 718
grep -q "담당 허브 미배정(메인 판단)" "$D/orca.log" && ok "후보가 2개 → 지정 안 함" || ko "후보 중복" "$(cat "$D/orca.log")"

# 저장소 범위·상한 인자 전달, 매핑 실패 → 표
fresh; log "not ok 3 - eye"; prs "$(pj 760 feat/LRM-397-w 1212121212121212)"; wt feat/LRM-397-w; cycle 760
grep -q "^worktree list --repo path:/p/LoveRevolutionMaker --limit 1000 --json" "$D/orca.log" && ok "worktree list 에 --repo path:·--limit 1000 전달" || ko "repo·limit 인자" "$(cat "$D/orca.log")"
fresh; LRM_WT_LIMIT=300 python3 -c 'import sys;sys.path.insert(0,sys.argv[1]);import ci_review as r;r.list_worktrees(sys.argv[2],"A/LoveRevolutionMaker",300)' "$S" "$HERE/fake_orca.sh"
grep -q -- "--limit 300 --json" "$D/orca.log" && ok "--limit 설정값 반영" || ko "limit 설정" "$(cat "$D/orca.log")"
fresh; echo '{"result":{"repos":[{"path":"/p/X","displayName":"Other"}]}}' > "$D/repos.json"; prs "$(pj 761 feat/LRM-397-v 1313131313131313)"; wt feat/LRM-397-v; cycle 761
grep -q "담당 허브 VFX 허브(vfx) (근거: 허브 표" "$D/orca.log" && ! grep -q "^worktree list" "$D/orca.log" && ok "저장소 매핑 실패 → 작업 공간 조회 안 하고 표로" || ko "매핑 실패" "$(cat "$D/orca.log")"
echo '{"result":{"repos":[{"id":"r2","path":"/p/LoveRevolutionMaker","displayName":"LoveRevolutionMaker"}]}}' > "$D/repos.json"

# 공통 원인: 서로 다른 PR 의 같은 시험 실패 → QA 허브. 처리 순서를 바꿔도 같은 결과
for order in "720 721" "721 720"; do
  fresh; log "not ok 9 - shared"; wt feat/mine; prs "$(pj 720 feat/mine 4040404040404040),$(pj 721 feat/o 5050505050505050)"
  set -- $order; first=$1
  [ "$first" = 721 ] && prs "$(pj 721 feat/o 5050505050505050),$(pj 720 feat/mine 4040404040404040)"
  cycle 720 721
  [ "$(grep -c "담당 허브 테스트·QA" "$D/orca.log")" -ge 1 ] && grep -q "담당 변경 PR#" "$D/orca.log" && ok "공통 원인 → QA 허브 + 먼저 간 PR 담당 변경 ($order)" || ko "공통 원인 $order" "$(cat "$D/orca.log")"
done
# 같은 종료 요약, 서로 다른 assert → 공통 아님
fresh; wt feat/mine; prs "$(pj 730 feat/mine 6060606060606060),$(pj 731 feat/o 7070707070707070)"
printf 'x\t2026-11-11T05:00:01.1Z ##[error]Process completed with exit code 1.\n' > "$D/log.txt"
cycle 730 731
! grep -q "테스트·QA" "$D/orca.log" && grep -q "담당 허브 UI 계층" "$D/orca.log" && ok "종료 요약만 같고 구체 원인 없음 → 공통 아님" || ko "종료 요약" "$(cat "$D/orca.log")"
python3 -c '
import sys;sys.path.insert(0,sys.argv[1]);import ci_review as r
n={"test-qa":"테스트·QA","ui-layer":"UI 계층 통합 설계"}
def e(h,c): return {"head":h,"fails":[{"key":"U","cause":c}],"hub":""}
f={"710":e("a","not ok 1 - A"),"711":e("b","not ok 1 - A"),"712":e("c","not ok 2 - B"),"713":e("d","Process completed with exit code 1."),"714":e("e","Process completed with exit code 1.")}
assert r.common_cause(f,"710")==["711"] and r.common_cause(f,"711")==["710"], "공통 대칭"
assert r.common_cause(f,"712")==[] and r.common_cause(f,"713")==[], "단독·종료 요약"
r.prune_causes(f,{"710":"a","712":"zzz"}); assert set(f)=={"710"}, "닫힌 PR·옛 head 제거"
assert r.common_cause(f,"710")==[], "제거 뒤 공통 아님"
h,w=r.assign_hub("feat/zzz","t",[],[],n,["711"],"테스트·QA"); assert h=="테스트·QA" and "711" in w, "QA"
assert r.assign_hub("feat/zzz","t",[],[],n,[],"테스트·QA")[0].startswith("미배정"), "공통 없음"
' "$S" && ok "공통 원인 판정: 대칭·종료 요약 제외·닫힌 PR/옛 head 제거" || ko "공통 원인 단위"
fresh; log "##[error]Process completed with exit code 1."; printf 'x\t2026-11-11T05:00:02.1Z not ok 5 - real\n' >> "$D/log.txt"; prs "$(pj 740 feat/mine 8080808080808080)"; cycle 740
grep -q "원인 Run Unity validation / Unity validation: not ok 5 - real" "$D/orca.log" && ok "일반 종료 요약은 건너뛰고 구체 원인 추출" || ko "원인 추출" "$(cat "$D/orca.log")"
# 닫힌 PR 은 상태 파일 원인 표에서 제거
fresh; log "not ok 9 - shared"; prs "$(pj 750 feat/mine 9090909090909090)"; cycle 750
prs "$(pj 750 feat/mine 9090909090909090 ',"state":"CLOSED"')"; ck pending 11 750; run
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert "750" not in d.get("_causes",{}),d' "$D/ci-watch.state.json" && ok "닫힌 PR 원인 제거" || ko "닫힌 PR"

echo "통과 $PASS / 실패 $FAIL"; [ "$FAIL" = 0 ]
