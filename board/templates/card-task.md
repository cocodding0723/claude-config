# 작업 카드: <할 일>

labels: `kind:task`(생략 가능) · `file:<고칠 경로 접두>`

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
