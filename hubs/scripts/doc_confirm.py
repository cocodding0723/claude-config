#!/usr/bin/env python3
"""문서 사이트 확인 목록(GET·POST /api/confirm 뒤쪽). doc_browser.py 의 처리기만 부름. 표준 라이브러리 + 공용 모듈만 사용.

읽기: 진행 스냅샷(progress.json) → confirm_items.build → 항목 목록(안 함 먼저). 수집은 서버의 수집 회차(refresh)가 하고 요청은 캐시만 읽음.
쓰기('했음' 버튼): 화면은 항목 키 하나만 보냄(알 수 없는 키는 요청 형식 오류). 서버가 현재 목록에서 그 항목을 찾아
  확인 가능한 종류(결정·병합 제외)이고 아직 안 함일 때만 확인 기록(confirm-log.jsonl) → 담당 허브에 '사용자 원문: 확인 완료: … (출처: 문서 사이트)' 한 줄.
  문구는 서버가 목록의 제목으로 조립함. 이미 확인한 항목은 다시 전달하지 않음. 허브를 모르면 메인으로 보냄.
감사 로그: docsite-audit.jsonl 에 {at, source, kind: confirm, key, result} 한 줄. 본문·토큰은 남기지 않음.
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import answer_route  # noqa: E402
import answer_store  # noqa: E402
import confirm_items as ci  # noqa: E402
import doc_decide  # noqa: E402
import progress_collect as pc  # noqa: E402
import todo_facts  # noqa: E402
import todo_items  # noqa: E402

REQUEST_KEYS = {"id"}
RELATED_MAX = 2
SNAP_MAX_AGE_S = 600  # 수집기가 이만큼 멈췄으면 목록이 낡았다고 알림
KIND_TEXT = {ci.REVIEW: "검수 자료", ci.COMMAND: "직접 실행 명령", ci.SHEET: "시트·콘솔 작업", ci.WINDOW: "창 승인"}
SAY = "확인 완료: {title} ({kind})"


def related_docs(item: dict, recs: list, limit: int = RELATED_MAX) -> list:
    """항목과 관련된 문서 [{id, title}] 최근 순. 결정은 그 번호를 가진 문서, PR 은 제목에 #번호가 든 문서, 그 밖은 같은 허브 문서"""
    if item["did"]:
        hit = [r for r in recs if any(d.get("id") == item["did"] for d in r.decisions)]
    elif item["kind"] == ci.MERGE:
        hit = [r for r in recs if f"#{item['key'].rsplit('#', 1)[-1]}" in r.title]
    else:
        hit = []
    hit = hit or [r for r in recs if item["hub"] and r.hub == item["hub"]]
    return [{"id": r.id, "title": r.title, "hub": r.hub, "mtime": r.mtime} for r in sorted(hit, key=lambda r: -r.mtime)[:limit]]


class Checklist:
    def __init__(self, store: ci.Store | None = None, router: answer_route.Router | None = None, now=time.time) -> None:
        self.store = store or ci.Store()
        self.router = router or answer_route.Router(answer_store.AnswerStore(self.store.dir), answer_route.main_line_sender())
        self.now = now
        self.audit_path = os.path.join(self.store.dir, doc_decide.AUDIT_NAME)
        self._facts_cache: dict = {}
        self._items: list = []
        self._notes: list = []
        self._at = 0.0
        self._lock = threading.Lock()

    # ---- 읽기 ----
    def refresh(self, use_pr: bool = True) -> None:
        """스냅샷을 읽어 목록을 다시 만듦. 실패하면 이전 목록을 두고 안내만 남김"""
        notes = []
        try:
            snap = pc.load_snapshot(pc.snapshot_path())
        except pc.SourceError as err:
            snap, notes = None, [str(err)]
        if snap is None:
            notes.append("진행 스냅샷 없음 — 수집기가 돌아야 목록이 채워짐")
            snap = {"items": []}
        elif self.now() - snap.get("updatedAt", 0) > SNAP_MAX_AGE_S:
            notes.append("진행 스냅샷이 낡음 — 수집기 확인 필요")
        raw = [(i["key"], i["bucket"], i["title"], i["since"] if isinstance(i.get("since"), (int, float)) else 0.0) for i in snap["items"]]
        now = self.now()
        facts = todo_facts.make_facts([r[0] for r in raw], self._facts_cache, now, use_pr)
        items = ci.build(raw, now, facts, self.store)
        with self._lock:
            self._items, self._notes, self._at = items, notes + facts.errors, now

    def payload(self) -> dict:
        with self._lock:
            items, notes, at = list(self._items), list(self._notes), self._at
        counts = {k: sum(1 for i in items if i.kind == k and not i.done) for k in ci.KIND_ORDER}
        return {"items": [i.public() for i in items], "todo": sum(counts.values()), "done": sum(1 for i in items if i.done),
                "counts": counts, "kinds": [{"kind": k, "label": label} for k, label in ci.KINDS], "notes": notes, "at": at, "scanning": not at}

    # ---- 쓰기: 했음 ----
    def handle(self, req: dict) -> dict:
        if not isinstance(req, dict) or set(req) != REQUEST_KEYS or not isinstance(req["id"], str):
            raise doc_decide.DecideError(400, "요청 형식 오류 — id(항목 키) 하나만 받음. 문구는 서버가 만듦")
        key = req["id"]
        item = self.find(key)
        if item is None:
            self.audit(key, "rejected:404")
            raise doc_decide.DecideError(404, "없는 항목 — 목록을 새로 고치세요")
        if item.kind not in ci.CONFIRMABLE:
            self.audit(key, "rejected:kind")
            raise doc_decide.DecideError(409, "이 종류는 '했음'으로 확인하지 않음 — 결정은 답으로, PR 은 병합으로 닫힘")
        if item.done:
            self.audit(key, "already")
            return {"ok": True, "already": True, "note": item.basis}
        if not self.store.confirm(key, answer_store.DOCSITE, self.now()):
            self.audit(key, "already")
            return {"ok": True, "already": True, "note": "이미 확인됨"}
        hub = "" if item.hub in ("", todo_items.UNKNOWN_HUB) else pc.hub_slug(item.hub)
        out = self.router.tell(hub, key, answer_store.DOCSITE, SAY.format(title=item.title, kind=KIND_TEXT[item.kind]))
        self.audit(key, f"{out.via}:{'ok' if out.ok else 'fail'}")
        self.refresh(use_pr=False)
        if not out.ok:
            raise doc_decide.DecideError(502, f"확인은 기록했지만 담당에게 전달하지 못함 — {out.note}. 허브·메인에 직접 알려 주세요")
        return {"ok": True, "via": out.via, "hub": out.hub, "note": out.note}

    # ---- 내부 ----
    def find(self, key: str):
        with self._lock:
            return next((i for i in self._items if i.key == key), None)

    def audit(self, key: str, result: str) -> None:
        key = key if ci.KEY_RE.fullmatch(key) else "invalid"  # 형식이 틀린 값은 그대로 남기지 않음(로그 오염 방지)
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.now())), "source": answer_store.DOCSITE, "kind": "confirm", "key": key, "result": result}
        try:
            answer_store.AnswerStore.append(self.audit_path, json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as err:
            print(f"감사 로그 기록 실패 {type(err).__name__}", file=sys.stderr, flush=True)
