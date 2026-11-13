#!/usr/bin/env python3
"""문서 사이트 PR 병합 승인(POST /api/approve 뒤쪽). doc_browser.py 의 처리기만 부름. 표준 라이브러리 + 공용 모듈만 사용.

하는 일은 '승인 기록 + 담당 허브 전달'뿐임. 병합·gh pr merge·push 는 어디서도 실행하지 않음(병합은 코디네이터가 충돌 검사 뒤에).
흐름: 화면은 항목 키 하나만 보냄 → 서버가 확인 목록에서 열린 PR 줄인지 확인 → 서버 프로세스가 화면 밖 확인 창(macOS)을 띄움
  → '승인'이면 answer_store 에 승인 기록(종류 pr-approve, 먼저 온 것만) → 담당 허브에 '사용자 원문: PR 저장소#번호 병합 승인(헤드 abc1234) — 헤드가 같을 때만 병합, 다르면 다시 승인 요청 (출처: 문서 사이트)' 한 줄.
  승인은 기록 시점 PR 헤드 커밋에 묶임: 승인 직전·확인 창 뒤 두 번 재조회해 닫힘·헤드 변경이면 거부. 헤드가 바뀌면 목록에 '승인 낡음 — 새 커밋'.
  '취소'·시간 초과(DIALOG_TIMEOUT_S)·창을 못 띄움이면 기록·전달 없음. 허브를 모르면 메인으로.
확인 창은 주입 가능한 함수(ask)라 시험은 가짜를 씀. 같은 PR 의 확인 창은 동시에 하나만 띄움.
전달 기록·재전달 규칙은 확인 목록('했음')과 같음: 승인은 기록했는데 전달이 실패·pending 이면 다시 누를 때 1회만 재전달.
감사 로그: docsite-audit.jsonl 에 {at, source, kind: pr-approve, key, result}.
"""
import os
import re
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import answer_store  # noqa: E402
import confirm_items as ci  # noqa: E402
import doc_confirm  # noqa: E402
import doc_decide  # noqa: E402
import progress_collect as pc  # noqa: E402
import todo_facts  # noqa: E402
import todo_items  # noqa: E402

REQUEST_KEYS = {"id"}
DIALOG_TIMEOUT_S = 60
TITLE_MAX = 80
SAY = "PR {repo}#{num} 병합 승인(헤드 {head}) — 헤드가 같을 때만 병합, 다르면 다시 승인 요청"
HEAD_SHORT = 7
DIALOG_TEXT = "PR 병합 승인\n\n{repo}#{num}\n헤드 {head}\n{title}\n담당 허브: {hub}\n\n승인하면 담당 허브에 병합 승인(이 헤드 한정)을 전달함. 헤드가 바뀌면 무효. 병합은 충돌 검사 뒤 코디네이터가 함."
OK_BUTTON, NO_BUTTON = "승인", "취소"
_PR_KEY = re.compile(r"^pr:([\w.\-]+/[\w.\-]+)#(\d+)$")
_OSA = ["on run argv",
        f'display dialog (item 1 of argv) with title "PR 병합 승인" buttons {{"{NO_BUTTON}", "{OK_BUTTON}"}} default button "{NO_BUTTON}" cancel button "{NO_BUTTON}" with icon caution giving up after {DIALOG_TIMEOUT_S}',
        "end run"]
APPROVED, CANCELED, TIMED_OUT, UNAVAILABLE = "approved", "canceled", "timeout", "unavailable"


def osascript_dialog(text: str) -> str:
    """macOS 확인 창. 문구는 argv 로 넘겨 스크립트 문자열에 끼워 넣지 않음(주입 방지). 반환: approved | canceled | timeout | unavailable"""
    cmd = ["osascript"] + [a for line in _OSA for a in ("-e", line)] + [text]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=DIALOG_TIMEOUT_S + 15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return UNAVAILABLE
    out = p.stdout or ""
    if p.returncode != 0:  # 취소 버튼 = 오류 -128
        return CANCELED if "-128" in (p.stderr or "") else UNAVAILABLE
    if "gave up:true" in out:
        return TIMED_OUT
    return APPROVED if f"button returned:{OK_BUTTON}" in out else CANCELED


def clean(text: str, limit: int = TITLE_MAX) -> str:
    """창에 보일 문구에서 제어 문자 제거·길이 제한"""
    one = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    return one if len(one) <= limit else one[:limit - 1] + "…"


class Approver:
    """ask: 확인 창 함수(text) -> approved|canceled|timeout|unavailable. checklist 의 목록·전달 경로·감사 로그를 재사용함"""

    def __init__(self, checklist: doc_confirm.Checklist, ask=osascript_dialog) -> None:
        self.cl, self.ask = checklist, ask
        self._busy: set = set()
        self._lock = threading.Lock()

    def handle(self, req: dict) -> dict:
        if not isinstance(req, dict) or set(req) != REQUEST_KEYS or not isinstance(req["id"], str):
            raise doc_decide.DecideError(400, "요청 형식 오류 — id(PR 항목 키) 하나만 받음. 문구는 서버가 만듦")
        key = req["id"]
        item = self.cl.find(key)
        m = _PR_KEY.match(key)
        if item is None:
            self.audit(key, "rejected:404")
            raise doc_decide.DecideError(404, "없는 항목 — 목록을 새로 고치세요")
        if item.kind != ci.MERGE or not m:
            self.audit(key, "rejected:kind")
            raise doc_decide.DecideError(409, "PR 병합 줄만 승인할 수 있음")
        if item.done:
            self.audit(key, "rejected:closed")
            raise doc_decide.DecideError(409, "이미 병합되었거나 닫힌 PR — 승인할 수 없음")
        store = self.cl.router.store
        since = self.cl.store.load_seen()["first"].get(key, 0)
        live = self.cl.fresh_pr(key)
        head = (live or {}).get("head", "")
        if not head:
            self.audit(key, "rejected:head-unknown")
            raise doc_decide.DecideError(502, "PR 헤드 커밋을 확인하지 못함 — 잠시 뒤 다시 누르세요")
        if live["state"] in todo_facts.CLOSED_STATES:
            self.audit(key, "rejected:closed")
            raise doc_decide.DecideError(409, "이미 병합되었거나 닫힌 PR — 승인할 수 없음")
        rec = store.approvals().get(key)
        if rec and rec.get("at", 0) >= int(since) and rec.get("head", "") == head:
            return self.again(key, item, rec)  # 같은 헤드로 이미 승인함: 확인 창 없이 전달 상태만 점검
        with self._lock:
            if key in self._busy:
                raise doc_decide.DecideError(409, "이 PR 의 확인 창이 이미 떠 있음 — 화면 밖 창에서 답하세요")
            self._busy.add(key)
        try:
            hub = "" if item.hub in ("", todo_items.UNKNOWN_HUB) else item.hub
            res = self.ask(DIALOG_TEXT.format(repo=m.group(1), num=m.group(2), head=head[:HEAD_SHORT], title=clean(item.title), hub=clean(hub) or "미상(메인으로 전달)"))
        finally:
            with self._lock:
                self._busy.discard(key)
        if res != APPROVED:
            self.audit(key, f"dialog:{res}")
            why = {CANCELED: "확인 창에서 취소함", TIMED_OUT: f"확인 창 응답 없음({DIALOG_TIMEOUT_S}초)", UNAVAILABLE: "확인 창을 띄우지 못함(macOS 화면 필요)"}.get(res, "승인되지 않음")
            raise doc_decide.DecideError(409, f"{why} — 기록·전달 없음")
        now = self.cl.fresh_pr(key)  # 확인 창이 떠 있던 동안 PR 이 닫히거나 헤드가 바뀌었을 수 있음
        if now is None or now["state"] in todo_facts.CLOSED_STATES or now.get("head", "") != head:
            self.audit(key, "rejected:changed")
            raise doc_decide.DecideError(409, "확인 창이 떠 있는 동안 PR 이 닫혔거나 헤드가 바뀜(또는 재확인 실패) — 기록·전달 없음, 다시 승인 요청")
        at = int(self.cl.now())
        if not store.approve(key, answer_store.DOCSITE, at, since, head):
            return self.again(key, item, store.approvals().get(key) or {})
        return self.deliver(key, item, m, str(at), retry=False, head=head)

    def deliver(self, key: str, item, m, ts: str, retry: bool, head: str = "") -> dict:
        """담당에게 승인 한 줄 전달하고 결과를 전달 기록에 남김. ts 는 승인 시각(승인 한 건의 식별자)"""
        store = self.cl.router.store
        if not retry:
            try:
                store.mark(key, ts, answer_store.PENDING, None, self.cl.now())
            except OSError:
                pass  # 기록 실패가 전달을 막지 않음
        hub = "" if item.hub in ("", todo_items.UNKNOWN_HUB) else pc.hub_slug(item.hub)
        out = self.cl.router.tell(hub, key, answer_store.DOCSITE, SAY.format(repo=m.group(1), num=m.group(2), head=head[:HEAD_SHORT]))
        store.record_delivery(key, ts, out.via, out.ok)
        self.audit(key, f"{'redeliver:' if retry else ''}{out.via}:{'ok' if out.ok else 'fail'}")
        if not out.ok:
            raise doc_decide.DecideError(502, f"승인은 기록했지만 담당에게 전달하지 못함 — {out.note}. 다시 누르면 1회 재전달함" if not retry
                                         else f"재전달도 실패 — {out.note}. 허브·메인에 직접 알려 주세요")
        return {"ok": True, "via": out.via, "hub": out.hub, "note": out.note or doc_confirm.APPROVED_BASIS}

    def again(self, key: str, item, rec: dict) -> dict:
        """이미 승인한 PR 을 또 누름. 전달이 실패·pending 으로 끝난 경우만 1회 재전달, 아니면 아무것도 보내지 않음"""
        m = _PR_KEY.match(key)
        ts = str(rec["at"]) if rec.get("source") == answer_store.DOCSITE and isinstance(rec.get("at"), int) else ""
        if ts and self.cl.claim_retry(key, ts):
            return self.deliver(key, item, m, ts, retry=True, head=str(rec.get("head", "")))
        self.audit(key, "already")
        return {"ok": True, "already": True, "note": doc_confirm.APPROVED_BASIS}

    def audit(self, key: str, result: str) -> None:
        self.cl.audit(key if _PR_KEY.match(key) else "invalid", result, kind=answer_store.APPROVE_KIND)
