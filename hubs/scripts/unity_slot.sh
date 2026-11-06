#!/bin/bash
# Unity 동시 실행 슬롯(최대 1개, LRM_UNITY_SLOTS 로 조정). 대기표 순서대로 배정.
# 사용: unity_slot.sh acquire <작업이름>   → 슬롯 번호 출력(블록)
#       LRM_SLOT_PRIORITY=1 이면 대기열 맨 앞에 선다(사용자 최우선 작업만). 실행 중 슬롯은 뺏지 않음
#       unity_slot.sh release <슬롯번호>
#       unity_slot.sh status
set -u
ROOT="${LRM_UNITY_SLOT_DIR:-/tmp/lrm-unity-slots}"
MAX="${LRM_UNITY_SLOTS:-1}"
mkdir -p "$ROOT/queue"

alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

reap() {
  for d in "$ROOT"/slot-*; do
    [ -d "$d" ] || continue
    pid=$(cat "$d/pid" 2>/dev/null)
    alive "$pid" || rm -rf -- "${d:?}"
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
