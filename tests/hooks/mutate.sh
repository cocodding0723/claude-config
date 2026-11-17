#!/usr/bin/env bash
# 변이 확인: ws_token_guard.py 사본에 결함을 하나씩 심고 tests/hooks/run.sh 가 실패하는지 본다. 놓친 변이가 있으면 종료 코드 1.
# 원본은 바꾸지 않고 임시 사본(mktemp -d)에서만 실행함.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
ROOT="$HERE/../.."
WORK=$(mktemp -d "${TMPDIR:-/tmp}/cc-hooks-mut.XXXXXX")
trap 'rm -rf "${WORK:?}"' EXIT
MISSED=0

mutant() { # id 설명 perl치환식
  local id="$1" desc="$2" expr="$3" out rc
  rm -rf "${WORK:?}/t"; mkdir -p "$WORK/t"
  (cd "$ROOT" && tar -cf - --exclude=./.git --exclude=./.claude . 2>/dev/null) | (cd "$WORK/t" && tar -xf -)  # run.sh 의 다른 구역이 저장소 전체를 요구함
  perl -0pi -e "$expr" "$WORK/t/claude/hooks/lib/ws_token_guard.py"
  if cmp -s "$ROOT/claude/hooks/lib/ws_token_guard.py" "$WORK/t/claude/hooks/lib/ws_token_guard.py"; then echo "$id 변이 정의 오류: 사본이 바뀌지 않음" >&2; MISSED=1; return; fi
  out=$(bash "$WORK/t/tests/hooks/run.sh" 2>&1); rc=$(printf '%s\n' "$out" | grep -c '^FAIL  ws-guard')  # 다른 구역 실패는 세지 않음
  if [ "$rc" != 0 ]; then echo "검출 $id — $desc (${rc}건 실패)"; else echo "놓침 $id — $desc"; MISSED=1; fi
}

mutant M1 "TOKEN_RE 의 .example 예외 제거" 's/\(\?!\\\.example\)//'
mutant M2 "파일 인자의 토큰 판정 제거" 's/    return any\(names_token\(a\) for a in files\)\n/    return False\n/'
mutant M3 "내용 비출력 명령 목록 비움(test -f·ls 도 거부)" 's/CONTENT_SAFE = \{[^}]*\}/CONTENT_SAFE = set()/'
mutant M4 "검색 패턴 자리를 파일로 취급" 's/files, pat_given, k = \[\], cmd not in PATTERN_FIRST, 0/files, pat_given, k = [], True, 0/'
mutant M5 "git 앞 경로 제거 해석 누락(/usr/bin/git)" 's/if base\(seg\[i\]\) != "git":/if seg[i] != "git":/'
mutant M6 "worktree add 판정 반전" 's/== WT_ADD_WORDS/!= WT_ADD_WORDS/'
mutant M7 "heredoc 본문 제거 생략" 's/if m and "<<<" not in line:/if False:/'
mutant M8 "인라인 코드 경로 검사 제거" 's/if interp and re\.search/if False and re.search/'
mutant M9 "설정 폴더 통째 Grep 검사 제거" 's/tool == "Grep" and real == hub_dir\(\)/False/'
mutant M10 "bash -n 예외 제거" 's/if any\(re\.fullmatch\(r"-\[a-zA-Z\]\*n\[a-zA-Z\]\*", o\) for o in shell_opts\):/if False:/'
mutant M11 "와일드카드 토큰 판정 제거" 's/return bool\(d\) and any\(c in b for c in "\*\?\["\) and/return False and any(c in b for c in "*?[") and/'
exit "$MISSED"
