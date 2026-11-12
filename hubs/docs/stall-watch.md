# 멈춘 워커 감시 카드 (stall_watch)

## 쉬운 말 3줄 요약

- 무엇이 생겼나: 종료 보고 없이 멈췄거나 30분 넘게 응답이 없는 워커가 생기면 Slack 에 카드 1장이 온다. 허브별로 한 줄씩 묶인다.
- 왜: 재시작 뒤 워커가 말없이 멈춰 있어도 아무도 몰랐다. 침묵은 워커가 죽었다는 증거가 아니라서 문구는 "응답 없음"이다.
- 사용자가 할 일: `담당 허브에 알리기`를 누르면 허브 코디네이터에 한 줄만 간다. `무시`는 아무것도 안 한다. 워커는 어느 쪽이든 건드리지 않는다.

## 대상

| 구분 | 조건 |
|---|---|
| 종료 보고 없이 멈춤 | 워커 stopped + 터미널 retained, 작업은 아직 열림 |
| 응답 없음 | 작업 진행 중인데 워커 관찰이 30분 넘게 갱신 없음(관찰 시각이 비어 있으면 제외) |
| 중복 코디네이터 줄 | 같은 허브 폴더(`coord-*-hub`)에 연결된 claude 터미널이 2개 이상. 카드 본문에 줄만 추가 |

- 허브 판별: 작업을 만든 터미널의 워크트리(`coord-<slug>-hub`) → `hubs.tsv`. 못 찾으면 "허브 미확인".
- 같은 dispatch 는 6시간 안에 다시 알리지 않는다(`stall-watch.json`).
- 첫 실행은 이미 멈춰 있던 옛 워커를 기준선으로만 기록한다(카드 없음). 옛 워커도 보고 싶으면 `check --report-existing` 을 한 번 실행한다.
- 알리기 동작: 코디네이터가 1개인 허브에는 한 줄 전송. 2개 이상이면 어느 쪽인지 몰라 허브에는 보내지 않고 메인에 중복 사실만 알린다. 어떤 창도 닫지 않는다.

## 설치·확인 (병합 뒤)

```sh
./install.sh --allow-live-diff
launchctl kickstart -k gui/$(id -u)/ai.lrm.slacksocket
python3 ~/.config/lrm-hubs/stall_watch.py check --dry-run   # 감지 결과만 보기(전송·저장 없음)
```

기존 `ai.lrm.slacksocket` 의 감시 루프(120초)에서 돈다. 새 launchd 는 없다.

## 끄기·되돌리기

- 이 감시만 끄기: `slack_socket.py` 의 `WATCHERS` 에서 `stall_watch.py` 삭제. 감시 전체는 `LRM_SOCKET_WATCH_S=0`.
- 기준 바꾸기: `LRM_STALL_SILENCE_MIN`(분, 기본 30), `LRM_STALL_DEDUPE_H`(시간, 기본 6).
- 처음부터 다시: `stall-watch.json` 삭제(다음 실행은 기준선만 기록).
- 시험: `bash tests/stall_watch/run.sh`
