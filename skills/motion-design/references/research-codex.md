**반복 조작의 바운스·대기부터 줄이고, 보상 연출은 별도로 조정한다.** 아래는 이를 위한 기준 초안이다.

7/7 주제 조사 완료. **신뢰도는 해당 출처가 권장 규칙을 얼마나 직접 뒷받침하는지**를 뜻한다. 모바일 캐주얼·방치형 게임에서 검증되지 않은 수치·적용 판단은 **‘근거 없음’**으로 표시했다. GDC는 공개 발표 소개문, 논문은 확인 가능한 초록 범위에서 인용했다.

## 1. 게임 UI 모션 가이드와 사례

**권장 규칙**  
모션의 목적을 입력 확인·상태 변화·화면 관계·보상 강조 중 하나로 설명한다.  
반복 구매·업그레이드·탭 전환에서는 연출 완료를 다음 입력의 필수 조건으로 삼지 않는다.  
일상 조작과 특별 보상의 강도를 구분한다. 단, 구체적인 강도 비율은 **근거 없음**.

**근거**

- [Apple HIG — Motion](https://developer.apple.com/design/human-interface-guidelines/motion): 게임의 성공 피드백은 짧고 정확해야 하며, 반복해서 보는 애니메이션 때문에 기다리게 하지 말라고 권고한다. 자주 발생하는 UI 조작에는 불필요한 모션을 피하도록 안내한다.
- [Paul Furio — Animation Subsystem](https://www.gamedeveloper.com/programming/animation-subsystem): 포커·매치3 등 캐주얼 게임에서 게임 상태와 표시를 분리한 실무 패턴을 설명한다. 빠른 연속 입력이 표시 충돌을 일으키므로 연출 순서와 blocking 여부를 명시적으로 관리한다.
- [GDC 2025 — Designing for Presence Without Intrusion](https://gdcvault.com/play/1035099/Thriving-Players-Summit-Designing-for): 모바일·방치형·캐주얼 게임을 직접 대상으로, 지속적인 주의 요구보다 플레이어를 지원하는 설계를 제안한다. **트윈 수치 지침은 아니다.**
- [Riso — Character Level-Up](https://dribbble.com/shots/22360629-Character-Level-Up): *Dragons: Rise of Berk* 기반 모바일 UI 흐름을 After Effects에서 설계하고 Unity로 재현한 제작 사례다. **제작자 포트폴리오이며, 출시판 적용·사용성 개선·권장 시간의 증거는 아니다.**

**신뢰도: 중간.** 원칙과 실무 패턴은 뒷받침되지만, 대상 장르의 공개 모션 표준·비교 실험은 부족하다.

## 2. 트윈 시간·Ease 권장값

**권장 규칙**  
단순 피드백은 약 **100ms**, 모달 등 큰 상태 변화는 **200~300ms**부터 검토한다.  
일상 UI의 **500ms 이상** 연출은 대기 이유를 재검토한다. 전체 Sequence 기준으로 평가한다.  
DOTween 시작안: 버튼 **0.10초**, 팝업 진입 **0.25초**, 퇴장 **0.20초**. 정확한 조합의 게임 검증은 **근거 없음**.  
이동은 감속·가속 곡선을 목적에 맞게 선택한다. `OutQuad`를 Material 곡선과 동일하다고 취급하지 않는다.

**근거**

- [NN/g — Executing UX Animations](https://www.nngroup.com/articles/animation-duration/): 일반 범위 100~500ms, 단순 피드백 약 100ms, 모달 200~300ms를 제시한다. 500ms에서는 지연감이 커지며, 등장보다 퇴장을 짧게 하는 예도 든다. **UX 지침이며 모바일 게임 최적값을 입증한 실험은 아니다.**
- [Material 공식 구현 문서 — Motion](https://github.com/material-components/material-components-android/blob/master/docs/theming/Motion.md): duration 토큰은 Short **50/100/150/200ms**, Medium **250/300/350/400ms**, Long **450/500/550/600ms**, ExtraLong **700/800/900/1000ms**다. 토큰의 존재가 모든 UI에 해당 시간을 권한다는 뜻은 아니다.
- 같은 Material 문서의 utility 곡선은 화면 내 변화 **`(0.2,0,0,1)`**, 진입 **`(0,0,0,1)`**, 퇴장 **`(0.3,0,1,1)`**이다. 현재 문서는 spring 체계도 제공하므로 duration/easing 체계를 유일한 최신 방식으로 보기는 어렵다.
- [Apple HIG — Motion](https://developer.apple.com/design/human-interface-guidelines/motion): 짧고 정확한 피드백을 권고하지만, 공통으로 적용할 **ms·Ease 수치는 제시하지 않는다**.

**신뢰도: 높음—출처의 수치·토큰 / 중간—장르 적용 / 낮음—제안한 정확한 프리셋.**

## 3. 디즈니 애니메이션 12원칙의 UI 적용

**권장 규칙**  
우선 적용: **Staging·Slow in/out·Timing**으로 시선·정착·응답 속도를 정리한다.  
선별 적용: **Squash/stretch·Anticipation·Follow-through/overlap·Arcs·Secondary action·Exaggeration**은 의미 있는 강조에 제한한다.  
**Straight ahead/pose-to-pose·Solid drawing·Appeal**은 제작·시각 설계 관점으로 참고한다. 모든 UI에 움직임을 추가하라는 규칙으로 쓰지 않는다.  
글자 변형·입력 전 예비동작·반복 진동의 허용 수치에는 **근거 없음**.

**근거**

- [Disney Animation — Animation](https://disneyanimation.com/process/animation/): timing, staging, squash/stretch, anticipation, follow-through, secondary action을 **캐릭터를 살아 있게 만드는 원칙**으로 설명한다. UI 사용성 검증 자료는 아니다.
- [Issara Willenskomer — UI Animation Principles: Disney is Dead](https://medium.com/ux-in-motion/ui-animation-principles-disney-is-dead-8bf6c66207f9): easing과 일부 secondary action의 유용성을 인정하면서도, 생물의 물리적 표현을 위한 원칙을 UI에 일괄 이식하는 접근을 비판한다. **전문가 견해이며 실험 결과는 아니다.**
- 위의 우선·선별 분류는 두 관점과 응답성 지침을 종합한 **설계 판단**이다. 특히 게임 보상·캐릭터 UI에서는 일반 업무용 UI보다 과장을 허용할 여지가 있지만, 허용량의 직접 근거는 없다.

**신뢰도: 중간.** 원칙의 목적은 명확하지만 적용 순위는 해석이다.

## 4. 게임필·주스 과다의 경계

**권장 규칙**  
연출 추가 여부는 재미뿐 아니라 **정보 식별·반복 입력·불편감**으로 판단한다.  
흔들림·확대·파티클·숫자 팝업이 같은 정보를 중복 강조하면 각각의 필요성을 검토한다.  
“바운스 1회”, “스케일 1.05 이하”, “동시 연출 2개 이하” 같은 보편적 상한은 **근거 없음**.  
장시간 피로는 별도 반복 플레이로 확인한다. 짧은 선호도 결과를 장기 피로 감소의 증거로 쓰지 않는다.

**근거**

- [GDC Europe 2012 — Juice It or Lose It](https://gdcvault.com/play/1016789/Juice-It-or-Lose): 효과를 추가해 플레이 만족감을 높이는 시연이다. **모든 장르에서 효과량과 재미가 비례한다는 검증은 아니다.**
- [GDC Europe 2014 — Don’t Juice It or Lose It](https://www.gdcvault.com/play/1020937/Don-t-Juice-It-or): 단단한 돌의 바운스, 먼지 없는 곳의 먼지 등 맥락 없는 장식이 몰입을 해칠 수 있다고 비판한다.
- [Hicks 외, CHI PLAY 2019 — Juicy Game Design](https://doi.org/10.1145/3311350.3347171): 두 연구에서 시각적 매력은 개선됐지만, 유능감 등 다른 경험 요소의 개선은 조건부였다. **매력 개선과 조작·성과 개선을 구분해야 한다.**
- [Durmanova, 2022 — The Effects of Juicy Game Design on Exergames](https://uwspace.uwaterloo.ca/items/c9d30c95-d7db-407f-9523-980cc85645ba): 100명 설문에서 일정 수준 이후 장식 증가가 즐거움 증가로 이어지지 않았다. 운동 게임의 미디어 평가이므로 모바일 방치형 UI에 직접 일반화하기 어렵다.

**신뢰도: 중간.** 효과 증가의 한계는 근거가 있으나, 대상 장르의 장기 피로 임계값은 확인하지 못했다.

## 5. DOTween 실무 패턴

**권장 규칙**  
재진입 시 해당 연출의 기존 핸들을 종료하거나 재사용한다. `Kill(false)`는 현재 값에서 멈추므로 재시작·원복 정책을 정한다.  
`Sequence`의 순차는 `Append`, 병렬은 `Join`으로 구성하고, `SetLink`·`SetUpdate`는 루트 Sequence에 설정한다.  
일시정지 중에도 필요한 UI는 `SetUpdate(UpdateType.Normal, true)`를 사용한다.  
Destroy와 Disable을 구분한다. 보상 지급 같은 게임 상태 변경을 취소 가능한 연출 완료에만 의존시키지 않는다.

**근거**

- [DOTween 공식 문서](https://dotween.demigiant.com/documentation.php): `Kill(true)`는 최종값 적용 후 종료하며, 기본 `Kill(false)`는 강제 완료하지 않는다. 완료 후 기본 auto-kill되므로 지속 재사용에는 별도 설정이 필요하다.
- 같은 문서: `SetLink` 기본값은 `KillOnDestroy`다. **Sequence 내부 트윈에 설정하면 효과가 없다.** Disable 시 동작은 `LinkBehaviour`로 선택한다.
- 같은 문서: independent update는 `timeScale`과 `maximumDeltaTime`을 무시한다. 자식의 `SetUpdate`는 무시되고 Sequence 설정이 적용된다. `timeScale=0`에서는 FixedUpdate가 실행되지 않아 independent Fixed 사용을 권하지 않는다.
- 같은 문서: recycling은 트윈 재사용을 지원하지만, 종료된 참조가 다른 트윈을 가리킬 위험이 있어 참조 관리가 필요하다. **재진입·중복 방지·보상 분리 규칙은 API 동작에서 도출한 설계 판단**이다.

**신뢰도: 높음—API 동작 / 중간—권장 소유·취소 패턴.**

## 6. 성능: 오버드로·Canvas 리빌드·GC

**권장 규칙**  
정적 UI와 자주 움직이는 UI를 갱신 빈도에 따라 Canvas로 분리한다. 무조건 요소마다 분리하지 않는다.  
중첩 LayoutGroup의 레이아웃 변경과 큰 반투명 레이어·파티클 중첩을 줄인다.  
숫자 카운트업은 표시값이 바뀔 때 텍스트를 갱신하고, 반복 생성물은 풀링을 검토한다.  
DOTween 도입만으로 리빌드·오버드로·GC가 사라진다고 가정하지 않는다.

**근거**

- [Unity — UI performance optimization tips](https://unity.com/how-to/unity-ui-optimization-tips): Canvas 변경 시 batching 재분석 비용을 설명하고, 정적·동적 요소를 갱신 빈도로 분리하도록 권한다. 중첩 LayoutGroup의 탐색 비용과 겹친 UI의 오버드로도 지적한다.
- 같은 Unity 문서: UI Animator는 값이 같아도 매 프레임 요소를 dirty 처리할 수 있어, 일시적인 UI 변화에는 코드·트윈 시스템을 권한다. **트윈의 실제 속성 변경 비용까지 없어진다는 뜻은 아니다.**
- [Unity — Garbage collection best practices](https://docs.unity.cn/Manual/performance-garbage-collection-best-practices.html): 반복 문자열 결합·임시 할당·클로저를 줄이고 객체를 재사용하도록 안내한다. 점수 텍스트도 값이 변할 때만 갱신하는 예를 제공한다.

**신뢰도: 높음—uGUI 비용 구조 / 중간—개별 프로젝트 개선량.** Canvas 수·파티클 수의 공통 안전 상한은 **근거 없음**.

## 7. 접근성: Reduce Motion과 게임 대응

**권장 규칙**  
게임에 움직임 줄이기 옵션을 제공하고, 시스템 설정도 가능한 범위에서 반영한다.  
확대·회전·패럴랙스·흔들림·장식 루프를 제거하거나 짧은 페이드·정적 강조로 대체한다.  
획득·선택·상태 변화의 의미는 텍스트·아이콘 등으로 유지한다. 단순 재생속도 증가를 대안으로 삼지 않는다.  
시스템 설정을 켰을 때 Unity·DOTween 연출이 실제로 바뀌는지 기기에서 확인한다.

**근거**

- [Apple — Reduced Motion 평가 기준](https://developer.apple.com/help/app-store-connect/manage-app-accessibility/reduced-motion-evaluation-criteria): 2D 화면의 확대·회전도 불편을 유발할 수 있다고 설명한다. 장식은 중단하고, 의미 있는 변화는 dissolve·highlight fade 등의 대안으로 보존하도록 권고한다.
- [Apple — UIAccessibility](https://developer.apple.com/documentation/uikit/uiaccessibility): `isReduceMotionEnabled`와 설정 변경 알림을 제공한다.
- [Google — Android 표시 설정](https://support.google.com/accessibility/android/answer/11183305?hl=en): 애니메이션 삭제 설정을 제공한다. [Android — ValueAnimator](https://developer.android.com/reference/android/animation/ValueAnimator#areAnimatorsEnabled())의 `areAnimatorsEnabled()`는 API 26부터 시스템 animator 활성 상태를 반환한다. 배터리 절약 등도 영향을 주므로 접근성 의도만 식별하는 값은 아니다.
- **플랫폼 API를 게임 모션 정책에 연결하는 것은 구현 판단**이다. 위 API 문서는 Unity·DOTween의 자동 대응을 보장하지 않는다.

**신뢰도: 높음—접근성 원칙·플랫폼 API / 중간—게임 연결 방식.**

## 서로 충돌하는 근거

| 충돌 | 판단 기준 |
|---|---|
| 주스를 늘리면 만족감 증가 ↔ 맥락 없는 주스가 몰입 훼손 | [2012 시연](https://gdcvault.com/play/1016789/Juice-It-or-Lose)과 [2014 비판](https://www.gdcvault.com/play/1020937/Don-t-Juice-It-or)은 목적이 다르다. 해당 UI가 전달할 정보와 반복 빈도로 선택한다. |
| Material에 1000ms 토큰 존재 ↔ NN/g는 500ms에서 지연감 경고 | [Material](https://github.com/material-components/material-components-android/blob/master/docs/theming/Motion.md)은 표현 체계, [NN/g](https://www.nngroup.com/articles/animation-duration/)는 사용성 지침이다. 토큰 최대값을 일상 UI 허용시간으로 쓰지 않는다. |
| 디즈니 원칙 적용 ↔ UI에 일괄 적용 부적합 | [Disney](https://disneyanimation.com/process/animation/)는 캐릭터 표현, [Willenskomer](https://medium.com/ux-in-motion/ui-animation-principles-disney-is-dead-8bf6c66207f9)는 UI 사용성이 중심이다. 같은 화면에서도 캐릭터와 버튼을 다르게 취급한다. |
| 모션 축소 ↔ 상태 이해를 돕는 모션 보존 | [Apple 평가 기준](https://developer.apple.com/help/app-store-connect/manage-app-accessibility/reduced-motion-evaluation-criteria)은 전부 삭제보다 트리거별 대체를 권한다. |

**다음 행동: 가장 자주 누르는 UI 하나에서 ‘대기·이동·바운스·파티클’ 중 불필요한 항목을 표시한다.**