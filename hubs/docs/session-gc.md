# 주기 세션 정리 (session_gc)

> 실제 정리 모드는 2026-11-13 사용자 승인(Slack 결정 카드 D-CFGAPPLY '1. 전부 적용')으로 plist 에 `--apply` 가 들어갔다. 작업 공간 정리기(ws_gc)도 같은 승인으로 `--apply` 유지(`hubs/docs/ws-gc.md`).

## 쉬운 말 3줄 요약

- 무엇이 생겼나: 30분마다 끝난 워커 창을 찾는다. 기본은 목록만 보여주고 아무것도 닫지 않는다. `--apply` 로 켜도 자동으로 하는 일은 `worker-release` 하나뿐이다.
- 왜: 재시작 뒤 끝난 워커 창이 몇 시간씩 남아 메모리를 먹었다. 닫으면 안 되는 창을 닫는 사고를 막으려고 범위를 줄였다.
- 사용자가 할 일: 목록이 맞는지 로그로 며칠 본 뒤, 괜찮으면 `--apply` 로 켠다. `사람 확인` 줄은 직접 판단해 닫는다.

## 무엇을 하나

| 구분 | 조건 | 동작 |
|---|---|---|
| release 대상 | Orca 감독 워커(dispatch 있음) + 작업이 completed/failed 로 끝남 + Orca 가 터미널을 소유 + 입력 칸이 비어 있음(승인 대기·쓰던 글 없음) + 30분 넘게 출력 없음 + 작업 공간 미커밋·미추적 0 + 대화 기록 보관 확인 | `--apply` 일 때만 `orchestration worker-release`. 실행 직전에 그 한 건만 다시 조회해 하나라도 달라졌으면 건너뜀 |
| 사람 확인 | 위와 같지만 소유권 불명·없음, 사용자 인수·유지 표식이 있는 창 | 목록·로그에만 표시. 자동으로 닫지 않음 |
| 중복 코디네이터 | 같은 허브 폴더(전체 경로 기준)에 연결된 claude 창 2개 이상. 워커·메인 창은 세지 않음 | 목록·로그에만 표시. 전송·닫기 없음(멈춘 워커 카드가 이미 알림) |

- dispatch 없는 창, `orca terminal close` 대상은 이 도구가 다루지 않는다.
- 닫지 않는 것: 승인 대기 창(멈춘 워커 카드 담당), 쓰던 글이 있는 창, 코디네이터·메인 창, 진행 중 워커와 같은 창, 종료 보고 없이 멈춘 워커.
- 조회 실패·조회 불완전(쪽 상한에 닿고 더 남음)·판정 불가면 그 회차는 아무것도 하지 않는다(종료 코드 1).
- 첫 `--apply` 는 목록만 보여주고 다음 실행부터 동작한다.
- 로그·화면의 제목·사유에서 비밀 값은 `[가림]` 으로 바뀐다.

## 확인 (병합 뒤)

```sh
python3 ~/.config/lrm-hubs/session_gc.py            # 시험 모드: 목록·근거만, orca 변경 0회
tail -n 30 ~/.config/lrm-hubs/session-gc.log        # 실행마다 1줄 이상
```

## 켜기

```sh
./install.sh --allow-live-diff
launchctl load ~/Library/LaunchAgents/ai.lrm.sessiongc.plist       # 실제 정리 모드(--apply)로 30분마다
# 시험 모드로 되돌리기: plist 의 <string>--apply</string> 줄 삭제 → unload 후 다시 load
# python 은 설치 때 확인한 절대 경로(__PYTHON3__)이며 3.10 이상이어야 한다. 낮으면 실행 시 안내 문구와 종료 코드 2
```

## 끄기·되돌리기

- 끄기: `launchctl unload ~/Library/LaunchAgents/ai.lrm.sessiongc.plist`
- 실제 정리만 끄기: plist 에서 `--apply` 줄 삭제 후 다시 load
- 처음부터 다시(첫 `--apply` 는 목록만): `session-gc.json` 삭제
- 닫힌 워커 출력은 `orca orchestration worker-read --dispatch <id>` 로 계속 읽힌다.
- 기준: `LRM_GC_IDLE_MIN`(분, 기본 30), 조회 쪽 수 `LRM_STALL_PAGES`(기본 5), 로그 경로 `LRM_GC_LOG`
- 시험: `bash tests/session_gc/run.sh`
