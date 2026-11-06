#!/usr/bin/env bash
# 워크트리 상태를 JSON 으로 수집. 읽기 전용.
# 사용: scan.sh [메인 저장소 경로] [기준 브랜치]
set -uo pipefail

REPO="${1:-$(git rev-parse --show-toplevel)}"
BASE="${2:-origin/dev}"
WS_ROOT="$(dirname "$REPO")/workspaces/$(basename "$REPO")"
NOW=$(date +%s)
git -C "$REPO" fetch -q origin "${BASE#origin/}" 2>/dev/null || true

# 원격 브랜치 기준 PR 목록 1회 조회. 실패해도 나머지는 진행
PRS=$(cd "$REPO" && gh pr list --state all --limit 200 \
  --json number,state,headRefName,isDraft,title 2>/dev/null || echo '[]')

# 미커밋 수에서 뺄 소음: 순수 C# .meta, PlayMode 부산물 폰트 아틀라스
NOISE='\.cs\.meta$|_Dynamic\.asset$|^TestResults/'

rows=()
registered=()
while IFS= read -r line; do
  case "$line" in
    "worktree "*) wt="${line#worktree }"; branch=""; detached=false ;;
    "branch "*)   branch="${line#branch refs/heads/}" ;;
    "detached")   detached=true ;;
    "")
      [ -z "${wt:-}" ] && continue
      [ "$wt" = "$REPO" ] && { wt=""; continue; }
      registered+=("$(basename "$wt")")
      if [ -d "$wt" ]; then
        last=$(git -C "$wt" log -1 --format=%ct 2>/dev/null || echo 0)
        st=$(git -C "$wt" status --porcelain 2>/dev/null)
        real=$(grep -v '^??' <<<"$st" | awk '{print $NF}' | grep -Ev "$NOISE" | grep -c .)
        all=$(grep '^??' <<<"$st" | awk '{print $NF}' | grep -Ev "$NOISE|\.meta$" | grep -c .)
        ab=$(git -C "$wt" rev-list --left-right --count "$BASE...HEAD" 2>/dev/null | tr '\t' ' ' || true)
        [ -z "$ab" ] && ab="? ?"
        missing=false
      else
        last=0; all=0; real=0; ab="? ?"; missing=true
      fi
      keys=$(printf '%s %s' "$branch" "$(basename "$wt")" | grep -oE 'LRM-[0-9]+' | sort -u | paste -sd, -)
      rows+=("$(jq -n \
        --arg dir "$(basename "$wt")" --arg branch "$branch" --argjson detached "$detached" \
        --arg keys "$keys" --argjson idle $(( (NOW - last) / 86400 )) \
        --argjson dirty "$real" --argjson dirtyAll "$all" \
        --arg behind "${ab%% *}" --arg ahead "${ab##* }" --argjson missing "$missing" \
        --argjson prs "$PRS" '
        { dir: $dir, branch: $branch, detached: $detached,
          tickets: ($keys | split(",") | map(select(. != ""))),
          idleDays: $idle, modified: $dirty, untracked: $dirtyAll,
          ahead: $ahead, behind: $behind, missingDir: $missing,
          prs: [ $prs[] | select(.headRefName == $branch)
                 | {number, state, draft: .isDraft} ] }')")
      wt=""
      ;;
  esac
done < <(cd "$REPO" && git worktree list --porcelain; echo)

# 워크트리 목록에 없는 폴더 = 고아
orphans='[]'
if [ -d "$WS_ROOT" ]; then
  orphans=$(for d in "$WS_ROOT"/*/; do
    n=$(basename "$d")
    [[ "$n" == _* ]] && continue
    printf '%s\n' "${registered[@]}" | grep -qxF "$n" || echo "$n"
  done | jq -R . | jq -s .)
fi

printf '%s\n' "${rows[@]}" | jq -s --arg base "$BASE" --argjson orphans "$orphans" \
  --argjson prs "$PRS" '
  { base: $base, worktrees: ., orphanDirs: $orphans,
    # 워크트리 없이 PR 만 남은 티켓도 잡도록 제목·브랜치의 LRM-n 으로 묶음
    prsByTicket: ( [ $prs[] | . as $p
        | ((.title + " " + .headRefName) | [scan("LRM-[0-9]+")] | unique[])
        | {key: ., pr: {number: $p.number, state: $p.state}} ]
      | group_by(.key) | map({key: .[0].key, value: map(.pr)}) | from_entries ) }'
