#!/usr/bin/env bash
# Sync GitHub issues of a repo -> local cache + OpenViking resources. Runs detached from session-start.
# usage: issue-sync.sh <repo_top>     (zero Claude tokens: nothing is printed to the session)
. "$(dirname "$(perl -MCwd=realpath -e 'print realpath shift' "${BASH_SOURCE[0]}")")/common.sh"
top=$1; [ -d "$top" ] || exit 0
slug=$(gh_slug "$top"); [ -n "$slug" ] || exit 0
command -v gh >/dev/null && gh auth status >/dev/null 2>&1 || exit 0
rh=$(repo_hash "$top") || exit 0; rd=$(repo_dir "$rh")
cache="$rd/issues.json"; stamp="$rd/issues.sync"
mkdir -p "$STATE_DIR/logs"; exec >> "$STATE_DIR/logs/issue-sync.log" 2>&1
echo "[$(date '+%F %T')] sync $slug"

# incremental: only issues updated since last sync (first run: last 300)
search=""; [ -f "$stamp" ] && search="updated:>=$(date -r "$(cat "$stamp")" +%F)"
now=$(date +%s)
fresh=$(gh issue list --repo "$slug" --state all --limit 300 ${search:+--search "$search"} \
        --json number,title,body,state,labels,updatedAt,url 2>/dev/null) || exit 0
[ -f "$cache" ] || echo '[]' > "$cache"
jq -s '(.[0] + .[1]) | group_by(.number) | map(max_by(.updatedAt)) | sort_by(-.number)' "$cache" <(printf '%s' "$fresh") > "$cache.tmp" \
  && mv "$cache.tmp" "$cache"
printf '%s' "$now" > "$stamp"
cnt=$(printf '%s' "$fresh" | jq 'length'); echo "  fetched=$cnt total=$(jq length "$cache")"
[ "$cnt" -gt 0 ] || exit 0

# push changed issues to OpenViking (one markdown doc per issue) — best effort
curl -s -m 2 "$OV_URL/health" >/dev/null 2>&1 || { echo "  OV down, skip"; exit 0; }
base="viking://user/default/resources/gh-issues/$(printf '%s' "$slug" | tr '/' '_')"
printf '%s' "$fresh" | jq -c '.[]' | while IFS= read -r it; do
  n=$(printf '%s' "$it" | jq -r .number)
  body=$(printf '%s' "$it" | jq -r '"# \(.title)\n\nrepo: '"$slug"' · issue #\(.number) · state: \(.state) · labels: \([.labels[].name]|join(", ")) · updated: \(.updatedAt)\nurl: \(.url)\n\n\(.body // "" | .[0:6000])"')
  payload=$(jq -cn --arg u "$base/issue-$n.md" --arg c "$body" --arg s "$(printf '%s' "$it" | jq -r .state)" \
    '{uri:$u, content:$c, mode:"replace", wait:false, tags:["kind=gh-issue","repo='"$slug"'","state="+$s], tag_mode:"replace"}')
  curl -s -m 5 -X POST "$OV_URL/api/v1/content/write" -H 'Content-Type: application/json' -d "$payload" >/dev/null 2>&1
done
echo "  pushed to OV: $base"
