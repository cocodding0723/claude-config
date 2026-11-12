# 스크럼 자동 초안 런북

평일 10:00 에 스크럼 회의록 본인 칸 초안을 claude 로 쓰고 Slack 봇 DM 1건으로 보내는 `scrum_run.sh` 의 흐름, 등록, 끄기, 되돌리기를 적는다. 작성일은 2026-11-12 이다.

## 쉬운 말 3줄 요약

- 무엇이 생겼나: 평일 아침 10시에 어제 한 일(PR·커밋·허브 보고)을 모아 회의록 형식의 본인 칸 초안을 만들고, 봇이 Slack 개인 DM 으로 보낸다. 초안 전체는 마크다운 첨부 파일이다.
- 왜: 스크럼 직전에 초안을 처음부터 쓰는 시간을 없앤다. Confluence 에는 쓰지 않는다. 사용자가 DM 첨부를 보고 직접 붙여 넣는다.
- 사용자가 할 일: 등록 승인 1건(2절)과 Atlassian 연결 인증 1회(4절). 승인 전에는 아무것도 자동으로 돌지 않는다.

## 1. 흐름

```text
 launchd (월~금 10:00. 자고 있던 Mac 은 깨어난 뒤 1회)
   │
   ▼
 scrum_run.sh ── caffeinate -i (실행 중 잠자기 방지) ──▶ scrum_post.py
   │  1 주말·공휴일이면 종료(공휴일은 ~/.config/lrm-hubs/scrum-holidays.txt, 없으면 평일만 검사)
   │  2 상태 파일에 오늘이 있으면 종료(같은 날 두 번 안 보냄)
   │  3 잠금 파일(scrum.lock). 이미 도는 실행이 있으면 종료
   ▼
 scrum_collect.py ─▶ gh PR · 워크스페이스 커밋 · 허브 보고 · HANDOFF · 최근 24시간 Slack 지시   (JSON)
   ▼
 claude -p  (fable-5-1 → fable-5-2 → opus-5-5 순. 양식 검증을 통과한 첫 결과 채택)
   │  Jira·Confluence 는 Atlassian 읽기 도구로 직접 읽음(쓰기 도구는 허용 안 함)
   ▼
 초안 파일 ~/Documents/90_정리기록/scrum-draft/YYYY-MM-DD.md
   ▼
 slack_notify.sh --kind doc 1건(요약 + 초안 첨부)       실패는 --kind warn 1건
```

## 2. 등록·끄기·되돌리기

등록은 병합 뒤 사용자 승인을 받고 한다. 이 문서의 명령은 승인 전에 실행하지 않는다.

```sh
# 0 파일 연결(링크·plist 복사). 원본 저장소에서
~/orca/claude-config/install.sh
# 1 본인 칸 제목 설정(회의록의 '# 제목' 칸). 예시는 scrum.env.example
cp ~/.config/lrm-hubs/scrum.env.example ~/.config/lrm-hubs/scrum.env   # 열어서 SCRUM_NAME 채움
# 2 launchd 등록
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.scrum.plist
launchctl print gui/$(id -u)/ai.lrm.scrum | head -5      # 등록 확인
```

끄기(스케줄만 멈춤. 초안·상태 파일은 그대로):

```sh
launchctl bootout gui/$(id -u)/ai.lrm.scrum
```

되돌리기(완전 제거):

```sh
launchctl bootout gui/$(id -u)/ai.lrm.scrum
rm ~/Library/LaunchAgents/ai.lrm.scrum.plist
rm -f ~/.config/lrm-hubs/scrum.state.json ~/.config/lrm-hubs/scrum.lock    # 상태 초기화(선택)
```

`pmset` 깨우기 예약은 쓰지 않는다. 사용자가 Mac 을 재우지 않기로 했다(2026-11-11). 10:00 에 자고 있었다면 깨어난 뒤 첫 실행 기회에 오늘 분을 쓰고, 다음 실행의 초안 메시지에 `경고: 어제 초안이 안 나갔음` 한 줄이 붙는다.

## 3. 수동 실행

| 목적 | 명령 |
|---|---|
| Slack 없이 초안만 보기 | `~/.config/lrm-hubs/scrum_run.sh --dry-run --date 2026-11-12` |
| 재료만 보기 | `python3 ~/.config/lrm-hubs/scrum_collect.py --date 2026-11-12` |
| 오늘 분 다시 보내기 | `~/.config/lrm-hubs/scrum_run.sh --force` |

`--dry-run` 은 상태 파일을 바꾸지 않고 Slack 으로 보내지 않는다. 초안은 표준 출력과 `<날짜>.dry.md` 에 남는다. 종료 코드는 0 성공·건너뜀, 1 초안 작성 또는 발송 실패, 2 인자·설정 오류, 3 환경 오류다.

## 4. 실패와 대응

| 증상 | 원인 | 대응 |
|---|---|---|
| Slack 에 `스크럼 초안 작성 실패` warn 1건 | 세 모델이 모두 실패(한도·인증·양식 불량) | 로그 `~/orca/archive/scrum_post.log` 의 `모델 실패` 줄에서 모델별 사유 확인 → `scrum_run.sh --force` |
| 초안에 `경고: Jira·Confluence 를 못 읽어` | Atlassian MCP 가 `needs-auth` 캐시 상태라 claude -p 가 연결을 건너뜀 | 대화형 `claude` 에서 `/mcp` → atlassian 인증 1회. 그동안은 PR·커밋·허브 보고만으로 초안이 나간다 |
| 발송이 3번 실패 | Slack API 오류(종료 코드 1만 30초·90초 뒤 재시도) | 초안 파일은 남고 로컬 알림이 뜬다. 다음 평일 재시도는 없다 |
| `본인 칸 제목 없음` | `scrum.env` 없음 | 2절 1번 |

claude 는 `USER` 로 키체인 로그인 항목을 찾는다. 래퍼가 `USER`·`LOGNAME` 이 비어 있으면 채워 두므로 launchd 의 짧은 환경에서도 로그인이 풀리지 않는다. plist 는 `HOME`·`PATH`(claude·gh·python3 위치 포함)를 명시한다.

## 5. 양식 기준

11-12 스크럼 페이지의 본인 칸을 따른다. 최상위 불릿은 `**주제**` + 선택 `(진행 중|해야 할 일|검토 대기)`, 하위 불릿은 1단계만, 티켓·PR 은 링크, 마지막은 `**막힌 것**`, 전체 30줄 이하다. 프롬프트 원본은 `hubs/scripts/scrum-prompt.md` 다. 다른 사람 칸은 읽지도 옮기지도 않는다. 모델이 칸 밖 문장을 붙이면 검증 단계에서 잘라낸다.

## 6. 파일

| 파일 | 역할 |
|---|---|
| `hubs/scripts/scrum_run.sh` | launchd 진입점. PATH·USER 보정, caffeinate |
| `hubs/scripts/scrum_post.py` | 날짜 잠금, 모델 순차 시도, 양식 검증, Slack 발송·재시도 |
| `hubs/scripts/scrum_collect.py` | 재료 수집(읽기 전용) |
| `hubs/scripts/scrum-prompt.md` | 초안 작성 지시문 |
| `hubs/launchd/ai.lrm.scrum.plist` | 평일 10:00 스케줄 |
| `hubs/scrum.env.example` | 본인 칸 제목 설정 예시 |
| `tests/scrum/run.sh` | 날짜 잠금·모델 순서·주말·warn 1건 시험(가짜 claude·알림 사용) |
