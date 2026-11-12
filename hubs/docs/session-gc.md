# 주기 세션 정리 (session_gc)

## 쉬운 말 3줄 요약

- 무엇이 생겼나: 30분마다 같은 허브에 뜬 코디네이터 창 중복과, 끝난 지 30분 넘은 워커 창을 찾는다. 기본은 목록만 보여주고 아무것도 닫지 않는다.
- 왜: 재시작 뒤 코디네이터 창이 2개 뜨고, 끝난 워커 창이 몇 시간씩 남아 메모리를 먹었다.
- 사용자가 할 일: 목록이 맞는지 로그로 며칠 본 뒤, 괜찮으면 `--apply` 로 켠다.

## 무엇을 하나

| 대상 | 조건 | 동작(`--apply` 일 때만) |
|---|---|---|
| 중복 코디네이터 | 같은 허브 폴더(`coord-*-hub`)에 연결된 claude 창 2개 이상 | 최근 출력이 있는 쪽 유지. 다른 창에 "HANDOFF 작성 후 종료" 요청 한 줄 전송. 직접 닫지 않음. 같은 창에는 6시간에 1번 |
| 끝난 워커 | 작업 성공 종료 + 입력 대기 + 30분 넘게 출력 없음 + 작업 공간 미커밋 0 + 대화 기록 보관 확인 | 소유 창은 `worker-release`, 채택된 창은 `terminal close` |

절대 건드리지 않는 것: 승인 대기 창(멈춘 워커 카드 담당), 사용자가 인수·유지한 창, 코디네이터·메인 창, 진행 중 워커와 같은 창, 실패·중단 워커. 조회가 실패하거나 판정이 안 되면 아무것도 하지 않는다. 첫 `--apply` 는 목록만 보여주고 다음 실행부터 동작한다.

## 확인 (병합 뒤)

```sh
python3 ~/.config/lrm-hubs/session_gc.py            # 시험 모드: 목록·근거만, orca 변경 0회
tail -n 30 ~/.config/lrm-hubs/session-gc.log        # 실행마다 1줄 이상
```

## 켜기

```sh
./install.sh --allow-live-diff
launchctl load ~/Library/LaunchAgents/ai.lrm.sessiongc.plist       # 시험 모드로 30분마다
# 실제 정리: plist 의 ProgramArguments 에 <string>--apply</string> 추가 → unload 후 다시 load
```

## 끄기·되돌리기

- 끄기: `launchctl unload ~/Library/LaunchAgents/ai.lrm.sessiongc.plist`
- 실제 정리만 끄기: plist 에서 `--apply` 줄 삭제 후 다시 load
- 처음부터 다시(첫 `--apply` 는 목록만): `session-gc.json` 삭제
- 닫힌 워커 출력은 `orca orchestration worker-read --dispatch <id>` 로 계속 읽힌다.
- 기준: `LRM_GC_IDLE_MIN`(분, 기본 30), `LRM_GC_HANDOFF_H`(시간, 기본 6), 로그 경로 `LRM_GC_LOG`
- 시험: `bash tests/session_gc/run.sh`
