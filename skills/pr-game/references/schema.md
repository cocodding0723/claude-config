# 데이터 형식

`build.py` 가 최종 기준이다. 틀리면 항목별로 거부한다.

```json
{
  "pr": {"num": 1, "repo": "org/name", "title": "", "url": "", "head": "<headRefOid>", "branch": "<headRefName>", "base": "<baseRefName>"},
  "brief": {
    "summary": ["비개발자도 읽는 3줄"],
    "background": [{"t": "처음 보는 말", "d": "하는 일로 푼 설명"}],
    "problem": "무엇이 문제였나",
    "change": "이제 어떻게 동작하나",
    "unchanged": ["바뀌지 않는 것"],
    "reading": [{"ref": "경로:행", "why": "이 순서로 읽는 이유"}]
  },
  "graph": {
    "nodes": [{"id": "a", "label": "표시 이름", "layer": 0, "state": "same|changed|added|removed",
               "problem": true, "role": "역할", "ref": "경로:행"}],
    "edges": [{"from": "a", "to": "b", "state": "same|added|removed", "problem": true, "label": "선택"}]
  },
  "q": [{
    "kind": "code|structure|impact",
    "level": "learn|check",
    "learn": "learn 일 때 필수. 풀기 전에 보여 줄 설명 2~4문장",
    "terms": [{"t": "용어", "d": "풀이"}],
    "ask": "문항",
    "opts": ["보기 4개"], "ans": 0,
    "miss": ["", "오답마다 왜 틀렸는지", "", ""],
    "why": "정답 근거",
    "ref": "다시 읽을 경로:행",
    "file": "code 를 쓸 때 그 파일 경로",
    "code": ["diff 에서 그대로 옮긴 줄. '-' '+' 를 유지"],
    "diagram": "before|after",
    "hide": "도식에서 ? 로 가릴 노드 id"
  }],
  "why": [{"what": "결정", "reason": "왜", "result": "결과"}],
  "compare": {"note": "선택", "rows": [{"where": "", "before": [""], "after": [""]}]},
  "media": {"before": "pr-json 기준 상대 경로", "after": ""}
}
```

- `pr.branch`·`pr.base`·`file`·`code`·`diagram`·`hide`·`compare`·`media` 는 선택이다. `pr.branch` 를 넣으면 BasakPages 스킨의 코드 링크가 커밋 대신 그 브랜치로 가고, 브랜치가 지워지면 `pr.base` 로 간다. `removed` 노드는 `ref` 가 없어도 된다. `ans` 자리의 `miss` 는 빈 문자열이다.
- `graph` 는 왼쪽부터 `layer` 값 순서로 위에서 아래로 쌓는다. 같은 층 안의 순서는 `nodes` 순서다. 한 층씩 내려가게 두어야 선이 상자를 가로지르지 않는다. 호출하는 쪽을 가운데 층에 두면 판정과 출력을 위아래로 나눌 수 있다.
- `problem` 은 이전 탭에서 빨갛게 표시한다.
- `level`·`learn`·`terms`·`background` 는 선택이다. `level` 을 한 문항에 쓰면 모든 문항에 단다. 배우기 문항이 있으면 `background` 가 필요하다.

## 빌드가 거부하는 것

| 대상 | 규칙 |
|---|---|
| 스니펫 | `+`·`-` 줄이 그 파일 diff 에 없음 |
| 경로 | `--repo` 의 head 에 파일이나 행이 없음. `--repo` 없으면 diff 안의 파일만 허용 |
| 보기 | 4개가 아니거나 중복, 오답 근거 누락, 정답이 최장 오답보다 15% 넘게 김 |
| 트랙 | `level` 을 일부에만 씀, 배우기 3개 미만, 확인 4개 미만, 합 24개 초과, 배우기 문항에 `learn` 없음, `background` 없음 |
| 묶음 (확인 문항, `level` 없으면 전체) | 5문항 미만, 구조 `max(2, n/3)` 미만, 코드·영향 0개, 코드·도식 없는 문항 절반 미만, "왜" 시작 절반 초과, 정답이 최장 보기인 문항 절반 초과 |
| 배우기 묶음 | "왜" 시작 절반 초과, 정답이 최장 보기인 문항 절반 초과 |
| 도식 | 노드 3개 미만, id 중복, 없는 노드를 잇는 간선 |
