# 스크럼 자동 초안 런북

평일 10:00 에 스크럼 회의록 본인 칸 초안을 claude 로 쓰고 Slack 봇 DM 1건으로 보내는 `scrum_run.sh` 의 흐름, 등록, 끄기, 되돌리기를 적는다. 작성일은 2026-11-12, 11-13 에 진행 상세·첨부·팀 공유 사항을 더했다.

## 쉬운 말 3줄 요약

- 무엇이 생겼나: 평일 아침 10시에 어제 한 일(PR·커밋·허브 보고)과 진행 중인 작업(PR 검사 결과·막힘·결정)을 모아 회의록 형식의 본인 칸 초안을 만들고, 봇이 Slack 개인 DM 으로 보낸다. 초안 전체는 마크다운 첨부 파일이고, 오늘·어제 만든 화면·영상(최대 5개)과 팀 공유 사항 칸이 함께 붙는다.
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
 scrum_collect.py ─▶ gh PR · 워크스페이스 커밋 · 허브 보고 · HANDOFF · 최근 24시간 Slack 지시
   │                  · 허브별 진행 상세(상태 파일·열린 PR 검사·최근 결정) · 첨부 후보(이미지·영상)   (JSON)
   ▼
 claude -p  (fable-5-1 → fable-5-2 → opus-5-5 순. 양식 검증을 통과한 첫 결과 채택)
   │  Jira·Confluence 는 Atlassian 읽기 도구로 직접 읽음(쓰기 도구는 허용 안 함)
   ▼
 초안 파일 ~/Documents/90_정리기록/scrum-draft/YYYY-MM-DD.md   (+ 팀 공유 사항 칸을 '막힌 것' 앞에 삽입)
   ▼
 첨부 없음: slack_notify.sh --kind doc 1건(요약 + 초안 첨부)
 첨부 있음: 요약 글 1건 → 그 스레드에 초안 파일 → 이미지·영상 한 건씩      실패는 --kind warn 1건
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

등록 직후, 첫 자동 실행 전에 launchd 와 같은 최소 환경으로 점검 1회(발송·초안 작성·상태 기록 없음. gh 인증·claude 로그인·Atlassian MCP 연결·Slack 봇 스크립트·본인 칸 제목만 확인하고 토큰 값은 출력하지 않는다):

```sh
env -i HOME="$HOME" PATH=/usr/bin:/bin ~/.config/lrm-hubs/scrum_run.sh --preflight
```

전부 `PASS` 면 종료 코드 0, 하나라도 `FAIL` 이면 1 이며 줄마다 조치를 안내한다.

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
| 첫 실행 전 점검(발송 없음) | `~/.config/lrm-hubs/scrum_run.sh --preflight` |
| 재료만 보기 | `python3 ~/.config/lrm-hubs/scrum_collect.py --date 2026-11-12` |
| 오늘 분 다시 보내기 | `~/.config/lrm-hubs/scrum_run.sh --force` |

`--dry-run` 은 상태 파일을 바꾸지 않고 Slack 으로 보내지 않는다. 초안은 표준 출력과 `<날짜>.dry.md` 에 남는다. 종료 코드는 0 성공·건너뜀, 1 초안 작성 또는 발송 실패, 2 인자·설정 오류, 3 환경 오류다.

## 4. 실패와 대응

| 증상 | 원인 | 대응 |
|---|---|---|
| Slack 에 `스크럼 초안 작성 실패` warn 1건 | 세 모델이 모두 실패(한도·인증·양식 불량) | 로그 `~/orca/archive/scrum_post.log` 의 `모델 실패` 줄에서 모델별 사유 확인 → `scrum_run.sh --force` |
| 초안에 `경고: Jira·Confluence 를 못 읽어` | Atlassian MCP 가 `needs-auth` 캐시 상태라 claude -p 가 연결을 건너뜀 | 대화형 `claude` 에서 `/mcp` → atlassian 인증 1회. 그동안은 PR·커밋·허브 보고만으로 초안이 나간다 |
| 초안에 `경고: 재료 수집 누락(commits 일부 워크트리)` | 워크트리 일부에서 `git log` 가 실패(잠김·손상) | 로그 `재료 수집 누락` 줄과 `scrum_collect.py --date <날짜>` 의 errors 에서 실패한 폴더 확인. 전부 실패하면 초안 대신 실패 알림이 나간다 |
| 발송이 3번 실패 | Slack API 오류(종료 코드 1만 30초·90초 뒤 재시도) | 초안 파일은 남고 로컬 알림이 뜬다. 다음 평일 재시도는 없다 |
| `본인 칸 제목 없음` | `scrum.env` 없음 | 2절 1번 |

초안 claude 는 사용자 설정(`settings.json` 의 허용 목록·훅)과 기존 MCP 서버를 읽지 않는다(`--setting-sources ""`, `--strict-mcp-config`). 스크럼 전용 MCP 설정은 Atlassian 하나뿐이고, 읽기 도구만 허용한다. 인증은 키체인에 저장된 같은 이름(`atlassian`)·URL 항목을 쓴다.

claude 는 `USER` 로 키체인 로그인 항목을 찾는다. 래퍼가 `USER`·`LOGNAME` 이 비어 있으면 채워 두므로 launchd 의 짧은 환경에서도 로그인이 풀리지 않는다. plist 는 `HOME`·`PATH`(claude·gh·python3 위치 포함)를 명시한다.

## 5. 진행 상세·첨부·팀 공유 사항

### 진행 상세

주제마다 하위 불릿으로 `진행`(지금 하는 일)·`PR`(번호·검사 통과/실패/대기)·`막힘`(풀어 줄 사람까지)·`결정`(팀이 알아야 하는 것)을 적는다. 재료는 `hub_progress` 다.

| 출처 | 내용 |
|---|---|
| `~/.config/lrm-hubs/hub-status/*.json` | 갈래별 작업·다음 할 일·막힘·브랜치 |
| `progress.json` | 대기 항목(내부 장부·PR·결정 묶음은 제외) |
| `gh pr list --author @me --state open` | 열린 PR 번호·초안 여부·검사 요약. 브랜치가 맞는 갈래에 붙이고 나머지는 `other_prs` |
| `coord-*-hub/decisions.tsv` | 수집 시작 이후 결정 기록(갈래당 최근 5개) |

상태 파일이 없어도 열린 PR 만으로 수집한다. `gh` 가 실패하면 `errors` 에 `hub_progress` 로 남고 초안은 나머지 재료로 쓴다. 이 출처는 '전 출처 빔' 판정에 넣지 않는다.

### 이미지·영상 첨부

- 후보: 허브 보고(`hub-reports.jsonl`)의 `files`(원본이 없으면 접수 때 복사본 `staged`)와 `~/Documents/90_정리기록/`(깊이 3)의 오늘·어제 파일. 확장자는 png·jpg·jpeg·gif·webp·mp4·mov·webm·mkv.
- 거름: `hub_report.py` 의 첨부 규칙을 그대로 쓴다. 비밀 파일(.env·키·인증서·`.ssh` 등)과 50MB 초과, 없는 파일은 뺀다. 같은 이름·크기는 하나만. 허브 보고 첨부를 먼저, 그 안에서 최신순으로 5개까지. 발송 직전에 한 번 더 거른다.
- 발송: 첨부가 있으면 요약 글을 먼저 올리고(파일 업로드는 메시지 ts 가 없어 스레드를 걸 수 없다) 그 스레드에 초안 파일과 첨부를 한 건씩 올린다. 첨부 하나가 실패하면 스레드에 경고 1건과 로그를 남기고 초안은 보낸 것으로 친다. 첨부가 없으면 기존처럼 초안 파일 1건이다.
- 이 흐름에서 요약 글이 올라간 뒤 초안 파일이 실패하면 보냄 기록이 없어, `--force` 재실행 때 요약 글이 한 번 더 올라갈 수 있다.

### 팀 공유 사항

`~/.config/lrm-hubs/scrum-share.md`(예시 `hubs/scrum-share.example.md`)를 읽어 `**팀 공유 사항**` 칸을 `**막힌 것**` 앞에 넣는다. 문구는 모델이 아니라 코드가 그대로 넣는다.

| 상황 | 동작 |
|---|---|
| 파일 없음·쓸 줄 없음 | 칸 생략, 경고 없음 |
| `날짜: YYYY-MM-DD` 줄이 오늘이 아님 | 낡은 파일로 보고 칸 생략(로그에 남김) |
| 비밀·로컬 용어(허브·워커·코디네이터·내부 번호)가 든 줄, 12줄·200자 초과 | 그 줄만 빼고 Slack 요약에 `뺀 줄 수` 경고. 원문은 어디에도 안 남김 |

## 6. 양식 기준

11-12 스크럼 페이지의 본인 칸을 따른다. 최상위 불릿은 `**주제**` + 선택 `(진행 중|해야 할 일|검토 대기)`, 하위 불릿은 1단계만, 티켓·PR 은 링크, 마지막은 `**막힌 것**`, 전체 45줄 이하다(팀 공유 사항 칸 제외). 프롬프트 원본은 `hubs/scripts/scrum-prompt.md` 다. 다른 사람 칸은 읽지도 옮기지도 않는다. 모델이 칸 밖 문장을 붙이면 검증 단계에서 잘라낸다.

## 7. 파일

| 파일 | 역할 |
|---|---|
| `hubs/scripts/scrum_run.sh` | launchd 진입점. PATH·USER 보정, caffeinate |
| `hubs/scripts/scrum_post.py` | 날짜 잠금, 모델 순차 시도, 양식 검증, Slack 발송·재시도 |
| `hubs/scripts/scrum_collect.py` | 재료 수집(읽기 전용). 진행 상세·첨부 후보 포함 |
| `hubs/scripts/scrum-prompt.md` | 초안 작성 지시문 |
| `hubs/launchd/ai.lrm.scrum.plist` | 평일 10:00 스케줄 |
| `hubs/scrum.env.example` | 본인 칸 제목 설정 예시 |
| `hubs/scrum-share.example.md` | 팀 공유 사항 설정 예시(실제 값은 `~/.config/lrm-hubs/scrum-share.md`, 저장소에 두지 않음) |
| `tests/scrum/run.sh` | 날짜 잠금·모델 순서·주말·warn 1건·진행 상세·첨부 선택·스레드 발송·공유 사항 시험(가짜 claude·알림·gh 사용) |
