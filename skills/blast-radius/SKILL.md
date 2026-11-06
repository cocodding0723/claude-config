---
name: blast-radius
description: PR 전에 diff 밖에서 깨질 곳(호출처·구독·직렬화·프리팹 참조·Addressables 주소·시트 키)을 찾고, "안전하다"는 판단을 테스트·컴파일·grep 실행 결과로 증명한다. "영향 범위", "이거 바꾸면 어디 깨져", "blast radius" 요청이나 믿기 어려운 작은 diff 검토에 사용한다.
---

# blast-radius

diff 안을 읽는 것은 리뷰다. 이 스킬은 **diff 밖에서 깨지는 것**을 찾는다. 결론은 글이 아니라 실행 결과로 낸다.

## 핵심 규칙

1. **안전 근거 한 가지를 먼저 찾는다.** 위험해 보이는 변경 대부분은 사실 하나("이 필드는 저장되지 않는다", "이 이벤트 구독자는 한 곳") 때문에 안전하다. 그 사실을 찾고 증명하는 데 시간을 쓴다.
2. **증거 단계를 적는다.** 각 근거마다 어디까지 확인했는지 표시한다.
   - 0 말로만 / 1 `파일:줄` 제시 / 2 실패 경로를 따라가 막히는 지점 확인 / 3 실행(컴파일·EditMode·grep·node 스크립트) / 4 앱에서 재현(녹화 스모크)
   - 3 미만인 근거는 "미검증"으로 보고한다.
3. **grep 이 못 보는 연결을 따라간다.** 아래 표를 전부 훑는다.

## Unity/C# 연결 점검표 (연애혁명 키우기)

| 연결 | 깨지는 방식 | 확인 명령 예 |
|---|---|---|
| 호출처·상속 | 시그니처·의미 변경, 간접 상속 MonoBehaviour | `grep -rn "Method(" Assets --include=*.cs` |
| R3·이벤트 구독 | 발행 시점·순서 변경, 해제 누락 | `grep -rn "Subscribe\|OnNext" Assets` |
| 직렬화 | 필드 개명 → 프리팹·에셋 값 유실, 저장 JSON·Firestore 필드명 변경 | `grep -rn "m_필드명\|FormerlySerializedAs" Assets` |
| 프리팹·씬 참조 | 클래스 이동 → `.meta` GUID 끊김, Missing Script | `grep -rl "guid: <GUID>" Assets --include=*.prefab --include=*.unity --include=*.asset` |
| Addressables 주소 | 주소 문자열·그룹 변경, 낡은 번들 | `grep -rn "주소" Assets/AddressableAssetsData` + 번들 mtime 대조 |
| 시트·데이터 키 | 시트 열·키·enum 코드젠 불일치 | 데이터 JSON·생성 enum grep |
| VContainer 등록 | 등록 누락은 런타임에만 터짐 | `grep -rn "Register<" Assets` + 부트 스모크 |
| 번역 키 | 키 삭제·이름 변경 | 번역 테이블 grep (파일 직접 수정 금지) |

## 절차

1. diff 와 커밋에서 추가·변경·삭제 심볼과 **diff 가 말하지 않는 동작 변화**를 적는다.
2. 안전 근거 후보를 1~2개로 좁힌다.
3. 점검표를 훑어 연결마다 명령을 실행하고 결과를 남긴다.
4. 위험이 남으면 가장 싼 검증(컴파일 → 관련 EditMode → 필요할 때만 `verify.sh` 녹화 스모크)으로 확인한다.
5. 보고: 근거별 `명령 — 결과`와 증거 단계, 미검증 목록, 리뷰어가 볼 곳.

## 하지 않는 것

- 호출처 목록 나열로 끝내지 않는다. 그것은 출발점이다.
- 실행 없이 "영향 없음"이라고 쓰지 않는다.
- 이 스킬은 수정하지 않는다. 고칠 곳은 보고하고 승인 뒤 다룬다.

## 참고

- 개념 출처: cursor/plugins `pstack/skills/blast-radius` (라이선스 없음, 문구 미사용)
