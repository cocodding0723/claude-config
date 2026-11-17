# 작업 카드: <할 일>

labels: `kind:task`(생략 가능) · `file:<고칠 경로 접두>`

순서: 독립 | 순차(<앞 카드 id>) — 계획서 카드 표와 같게. 순차 카드는 앞 카드 작업자가 이어 받는다.

## 지시(사용자 원문 + 뒤 지시)

비우면 board-claim 이 거부한다. 요약하지 말고 그대로 옮긴다. 추가 지시가 오면 이 칸에 덧붙이고 새 작업자에게 준다.

- 원문(<날짜>): "<사용자 말 그대로>"
- 뒤 지시(<날짜>): "<추가 지시 그대로>"

## 완료 기준

- <확인 가능한 결과 한 줄씩>

## 작업

- [ ] 구현
- [ ] 컴파일 → `~/orca/board/bin/board-run compile -- <컴파일 명령>` 이 출력한 `evidence:compile=… commit=…` 줄
- [ ] 관련 EditMode → `board-run editmode --result <xml> -- <EditMode 명령>` 이 출력한 `evidence:editmode=…` 줄(통과 1개 이상, 전부 건너뜀 거부)

## 완료

```bash
board-done <카드 id> <작업자>
```

증거 줄은 카드 notes 나 코멘트에 한 줄씩 단다(`bd comments add <id> "evidence:compile=/abs/compile.log commit=<브랜치 끝>"`).
board-run 로그는 실행한 작업 트리 내용(tree)이 브랜치 끝과 같아야 한다. 커밋 전에 돌렸어도 그대로 커밋했으면 인정된다.
손으로 쓴 로그·빈 로그는 거부된다. Unity 없는 도구 저장소 카드는 `kind:tool` + `board-run test -- <시험 명령>`.
