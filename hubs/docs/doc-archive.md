# 문서 보관소 (doc_archive.py)

## 쉬운 말 3줄 요약

- 무엇: Slack 으로 보낸 문서(HTML·PDF)를 `~/Documents/90_정리기록/<날짜-작업 이름>/` 에 복사하고, 색인에 한 줄을 남기는 도구다.
- 왜: 문서가 작업 공간 `docs/out`·임시 폴더·Slack 에 흩어져 있고, 작업 공간을 정리하면 사라진다. 정리기록 폴더를 정본으로 삼는다.
- 사용자가 할 일: 지금은 없다. 1차는 도구와 시험까지이고, 문서를 보낼 때 자동으로 부르는 연결(2차)은 아직 없다.

## 폴더와 색인

```
<보관 뿌리>/                      기본 ~/Documents/90_정리기록 (LRM_ARCHIVE_ROOT 로 변경)
  archive-index.jsonl            색인. 한 줄 = 한 보관
  2026-11-23-우편함 개편/
    plan.html
    plan.pdf                     HTML 옆에 같은 이름 PDF 가 있을 때만 함께 보관
```

색인 한 줄의 칸: `date`, `title`, `card`(작업 카드), `ticket`(티켓 번호), `ticket_url`, `kind`(문서 종류), `thread`(Slack 스레드), `dir`(보관 폴더), `files`(이름·sha256 목록), `archived_at`.

- `ticket_url` 은 환경 변수 `LRM_TICKET_URL_BASE` + 티켓 번호다. 설정이 없으면 빈 칸이다.
- 같은 보관(폴더와 파일 이름·해시 묶음이 같음)을 다시 실행하면 줄이 늘지 않고 그 줄의 메타 칸만 갱신한다.

## 이름이 겹칠 때

- 같은 이름·같은 내용: 다시 복사하지 않는다.
- 같은 이름·다른 내용: 기존 파일을 덮어쓰지 않고 `<이름>-<sha256 앞 8자><확장자>` 로 둔다. 색인에는 새 줄이 생긴다.
- 폴더 이름의 `\ / : * ? " < > |` 와 제어 문자는 `-` 로 바꾸고 80자로 자른다. 한글·공백은 그대로 둔다.

- 날짜 주의: `--date` 기본값이 오늘이라 같은 작업의 문서를 다른 날 보내면 폴더가 갈라진다. 2차 연결에서는 작업 카드를 만든 날처럼 고정된 날짜를 넘겨야 한다.
- 한 번의 `add` 안에서 이름이 같은 파일도 같은 규칙을 따른다(내용이 같으면 한 번만, 다르면 해시 이름). 계획·복사·색인 쓰기는 한 잠금 안에서 해 동시 실행끼리도 덮어쓰지 않는다.

## 명령

```
doc_archive.py add --file docs/out/plan.html --title "우편함 개편" --card lrm-12 --ticket LRM-612 --kind plan --thread 1700000000.0001
doc_archive.py add --file a.html --title 시험 --dry-run     # 쓰지 않고 계획만 출력
doc_archive.py list --card lrm-12 --json
doc_archive.py list --since 2026-11-01
```

종료 코드: 0 성공, 2 사용법 오류, 3 입력 오류(없는 파일·날짜 형식·빈 작업 이름), 4 쓰기 실패. 파일 하나라도 없으면 아무것도 복사하지 않는다.

시험: `bash tests/doc_archive/run.sh` (임시 폴더만 사용).

## 기존 색인과의 관계

`doc_index.py`(문서 모아보기)는 이미 `~/Documents/90_정리기록/**/*.html` 을 수집한다. 보관한 HTML 은 연결 없이 목록에 나온다. `archive-index.jsonl` 은 HTML 이 아니라 수집 대상이 아니다. 새 색인을 따로 만든 이유는 카드·티켓·스레드 같은 메타 칸이 문서 파일에는 없기 때문이다.

## 2차 연결 계획 (아직 하지 않음)

1. 문서를 Slack 에 올리는 곳 `report_sender.py` 의 `send_files()` 에서 업로드가 성공한 직후 `doc_archive.py add` 를 부른다. 보고 기록의 `card`·`thread`(`target`)·`kind` 와 `source_of(rep, path)` 원본 경로를 넘긴다. 실패해도 전송은 실패로 치지 않고 경고만 남긴다.
2. 보고 접수 쪽 `hub_report.py` 의 `stage_files()` 는 첨부를 임시 복사본으로 만든다. 보관은 이 복사본이 아니라 원본으로 해야 한다.
3. 사용자가 모아보기에서 직접 보내는 경로 `doc_send.py` 의 `send()` 는 `slack_notify.sh` 가 성공한 뒤 `SENT_LOG` 를 쓰는 곳 바로 앞에 같은 호출을 둔다.
4. `doc_index.py` 가 `archive-index.jsonl` 을 읽어 `kind`·카드·티켓을 `DocRecord` 에 채우게 한다(경로로 연결).
5. 작업판 카드에 코멘트를 남긴다: `doc-archive: <보관 폴더> | <파일 이름들> | thread=<ts> | ticket=<번호>`.
