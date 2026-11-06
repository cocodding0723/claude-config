# 입력: -s 로 [저장소 기본값, Orca 항목]. 훅 배열은 덮어쓰지 않고 순서를 유지한 채 이어붙임
# (jq '.[0] * .[1]' 은 배열을 덮어써 훅을 잃음)
def dd: reduce .[] as $i ([]; if index([$i]) then . else . + [$i] end);

.[0] as $a | .[1] as $b | ($a * $b)
| .hooks = (
    ($a.hooks // {}) as $x | ($b.hooks // {}) as $y
    | reduce ((($x | keys_unsorted) + ($y | keys_unsorted)) | dd[]) as $k
        ({}; .[$k] = ((($x[$k] // []) + ($y[$k] // [])) | dd))
  )
