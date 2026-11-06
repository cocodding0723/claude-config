---
name: csharp-readable-layout
description: C# 코드를 검수하기 쉽게 개행과 중괄호를 정리한다. 리팩토링·기능 구현으로 손댄 C# 파일에 적용하고, 동작은 바꾸지 않는다. "개행 정리", "브레이스 추가", "가독성 정리", 리팩토링 커밋 전 정리 단계에 사용한다. 이름·구조 변경은 하지 않는다(그건 리팩토링 기준·동사 사전 담당).
---

# csharp-readable-layout

목적: **검수자가 블록 경계를 한눈에 보게** 한다. 동작 변경 0.

## 중괄호
- `if`·`else`·`for`·`foreach`·`while`·`using`·`lock` 은 **한 줄이어도 항상 `{ }`**.
- 중괄호는 Allman(새 줄 시작) — 저장소 기존 관례를 따른다.
- **예외 — 얼리 리턴(가드)은 한 줄** (11-04 사용자): 메서드·람다 맨 앞에서 조건이 안 맞아 빠져나가는 `return`·`continue`·`break`·`throw` 는 `if (item == null) return;` 처럼 한 줄로 쓴다. 조건이 120자를 넘거나 빠져나가기 전에 다른 문장이 있으면 블록으로 쓴다. 가드 묶음 뒤에는 빈 줄 1개.
- 식 본문 멤버(`=>`)는 한 줄에 들어가는 순수 식일 때만 유지.

## 개행 (빈 줄)
1. 앞쪽 가드(조건 안 맞으면 return/throw)들 **뒤에** 빈 줄 1개.
2. 의미 단계(준비 → 실행 → 결과 반영·통지)가 바뀌는 곳마다 빈 줄 1개.
3. `return`·`throw` 앞에 빈 줄(블록의 첫 줄이면 생략).
4. `if`/`for`/`foreach`/`while`/`try`/`using` **블록 앞뒤**에 빈 줄(블록이 메서드 첫·끝이면 생략).
5. 지역 변수 선언 묶음과 그것을 쓰는 코드 사이에 빈 줄.
6. 멤버(필드 묶음·생성자·메서드·프로퍼티) 사이 빈 줄 1개. 필드끼리는 묶음으로 붙여 둔다.
7. 빈 줄은 **연속 1개까지**. 여는 `{` 바로 뒤·닫는 `}` 바로 앞에는 빈 줄 없음.
8. 한 줄이 120자를 넘으면 인자·체인을 줄마다 끊는다(`.Where(…)` 마다 새 줄).

## 진행
1. 손댄 파일만 대상(전체 저장소 일괄 정리 금지 — diff 폭발).
2. **공백·중괄호만 바꾼 커밋은 따로** 만든다: 제목 `style: <대상> 개행·중괄호 정리`. 동작 변경 커밋과 섞지 않는다.
3. 검증: 컴파일 + `git diff -w --ignore-blank-lines` 결과가 중괄호 추가 외에 비어 있어야 한다(내용 변경 없음 증명).
4. `.meta`·생성 코드(`*.g.cs`)·서드파티(`Assets/Plugins`, `Packages`)는 손대지 않는다.

## 예
```csharp
// before
public void Apply(Item item){ if(item==null) return; var v=Calc(item); if(v<=0) return; _list.Add(v); Changed?.Invoke(); }

// after
public void Apply(Item item)
{
    if (item == null) return;

    var value = Calc(item);
    if (value <= 0) return;

    _list.Add(value);

    Changed?.Invoke();
}
```
(지역 변수 이름 변경 `v → value` 는 이 스킬 범위가 아니다 — 예시에서는 읽기 위해 바꿨을 뿐, 실제 적용 시 이름은 그대로 둔다.)
