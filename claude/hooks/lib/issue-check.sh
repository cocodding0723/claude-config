#!/usr/bin/env bash
# On test failure: relate the failure to known failures, cached GitHub issues and OpenViking.
# issue_check REPO_HASH LOG SESSION_ID -> prints <=3 lines (may print nothing). Source after common.sh.

_fail_lines() { grep -aE 'FAIL|FAILED|Failed|✗|Error|Exception|Expected|panic:' "$1" | grep -avE '^\s*(at |#[0-9])' | head -5; }

issue_check() {
  local rh=$1 log=$2 sid=$3 rd sig key kf n out="" q
  rd=$(repo_dir "$rh")
  sig=$(_fail_lines "$log" | head -1 | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//' | cut -c1-160)
  [ -z "$sig" ] && return 0
  key=$(printf '%s' "$sig" | sed -E 's/[0-9]+//g; s/0x[0-9a-f]+//g' | shasum | cut -c1-10)

  # 1) local known-failures ledger (per repo)
  kf="$rd/known-failures.json"; [ -f "$kf" ] || echo '{}' > "$kf"
  n=$(jq -r --arg k "$key" '.[$k].count // 0' "$kf")
  if [ "$n" -gt 0 ]; then
    out="이 실패 시그니처는 이전에 ${n}회 발생 (첫 발생 $(jq -r --arg k "$key" '.[$k].first' "$kf"))"
  fi
  jq --arg k "$key" --arg s "$sig" --arg d "$(date +%F)" --arg sid "$sid" \
    '.[$k] |= (. // {sig:$s, first:$d, count:0, sessions:[]}) | .[$k].count += 1 | .[$k].last=$d | .[$k].sessions = ((.[$k].sessions + [$sid]) | unique | .[-5:])' \
    "$kf" > "$kf.tmp" && mv "$kf.tmp" "$kf"

  # 2) cached GitHub issues (synced by issue-sync.sh) — keyword overlap on title/body
  if [ -f "$rd/issues.json" ]; then
    local kws hit
    kws=$(printf '%s' "$sig" | tr -c '[:alnum:]_' '\n' | awk 'length($0)>4' | sort -u | head -6 | tr '\n' ' ')
    hit=$(jq -r --arg kws "$kws" '
      ($kws | split(" ") | map(select(length>0))) as $k
      | [ .[] | . as $i | ($i.title + " " + ($i.body//"")) | ascii_downcase as $t
          | {n:$i.number, s:$i.state, t:$i.title, u:$i.url, score:([ $k[] as $kw | select($t | contains($kw | ascii_downcase)) ] | length)} ]
      | map(select(.score>0)) | sort_by(-.score) | .[:2][] | "관련 이슈 #\(.n) (\(.s)) \(.t) — \(.u)"' "$rd/issues.json" 2>/dev/null)
    [ -n "$hit" ] && out="$out${out:+
}$hit"
  fi

  # 3) OpenViking semantic search over synced issues (best effort, 3s)
  q=$(jq -cn --arg q "$sig" '{query:$q, limit:2, target_uri:"viking://user/default/resources/gh-issues"}')
  hit=$(curl -s -m 3 -X POST "$OV_URL/api/v1/search/search" -H 'Content-Type: application/json' -d "$q" 2>/dev/null \
        | jq -r '.result.resources // [] | map(select(.uri | test("/\\.[^/]*$") | not)) | .[:1][] | "OV 유사 이슈: \(.uri) — \((.abstract // "") | .[0:120])"' 2>/dev/null | head -1)
  [ -n "$hit" ] && out="$out${out:+
}$hit"
  [ -n "$out" ] && printf '%s\n' "$out" | head -3
  return 0
}
