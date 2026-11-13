#!/usr/bin/env bash
# 결정 카드 필수 항목 규칙(decide_card.py)과 저장소 안 자동 결정 카드(재시작·멈춘 워커·허브 분할)가 규칙을 통과하는지 시험.
# 읽기 전용: Slack·orca·작업 폴더를 쓰지 않음(카드 문구만 만들어 검사).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE/../../hubs/scripts" && exec python3 -I "$HERE/check.py"
