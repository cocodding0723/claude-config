# 모션 디자인 리서치 종합 (2026-11-11)

두 리서처가 서로 결과를 보지 않고 같은 질문(7개 주제)을 조사했다. Claude(Fable 5.2, 웹 검색) 원문은 `research-claude.md`, Codex(웹 검색) 원문은 `research-codex.md`. 아래는 SKILL.md 규칙이 어느 근거에서 왔는지와 채택·기각 이유다.

## 합의(두 리서처가 같은 출처·결론에 닿음, 신뢰 높음)

| 규칙 | 출처 | SKILL.md |
|---|---|---|
| 단순 피드백 약 100ms, 모달 200~300ms, 500ms 부터 지연감 | [NN/g](https://www.nngroup.com/articles/animation-duration/) | 2절 표·0.4초 상한 |
| Material duration 토큰 50~1000ms, 감속 곡선 기본 | [Material 구현 문서](https://github.com/material-components/material-components-android/blob/master/docs/theming/Motion.md), [M3 토큰](https://raw.githubusercontent.com/material-components/material-web/main/tokens/versions/v0_192/_md-sys-motion.scss) | 2절 Ease |
| 반복해서 보는 피드백은 짧게, 기다리게 하지 말 것 | [Apple HIG Motion](https://developer.apple.com/design/human-interface-guidelines/motion) | 1·2절 |
| 디즈니 원칙은 캐릭터용. UI 에는 Timing·Slow in/out·Staging 위주, 나머지는 선별 | [Disney](https://disneyanimation.com/process/animation/), [IxDF](https://ixdf.org/literature/article/ui-animation-how-to-apply-disney-s-12-principles-of-animation-to-ui-design), [Willenskomer](https://medium.com/ux-in-motion/ui-animation-principles-disney-is-dead-8bf6c66207f9) | 3절 |
| 주스는 많을수록 좋지 않다. 맥락 없는 효과는 몰입을 해침 | [GDC 2012](https://gdcvault.com/play/1016789/Juice-It-or-Lose), [GDC 2014 반론](https://www.gdcvault.com/play/1020937/Don-t-Juice-It-or), [Hicks 외 CHI PLAY 2019](https://doi.org/10.1145/3311350.3347171), Kao 연구 소개 | 4절 |
| SetLink·SetUpdate 는 Sequence 안 트윈에서 효과 없음, KillOnDestroy 기본, Kill(true/false) 차이 | [DOTween 문서](https://dotween.demigiant.com/documentation.php) | 5절 |
| 움직이는 UI 는 Canvas 분리, 중첩 LayoutGroup 비용, Animator 대신 트윈 | [Unity UI 최적화](https://unity.com/how-to/unity-ui-optimization-tips) | 6절 |
| 움직임 줄이기는 삭제가 아니라 대체(페이드·하이라이트), 의미는 유지 | [Apple Reduced Motion 기준](https://developer.apple.com/help/app-store-connect/manage-app-accessibility/reduced-motion-evaluation-criteria), [Game Accessibility Guidelines](https://gameaccessibilityguidelines.com/provide-an-option-to-turn-off-hide-background-movement), [Xbox 가이드 117](https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/117) | 7절 |

## 한쪽만 찾은 근거(신뢰 중간)

- Claude: Unity `AccessibilitySettings` 에 움직임 줄이기 속성이 없음 → 네이티브 호출 필요([Unity 문서](https://docs.unity.com/en-us/engine/6000.3/script-reference/unityengine/accessibility/accessibilitysettings)). 7절에 채택.
- Claude: 트윈 생성은 할당 → 재사용([CyberAgent 튜닝 바이블](https://cyberagentgameentertainment.github.io/UnityPerformanceTuningBible/en/tuning_practice_third_party.html)). 6절 GC 0 근거로 채택.
- Codex: 반복 입력 시 표시 충돌 → 상태와 표시 분리([Game Developer, Animation Subsystem](https://www.gamedeveloper.com/programming/animation-subsystem)). 2절 "완료를 다음 입력 조건으로 삼지 않음"·5절 "보상은 OnComplete 에 걸지 않음"에 채택.
- Codex: Android `areAnimatorsEnabled()`(API 26+)는 배터리 절약에도 영향 받음([Android 문서](https://developer.android.com/reference/android/animation/ValueAnimator#areAnimatorsEnabled())). 7절 "기기에서 확인"의 근거.

## 기각·보류

- 클릭커 UI 글의 "스케일 100~150ms, 눌림 0.97" — 검색 요약만 확인, 출처 신뢰 낮음. 2절 표의 범위 안이라 별도 규칙으로 두지 않음.
- "바운스 1회·스케일 1.05 이하·동시 연출 2개" 같은 보편 상한 — 두 리서치 모두 근거 없음. 규칙에 수치로 넣지 않고 "참고 영상이 상한"(사내 결정)으로 대신함.
- Material 1000ms 토큰을 일상 UI 허용치로 쓰는 해석 — NN/g 지연감 근거와 충돌. 토큰은 표현 체계일 뿐이라 기각.
- cubic-bezier ↔ DOTween Ease 이름 대응 — 공식 근거 없음. 표의 Ease 는 근사 선택이며, 정확히 맞추려면 AnimationCurve 로 옮긴다.
- "새 트윈 전 DOKill 호출" — 공식 문장 없음. 5절은 "핸들 보관 후 Kill" 관행으로 적고 Kill(true/false) 선택만 공식 근거로 둠.
- 방치형 장르 전용 수치·GDC 모바일 UI 모션 발표 — 두 리서치 모두 찾지 못함.

## 사내 근거(메모리·이슈)

- 텐션업 연출 "너무 과하다" 피드백(2026-11-11) → 참고 영상이 세기 상한, UI 트윈 0.15~0.35초·OutQuad/OutCubic·튕김 최소.
- 성능 예산 먼저(2026-11-10), 움직임은 녹화로 판정(2026-11-04), 메인 메뉴 팔레트(2026-11-05), UI 프리팹 규칙(2026-10-29).
- 탭 스킵 중 동기 Cancel 로 DOTween 목록이 중첩 수정돼 예외 발생한 선례(JobMaker 이슈) → 5절 마지막 규칙.
- 말풍선 표시 시간이 짧아 못 읽는다는 피드백(JobMaker 이슈) → 정보 표시는 읽을 시간을 확보(2절 상한은 장식 트윈에만 적용).
