# 현재 설정에서 Orca 가 주입한 훅·상태줄만 추출. 식별 기준은 훅이 호출하는 ~/.orca/agent-hooks 경로
def orca: tostring | contains(".orca/agent-hooks");

{ hooks: ((.hooks // {}) | map_values(map(select(orca))) | with_entries(select(.value | length > 0))) }
+ (if ((.statusLine // null) | orca) then { statusLine } else {} end)
