#!/usr/bin/env bash
# 패널 수집기 변이 검증: 수집기 사본에 결함을 하나씩 심고 tests/panel/run.sh 가 실패하는지 확인함.
# 원본 작업 트리는 바꾸지 않고 임시 사본(mktemp -d)에서만 실행. ONLY="P1 P3" 처럼 일부만 실행 가능. 검출 못 한 변이가 있으면 종료 코드 1.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/cc-panelmut.XXXXXX")
trap 'rm -rf "${WORK:?}"' EXIT
MISSED=0

case_run() { # 번호 설명 perl 치환식(panel_collect.py 대상)
  local id="$1" desc="$2" expr="$3" out rc first
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $id "* ]]; then return; fi
  rm -rf "${WORK:?}/scripts"
  cp -R "$REPO/hubs/scripts" "$WORK/scripts"
  cp "$WORK/scripts/panel_collect.py" "$WORK/before"
  perl -0pi -e "$expr" "$WORK/scripts/panel_collect.py"
  if cmp -s "$WORK/before" "$WORK/scripts/panel_collect.py"; then printf '| %s | %s | 정의 오류 | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); return; fi
  out=$(COLLECT_DIR="$WORK/scripts" bash "$REPO/tests/panel/run.sh" 2>&1); rc=$?
  first=$(echo "$out" | grep '^FAIL' | head -1 | sed 's/^FAIL  //' | cut -c1-70)
  if [ "$rc" != 0 ]; then printf '| %s | %s | 검출 | %s |\n' "$id" "$desc" "$first"
  else printf '| %s | %s | **미검출** | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); fi
}

echo '| 변이 | 내용 | 결과 | 먼저 실패한 검사 |'
echo '|---|---|---|---|'
case_run P1 "원자 교체 제거(결과 파일에 바로 씀)" 's/fd, tmp = tempfile\.mkstemp\(dir=DIR, prefix="\.panel-auto\.", suffix="\.tmp"\)/fd, tmp = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC), OUT/'
case_run P2 "메인 panel.json 에 씀" 's/OUT = os\.path\.join\(DIR, "panel-auto\.json"\)/OUT = os.path.join(DIR, "panel.json")/'
case_run P3 "gh 간격 스킵 제거(매번 호출)" 's/return now - os\.path\.getmtime\(GH_STAMP\) >= interval/return True/'
case_run P4 "실패 시 이전 값 삭제" 's/"stale": True, "value": old\.get\("value"\)\}/"stale": True, "value": None}/'
case_run P5 "화면 본문(preview) 저장" 's/("connected": connected,\n)/$1            "preview": t.get("preview"),\n/'
case_run P6 "잠금 제거(항상 획득)" 's/            os\.mkdir\(LOCK\)\n            return True/            return True/'
case_run P7 "작업 중 판정에서 '…' 조건 제거(끝난 턴도 작업 중)" 's/\(\?: \[a-z\]\+\)\?…"\)/(?: [a-z]+)?")/'
case_run P8 "결정 대기에서 반응 제외 규칙 제거" 's/done = \{d\.get\("parent_ts"\) for d in inbox if d\.get\("type"\) == "reaction"\}/done = set()/'
case_run P12 "쉬는 워크스페이스 20개 제한 제거" 's/for w in quiet\[:IDLE_LIST_MAX\]:/for w in quiet:/'
case_run P13 "디스크 경고 기준 뒤집음" 's/"warn": free < min_gb/"warn": free > min_gb/'
case_run P14 "쉬는 자원 부분 실패 시 이전 값 삭제" 's/            errs\.append\(f"\{name\}:\{error_code\(e\)\}"\)\n            value\[name\] = old_val\.get\(name\)/            errs.append(f"{name}:{error_code(e)}")\n            value[name] = None/'
case_run P15 "정리 명령 끼워 넣기(워크트리 삭제)" 's/(def merged\(path, base\):\n)/$1    _cleanup = ["worktree", "rm"]\n/'
board_run() { # 번호 설명 perl 치환식(mod board.ts 대상, node 시험)
  local id="$1" desc="$2" expr="$3" out rc first
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $id "* ]]; then return; fi
  mkdir -p "$WORK/mod/hooks" && cp "$REPO/hubs/mods/lrm-board/hooks/board.ts" "$WORK/mod/hooks/board.ts"
  perl -0pi -e "$expr" "$WORK/mod/hooks/board.ts"
  if cmp -s "$REPO/hubs/mods/lrm-board/hooks/board.ts" "$WORK/mod/hooks/board.ts"; then printf '| %s | %s | 정의 오류 | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); return; fi
  out=$(BOARD_TS="$WORK/mod/hooks/board.ts" node --test "$REPO/tests/panel/board.test.mjs" 2>&1); rc=$?
  first=$(echo "$out" | grep -m1 '^✖' | sed 's/^✖ //' | cut -c1-70)
  if [ "$rc" != 0 ]; then printf '| %s | %s | 검출 | %s |\n' "$id" "$desc" "$first"
  else printf '| %s | %s | **미검출** | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); fi
}
board_run P9 "오래됨 기준 10분 → 60분" 's/AUTO_STALE_MIN = 10/AUTO_STALE_MIN = 60/'
board_run P10 "자동 칸 시계를 수정 시각 우선으로" 's/if \(epochMs !== undefined && epochMs > 0 && epochMs <= nowMs \+ FUTURE_SLACK_MS\) return epochMs/if (mtimeMs > 0) return mtimeMs/'
board_run P11 "파일 없음 경고 제거" "s/if \(autoMtimeMs === 0\) return \{ level: 'missing'/if (false) return { level: 'missing'/"
board_run P16 "디스크 경고 색 표시 제거" 's/warn: d\.warn \}\)/warn: false })/'
echo
[ "$MISSED" = 0 ] && echo "변이 전부 검출" || echo "미검출·정의 오류 ${MISSED}건"
[ "$MISSED" = 0 ]
