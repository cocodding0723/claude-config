# 재시작 감지 카드 (restart_watch)

## 쉬운 말 3줄 요약

- 무엇이 생겼나: Orca 나 맥이 다시 시작되면 Slack 에 "Orca 재시작 감지" 카드가 1장 온다. 진행 작업이 있던 허브 목록이 들어 있다.
- 왜: 11-12 에 4번 재시작됐고, 멈춘 허브를 사람이 하나씩 깨워야 했다.
- 사용자가 할 일: 카드에서 `이어받기`를 누르면 그 목록의 허브만 다시 뜬다. `보류`는 아무것도 안 한다.

## 동작

```text
slack_socket.py (상주, 120초마다) ─▶ restart_watch.py check
   orca status 의 runtimeId·pid·시작 시각을 저장값과 비교
   ├ 첫 실행: 기록만(카드 없음)
   ├ 조회 실패: "판정 불가"로 기록만(재시작 아님)
   └ 바뀜: 대상 허브(상태 파일에 진행 작업, 24시간 안 갱신)로 카드 1장
          같은 재시작은 한 번만. 전송 실패면 다음 주기에 재시도
카드 이어받기 ─▶ card_actions.py ─▶ resume_all.sh --only <허브>...   (메인에는 전달 안 함)
```

- 상태 파일: `~/.config/lrm-hubs/restart-watch.json`, 대기 중 카드: `card-actions.json`
- 새 launchd 는 없다. 기존 `ai.lrm.slacksocket` 안에서 돈다.

## 설치·확인 (병합 뒤)

```sh
./install.sh --allow-live-diff
launchctl kickstart -k gui/$(id -u)/ai.lrm.slacksocket
python3 ~/.config/lrm-hubs/restart_watch.py check --dry-run   # 판정만 보기
```

## 끄기·되돌리기

- 끄기: `ai.lrm.slacksocket` 의 환경 변수 `LRM_SOCKET_WATCH_S=0` 으로 감시 전체를 끈다. 재시작 감지만 끄려면 `slack_socket.py` 의 `WATCHERS` 에서 `restart_watch.py` 를 뺀다.
- 주기 변경: `LRM_SOCKET_WATCH_S`(초, 기본 120).
- 처음부터 다시: `restart-watch.json` 을 지우면 다음 실행이 첫 실행으로 기록만 한다.
- 시험: `bash tests/restart_watch/run.sh`
