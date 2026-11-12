# 진행 목록 영구판 런북

지금 진행 중인 일을 Slack 목록과 DM 으로 주기적으로 알려 주는 `progress_digest.py` 의 동작, 켜기·끄기·되돌리기를 적는다. 작성일은 2026-11-12 이다.

## 쉬운 말 3줄 요약

- 무엇이 바뀌었나: 평일 9~20시 매시 정각에 장부·허브 상태·내 PR 을 모아 Slack 목록(강비서 할 일)을 고치고 DM 한 줄을 보낸다. 직전과 내용이 같으면 아무것도 보내지 않는다.
- 왜: 진행 중인 일을 잊는 문제를 메인 세션 임시 크론이 대신해 왔고, 세션이 끝나면 사라진다.
- 할 일: 병합 뒤 허브가 설치·켠 다음, 메인 세션의 임시 크론(평일 9~20시 매시 DM)을 끈다(5절).

## 1. 흐름

```text
 장부 · 허브 상태 · 내 PR · 결정 대기
   │  panel_collect (90초)가 progress_collect.py 호출
   ▼
 ~/.config/lrm-hubs/progress.json   (스냅샷, 쓰는 쪽은 수집기 하나)
   ├─▶ 옆 패널(lrm-board): 허브별 '진행 중 / 답 대기 / 막힘'
   └─▶ progress_digest.py (launchd 매시 정각)
         ├─ 직전 해시와 같음 → 종료(전송 0)
         ├─ Slack 목록 갱신(이 도구가 만든 행만)
         └─ DM 한 줄
```

## 2. 분류 규칙

| 묶음 | 기준 |
| --- | --- |
| 진행 중 | 장부 접수·진행, 허브 작업, 열린 PR |
| 답 대기 | 문구에 `병합 대기`·`승인 대기`·`답 대기`·`결정 대기`·`사용자 실행 대기`·`사용자 답`·`사용자 확인` 포함, 승인된 PR, 안 닫힌 결정 카드 |
| 막힘 | 장부 보류, 허브 차단 줄, 변경 요청·충돌 PR |

- 허브 상태가 24시간 넘으면 제목에 `[낡음]` 을 붙인다.
- PR 조회가 실패하면 알림에 `PR 조회 실패` 한 줄만 남기고 계속한다. 직전 PR 값은 유지하므로 PR 행이 완료로 바뀌지 않는다.
- 목록 행은 이 도구가 만든 것만 고친다. 손으로 넣은 행은 건드리지 않는다. 끝난 항목은 삭제하지 않고 완료 칸을 체크한다.
- DM 은 `진행 중 5 · 답 대기 2 · 막힘 1 — 목록 열기` 와 답 대기·막힘 이름(최대 3개, 넘으면 `외 N`)이다.

## 3. 확인

```sh
python3 ~/.config/lrm-hubs/progress_digest.py --dry-run        # 전송 없이 결과만
python3 ~/.config/lrm-hubs/progress_collect.py --dry-run       # 스냅샷 JSON 만
python3 ~/.config/lrm-hubs/progress_digest.py --help
```

종료 코드: 0 성공(변화 없음 포함), 1 목록 갱신·DM 실패(다음 회차 재시도), 2 설정 오류.

## 4. 켜기

전제: 봇 토큰에 목록 쓰기 권한(`lists:write`, 읽기는 `lists:read`)이 있어야 한다. 현재 `hubs/slack-bot-manifest.yml` 에 없으므로 앱 권한을 추가하고 재설치한다. 권한이 없으면 목록 갱신만 실패하고(`missing_scope`) DM 은 보낸다. 실패한 회차는 다음 회차에 다시 시도한다.

```sh
./install.sh                         # 스크립트·plist 배치
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.progressdigest.plist
launchctl kickstart gui/$(id -u)/ai.lrm.progressdigest   # 바로 1회 실행
```

## 5. 끄기·되돌리기

```sh
launchctl bootout gui/$(id -u)/ai.lrm.progressdigest
rm ~/.config/lrm-hubs/progress-digest.state.json      # 선택: 상태 초기화(다시 켜면 행이 한 번 더 생김)
```

- 목록에 이 도구가 만든 행은 `🔄·⏳·🚧` 로 시작한다. 되돌릴 때 직접 지우거나 완료 체크로 둔다.
- 패널만 되돌리려면 `progress.json` 을 지우면 된다. 패널은 예전 손 칸으로 돌아간다.
- **임시 크론 끄기**: 이 도구가 켜지면 메인 세션의 임시 크론(평일 9~20시 매시 DM)을 끈다. 둘이 같이 돌면 DM 이 두 번 온다. 메인에게 "진행 목록 영구판이 켜졌으니 임시 크론 삭제" 라고 전달한다.

## 6. 환경 변수

| 이름 | 뜻 |
| --- | --- |
| `LRM_HUB_DIR` | 작업 폴더(기본 `~/.config/lrm-hubs`) |
| `LRM_PROGRESS` | 스냅샷 경로 |
| `LRM_DIGEST_REPOS` | PR 조회 저장소(쉼표). 비면 `PANEL_GH_REPOS`(환경 변수 → 작업 폴더 `panel.env`), 그것도 없으면 실행 위치의 저장소 |
| `LRM_DIGEST_LIST_ID`·`LRM_DIGEST_LIST_URL` | Slack 목록 |
| `SLACK_API_BASE` | API 주소(시험용). `LRM_DIGEST_LOCAL_ONLY=1` 이면 로컬 외 주소는 요청 전에 실패 |

## 7. 제안(이번엔 구현 안 함)

- 상태가 바뀐 즉시 반영하려면 `slack_poll.sh` 회차 끝에 `progress_digest.py --quiet` 를 호출하면 된다. 해시 비교로 변화가 없으면 전송이 없어 중복 부담이 작다. 폴러 파일은 다른 브랜치 소관이라 보류했다.
- 장부 쪽의 `ledger_sync.py` 진행 스레드와 목록 행의 연결(링크 칸)은 아직 없다.
