#!/usr/bin/env bash
# 보호 브랜치 직접 push 판정(git-guard 가 source). 값은 이 파일 한 곳에만 둠.
# 게임 저장소의 PR 우회 방지: 사용자 11-16 "PR로 머지 올리면되잖아". git config 는 읽기만 함.
# 사용자가 터미널에서 '!' 로 직접 실행하는 push 는 훅 밖이라 영향 없음. 게임 저장소 아닌 곳(claude-config 등)도 영향 없음.
PP_PROTECTED_RE='^(main|master|dev|develop|release/.+|asset-patch/.+)$'

# 인자: push_targets JSON 한 줄(.dir·.flags·.remote·.refs). 출력: 없음=통과, "deny <브랜치>", "ask <사유>"
pp_check() {
  local line=$1 d cur up remote flags ref dst n
  d=$(printf '%s' "$line" | jq -r '.dir // empty')
  flags=$(printf '%s' "$line" | jq -r '(.flags // [])[]')
  printf '%s\n' "$flags" | grep -Eq '^(--delete|-d|--dry-run|-n)$' && return 0   # 삭제는 기존 규칙, dry-run 은 원격을 안 바꿈
  printf '%s\n' "$flags" | grep -Eq '^(--all|--mirror)$' && { echo "deny 보호 브랜치 포함 가능(--all·--mirror)"; return 0; }
  remote=$(printf '%s' "$line" | jq -r '.remote // empty')
  cur=$(git -C "$d" symbolic-ref --short -q HEAD 2>/dev/null)
  up=""; [ -n "$cur" ] && up=$(git -C "$d" config --get "branch.$cur.merge" 2>/dev/null)
  up=${up#refs/heads/}
  n=0
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    n=$((n + 1))
    case "$ref" in *'$'*|*'`'*|*'*'*) echo "ask"; return 0;; esac
    ref=${ref#+}
    case "$ref" in
      :*) continue;;                       # 삭제 refspec 은 기존 규칙
      *:*) dst=${ref##*:}; [ -n "$dst" ] || continue;;
      *) dst=$ref;;
    esac
    case "$dst" in refs/heads/*) dst=${dst#refs/heads/};; refs/*) continue;; esac   # 태그 등은 대상 아님
    if [ "$dst" = HEAD ]; then
      [ -n "$cur" ] || { echo "ask"; return 0; }
      dst=$cur
      # 'HEAD' 단독: upstream 이름이 다르고 보호 브랜치면 그쪽도 대상으로 봄
      case "$ref" in *:*) ;; *) [ -n "$up" ] && printf '%s' "$up" | grep -Eq "$PP_PROTECTED_RE" && { echo "deny $up"; return 0; };; esac
    fi
    printf '%s' "$dst" | grep -Eq "$PP_PROTECTED_RE" && { echo "deny $dst"; return 0; }
  done < <(printf '%s' "$line" | jq -r '(.refs // [])[]')
  [ "$n" -gt 0 ] && return 0
  # refspec 없음: 태그만 push(--tags)는 통과, 아니면 현재 브랜치(+upstream 이름)가 대상
  printf '%s\n' "$flags" | grep -Eq '^--tags$' && return 0
  [ -n "$cur" ] || { echo "ask"; return 0; }
  for dst in $(git -C "$d" config --get-all "remote.${remote:-origin}.push" 2>/dev/null | sed 's/^+//; s/.*://; s#^refs/heads/##') "$cur" "$up"; do
    [ -n "$dst" ] || continue
    printf '%s' "$dst" | grep -Eq "$PP_PROTECTED_RE" && { echo "deny $dst"; return 0; }
  done
  return 0
}
