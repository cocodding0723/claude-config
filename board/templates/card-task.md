# 작업 카드: <할 일>

labels: `kind:task`(생략 가능) · `file:<고칠 경로 접두>`

순서: 독립 | 순차(<앞 카드 id>) — 계획서 카드 표와 같게. 순차 카드는 앞 카드 작업자가 이어 받는다.

## 완료 기준

- <확인 가능한 결과 한 줄씩>

## 작업

- [ ] 구현
- [ ] 컴파일 → `evidence:compile=<로그 경로>`
- [ ] 관련 EditMode → `evidence:editmode=<결과 xml 경로>`

## 완료

```bash
board-done <카드 id> <작업자>
```

증거 줄은 카드 notes 나 코멘트에 한 줄씩 단다(`bd comments add <id> "evidence:compile=/abs/compile.log"`).
파일은 카드를 집은 뒤 새로 만든 것이어야 한다.
