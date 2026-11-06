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
./install.sh --dry-run | tail -3   # 요약 줄 확인(링크 74 안팎, 병합 1, 복사 2 안팎)
```

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
diff -r ~/.claude/hooks "$(cat ~/.claude-config-backup/LAST-PRE-LIVE)"/hooks && echo 훅 동일
```

`install.sh` 로 복원이 안 될 때의 수동 경로는 2번 백업 폴더(`$B`)다. 링크를 지우고(`rm ~/.claude/skills/<이름>`) `cp -a "$B"/skills/<이름> ~/.claude/skills/` 처럼 되돌린다. `state`·`synced`·외부 링크는 전환 중에도 제자리에 있었으므로 복원 대상이 아니다.

## 6. 승인 전 확인 사항

- 전환 중 몇 초간 훅·스킬 경로가 비는 구간이 있다. 진행 중인 Claude·Orca 세션이 없는 때에 한다.
- 폴링(`launchctl bootout`)을 멈추는 동안 슬랙 명령 수신이 끊긴다. 재적재 뒤 `slack-poll.log` 에 새 줄이 생겨야 정상이다.
- 이후 `settings.json` 을 직접 고치면 다음 `install.sh` 때 저장소 기본값 기준으로 덮어쓴다(백업은 남는다). 설정을 바꿀 땐 `claude/settings.base.json` 을 같이 고친다.
