# 시안 카드: <화면 이름>

labels: `kind:mockup` · `file:<고칠 경로 접두>`

## 지시(사용자 원문 + 뒤 지시)

비우면 board-claim 이 거부한다. 요약하지 말고 그대로 옮긴다. 추가 지시가 오면 이 칸에 덧붙이고 새 작업자에게 준다.

- 원문(<날짜>): "<사용자 말 그대로>"
- 뒤 지시(<날짜>): "<추가 지시 그대로>"

## 1. 대응표 (첫 단계, 비우면 완료 불가)

대응표는 `<작업 공간>/evidence/<카드 id>/mapping.md` 에 이 표 그대로 두고 카드에 `evidence:mapping=<경로>` 줄을 단다.
"연결 기능" 칸이 비었거나 `-`·`미정`·`TODO` 인 행이 하나라도 있으면 `board-done` 이 거부한다.
장식만 있는 레이어도 연결 기능에 `없음(장식)` 이라고 적는다.

| PSD 레이어 | 프리팹 노드 경로 | 값(위치·크기·색) | 연결 기능(코드 클래스·메서드) | 데이터 출처 |
|---|---|---|---|---|
| 예) 상단바/코인 | Canvas/Top/CoinText | (120,-40) 200×48 #FFFFFF | WalletPresenter.OnCoinChanged | 지갑 저장소 |

## 2. 작업

- [ ] 대응표 전 행 작성 (연결 기능 빈 칸 0)
- [ ] 프리팹 배치·값 반영
- [ ] 연결 기능 코드 연결
- [ ] 프리팹 값 비교 실행 → `evidence:prefab-diff=<경로>` (불일치 0)
- [ ] 컴파일 → `~/orca/board/bin/board-run compile -- <컴파일 명령>` 이 출력한 `evidence:compile=… commit=…` 줄
- [ ] 관련 EditMode → `board-run editmode --result <xml> -- <EditMode 명령>` 이 출력한 `evidence:editmode=…` 줄(통과 1개 이상, 전부 건너뜀 거부)

## 3. 완료

```bash
board-done <카드 id> <작업자>
```

화면 촬영·녹화는 이 카드에서 요구하지 않는다. 기능 묶음(epic) 완료 때 `board-done --epic` 이 요구한다.
