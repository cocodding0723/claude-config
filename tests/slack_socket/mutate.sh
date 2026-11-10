#!/usr/bin/env bash
# 수신기 연결 감시 변이 검증: 수신기 사본에 결함을 하나씩 심고 tests/slack_socket/run.sh 가 실패하는지 확인함.
# 원본 작업 트리는 바꾸지 않고 임시 사본(mktemp -d)에서만 실행. ONLY="W1 W3" 처럼 일부만 실행 가능. 검출 못 한 변이가 있으면 종료 코드 1.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1

HERE=$(cd "$(dirname "$0")" && pwd -P)
SRC="$HERE/../../hubs/scripts/slack_socket.py"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/cc-sockmut.XXXXXX")
trap 'rm -rf "${WORK:?}"' EXIT
MISSED=0

case_run() { # 번호 설명 perl 치환식
  local id="$1" desc="$2" expr="$3" out rc first
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $id "* ]]; then return; fi
  cp "$SRC" "$WORK/mut.py"
  perl -0pi -e "$expr" "$WORK/mut.py"
  if cmp -s "$SRC" "$WORK/mut.py"; then printf '| %s | %s | 정의 오류 | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); return; fi
  out=$(SOCK_SCRIPT="$WORK/mut.py" bash "$HERE/run.sh" 2>&1); rc=$?
  first=$(echo "$out" | grep '^FAIL' | head -1 | sed 's/^FAIL  //' | cut -c1-70)
  if [ "$rc" != 0 ]; then printf '| %s | %s | 검출 | %s |\n' "$id" "$desc" "$first"
  else printf '| %s | %s | **미검출** | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); fi
}

echo '| 변이 | 내용 | 결과 | 먼저 실패한 검사 |'
echo '|---|---|---|---|'
case_run W1 "끊김 임계 비교 반전(>= → <)" 's/now - down_since >= timing\.down_s/now - down_since < timing.down_s/'
case_run W2 "끊김 재연결 호출 삭제" 's/(elif now - down_since >= timing\.down_s:\n\s+try:\n\s+)client = swap\(client, make\)/$1client = client/'
case_run W3 "5연속 실패 종료 삭제" 's/if fails >= MAX_FAILS:/if False:/'
case_run W4 "생존 로그에 값 추가(pid)" 's/(재연결=\{pulse\.reconnects\}회)"/$1 pid={os.getpid()}"/'
case_run W5 "정기 재연결 0 이어도 수행" 's/if up and down_since is None and timing\.refresh_s and /if up and down_since is None and /'
case_run W6 "생존 로그 주기 무시(매 점검)" 's/if now - last_alive >= timing\.alive_s:/if True:/'
case_run W7 "실패 로그에 예외 본문 포함" 's/재연결 실패 \{type\(err\)\.__name__\} \{fails\}/재연결 실패 {type(err).__name__} {err} {fails}/'
case_run W8 "봇 자신 이벤트 필터 제거" 's/event\.get\("bot_id"\) or \(me and/(me and/'
case_run W9 "옛 클라이언트 정리 삭제" 's/    retire\(old\)\n/    pass\n/'
case_run W10 "재연결 성공 뒤 실패 횟수 초기화 삭제" 's/down_since, fails, last_refresh = None, 0, now/down_since, last_refresh = None, now/'
case_run W11 "끊김 재연결 뒤 수집 트리거 삭제" 's/(                log\("재연결됨"\)\n)                trigger\.fire\("재연결"\)\n(            up = is_up)/$1$2/'
# 리뷰 지적(예외 없는 연결 실패·runner 누수·늦은 연결)과 기본값·폭주·리스너 변이
case_run W12 "연결 뒤 is_connected 확인 삭제(연결 안 됨을 성공으로 처리)" 's/    if not is_up\(client\):\n        raise ConnectionError\("연결 안 됨"\)\n//'
case_run W13 "첫 연결에서 연결 확인 생략" 's/        connect_checked\(client\)\n/        client.connect()\n/'
case_run W14 "옛 클라이언트 runner 종료 삭제" 's/    bounded\("runner", lambda: end_runner\(client\), RETIRE_TIMEOUT_S\)\n//'
case_run W15 "close 뒤 disconnect 재호출 삭제" 's/    quiet\("disconnect", lambda: client\.disconnect\(\)\)\n//'
case_run W16 "runner 종료 전 terminated 플래그 삭제(join 영구 대기)" 's/        state\.terminated = True[^\n]*\n/        pass\n/'
case_run W17 "실패한 새 클라이언트 정리 삭제" 's/        retire\(fresh\)\n        raise/        raise/'
case_run R1 "끊김 임계 기본값 60→600" 's/env_secs\("LRM_SOCKET_DOWN_S", 60\.0\)/env_secs("LRM_SOCKET_DOWN_S", 600.0)/'
case_run R2 "정기 재연결 기본 단위 오류(6*3600→6*60)" 's/6 \* 3600\.0/6 * 60.0/'
case_run R4 "SDK 자체 복구 때 실패 횟수 초기화 안 함" 's/down_since, fails = None, 0/down_since = None/'
case_run R5 "새 클라이언트에 수신 리스너 안 붙임" 's/    fresh = make\(\)\n/    fresh = make()\n    fresh.socket_mode_request_listeners.clear()\n/'
case_run R6 "정기 재연결 뒤 주기 갱신 삭제(매 점검 재연결 폭주)" 's/            last_refresh = now  # 실패해도/            pass  # 실패해도/'
# runner 정리 시간 상한(SDK join 영구 대기 대비)
case_run W18 "runner 정리 상한 제거(join 무기한)" 's/worker\.join\(limit_s\)/worker.join()/'
case_run W19 "runner 정리를 스레드 없이 직접 호출" 's/    worker\.start\(\)\n    worker\.join\(limit_s\)\n/    worker.run()\n/'
case_run W20 "시간 초과 로그 삭제" 's/        log\(f"정리 \{step\} 시간 초과 \{limit_s:g\}초"\)/        pass/'
case_run W21 "정리 상한 환경 변수 무시" 's/RETIRE_TIMEOUT_S = env_secs\("LRM_SOCKET_RETIRE_TIMEOUT_S", 5\.0\)/RETIRE_TIMEOUT_S = 5.0/'
case_run R7 "정리 상한 기본값 5→500" 's/"LRM_SOCKET_RETIRE_TIMEOUT_S", 5\.0/"LRM_SOCKET_RETIRE_TIMEOUT_S", 500.0/'
echo
[ "$MISSED" = 0 ] && echo "변이 전부 검출" || echo "미검출·정의 오류 ${MISSED}건"
[ "$MISSED" = 0 ]
