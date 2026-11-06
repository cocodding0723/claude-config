# LoveRevolutionMaker 운영 프로필

2026-11-02 사용자 결정과 Astra 독립 검토를 반영한다. 이 프로필로 사용자가 요청한 프로젝트 오케스트레이션 범위에 적용하며, 다른 저장소의 승인 규칙으로 확대하지 않는다.

## 역할·권한

- 개발 코디네이터는 **Claude Opus 5.5**다. 공식 ID `claude-opus-5-5`를 명시한다. `opus` 별칭이 항상 5.5라고 가정하지 않으며 다른 모델로 묵시적 대체하지 않는다. 요청값과 `launch.effective` 일치만으로 실제 작업 성공을 선언하지 않는다.
- 구현·리뷰·QA·기획 워커의 Codex/Claude·모델·effort는 중앙이 자동 배정한다. 사용 가능한 도구·인증을 먼저 보고, 난도·위험·불확실성으로 고른다. 별도 모델 비교를 위해 모든 후보를 상시 실행하지 않는다.
- 기능별 관련 테스트·독립 리뷰·QA 통과 후 **로컬 커밋 자동 진행**을 사용자가 승인했다. 이번 운영 범위에서는 커밋 직전 사용자 승인 대기를 두지 않는다. 사전 기능 단위 합의, 보고, 비밀/기존 변경 보존과 검증은 유지한다.
- PR 준비 때 dev 대비 변경·의존성·문서·QA 증거·리뷰 포인트를 정리한다. push·PR 게시·merge·rebase·이력 재작성은 별도 명시적 승인 범위를 따른다. 기능별 커밋을 임의 squash하지 않는다.

## 설계·리뷰 통과 기준

| 원칙 | 판정 기준 |
|---|---|
| MVP | 수동 View는 표시·입력 전달, 순수 Presenter는 화면 동작 조정. Unity 화면 없이 핵심 Presenter 검증 가능 |
| DDD | 게임 규칙·상태 소유자가 기능 모듈에 모임. 순수 도메인은 Unity·Firebase·구체 View에 의존하지 않음 |
| 응집도 | 함께 바뀌는 규칙·데이터의 책임을 한곳에서 설명 가능. 기능별 변경이 무관한 모듈에 반복 확산되지 않음 |
| 결합도·의존성 | 작고 명시적인 모듈 계약, 순환 참조 없음. 경계의 외부 구현 주입. 다른 모듈 내부 상태·전역 싱글턴 연쇄 접근 지양 |
| 가독성 | 통용 이름·명시적 흐름·작은 책임. 불필요한 인터페이스·계층·미래용 프레임워크도 반려 |

새 UI·대사·안내는 기존 UI 재사용 가능성을 먼저 확인한다. 대사는 시나리오/기존 이벤트 다이얼로그, 안내는 기존 안내 다이얼로그가 후보이며 현재 코드와 기획을 확인한다. 범용 구현은 기능 모듈 밖 공용 소유자로 둔다. 공용화가 실제 중복·변경 이유를 줄이는지도 확인한다.

기능 구현·SDK·Android 빌드 설정 변경 전 저장소의 `docs/ANDROID-PC-COMPATIBILITY.md`를 읽는다. 기본 배포물은 모바일·PC 공통 AAB다. 스토리·경쟁모드 중 실제 영향 범위를 모두 검증한다. Unity .meta, 기존 에셋·서브모듈, 주석·기능별 커밋 크기·완료 문서는 현재 저장소 규칙을 따른다.

## 기획 리딩·스크럼

Jira/Confluence 원문 URL·버전·절을 읽고 명시 내용·추론·사용자 결정을 구분한다. 구현·QA 누락을 대조하고 미결정은 중앙에 올린다. 과거 메모리의 기획을 현재 원문 대신 쓰지 않는다.

스크럼 안건은 기획 충돌·차단·일정 위험·결정 요청을 `문제/영향/선택지/추천/결정 시점`으로 정리한다. 대상 회의·게시 권한·중복 식별 기준이 확정되지 않으면 초안까지 진행한다. 허용된 게시 시 기존 안건을 확인하고 같은 안건을 중복 생성하지 않는다. 기록된 확정 결정만 요구사항·개발·QA 기준에 회수한다.

## 도입 상태와 조사 근거

- Sol low의 Orca 시작/보고/release와 Codex Atlassian Jira·Confluence 읽기는 2026-11-02 실제 검증했다. 다른 모델·역할·복구 동작의 검증을 대신하지 않는다.
- 2026-11-02 Opus 검증 정정: `run_aa276dcea80d`의 `ctx_1f0ba6dd34c2`에서 요청·적용 모델 `claude-opus-5-5`, effort `high`, 입력 수락·턴 시작을 확인했다. 실제 응답은 Claude 주간 한도 초과였고 도구 호출·중앙 보고 왕복은 검증하지 못했다. Claude 표시 초기화 시각은 2026-11-04 04:00 KST다. 이 시각은 당시 화면 근거이며 재시도 때 현재 한도를 다시 확인한다. 한도 해제 후 읽기 전용 도구 호출·status·worker_done 왕복을 검증하기 전에는 개발 코디네이터 운영 가능으로 표시하지 않는다. 검증용 워커는 종료했고 다른 모델로 대체하지 않았다. 모델 ID 근거는 [Anthropic 공식 문서](https://platform.claude.com/docs/en/models/opus-5-5/overview)다.
- Astra high의 독립 검토는 조건부 채택이었다. 권한·정본, 실제 수정 소유권 회수·중앙 복구, 구현 종료와 기능 수용 분리, 모델/도구 확인, 보고 보장 범위를 보완하도록 했다.
- [BMad 공유 설계 결정](https://docs.bmad-method.org/plan/design-ux-and-architecture/), [Spec Kit 명세 대조](https://github.github.io/spec-kit/), [Superpowers 독립 리뷰](https://github.com/obra/superpowers)를 절차 참고로 사용한다. 해당 도구를 설치·호출하거나 Orca를 대체하라는 뜻이 아니다.
- [Savvy Progress](https://github.com/JohnnyVizz/claude-kit/tree/main/plugins/savvy-progress)는 Claude 세션/하위 에이전트 UI, [File Tree](https://github.com/data-goblin/claude-code-filetree)·[Replay Theater](https://github.com/anthropics/claude-code-playground/tree/main/claude-code/mods/replay-theater)는 Claude mod다. Codex의 임의 UI 패널 지원을 의미하지 않는다.
- Codex는 [native HUD 프리셋](https://github.com/ir272/codex-hud)과 이 스킬의 Orca 읽기 전용 HUD를 함께 사용한다. 후자는 lifecycle 상태이며 편집 도구 호출 단위 replay나 임의 사용자 입력 감시는 제공하지 않는다.
