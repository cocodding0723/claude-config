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
CKN="Run Unity validation / Unity validation"  # 래퍼 검사. 공통 원인 시험은 실제 검사 이름으로 바꿔 씀
ck() { printf '[{"name":"%s","workflow":"PR Test Pipeline","bucket":"%s","link":"https://x/actions/runs/%s/job/22"}]' "$CKN" "$1" "$2" > "$D/checks-$3.json"; }
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
CKN="Unity Tests / EditMode"
for order in "720 721" "721 720"; do
  fresh; log "not ok 9 - shared"; wt feat/mine; prs "$(pj 720 feat/mine 4040404040404040),$(pj 721 feat/o 5050505050505050)"
  set -- $order; first=$1
  [ "$first" = 721 ] && prs "$(pj 721 feat/o 5050505050505050),$(pj 720 feat/mine 4040404040404040)"
  cycle 720 721
  [ "$(grep -c "담당 허브 테스트·QA" "$D/orca.log")" -ge 1 ] && grep -q "담당 변경 PR#" "$D/orca.log" && ok "공통 원인 → QA 허브 + 먼저 간 PR 담당 변경 ($order)" || ko "공통 원인 $order" "$(cat "$D/orca.log")"
done
CKN="Unity Tests / EditMode"  # 아래 시험도 실제 검사 이름 사용
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
CKN="Run Unity validation / Unity validation"
fresh; log "##[error]Process completed with exit code 1."; printf 'x\t2026-11-11T05:00:02.1Z not ok 5 - real\n' >> "$D/log.txt"; prs "$(pj 740 feat/mine 8080808080808080)"; cycle 740
grep -q "원인 Run Unity validation / Unity validation: not ok 5 - real" "$D/orca.log" && ok "일반 종료 요약은 건너뛰고 구체 원인 추출" || ko "원인 추출" "$(cat "$D/orca.log")"
# 닫힌 PR 은 상태 파일 원인 표에서 제거
fresh; log "not ok 9 - shared"; prs "$(pj 750 feat/mine 9090909090909090)"; cycle 750
prs "$(pj 750 feat/mine 9090909090909090 ',"state":"CLOSED"')"; ck pending 11 750; run
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert "750" not in d.get("_causes",{}),d' "$D/ci-watch.state.json" && ok "닫힌 PR 원인 제거" || ko "닫힌 PR"

# 래퍼 검사만 같으면 공통 원인 아님(요약 줄이 모든 PR 에 같음)
CKN="Run Unity validation / Unity validation"; fresh; log "not ok 9 - shared"; wt feat/mine; prs "$(pj 722 feat/mine 4141414141414141),$(pj 723 feat/o 5151515151515151)"
cycle 722 723
! grep -q "테스트·QA" "$D/orca.log" && grep -q "담당 허브 UI 계층" "$D/orca.log" && ok "래퍼 검사만 같음 → 공통 원인 아님" || ko "래퍼 검사" "$(cat "$D/orca.log")"
# 같은 워크플로의 앞 잡이 취소되면 요약 잡 실패는 취소 여파라 알리지 않음
fresh; log "not ok 9 - x"; prs "$(pj 724 feat/mine 6161616161616161)"
cc() { printf '[{"name":"Unity Tests / EditMode","workflow":"PR Test Pipeline","bucket":"%s","link":""},{"name":"Unity Tests / Summary","workflow":"PR Test Pipeline","bucket":"%s","link":"https://x/actions/runs/11/job/22"}]' "$1" "$2" > "$D/checks-724.json"; }
cc pending pending; run; rm -f "$D/sent.log"; cc cancel fail; run
[ "$(sent)" = 0 ] && ok "같은 워크플로 취소 여파 실패 → 알림 없음" || ko "취소 여파" "$(cat "$D/sent.log")"
# run 스크립트 원문(ECHO)·통과 TAP 줄은 원인에서 제외
fresh; printf 'x\t2026-11-11T05:00:01.1Z \033[36;1mecho error here\033[0m\nx\t2026-11-11T05:00:02.1Z ok 3 - 설정 없음 처리\nx\t2026-11-11T05:00:03.1Z not ok 7 - real\n' > "$D/log.txt"
python3 -c '
import sys;sys.path.insert(0,sys.argv[1]);import ci_watch as w
c,_=w.cause("https://x/actions/runs/11/job/22","Build")
assert c.endswith("not ok 7 - real"),c
' "$S" && ok "스크립트 원문·통과 시험 줄은 원인 제외" || ko "ECHO 제외"
# ---- 공통 원인은 시험 이름이 아니라 실패 메시지(정규화 전체) 기준 ----
python3 -c '
import sys;sys.path.insert(0,sys.argv[1]);import ci_review as r
def e(*fs): return {"head":"h","fails":[{"key":"U","cause":c,"msg":m} for c,m in fs],"hub":""}
c={"719":e(("StageClearTest: 스테이지 main.ch1.2 통과 판정 로그 없음","스테이지 main.ch1.2 통과 판정 로그 없음")),
   "727":e(("StageClearTest: 1일차 가이드 입력 대상 미도달","1일차 가이드 입력 대상 미도달")),
   "730":e(("StageClearTest: 스테이지 main.ch1.3 통과 판정 로그 없음","스테이지 main.ch1.3 통과 판정 로그 없음")),
   "732":e(("StageClearTest: 스테이지 main.ch1.2 통과 판정 로그 없음","스테이지 main.ch1.2 통과 판정 로그 없음")),
   "731":e(("T: 종료","Process completed with exit code 1.")),
   "740":e(("T: stage-clear event absent","stage-clear event absent "+"x"*80+" 같은 앞부분")),
   "741":e(("T: guide input target absent","guide input target absent "+"x"*80+" 같은 앞부분")),
   "742":e(("T: z","공통 앞부분 "+"y"*70+" 끝 A")),
   "743":e(("T: z","공통 앞부분 "+"y"*70+" 끝 B"))}
assert r.common_cause(c,"719")==["732"], "같은 메시지만 공통: ch1.3(730)·이름만 같은 727 은 제외"
assert r.common_cause(c,"730")==[] and r.common_cause(c,"727")==[] and r.common_cause(c,"731")==[], "스테이지 번호 보존·이름만 같음·종료 코드 요약"
assert r.common_cause(c,"740")==[] and r.common_cause(c,"742")==[], "앞부분이 길게 같아도 전체가 다르면 공통 아님(자르기 없음)"
s=r.signature
assert s("실패 /Users/a/b/Foo.cs:12 at 2026-11-12T05:00:01.1Z abcdef1234567 PR #719 took 3.5s x 3")==s("실패 /w/z/Foo.cs:99 at 2026-11-13T07:01:02Z 7654321fedcba PR #800 took 120 ms x 3"), "가변 필드(경로·시각·해시·PR 번호·소요 시간)만 정규화"
assert s("stage main.ch1.2 기댓값 2")!=s("stage main.ch1.3 기댓값 2") and s("기댓값 2")!=s("기댓값 3"), "스테이지·기댓값 숫자 보존"
# 신구 혼합: 구형(msg 없음, 시험명: 메시지) ↔ 신형(msg)
old={"head":"h","fails":[{"key":"U","cause":"StageClearTest: 스테이지 main.ch1.2 통과 판정 로그 없음"}],"hub":""}
new={"head":"h","fails":[{"key":"U","cause":"StageClearTest: 스테이지 main.ch1.2 통과 판정 로그 없음","msg":"스테이지 main.ch1.2 통과 판정 로그 없음"}],"hub":""}
oth={"head":"h","fails":[{"key":"U","cause":"StageClearTest: 1일차 가이드 입력 대상 미도달","msg":"1일차 가이드 입력 대상 미도달"}],"hub":""}
m={"1":old,"2":new,"3":oth}
assert r.common_cause(m,"1")==["2"] and r.common_cause(m,"2")==["1"] and r.common_cause(m,"3")==[], "혼합 상태 비교(구형은 시험명을 떼고 비교)"
' "$S" && ok "공통 원인: 메시지 정규화 전체 기준(번호 보존·자르기 없음·신구 혼합 상태)" || ko "공통 원인 정규화"
fresh; log "not ok 1 - same"; prs "$(pj 780 feat/a 1a1a1a1a1a1a1a1a),$(pj 781 feat/b 2b2b2b2b2b2b2b2b)"
cycle 780 781
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]))["_causes"];assert all(f.get("msg") for e in d.values() for f in e["fails"]),d' "$D/ci-watch.state.json" && ok "상태 파일 원인에 메시지 값 저장" || ko "msg 저장"

# ---- 소유 허브 2차: PR 제목 티켓 → 담당 허브(허브 상태 파일·접수 경로) ----
tp() { echo "[{\"number\":$1,\"title\":\"$2\",\"headRefName\":\"sprint-x/bundle-automa\",\"headRefOid\":\"$3\"}]" > "$D/prs.json"; }
mkdir -p "$D/hub-status"; NOW=$(python3 -c 'from datetime import datetime;print(datetime.now().astimezone().isoformat(timespec="seconds"))')
echo '{"slug":"ui-layer","updatedAt":"'$NOW'","tasks":["오토마 LRM-458 구현","LRM-461 보강"],"branches":["feat/LRM-458-a"]}' > "$D/hub-status/ui-layer.json"
echo '{"slug":"vfx","updatedAt":"'$NOW'","tasks":["LRM-397 연출"],"branches":[]}' > "$D/hub-status/vfx.json"
fresh; log "not ok 3 - eye"; tp 717 "[LRM-458][LRM-461] feat: 오토마" 3c3c3c3c3c3c3c3c; wt feat/mine; cycle 717
grep -q "담당 허브 UI 계층 통합 설계 (근거: 제목 티켓 LRM-458 → UI 계층 통합 설계)" "$D/orca.log" && ok "묶음 브랜치 + 제목 티켓 → 담당 허브(근거 줄에 티켓·허브)" || ko "제목 티켓" "$(cat "$D/orca.log")"
fresh; log "not ok 3 - eye"; tp 718 "[LRM-458][LRM-397] feat: 섞임" 4d4d4d4d4d4d4d4d; wt feat/mine; cycle 718
grep -q "담당 허브 미배정(메인 판단) (근거: 제목 티켓이 여러 허브로 갈림" "$D/orca.log" && ! grep -q "VFX 허브(vfx)" "$D/orca.log" && ok "티켓이 여러 허브로 갈림 → 미배정" || ko "티켓 여러 허브" "$(cat "$D/orca.log")"
# 낡은·시각 불명 상태 파일은 근거 제외
echo '{"slug":"ui-layer","updatedAt":"2020-01-01T00:00:00+09:00","tasks":["LRM-458"]}' > "$D/hub-status/ui-layer.json"
fresh; log "not ok 3 - eye"; tp 722 "[LRM-458] x" 8b8b8b8b8b8b8b8b; wt feat/mine; cycle 722
grep -q "담당 허브 미배정(메인 판단)" "$D/orca.log" && ! grep -q "제목 티켓 LRM-458" "$D/orca.log" && ok "24시간 넘은 상태 파일 티켓은 근거 제외" || ko "낡은 상태" "$(cat "$D/orca.log")"
echo '{"slug":"ui-layer","tasks":["LRM-458"]}' > "$D/hub-status/ui-layer.json"
fresh; log "not ok 3 - eye"; tp 723 "[LRM-458] x" 9c9c9c9c9c9c9c9c; wt feat/mine; cycle 723
grep -q "담당 허브 미배정(메인 판단)" "$D/orca.log" && ok "updatedAt 없는 상태 파일은 근거 제외" || ko "시각 불명" "$(cat "$D/orca.log")"
echo '{"slug":"ui-layer","updatedAt":"'$NOW'","tasks":["LRM-458"]}' > "$D/hub-status/ui-layer.json"
LRM_HUB_STATUS_STALE_HOURS=0.0000001 python3 - "$S" "$D" <<'PY'
import sys,time;sys.path.insert(0,sys.argv[1]);import ci_review as r
time.sleep(0.01)
assert r.ticket_hubs(sys.argv[2]+"/hub-status",sys.argv[2]+"/none.jsonl",sys.argv[2]+"/hubs.tsv")=={}, "stale_hours 환경값 반영"
PY
[ $? = 0 ] && ok "낡음 기준은 hub_status 의 stale_hours(환경값 포함)" || ko "stale_hours"
# 다른 저장소 허브(hubs.tsv 저장소 칸)의 같은 티켓 번호는 제외
echo '{"slug":"config","updatedAt":"'$NOW'","tasks":["LRM-458 설정"]}' > "$D/hub-status/config.json"; echo '{"slug":"ui-layer","updatedAt":"'$NOW'","tasks":["LRM-500"]}' > "$D/hub-status/ui-layer.json"
fresh; log "not ok 3 - eye"; tp 724 "[LRM-458] x" adadadadadadadad; wt feat/mine; cycle 724
grep -q "담당 허브 미배정(메인 판단)" "$D/orca.log" && ! grep -q "개인 설정" "$D/orca.log" && ok "다른 저장소 허브의 같은 티켓 번호는 배정 제외" || ko "저장소 검증" "$(cat "$D/orca.log")"
echo '{"id":"C2","hub":"config","text":"LRM-459 설정 접수"}' > "$D/intake-routes.jsonl"
fresh; log "not ok 3 - eye"; tp 725 "[LRM-459] x" aeaeaeaeaeaeaeae; wt feat/mine; cycle 725
grep -q "담당 허브 미배정(메인 판단)" "$D/orca.log" && ok "접수 경로도 허브 저장소 소속 검증" || ko "접수 경로 저장소" "$(cat "$D/orca.log")"
rm -f "$D/intake-routes.jsonl"
rm -rf "$D/hub-status"; echo '{"id":"C1","hub":"ui","text":"LRM-470 팝업 접수"}' > "$D/intake-routes.jsonl"
fresh; log "not ok 3 - eye"; tp 719 "[LRM-470] fix: 팝업" 5e5e5e5e5e5e5e5e; wt feat/mine; cycle 719
grep -q "근거: 제목 티켓 LRM-470 → UI 계층 통합 설계" "$D/orca.log" && ok "접수 경로(별칭 ui → ui-layer)도 티켓 근거" || ko "접수 경로" "$(cat "$D/orca.log")"
fresh; log "not ok 3 - eye"; tp 720 "[LRM-9999] x" 6f6f6f6f6f6f6f6f; wt feat/mine; cycle 720
grep -q "담당 허브 미배정(메인 판단)" "$D/orca.log" && ok "근거 없는 티켓 → 미배정" || ko "근거 없음" "$(cat "$D/orca.log")"
rm -f "$D/intake-routes.jsonl"
fresh; log "not ok 3 - eye"; prs "$(pj 721 feat/mine 7a7a7a7a7a7a7a7a)"; wt feat/mine; cycle 721
grep -q "근거: PR 브랜치의 작업 공간 허브" "$D/orca.log" && ok "작업 공간 허브가 티켓보다 우선" || ko "우선순위" "$(cat "$D/orca.log")"

# ---- Codex 재확인 4건 ----
python3 -c '
import sys;sys.path.insert(0,sys.argv[1]);import ci_review as r
L=lambda c:{"head":"h","fails":[{"key":"U","cause":c}],"hub":""}
# 1) 판별 불명 구형 값: 콜론 뒤 공통부로 합치지 않음. xml 시험명 꼴 접두만 뗌
m={"1":L("Shader error in Foo: undeclared identifier"),"2":L("Shader error in Bar: undeclared identifier"),"3":L("Error: boom"),"4":L("Failed: boom"),
   "5":L("StageTest: boom"),"6":L("OtherTest: boom"),"7":L("Shader error in Foo: undeclared identifier")}
assert r.common_cause(m,"1")==["7"] and r.common_cause(m,"2")==[], "Foo/Bar 는 다른 원인, 같은 전체 문자열만 공통"
assert r.common_cause(m,"3")==[] and r.common_cause(m,"4")==[], "로그 머리말(Error/Failed)은 시험명 아님"
assert r.common_cause(m,"5")==["6"], "시험명 꼴 접두는 뗌"
# 3) 짧은 e 표기 숫자는 해시 아님, 해시 필드·GUID·긴 해시는 치환
s=r.signature
assert s("expected 1e00000")!=s("expected 2e00000"), "1e00000/2e00000 구분"
assert s("commit abcdef1234567 x")==s("commit 7654321fedcba x") and s("sha: 1e00000 x")==s("sha: 2e00000 x"), "해시 필드 값 치환"
assert s("guid 0f8fad5b-d9cb-469f-a165-70867728950e")==s("guid 1a8fad5b-d9cb-469f-a165-70867728950f") and s("f "+"a1"*8)==s("f "+"b2"*8), "GUID·긴 해시 치환"
# 4) 저장소는 owner/name 전체, 경로 basename 만 알면 미확인
import json,os,tempfile,datetime
d=tempfile.mkdtemp();os.makedirs(d+"/hs")
now=datetime.datetime.now().astimezone().isoformat(timespec="seconds")
def st(slug): open(d+"/hs/%s.json"%slug,"w").write(json.dumps({"slug":slug,"updatedAt":now,"tasks":["LRM-1"]}))
open(d+"/hubs.tsv","w").write("ui-layer\tUI\t~/r/role-ui.txt\t-\t-\nown\tOWN\t~/r/role-own.txt\t-\tAcme/Game\ncfg\tCFG\t~/r/role-cfg.txt\t~/w\t~/orca/claude-config\n")
for x in ("ui-layer","own","cfg"): st(x)
t=lambda repo,home:r.ticket_hubs(d+"/hs",d+"/n.jsonl",d+"/hubs.tsv",repo,home).get("1",set())
assert t("Basakansoft/Game","Basakansoft/Game")=={"UI"}, "기본 게임 저장소 허브만"
assert t("Other/Game","Basakansoft/Game")==set(), "owner 가 다르면 같은 이름이어도 제외"
assert t("Acme/Game","Basakansoft/Game")=={"OWN"}, "owner/name 명시 허브"
assert t("Basakansoft/claude-config","Basakansoft/LoveRevolutionMaker")==set(), "감시 대상 ≠ 기본 게임 저장소면 게임 허브 제외·경로만 있는 허브는 미확인"
assert t("Basakansoft/Game","")==set(), "기본 게임 저장소를 모르면 \x27-\x27 허브 제외"
' "$S" && ok "재확인: 구형 cause 보수 비교·해시 문맥·저장소 owner 검증" || ko "재확인 단위"
mkdir -p "$D/hub-status"; NOW=$(python3 -c 'from datetime import datetime;print(datetime.now().astimezone().isoformat(timespec="seconds"))')
echo '{"slug":"ui-layer","updatedAt":"'$NOW'","tasks":["LRM-458"]}' > "$D/hub-status/ui-layer.json"
fresh; log "not ok 3 - eye"; tp 726 "[LRM-458] x" b1b1b1b1b1b1b1b1; wt feat/mine; LRM_CI_REPO=Basakansoft/claude-config cycle 726
grep -q "담당 허브 미배정(메인 판단)" "$D/orca.log" && ok "감시 대상이 claude-config 여도 게임 허브(ui-layer)로 배정 안 함" || ko "감시 대상 분리" "$(cat "$D/orca.log")"
rm -rf "$D/hub-status"

# ---- 작업 공간 이름 → 허브 (2차 근거) ----
python3 -c '
import sys,os,tempfile;sys.path.insert(0,sys.argv[1]);import ci_review as r
d=tempfile.mkdtemp();os.makedirs(d+"/hs")
open(d+"/hubs.tsv","w").write("ui-layer\tUI\t~/r/role-ui.txt\t-\t-\nvfx\tVFX\t~/r/role-vfx.txt\t-\t-\n")
G="Basakansoft/Game";PJ="github:basakansoft/game"
def w(i,name,hub,pj=PJ,br="refs/heads/x"): return {"id":"r::/w/"+i,"path":"/w/"+i,"displayName":name,"branch":br,"projectId":pj,"parentWorktreeId":"r::/w/coord-%s-hub"%hub}
def t(wts,n="458"): return r.ticket_hubs(d+"/hs",d+"/n.jsonl",d+"/hubs.tsv",G,G,wts).get(n,set())
assert t([w("a","[LRM-458] 오토마 데이터 설계","ui-layer"),w("b","[LRM-461] x","ui-layer")])=={"UI"}, "UI 단독 → UI"
assert t([w("a","[LRM-458] x","ui-layer"),w("b","y","vfx",br="refs/heads/feat/LRM-458-z")])=={"UI","VFX"}, "두 허브 갈림"
assert t([w("a","[LRM-458] x","ui-layer",pj="github:other/repo")])==set(), "다른 저장소 제외"
assert t(None)==set() and t([])==set(), "조회 실패·빈 목록은 근거 없음"
assert t([w("a","[LRM-458] x","unknown")])==set(), "허브 아닌 부모 제외"
wsm=r.workspace_tickets([w("a","[LRM-458] x","ui-layer")],{"ui-layer":"UI"},G)
h,why=r.assign_hub("bundle-automa-data","[LRM-458][LRM-461] t",[],[],{"ui-layer":"UI"},tmap={"458":{"UI"},"461":{"UI"}},ws=wsm)
assert h=="UI" and "작업 공간 [LRM-458] → ui-layer" in why, why
' "$S" && ok "작업 공간 근거: UI 단독·갈림·다른 저장소·실패" || ko "작업 공간 근거 단위"
# 통합: 브랜치로는 못 찾아도 작업 공간 이름의 티켓으로 UI 허브 배정, 조회는 회차당 1회
fresh; prs "$(pj 730 bundle-x 4040404040404040)"; wt feat/other; sed -i '' 's#"branch":"refs/heads/feat/other"#"branch":"refs/heads/feat/other","displayName":"[LRM-1] 이름"#' "$D/worktrees.json"; cycle 730
grep -q "담당 허브 UI 계층 통합 설계 (근거: 제목 티켓 LRM-1 · 작업 공간 \[LRM-1\] → ui-layer)" "$D/orca.log" && ok "작업 공간 이름 티켓 → ui-layer 근거 문구" || ko "작업 공간 통합" "$(cat "$D/orca.log")"
fresh; prs "$(pj 731 b1 5050505050505050),$(pj 732 b2 6060606060606060)"; wt feat/other; cycle 731 732
[ "$(grep -c '^worktree list' "$D/orca.log")" = 1 ] && ok "작업 공간 조회 회차당 1회(PR 2개 → 1회)" || ko "조회 횟수" "$(grep -c '^worktree list' "$D/orca.log")"

echo "통과 $PASS / 실패 $FAIL"; [ "$FAIL" = 0 ]
