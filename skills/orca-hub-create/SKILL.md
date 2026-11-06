---
name: orca-hub-create
description: Orca 에 새 코디네이터 허브(sparse 관리 워크스페이스 + 상주 Opus 코디네이터)를 만들고, 허브 하위 워커용 sparse 워크스페이스를 만든다. "코디네이터 만들어줘", "○○ 허브 생성", "워커 워크스페이스 분리" 요청에 사용한다. 기존 Run 운영·복구는 orca-coordinator 가 맡는다.
---

# Orca 코디네이터 허브 생성

허브 = 메인 코디네이터 하위의 sparse 관리 워크스페이스 + 그 안의 코디네이터 터미널 1개. 워커는 허브에서 돌지 않고 허브 하위 sparse 워크스페이스에서 돈다.

## 순서

1. **겹침 확인**: `orca worktree list --json` 에서 `[관리]` 허브 목록을 보고 같은 역할 허브가 있으면 새로 만들지 않는다.
2. **역할 프롬프트 작성**: [역할 템플릿](references/role-prompt.md) 의 빈칸(역할·첫 작업·완료 조건·워커 모델)을 채운다. 공통 규칙은 스크립트가 붙이므로 쓰지 않는다.
3. **허브 생성·코디네이터 기동**:
   ```bash
   bash scripts/create_hub.sh --slug <영문-slug> --name "<한글 역할>" \
     --paths "/docs/ /.claude/ /CLAUDE.md <필요 경로…>" --role-file <역할 프롬프트 파일>
   ```
   sparse 체크아웃(origin/dev, detached) → `[관리] <이름> 코디네이터` 표시 이름·메인 하위 연결 → Opus 5.5 코디네이터 기동 → 프롬프트 제출 확인(붙여넣기만 되고 멈추면 Enter 재전송)까지 한다.
4. **보고**: 허브 경로·크기·터미널 핸들·코디네이터 첫 상태 한 줄. 메모리 `orca-coordinator-sparse-hub` 에 허브 한 줄 추가.

워커 워크스페이스는 코디네이터가 만든다: `bash scripts/create_worker_ws.sh --hub <허브 경로> --name "[LRM-n] 하는 일" --paths "…" [--branch <브랜치>]`. 브랜치를 주지 않으면 읽기 전용(detached)이다. Unity 실행·녹화·빌드는 `--full` 로 전체 체크아웃.

## 공통 규칙 요약 (정본: references/common-rules.txt, 스크립트가 코디네이터 프롬프트에 붙임)

- 코디네이터 Opus 5.5(`claude-opus-5-5`). 워커 모델·허브당 동시 워커 수는 `references/common-rules.txt` 가 정본이다(여기에 숫자를 다시 적지 않는다).
- 코디네이터는 배정·판정·보고만. 파일 쓰기·구현·긴 조사 금지.
- 워커는 Orca 오케스트레이션으로만, 허브 하위 sparse 워크스페이스 1개씩.
- 게임 프로젝트 작업의 하위 워크스페이스는 Jira 선발급(중복 검색, 같은 티켓 하위 작업은 키 재사용). 사용자 개인 업무(오케스트레이션·스킬·도구·개인 보고·스크럼 초안·정리)는 Jira 없이 `[개인] 하는 일`.
- 워커 bypass, 승인 요청은 코디네이터가 처리. push·rebase·merge·PR·reset --hard·clean·branch -D 만 사용자 승인.
- rm 은 리터럴 경로·`${VAR:?}`·`mktemp -d`. Unity activate 는 녹화 때만 사전 고지.
- Slack·Confluence 게시는 인증이 없으면 메인 코디네이터에 파일 경로와 핵심 3줄을 넘긴다.

## 주의

- `orca agent launch` 프롬프트가 붙여넣기만 되고 제출되지 않는 경우가 잦다. 스크립트가 확인하지만, 실패 보고가 나오면 `orca terminal send --terminal <h> --enter`.
- Orca 재시작 시 코디네이터 터미널 핸들이 전부 무효가 된다. 같은 스크립트를 `--resume "<기존 Run id·산출물 위치>"` 로 다시 실행해 이어받게 한다.
- 메인 체크아웃은 미커밋 보존 때문에 sparse 로 바꾸지 않는다.
- 이미 코디네이터 터미널이 살아 있는 허브에는 스크립트가 새 코디네이터를 띄우지 않고 중단한다. 의도적으로 하나 더 띄우려면 `--force`.
- `common-rules.txt` 개정은 이미 떠 있는 코디네이터에 반영되지 않는다. 필요하면 `--resume` 으로 재기동한다.
