# 입력: -s 로 [저장소 Codex 훅, 현재 hooks.json]. 우리 항목(claude-compat.py)만 제자리에서 갈고 나머지는 그대로 둠.
# Codex 는 훅 신뢰를 '파일:사건:순번'으로 기억하므로 남의 항목 순서를 옮기면 신뢰가 풀림
def ours: (.hooks // []) as $h | ($h | length) > 0 and ($h | all((.command // "") | contains("/claude-compat.py")));

.[0].hooks as $b | .[1] as $cur
| $cur + { hooks: (
    reduce ((($cur.hooks // {}) | keys_unsorted) + ($b | keys_unsorted) | reduce .[] as $k ([]; if index([$k]) then . else . + [$k] end))[] as $e ({};
      ($cur.hooks[$e] // []) as $g | ($b[$e] // []) as $new
      | ([$g | to_entries[] | select(.value | ours) | .key][0]) as $at
      | (if $at == null then $g + $new
         else [$g | to_entries[] | if .key == $at then $new[] elif (.value | ours) then empty else .value end] end) as $out
      | if ($out | length) > 0 then .[$e] = $out else . end)
  ) }
