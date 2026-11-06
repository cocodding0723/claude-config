#!/usr/bin/env bash
# Run tests scoped to the changed files.
#   (no args)  Stop hook: stdin=hook JSON. Report-only (never blocks). Skips if nothing changed since last run.
#   --gate     git pre-commit: scope = staged files, cwd = repo. Non-zero exit aborts the commit.
#   --run      manual, cwd = repo. Prints summary + log path.
# Off switch: SCOPED_TEST=off  |  <repo>/.claude/scoped-test.json {"enabled":false} / {"on_stop":false}
# git이 훅에 내보내는 GIT_*는 하위 프로세스까지 상속된다. flutter가 자기 버전을 git으로
# 계산할 때 이 변수를 물면 SDK 대신 대상 저장소를 읽어 bin/cache/flutter.version.json에
# 엉뚱한 버전을 캐시하고, 이후 pub get이 SDK 제약 위반으로 실패한다.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
_d="$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")/lib"; . "$_d/common.sh"; . "$_d/runner.sh"; . "$_d/issue-check.sh"
mode=${1:-stop}; [ "$mode" = stop ] || mode=${mode#--}

if [ "$mode" = doctor ]; then   # --doctor: verify the harness can actually run
  ok=0; bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "  ✅ $1"; ok=$((ok+1)); else echo "  ❌ $1  → $3"; bad=$((bad+1)); fi; }
  echo "scoped-test doctor ($HOOKS_DIR)"
  chk "jq"            'command -v jq'                         "brew install jq"
  chk "perl"          'command -v perl'                       "macOS 기본 포함"
  chk "git ≥2.31"     'git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || git version | awk "{split(\$3,v,\".\"); exit !(v[1]>2 || (v[1]==2 && v[2]>=31))}"' "git 업데이트"
  chk "gh 로그인"     'gh auth status'                        "gh auth login (이슈 sync만 영향)"
  chk "OpenViking"    "curl -s -m 2 $OV_URL/health | grep -q healthy" "OV 서버 확인 (이슈 검색만 영향)"
  chk "core.hooksPath → pre-commit" '[ -x "$(git config --global core.hooksPath | sed "s#^~#$HOME#")/pre-commit" ]' "git config --global core.hooksPath ~/.config/git/hooks"
  chk "pre-commit → scoped-test 경로" 'grep -q scoped-test.sh "$(git config --global core.hooksPath | sed "s#^~#$HOME#")/pre-commit"' "pre-commit 안 경로 수정"
  for h in git-guard post-edit prompt-context scoped-test session-start session-end notify; do
    chk "settings.json hook: $h.sh" "jq -e '[.hooks[][].hooks[].command] | any(test(\"$h.sh\"))' ~/.claude/settings.json && [ -x \"$HOOKS_DIR/$h.sh\" ]" "settings.json 등록/실행권한 확인"
  done
  chk "state 디렉토리 쓰기" '[ -w "$STATE_DIR" ]' "chmod u+w $STATE_DIR"
  echo "  ok=$ok bad=$bad"; [ $bad -eq 0 ]; exit
fi
[ "$SCOPED_TEST" = off ] && { [ "$mode" != stop ] && echo "scoped-test: off (SCOPED_TEST=off)"; exit 0; }

if [ "$mode" = stop ]; then hook_input; cwd=$(hj '.cwd'); sid=$(hj '.session_id'); else cwd=$PWD; sid=""; fi
top=$(repo_top "$cwd") || exit 0
rh=$(repo_hash "$top") || exit 0
[ "$(cfg_get "$top" '.enabled' true)" = false ] && exit 0
[ "$mode" = stop ] && [ "$(cfg_get "$top" '.on_stop' true)" = false ] && exit 0
timeout=$(cfg_get "$top" '.timeout' 120)

files=$(mktemp "$STATE_DIR/logs/scope.XXXXXX")
if [ "$mode" = gate ]; then scope_staged "$top" > "$files"; fpmode=staged; else scope_worktree "$top" "$sid" > "$files"; fpmode=worktree; fi
[ -s "$files" ] || { rm -f "$files"; [ "$mode" = gate ] && echo "scoped-test: 스테이징된 코드 파일 없음, 건너뜀"; exit 0; }
fp=$(fp_of "$top" "$fpmode" < "$files")

prev=$(load_result "$rh" "$fp")
if [ -n "$prev" ]; then
  pst=$(printf '%s' "$prev" | jq -r .status); psu=$(printf '%s' "$prev" | jq -r .summary)
  case "$mode" in
    stop) rm -f "$files"; exit 0 ;;                       # unchanged since last run: stay quiet
    gate) [ "$pst" = pass ] && { rm -f "$files"; echo "scoped-test: 동일 내용으로 이미 통과 ($psu)"; exit 0; } ;;
  esac
fi

# one test run per repo at a time (parallel worktrees)
if ! lock_acquire "$rh"; then
  if [ "$mode" = stop ]; then rm -f "$files"; json_msg "scoped-test: 다른 세션이 이 repo 테스트 중 — 다음 Stop에서 재시도"; exit 0; fi
  w=0; while ! lock_acquire "$rh"; do sleep 2; w=$((w+2)); [ $w -ge 120 ] && { echo "scoped-test: 잠금 대기 초과 (다른 세션이 테스트 중)"; rm -f "$files"; exit 1; }; done
fi
trap 'lock_release "$rh"; rm -f "$files"' EXIT

log="$STATE_DIR/logs/$rh-$(date +%Y%m%d-%H%M%S).log"
n=$(wc -l < "$files" | tr -d ' ')
summary=$(run_scoped_tests "$top" "$files" "$log" "$timeout"); rc=$?
case $rc in 0) status=pass;; 1) status=fail;; 3) status=timeout;; *) status=skip;; esac
extra=""; [ "$status" = fail ] && extra=$(issue_check "$rh" "$log" "$sid")
save_result "$rh" "$fp" "$status" "$summary" "$log" "$sid"

case "$mode" in
  stop)
    case "$status" in
      pass) json_msg "✅ scoped-test: $n files → $summary" ;;
      skip) json_msg "⏭ scoped-test: $summary" ;;
      *)    json_msg "❌ scoped-test: $summary${extra:+
$extra}
log: $log" ;;
    esac ;;
  gate)
    echo "scoped-test [$status]: $n staged files → $summary"
    case "$status" in
      pass|skip) exit 0 ;;
      *) echo "--- 마지막 30줄 ($log) ---"; tail -30 "$log"; [ -n "$extra" ] && printf '%s\n' "--- 관련 ---" "$extra"
         echo "커밋 중단. 수정 후 다시 시도 (우회: SCOPED_TEST=off git commit …)"; exit 1 ;;
    esac ;;
  *)
    echo "scoped-test [$status]: $n files → $summary"; [ -n "$extra" ] && printf '%s\n' "$extra"; echo "log: $log"
    [ "$status" = fail ] && exit 1; exit 0 ;;
esac
