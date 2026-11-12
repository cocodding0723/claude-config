#!/bin/bash
# 가짜 claude. --model 값을 FAKE_DIR/claude.log 에 기록하고 FAKE_DIR/plan-<모델> 내용(fail|bad|empty|extra|ok)대로 동작
m=""; while [ $# -gt 0 ]; do [ "$1" = --model ] && m="$2"; shift; done
cat > /dev/null
echo "$m" >> "${FAKE_DIR:?}/claude.log"
N="${FAKE_NAME:?}"
case "$(cat "$FAKE_DIR/plan-$m" 2>/dev/null || echo ok)" in
  noatl) printf '# %s\n\n- **주제 A**\n    - 일 하나\n- **막힌 것**: 없음\n\n(참고: 설명 문장)\n@@atlassian-unavailable@@\n' "$N" ;;
  fail) echo "usage limit reached" >&2; exit 1 ;;
  empty) exit 0 ;;
  bad) echo "초안을 못 씁니다" ;;
  extra) printf '머리말\n# %s\n\n- **주제 A** (진행 중)\n    - 일 하나\n\n# 타인\n\n- 남의 칸\n' "$N" ;;
  *) printf '# %s\n\n- **주제 A** (진행 중)\n    - [LRM-1](https://x/LRM-1) 일 하나\n    - 오늘: 확인\n- **막힌 것**: 없음\n' "$N" ;;
esac
