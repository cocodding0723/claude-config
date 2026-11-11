# 게임 UI 모션 연출 기준 리서치 (Claude 독립 조사)

조사일 2026-11-11. 대상: Unity 모바일 캐주얼·방치형, 2D 플랫 UI, DOTween.
표기: 직접 열어 확인한 페이지만 URL 을 적었다. 열지 못했거나 검색 요약으로만 본 내용은 `근거 없음` 또는 `검색 요약만`으로 표시했다.

요약 3줄
- 자주 나오는 연출은 100~200ms, 팝업·화면 전환은 200~300ms, 400ms 를 상한으로 잡는다. 기본 Ease 는 감속(ease-out)이다.
- "과하다"는 피드백은 연구와도 맞는다. 주스는 중간 수준이 가장 좋고 없음·극단은 둘 다 나쁘다. 강한 연출은 드문 순간에만 쓴다.
- DOTween 은 수명 연결(SetLink)·재진입 전 Kill·독립 시간(SetUpdate)을 공통 규칙으로 두고, 움직이는 UI 는 캔버스를 분리한다.

---

## 1. 게임 UI 모션 가이드(모바일 캐주얼·방치형)

권장 규칙
- UI 연출은 "빠르고 은은하게"를 기본으로 한다. 플레이어를 기다리게 하는 연출은 넣지 않는다.
- 반복 횟수가 많은 조작(탭, 재화 획득, 강화 버튼)일수록 짧고 작게 한다. 방치형은 같은 연출을 수백 번 보므로 이 규칙이 가장 먼저 적용된다.
- 연출이 끝나기를 기다리지 않고 다음 입력을 받는다(탭으로 건너뛰기 포함).
- 방치형 전용 수치(보상 팝업·코인 비행 시간 등)는 `근거 없음`. 아래 2번의 일반 UI 수치를 시작값으로 쓰고 플레이 테스트로 조정한다.

근거
- https://bugnet.io/blog/how-to-add-juice-to-your-ui — "UI juice should enhance, not distract or slow down", "excessive or slow UI animation becomes annoying". 수치는 없다.
- https://developer.apple.com/tutorials/data/design/human-interface-guidelines/motion.json (Apple HIG Motion 본문 데이터) — "avoid adding motion to UI interactions that occur frequently", "don't make people wait for an animation to complete before they can do anything, especially if they have to experience the animation more than once". 게임 항목: 성공한 행동에 정확히 묶인 간결한 애니메이션이면 플레이어가 "게임 플레이에서 주의를 뺏기지 않고" 메시지를 받는다.
- 검색 요약만(페이지 미확인): 클릭커 UI 글의 "스케일 연출 100~150ms", 버튼 눌림 scale 0.97·160ms, 팝업 시작 scale 0.95 이상(0 에서 시작 금지). 출처 신뢰도가 낮아 참고값으로만 둔다.
- GDC 의 모바일 UI 모션 전용 발표는 찾지 못했다. `근거 없음`.

신뢰도: 중간(원칙은 Apple 공식, 방치형 전용 수치는 없음)

## 2. 트윈 시간·Ease 권장값

권장 규칙
- 시간: 단순 피드백(토글·버튼) 약 100ms, 팝업·패널 200~300ms, 큰 이동 최대 400ms. 500ms 이상은 특별한 순간(희귀 보상 등)에만 쓴다.
- 등장은 퇴장보다 길게: 등장 300ms 라면 퇴장 200~250ms.
- Ease: 등장·기본은 감속(DOTween `OutQuad`/`OutCubic` 계열), 퇴장은 가속(`InQuad` 계열). `Linear` 는 이동에 쓰지 않는다.
- 시간·Ease 는 흩어진 숫자 대신 토큰(짧음·보통·김 × 표준·감속·가속) 몇 개로 묶어 한 곳에서 관리한다.

근거
- https://www.nngroup.com/articles/animation-duration/ — 대부분 100~500ms. "It is far more common for animations to be too long than too short." 체크박스·토글 약 100ms, 모달·패널 200~300ms, 400ms 는 큰 화면의 큰 이동용, "At 500ms, animations start to feel like a real drag". 팝업 등장 300ms / 퇴장 200~250ms. "the more frequent the animation, the more subtle and shorter". 기본은 ease-out, 선형은 "weird and unnatural".
- https://raw.githubusercontent.com/material-components/material-web/main/tokens/versions/v0_192/_md-sys-motion.scss (Material 3 공식 토큰 소스) — duration short1~4 = 50/100/150/200ms, medium1~4 = 250/300/350/400ms, long1~4 = 450/500/550/600ms, extra-long1~4 = 700~1000ms. easing standard = cubic-bezier(0.2, 0, 0, 1), standard-decelerate = (0, 0, 0, 1), standard-accelerate = (0.3, 0, 1, 1), emphasized-decelerate = (0.05, 0.7, 0.1, 1), emphasized-accelerate = (0.3, 0, 0.8, 0.15).
- Material 3 의 "어떤 토큰을 언제 쓰는가" 설명 페이지(m3.material.io)는 본문이 렌더링되지 않아 확인 못 했다. 용도 매핑은 `근거 없음`.
- Apple HIG(위 URL) — "Aim for brevity and precision in feedback animations." 구체적 ms 값은 제시하지 않는다.
- cubic-bezier 값과 DOTween Ease 이름의 대응은 직접 확인한 출처가 없다. `근거 없음`(필요하면 AnimationCurve 로 그대로 옮긴다).

신뢰도: 높음(NN/g·Material 토큰 수치 일치). 단, 웹·앱 기준이며 게임 전용 연구는 아니다.

## 3. 디즈니 12원칙의 UI 적용

권장 규칙
- 항상 쓰는 것: 타이밍, 슬로 인·아웃(Ease), 스테이징(한 번에 한 곳만 강조), 보조 동작(주 동작을 받쳐주는 정도).
- 조건부로 쓰는 것: 예비 동작(눌림 피드백 정도), 팔로 스루·겹침(목록 항목 시차 등 위계 표현).
- 줄일 것: 스쿼시·스트레치와 과장. 플랫 UI 에서는 아주 작게, 드문 순간에만 쓴다. 호(arc)·솔리드 드로잉·스트레이트 어헤드는 UI 에서 쓸 일이 적다.
- 동시에 여러 요소가 경쟁해 움직이면 스테이징 위반이다. 덜 중요한 요소의 움직임은 최소로 둔다.

근거
- https://ixdf.org/literature/article/ui-animation-how-to-apply-disney-s-12-principles-of-animation-to-ui-design — 과장: "if you exaggerate too much, it may annoy users". 스테이징: "keep the motion of everything else (that's not as important) to a minimum". 보조 동작은 주 동작을 "support or emphasize" 해야 한다. 전체 경고: 잘못 쓰면 "distracting and even nauseating", "animation for the sake of animation will result in a poor user interface".
- https://cssanimation.rocks/principles/ — 가장 유용한 원칙으로 무게감·예비 동작·보조 동작·타이밍을 꼽는다. 스쿼시·스트레치는 "It's possible to ignore this rule when working with elements of a web page", 솔리드 드로잉은 3D 용.
- "Disney is Dead"(UX in Motion, Medium)은 403 으로 열지 못했다. 검색 요약만: 과장은 UI 에 "hardly relevant"라는 비판적 입장.

신뢰도: 중간(공식 표준이 아니라 디자인 교육 글. 두 출처의 방향은 일치)

## 4. 게임필·주스 과다 경계

권장 규칙
- 주스는 "중간"이 목표다. 없음도 나쁘고 극단도 나쁘다.
- 강한 연출(흔들림·플래시·큰 스케일 펀치·파티클 다발)은 드문 사건에만 쓴다. 모든 입력에 같은 강도를 걸지 않는다.
- 연출이 정보를 가리면 실패다. 숫자·버튼·상태 표시 위에 이펙트가 덮이지 않게 한다.
- 화면 흔들림은 UI 화면에서 기본값으로 쓰지 않는다(7번 접근성 근거와 같음).

근거
- https://www.goodreads.com/author_blog_posts/19932694-the-effects-of-juiciness-in-an-action-rpg-new-study (Kao 연구 소개 글 미러) — 같은 액션 RPG 를 주스 없음·중간·높음·극단 4단계로 비교, "both Medium Juiciness and High Juiciness outperform No Juiciness and Extreme Juiciness across all measures". 표본 N=3018, Entertainment Computing 2020 게재는 검색 요약만. 논문 원문은 열지 못했다. 장르가 액션 RPG 이므로 방치형 UI 로의 일반화는 추정이다.
- https://gamedeveloper.com/design/video-indies-resist-the-urge-to-juice-it-or-lose-it- — Folmer Kelly, GDC Europe 2014. 2012년 "Juice It or Lose It"(Jonasson·Purho)에 대한 반론: "we're actually losing a level of immersion", "the context doesn't get enough consideration".
- https://lawofgamedesign.com/2016/04/18/theory-too-much-juice/ — 주스가 "just plain excessive" 할 수 있다. 게임에 중요한 요소가 이펙트에 묻혀 "nigh-impossible even to see".
- 피로 누적을 직접 측정한 연구는 찾지 못했다. `근거 없음`. 포럼 의견 수준(모든 행동에 흔들림이 걸리면 거슬린다)은 검색 요약만.

신뢰도: 중간(실증 연구 1건은 2차 출처로만 확인, 나머지는 실무자 의견)

## 5. DOTween 실무 패턴

권장 규칙
- 모든 UI 트윈에 `SetLink(gameObject)` 를 붙여 오브젝트 파괴 시 함께 죽게 한다. 단, Sequence 안에 넣은 트윈에는 효과가 없으므로 Sequence 자체에 건다.
- 재진입(같은 대상에 다시 재생) 전에는 기존 트윈을 먼저 Kill 한다. 중간 상태로 남으면 안 되는 값은 `Kill(complete: true)` 로 끝값을 확정한다.
- 일시정지(timeScale 0) 중에도 움직여야 하는 UI 는 `SetUpdate(true)`. Sequence 에 넣은 경우 Sequence 쪽 설정만 적용된다.
- 자주 재생하는 연출은 `SetAutoKill(false)` + `Restart()` 로 재사용하되 반드시 수명을 직접 관리한다(Kill 또는 SetLink). 보관한 참조는 `OnKill` 에서 null 로 비운다.

근거
- https://dotween.demigiant.com/documentation.php (공식 문서)
  - SetLink: "Links this tween to a GameObject and assigns a behaviour depending on its active state.", "`LinkBehaviour.KillOnDestroy` is always evaluated—and set by default", "has no effect if the tween is added to a Sequence". 예시로 `PauseOnDisableRestartOnEnable` 확인. 나머지 LinkBehaviour 값 목록은 확인 못 함(`근거 없음`).
  - SetUpdate: isIndependentUpdate 가 TRUE 면 "the tween will ignore Unity's Time.timeScale and maximumDeltaTime", "Has no effect if the tween is nested inside a Sequence (only the Sequence's eventual SetUpdate will count)".
  - Kill: "complete If TRUE instantly completes the tween (setting the target to its final value) before killing it." 기본은 완료 시 자동 Kill. IsActive: "Returns FALSE if the tween has been killed."
  - Sequence: 한 트윈은 하나의 Sequence 에만 넣을 수 있고("you can't reuse the same tween in multiple Sequences"), 넣은 뒤에는 개별 제어 불가, 내부 트윈은 무한 루프 불가.
  - 용량: 기본 "200 Tweeners and 50 Sequences", 시작 시 `SetTweensCapacity` 로 "avoid hiccups when it's raised automatically".
  - Safe mode: 대상 파괴를 자동 처리하지만 "slightly slower". 참조 정리: `.OnKill(()=> myTweenReference = null)`.
  - SetId: "using an int or string ID makes filtered operations much faster".
- https://cyberagentgameentertainment.github.io/UnityPerformanceTuningBible/en/tuning_practice_third_party.html — 트윈 생성은 "basically involves memory allocation", 자주 재생하면 재사용. "a tween that calls SetAutoKill(false) will leak if it is not explicitly destroyed". 무한 루프·AutoKill(false) 는 SetLink 로 수명 연결. 에디터의 [DOTween] 인스펙터로 누수 확인.
- "새 트윈 시작 전 DOKill 을 호출하라"는 문장은 공식 문서에서 확인하지 못했다. 같은 속성에 트윈 두 개가 동시에 걸리면 충돌한다는 실무 관행에서 나온 규칙이다. `근거 없음`(공식 근거).

신뢰도: 높음(공식 문서). 재진입 규칙만 중간.

## 6. 성능: 오버드로·캔버스 리빌드·GC

권장 규칙
- 움직이는 UI 는 별도(하위) 캔버스로 분리한다. 요소 하나가 바뀌면 그 캔버스 전체가 다시 만들어진다.
- UI 연출은 Animator 대신 코드·트윈으로 한다. Animator 는 값이 안 변해도 매 프레임 요소를 더럽힌다.
- 트윈 대상의 부모에 Layout Group 을 두지 않는다. 크기·위치 트윈이 레이아웃 재계산을 계속 유발한다.
- 화면을 숨길 때는 GameObject 비활성 대신 Canvas 컴포넌트를 끈다. 전체 화면 반투명 레이어를 겹쳐 쌓는 연출은 오버드로를 늘린다.

근거
- https://unity.com/how-to/unity-ui-optimization-tips (Unity 공식) — "When a single element changes on the UI Canvas, it dirties the whole Canvas." "Animators will dirty their UI Elements on every frame, even if the value in the animation does not change." → 가끔만 변하는 요소는 코드나 트윈 사용. "Avoid layout groups when possible." "Disable the Canvas component itself." 겹쳐 쌓은 UI 는 "creates overdraw". 움직이지 않는 요소는 Raycast Target 끄기.
- https://cyberagentgameentertainment.github.io/UnityPerformanceTuningBible/en/tuning_practice_ui.html — 중첩 캔버스는 "a rebuild of the child Canvas will only run, not the parent Canvas". 단 캔버스를 나누면 배칭이 깨질 수 있으니 묶음 단위를 정한다. "Layout rebuilds, like mesh rebuilds, are costly processes."
- GC: 5번의 CyberAgent 문서(트윈 생성 = 메모리 할당, 재사용 권장). 트윈 1회당 할당량 수치는 `근거 없음`.
- Unity Learn 의 "Optimizing Unity UI" 강좌는 "no longer available" 로 내려가 있어 인용하지 않았다.

신뢰도: 높음(Unity 공식 + 대형 스튜디오 공개 문서)

## 7. 접근성: 움직임 줄이기

권장 규칙
- 게임 설정에 "연출 줄이기" 토글을 둔다. 켜면 이동·스케일·흔들림을 페이드 또는 즉시 전환으로 바꾸고, 진행 표시 같은 기능적 표시는 남긴다.
- OS 설정을 초기값으로 읽는다. iOS 는 Reduce Motion, Android 는 애니메이션 배율이 0 인지로 판단한다. Unity 기본 API 에는 없어서 네이티브 호출이 필요하다.
- 화면 흔들림·깜빡임·배경 반복 움직임은 끌 수 있게 하거나 쓰지 않는다.
- 중요한 정보를 움직임으로만 전달하지 않는다(소리·햅틱·정적 표시 병행).

근거
- https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/117 (Xbox 접근성 지침 117) — 움직이거나 깜빡이는 콘텐츠를 "entirely disable" 또는 "pause or hide" 하는 수단 제공. "Avoid the use of camera shake, camera bobbing effects, motion blur ... or provide an option to turn off". 반복적인 좌우·상하 화면 움직임은 핵심 플레이가 아니면 피한다. Halo Infinite 는 화면 흔들림 강도를 0~100% 로 조절.
- https://gameaccessibilityguidelines.com/provide-an-option-to-turn-off-hide-background-movement — "Background animation can distract greatly from elements that are important to gameplay."
- Apple HIG(1번 URL) — 움직임이 중요한 정보를 전달하는 유일한 수단이면 안 되고 햅틱·오디오로 보완한다.
- https://docs.unity.com/en-us/engine/6000.3/script-reference/unityengine/accessibility/accessibilitysettings — Unity `AccessibilitySettings` 는 fontScale·isBoldTextEnabled·isClosedCaptioningEnabled 만 제공한다. 움직임 줄이기 속성은 없다(Android·iOS 지원).
- https://developer.android.com/reference/android/provider/Settings.Global — `ANIMATOR_DURATION_SCALE`: "Scaling factor for Animator-based animations."
- https://dev.to/eevajonnapanula/android-animations-and-reduced-motion-2em2 — Android 는 전용 API 가 없고 "The animation duration scale is 0f if reduced motion-setting is enabled." `Settings.Global.getFloat` 로 읽는다.
- iOS `UIAccessibility.isReduceMotionEnabled` 공식 페이지는 본문이 렌더링되지 않아 확인 못 했다. API 이름·동작은 `근거 없음`(널리 알려진 내용이지만 이번 조사에서 원문 미확인).

신뢰도: 높음(지침), 중간(Unity 에서의 감지 구현 방법)

---

## 서로 충돌하는 근거

1. 주스 많을수록 좋다 vs 줄여라 — "Juice It or Lose It"(2012)는 연출을 쌓을수록 재미있어진다고 봤고, Folmer Kelly(2014)와 Kao 연구는 과하면 몰입·경험이 떨어진다고 본다. Kao 결과는 "없음도 나쁘다"이므로 결론은 제거가 아니라 중간 수준이다.
2. 자주 쓰는 조작에 모션을 넣지 마라 vs 모든 입력에 피드백을 줘라 — Apple HIG 는 "앱에서" 빈번한 조작의 모션을 피하라고 하고, 게임 UI 글은 모든 상호작용에 눈에 보이는 반응을 권한다. 절충: 반응은 주되 100ms 안팎·작은 변화량으로 제한한다.
3. 시간 상한 — NN/g 는 400ms 를 사실상 상한으로 보지만 Material 3 토큰은 1000ms 까지 정의한다. 토큰 존재가 권장을 뜻하지는 않으며, 긴 값의 용도 설명은 확인하지 못했다.
4. 희귀 보상은 길게 vs 전부 짧게 — 검색 요약만 확인한 글에서 "희귀할수록 공개 연출을 느리게"를 권한다. NN/g 의 "빈도가 높을수록 짧게"와 방향은 같다(드문 것은 길어도 됨). 다만 방치형 수치 근거는 없다.
5. 과장·스쿼시 — IxDF 는 과장을 개성 표현 수단으로 인정하면서 과하면 짜증을 유발한다고 하고, cssanimation.rocks 는 UI 에서 스쿼시·스트레치를 무시해도 된다고 한다. 플랫 UI 에서는 "작게, 드물게"로 정리된다.
6. 캔버스 분리 — Unity 는 동적 요소 분리를 권하지만 CyberAgent 문서는 분리가 배칭을 깨 드로콜을 늘릴 수 있다고 경고한다. 화면·연출 묶음 단위로만 나눈다.
7. Safe mode·SetLink — 둘 다 파괴된 대상을 안전하게 처리하지만 Safe mode 는 "slightly slower" 다. 어느 쪽을 주 방어선으로 둘지에 대한 공식 권고는 확인하지 못했다. `근거 없음`.
