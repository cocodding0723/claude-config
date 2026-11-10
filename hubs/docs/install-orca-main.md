# orca-main 새 맥 설치 안내

다른 맥에 메인 코디네이터(orca-main)와 허브 스크립트·Slack 폴러·옆 패널을 한 번에 까는 순서다. 기준은 이 저장소의 `install.sh` 하나이며, 시크릿(봇 토큰)은 저장소에 없고 설치 뒤 직접 입력한다. 작성일은 2026-11-10 이다.

## 쉬운 말 3줄 요약

- 무엇이 바뀌었나: 저장소를 받아 `./install.sh` 를 한 번 돌리면 메인 스킬·허브 스크립트·Slack 폴러·옆 패널이 모두 제자리에 깔린다.
- 왜: 예전 압축본에는 허브 스크립트 일부·연결 스킬·Slack 수신 서비스·옆 패널이 빠져 새 맥에서 메인이 돌지 않았다.
- 사용자가 할 일: 값 파일 하나를 만들고, 설치 뒤 Slack 토큰 파일을 직접 채우고, 폴러를 켜는 명령 한 줄을 실행한다. Socket Mode 수신기는 나중에 따로 켠다.

## 0. 알아야 할 것

| 용어 | 뜻 |
|---|---|
| 값 파일 | `~/.config/claude-config/values.env`. 실명·DM 채널 ID 처럼 계정에 묶인 값. 저장소에는 양식(`values.env.example`)만 있다 |
| 시크릿 파일 | `~/.config/lrm-hubs/slack-bot.env`. Slack 봇 토큰. 설치기는 이 파일을 만들거나 읽지 않는다 |
| 메인 설정 | `~/.config/lrm-hubs/main.env`. 허브 폴더·스크립트 위치·메인 터미널 핸들. 양식은 `skills/orca-main/references/main.env.example` |
| 폴러 | launchd 서비스 `ai.lrm.slackpoll`. 2분마다 Slack DM 을 모아 메인에 넘긴다 |
| Socket Mode 수신기 | launchd 서비스 `ai.lrm.slacksocket`. 메시지가 오면 폴러를 바로 돌린다. 연결이 끊기면 스스로 다시 연결한다. 설치만 하고 켜지 않는다 |
| 옆 패널 | Claude Code mod `lrm-board`. `panel.json` 을 15초마다 읽어 승인·할 일·허브 상태를 보여 준다 |

설치기가 까는 것(전부 `links.tsv` 에 있다):

| 항목 | 저장소 위치 | 설치 위치 | 방식 |
|---|---|---|---|
| orca-main 스킬 | `skills/orca-main` | `~/.claude/skills/orca-main` | 링크 |
| 연결 스킬(orca-hub-create·orca-coordinator·doc-kit·pretty-code 등) | `skills/*` | `~/.claude/skills/*` | 링크(일부 render) |
| 허브 스크립트(restart_hub·resume_all·slack_*·unity_slot 등) | `hubs/scripts` | `~/.config/lrm-hubs` | 링크 |
| pick_host.sh | `skills/orca-hub-create/scripts` | `~/.claude/skills/orca-hub-create/scripts` | 링크 |
| Slack 폴러 plist | `hubs/launchd/ai.lrm.slackpoll.plist` | `~/Library/LaunchAgents` | 복사(`__HOME__` 치환) |
| Socket Mode 수신기·plist | `hubs/scripts/slack_socket.py`, `hubs/launchd/ai.lrm.slacksocket.plist` | `~/.config/lrm-hubs` (LaunchAgents 아님) | 복사(`__HOME__`·`__PYTHON3__` 치환) |
| 옆 패널 mod | `hubs/mods/lrm-board` | `~/.config/lrm-hubs/mods/lrm-board` | 복사(`__HOME__` 치환) |

## 1. 설치 순서

1. **받기.** 압축본이면 원하는 곳(권장 `~/orca/claude-config`)에 풀고, git 으로 받으면 `git clone <저장소 주소> ~/orca/claude-config` 한다. 링크가 이 폴더를 가리키므로 설치 뒤 옮기지 않는다.
2. **값 파일 만들기.**
   ```sh
   mkdir -p ~/.config/claude-config
   cp values.env.example ~/.config/claude-config/values.env
   chmod 600 ~/.config/claude-config/values.env
   ```
   편집기로 열어 `KEY=값` 을 따옴표 없이 채운다. 빈 키가 있으면 설치기는 아무것도 바꾸지 않고 멈춘다.
3. **의존성 점검.** `./install.sh --doctor`. `MISS` 줄이 있으면 그 줄의 조치를 하고 다시 돌린다. `WARN` 은 선택 항목이라 설치를 막지 않는다. 아무것도 바꾸지 않는다.
4. **미리 보기.** `./install.sh --dry-run`. 만들 링크·복사본이 나온다. 이미 쓰던 맥에서는 라이브 파일이 저장소와 다르면(`LIVE-NEWER`·`LIVE-ONLY`) 목록 없이 멈춘다. 목록만 보려면 `./install.sh --dry-run --allow-live-diff`(아래 「5. 알려진 한계」).
5. **설치.** `./install.sh`. 덮어쓰는 파일은 `~/.claude-config-backup/<시각>/` 으로 옮긴 뒤 처리한다. 끝에 「설치 뒤 할 일」이 나온다.
6. **시크릿 입력.** 설치기가 `[필요]` 로 알린 파일을 직접 만든다. 값은 Slack 앱 설정 화면에서 복사해 넣는다.
   ```sh
   # ~/.config/lrm-hubs/slack-bot.env (편집기로 작성)
   SLACK_BOT_TOKEN=
   SLACK_USER_ID=
   ```
   ```sh
   chmod 600 ~/.config/lrm-hubs/slack-bot.env
   cp ~/.claude/skills/orca-main/references/main.env.example ~/.config/lrm-hubs/main.env
   chmod 600 ~/.config/lrm-hubs/main.env
   ```
   `main.env` 는 열어서 `HUB_ROOT` 같은 경로를 이 맥에 맞춘다. 토큰은 `main.env` 에 넣지 않는다.
7. **Slack 폴러 켜기.** 이것만 켠다.
   ```sh
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.slackpoll.plist
   ```
8. **Orca·메인 시작.** Orca 앱을 열고(`orca open`, `orca status` 로 확인) 메인 체크아웃(기본 `~/orca/LoveRevolutionMaker`) 터미널에서 Claude Code 를 띄워 `/orca-main` 을 실행한다. 메인 터미널 핸들을 한 번 기록한다.
   ```sh
   ~/.config/lrm-hubs/set_main_terminal.sh <orca terminal list --json 의 메인 핸들>
   ```
9. **확인.**
   - `./install.sh --check` → `점검 통과: 문제 없음`
   - `./install.sh --doctor` → `요약: 필수 누락 0`
   - `tail -5 ~/.config/lrm-hubs/slack-poll.log` 에 수집 기록이 쌓인다.
   - Claude Code 에서 `/lrm-board` 로 옆 패널이 열린다.

## 2. Socket Mode 켜기(나중에)

설치기는 수신기 파일만 `~/.config/lrm-hubs` 에 둔다. `~/Library/LaunchAgents` 에 두지 않으므로 로그인해도 켜지지 않고, `--reload-launchd` 를 붙여도 적재하지 않는다. 켤 때 아래를 사용자가 직접 한다.

1. Slack 앱 설정에서 Socket Mode 를 켜고 앱 수준 토큰(`connections:write`)을 발급해 `slack-bot.env` 에 `SLACK_APP_TOKEN=` 줄로 넣는다.
2. 수신기가 쓰는 모듈을 가상환경에 설치하고 plist 가 그 python3 를 쓰게 한다. plist 는 `python3 -I` 로 실행하는데 `-I` 는 사용자 폴더(`pip install --user` 위치)를 읽지 않아 `--user` 로 깐 모듈은 보이지 않는다. 시스템 python3 에 직접 까는 것은 Homebrew 등이 막는다. 가상환경은 `-I` 로도 읽히고 시스템을 건드리지 않아 가장 단순하다.
   ```sh
   python3 -m venv "$HOME/.local/share/lrm-hubs/venv"
   "$HOME/.local/share/lrm-hubs/venv/bin/pip" install slack_sdk
   export CLAUDE_CONFIG_PYTHON3="$HOME/.local/share/lrm-hubs/venv/bin/python3"
   ./install.sh --doctor
   ./install.sh --allow-live-diff
   ```
   `--doctor` 의 `python3 slack_sdk` 줄이 `OK` 여야 한다(doctor 도 `CLAUDE_CONFIG_PYTHON3` 의 python3 를 `-I` 로 확인). 마지막 줄은 보관본 plist 의 python3 경로를 가상환경으로 바꾼다(기존 파일은 백업). 설치 전에 이미 같은 값을 지정했다면 생략한다.
3. 적재한다.
   ```sh
   cp ~/.config/lrm-hubs/ai.lrm.slacksocket.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.slacksocket.plist
   ```
4. `tail -5 ~/.config/lrm-hubs/slack-socket.log` 에 `연결됨` 이 나오면 된다.

끄기: `launchctl bootout gui/$(id -u)/ai.lrm.slacksocket` 뒤 `~/Library/LaunchAgents/ai.lrm.slacksocket.plist` 를 다른 곳으로 옮긴다. 폴러는 그대로 돌아 수신이 멈추지 않는다.

### 연결 감시

연결이 조용히 끊겨도 로그만 봐서는 "끊겼다"와 "사용자가 글을 안 썼다"를 구분할 수 없어서 감시와 생존 기록을 넣었다. slack_sdk 도 5초마다 ping 을 보내고 응답이 20초 넘게 없으면 스스로 다시 연결한다. 수신기의 감시는 그 위에서 SDK 가 복구하지 못한 경우를 맡는 두 번째 안전장치다.

| 상황 | 수신기 동작 | 로그 |
|---|---|---|
| 연결이 60초 이상 끊겨 있음 | 새 클라이언트로 다시 연결하고 놓친 글을 한 번 수집 | `재연결됨`, `재연결 → 수집` |
| 60초 안에 SDK 가 스스로 복구 | 다시 연결하지 않고 복구만 확인해 한 번 수집 | `재연결됨`, `재연결 → 수집` |
| 다시 연결이 5번 연속 실패 | 종료 코드 3 으로 끝냄. launchd KeepAlive 가 곧바로(직전 시작 뒤 30초 이내면 30초 뒤) 새 프로세스를 띄운다 | `재연결 실패 <예외 종류> n/5`, `재연결 연속 실패 — 종료 후 launchd 재시작` |
| 6시간마다(연결이 살아 있어도) | 새로 연결하고 옛 연결을 닫은 뒤 한 번 수집. 반쯤 죽은 연결 대비 | `정기 재연결`, `정기 재연결 → 수집` |
| 10분마다 | 상태 한 줄 기록 | `생존 연결=예 마지막이벤트=3분 전 재연결=1회` |

첫 연결과 다시 연결 모두 예외가 없어도 연결이 실제로 맺어졌는지(`is_connected`)까지 확인해 성공으로 친다. 첫 연결이 안 붙으면 `연결 실패 ConnectionError` 로 종료 코드 2 다. slack_sdk 는 웹소켓 단계 실패를 예외 없이 넘기므로, 이 확인이 없으면 웹 API 만 되고 wss 가 막힌 장애에서 실패가 `재연결됨` 으로 기록되고 종료 코드 3 도 동작하지 않는다. 옛 클라이언트는 닫을 때 `close()` 뒤 `disconnect()` 를 한 번 더 부르고 SDK 가 남기는 수신 스레드(`current_session_runner`)까지 끝낸다. 이 수신 스레드 종료는 SDK 의 대기에 상한이 없어 드물게 영구 대기할 수 있으므로 별도 스레드에서 상한(`LRM_SOCKET_RETIRE_TIMEOUT_S`)까지만 기다린다. 넘기면 `정리 runner 시간 초과 5초` 한 줄을 남기고 감시를 계속한다(스레드 1개는 남는다).

알려진 한계: 장애 중에는 첫 연결 실패(종료 코드 2)도 KeepAlive 가 30초마다 다시 시작해 로그에 실패 한 줄이 30초마다 쌓인다(최대 하루 2880줄). 연결이 복구되면 다음 시작에서 저절로 붙는다.

로그 읽는 법(`tail -20 ~/.config/lrm-hubs/slack-socket.log`):

- `생존 연결=예|아니오`: 점검 시점에 연결이 살아 있는지. `아니오` 가 몇 줄 이어지다 `재연결됨` 이 나오면 정상 복구다.
- `마지막이벤트=N분 전`: 마지막으로 Slack 에서 무엇이든 받은 뒤 지난 시간(봇 글·무시한 이벤트 포함). 아직 받은 것이 없으면 `없음`. 사용자가 글을 안 쓰는 동안 커지는 것은 정상이라 끊김 판정에는 쓰지 않는다. `연결=예` 인데 이벤트가 오래 없어도 글을 안 쓴 것일 수 있으며, 의심되면 DM 한 통을 보내 `DM → 수집` 이 찍히는지 본다.
- `재연결=n회`: 시작 뒤 다시 연결한 횟수(복구 확인·정기 재연결 모두 포함). 정기 재연결 때문에 6시간마다 1씩 오르는 것은 정상이다.
- 생존 줄은 본문·토큰·ID 를 담지 않는다. 한 줄이 10분에 하나라 하루 144줄이다.
- **맥이 깨어 있는 동안 생존 로그가 10분 넘게 없으면 프로세스 문제다**(점검 간격 때문에 10분 30초까지는 정상이고, 다시 연결이 HTTP 30초 제한·호출 제한 대기에 막히면 그보다 길 수 있다). 감시 시계(`time.monotonic`)는 맥이 잠든 동안 멈추므로 그 구간에는 생존 줄이 없고 6시간 정기 재연결 주기도 깨어 있는 시간 기준이다. 문제면 `launchctl kickstart -k gui/<uid>/ai.lrm.slacksocket` (`<uid>` 는 `id -u`). 이 명령은 문서에만 있고 수신기가 실행하지 않는다.

갱신 적용: `slack_socket.py` 는 복사 항목이라 병합 뒤 `./install.sh --allow-live-diff` 로 복사한 다음 `launchctl kickstart -k gui/<uid>/ai.lrm.slacksocket` 으로 실행 중인 프로세스를 다시 띄운다. 두 명령은 문서에만 있고 수신기가 실행하지 않는다.

환경 변수(기본값으로 충분하다. 바꾸려면 plist 의 `EnvironmentVariables` 에 넣는다):

| 변수 | 기본 | 뜻과 근거 |
|---|---|---|
| `LRM_SOCKET_CHECK_S` | 30 | 연결 점검 간격(초). SDK 의 ping 5초보다 충분히 길어 일시 끊김은 대부분 스스로 복구된 뒤에 본다 |
| `LRM_SOCKET_DOWN_S` | 60 | 이 시간 이상 끊겨 있어야 직접 다시 연결(초). SDK 자체 복구에 먼저 맡기는 여유 |
| `LRM_SOCKET_REFRESH_S` | 21600 | 정기 재연결 주기(초, 6시간). `0` 이면 끈다 |
| `LRM_SOCKET_ALIVE_S` | 600 | 생존 기록 주기(초) |
| `LRM_SOCKET_DEBOUNCE_S` | 2 | 연속 이벤트를 한 번의 수집으로 묶는 대기(초). 기존 값 그대로 |
| `LRM_SOCKET_RETIRE_TIMEOUT_S` | 5 | 옛 연결의 수신 스레드 종료를 기다리는 상한(초). 넘기면 시간 초과 한 줄을 남기고 계속한다. 평소 1초 안에 끝난다 |

잘못된 값·음수는 기본값으로 돌아간다. 이 감시는 가짜 연결로만 시험했다(`tests/slack_socket/run.sh`). 실제 Slack 연결 끊김을 일으켜 본 적은 없으므로 켠 뒤 며칠은 생존 줄이 10분마다 찍히는지 확인한다.

## 3. 되돌리기

`./install.sh --restore ~/.claude-config-backup/<시각>`. 그 설치가 만든 링크·복사본을 지우고 백업한 원래 파일을 제자리로 돌린다. 두 번 돌려도 같은 결과다. 시크릿 파일과 직접 적재한 launchd 서비스는 설치기가 만든 것이 아니라 되돌리지 않는다. 폴러를 내리려면 `launchctl bootout gui/$(id -u)/ai.lrm.slackpoll`.

## 4. 배포용 압축본

`scripts/make_dist_zip.sh [ref]` 가 git 에 추적된 파일만 담은 zip 을 `~/Documents/90_정리기록/배포/orca-main-install-<YYYYMMDD>.zip` 로 만든다. 만들기 전에 토큰·이메일·사용자 홈 절대 경로·이 맥 사용자명·Slack ID 형태·금지 파일 이름(`.env`·`.env.*`·키·`.bak`·`__pycache__` 등)·허용 확장자 밖 바이너리를 검사해 하나라도 걸리면 zip 을 만들지 않는다. 바이너리도 토큰 형태는 검사한다. 걸린 위치는 파일:줄만 보여 주고 값은 출력하지 않는다. 실명 검사가 필요하면 `DIST_DENY_PATTERN=<정규식>` 을 붙인다. 내부 호스트(Tailscale 대역 IP)·회사 Jira 도메인·개인 `github.io` 주소는 시크릿이 아니라 경고로만 보고하고(종류·파일 수만, 값은 출력하지 않음) zip 은 만든다. `--strict-internal` 을 주면 경고도 중단한다. **zip 에 내부 호스트·회사 도메인 주소가 들어 있을 수 있으니 다른 사람과 공유하기 전에 확인한다.** ref 기본값은 `HEAD` 이며, 병합 뒤에는 `scripts/make_dist_zip.sh main` 으로 다시 만든다. 같은 날 같은 이름이 이미 있으면 덮어쓰지 않고 멈춘다.

받는 쪽은 압축을 풀고 「1. 설치 순서」 1번부터 따른다. zip 에는 git 기록이 없으므로 이후 갱신은 새 zip 을 받거나 git 으로 다시 받는다.

## 5. 알려진 한계

- 다른 맥 실설치는 아직 해 보지 않았다. 검증은 임시 HOME 샌드박스 시험(`tests/install/run.sh`)까지다.
- 옆 패널 mod 는 파일만 깐다. Claude Code 가 mod 를 읽어 들이게 등록하는 절차는 이 맥에서 확인하지 못했다. 패널이 안 보이면 Claude Code 의 mod 만들기 기능(plugin-authoring)으로 `~/.config/lrm-hubs/mods/lrm-board` 를 등록한다.
- 옆 패널은 `~/orca/LoveRevolutionMaker` 에서 띄운 세션에서만 자동으로 열린다. 메인 체크아웃이 다른 곳이면 `/lrm-board` 로 연다.
- 이미 쓰던 맥에서는 `--dry-run` 도 라이브 파일과 차이가 있으면(`LIVE-NEWER`·`LIVE-ONLY`) 설치 목록 없이 멈춘다. 미리 보려면 `./install.sh --dry-run --allow-live-diff`.
- 이미 쓰던 맥에서 다시 설치하면, 라이브 orca-main 폴더에 옛 양식 이름 `config.example.env` 가 남아 있어 `LIVE-ONLY` 로 한 번 멈춘다. 그 파일은 `main.env.example` 로 이름만 바뀐 것이라 `./install.sh --allow-live-diff` 로 진행하면 된다(원래 폴더는 백업된다).
- Socket Mode 를 켠 뒤 `~/Library/LaunchAgents` 에 복사한 plist 는 설치기가 관리하지 않는다. 저장소 템플릿이 바뀌면 2절 3번을 다시 한다.
- python3 를 바꾸면(예: 다른 버전 설치) 소켓 plist 의 python3 경로가 달라져 `--check` 는 `DRIFT`, 다음 설치는 `LIVE-NEWER` 로 멈춘다. 새 경로가 맞으면 `./install.sh --allow-live-diff`, 옛 경로를 지키려면 `CLAUDE_CONFIG_PYTHON3=<옛 경로> ./install.sh`.
