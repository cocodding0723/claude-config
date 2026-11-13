#!/usr/bin/env bash
# claude/hooks 시험: git-guard(회귀·sprint 접두·merge 받아오기), search-guard, ai-trace-guard. 임시 저장소만 씀.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
H="$HERE/../../claude/hooks"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/cc-hooks.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
# run <훅> <명령> <cwd> → pass|allow|ask|deny
run() { local out; out=$(printf '%s' "$(jq -cn --arg c "$2" --arg d "$3" '{tool_input:{command:$c},cwd:$d}')" | HOME="$SB/home" bash "$H/$1" 2>/dev/null); [ -z "$out" ] && { echo pass; return; }; printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision'; }
exp() { local got; got=$(run "$2" "$3" "$4"); [ "$got" = "${5:-pass}" ] && ok "$1" || ko "$1" "기대 ${5:-pass}, 실제 ${got:-빈 출력}"; }
mkdir -p "$SB/home"
mk() { git init -q -b "$2" "$1"; git -C "$1" commit -q --allow-empty -m init; }

# ---- git-guard ----
R="$SB/r"; mk "$R" main; git -C "$R" config merge.ff false
GG=git-guard.sh
exp "git-guard: 강제 push 차단" $GG 'git push --force origin x' "$R" deny
exp "git-guard: reset --hard 차단" $GG 'git reset --hard' "$R" deny
exp "git-guard: branch -D 차단" $GG 'git branch -D x' "$R" deny
exp "git-guard: 새 브랜치 feat/ 통과" $GG 'git checkout -b feat/x' "$R"
exp "git-guard: sprint-YYYYMMDD/ 브랜치 허용" $GG 'git checkout -b sprint-20261113/bundle-a' "$R"
exp "git-guard: sprint-abc 는 확인" $GG 'git checkout -b sprint-abc/x' "$R" ask
exp "git-guard: foo/ 는 확인" $GG 'git checkout -b foo/x' "$R" ask
exp "git-guard: chore/ 는 기본 확인" $GG 'git checkout -b chore/x' "$R" ask
git -C "$R" config hooks.branchPrefixes 'chore/, docs/'
exp "git-guard: hooks.branchPrefixes 로 chore/ 허용" $GG 'git checkout -b chore/x' "$R"
exp "git-guard: hooks.branchPrefixes 로 docs/ 허용" $GG 'git checkout -b docs/y' "$R"
exp "git-guard: 확장해도 foo/ 는 확인" $GG 'git checkout -b foo/x' "$R" ask
git -C "$R" checkout -q -b feat/work
exp "git-guard: 작업 브랜치에서 merge origin/dev 허용" $GG 'git merge origin/dev' "$R" allow
exp "git-guard: merge origin/main --no-edit 허용" $GG 'git merge --no-edit origin/main' "$R" allow
exp "git-guard: 작업 브랜치 merge 로컬 브랜치는 확인" $GG 'git merge feat/other' "$R" ask
exp "git-guard: merge origin/dev 와 다른 대상 섞으면 확인" $GG 'git merge origin/dev feat/other' "$R" ask
exp "git-guard: 연결 명령 안의 merge 는 확인" $GG 'git merge origin/dev && git push' "$R" ask
exp "git-guard: --squash 는 확인" $GG 'git merge --squash origin/dev' "$R" ask
git -C "$R" checkout -q -b sprint-20261113/b
exp "git-guard: sprint 브랜치에서 merge origin/dev 허용" $GG 'git merge origin/dev' "$R" allow
git -C "$R" checkout -q main
exp "git-guard: 공유 브랜치로 merge origin/dev 는 허용 안 함(확인)" $GG 'git merge origin/dev' "$R" ask
exp "git-guard: 공유 브랜치 --no-ff 병합도 확인" $GG 'git merge --no-ff feat/work' "$R" ask
git -C "$R" config merge.ff true
exp "git-guard: 공유 브랜치 merge.ff 미설정이면 차단" $GG 'git merge feat/work' "$R" deny
git -C "$R" config merge.ff false
git -C "$R" checkout -q -b chore/z
exp "git-guard: chore/ 브랜치도 받아오기 허용" $GG 'git merge origin/main' "$R" allow
exp "git-guard: rebase 공유 브랜치 차단 회귀" $GG 'git rebase main' "$SB/r" pass
git -C "$R" checkout -q main
exp "git-guard: 공유 브랜치 rebase 차단" $GG 'git rebase feat/x' "$R" deny
exp "git-guard: git 아닌 명령 통과" $GG 'ls -la' "$R"

# ---- search-guard ----
SG=search-guard.sh; HM="$SB/home"; mkdir -p "$HM/orca/ws"
exp "search-guard: find / 차단" $SG 'find / -name x' /tmp deny
exp "search-guard: find ~ 차단" $SG 'find ~ -name x' /tmp deny
exp "search-guard: find \$HOME 차단" $SG 'find $HOME -type f' /tmp deny
exp "search-guard: find $HM 차단(절대 경로)" $SG "find $HM -name x" /tmp deny
exp "search-guard: find /Users 차단" $SG 'find /Users -name x' /tmp deny
exp "search-guard: find ~/* 차단" $SG 'find ~/* -name x' /tmp deny
exp "search-guard: grep -rn / 차단" $SG 'grep -rn foo /' /tmp deny
exp "search-guard: grep -R ~ 차단" $SG 'grep -R foo ~' /tmp deny
exp "search-guard: grep --recursive ~/ 차단" $SG 'grep --recursive foo ~/' /tmp deny
exp "search-guard: fd 패턴 + / 차단" $SG 'fd foo /' /tmp deny
exp "search-guard: fd --search-path ~ 차단" $SG 'fd foo --search-path ~' /tmp deny
exp "search-guard: bfs -L ~ 차단" $SG 'bfs -L ~ -name x' /tmp deny
exp "search-guard: 파이프 뒤 find 도 차단" $SG 'echo a | find / -name x' /tmp deny
exp "search-guard: 경로 생략 + cwd 가 홈이면 차단" $SG 'find . -name x' "$HM" deny
exp "search-guard: ~/orca 아래는 허용" $SG 'find ~/orca/ws -name x' /tmp
exp "search-guard: 저장소 상대 경로 허용" $SG 'find . -name x' "$HM/orca/ws"
exp "search-guard: grep -r 저장소 폴더 허용" $SG 'grep -rn foo src' /tmp
exp "search-guard: grep(재귀 아님) ~ 허용" $SG 'grep foo ~/a.txt' /tmp
exp "search-guard: grep 패턴이 / 여도 경로 아님(허용)" $SG 'grep -r / src' /tmp
exp "search-guard: 관계없는 명령 허용" $SG 'ls /' /tmp
exp "search-guard: 따옴표 안 문구는 명령 아님" $SG "echo 'find / -name x'" /tmp
exp "search-guard: 깨진 따옴표는 통과(검사 불가)" $SG "find / -name 'x" /tmp

# ---- ai-trace-guard ----
AT=ai-trace-guard.sh
O="$SB/o.git"; git init -q --bare -b main "$O"
mkclone() { git clone -q "$O" "$1" 2>/dev/null; git -C "$1" checkout -q -B main; git -C "$1" commit -q --allow-empty -m base; git -C "$1" push -q -u origin main 2>/dev/null; }
mkclone "$SB/g"   # 게임 저장소 가정(기본 ask)
git -C "$SB/g" checkout -q -b feat/a; git -C "$SB/g" commit -q --allow-empty -m "깨끗함"
exp "ai-trace: 깨끗한 커밋 push 통과" $AT 'git push -u origin feat/a' "$SB/g"
git -C "$SB/g" commit -q --allow-empty -m "기능

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
exp "ai-trace: 게임 저장소(기본) Co-Authored-By 는 경고(ask)" $AT 'git push origin feat/a' "$SB/g" ask
git -C "$SB/g" config hooks.aiTraceMode deny
exp "ai-trace: hooks.aiTraceMode=deny 면 거부" $AT 'git push origin feat/a' "$SB/g" deny
git -C "$SB/g" config hooks.aiTraceMode off
exp "ai-trace: off 면 통과" $AT 'git push origin feat/a' "$SB/g"
git -C "$SB/g" config --unset hooks.aiTraceMode
mkdir -p "$SB/claude-config"; mkclone "$SB/claude-config"
git -C "$SB/claude-config" checkout -q -b feat/a; git -C "$SB/claude-config" commit -q --allow-empty -m "x

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
exp "ai-trace: claude-config 저장소는 기본 거부" $AT 'git push origin feat/a' "$SB/claude-config" deny
# 이미 원격에 있는 이력은 검사 안 함
git -C "$SB/g" push -q -f origin feat/a 2>/dev/null; git -C "$SB/g" branch -q -u origin/feat/a feat/a 2>/dev/null
exp "ai-trace: 이미 push 된 이력은 대상 아님" $AT 'git push' "$SB/g"
exp "ai-trace: gh pr create 본문의 Generated with 경고" $AT 'gh pr create --title t --body "요약
🤖 Generated with [Claude Code](https://claude.com/claude-code)"' "$SB/g" ask
exp "ai-trace: gh pr create 깨끗한 본문 통과" $AT 'gh pr create --title t --body "요약 한 줄"' "$SB/g"
printf '요약\nCo-Authored-By: Claude <noreply@anthropic.com>\n' > "$SB/body.md"
exp "ai-trace: --body-file 내용 검사" $AT "gh pr edit 3 --body-file $SB/body.md" "$SB/g" ask
exp "ai-trace: git push 아닌 명령은 통과" $AT 'echo Co-Authored-By: Claude' "$SB/g"
exp "ai-trace: 사람 공저자는 통과" $AT 'gh pr create --body "Co-Authored-By: Kim <kim@x.com>"' "$SB/g"

# 범위·대상 해석: 명령의 cd·-C·refspec 이 hook cwd 보다 우선, 낡은 origin/dev 무시, 문구 속 push 무시
CC="$SB/claude-config"   # feat/a 에 흔적 커밋, main 은 깨끗
mkdir -p "$SB/oth"; mk "$SB/oth" chore/x   # upstream 없는 다른 저장소(허브 작업 공간 역할)
git -C "$CC" checkout -q main
git -C "$CC" update-ref refs/remotes/origin/dev "$(git -C "$CC" rev-list --max-parents=0 main | head -1)"
git -C "$CC" commit -q --allow-empty -m "정상 커밋"
exp "ai-trace: cd 로 옮긴 저장소의 main 범위만 검사(다른 cwd 통과)" $AT "cd $CC && git push origin main" "$SB/oth"
exp "ai-trace: git -C 형태 통과" $AT "git -C $CC push origin main" "$SB/oth"
git -C "$CC" checkout -q feat/a
exp "ai-trace: 흔적 커밋이 든 브랜치는 cd 형태도 거부" $AT "cd $CC && git push origin feat/a" "$SB/oth" deny
exp "ai-trace: -u 형태 거부" $AT "git -C $CC push -u origin feat/a" "$SB/oth" deny
exp "ai-trace: HEAD:refs/heads/x 형태 거부" $AT "git -C $CC push origin HEAD:refs/heads/feat/a" "$SB/oth" deny
exp "ai-trace: 로컬 main:main refspec 은 main 범위만 검사" $AT "git -C $CC push origin main:main" "$SB/oth"
exp "ai-trace: 낡은 origin/dev 가 있어도 범위에 안 들어감" $AT "git -C $CC push origin main" "$SB/oth"
exp "ai-trace: heredoc 본문 속 push 글자 무시" $AT "cat > $SB/x.md <<'EOF'
git -C $CC push origin feat/a
EOF" "$SB/oth"
exp "ai-trace: 따옴표 속 push 글자 무시" $AT "echo 'git -C $CC push origin feat/a'" "$SB/oth"
exp "ai-trace: 저장소 아닌 경로는 확인(ask)" $AT "cd /nonexistent-zz && git push origin main" "$SB/oth" ask
exp "ai-trace: 변수 경로는 확인(ask)" $AT 'cd "$X" && git push origin main' "$SB/oth" ask

# ---- 저장소 commit-msg 훅(claude-config) ----
CM="$HERE/../../git/repo-hooks/commit-msg"
cm() { printf '%b' "$1" > "$SB/msg"; bash "$CM" "$SB/msg"; cat "$SB/msg"; }
out=$(cm '기능 추가\n\n본문 한 줄\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\n')
[ "$out" = $'기능 추가\n\n본문 한 줄' ] && ok "commit-msg: Co-Authored-By Claude 줄 제거, 끝 빈 줄 정리" || ko "commit-msg 제거" "$out"
out=$(cm '제목\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\nCo-Authored-By: Anthropic <noreply@anthropic.com>\n')
[ "$out" = "제목" ] && ok "commit-msg: 생성 표시·Anthropic 공저자 줄 모두 제거" || ko "commit-msg 생성 표시" "$out"
out=$(cm '제목\n\nCo-Authored-By: Kim <kim@x.com>\n')
[ "$out" = $'제목\n\nCo-Authored-By: Kim <kim@x.com>' ] && ok "commit-msg: 사람 공저자는 유지" || ko "commit-msg 사람" "$out"
out=$(cm '제목만\n')
[ "$out" = "제목만" ] && ok "commit-msg: 흔적 없으면 불변" || ko "commit-msg 불변" "$out"
# 전역 훅 체인 경유: 실제 git commit 으로 확인
CR="$SB/cmrepo"; mk "$CR" main; mkdir -p "$CR/.git/hooks"; ln -s "$CM" "$CR/.git/hooks/commit-msg"
git -C "$CR" -c core.hooksPath="$HERE/../../git/hooks" commit -q --allow-empty -m "$(printf '작업\n\nCo-Authored-By: Claude <noreply@anthropic.com>')"
[ "$(git -C "$CR" log -1 --format=%B | sed '/^$/d')" = "작업" ] && ok "commit-msg: 전역 체인 경유 커밋에서 흔적 제거" || ko "체인 경유" "$(git -C "$CR" log -1 --format=%B)"
# install.sh: 임시 HOME 의 주 체크아웃에서만 링크, 시험 HOME 밖 저장소는 불변
IH="$(cd "$SB" && pwd -P)/ih"; IR="$IH/orca/cc"; mkdir -p "$IR"
(cd "$HERE/../.." && tar --exclude=.git -cf - .) | tar -xf - -C "$IR"
git init -q -b main "$IR"; git -C "$IR" add -A; git -C "$IR" -c user.name=t -c user.email=t@t commit -q -m seed --no-verify
sed -E 's/^([A-Z_]+)=$/\1=x/' "$HERE/../../values.env.example" > "$SB/values.env"
env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM HOME="$IH" CLAUDE_CONFIG_VALUES="$SB/values.env" CLAUDE_CONFIG_ALLOW_LIVE_DIFF=1 bash "$IR/install.sh" >"$SB/inst.out" 2>&1
[ "$(readlink "$IR/.git/hooks/commit-msg")" = "$IR/git/repo-hooks/commit-msg" ] && ok "install.sh: 주 체크아웃 .git/hooks/commit-msg 링크" || ko "install 링크" "$(tail -3 "$SB/inst.out")"
again=$(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM HOME="$IH" CLAUDE_CONFIG_VALUES="$SB/values.env" CLAUDE_CONFIG_ALLOW_LIVE_DIFF=1 bash "$IR/install.sh" 2>&1)
[[ "$again" == *"SKIP   저장소 commit-msg 훅"* ]] && ok "install.sh: 재실행은 건너뜀(멱등)" || ko "install 멱등" ""

# ---- git-guard: 문구 오탐 방지(명령 위치의 git 만 판정) ----
exp "git-guard 오탐: heredoc 본문의 git push --force 문구는 통과" $GG $'cat > f.txt <<\'EOF\'\nrun git push --force origin x\nforce-with-lease push 설명\nEOF\ngit status' "$R"
exp "git-guard 오탐: python heredoc 안의 reset --hard 문구는 통과" $GG $'python3 - <<\'PY\'\nprint("git reset --hard")\nPY\ngit diff --stat' "$R"
exp "git-guard 오탐: echo 따옴표 안 force-with-lease push 는 통과" $GG "git log -1 && echo 'git push --force-with-lease origin x'" "$R"
exp "git-guard 오탐: git notes -m 메시지 안 branch -D 문구는 통과" $GG 'git notes add -m "docs: git branch -D 와 git push --force 설명"' "$R"
exp "git-guard 오탐: python -c 문자열 안 git clean -fd 는 통과" $GG "git status; python3 -c 'print(\"git clean -fd\")'" "$R"
exp "git-guard: 실제 git push --force-with-lease 는 계속 확인" $GG 'git push --force-with-lease origin feat/x' "$R" ask
exp "git-guard: heredoc 뒤의 실제 force-with-lease push 도 확인" $GG $'cat <<\'E\'\n문구\nE\ngit push --force-with-lease origin x' "$R" ask
exp "git-guard: 실제 git push --force 는 계속 차단" $GG 'git push --force origin x' "$R" deny
exp "git-guard: heredoc 뒤의 실제 reset --hard 도 차단" $GG $'cat <<\'E\'\n문구\nE\ngit reset --hard' "$R" deny
exp "git-guard: bash -c 안의 실제 push --force 는 차단" $GG 'bash -c "git push --force origin x"' "$R" deny
exp "git-guard: 따옴표 안 \$( ) 로 실행되는 push --force 는 차단" $GG 'echo "$(git push --force origin x)"' "$R" deny

echo "----"; echo "통과 $PASS · 실패 $FAIL"
[ "$FAIL" = 0 ]
