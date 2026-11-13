#!/usr/bin/env bash
# scrum_post.py·scrum_collect.py 시험. 가짜 claude·알림·osascript 와 임시 폴더만 씀(네트워크·Slack·Jira 없음)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P); S="$HERE/../../hubs/scripts"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); echo "PASS  $1"; }
ko() { FAIL=$((FAIL + 1)); echo "FAIL  $1 — ${2:-}"; }
D=$(mktemp -d "${TMPDIR:-/tmp}/cc-scrum.XXXXXX"); trap 'rm -rf "${D:?}"' EXIT
export LRM_SCRUM_NAME="시험이름" FAKE_NAME="시험이름" FAKE_DIR="$D" LRM_HUB_DIR="$D" LRM_SCRUM_DRAFT_DIR="$D/draft" LRM_SCRUM_LOG="$D/scrum.log" \
  LRM_CLAUDE="$HERE/fake_claude.sh" LRM_NOTIFY="$HERE/fake_send.sh" LRM_SCRUM_OSA="$HERE/fake_osa.sh" \
  LRM_SCRUM_COLLECT="$HERE/fake_collect.py" LRM_SCRUM_NET_WAIT=0 LRM_SCRUM_RETRY_WAITS="0,0"
chmod +x "$HERE"/fake_*.sh
post() { python3 "$S/scrum_post.py" "$@" > "$D/out" 2> "$D/err"; }
sent() { [ -f "$D/sent.log" ] && grep -c '^===$' "$D/sent.log" || echo 0; }
sent_has() { python3 -c 'import json,sys;sys.exit(0 if sys.argv[2] in json.load(open(sys.argv[1]))["sent"] else 1)' "$D/scrum.state.json" "$1"; }
pending_has() { python3 -c 'import json,sys;sys.exit(0 if sys.argv[2] in json.load(open(sys.argv[1]))["pending"] else 1)' "$D/scrum.state.json" "$1"; }
reset() { rm -f "$D"/sent.log "$D"/claude.log "$D"/claude.args "$D"/osa.log "$D"/send-* "$D"/slack-sent.jsonl "$D"/plan-* "$D"/scrum.state.json "$D"/scrum.lock; rm -rf "${D:?}/draft"; }
WK=2026-11-12   # 목요일

# ---- 날짜 잠금 ----
reset; post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(sent)" = 1 ] && grep -q -- "--kind doc" "$D/sent.log" && ok "평일 첫 실행 → doc 1건 발송" || ko "첫 발송" "rc=$rc $(sent)"
post --date $WK; [ "$(sent)" = 1 ] && grep -q "이미 보냄" "$D/err" && ok "같은 날 두 번째 실행은 보내지 않음(날짜 잠금)" || ko "날짜 잠금" "$(sent)"
post --date $WK --force; [ "$(sent)" = 2 ] && ok "--force 는 잠금 무시" || ko "--force" "$(sent)"
post --date 2026-11-13; [ "$(sent)" = 3 ] && ok "다른 날짜는 따로 발송" || ko "다른 날" "$(sent)"
# 잠금은 OS 파일 잠금(리뷰 지적 2): 살아 있는 보유자는 파일이 오래돼도 지켜지고, 죽은 실행의 잔재 파일은 막지 않음
holder() { exec python3 -c 'import fcntl,os,sys,time; fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR); fcntl.flock(fd,fcntl.LOCK_EX); print("held",flush=True); time.sleep(30)' "$D/scrum.lock"; }
reset; holder > "$D/holder.out" & H_PID=$!
for _ in $(seq 50); do grep -q held "$D/holder.out" 2>/dev/null && break; sleep 0.1; done
touch -t 202001010000 "$D/scrum.lock"   # 30분 훨씬 넘게 오래된 잠금 파일
post --date $WK; [ "$(sent)" = 0 ] && [ ! -e "$D/claude.log" ] && grep -q "진행 중" "$D/err" && ok "살아 있는 실행이 잠금을 쥐면 mtime 이 오래돼도 건너뜀" || ko "오래된 잠금 오삭제" "$(sent)"
kill "$H_PID" 2>/dev/null; wait "$H_PID" 2>/dev/null; sleep 0.2
post --date $WK; [ "$(sent)" = 1 ] && ok "보유 프로세스가 죽으면 잠금이 풀려 실행(잔재 파일은 무시)" || ko "죽은 잠금" "$(sent)"
reset; echo x > "$D/scrum.lock"; post --date $WK; [ "$(sent)" = 1 ] && ok "내용만 있고 아무도 안 쥔 잠금 파일은 막지 않음" || ko "잔재 파일" "$(sent)"
python3 - "$S" "$D" <<'PY' && ok "같은 프로세스의 두 번째 take_lock 은 거부(flock)" || ko "중복 take_lock"
import os, sys
sys.path.insert(0, sys.argv[1]); os.environ["LRM_HUB_DIR"] = sys.argv[2]
import scrum_post as p
a = p.take_lock(); b = p.take_lock()
assert a is not None and b is None, (a, b)
p.release_lock(a); c = p.take_lock(); assert c is not None
PY

# ---- 주말·공휴일 건너뜀 ----
reset; post --date 2026-11-14; rc=$?
[ $rc = 0 ] && [ "$(sent)" = 0 ] && [ ! -e "$D/claude.log" ] && grep -q "주말" "$D/err" && ok "토요일은 claude 도 부르지 않고 건너뜀" || ko "토요일" "rc=$rc"
post --date 2026-11-15; [ "$(sent)" = 0 ] && ok "일요일 건너뜀" || ko "일요일"
printf '# 시험 공휴일\n2026-11-12\n' > "$D/scrum-holidays.txt"; post --date $WK; [ "$(sent)" = 0 ] && ok "공휴일 목록의 날은 건너뜀" || ko "공휴일"
rm -f "$D/scrum-holidays.txt"
python3 -c '
import sys, datetime as dt; sys.path.insert(0, sys.argv[1]); import scrum_collect as c
assert c.prev_workday(dt.date(2026, 11, 16)) == dt.date(2026, 11, 13), "월요일 → 금요일"
assert c.prev_workday(dt.date(2026, 11, 12)) == dt.date(2026, 11, 11), "목요일 → 수요일"
' "$S" && ok "직전 평일: 월요일은 금요일부터" || ko "직전 평일"

# ---- 모델 대체 순서 ----
reset; echo fail > "$D/plan-claude-fable-5-1"; post --date $WK
[ "$(tr '\n' ' ' < "$D/claude.log")" = "claude-fable-5-1 claude-fable-5-2 " ] && grep -q "모델 claude-fable-5-2" "$D/sent.log" && ok "fable-5-1 실패 → fable-5-2 채택, 참고 줄에 모델 기록" || ko "1→2" "$(cat "$D/claude.log")"
reset; echo fail > "$D/plan-claude-fable-5-1"; echo bad > "$D/plan-claude-fable-5-2"; post --date $WK
[ "$(tr '\n' ' ' < "$D/claude.log")" = "claude-fable-5-1 claude-fable-5-2 claude-opus-5-5 " ] && grep -q "모델 claude-opus-5-5" "$D/sent.log" && ok "양식 불량도 실패로 쳐 opus 까지 내려감" || ko "1→2→3" "$(cat "$D/claude.log")"
reset; post --date $WK; [ "$(cat "$D/claude.log")" = "claude-fable-5-1" ] && ok "첫 모델이 되면 다음 모델은 부르지 않음" || ko "첫 모델" "$(cat "$D/claude.log")"
reset; echo empty > "$D/plan-m-a"; LRM_SCRUM_MODELS="m-a,m-b" post --date $WK; [ "$(tr '\n' ' ' < "$D/claude.log")" = "m-a m-b " ] && ok "모델 순서는 LRM_SCRUM_MODELS 로 바꿈" || ko "모델 목록" "$(cat "$D/claude.log")"
reset; echo extra > "$D/plan-claude-fable-5-1"; post --date $WK
f=$(ls "$D"/draft/*.md | head -1); ! grep -q "타인" "$f" && ! grep -q "머리말" "$f" && head -1 "$f" | grep -q "^# $LRM_SCRUM_NAME\$" && ok "머리말·다른 사람 칸은 잘라냄" || ko "칸 분리" "$(cat "$f")"

reset; echo noatl > "$D/plan-claude-fable-5-1"; post --date $WK; f=$(ls "$D"/draft/*.md | head -1)
grep -q "경고: Jira·Confluence 를 못 읽어" "$D/sent.log" && ! grep -q "atlassian-unavailable\|설명 문장" "$f" && ok "Atlassian 못 읽음 → Slack 경고 1줄, 표시·끝 설명 문장은 초안에서 제거" || ko "Atlassian 경고" "$(cat "$f")"
reset; post --date $WK; ! grep -q "Jira·Confluence" "$D/sent.log" && ok "Atlassian 읽었으면 경고 없음" || ko "Atlassian 정상"

# ---- 초안 claude 도구 제한(리뷰 지적 1) ----
reset; post --date $WK
args=$(sed '/^---$/,$d' "$D/claude.args"); opt() { printf '%s\n' "$args" | grep -A1 -x -- "$1" | tail -1; }
[ "$(opt --tools)" = "<빈값>" ] && ok "claude -p 에 --tools \"\" 로 내장 도구 전부 끔" || ko "--tools" "$(opt --tools)"
[ "$(opt --permission-mode)" = dontAsk ] && ok "--permission-mode dontAsk 로 목록 밖 호출 거부" || ko "dontAsk" "$(opt --permission-mode)"
allowed=$(opt --allowedTools)
[ -n "$allowed" ] && ! printf '%s' "$allowed" | tr ',' '\n' | grep -qv '^mcp__atlassian__\(get\|search\|atlassianUserInfo\)' && ok "--allowedTools 는 Atlassian 읽기 도구만" || ko "allowedTools" "$allowed"
denied=$(opt --disallowedTools)
for t in Bash Read Write Edit WebFetch mcp__atlassian__createJiraIssue mcp__atlassian__editJiraIssue mcp__atlassian__addOrEditJiraIssueComment mcp__atlassian__updateConfluenceContent mcp__atlassian__executeWrite; do
  printf '%s' "$denied" | tr ',' '\n' | grep -qx "$t" || ko "--disallowedTools" "$t 누락"
done; ok "--disallowedTools 에 Bash·파일·웹·Atlassian 쓰기 도구 명시"

# ---- 사용자 설정 격리(재검토 지적 1) ----
[ "$(opt --setting-sources)" = "<빈값>" ] && ok "--setting-sources \"\" 로 사용자·프로젝트 settings 배제" || ko "--setting-sources" "$(opt --setting-sources)"
printf '%s\n' "$args" | grep -qx -- --strict-mcp-config && ok "--strict-mcp-config 로 기존 MCP 서버 배제" || ko "--strict-mcp-config"
python3 -c 'import json,sys; c=json.loads(sys.argv[1]); assert list(c["mcpServers"])==["atlassian"]' "$(opt --mcp-config)" && ok "--mcp-config 는 스크럼 전용 atlassian 하나뿐" || ko "--mcp-config" "$(opt --mcp-config)"
if command -v claude > /dev/null 2>&1; then
  # 재검토와 같은 형태: 진짜 claude + localhost 가짜 API + 가짜 MCP(writeProbe) + 임시 HOME·설정 폴더에 writeProbe 허용
  I="$D/iso"; mkdir -p "$I/home" "$I/cfg"; echo '{"permissions":{"allow":["mcp__review__writeProbe"]}}' > "$I/cfg/settings.json"
  python3 -c 'import json,sys; print(json.dumps({"mcpServers":{"review":{"command":"python3","args":[sys.argv[1],sys.argv[2]]}}}))' "$HERE/fake_mcp.py" "$I/called.txt" > "$I/mcp.json"
  python3 "$HERE/fake_api.py" "$I/port" mcp__review__writeProbe & API_PID=$!
  for _ in $(seq 50); do [ -s "$I/port" ] && break; sleep 0.1; done
  iso() { rm -f "$I/called.txt"; (cd "$I" && env -i HOME="$I/home" PATH="$PATH" CLAUDE_CONFIG_DIR="$I/cfg" ANTHROPIC_BASE_URL="http://127.0.0.1:$(cat "$I/port")" ANTHROPIC_API_KEY=sk-fake \
    LRM_HUB_DIR="$D" LRM_SCRUM_DRAFT_DIR="$D/draft" LRM_SCRUM_LOG="$D/scrum.log" LRM_SCRUM_NAME=x LRM_SCRUM_MCP_CONFIG="$I/mcp.json" LRM_SCRUM_MODELS=test python3 -I -c '
import sys; sys.path.insert(0, sys.argv[1]); import scrum_post as p
print(p.ask_claude("test", "hi")[1] or "ok")' "$S" > /dev/null 2>&1); }
  iso; [ ! -e "$I/called.txt" ] && ok "사용자 settings 가 writeProbe 를 허용해도 ask_claude 는 호출 못 함(called.txt 없음)" || ko "writeProbe 호출됨"
  # 대조군: 격리 플래그가 없는 명령이면 같은 환경에서 호출됨(시험이 실제로 위험을 재현하는지)
  rm -f "$I/called.txt"; (cd "$I" && echo hi | env -i HOME="$I/home" PATH="$PATH" CLAUDE_CONFIG_DIR="$I/cfg" ANTHROPIC_BASE_URL="http://127.0.0.1:$(cat "$I/port")" ANTHROPIC_API_KEY=sk-fake \
    claude -p --model test --tools "" --permission-mode dontAsk --allowedTools mcp__atlassian__getJiraIssue --mcp-config "$I/mcp.json" > /dev/null 2>&1)
  [ -e "$I/called.txt" ] && ok "대조: 격리 없는 명령은 같은 환경에서 writeProbe 가 실행됨(시험이 위험을 재현)" || ko "대조군 미재현"
  kill "$API_PID" 2>/dev/null; wait "$API_PID" 2>/dev/null
else
  echo "SKIP  claude 없음 — 격리 회귀 시험 생략"
fi

# ---- 워크트리별 git log 실패(재검토 지적 2) ----
W="$D/ws"; mkdir -p "$W/ok1/.git" "$W/ok2/.git" "$W/bad1/.git" "$W/bad2/.git"
cat > "$D/fake_git.sh" <<'G'
#!/bin/bash
[ "$1" = config ] && { echo me; exit 0; }
wt="$2"; shift 2
case "$1:$(basename "$wt")" in
  log:bad*) echo "fatal: bad object" >&2; exit 128 ;;
  log:*) printf 'h%s\t2026-11-12T11:00:00+09:00\tsubj\n' "$(basename "$wt")" ;;
  rev-parse:*) echo br ;;
esac
G
printf '#!/bin/bash\necho "[]"\n' > "$D/fake_gh.sh"; chmod +x "$D/fake_git.sh" "$D/fake_gh.sh"
coll() { LRM_GIT="$D/fake_git.sh" LRM_GH="$D/fake_gh.sh" LRM_SCRUM_WS_ROOT="$W" python3 "$S/scrum_collect.py" --date $WK; }
coll > "$D/coll.json"; python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); e=d["errors"]
assert len(d["commits"])==2 and len(e)==1 and e[0].startswith("commits_partial:") and "bad1" in e[0] and "bad2" in e[0] and "2/4" in e[0], d' "$D/coll.json" && ok "일부 워크트리 git log 실패 → 나머지 커밋 수집 + errors 에 commits_partial(실패 폴더 명시)" || ko "부분 실패" "$(cat "$D/coll.json")"
rm -rf "$W/ok1" "$W/ok2"; coll > "$D/coll.json"; python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); e=d["errors"]
assert d["commits"]==[] and len(e)==1 and e[0].startswith("commits:") and "2개 전부" in e[0], d' "$D/coll.json" && ok "전부 실패 → errors 에 commits(필수 출처 누락)로 구분" || ko "전체 실패" "$(cat "$D/coll.json")"
mkdir -p "$W/ok1/.git"; rm -rf "$W/bad1" "$W/bad2"; coll > "$D/coll.json"; python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["errors"]==[] and len(d["commits"])==1, d' "$D/coll.json" && ok "전부 성공하면 errors 비어 있음" || ko "정상 수집"
mkdir -p "$W/ok2/.git" "$W/bad1/.git"
reset; LRM_SCRUM_COLLECT="$S/scrum_collect.py" LRM_GIT="$D/fake_git.sh" LRM_GH="$D/fake_gh.sh" LRM_SCRUM_WS_ROOT="$W" post --date $WK; rc=$?
[ $rc = 0 ] && grep -q -- "--kind doc" "$D/sent.log" && grep -q "경고: 재료 수집 누락(commits 일부 워크트리)" "$D/sent.log" && ! grep -q "bad object" "$D/sent.log" && ok "일부 워크트리 실패 → 초안은 발송하되 본문에 누락 경고(오류 원문은 안 나감)" || ko "발송 경고" "rc=$rc $(cat "$D/sent.log")"
rm -rf "$W/ok1" "$W/ok2"; mkdir -p "$W/bad2/.git"
reset; LRM_SCRUM_COLLECT="$S/scrum_collect.py" LRM_GIT="$D/fake_git.sh" LRM_GH="$D/fake_gh.sh" LRM_SCRUM_WS_ROOT="$W" post --date $WK; rc=$?
[ $rc = 1 ] && grep -q -- "--kind warn" "$D/sent.log" && grep -q "collect_all_empty(commits)" "$D/sent.log" && ok "전부 실패(빈 날과 구분) → 초안 없이 warn" || ko "전체 실패 warn" "rc=$rc"

# ---- 허브별 진행 상세(가짜 gh·상태 파일) ----
H="$D/hubdir"; HW="$D/hubws"; mkdir -p "$H/hub-status" "$HW/coord-ui-hub" "$HW/coord-qa-hub"
cat > "$H/hub-status/ui.json" <<'J'
{"slug":"ui","updatedAt":"2026-11-13T10:00:00+09:00","tasks":["오토마 4개 push, CI 대기"],"branches":["feat/a"],"next":"CI 확인","blockers":["#731 병합 대기"]}
J
echo '{"slug":' > "$H/hub-status/broken.json"
cat > "$H/progress.json" <<'J'
{"items":[{"bucket":"wait","group":"ui","title":"ui: 서버 답 대기"},{"bucket":"wait","group":"장부","title":"F1 내부"},{"bucket":"doing","group":"ui","title":"ui: 진행"}]}
J
printf '시각\t무엇\t왜\t근거\t결과\t되돌리기\n2026-11-01 10:00:00\t옛 결정\t왜\t-\t결과\t-\n2026-11-13 09:00:00\t새 결정\t이유\t-\t적용\t-\n' > "$HW/coord-ui-hub/decisions.tsv"
cat > "$D/gh_prs.sh" <<'G'
#!/bin/bash
cat <<'J'
[{"number":7,"title":"A 기능","isDraft":false,"headRefName":"feat/a","baseRefName":"dev","url":"https://x/pull/7","reviewDecision":"",
  "statusCheckRollup":[{"__typename":"CheckRun","name":"unity","status":"COMPLETED","conclusion":"FAILURE"},{"__typename":"CheckRun","name":"lint","status":"COMPLETED","conclusion":"SUCCESS"},
  {"__typename":"CheckRun","name":"wake","status":"COMPLETED","conclusion":"SKIPPED"},{"__typename":"CheckRun","name":"slow","status":"IN_PROGRESS","conclusion":""}]},
 {"number":9,"title":"주인 없는 PR","isDraft":true,"headRefName":"feat/zzz","baseRefName":"dev","url":"https://x/pull/9","statusCheckRollup":[]}]
J
G
chmod +x "$D/gh_prs.sh"
hp() { LRM_HUB_DIR="$H" LRM_GH="$D/gh_prs.sh" LRM_GIT="$D/fake_git.sh" LRM_SCRUM_WS_ROOT="$HW" python3 "$S/scrum_collect.py" --date $WK --since '2026-11-12 10:00' > "$D/hp.json"; }
hp; python3 - "$D/hp.json" <<'PY' && ok "허브별 진행: 상태 파일·대기·브랜치가 맞는 PR 검사 요약·최근 결정만(깨진 파일·내부 묶음 제외)" || ko "허브 진행" "$(head -c 600 "$D/hp.json")"
import json, sys
d = json.load(open(sys.argv[1])); hp = d["hub_progress"]
assert not [e for e in d["errors"] if e.startswith("hub_progress")], d["errors"]
assert [h["hub"] for h in hp["hubs"]] == ["ui"], hp
h = hp["hubs"][0]
assert h["next"] == "CI 확인" and h["blockers"] == ["#731 병합 대기"] and h["waiting"] == ["서버 답 대기"], h
pr = h["prs"][0]; c = pr["checks"]
assert pr["number"] == 7 and c["fail"] == 1 and c["pass"] == 2 and c["pending"] == 1 and c["failed"] == ["unity"], pr
assert [p["number"] for p in hp["other_prs"]] == [9] and hp["other_prs"][0]["draft"] is True
assert [x["what"] for x in h["decisions"]] == ["새 결정"], h["decisions"]
PY
rm -rf "$H/hub-status" "$H/progress.json"; hp; python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["hub_progress"]["hubs"]==[] and len(d["hub_progress"]["other_prs"])==2' "$D/hp.json" && ok "상태 파일·progress.json 이 없어도 열린 PR 만으로 수집(실패 아님)" || ko "상태 파일 없음"
printf '#!/bin/bash\necho boom >&2; exit 1\n' > "$D/gh_bad.sh"; chmod +x "$D/gh_bad.sh"
LRM_HUB_DIR="$H" LRM_GH="$D/gh_bad.sh" LRM_GIT="$D/fake_git.sh" LRM_SCRUM_WS_ROOT="$HW" python3 "$S/scrum_collect.py" --date $WK > "$D/hp.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["hub_progress"]=={} or d["hub_progress"]==[]; assert any(e.startswith("hub_progress:") for e in d["errors"]), d["errors"]' "$D/hp.json" && ok "gh 실패 → hub_progress 오류 기록(다른 출처는 계속)" || ko "gh 실패" "$(head -c 400 "$D/hp.json")"

# ---- 이미지·영상 첨부(수집기) ----
M="$D/mediadir"; mkdir -p "$M/sub/deep/deeper/deepest" "$M/.ssh" "$H" "$M/old"
for n in new1.png new2.jpg sub/new3.mp4 .ssh/shot.png secret.env.png sub/note.txt sub/deep/deeper/deepest/too-deep.png; do echo img > "$M/$n"; done
echo img > "$M/old/old.png"; touch -t 202001010000 "$M/old/old.png"
truncate -s 51m "$M/huge.mp4"
echo img > "$M/dup-a.png"; mkdir -p "$M/copy"; echo img > "$M/copy/dup-a.png"   # 같은 이름·크기 복사본
echo vid > "$D/rep-orig.mp4"; mkdir -p "$D/stg"; echo vid > "$D/stg/rep-staged.mp4"
now=$(date +%s)
printf '{"at":"%s","hub":"x","files":["%s","/없는/경로/gone.mp4","%s"],"staged":{"/없는/경로/gone.mp4":"%s"}}\n' "$now" "$D/rep-orig.mp4" "$D/ok.html" "$D/stg/rep-staged.mp4" > "$H/hub-reports.jsonl"
printf '{"at":"1000","hub":"x","files":["%s"]}\n' "$D/rep-old.mp4" >> "$H/hub-reports.jsonl"; echo old > "$D/rep-old.mp4"
mc() { LRM_HUB_DIR="$H" LRM_SCRUM_MEDIA_DIR="$M" LRM_GH="$D/fake_gh.sh" LRM_GIT="$D/fake_git.sh" LRM_SCRUM_WS_ROOT="$HW" python3 "$S/scrum_collect.py" --date "$(date +%F)" > "$D/mc.json"; }
mc; python3 - "$D/mc.json" <<'PY' && ok "첨부 선택: 허브 보고(원본·복사본)+정리 기록, 최신 이틀·이미지 영상만, 비밀·초과·깊은 폴더·중복 제외, 상한 5" || ko "첨부 선택" "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["attachments"])' "$D/mc.json")"
import json, sys
a = json.load(open(sys.argv[1]))["attachments"]
names = [f["name"] for f in a["files"]]
assert sorted(names[:2]) == ["rep-orig.mp4", "rep-staged.mp4"], names   # 허브 보고가 먼저, 원본 없으면 복사본
assert len(names) == 5, names                                   # 상한
for bad in ("old.png", "shot.png", "secret.env.png", "huge.mp4", "note.txt", "too-deep.png", "rep-old.mp4"):
    assert bad not in names, bad
assert names.count("dup-a.png") <= 1
why = {s["why"] for s in a["skipped"]}
assert any("민감" in w for w in why) and any("크기 초과" in w for w in why), a["skipped"]
assert all("/" not in s["name"] for s in a["skipped"]), "경로가 skipped 에 담김"
PY
rm -rf "$M" "$H/hub-reports.jsonl"; mc; python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["attachments"]=={"files":[],"skipped":[]} and not d["errors"], d' "$D/mc.json" && ok "첨부 폴더·보고 기록이 없으면 빈 목록(오류 아님)" || ko "첨부 없음" "$(cat "$D/mc.json" | head -c 300)"

# ---- 이미지·영상 첨부(발송) ----
export LRM_SCRUM_MEDIA=on
reset; echo media > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(sent)" = 5 ] && [ "$(sed -n 1p "$D/sent.log")" != "" ] && ! sed -n 1p "$D/sent.log" | grep -q "2026-11-12.md" && grep -c -- "--thread 1700000000.000100" "$D/sent.log" | grep -qx 4 && grep -q "초안 전체" "$D/sent.log" && grep -q "첨부 2개" "$D/sent.log" && ok "요약 글(첨부 파일 없음) → 스레드 4건(초안·첨부 2·실패 경고 1)" || ko "스레드 순서" "rc=$rc $(sent)"
grep -q "첨부 일부 못 올림" "$D/sent.log" && grep -q -- "--kind warn" "$D/sent.log" && grep -q "첨부 2/2 실패" "$D/scrum.log" && sent_has 2026-11-12 && ok "첨부 하나 실패 → 스레드 경고 1건·로그, 초안은 보냄으로 확정" || ko "첨부 실패 처리"
reset; echo media-bad > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(sent)" = 3 ] && ! grep -q "\.env\.png\|big\.mp4" "$D/sent.log" && grep -q "a\.png" "$D/sent.log" && ok "발송 직전 재검사: 비밀 파일·50MB 초과는 후보에 있어도 올리지 않음" || ko "발송 직전 재검사" "rc=$rc $(sent)"
reset; post --date $WK; [ "$(sent)" = 1 ] && grep -q -- "--kind doc" "$D/sent.log" && grep -q "2026-11-12.md" "$D/sent.log" && ! grep -q "첨부 .*개" "$D/sent.log" && ok "첨부가 없으면 기존처럼 초안 파일 1건 발송(스레드 없음)" || ko "첨부 없음 발송"
reset; echo media > "$D/plan-collect"; echo 5 > "$D/send-fails"; post --date $WK; rc=$?
[ $rc = 1 ] && ! sent_has 2026-11-12 && ok "요약 글이 끝내 거절되면 실패(보냄 기록 없음)" || ko "요약 거절" "rc=$rc"
unset LRM_SCRUM_MEDIA
# 기본은 첨부 끔(11-13 사용자 요청): 후보가 있어도 안 올리고, 초안 작성 재료에서도 빠짐
reset; echo media > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(sent)" = 1 ] && grep -q "2026-11-12.md" "$D/sent.log" && ! grep -q "첨부 .*개\|--thread" "$D/sent.log" && ! grep -q "b-fail-me\|\"attachments\"" "$D/claude.stdin" && ok "기본(설정 없음): 영상·이미지 첨부 없이 초안 파일 1건, 재료에 첨부 후보도 안 넘김" || ko "첨부 기본 끔" "rc=$rc $(sent)"
reset; echo media > "$D/plan-collect"; LRM_SCRUM_MEDIA=on post --date $WK; grep -q "\"attachments\"" "$D/claude.stdin" && [ "$(sent)" = 5 ] && ok "LRM_SCRUM_MEDIA=on 이면 첨부 후보를 재료에 넘기고 스레드로 올림" || ko "첨부 켬"
reset; echo "SCRUM_MEDIA=on" > "$D/scrum.env"; echo media > "$D/plan-collect"; post --date $WK; [ "$(sent)" = 5 ] && ok "scrum.env 의 SCRUM_MEDIA=on 으로도 켬" || ko "scrum.env 첨부"
rm -f "$D/scrum.env"

# ---- 팀 공유 사항 칸 ----
SH="$D/share.md"; export LRM_SCRUM_SHARE_FILE="$SH"; rm -f "$SH"
reset; post --date $WK; ! grep -q "팀 공유 사항" "$D"/draft/2026-11-12.md && ! grep -q "경고: 팀 공유" "$D/sent.log" && ok "공유 사항 파일이 없으면 칸·경고 모두 생략" || ko "공유 파일 없음"
printf '# 주석만\n\n' > "$SH"; reset; post --date $WK; ! grep -q "팀 공유 사항" "$D"/draft/2026-11-12.md && ok "주석·빈 줄뿐인 파일도 칸 생략" || ko "빈 공유 파일"
cat > "$SH" <<'J'
# 시험
날짜: 2026-11-12
- 앱 설치 안내 [문서](https://example.com/a) 정리함
매점 2일차부터 열림
- 허브 내부 사정 공유
- 키 token=FAKE_TEST_SECRET 노출
- 결정 D-ABC123 안내
J
reset; post --date $WK; f="$D/draft/2026-11-12.md"
python3 - "$f" <<'PY' && ok "공유 사항: 칸 맨 앞(제목 바로 아래, 4칸 들여쓰기), 불릿 표시 유무 모두 허용" || ko "공유 칸 위치" "$(cat "$f")"
import sys
L = open(sys.argv[1], encoding="utf-8").read().splitlines()
i = L.index("- **팀 공유 사항**")
assert L[i + 1] == "    - 앱 설치 안내 [문서](https://example.com/a) 정리함" and L[i + 2] == "    - 매점 2일차부터 열림", L
assert L[i - 2] == "# 시험이름" and L[i - 1] == "", L   # 맨 앞: 제목 바로 아래
assert L[i + 3].startswith("- **주제 A**"), L
PY
! grep -q "허브 내부\|FAKE_TEST_SECRET\|D-ABC123" "$f" "$D/sent.log" && grep -q "경고: 팀 공유 사항 3줄을 뺌" "$D/sent.log" && ok "로컬 용어·비밀·내부 번호 줄은 빼고 뺀 줄 수만 경고(원문은 Slack·초안에 없음)" || ko "공유 줄 거름" "$(cat "$D/sent.log")"
! grep -q "FAKE_TEST_SECRET\|허브 내부" "$D/scrum.log" && ok "빼는 줄의 원문은 로그에도 없음" || ko "로그 원문"
sed -i.bak 's/2026-11-12/2026-11-11/' "$SH"; rm -f "$SH.bak"; reset; post --date $WK
! grep -q "팀 공유 사항" "$D/draft/2026-11-12.md" && grep -q "날짜(2026-11-11)가 오늘" "$D/scrum.log" && ok "날짜 줄이 오늘이 아니면 낡은 공유 사항으로 보고 생략" || ko "낡은 공유"
{ echo '날짜: 2026-11-12'; for i in $(seq 27); do echo "- 줄 $i"; done; } > "$SH"; reset; post --date $WK
[ "$(grep -c '^    - 줄 ' "$D/draft/2026-11-12.md")" = 24 ] && grep -q "경고: 팀 공유 사항 3줄을 뺌" "$D/sent.log" && ok "공유 사항 24줄 상한" || ko "공유 상한"
rm -f "$SH"; unset LRM_SCRUM_SHARE_FILE

# ---- 수정본 답글(--reply-thread) ----
TS=1794533351.285799
reset; post --date $WK; post --date $WK --force --reply-thread $TS; rc=$?
[ $rc = 0 ] && [ "$(sent)" = 2 ] && last=$(awk 'BEGIN{RS="===\n"} NF{b=$0} END{print b}' "$D/sent.log") && printf '%s' "$last" | grep -q -- "--thread $TS" && printf '%s' "$last" | grep -q "수정본 11-12 초안" && printf '%s' "$last" | grep -q "2026-11-12.수정본.md" && ! printf '%s' "$last" | grep -q "스크럼 초안 도착" && [ -s "$D/draft/2026-11-12.수정본.md" ] && sent_has 2026-11-12 && ok "--reply-thread: 요약 글 없이 그 스레드에 '수정본' 답글로 초안 파일만(첫 초안 파일과 이름 구분)" || ko "수정본 답글" "rc=$rc $(sent)"
reset; echo media > "$D/plan-collect"; LRM_SCRUM_MEDIA=on post --date $WK --force --reply-thread $TS; [ "$(sent)" = 1 ] && ok "--reply-thread 는 첨부가 켜져 있어도 초안 파일만 올림" || ko "수정본 첨부" "$(sent)"
post --date $WK --reply-thread 12345; [ $? = 2 ] && grep -q "숫자.숫자" "$D/err" && post --date $WK --dry-run --reply-thread $TS; [ $? = 2 ] && ok "--reply-thread 형식 오류·--dry-run 조합은 종료 코드 2 + 조치 안내" || ko "reply 인자 오류"

# ---- --preflight(첫 실행 전 점검) ----
cat > "$D/pf_gh.sh" <<'G'
#!/bin/bash
echo "Token: gho_FAKE_PREFLIGHT_TOKEN"; [ "$(cat "$FAKE_DIR/pf-gh" 2>/dev/null)" = fail ] && exit 1; exit 0
G
cat > "$D/pf_claude.sh" <<'G'
#!/bin/bash
case "$1 $2" in
  "auth status") [ "$(cat "$FAKE_DIR/pf-login" 2>/dev/null)" = fail ] && echo '{"loggedIn": false}' || echo '{"loggedIn": true, "email": "secret@example.com"}' ;;
  "mcp get") [ "$(cat "$FAKE_DIR/pf-mcp" 2>/dev/null)" = fail ] && echo "  Status: ! Needs authentication" || echo "  Status: ✔ Connected" ;;
esac
G
chmod +x "$D/pf_gh.sh" "$D/pf_claude.sh"
pf() { rm -f "$D"/pf-*; for k in "$@"; do echo fail > "$D/pf-$k"; done; reset; LRM_GH="$D/pf_gh.sh" LRM_CLAUDE="$D/pf_claude.sh" LRM_NOTIFY="$HERE/fake_send.sh" post --preflight; }
pf; rc=$?; [ $rc = 0 ] && [ "$(grep -c '^PASS' "$D/out")" = 5 ] && [ "$(sent)" = 0 ] && [ ! -e "$D/scrum.state.json" ] && [ ! -e "$D/claude.log" ] && ok "--preflight 전부 통과 → 종료 코드 0, 발송·상태 파일·초안 작성 없음" || ko "preflight 통과" "rc=$rc $(cat "$D/out")"
! grep -q "FAKE_PREFLIGHT_TOKEN\|secret@example.com" "$D/out" "$D/err" && ok "--preflight 출력에 토큰·계정 값 없음" || ko "preflight 노출"
pf gh; rc=$?; [ $rc = 1 ] && grep -q "FAIL  gh 인증" "$D/out" && ok "gh 인증 실패 감지(종료 코드 1)" || ko "gh 실패" "rc=$rc"
pf login; rc=$?; [ $rc = 1 ] && grep -q "FAIL  claude 로그인" "$D/out" && ok "claude 로그아웃 감지" || ko "claude 실패" "rc=$rc"
pf mcp; rc=$?; [ $rc = 1 ] && grep -q "FAIL  Atlassian MCP 연결" "$D/out" && grep -q "/mcp" "$D/out" && ok "Atlassian MCP 미연결 감지 + 조치 안내" || ko "mcp 실패" "rc=$rc"
rm -f "$D"/pf-*; reset; LRM_GH="$D/pf_gh.sh" LRM_CLAUDE="$D/pf_claude.sh" LRM_NOTIFY="$D/없는-명령" post --preflight; rc=$?
[ $rc = 1 ] && grep -q "FAIL  Slack 봇 스크립트" "$D/out" && ok "Slack 봇 스크립트 없음 감지" || ko "slack 스크립트" "rc=$rc"
reset; (unset LRM_SCRUM_NAME; LRM_GH="$D/pf_gh.sh" LRM_CLAUDE="$D/pf_claude.sh" LRM_NOTIFY="$HERE/fake_send.sh" post --preflight); rc=$?
[ $rc = 1 ] && grep -q "FAIL  본인 칸 제목" "$D/out" && ok "본인 칸 제목 없음 감지(인자 오류 2 가 아니라 점검 실패 1)" || ko "제목 점검" "rc=$rc"

# ---- 수집 오류 반영(리뷰 지적 5) ----
reset; echo err-all > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 1 ] && [ ! -e "$D/claude.log" ] && grep -q -- "--kind warn" "$D/sent.log" && grep -q "collect_required_missing" "$D/sent.log" && [ ! -d "$D/draft" ] && ok "prs·commits 수집 둘 다 실패 → 초안 없이 warn(정상 초안으로 확정 안 함)" || ko "수집 전부 실패" "rc=$rc"
python3 -c 'import json,sys;sys.exit(1 if "2026-11-12" in json.load(open(sys.argv[1]))["sent"] else 0)' "$D/scrum.state.json" && ok "수집 실패한 날은 '보냄' 기록 없음" || ko "수집 실패 기록"
! grep -q "keyring" "$D/sent.log" && ok "수집 오류 원문(keyring)은 Slack 으로 안 나감" || ko "오류 원문 노출"
reset; echo err-only > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 1 ] && grep -q "collect_all_empty" "$D/sent.log" && ok "전 출처가 비고 오류가 있으면 실패" || ko "전부 빔" "rc=$rc"
reset; echo err-prs > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 0 ] && grep -q -- "--kind doc" "$D/sent.log" && grep -q "경고: 재료 수집 누락(prs)" "$D/sent.log" && ok "일부 출처 누락 → 초안은 보내되 누락 출처를 경고 줄로 명시" || ko "일부 누락" "rc=$rc"
reset; echo badjson > "$D/plan-collect"; post --date $WK; rc=$?
[ $rc = 1 ] && grep -q "collect_bad_json" "$D/sent.log" && ok "수집 출력이 JSON 이 아니면 실패" || ko "깨진 JSON" "rc=$rc"
reset; post --date $WK; ! grep -q "수집 누락" "$D/sent.log" && ok "오류 없으면 누락 경고 없음" || ko "정상 경고"

# ---- notify 시간 초과·실행 불가(리뷰 지적 6) ----
reset; echo hang > "$D/send-mode"; LRM_SCRUM_NOTIFY_TIMEOUT=1 post --date $WK; rc=$?
[ $rc = 1 ] && ! grep -q "Traceback" "$D/err" && [ -s "$D/osa.log" ] && ls "$D"/draft/2026-11-12.md > /dev/null && grep -q "시간 초과" "$D/scrum.log" && ok "notify 시간 초과 → 예외 없이 로그·로컬 알림·초안 보존, 종료 코드 1" || ko "notify 시간 초과" "rc=$rc $(tail -3 "$D/err")"
reset; LRM_NOTIFY="$D/없는-명령" post --date $WK; rc=$?
[ $rc = 1 ] && ! grep -q "Traceback" "$D/err" && [ -s "$D/osa.log" ] && ls "$D"/draft/2026-11-12.md > /dev/null && ! python3 -c 'import json,sys;sys.exit(0 if json.load(open(sys.argv[1]))["sent"] else 1)' "$D/scrum.state.json" && ok "notify 실행 불가(OSError) → 예외 없이 로컬 알림, 보냄 기록 없음" || ko "notify OSError" "rc=$rc $(tail -3 "$D/err")"
reset; echo hang > "$D/send-mode"; for m in claude-fable-5-1 claude-fable-5-2 claude-opus-5-5; do echo fail > "$D/plan-$m"; done; LRM_SCRUM_NOTIFY_TIMEOUT=1 post --date $WK; rc=$?
[ $rc = 1 ] && ! grep -q "Traceback" "$D/err" && [ -s "$D/osa.log" ] && ok "warn 발송 중 시간 초과도 예외 없이 로컬 알림" || ko "warn 시간 초과" "rc=$rc"

# ---- 발송 결과 불명 시 중복 게시 방지(리뷰 지적 3) ----
reset; echo record-hang > "$D/send-mode"; LRM_SCRUM_NOTIFY_TIMEOUT=1 post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(cat "$D/send-calls")" = 1 ] && sent_has 2026-11-12 && ! pending_has 2026-11-12 && grep -q "F_FAKE_1" "$D/scrum.state.json" && ok "올라간 뒤 응답만 못 받음(시간 초과) → 발송 기록 확인으로 보냄 확정, 재게시 없음, file_id 저장" || ko "올라간 뒤 시간 초과" "rc=$rc calls=$(cat "$D/send-calls")"
reset; echo record-silent1 > "$D/send-mode"; post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(cat "$D/send-calls")" = 1 ] && sent_has 2026-11-12 && ok "올라간 뒤 종료 코드 1(원인 불명) → 재시도 없이 기록 확인으로 확정" || ko "올라간 뒤 코드 1" "rc=$rc calls=$(cat "$D/send-calls")"
reset; echo silent1 > "$D/send-mode"; post --date $WK; rc=$?
[ $rc = 1 ] && [ "$(cat "$D/send-calls")" = 1 ] && ! sent_has 2026-11-12 && pending_has 2026-11-12 && [ -s "$D/osa.log" ] && grep -q "결과 불명" "$D/scrum.log" && ok "결과 불명(기록 없음) → 재시도 안 함, 전송 의도 남김, 로컬 알림" || ko "결과 불명" "rc=$rc calls=$(cat "$D/send-calls")"
rm -f "$D/send-mode"; post --date $WK; rc=$?
[ $rc = 1 ] && [ "$(cat "$D/send-calls")" = 1 ] && grep -q "결과 불명" "$D/scrum.log" && ok "결과 불명인 날의 재실행은 자동 재전송 안 함(중복 방지)" || ko "불명 재실행" "rc=$rc calls=$(cat "$D/send-calls")"
printf '{"at":%s,"kind":"file","path":"%s","file_id":"F_LATE"}\n' "$(date +%s)" "$(python3 -c 'import os,sys;print(os.path.normpath(sys.argv[1]))' "$D/draft/2026-11-12.md")" >> "$D/slack-sent.jsonl"; post --date $WK; rc=$?
[ $rc = 0 ] && sent_has 2026-11-12 && [ "$(cat "$D/send-calls")" = 1 ] && grep -q F_LATE "$D/scrum.state.json" && ok "뒤늦게 발송 기록이 확인되면 재전송 없이 보냄으로 확정" || ko "뒤늦은 확정" "rc=$rc"
reset; echo silent1 > "$D/send-mode"; post --date $WK; rm -f "$D/send-mode"; post --date $WK --force; [ "$(cat "$D/send-calls")" = 2 ] && sent_has 2026-11-12 && ok "--force 는 결과 불명 날도 다시 보냄" || ko "--force 재전송" "$(cat "$D/send-calls")"
reset; echo 1 > "$D/send-fails"; post --date $WK; [ "$(cat "$D/send-calls")" = 2 ] && sent_has 2026-11-12 && ok "Slack 거절(오류 코드)은 미게시가 확실하므로 재시도" || ko "거절 재시도" "$(cat "$D/send-calls")"
reset; echo 5 > "$D/send-fails"; post --date $WK; ! pending_has 2026-11-12 && ! sent_has 2026-11-12 && ok "끝내 거절되면 전송 의도 기록을 지움(다음 실행이 재시도)" || ko "거절 뒤 의도"

# ---- Slack 으로 나가는 문자열 정제(리뷰 지적 4) ----
reset; echo secret > "$D/plan-claude-fable-5-1"; post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(tr '\n' ' ' < "$D/claude.log")" = "claude-fable-5-1 claude-fable-5-2 " ] && ! grep -rq "FAKE_TEST_SECRET" "$D/sent.log" "$D"/draft && ok "비밀 패턴이 든 초안은 거부 → 다음 모델 채택, 본문·첨부에 노출 없음" || ko "비밀 초안" "rc=$rc $(cat "$D/claude.log")"
reset; for m in claude-fable-5-1 claude-fable-5-2 claude-opus-5-5; do echo secret > "$D/plan-$m"; done; post --date $WK
grep -q -- "--kind warn" "$D/sent.log" && grep -q "sensitive_text" "$D/sent.log" && ! grep -q "FAKE_TEST_SECRET\|D-PRIVATE" "$D/sent.log" "$D/scrum.log" && ok "전 모델이 비밀 초안이면 분류 코드만 담은 warn, 로그에도 원문 없음" || ko "비밀 warn"
reset; echo idtopic > "$D/plan-claude-fable-5-1"; post --date $WK
body=$(sed -n '1,/^참고/p' "$D/sent.log"); ! printf '%s' "$body" | grep -q "LRM-123\|D-PRIVATE" && printf '%s' "$body" | grep -q "정리" && grep -q "LRM-123" "$D"/draft/*.md && ok "Slack 본문 주제에서 내부 번호 제거(첨부 원문은 유지)" || ko "주제 정제" "$body"
reset; for m in claude-fable-5-1 claude-fable-5-2 claude-opus-5-5; do echo authfail > "$D/plan-$m"; done; post --date $WK
grep -q -- "--kind warn" "$D/sent.log" && grep -q "exit_1" "$D/sent.log" && ! grep -q "FAKE_TEST_SECRET\|Bearer\|Authorization" "$D/sent.log" "$D/scrum.log" && ok "CLI 오류 원문(Authorization 등)은 Slack·로그에 없고 분류 코드만" || ko "오류 원문" "$(cat "$D/sent.log")"

# ---- 실패 시 warn 1건 ----
reset; for m in claude-fable-5-1 claude-fable-5-2 claude-opus-5-5; do echo fail > "$D/plan-$m"; done; post --date $WK; rc=$?
[ $rc = 1 ] && [ "$(sent)" = 1 ] && grep -q -- "--kind warn" "$D/sent.log" && grep -q "• 무엇" "$D/sent.log" && grep -q "• 왜" "$D/sent.log" && grep -q "• 결과" "$D/sent.log" && grep -q "👉 답" "$D/sent.log" && ok "전부 실패 → warn 1건(무엇·왜·결과·답 양식), 종료 코드 1" || ko "warn" "rc=$rc $(sent)"
post --date $WK; [ "$(sent)" = 1 ] && ok "같은 날 재실행해도 warn 은 1건" || ko "warn 중복" "$(sent)"
python3 -c 'import json,sys;sys.exit(1 if "2026-11-12" in json.load(open(sys.argv[1]))["sent"] else 0)' "$D/scrum.state.json" && ok "실패한 날은 '보냄' 으로 기록하지 않음(재시도 가능)" || ko "실패 기록"
rm -f "$D/plan-claude-fable-5-1"; post --date $WK; [ "$(sent)" = 2 ] && grep -q -- "--kind doc" "$D/sent.log" && ok "복구 뒤 재실행은 초안 발송" || ko "복구" "$(sent)"

# ---- 발송 경로·dry-run ----
reset; echo 2 > "$D/send-fails"; post --date $WK; rc=$?
[ $rc = 0 ] && [ "$(cat "$D/send-calls")" = 3 ] && ok "Slack API 실패(코드 1)는 2회 재시도 뒤 성공" || ko "재시도" "rc=$rc calls=$(cat "$D/send-calls")"
reset; echo 5 > "$D/send-fails"; post --date $WK; rc=$?
[ $rc = 1 ] && [ "$(cat "$D/send-calls")" = 3 ] && [ -s "$D/osa.log" ] && ls "$D"/draft/2026-11-12.md > /dev/null && ! sent_has 2026-11-12 && python3 -c 'import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDWR); fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)' "$D/scrum.lock" && ok "끝내 실패 → 초안 파일 남김·로컬 알림·잠금 풀림" || ko "발송 실패" "rc=$rc"
reset; post --date $WK --dry-run; rc=$?
[ $rc = 0 ] && [ "$(sent)" = 0 ] && [ ! -e "$D/scrum.state.json" ] && grep -q "^# $LRM_SCRUM_NAME\$" "$D/out" && [ -s "$D/draft/2026-11-12.dry.md" ] && ok "--dry-run: 발송·상태 기록 없이 초안만 출력·저장" || ko "dry-run" "rc=$rc"
reset; post --date $WK; post --date $WK --dry-run; [ "$(sent)" = 1 ] && grep -q "^# $LRM_SCRUM_NAME\$" "$D/out" && ok "--dry-run 은 이미 보낸 날에도 초안 생성" || ko "dry-run 재생성"
reset; echo '{"sent":{"2026-11-09":{"at":1}}}' > "$D/scrum.state.json"; post --date $WK
grep -q "경고: 11-11 초안이 안 나갔음" "$D/sent.log" && ok "어제 건너뜀 경고 1줄" || ko "어제 건너뜀" "$(cat "$D/sent.log")"
reset; post --date $WK; ! grep -q "경고" "$D/sent.log" && ok "첫 실행(기록 없음)은 건너뜀 경고 없음" || ko "첫 실행 경고"
reset; post --date $WK; [ "$(sent)" = 1 ] && [ "$(grep -c . "$D/sent.log")" -le 12 ] && ! grep -q "LRM-1" <(sed -n 1,/참고/p "$D/sent.log" | grep -v "참고:") && ok "Slack 본문엔 티켓 번호 없음(원문은 첨부 파일)" || ko "본문 규칙"

# ---- 본인 칸 제목 ----
reset; (unset LRM_SCRUM_NAME; post --date $WK); rc=$?; [ $rc = 2 ] && grep -q "SCRUM_NAME" "$D/err" && [ "$(sent)" = 0 ] && ok "제목 없으면 종료 코드 2 + 설정 방법 안내" || ko "제목 없음" "rc=$rc"
reset; echo "SCRUM_NAME=파일이름" > "$D/scrum.env"; (unset LRM_SCRUM_NAME; FAKE_NAME=파일이름 post --date $WK); rc=$?; [ $rc = 2 ] || { head -1 "$D"/draft/*.md | grep -q "^# 파일이름$" && ok "scrum.env 의 SCRUM_NAME 사용" || ko "scrum.env" "rc=$rc"; }
rm -f "$D/scrum.env"

# ---- 인자 오류·도움말 ----
post --date 11-12; [ $? = 2 ] && grep -q "YYYY-MM-DD" "$D/err" && ok "날짜 형식 오류 → 종료 코드 2 + 조치 안내" || ko "인자 오류"
"$S/scrum_run.sh" --help > "$D/help"; [ $? = 0 ] && grep -q -- "--dry-run" "$D/help" && ok "래퍼 --help" || ko "--help"
python3 "$S/scrum_collect.py" --date 2026-13-01 > /dev/null 2> "$D/err"; [ $? = 2 ] && ok "수집기 날짜 오류 → 종료 코드 2" || ko "수집기 오류"

# ---- plist 로그 부모 폴더(리뷰 부가) ----
# launchd 는 로그 파일을 열기 전 부모 폴더를 만들지 않음. install.sh 가 확실히 만드는 폴더(links.tsv 대상의 부모)여야 함
python3 - "$HERE/../../hubs/launchd/ai.lrm.scrum.plist" "$HERE/../../links.tsv" <<'PY' && ok "plist 로그 경로의 부모 폴더는 install.sh 가 만드는 폴더" || ko "plist 로그 폴더"
import plistlib, sys, posixpath
pl = plistlib.load(open(sys.argv[1], "rb"))
dsts = [ln.split("\t")[1] for ln in open(sys.argv[2], encoding="utf-8") if ln.strip() and not ln.startswith("#")]
parents = {posixpath.dirname(d) for d in dsts}
for k in ("StandardOutPath", "StandardErrorPath"):
    rel = pl[k].replace("__HOME__/", "")
    assert posixpath.dirname(rel) in parents, "%s 의 부모 %s 는 설치 대상이 아님" % (k, posixpath.dirname(rel))
PY

echo "---- PASS $PASS / FAIL $FAIL"
[ "$FAIL" = 0 ]
