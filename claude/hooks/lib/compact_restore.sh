#!/usr/bin/env bash
# 압축 직후 코디네이터 허브(coord-…-hub)에 카드 상태를 다시 알림. 인자: cwd. 출력은 세션 문맥에 들어감(20줄 이내).
# 작업판 조회는 건당 2초 제한, 실패하면 안내 한 줄만 냄. 훅이 세션을 막지 않도록 항상 종료 0.
cwd=${1:-}
hub=$(basename "$cwd")
case "$hub" in coord-*-hub) ;; *) exit 0 ;; esac
BIN=${BOARD_BIN:-$HOME/orca/board/bin}
BDIR=${BOARD_DIR:-$HOME/orca/board}
cap() { perl -e 'alarm 2; exec @ARGV' "$@" 2>&1; }   # 2초 넘으면 SIGALRM 으로 종료
echo "[압축 직후] 방금 대화가 요약됐다. 세부는 카드 코멘트(bd comments <카드>)와 작업 스레드가 정본이다."
case "$hub" in
  coord-feat-*-hub)
    slug=${hub#coord-feat-}; slug=${slug%-hub}
    card=$(cd "$BDIR" 2>/dev/null && cap bd list --type epic --label "feature:$slug" --json | jq -r '.[0].id // empty' 2>/dev/null)
    if [ -z "$card" ]; then echo "기능 카드 조회 실패(feature:$slug). 카드 코멘트·역할 파일을 다시 읽을 것."; exit 0; fi
    echo "기능 카드: $card"
    st=$(BOARD_DIR="$BDIR" cap "$BIN/board-stage" "$card") && printf '%s\n' "$st" | head -6 || echo "단계 조회 실패. board-stage $card 로 확인할 것."
    said=$(BOARD_DIR="$BDIR" cap "$BIN/board-said" "$card" --check); rc=$?
    case $rc in
      0) echo "처리 안 된 지시: 없음" ;;
      6) printf '%s\n' "$said" | grep -m1 '^미처리' | sed 's/\. 조치.*//' | cut -c1-200 ;;
      *) echo "지시 조회 실패. board-said $card --check 로 확인할 것." ;;
    esac ;;
  *) echo "허브: $hub. 역할 파일·카드 코멘트를 다시 읽을 것." ;;
esac
exit 0
