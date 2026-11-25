#!/bin/bash
# Unity 동시 실행 슬롯(기본 2개, LRM_UNITY_SLOTS 로 조정). 대기표 순서대로 배정. 11-16 에 1개로 줄인 것은 예전 맥(48GB) 메모리 사고 때문. 11-25 새 맥(M5 Max 18코어·64GB)에서 스왑 0·CPU 유휴 35~49%(순간 8%)를 재고 2개로 올림. 호출은 unity-run 경유
# 사용: unity_slot.sh acquire <작업이름>   → 슬롯 번호 출력(블록)
#       LRM_SLOT_PRIORITY=1 이면 대기열 맨 앞에 선다(사용자 최우선 작업만). 실행 중 슬롯은 뺏지 않음
#       unity_slot.sh release <슬롯번호>
#       unity_slot.sh status
set -u
ROOT="${LRM_UNITY_SLOT_DIR:-/tmp/lrm-unity-slots}"
MAX="${LRM_UNITY_SLOTS:-2}"
mkdir -p "$ROOT/queue"

alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

reap() {
  for d in "$ROOT"/slot-*; do
    [ -d "$d" ] || continue
    pid=$(cat "$d/pid" 2>/dev/null)
    # child: unity-run 이 기록한 Unity pid. 래퍼가 kill -9 로 죽어도 Unity 가 살아 있으면 유지
    alive "$pid" || alive "$(cat "$d/child" 2>/dev/null)" || rm -rf -- "${d:?}"
  done
  for q in "$ROOT"/queue/*; do
    [ -f "$q" ] || continue
    alive "$(cut -d' ' -f1 "$q")" || rm -f -- "$q"
  done
}

case "${1:-}" in
  acquire)
    name="${2:?작업 이름 필요}"
    owner="${LRM_SLOT_OWNER_PID:-$PPID}"
    prefix=""; [ "${LRM_SLOT_PRIORITY:-0}" = 1 ] && prefix="0-"
    ticket="$ROOT/queue/${prefix}$(date +%s%N)-$$"
    echo "$owner $name" > "$ticket"
    while :; do
      reap
      # 소유자가 죽어 표가 회수되면 대기 종료. 고아 대기 루프 방지
      [ -f "$ticket" ] || { echo "오류: 소유 pid $owner 종료로 대기표 회수. 다시 실행" >&2; exit 3; }
      first=$(ls "$ROOT/queue" | sort | head -1)
      if [ "$ROOT/queue/$first" = "$ticket" ]; then
        for i in $(seq 1 "$MAX"); do
          if mkdir "$ROOT/slot-$i" 2>/dev/null; then
            echo "$owner" > "$ROOT/slot-$i/pid"
            echo "$name" > "$ROOT/slot-$i/name"
            date '+%F %T' > "$ROOT/slot-$i/since"
            rm -f -- "$ticket"
            echo "$i"; exit 0
          fi
        done
      fi
      sleep 5
    done ;;
  release)
    n="${2:?슬롯 번호 필요}"; rm -rf -- "${ROOT:?}/slot-$n"; echo "slot-$n 해제" ;;
  status)
    reap
    for i in $(seq 1 "$MAX"); do
      d="$ROOT/slot-$i"
      [ -d "$d" ] && echo "slot-$i 사용 중: $(cat "$d/name") (pid $(cat "$d/pid"), $(cat "$d/since"))" || echo "slot-$i 비어 있음"
    done
    echo "대기: $(ls "$ROOT/queue" | wc -l | tr -d ' ')개"
    for q in $(ls "$ROOT/queue" | sort); do echo "  - $(cut -d' ' -f2- "$ROOT/queue/$q")"; done ;;
  *) echo "usage: $0 acquire <name> | release <n> | status"; exit 2 ;;
esac
