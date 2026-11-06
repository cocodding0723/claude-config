# 라이브 전환 계획서

실제 `~/.claude`·`~/.config/lrm-hubs`·`~/.config/git`·launchd 를 저장소 링크로 바꾸는 순서. 샌드박스 검증(`tests/install/run.sh`)은 끝났고, 이 계획은 아직 실행하지 않았다. 사용자 승인 뒤에만 실행한다.

## 전제

- 설치 위치는 병합이 끝난 `main` 체크아웃(`~/orca/claude-config`)이다. 작업 브랜치 워크트리에서 설치하면 워크트리를 지울 때 링크가 모두 끊긴다.
- 프로필 홈(`~/Library/Application Support/orca/claude-profiles/*/home`)의 링크 14개는 `~/.claude` 를 가리키므로 바꾸지 않는다. 그 안의 settings·keybindings 복사본은 Orca 가 갱신하며 설치 범위 밖이다.
- 원격 맥은 접속 확인이 먼저다. 이 계획은 이 맥만 다룬다.
- 슬랙 알림 스크립트에는 dry-run 이 없다(전송 스크립트). 확인은 `--help`·`bash -n`·폴링 로그로 대신한다.

## 1. 사전 점검 (읽기만)

```sh
cd ~/orca/claude-config
git status --short                 # 출력 없음
tests/install/run.sh               # 결과: PASS n, FAIL 0
```

**값 파일 만들기.** 실명·DM 채널 ID 는 저장소에 없고 로컬 값 파일로 채운다. 파일이 없거나 키가 비면 `install.sh` 는 아무것도 바꾸지 않고 중단한다.

```sh
mkdir -p ~/.config/claude-config
cp values.env.example ~/.config/claude-config/values.env
chmod 600 ~/.config/claude-config/values.env
# 편집기로 OWNER_NAME·PERSON_FULL·PERSON_SHORT·DM_CHANNEL 값을 채운다 (따옴표 없이 KEY=값, 값을 화면·로그에 출력하지 않는다)
```

**값을 가져올 곳.** 값은 라이브 파일에 이미 들어 있다. 자리표시 위치는 아래로 찾고, 같은 줄의 라이브 파일에서 해당 문구(이름·채널 ID)를 옮겨 적는다. 값 자체는 이 문서나 저장소에 쓰지 않는다.

```sh
grep -n '__[A-Z_]*__' hubs/roles/role-docs.txt.tmpl skills/orca-hub-create/references/common-rules.txt.tmpl
# 각 줄을 ~/.config/lrm-hubs/roles/role-docs.txt, ~/.claude/skills/orca-hub-create/references/common-rules.txt 의 같은 줄과 나란히 열어
# __OWNER_NAME__ → 본인 이름, __PERSON_FULL__ / __PERSON_SHORT__ → 리뷰어 성명·이름만, __DM_CHANNEL__ → DM 채널 ID
```

잘못 옮기면 아래 렌더 확인에서 `RENDER` 로 드러난다.

**라이브 우선 점검(중단 조건).** 저장소 원본과 라이브 대상을 내용으로 비교한다. 라이브가 더 최신인 파일이 있으면 링크 전환이 그것을 구버전으로 되돌리므로 전환하지 않는다. 의도된 경로 치환(`live-diff.tsv`)만 차이로 치지 않는다. 설치와 `--dry-run` 도 같은 점검을 먼저 하고 중단한다.

```sh
./install.sh --diff-live      # 기대: 라이브가 앞선 항목 없음 (LIVE-NEWER·LIVE-ONLY 0줄, 종료 코드 0)
```

`LIVE-NEWER`·`LIVE-ONLY` 가 나오면 **중단**한다. 해당 라이브 변경을 저장소(`hubs/scripts/` 등)로 옮겨 커밋·리뷰한 뒤 다시 한다. `chore/slack-bot-optimize` 처럼 다른 브랜치가 라이브를 먼저 바꾼 경우가 대표적이다.

**렌더 결과가 라이브 파일과 같은지 확인.** 값 파일로 채운 5개 파일(역할 3개, 스킬 파일 2개)이 현재 라이브 파일과 바이트 단위로 같아야 한다. 다르면 `RENDER` 로 표시된다.

```sh
./install.sh --dry-run | grep -E 'RENDER|렌더 결과 동일'   # 기대: SKIP ... (렌더 결과 동일) 5줄, RENDER 0줄
./install.sh --dry-run | tail -3                         # 요약 줄 확인
```

`RENDER` 가 나오면 값 파일 오타 또는 템플릿 변경이므로 **중단**하고 원인을 확인한다. 같은 방식으로 5개 파일의 동일성은 샌드박스에서 확인했다.

**비훅 키 점검(중단 조건).** 병합은 `hooks`·`statusLine` 을 뺀 모든 키(`permissions`·`env`·`enabledPlugins` 등)를 저장소 기본값으로 덮어쓴다. 설계 문서 작성 뒤 라이브에 추가된 허용 규칙이 있으면 사라진다. 값은 출력하지 않고 키 경로별 개수만 보여 준다.

```sh
merge/keydiff.sh      # 종료 코드 0: 차이 없음 / 1: 차이 있음
```

`live_only` 가 비어 있지 않으면 **전환을 중단**한다. 라이브에만 있는 값을 `claude/settings.base.json` 에 옮겨 커밋·리뷰한 뒤 이 단계를 다시 한다. `repo_only` 는 저장소에만 있는 값이므로 전환 후 라이브에 추가된다는 뜻이다. 의도한 것인지 확인한다.

## 2. 전체 백업

`install.sh` 가 덮어쓸 항목은 따로 백업하지만, 전환 전 상태를 통째로 한 번 더 남긴다.

```sh
B=~/.claude-config-backup/pre-live-$(date +%Y%m%d-%H%M%S)
mkdir -p "$B"
cp -p  ~/.claude/settings.json ~/.claude/CLAUDE.md ~/.claude/keybindings.json "$B"/
cp -a  ~/.claude/hooks   "$B"/hooks        # state 포함 약 13MB
cp -a  ~/.claude/skills  "$B"/skills       # 약 12MB. 링크 2개는 링크 그대로 보존됨
cp -a  ~/.claude/meat-proxy "$B"/meat-proxy
cp -a  ~/.config/lrm-hubs "$B"/lrm-hubs    # slack-bot.env 포함, 권한 600 유지
cp -a  ~/.config/git     "$B"/git
mkdir -p "$B"/claude-config && cp -p ~/.config/claude-config/values.env "$B"/claude-config/   # 값 파일(권한 600 유지). install.sh 백업 대상이 아님
cp -p  ~/.gitconfig      "$B"/gitconfig
cp -p  ~/Library/LaunchAgents/ai.lrm.slackpoll.plist "$B"/
echo "$B" > ~/.claude-config-backup/LAST-PRE-LIVE
ls "$B"
```

## 3. 전환

```sh
launchctl bootout gui/$(id -u)/ai.lrm.slackpoll    # 폴링 잠시 중지
cd ~/orca/claude-config
./install.sh                                       # 출력의 '백업:' 경로를 기록
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.slackpoll.plist
```

plist 가 허브 쪽 사본과 같아 `copy` 항목은 SKIP 된다. plist 내용이 바뀌는 경우에만 `./install.sh --reload-launchd` 를 쓴다.

## 4. 확인

| 명령 | 기대 결과 |
|---|---|
| `./install.sh --check` | `점검 통과: 문제 없음` |
| `ls ~/.claude/skills \| wc -l` | `37` (전환 전과 같음. synced·외부 링크 2개 포함) |
| `ls -l ~/.claude/CLAUDE.md ~/.claude/hooks/git-guard.sh` | `-> ~/orca/claude-config/...` 링크 |
| `jq '[.hooks[][]] \| length' ~/.claude/settings.json` | `22` |
| `diff <(jq -S . ~/.claude/settings.json) <(jq -S . "$B"/settings.json)` | 출력 없음(내용 동일) |
| `bash -c 'source ~/.claude/hooks/lib/common.sh; echo $STATE_DIR'` | `$HOME/.claude/hooks/state` |
| `ls ~/.claude/hooks/state/logs \| head -3; ls ~/orca/claude-config/claude/hooks` | 로그 그대로, 저장소에 `state` 없음 |
| `git config --global --get-regexp '^(core.hookspath\|merge.ff\|pull.rebase\|rebase.autostash)'` | 4개 키가 `gitconfig.tsv` 값 |
| 임시 저장소에서 `git rebase --force-rebase HEAD~1` (main 브랜치, 커밋 2개) | `pre-rebase: 공유 브랜치 'main' 은 rebase 하지 않습니다` |
| `bash -n ~/.config/lrm-hubs/slack_poll.sh && ~/.config/lrm-hubs/slack_notify.sh --help \| head -2` | 오류 없음 |
| `launchctl list \| grep slackpoll` 후 30초 뒤 `tail -3 ~/.config/lrm-hubs/slack-poll.log` | 상태 `0`, 로그에 새 줄 |
| 새 Claude 세션 1회 열기 | 훅 22개 동작, 오류 없음 |

하나라도 어긋나면 5번으로 되돌린다.

## 5. 되돌리기

```sh
launchctl bootout gui/$(id -u)/ai.lrm.slackpoll
cd ~/orca/claude-config
./install.sh --restore <3번에서 기록한 백업 폴더>   # 링크·병합 결과 제거, 원본 복원, .gitconfig 원상
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.slackpoll.plist
./install.sh --check || true                        # 링크가 남았는지 확인용(복원 뒤엔 실패가 정상)
ls -l ~/.claude/CLAUDE.md                           # 링크가 아닌 실제 파일
diff -r -x state ~/.claude/hooks "$(cat ~/.claude-config-backup/LAST-PRE-LIVE)"/hooks && echo 훅 동일   # state 는 실행 중 계속 바뀌므로 제외
```

복원은 백업 원본을 복사해 되돌리므로 두 번 실행해도 안전하다. 중간에 멈췄다면 같은 명령을 다시 실행한다. 백업 원본이 하나라도 없으면 아무것도 지우지 않고 중단한다. 설치가 새로 만든 빈 폴더(`.config/git/hooks` 등)는 복원 때 비어 있으면 함께 지운다.

값 파일(`~/.config/claude-config/values.env`)은 `install.sh` 가 만들거나 지우는 파일이 아니라 복원 대상이 아니다. 잃어버렸다면 2번 백업의 `"$B"/claude-config/values.env` 를 `cp -p` 로 되돌리고 권한을 600 으로 맞춘다. 값 파일이 없으면 `install.sh` 는 중단한다.

`install.sh` 로 복원이 안 될 때의 수동 경로는 2번 백업 폴더(`$B`)다. 링크를 지우고(`rm ~/.claude/skills/<이름>`) `cp -a "$B"/skills/<이름> ~/.claude/skills/` 처럼 되돌린다. `state`·`synced`·외부 링크는 전환 중에도 제자리에 있었으므로 복원 대상이 아니다.

## 6. 승인 전 확인 사항

- 전환 중 몇 초간 훅·스킬 경로가 비는 구간이 있다. 진행 중인 Claude·Orca 세션이 없는 때에 한다.
- 폴링(`launchctl bootout`)을 멈추는 동안 슬랙 명령 수신이 끊긴다. 재적재 뒤 `slack-poll.log` 에 새 줄이 생겨야 정상이다.
- 이후 `settings.json` 을 직접 고치면 다음 `install.sh` 때 저장소 기본값 기준으로 덮어쓴다(백업은 남는다). 설정을 바꿀 땐 `claude/settings.base.json` 을 같이 고친다.
- render 5개 파일(역할 3개, 스킬 파일 2개)과 `settings.json` 은 링크가 아니라 생성된 파일이다. `.tmpl`·값 파일·`settings.base.json` 을 고쳐도 `./install.sh` 를 다시 실행하기 전에는 라이브에 반영되지 않는다. 반영 여부는 `./install.sh --check` 의 `DRIFT` 로 본다.
