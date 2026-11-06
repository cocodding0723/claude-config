---
name: pretty-code
description: 연애혁명 키우기 C# 코드를 "검수자가 빨리 읽고 믿을 수 있는 코드"로 정리하는 통합 기준. 리팩토링·기능 구현·리뷰 반영 커밋 전에 적용한다. 개행·중괄호(csharp-readable-layout), 의도 주석, 함수 동사 사전, 클래스 네이밍, 매직 값, MVP·DDD·SOLID, 비동기·리소스 수명, 프레임워크(ZLinq·R3·UniTask·VContainer) 활용, 프로젝트 규칙을 한 번에 점검한다. "pretty code", "코드 정리", "가독성 좋게", "좋은 코드 가이드대로" 요청에 사용한다. 동작 변경은 하지 않는다.
---

# pretty-code

검수가 병목이다. 목표는 **검수자가 한 번 읽고 의도를 알고, 위험한 곳만 보면 되는 코드**다. 게임 동작은 바꾸지 않는다.

## 적용 순서 (커밋도 이 순서로 나눈다)

| # | 단계 | 기준 | 커밋 |
|---|---|---|---|
| 1 | 이름 | 함수 동사 사전(`docs/METHOD-VERBS.md`: Get 필수·Find 없을 수 있음·Try 실패 예상, Update/Refresh/Apply, Show/Render/Bind, On, ~Async, Is/Has/Can, Load/Save), 클래스는 역할이 드러나는 행위자 이름(-er/-or, 보편 관례 Format·Rule·Policy·Config·State·Factory·Repository 허용, Util·Helper·Logic·Common·Misc 금지) | `refactor: … 이름 정리` (순수 개명만) |
| 2 | 값 | 매직 숫자·문자열 → 소유 모듈 한 곳(설정 객체·enum·시트), 흩어진 const 금지, `public static` 전역 값 금지 | `refactor: … 값 정리` |
| 3 | 구조 | MVP(View 는 Render 만, Presenter 는 Unity 비의존), DDD(도메인 순수·포트/어댑터), SOLID, 결합↓ 응집↑, 바깥과 닿는 곳·구현 2개 이상만 인터페이스, 순수 규칙은 `sealed` | 구조 변경은 **structure-change-doc 구조안 승인 뒤** |
| 4 | 안전 | 비동기 취소 토큰·화면 닫힘 취소·`async void` 금지, 구독·Addressables 해제, null 경계(필수 데이터는 바로 오류), 멱등(두 번 불러도 같은 결과) | `fix:` 또는 `refactor:` |
| 5 | 프레임워크 | `System.Linq` → ZLinq(`AsValueEnumerable()`), 이벤트 → R3, 코루틴 → UniTask, `new` 직접 생성 → VContainer 주입, 로그 → `LogEngine`, 화면 문구 → 번역 키 | `refactor: … ZLinq 전환` 등 묶음별 |
| 6 | 주석 | 아래 「의도 주석」 | 해당 커밋에 포함 |
| 7 | 모양 | `csharp-readable-layout`: 한 줄 if 도 중괄호(얼리 리턴 가드는 한 줄), 가드 뒤·단계 사이·블록 앞뒤 빈 줄, 연속 빈 줄 1개, 120자 넘으면 끊기 | `style: … 개행·중괄호 정리` (공백·중괄호만) |

### 리팩토링 진행 기준

- **검증 가능한 단위로 쪼갠다.** 바꾸기 하나 → 컴파일·관련 테스트 → 다음 바꾸기. 묶어서 한 번에 확인하지 않는다.
- 커밋 순서도 리뷰어가 따라가며 매 단계 초록을 확인할 수 있게 쌓는다.
- **새 API 를 만들면 호출처를 같은 묶음에서 옮기고 옛 API 는 지운다.** 외부 사용자가 없는 내부 API 에 호환 계층을 남기지 않는다.
- 옛 구현 세부만 지키던 테스트는 지우고, 새 계약을 확인하는 테스트로 바꾼다.
- **테스트는 동작을 확인한다.** 호출자가 볼 수 있는 결과(반환값·예외·다시 읽은 상태·외부 효과)만 검사하고, 내부 함수·호출 순서·중간 데이터는 검사하지 않는다.
- 점검: 동작을 일부러 틀리게 하면 실패해야 하고, 결과는 같고 내부만 바꾸면 통과해야 한다. 둘 중 하나라도 어긋나면 테스트를 고친다.
- **완료는 실제 결과물로 증명한다.** 컴파일 통과·자기 보고·낡은 스크린샷·파일 시각 같은 대리 신호가 아니라 기능을 돌려 실제 값·화면·diff 를 직접 본다.
- 가능하면 같은 비교를 다시 돌릴 수 있는 스크립트로 확인하고 그 출력을 남긴다. 확인이 실패하면 시스템보다 관찰 방법부터 의심한다.

## 의도 주석 (11-04 사용자: "의도가 담긴 주석을 넣어서 관리")

넣는다:
- **왜 이렇게 했나** — 다른 방법을 안 쓴 이유, 기획 근거, 순서가 중요한 이유. 예: `// 배율은 곱할 때마다 버림. 순서를 바꾸면 결과가 1~2 달라짐 — 출처 등록 순서 유지`
- **지켜야 할 약속** — 호출 순서, 스레드·프레임, 한 번만 불려야 함, null 이 오는 경우.
- **임시·출시 전 바꿀 것** — `// 임시: 승급 비용 100 고정. 출시 전 시트 이관(RELEASE-CHECKLIST)`
- 공개 API·포트·DTO 는 `///` 요약 1줄(+매개변수·반환·예외는 정보 그대로) — `xml-doc-readable` 기준.

넣지 않는다:
- 코드를 그대로 다시 읽는 주석(`// i 를 1 증가`), 변경 이력, 작성자, 티켓 번호만 있는 주석.
- 문체: 명사형 압축(`~함`, `~ 유지`). `~합니다` 금지.
- 정리: 기존 주석은 `comment-lint` 로 걸러 "왜"만 남긴다.

## 검증 (커밋마다)
1. 컴파일(Roslyn 빠른 컴파일 가능) 오류 0.
2. `style:`·순수 개명 커밋: `git diff -w --ignore-blank-lines` 가 중괄호·이름 외 비어 있음.
3. 관련 EditMode(통합 테스트는 QA 허브).
4. 손댄 파일만 대상. 저장소 일괄 정리·`.meta`·`*.g.cs`·`Assets/Plugins`·`Packages` 금지.
5. 커밋 크기: 한 개념, 코드 3~10파일·400줄 이하, 15파일 초과 금지.

## 검수자에게 보낼 때
PR 본문 맨 위에 "이 PR 은 동작 변경 없음 / 있음(어디)"을 한 줄로. 동작 변경이 있는 커밋만 리뷰 포인트로 짚는다.

## 참고

- 「리팩토링 진행 기준」 개념 출처: cursor/plugins `pstack/skills/principle-sequence-verifiable-units`, `pstack/skills/principle-migrate-callers-then-delete-legacy-apis`, `pstack/skills/principle-test-behavior-not-implementation`, `pstack/skills/principle-prove-it-works` (라이선스 없음, 문구 미사용)
