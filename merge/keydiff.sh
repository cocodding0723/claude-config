#!/usr/bin/env bash
# 라이브 settings.json 과 저장소 기본값의 훅·상태줄 외 값 차이를 키 경로별 개수로만 출력(값은 출력하지 않음).
# 병합은 이 영역을 저장소 기본값으로 덮어쓰므로, 라이브에만 있는 값이 있으면 전환 전에 base 로 옮긴다.
# 종료 코드: 차이 없음 0, 있음 1, 입력 오류 2.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd -P)
LIVE="${1:-$HOME/.claude/settings.json}"
BASE="${2:-$REPO/claude/settings.base.json}"
[ -f "$LIVE" ] && [ -f "$BASE" ] || { echo "ERROR: 파일 없음 ($LIVE, $BASE). 인자: keydiff.sh [라이브 settings.json] [base.json]" >&2; exit 2; }

out=$(jq -n --slurpfile L "$LIVE" --slurpfile B <(sed "s#__HOME__#$HOME#g" "$BASE") '
  def leaves: del(.hooks, .statusLine) | [tostream | select(length == 2)];
  def keyname: .[0] | map(select(type == "string")) | join(".");
  def count_by_key: map(keyname) | group_by(.) | map({"key": .[0], "count": length});
  ($L[0] | leaves) as $l | ($B[0] | leaves) as $b
  | { "live_only": ([$l[] | select(. as $x | $b | index([$x]) | not)] | count_by_key),
      "repo_only": ([$b[] | select(. as $x | $l | index([$x]) | not)] | count_by_key) }')
printf '%s\n' "$out"
printf '%s' "$out" | jq -e '(.live_only | length) + (.repo_only | length) == 0' >/dev/null
