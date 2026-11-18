#!/usr/bin/env bash
# github-issue 스킬 시험: 검사기(키 형식)·보드 도구(gh_board.py). 가짜 gh 만 씀, 네트워크 없음.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
SK=$REPO/skills/github-issue/scripts
PY=${PY:-python3}
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-ghissue.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export GITHUB_ISSUE_GH="$HERE/fake_gh.py" FAKE_GH_LOG="$SB/log" FAKE_GH_STATE="$SB/state.json" GITHUB_ISSUE_CONFIG="$SB/c.json"
cp "$REPO/skills/github-issue/references/config.example.json" "$SB/c.json"
U=https://github.com/me/r/issues/5 P=https://github.com/me/r/issues/1

# ---- 검사기 -------------------------------------------------------------------
pre() { printf '%s' "$1" > "$SB/in.json"; "$PY" "$SK/issue_check.py" pre "$SB/in.json"; }
base='"type":"작업","summary":"[우편함] 받기 버튼 추가","assignee":"me","due":"2026-11-30","due_reason":"작은 작업","status":"해야 할 일","evidence":{}'
out=$(pre "{$base,\"parent\":{\"key\":\"me/r#1\",\"reason\":\"같은 우편함 흐름\"}}"); rc=$?
[ "$rc" = 0 ] && [ "$out" = 통과 ] && ok "검사기: OWNER/REPO#번호 상위 통과" || ko "검사기 통과" "rc=$rc $out"
out=$(pre "{$base,\"parent\":{\"key\":\"LRM-1\",\"reason\":\"x\"}}"); rc=$?
[ "$rc" = 1 ] && echo "$out" | grep -q 'OWNER/REPO#번호' && ok "검사기: Jira 키는 형식 오류(안내도 GitHub 기준)" || ko "검사기 키" "rc=$rc $out"
out=$(pre "{${base/\"assignee\":\"me\",/},\"parent\":{\"key\":\"me/r#1\",\"reason\":\"x\"}}"); rc=$?
[ "$rc" = 1 ] && echo "$out" | grep -q 'GitHub 로그인' && ok "검사기: 담당 없음 → GitHub 로그인 안내" || ko "검사기 담당" "$out"

# ---- 보드 도구 ----------------------------------------------------------------
out=$("$PY" "$SK/gh_board.py" add "$U" --status '해야 할 일' --due 2026-11-30 --parent "$P"); rc=$?
[ "$rc" = 0 ] && [ "$out" = "ok 보드 #6 상태 '해야 할 일' · 기한 2026-11-30 · 상위 연결함" ] && ok "add: 보드·상태·기한·상위 연결" || ko "add" "rc=$rc $out"
grep -q '"--single-select-option-id", "O_해야 할 일"' "$SB/log" && grep -q '"--date", "2026-11-30"' "$SB/log" && grep -q '"sub_issue_id=1005"' "$SB/log" \
  && ok "add: 상태 선택지 ID·기한·하위 ID 로 호출" || ko "add 호출" "$(cat "$SB/log")"
n=$(grep -c sub_issue_id "$SB/log"); out=$("$PY" "$SK/gh_board.py" add "$U" --status '해야 할 일' --parent "$P")
[ "$(grep -c sub_issue_id "$SB/log")" = "$n" ] && echo "$out" | grep -q '상위 이미 연결됨' && ok "add 재실행: 하위 연결 중복 없음" || ko "add 재실행" "$out"
"$PY" "$SK/gh_board.py" status "$U" 완료 >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "status 완료: 사람만 → 종료 코드 2" || ko "완료 거부" "rc=$rc"
"$PY" "$SK/gh_board.py" status "$U" 없는상태 >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "status 없는 선택지: 종료 코드 2" || ko "없는 상태" "rc=$rc"
"$PY" "$SK/gh_board.py" add LRM-1 --status '진행 중' >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "add URL 형식 오류: 종료 코드 2" || ko "URL 형식" "rc=$rc"
out=$("$PY" "$SK/gh_board.py" get "$U"); rc=$?
[ "$rc" = 0 ] && [ "$("$PY" -c 'import json,sys;d=json.loads(sys.argv[1]);print(d["key"],d["parent"],d["status"],d["due"],d["assignee"])' "$out")" = "me/r#5 me/r#1 진행 중 2026-11-30 me" ] \
  && ok "get: 점검 칸(키·상위·상태·기한·담당)" || ko "get" "rc=$rc $out"
GITHUB_ISSUE_CONFIG="$SB/없음.json" "$PY" "$SK/gh_board.py" config >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "설정 없음: 종료 코드 2" || ko "설정 없음" "rc=$rc"

printf '결과: PASS %d, FAIL %d\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
