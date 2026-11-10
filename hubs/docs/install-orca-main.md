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
| Socket Mode 수신기 | launchd 서비스 `ai.lrm.slacksocket`. 메시지가 오면 폴러를 바로 돌린다. 설치만 하고 켜지 않는다 |
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
4. **미리 보기.** `./install.sh --dry-run`. 만들 링크·복사본이 나온다. 이미 있는 파일이 저장소와 다르면 `LIVE-NEWER` 로 멈춘다(아래 「4. 알려진 한계」).
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
2. 수신기가 쓰는 모듈을 설치한다: `python3 -m pip install --user slack_sdk`. plist 의 python3 는 설치할 때 셸의 `python3` 절대 경로다. `./install.sh --doctor` 의 `python3 slack_sdk` 줄이 `OK` 인지 본다.
3. 적재한다.
   ```sh
   cp ~/.config/lrm-hubs/ai.lrm.slacksocket.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.slacksocket.plist
   ```
4. `tail -5 ~/.config/lrm-hubs/slack-socket.log` 에 `연결됨` 이 나오면 된다.

끄기: `launchctl bootout gui/$(id -u)/ai.lrm.slacksocket` 뒤 `~/Library/LaunchAgents/ai.lrm.slacksocket.plist` 를 다른 곳으로 옮긴다. 폴러는 그대로 돌아 수신이 멈추지 않는다.

## 3. 되돌리기

`./install.sh --restore ~/.claude-config-backup/<시각>`. 그 설치가 만든 링크·복사본을 지우고 백업한 원래 파일을 제자리로 돌린다. 두 번 돌려도 같은 결과다. 시크릿 파일과 직접 적재한 launchd 서비스는 설치기가 만든 것이 아니라 되돌리지 않는다. 폴러를 내리려면 `launchctl bootout gui/$(id -u)/ai.lrm.slackpoll`.

## 4. 알려진 한계

- 다른 맥 실설치는 아직 해 보지 않았다. 검증은 임시 HOME 샌드박스 시험(`tests/install/run.sh`)까지다.
- 옆 패널 mod 는 파일만 깐다. Claude Code 가 mod 를 읽어 들이게 등록하는 절차는 이 맥에서 확인하지 못했다. 패널이 안 보이면 Claude Code 의 mod 만들기 기능(plugin-authoring)으로 `~/.config/lrm-hubs/mods/lrm-board` 를 등록한다.
- 옆 패널은 `~/orca/LoveRevolutionMaker` 에서 띄운 세션에서만 자동으로 열린다. 메인 체크아웃이 다른 곳이면 `/lrm-board` 로 연다.
- 이미 쓰던 맥에서 다시 설치하면, 라이브 orca-main 폴더에 옛 양식 이름 `config.example.env` 가 남아 있어 `LIVE-ONLY` 로 한 번 멈춘다. 그 파일은 `main.env.example` 로 이름만 바뀐 것이라 `./install.sh --allow-live-diff` 로 진행하면 된다(원래 폴더는 백업된다).
- Socket Mode 를 켠 뒤 `~/Library/LaunchAgents` 에 복사한 plist 는 설치기가 관리하지 않는다. 저장소 템플릿이 바뀌면 2절 3번을 다시 한다.
- python3 를 바꾸면(예: 다른 버전 설치) 소켓 plist 의 python3 경로가 달라져 `--check` 는 `DRIFT`, 다음 설치는 `LIVE-NEWER` 로 멈춘다. 새 경로가 맞으면 `./install.sh --allow-live-diff`, 옛 경로를 지키려면 `CLAUDE_CONFIG_PYTHON3=<옛 경로> ./install.sh`.
