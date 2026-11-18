---
name: github-issue
description: "TRACKER=github 인 컴퓨터에서 버그·작업을 다른 사람이 이슈만 보고 재현·착수·확인할 수 있게 상세한 한국어 GitHub Issue 로 등록하고 Project 보드에 올린다. 'GitHub 이슈 등록', '이슈 만들어줘', '티켓 발급'(TRACKER=github 일 때) 요청과 허브의 작업 선발급에 사용한다. 진행 댓글 규칙도 포함한다. TRACKER=jira 면 jira-issue, 규칙 없이 초안만 필요하면 manage-issues 가 맡는다."
---

# GitHub 이슈 등록

`jira-issue` 의 GitHub 판. 원칙·제목·본문 템플릿·작업 댓글·금지 항목은 **`jira-issue` SKILL.md 와 같다**(그 파일의 「제목」「본문 템플릿」「작업 댓글」「금지」를 그대로 따른다). 여기는 달라지는 것만 적는다.

먼저 확인: `grep -E '^TRACKER=' ~/.config/claude-config/values.env` 가 `github` 가 아니면 이 스킬을 쓰지 않고 `jira-issue` 로 간다.

## 기본값

- 저장소: **작업하는 저장소**(`gh repo view --json nameWithOwner`). 여러 저장소에 걸친 일은 주 저장소에 올리고 나머지는 「관련」에 링크
- 보드: 설정 파일 `~/.config/claude-config/github-issue.json` 의 Project(개인 맥: 개인 보드 #6 「개인 작업」). `gh_board.py config` 로 확인
- 유형: 라벨 `bug`·`task`·`epic`(저장소에 없으면 `gh label create` 로 만든 뒤 사용). 개인 맥 GitHub 은 Jira 의 `버그`·`작업`·`에픽` 대신 이 라벨
- 담당자: 사용자 본인(`--assignee @me`). 누락 금지
- 기한: 보드 `기한` 칸에 반드시 넣는다. 작업 크기로 추정하고 근거를 본문에 한 줄
- 상위: 필수. 상위 이슈(라벨 `epic`)의 **하위 이슈**로 연결(GitHub sub-issue). 상위 없이 발급 금지
- 상태: 보드 Status. `해야 할 일`·`진행 중`·`확인 대기`·`보류`·`완료` 로 Jira 와 이름이 같다. 판정 규칙은 `jira-issue` 「실제 상태」와 같고 "dev 병합" 은 "기본 브랜치(또는 통합 브랜치) 병합"으로 읽는다
- 작업판(beads, `~/orca/board`)은 허브 안 짧은 할 일·결정 카드만. 작업 원본은 이 이슈다. beads 의 GitHub 동기화는 쓰지 않는다

## 도구

- 검사기: `python3 ~/.claude/skills/github-issue/scripts/issue_check.py pre|post <json>`. 입력 칸은 jira-issue 검사기와 같고 키는 `OWNER/REPO#번호`, `assignee` 는 GitHub 로그인
- 보드: `python3 ~/.claude/skills/github-issue/scripts/gh_board.py add|status|get|config` (사용법은 `--help`). 이슈 생성 자체는 `gh issue create`

## 절차

1. **자료 모으기.** `jira-issue` 와 같다. 모르는 칸은 `미확인`.
2. **중복 검사:** `gh issue list --repo OWNER/REPO --state all --search "핵심어"`(핵심어 2~3개 각각). 비슷한 게 있으면 링크를 보여주고 멈춘다.
3. **상위 결정.** `gh issue list --repo OWNER/REPO --label epic --state open --search "핵심어"` 로 후보를 찾고, 후보의 하위는 `gh api repos/OWNER/REPO/issues/번호/sub_issues --jq '.[].title'`. 고른 근거 한 줄. 맞는 게 없으면 새 상위 이슈(라벨 `epic`, 본문에 목표·범위·완료 기준·하위 목록)를 먼저 만든다.
4. **실제 단계 확인.** `git branch --list '*번호*'`, `git log --oneline 기준..HEAD`, `gh pr list --head 브랜치 --state all` 로 근거를 모아 상태를 정한다.
5. **초안 보여주기.** 상위·상태·기한·담당을 함께. 이 맥의 개인 작업 이슈 발급은 승인 없이 바로 한다(작업 원본이 이슈이므로). 상태를 `완료` 로 바꾸는 것은 사람만.
6. **발급 전 검사:** 입력 JSON → `issue_check.py pre`. 오류는 고친 뒤 다시.
7. **생성:**
   ```
   gh issue create --repo OWNER/REPO --title '[영역] 현상' --body-file /절대경로/body.md --label task --assignee @me
   gh_board.py add <이슈 URL> --status '해야 할 일' --due YYYY-MM-DD --parent <상위 이슈 URL>
   ```
   스크린샷·녹화는 본문에 끌어 넣을 수 없으므로 첫 댓글에 파일 이름과 위치를 적고, 공개 저장소면 올리기 전에 공개 범위를 확인한다.
8. **발급 뒤 자기 점검:** `gh_board.py get <URL>` 출력(여러 개면 배열로 모음)에 `evidence` 를 넣어 `issue_check.py post` 의 표를 그대로 보고한다. `비어 있음`·`고칠 것` 이 있으면 `gh issue edit`·`gh_board.py status` 로 고치고 다시 점검.
9. **여러 개 몰아 발급:** 한 개씩 7~8 을 통과한 뒤 다음으로.

## 진행 연결

- 브랜치·커밋에 이슈 번호: `feat/123-우편함`, 커밋 본문 `#123`
- PR 본문 첫 줄 근처에 `Closes OWNER/REPO#123`(같은 저장소면 `Closes #123`). 기본 브랜치 병합 때 이슈가 닫힌다. 통합 브랜치(dev)로 병합하면 자동으로 닫히지 않으니 병합 직후 `gh_board.py status <URL> '확인 대기'` 와 댓글을 남긴다
- PR 이 열리면 `진행 중`, 병합되면 `확인 대기`. `완료` 는 사람이 확인한 뒤 사람이 바꾼다
- 작업 댓글은 `gh issue comment <번호> --repo OWNER/REPO --body-file …`. 형식·시점은 `jira-issue` 「작업 댓글」과 같다
