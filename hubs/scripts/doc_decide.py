#!/usr/bin/env python3
"""문서 사이트의 결정 답 처리(POST /api/decide 뒤쪽). doc_browser.py 의 POST 처리기만 부름. 표준 라이브러리 + 공용 모듈만 사용.

흐름: 화면은 선택 번호만 보냄 → preview: 서버가 카드 기록에서 문구를 조립해 보여 주고 1회용 intent 발급
→ commit: 같은 번호·같은 토큰의 intent 가 맞을 때만 answer_store.claim → answer_route 로 전달.
  - 문구는 slack-sent.jsonl 의 선택지 기록으로만 만듦. 화면이 보낸 문구는 받지 않음(알 수 없는 키는 요청 형식 오류).
  - 확인(preview) 없이 commit 만 보내면 거부. intent 는 한 번 쓰면 사라지고 TTL 이 지나면 무효.
  - 이미 답한 결정은 preview·commit 모두 '이미 Slack/문서 사이트에서 답함' 으로 거부. 감시기 카드(card-actions 등록)는 Slack 에서만 답함.
감사 로그: <작업 폴더>/docsite-audit.jsonl 한 줄 = commit 시도 1건 {at, source, did, pick, result}. 토큰·문구·본문은 남기지 않음.
"""
import json
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import answer_route  # noqa: E402
import answer_store  # noqa: E402
import card_actions as actions  # noqa: E402

INTENT_TTL_S = 120  # 확인 단계가 유효한 시간
INTENT_MAX = 200
REQUEST_KEYS = {"id", "pick", "intent"}  # 요청이 가질 수 있는 키 전부
AUDIT_NAME = "docsite-audit.jsonl"
DID_RE = re.compile(r"D-[0-9A-Za-z]{1,16}", re.ASCII)
OPEN_ACTION = "감시기 카드는 Slack 에서 답하세요"


class DecideError(Exception):
    """사용자에게 보여 줄 거부 사유(원인 + 조치)와 HTTP 상태"""

    def __init__(self, status: int, why: str) -> None:
        super().__init__(why)
        self.status, self.why = status, why


@dataclass(frozen=True)
class Intent:
    did: str
    pick: int
    who: str  # 확인 단계를 연 페이지의 CSRF 토큰. 다른 페이지가 가로채 쓰지 못하게 묶음
    exp: float


class Decider:
    def __init__(self, store: answer_store.AnswerStore | None = None, router: answer_route.Router | None = None, now=time.time) -> None:
        self.store = store or answer_store.AnswerStore()
        self.router = router or answer_route.Router(self.store, answer_route.main_line_sender())
        self.now = now
        self.audit_path = os.path.join(self.store.dir, AUDIT_NAME)
        self._intents: dict[str, Intent] = {}
        self._lock = threading.Lock()

    # ---- 읽기 ----
    def view(self, did: str) -> dict:
        """결정 카드 표시용: 제목·선택지·답 여부. 선택지 문구는 카드 기록에서만 읽음"""
        card = self.card(did)
        by = self.store.first_answer(did, card["ts"])
        return {"id": did, "title": str(card.get("title") or did), "options": self.options(card), "recommended": card.get("recommended"),
                "answered": answer_store.LATE_TEXT.get(by, "") if by else "", "actionable": not by and not self.has_action(did)}

    # ---- 확인(preview) → 전달(commit) ----
    def handle(self, req: dict, who: str) -> dict:
        """요청 한 건. intent 가 없으면 확인 단계, 있으면 전달 단계"""
        if not isinstance(req, dict) or set(req) - REQUEST_KEYS or not isinstance(req.get("id"), str):
            raise DecideError(400, "요청 형식 오류 — id·pick(·intent) 만 받음. 문구는 서버가 만듦")
        pick = req.get("pick")
        if not isinstance(pick, int) or isinstance(pick, bool):
            raise DecideError(400, "요청 형식 오류 — pick 은 선택지 번호(정수)")
        did = req["id"]
        if "intent" not in req:
            return self.preview(did, pick, who)
        if not isinstance(req["intent"], str):
            raise DecideError(400, "요청 형식 오류 — intent 는 문자열")
        return self.commit(did, pick, req["intent"], who)

    def preview(self, did: str, pick: int, who: str) -> dict:
        card, label = self.pick_label(did, pick)
        self.ensure_open(did, card)
        body = answer_route.said_text(did, {"action": "pick", "pick": pick}, label)
        hub = self.router.hub_of(did)
        intent = secrets.token_urlsafe(24)
        with self._lock:
            now = self.now()
            for k in [k for k, v in self._intents.items() if v.exp < now]:
                del self._intents[k]
            if len(self._intents) >= INTENT_MAX:
                del self._intents[min(self._intents, key=lambda k: self._intents[k].exp)]
            self._intents[intent] = Intent(did, pick, who, now + INTENT_TTL_S)
        return {"ok": True, "phase": "preview", "intent": intent, "title": str(card.get("title") or did), "label": label,
                "target": f"{hub} 허브" if hub else "메인 터미널",
                "text": answer_route.HUB_LINE.format(body=body, source=answer_store.SOURCE_NAME[answer_store.DOCSITE]), "ttl": INTENT_TTL_S}

    def commit(self, did: str, pick: int, intent: str, who: str) -> dict:
        with self._lock:  # 한 번 쓰면 성공·실패와 무관하게 사라짐
            it = self._intents.pop(intent, None)
        if it is None or it.exp < self.now() or (it.did, it.pick, it.who) != (did, pick, who):
            self.audit(did, pick, "rejected:no_confirm")
            raise DecideError(409, "확인 단계가 없거나 만료됨 — 선택부터 다시 하세요")
        try:
            card, label = self.pick_label(did, pick)
            self.ensure_open(did, card)
        except DecideError as err:
            self.audit(did, pick, f"rejected:{err.status}")
            raise
        extra = {"action": "pick", "pick": pick}
        claim = self.store.claim(answer_store.Answer(did, card["ts"], str(card.get("channel") or ""), str(card.get("title") or ""), label,
                                                     answer_store.DOCSITE, extra))
        if claim.redeliver:  # 먼저 온 답이 전달 전에 끊김: 그 답 원문을 1회 다시 전달
            extra0, label0 = claim.replay()
            out = self.router.route(did, card["ts"], claim.by, answer_route.said_text(did, extra0, label0))
            self.audit(did, pick, f"redeliver:{out.via}:{'ok' if out.ok else 'fail'}")
            if not out.ok:
                raise DecideError(502, f"이전 답을 다시 전달하지 못함 — {out.note}. 허브·메인에 직접 알려 주세요")
            return {"ok": True, "phase": "commit", "via": out.via, "hub": out.hub, "note": "이전 답의 전달 기록이 없어 다시 전달함(이번 선택은 반영하지 않음)"}
        if claim.late:
            self.audit(did, pick, f"late:{claim.by}")
            raise DecideError(409, claim.late_text())
        status, entry = claim.taken
        if entry is not None:
            self.store.record_delivery(did, card["ts"], "action", True)
        if status == "busy" or entry is not None:  # 등록이 생긴 경우(확인 뒤 등록): 후속 동작은 Slack 수신기와 같은 방식으로 실행
            threading.Thread(target=self.run_entry, args=(did, pick, entry, card["ts"], label), daemon=True).start()
            self.audit(did, pick, "claimed:card_action")
            return {"ok": True, "phase": "commit", "via": "action", "note": "후속 동작을 실행 요청함"}
        out = self.router.route(did, card["ts"], answer_store.DOCSITE, answer_route.said_text(did, extra, label))
        self.audit(did, pick, f"{out.via}:{'ok' if out.ok else 'fail'}")
        if not out.ok:
            raise DecideError(502, f"답은 기록했지만 전달 결과가 확실하지 않음 — {out.note}. 허브·메인에 직접 알려 주세요")
        return {"ok": True, "phase": "commit", "via": out.via, "hub": out.hub, "note": out.note}

    # ---- 내부 ----
    def card(self, did: str) -> dict:
        card = self.store.find_card(did)
        if card is None:
            raise DecideError(404, "없는 결정 — 번호 카드 기록이 없음")
        return card

    @staticmethod
    def options(card: dict) -> list:
        opts = card.get("options") or {}
        return [{"n": int(k), "text": str(v)} for k, v in sorted(opts.items(), key=lambda kv: int(kv[0])) if str(k).isdigit()]

    def pick_label(self, did: str, pick: int) -> tuple:
        card = self.card(did)
        text = (card.get("options") or {}).get(str(pick))
        if not isinstance(text, str) or not text:
            raise DecideError(400, f"없는 선택지 — {pick}번은 이 결정의 선택지가 아님")
        return card, f"{pick}. {text}"

    def ensure_open(self, did: str, card: dict) -> None:
        by = self.store.first_answer(did, card["ts"])
        if by:
            raise DecideError(409, answer_store.LATE_TEXT.get(by, "이미 답함"))
        if self.has_action(did):
            raise DecideError(409, OPEN_ACTION)

    @staticmethod
    def has_action(did: str) -> bool:
        return did in actions.load()

    def run_entry(self, did: str, pick: int, entry, ts: str, label: str) -> None:
        """Slack 수신기의 route·settle 과 같은 규칙: busy 면 잠금을 기다려 꺼내고, 등록이 있으면 후속 동작, 없으면 일반 전달"""
        try:
            entry = entry if entry is not None else actions.take(did)
            if entry is not None:
                self.store.record_delivery(did, ts, "action", True)
                actions.execute(entry, pick)
            else:
                self.router.route(did, ts, answer_store.DOCSITE, answer_route.said_text(did, {"action": "pick", "pick": pick}, label))
        except Exception as err:  # 백그라운드 실패가 서버를 죽이지 않게 종류만 남김
            print(f"결정 후속 처리 실패 {type(err).__name__}", file=sys.stderr, flush=True)

    def audit(self, did: str, pick: int, result: str) -> None:
        did = did if DID_RE.fullmatch(did) else "invalid"  # 형식이 틀린 값은 그대로 남기지 않음(로그 오염 방지)
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.now())), "source": answer_store.DOCSITE, "did": did, "pick": pick, "result": result}
        try:
            answer_store.AnswerStore.append(self.audit_path, json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as err:
            print(f"감사 로그 기록 실패 {type(err).__name__}", file=sys.stderr, flush=True)
