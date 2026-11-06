# 공통 작업 규칙

- 현재 요청과 저장소 지침부터 확인한다. 사용자가 이미 허용한 작업은 반복 승인 없이 끝까지 수행한다.
- 코드 변경 전 Git 상태·브랜치·upstream과 SessionStart 동기화 결과를 확인한다. dirty·분리된 HEAD·인증 실패·분기 충돌이 있으면 원인을 보고한다. 자동 stash·reset·discard·rebase는 하지 않는다.
- 변경·관련 검증·diff 정리를 끝내고 `review-before-commit`으로 변경 파일, 동작, 검증 결과, 남은 위험을 제시한다. 사용자 검수 승인 전에 새로 stage·commit·push하지 않는다.
- 사용자가 이번 범위의 커밋·push·업로드를 이미 명시적으로 요청했다면 그 승인을 재사용한다. 기능 구현 요청만으로는 Git 게시 승인을 추정하지 않는다. 검수 뒤 변경 범위가 달라지면 변경분을 다시 보여준다.
- stage는 현재 작업 파일만 명시한다. `git add -A`·강제 push 금지. main·master 직접 push는 저장소가 허용한 경우에만 수행한다. push 후 로컬 HEAD와 원격 SHA를 비교한다.
- PR은 `create-pr`, Issue는 `manage-issues`를 사용한다. 초안 작성과 등록 요청을 구분한다. 다른 사람에게 메시지·댓글·리뷰 요청을 보내는 것은 별도 사용자 지시가 있을 때만 한다.
- 한국어로 결과부터 짧고 정확하게 쓴다. 문서 작성은 유형별 문서 스킬(`explain-diff`·`tech-record`·`create-pr`·`work-report`, 문체는 `tech-record/references/house-style.md`), 그 밖의 문서 구조는 `doc-coauthoring`, 문장 편집은 `writing-clearly-and-concisely`, 상투어 정리는 `writing-humanizer`, 표현 검사는 `prose-lint` 중 필요한 것만 읽는다.
- `i-have-adhd`는 명시적으로 호출할 때 적용한다. 압축 때문에 사실·조건·미확인 사항을 생략하지 않는다.
- 설치 성공, 스킬 인식, 자동 선택, 실제 작업 품질, 로컬 테스트, CI, 기기 검증을 서로 다른 근거로 보고한다. 확인하지 않은 결과를 통과로 표시하지 않는다.
