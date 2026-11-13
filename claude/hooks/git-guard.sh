#!/usr/bin/env bash
# PreToolUse(Bash) guard: block destructive git commands, escalate risky ones to "ask".
# stdin: hook JSON. stdout: permission decision JSON (or nothing = no opinion).
# 공유 브랜치(main/master/dev/develop/release/*) 이력 보호. 작업 브랜치 재작성은 허용.
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')
cwd=$(printf '%s' "$input" | jq -r '.cwd // ""')

printf '%s' "$cmd" | grep -Eq '(^|[[:space:];&|(])git[[:space:]]' || exit 0   # git 명령 아니면 즉시 통과
G='(^|[[:space:];&|(])git[[:space:]]+(-[^[:space:]]+[[:space:]]+)*'
SHARED_RE='^(main|master|dev|develop|release/.+)$'          # 공유 브랜치 이력 보호
# 새 브랜치 이름 규칙. 기본 feat/ fix/ hotfix/ release/ + sprint-YYYYMMDD/. 저장소별 확장: git config hooks.branchPrefixes "chore/ docs/" (공백·쉼표 구분)
EXTRA_PREFIX_RE=$(git -C "${cwd:-.}" config --get hooks.branchPrefixes 2>/dev/null | tr ',' ' ' | tr -s ' ' '\n' | sed 's/[][\.*^$(){}+?|]/\\&/g' | sed '/^$/d' | sed 's/$/[^[:space:]]+/' | paste -sd'|' -)
PREFIX_RE="^((feat|feature|fix|hotfix|release)/[^[:space:]]+|sprint-[0-9]{8}/[^[:space:]]+${EXTRA_PREFIX_RE:+|$EXTRA_PREFIX_RE})\$"
WORK_RE="^((feat|feature|fix|hotfix|chore|docs)/[^[:space:]]+|sprint-[0-9]{8}/[^[:space:]]+${EXTRA_PREFIX_RE:+|$EXTRA_PREFIX_RE})\$"   # 작업 브랜치(병합 받아오기 허용 대상)
SYNC_RE='^(origin|upstream)/(main|master|dev|develop)$'      # 받아오기 전용 병합 대상(원격 추적 브랜치)

deny() { jq -cn --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'; exit 0; }
ask()  { jq -cn --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$r}}'; exit 0; }
cur_branch() { [ -n "$cwd" ] && git -C "$cwd" symbolic-ref --short -q HEAD 2>/dev/null; }
is_shared()  { printf '%s' "$1" | grep -Eq "$SHARED_RE"; }

# ---- 하드 차단 (되돌릴 수 없는 작업) ----
if printf '%s' "$cmd" | grep -Eq "${G}(push)[^|;&]*(--force\b|[[:space:]]-f\b|[[:space:]]-[a-zA-Z]*f[a-zA-Z]*\b)" \
   && ! printf '%s' "$cmd" | grep -Eq -- '--force-with-lease'; then
  deny "git push --force 차단됨. 필요하면 --force-with-lease 사용 (승인 필요)."
fi
printf '%s' "$cmd" | grep -Eq "${G}(reset)[^|;&]*--hard"        && deny "git reset --hard 차단됨. 작업 내용이 영구 삭제됩니다. git stash 또는 사용자에게 직접 요청."
printf '%s' "$cmd" | grep -Eq "${G}(clean)[^|;&]*-[a-zA-Z]*[fd]" && deny "git clean -f/-d 차단됨. 추적되지 않은 파일이 영구 삭제됩니다."
printf '%s' "$cmd" | grep -Eq "${G}(checkout|restore)[^|;&]*(--[[:space:]]+\.|[[:space:]]\.[[:space:]]*$)" && deny "작업 트리 전체 되돌리기(checkout/restore .) 차단됨. 파일 단위로 지정하거나 사용자에게 요청."
printf '%s' "$cmd" | grep -Eq "${G}(branch)[^|;&]*[[:space:]]-D\b"  && deny "git branch -D 차단됨. 병합 안 된 브랜치가 사라집니다. -d 사용."
printf '%s' "$cmd" | grep -Eq "${G}(stash)[^|;&]*(drop|clear)"    && deny "git stash drop/clear 차단됨."
printf '%s' "$cmd" | grep -Eq "${G}(filter-branch|filter-repo)([[:space:]]|$)|${G}(update-ref)[^|;&]*[[:space:]]-d([[:space:]]|$)" && deny "히스토리 재작성 명령 차단됨."
printf '%s' "$cmd" | grep -Eq "${G}(push)[^|;&]*(--delete|[[:space:]]:[a-zA-Z])" && deny "원격 브랜치 삭제 차단됨."

# ---- 공유 브랜치 히스토리 보호 (rebase / amend) ----
# "서로 공유하는 브랜치의 커밋 그래프는 함부로 변경하지 않습니다"
if printf '%s' "$cmd" | grep -Eq "${G}(rebase)\b" && ! printf '%s' "$cmd" | grep -Eq "${G}(rebase)[^|;&]*--(continue|abort|skip|quit)\b"; then
  br=$(cur_branch)
  is_shared "$br" && deny "공유 브랜치 '$br'에서 rebase 차단됨. rebase는 feat/·fix/ 같은 작업 브랜치에서만 합니다 (git checkout <작업브랜치> 후 git rebase develop)."
fi
if printf '%s' "$cmd" | grep -Eq "${G}(commit)[^|;&]*--amend"; then
  br=$(cur_branch)
  is_shared "$br" && deny "공유 브랜치 '$br'에서 commit --amend 차단됨. 공유 브랜치 히스토리는 바꾸지 않습니다. 새 커밋으로 수정하세요."
fi

# ---- merge: 작업 브랜치로 origin/dev·origin/main 받아오기는 확인 없이 허용, 그 밖의 병합은 확인 ----
# 공유 브랜치로의 병합 규칙(--no-ff)은 아래에서 그대로 차단. 모든 병합 확인은 이 훅이 맡음(settings 의 git merge ask 규칙 대체)
if printf '%s' "$cmd" | grep -Eq "${G}(merge)[[:space:]]" && ! printf '%s' "$cmd" | grep -Eq '[;&|`$(]'; then
  br=$(cur_branch)
  if printf '%s' "$br" | grep -Eq "$WORK_RE" && ! is_shared "$br"; then
    targets=$(printf '%s' "$cmd" | sed -E "s/^.*git[[:space:]]+(-[^[:space:]]+[[:space:]]+)*merge[[:space:]]+//" | tr -s ' ' '\n' | grep -v '^-' | sed '/^$/d')
    if [ -n "$targets" ] && ! printf '%s' "$targets" | grep -Evq "$SYNC_RE" \
       && ! printf '%s' "$cmd" | grep -Eq -- '--(abort|continue|quit|squash)\b'; then
      jq -cn --arg r "작업 브랜치 '$br' 로 원격 기준 브랜치 받아오기: 확인 없이 허용" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",permissionDecisionReason:$r}}'; exit 0
    fi
  fi
fi

# ---- merge: 공유 브랜치로 병합할 때는 --no-ff (merge commit 남기기) ----
if printf '%s' "$cmd" | grep -Eq "${G}(merge)[[:space:]]" && ! printf '%s' "$cmd" | grep -Eq "${G}(merge)[^|;&]*(--no-ff|--ff-only|--squash|--abort|--continue|--quit)\b"; then
  br=$(cur_branch)
  if is_shared "$br" && [ "$(git -C "$cwd" config --get merge.ff 2>/dev/null)" != false ]; then
    deny "'$br'로 병합할 때는 git merge --no-ff <브랜치> 를 사용합니다 (merge commit으로 병합 이력 보존). 이 저장소는 merge.ff=false 설정이 없습니다."
  fi
fi

# ---- 새 브랜치 이름 규칙: feat/ fix/ hotfix/ release/ sprint-YYYYMMDD/ (+ hooks.branchPrefixes) ----
newbr=$(printf '%s' "$cmd" | grep -Eo "${G}(checkout[[:space:]]+-b|switch[[:space:]]+-c|worktree[[:space:]]+add[^|;&]*[[:space:]]-b)[[:space:]]+[^[:space:]|;&]+" | head -1 | awk '{print $NF}')
[ -z "$newbr" ] && newbr=$(printf '%s' "$cmd" | grep -Eo "${G}branch[[:space:]]+[^-[:space:]|;&][^[:space:]|;&]*" | head -1 | awk '{print $NF}')   # git branch NAME
if [ -n "$newbr" ] && ! printf '%s' "$newbr" | grep -Eq "$PREFIX_RE|$SHARED_RE"; then
  ask "새 브랜치 이름 '$newbr' 이 규칙(feat/… fix/… hotfix/… release/… sprint-YYYYMMDD/…, 저장소별 확장은 git config hooks.branchPrefixes)에 맞지 않습니다. 이대로 만들까요?"
fi

# ---- ask 승격 ----
printf '%s' "$cmd" | grep -Eq "${G}(merge)[[:space:]]" && ask "git merge 확인 필요 (작업 브랜치로 origin/dev·origin/main 받아오기만 확인 없이 허용됩니다)."
printf '%s' "$cmd" | grep -Eq -- '--force-with-lease' && ask "force-with-lease push — 원격 히스토리가 바뀝니다. 확인 필요."
printf '%s' "$cmd" | grep -Eq "${G}(commit)[^|;&]*--no-verify" && ask "--no-verify는 pre-commit 훅을 건너뜁니다. 확인 필요."
printf '%s' "$cmd" | grep -Eq "${G}(pull)\b" && ! printf '%s' "$cmd" | grep -Eq "${G}(pull)[^|;&]*--rebase" \
  && [ "$(git -C "${cwd:-.}" config --get pull.rebase 2>/dev/null)" != true ] \
  && ask "git pull 은 --rebase 로 합니다 (선형 히스토리 유지). 이 저장소는 pull.rebase=true 설정이 없습니다. 그래도 진행할까요?"

# 공유 개발 브랜치 직접 커밋은 별도 확인.
if printf '%s' "$cmd" | grep -Eq "${G}(commit)\b" && [ -n "$cwd" ]; then
  br=$(cur_branch)
  case "$br" in main|master|dev|develop)
    ask "현재 브랜치가 '$br' 입니다. 직접 커밋해도 될까요? (보통은 feat/ 또는 fix/ 브랜치를 먼저 만듭니다)";;
  esac
fi
exit 0
