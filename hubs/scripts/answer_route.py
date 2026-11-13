#!/usr/bin/env python3
"""결정 답 전달 경로. Slack 버튼(slack_socket.py)과 문서 사이트(doc_browser.py)가 함께 씀.

결정을 올린 허브가 기록으로 확인되면 답을 그 허브 코디네이터에 '사용자 원문: … (출처: Slack|문서 사이트)' 로 직접 보내고,
메인에는 참고 한 줄만 보냄. 허브 전송이 실패하면 메인에 원문을 대리 전달. 허브 창이 확인 창·질문 화면이라 입력이 막힌 경우(BLOCKED)는
바로 대리 전달하지 않고 백그라운드에서 같은 터미널에 재시도(card_actions.redeliver) — 그동안 결과는 pending, 끝내 막히면 대리 전달 + '창 확인 필요' 문구.
시간 초과는 도착 여부 불명이라 허브에 다시 보내지 않고
메인에 '확인 필요' 한 줄만 보냄(두 곳 중복 전달 방지). 결정→허브 기록이 없으면 지금처럼 메인으로만 보냄.
허브 전송은 card_actions.deliver 를 그대로 씀(후보 선택·재조회·마감 규칙을 여기서 다시 만들지 않음).
같은 결정 번호를 두 허브 이상이 올렸다면 어느 허브인지 알 수 없어 메인으로 보냄(허브 추측 금지).
전달이 끝나면 결과를 answer_store 의 전달 기록(answer-delivery.jsonl)에 남김.
결정→허브: hub-reports.jsonl(허브 보고 outbox)의 decision_id·hub 가 우선, 없으면 보낸 카드 기록의 tag (todo_facts.decision_hubs 규칙).
"""
import os
import re
import sys
import threading
from dataclasses import dataclass
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import answer_store  # noqa: E402
import card_actions as actions  # noqa: E402
import main_terminal as mt  # noqa: E402

HUB_LINE = "사용자 원문: {body} (출처: {source})"
MAIN_LINE = "[{source}→메인] {body} ({ts})"
MAIN_NOTE = "[{source}→메인 참고] {note}: {body} ({ts})"


def said_text(did: str, extra: dict, label: str) -> str:
    """답 한 문장. slack_push.py 의 스레드 글·반응 해석과 같은 머리: '결정 D-0001 = 선택 2(문구) (버튼)', 직접 입력은 '결정 D-0001 답(자유 글): 원문'"""
    if extra.get("action") == "custom":
        return f"결정 {did} 답(자유 글): {extra.get('text', '')}"
    pick = extra.get("pick")
    name = re.sub(r"^\d+\.\s*", "", label)
    return f"결정 {did} = " + (f"선택 {pick}({name})" if isinstance(pick, int) else name) + " (버튼)"


@dataclass(frozen=True)
class Outcome:
    """전달 결과. ok 는 답이 허브나 메인 중 어딘가에 도착했다는 뜻. via: hub | proxy(메인 대리) | main(허브 기록 없음) | unknown(시간 초과) | lost | pending(막힘 재시도 중, 끝나면 on_done 으로 최종 결과)"""
    ok: bool
    via: str
    hub: str = ""
    note: str = ""


class Router:
    """to_main: 메인 터미널 전송 함수(text) -> 성공 여부. deliver: 허브 전송 함수(slug, text) -> (상태, 시도 수, 실패 코드, 후보 수)"""

    def __init__(self, store: answer_store.AnswerStore, to_main: Callable[[str], bool], deliver: Callable = actions.deliver,
                 redeliver: Callable = actions.redeliver) -> None:
        self.store, self.to_main, self.deliver, self.redeliver = store, to_main, deliver, redeliver

    def hub_of(self, did: str) -> str:
        """결정을 올린 허브 slug. 기록을 못 읽거나 근거가 없으면 빈 문자열(= 메인으로)"""
        import ci_review
        import panel_collect
        import progress_collect as pc
        import todo_facts
        p = lambda name: os.path.join(self.store.dir, name)  # noqa: E731
        names = ci_review.load_hub_names(p("hubs.tsv"))
        known = set(names) | {pc.status_key(s) for s in names} | {pc.legacy_slug(s) for s in names}  # 보고의 hub 값은 slug·상태 파일 이름·옛 별칭 모두 있음
        try:
            sent, reports = panel_collect.jsonl(p("slack-sent.jsonl")), panel_collect.jsonl(p("hub-reports.jsonl"))
        except (panel_collect.SourceError, UnicodeDecodeError):
            return ""
        said = [r.get("hub") for r in reports if r.get("decision_id") == did and r.get("hub") in known]
        said = said or [c.get("tag") for c in sent if c.get("decision_id") == did and c.get("form_kind") == "decide" and c.get("tag") in known]
        if len(set(said)) > 1:  # 허브 둘 이상이 같은 번호를 보고함 — 모호
            return ""
        hub = todo_facts.decision_hubs(reports, sent, known).get(did, "")
        return pc.hub_slug(hub, set(names)) if hub else ""

    def route(self, did: str, ts: str, source: str, body: str, on_done: Callable[["Outcome"], None] | None = None) -> Outcome:
        """답 한 건을 전달하고 결과를 전달 기록에 남김. body 는 said_text 결과. 줄바꿈이 있으면 터미널에서 여러 줄로 갈라지므로 한 줄로 정리.
        입력 막힘이면 pending 을 돌려주고 백그라운드 재시도가 끝난 뒤 최종 결과를 기록하고 on_done 호출(재시도 중 수신기가 내려가면 pending 만 남아
        answer_store.retry_due 가 늦은 클릭의 재전달로 복구)"""
        def done(out: Outcome) -> None:
            self.store.record_delivery(did, ts, out.via, out.ok)
            if on_done:
                on_done(out)
        out = self.send_hub(self.hub_of(did), ts, source, " ".join(body.split()), done)
        if out.via == "pending":
            self.store.record_delivery(did, ts, answer_store.PENDING, False)  # 재시도 시작 시각 갱신(pending 만료 계산 기준)
        else:
            self.store.record_delivery(did, ts, out.via, out.ok)  # 즉시 끝난 결과는 호출자가 반환값으로 처리(on_done 은 재시도 끝에만)
        return out

    def send(self, did: str, ts: str, source: str, body: str) -> Outcome:
        return self.send_hub(self.hub_of(did), ts, source, body)

    def tell(self, hub: str, ref: str, source: str, body: str) -> Outcome:
        """결정이 아닌 한 줄(확인 완료 등)을 담당 허브에 전달. 허브를 모르면(빈 값) 메인으로. 전달 기록은 남기지 않음(호출자가 따로 기록).
        호출자가 결과를 바로 기록하므로 막힘 재시도 없이 대리 전달"""
        return self.send_hub(hub, ref, source, " ".join(body.split()))

    def send_hub(self, hub: str, ts: str, source: str, body: str, done: Callable[["Outcome"], None] | None = None) -> Outcome:
        """done 이 있으면 입력 막힘을 백그라운드 재시도하고 pending 반환, 없으면 막힘도 바로 대리 전달"""
        src = answer_store.SOURCE_NAME.get(source, source)
        if not hub:
            ok = self.to_main(MAIN_LINE.format(source=src, body=body, ts=ts))
            return Outcome(ok, "main", "", "" if ok else "메인 전달 실패")
        line = HUB_LINE.format(body=body, source=src)
        res = self.deliver(hub, line)
        if res[0] == actions.BLOCKED and done is not None:
            threading.Thread(target=self.retry, args=(hub, ts, src, body, line, getattr(res, "blocked", ()), done), daemon=True).start()
            return Outcome(False, "pending", hub, f"{hub} 허브 창 입력 막힘 — 재시도 중")
        return self.settle(hub, ts, src, body, line, res)

    def retry(self, hub: str, ts: str, src: str, body: str, line: str, handles, done: Callable[["Outcome"], None]) -> None:
        try:
            out = self.settle(hub, ts, src, body, line, self.redeliver(hub, line, handles))
        except Exception as err:  # 스레드가 조용히 죽어도 pending 은 남아 재전달 경로가 복구
            out = Outcome(False, "lost", hub, f"막힘 재시도 예외({type(err).__name__})")
        done(out)

    def settle(self, hub: str, ts: str, src: str, body: str, line: str, res) -> Outcome:
        status, _tries, codes, _found = res
        if status == actions.SENT:
            ref = self.to_main(MAIN_NOTE.format(source=src, note=f"{hub} 허브에 직접 전달함", body=body, ts=ts))
            return Outcome(True, "hub", hub, "" if ref else "허브 전달은 됐고 메인 참고 한 줄만 실패")
        if status == actions.UNKNOWN:
            self.to_main(MAIN_NOTE.format(source=src, note=f"{hub} 허브 전달이 시간 초과로 도착 여부 불명 — 확인 필요(허브에 다시 보내지 않았음)", body=body, ts=ts))
            return Outcome(False, "unknown", hub, f"{hub} 허브 전달 시간 초과 — 도착 여부 불명")
        why = ", ".join(dict.fromkeys(codes)) or "코디네이터 터미널 없음"
        stuck = f" · {actions.block_note(hub)}" if status == actions.BLOCKED else ""
        ok = self.to_main(MAIN_LINE.format(source=src, body=f"({hub} 허브에 전달하지 못해 대리 전달: {why}{stuck}) " + line, ts=ts))
        return Outcome(ok, "proxy" if ok else "lost", hub, f"{hub} 허브 전송 실패({why}{stuck})" + ("" if ok else " · 메인 대리 전달도 실패"))


def main_line_sender(env_path: str | None = None) -> Callable[[str], bool]:
    """main.env 의 메인 터미널에 한 줄 보내는 단순 전송(문서 사이트용). 핸들 재탐색은 하지 않음 — Slack 수신기의 send_main 이 그 일을 맡음"""
    path = env_path or os.environ.get("LRM_MAIN_ENV") or os.path.join(os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs"), "main.env")

    def send(text: str) -> bool:
        handle = mt.read_handle(path)
        return bool(handle) and actions.send_line(handle, text) is None
    return send
