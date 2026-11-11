#!/bin/bash
# 가짜 claude. --model 을 기록. FAKE_FAIL_MODEL 과 같으면 실패. 입력에 '검증한다' 가 있으면 검증 응답, 아니면 리뷰 응답
m=""; while [ $# -gt 0 ]; do [ "$1" = --model ] && m="$2"; shift; done
echo "$m" >> "${FAKE_DIR:?}/models.log"
[ "$m" = "${FAKE_FAIL_MODEL:-}" ] && { echo "rate limit" >&2; exit 1; }
in=$(cat)
if printf '%s' "$in" | grep -q "검증한다"; then echo '{"verdicts":[{"index":0,"verdict":"CONFIRMED","reason":"널 경로 실재"},{"index":1,"verdict":"REJECTED","reason":"이미 막음"}]}'
else echo '결과: {"findings":[{"file":"a.cs","line":3,"severity":"high","title":"널 참조","scenario":"x 가 null","fix":"검사 추가"},{"file":"b.cs","line":9,"severity":"low","title":"가짜","scenario":"-","fix":"-"}]}'; fi
