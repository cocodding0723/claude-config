#!/usr/bin/env bash
# pre-push 로컬 게이트 시험: 가짜 게임 저장소 + 가짜 local-gate.sh(통과·실패·환경 오류·느림). 임시 폴더만 씀.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$HERE/../.." && pwd -P)
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }
SB=$(mktemp -d "${TMPDIR:-/tmp}/lg-test.XXXXXX"); trap 'rm -rf "${SB:?}"' EXIT
mkdir -p "$SB/home"
export HOME="$SB/home" GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_GLOBAL="$SB/gitconfig" \
  GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t \
  CLAUDE_HOOKS_DIR="$REPO/claude/hooks" LRM_LOCAL_GATE_DIR="$SB/store" CALLS="$SB/calls"
git config --global core.hooksPath "$REPO/git/hooks"
git config --global push.default current

# 가짜 게이트: FAKE_MODE = pass|fail|env|slow. 호출 인자를 $CALLS 에 남김
write_gate() {
  mkdir -p "$1/Tools/ci"
  cat > "$1/Tools/ci/local-gate.sh" <<'GATE'
#!/usr/bin/env bash
echo "$*" >> "$CALLS"
while [ $# -gt 0 ]; do case $1 in --report) rep=$2; shift;; --base) base=$2; shift;; esac; shift; done
case ${FAKE_MODE:-pass} in
  fail) echo "컴파일 실패: Foo.cs(3)" >&2; echo '{"steps":[{"name":"compile","status":"fail"}]}' > "$rep"; exit 1;;
  env)  echo "dotnet 없음" >&2; exit 2;;
  slow) sleep 20; exit 0;;
esac
head=$(git rev-parse HEAD)
printf '{"head":"%s","base":"%s","steps":[{"name":"compile","status":"pass","seconds":1.5,"note":""}],"total_seconds":1.5}\n' "$head" "$base" > "$rep"
GATE
}
mkrepo() { # <이름> → 원격 + 작업 저장소(main·dev)
  git init -q --bare "$SB/$1.git"; git init -q -b main "$SB/$1"
  git -C "$SB/$1" remote add origin "$SB/$1.git"
  git -C "$SB/$1" commit -q --allow-empty -m init
  git -C "$SB/$1" branch dev
  git -C "$SB/$1" -c core.hooksPath=/dev/null push -q origin main dev 2>/dev/null
}
G="$SB/LoveRevolutionMaker"; mkrepo LoveRevolutionMaker
O="$SB/Other"; mkrepo Other
base_commit=$(git -C "$G" rev-parse HEAD)
git -C "$G" checkout -q -b feat/old                         # 게이트 도입 전 브랜치
git -C "$G" checkout -q -b feat/a
write_gate "$G"; git -C "$G" add -A; git -C "$G" commit -q -n -m gate
git -C "$O" checkout -q -b feat/a; write_gate "$O"; git -C "$O" add -A; git -C "$O" commit -q -n -m gate

calls() { [ -f "$CALLS" ] && wc -l < "$CALLS" | tr -d ' ' || echo 0; }
push() { local d=$1; shift; (cd "$d" && git push origin "$@" 2>&1); } # → 출력, 종료 코드는 호출 쪽에서 $?
newc() { git -C "$1" commit -q -n --allow-empty -m "$2"; }
fail_if() { [ "$1" = 1 ] && ok "$2" || ko "$2" "${3:-}"; }

# 1) 통과 + 기록 + 인자
out=$(push "$G" feat/a); rc=$?
head=$(git -C "$G" rev-parse HEAD)
[ $rc = 0 ] && [ -f "$SB/store/$head.json" ] && [ "$(calls)" = 1 ] && ok "통과: push 성공·기록 생성·1회 실행" || ko "통과" "rc=$rc calls=$(calls) $out"
grep -q -- '--no-unity' "$CALLS" && grep -q -- '--base origin/dev' "$CALLS" && ok "새 브랜치 base=origin/dev, --no-unity" || ko "인자" "$(cat "$CALLS")"
jq -e --arg h "$head" '.head == $h and (.steps | length) == 1' "$SB/store/$head.json" >/dev/null && ok "기록 = 보고서 사본" || ko "기록 내용"

# 2) 같은 커밋 재사용(다른 원격 이름으로 다시 push)
out=$(push "$G" feat/a:refs/heads/feat/b); rc=$?
[ $rc = 0 ] && [ "$(calls)" = 1 ] && printf '%s' "$out" | grep -q '통과 기록 있음' && ok "같은 커밋 재push: 다시 실행 안 함" || ko "재사용" "rc=$rc calls=$(calls) $out"

# 3) base = 원격 대상 브랜치
newc "$G" c2; out=$(push "$G" feat/a); rc=$?
[ $rc = 0 ] && tail -1 "$CALLS" | grep -q -- '--base origin/feat/a' && ok "원격 브랜치 있으면 base=origin/feat/a" || ko "base" "rc=$rc $(tail -1 "$CALLS")"

# 4) 실패 → 거부
newc "$G" c3; export FAKE_MODE=fail; n=$(calls)
out=$(push "$G" feat/a); rc=$?
head=$(git -C "$G" rev-parse HEAD)
[ $rc != 0 ] && printf '%s' "$out" | grep -q '실패: 컴파일 실패: Foo.cs(3)' && printf '%s' "$out" | grep -q "보고서: $SB/store/$head.last.json" && [ ! -f "$SB/store/$head.json" ] \
  && ok "실패: 거부·원인 한 줄·보고서 경로·기록 없음" || ko "실패" "rc=$rc $out"
! git -C "$SB/LoveRevolutionMaker.git" cat-file -e "$head" 2>/dev/null && ok "실패: 원격에 커밋 안 감" || ko "원격 오염"

# 5) 환경 오류 → 거부 + 조치
export FAKE_MODE=env; out=$(push "$G" feat/a); rc=$?
[ $rc != 0 ] && printf '%s' "$out" | grep -q '환경 오류.*dotnet 없음' && printf '%s' "$out" | grep -q '조치:' && ok "환경 오류: 거부·조치 한 줄" || ko "환경 오류" "rc=$rc $out"

# 6) 시간 초과
export FAKE_MODE=slow; git -C "$G" config hooks.localGateTimeout 2
s=$SECONDS; out=$(push "$G" feat/a); rc=$?; el=$((SECONDS - s))
[ $rc != 0 ] && printf '%s' "$out" | grep -q '시간 초과' && printf '%s' "$out" | grep -q -- '--offline' && [ $el -lt 15 ] && ok "시간 초과: 중단·거부(${el}초)" || ko "시간 초과" "rc=$rc el=$el $out"
git -C "$G" config --unset hooks.localGateTimeout

# 7) 스크립트 없는 옛 브랜치: 경고만 하고 통과
export FAKE_MODE=fail; n=$(calls); git -C "$G" checkout -q feat/old; newc "$G" old1
out=$(push "$G" feat/old); rc=$?
[ $rc = 0 ] && [ "$(calls)" = "$n" ] && printf '%s' "$out" | grep -q '게이트 도입 전' && ok "옛 브랜치: 경고만·통과" || ko "옛 브랜치" "rc=$rc $out"
git -C "$G" checkout -q feat/a

# 8) 다른 저장소는 실행 안 함, hooks.localGate=on 이면 실행
n=$(calls); out=$(push "$O" feat/a); rc=$?
[ $rc = 0 ] && [ "$(calls)" = "$n" ] && ok "다른 저장소: 게이트 실행 안 함" || ko "다른 저장소" "rc=$rc $out"
git -C "$O" config hooks.localGate on; newc "$O" o2; out=$(push "$O" feat/a); rc=$?
[ $rc != 0 ] && [ "$(calls)" = "$((n + 1))" ] && ok "hooks.localGate=on: 다른 저장소도 실행" || ko "localGate=on" "rc=$rc $out"

# 9) 게임 저장소 off: 경고 + 통과(우회 경로가 아님을 stderr 로 알림)
git -C "$G" config hooks.localGate off; n=$(calls)
out=$(push "$G" feat/a); rc=$?
[ $rc = 0 ] && [ "$(calls)" = "$n" ] && printf '%s' "$out" | grep -q '경고.*꺼져 있음' && ok "off: 경고 출력·실행 안 함" || ko "off" "rc=$rc $out"
git -C "$G" config --unset hooks.localGate

# 10) 삭제 push 는 게이트 대상 아님
export FAKE_MODE=pass; n=$(calls); out=$(cd "$G" && git push origin :feat/b 2>&1); rc=$?
[ $rc = 0 ] && [ "$(calls)" = "$n" ] && ok "삭제 push: 게이트 건너뜀" || ko "삭제 push" "rc=$rc $out"

# 11) 저장소 로컬 pre-push 훅도 여전히 실행(_chain), 실패하면 거부
printf '#!/bin/sh\ncat >/dev/null; echo LOCALHOOK >&2; exit 1\n' > "$G/.git/hooks/pre-push"; chmod +x "$G/.git/hooks/pre-push"
n=$(calls); newc "$G" c4; out=$(push "$G" feat/a); rc=$?
[ $rc != 0 ] && printf '%s' "$out" | grep -q LOCALHOOK && [ "$(calls)" = "$n" ] && ok "저장소 로컬 훅 우선 실행·실패 시 거부" || ko "_chain" "rc=$rc $out"
rm -f "$G/.git/hooks/pre-push"

# 12) 우회 감지: git-guard
H="$REPO/claude/hooks/git-guard.sh"
gg() { local o; o=$(printf '%s' "$(jq -cn --arg c "$1" --arg d "$2" '{tool_input:{command:$c},cwd:$d}')" | bash "$H" 2>/dev/null); [ -z "$o" ] && echo pass || printf '%s' "$o" | jq -r '.hookSpecificOutput.permissionDecision'; }
gx() { local got; got=$(gg "$2" "$3"); [ "$got" = "$4" ] && ok "$1" || ko "$1" "기대 $4, 실제 $got"; }
gx "우회: push --no-verify 거부" 'git push --no-verify origin feat/a' "$G" deny
gx "우회: 약어 --no-verif 도 거부" 'git push --no-verif origin feat/a' "$G" deny
gx "우회: -C 로 게임 저장소 지정해도 거부" "git -C $G push --no-verify origin x" /tmp deny
gx "우회: push -n(dry-run)은 통과" 'git push -n origin feat/a' "$G" pass
gx "우회: -c core.hooksPath 거부" 'git -c core.hooksPath=/dev/null push origin feat/a' "$G" deny
gx "우회: config core.hooksPath 거부" 'git config core.hooksPath /dev/null' "$G" deny
gx "우회: config --get core.hooksPath 통과" 'git config --get core.hooksPath' "$G" pass
gx "우회: hooks.localGate off 거부" 'git config hooks.localGate off' "$G" deny
gx "우회: hooks.localGate on 은 통과" 'git config hooks.localGate on' "$G" pass
gx "우회: 문구 속 --no-verify 는 통과" 'echo "git push --no-verify"' "$G" pass
gx "우회: 다른 저장소 push --no-verify 는 통과" 'git push --no-verify origin x' "$SB/Other.git" pass
git -C "$O" config --unset hooks.localGate
gx "우회: 다른 저장소(게이트 비대상)는 통과" 'git push --no-verify origin x' "$O" pass
gx "우회: 일반 push 통과" 'git push origin feat/a' "$G" pass

# 13) create-pr 헬퍼: 기록 있으면 단계별 줄, 없으면 종료 1
GL="$REPO/skills/create-pr/scripts/gate-lines.sh"
good=$(git -C "$G" rev-parse feat/a~0); rec=$(ls "$SB/store"/*.json | grep -v last | head -1); sha=$(basename "$rec" .json)
out=$(cd "$G" && bash "$GL" "$sha" 2>&1); rc=$?
[ $rc = 0 ] && printf '%s' "$out" | grep -q '`compile` — pass (1.5초)' && ok "gate-lines: 단계별 명령 — 결과 줄" || ko "gate-lines" "rc=$rc $out"
out=$(cd "$G" && bash "$GL" HEAD~1 2>&1); rc=$?
[ $rc = 1 ] && ok "gate-lines: 기록 없으면 종료 1" || ko "gate-lines 없음" "rc=$rc $out"

printf '\n%s 통과 / %s 실패\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
