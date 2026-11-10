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
case_run P8 "결정 대기에서 반응 제외 규칙 제거" 's/done = \{t for t in reactions if t not in cards\} \| /done = /'
case_run P12 "쉬는 워크스페이스 20개 제한 제거" 's/for w in quiet\[:IDLE_LIST_MAX\]:/for w in quiet:/'
case_run P13 "디스크 경고 기준 뒤집음" 's/"warn": free < min_gb/"warn": free > min_gb/'
case_run P14 "쉬는 자원 부분 실패 시 이전 값 삭제" 's/            errs\.append\(f"\{name\}:\{error_code\(e\)\}"\)\n            value\[name\] = old_val\.get\(name\)/            errs.append(f"{name}:{error_code(e)}")\n            value[name] = None/'
case_run P15 "정리 명령 끼워 넣기(워크트리 삭제)" 's/(def merged\(path, base\):\n)/$1    _cleanup = ["worktree", "rm"]\n/'
# 리뷰 변이 4건(M2·M5a·M5c·M11)과 이번 수정이 막는 결함
case_run P17 "원자 교체를 직접 쓰기로(임시 파일 내용을 결과 파일에 복사)" 's/        os\.replace\(tmp, OUT\)\n/        with open(tmp, encoding="utf-8") as src, open(OUT, "w", encoding="utf-8") as dst:\n            dst.write(src.read())\n        os.remove(tmp)\n/'
case_run P18 "쉬는 워크스페이스에 os.remove 삭제 추가" 's/(    for w in quiet\[:IDLE_LIST_MAX\]:\n)/$1        if w.get("cleanup"):\n            os.remove(str(w.get("path")))\n/'
case_run P19 "쉬는 워커에 os.kill 추가" 's/(            if str\(w\.get\("terminalState"\) or ""\) in LEFT_TERMINAL:\n)/$1                if w.get("pid"):\n                    os.kill(int(w["pid"]), 15)\n/'
case_run P20 "쓰기 실패 시 기존 파일 삭제" 's/(        except OSError:\n)(            log\("write fail"\))/$1            os.remove(OUT)\n$2/'
case_run P21 "worker-list 첫 쪽만 셈(쪽 넘기기 제거)" 's/            if not page\.get\("hasMore"\) or not isinstance\(cursor, str\) or not cursor:/            if True:/'
case_run P22 "worker-list 쪽 수 상한 제거(끝없는 커서에 멈추지 않음)" 's/for n in range\(WORKER_PAGES_MAX\):/for n in range(10 ** 9):/'
case_run P23 "번호 카드 보류를 닫힘으로" 's/return "hold" if keys == \["hold"\] else "closed"/return "closed"/'
case_run P24 "번호 카드를 옛 규칙으로(반응 있으면 닫힘)" 's/done = \{t for t in reactions if t not in cards\} \| /done = set(reactions) | /'
case_run P25 "du 시간 초과 때 자식 묶음 정리 제거" 's/            os\.killpg\(p\.pid, signal\.SIGKILL\)/            p.kill()/'
case_run P26 "du 시간 초과를 표시하지 않음" 's/        return \{\*\*old, "err": e\.code, "tried": now\}/        return old/'
case_run P27 "원료 격리를 SourceError 만으로" 's/    except Exception as e:  # noqa: BLE001 — 원료 하나의/    except SourceError as e:  # noqa: BLE001 — 원료 하나의/'
case_run P28 "terminal list 잘림 무시" 's/return bool\(terminal_list\(get\)\.get\("truncated"\)\)/return False/'
case_run P29 "결과 파일 권한 0644" 's/os\.chmod\(tmp, 0o600\)/os.chmod(tmp, 0o644)/'
case_run P30 "글머리 '· …' 도 작업 중(회전 기호 + … 만 봄)" 's/\[A-Z\]\[a-z\]\+\(\?: \[a-z\]\+\)\?…/.*…/'
case_run P31 "실행 중 잠금 갱신 제거" 's/            os\.utime\(LOCK, None\)/            pass/'
# 재리뷰 미검출 R1·R8 과 잠금 주인 확인
case_run P35 "worker-list 쪽 넘기기 시간 상한 제거" 's/            if time\.monotonic\(\) - start > budget:\n                break\n//'
case_run P36 "번호 카드 결정 번호 형식 검증 제거(손상 번호도 번호 카드로)" 's/\n             and isinstance\(s\.get\("decision_id"\), str\) and DECISION_ID_RE\.fullmatch\(s\["decision_id"\]\)\}/}/'
case_run P37 "끝날 때 잠금 주인 대조 제거(남의 잠금도 지움)" 's/        if lock_owner\(\) == mine:\n/        if True:\n/'
case_run P38 "잠금 갱신에서 주인 대조 제거(남의 잠금도 갱신)" 's/        if lock_owner\(\) != mine:[^\n]*\n            return\n//'
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
board_run P32 "번호 카드 보류·충돌 표시 제거" 's/const state = p\.state \? /const state = false ? /'
board_run P33 "워커를 다 못 셌다는 표시 제거" 's/const part = w\.complete === false \?/const part = false ?/'
board_run P34 "du 시간 초과 표시 제거" "s/const note = s\.err === 'timeout'/const note = s.err === 'never' \&\& false/"
echo
[ "$MISSED" = 0 ] && echo "변이 전부 검출" || echo "미검출·정의 오류 ${MISSED}건"
[ "$MISSED" = 0 ]
