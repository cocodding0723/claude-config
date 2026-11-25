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

메인으로 샌 답을 그 결정을 올린 세션에 다시 보내기: answer_route.py redeliver [--dry-run] <결정 번호…>
한 줄에 한 답(탭 구분): 대상|보냄|제외|실패, 결정 번호, 카드 ts, 작업 카드, 사유·본문. 종료 코드: 0 성공(제외 포함), 1 전달 실패 있음, 2 인자 오류
"""
import argparse
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import answer_store  # noqa: E402
import card_actions as actions  # noqa: E402
import main_inbox  # noqa: E402

HUB_LINE = "사용자 원문: {body} (출처: {source})"
REPORTS_NAME = "hub-reports.jsonl"  # 허브 보고 outbox. 결정을 올린 허브·작업 카드·작업 공간이 적힘
SENT_NAME = "slack-sent.jsonl"
MAIN_LINE = "[{source}→메인] {body} ({ts})"
MAIN_NOTE = "[{source}→메인 참고] {note}: {body} ({ts})"


def said_text(did: str, extra: dict, label: str) -> str:
    """답 한 문장. slack_push.py 의 스레드 글·반응 해석과 같은 머리: '결정 D-0001 = 선택 2(문구) (버튼)', 직접 입력은 '결정 D-0001 답(자유 글): 원문'"""
    if extra.get("action") == "custom":
        return f"결정 {did} 답(자유 글): {extra.get('text', '')}"
    pick = extra.get("pick")
    name = re.sub(r"^\d+\.\s*", "", label)
    return f"결정 {did} = " + (f"선택 {pick}({name})" if isinstance(pick, int) else name) + " (버튼)"


QUESTION_RE = answer_store.QUESTION_RE  # 질문 판정 규칙은 답 기록 모듈 한 곳(대기 목록·패널과 같은 판정)
QUESTION_NOTE = "질문(직접 입력, 답 아님·카드 버튼은 열려 있음)"


is_question = answer_store.is_question


# 답할 곳이 없으면 받은 세션이 답글 대신 카드를 다시 올리거나 답 없이 넘어감(11-20 두 건). 버튼 안내 줄은 전송기가 붙임(ask_open)
QUESTION_REPLY = " (답할 곳: Slack thread {ts} — 카드를 다시 올리지 말고 hub_report.py add --kind info --reply-to {ts} --text '답' 으로 답함. 버튼 안내 줄은 자동으로 붙음)"


def asked_text(did: str, text: str, thread: str = "") -> str:
    """질문 한 문장. said_text 와 달리 '답' 이 아니라 '질문' 머리를 써서 받는 쪽이 결정으로 처리하지 않게 함.
    thread 는 답글을 올릴 Slack 스레드 ts. 있으면 끝에 답할 곳과 명령을 붙임"""
    return f"결정 {did} {QUESTION_NOTE}: {text}" + (QUESTION_REPLY.format(ts=thread) if thread else "")


@dataclass(frozen=True)
class Outcome:
    """전달 결과. ok 는 답이 세션·허브·메인 중 어딘가에 도착했다는 뜻. via: ws(작업 카드 세션, hub 칸에 카드 id) | hub | proxy(메인 대리) | main(허브 기록 없음) | unknown(시간 초과) | lost | pending(막힘 재시도 중, 끝나면 on_done 으로 최종 결과)"""
    ok: bool
    via: str
    hub: str = ""
    note: str = ""

    def result(self) -> str:
        """로그·경고에 쓰는 결과 문구. 메인 받은 편지함·확인 카드에 기록만 된 것은 '전달 성공'이라 하지 않음:
        ok 는 어딘가에 도착했다는 뜻이라, 받을 세션이 없어 메인에 쌓인 경우도 성공으로 찍혀 세션에 닿은 것으로 오해됨(11-19)"""
        where = f"작업 카드 {self.hub} 세션" if self.via == "ws" else f"허브 {self.hub}"
        if not self.ok:
            return "도착 여부 불명 — " + where + " 확인 필요" if self.via == "unknown" else f"전달 실패({self.note})" if self.note else "전달 실패"
        if self.via in ("ws", "hub"):
            return f"{where} 전달 성공"
        if self.via == "main" and not self.note:
            return "받을 세션 없음 → 메인 확인 카드(작업판 카드·받은 편지함)에 기록, 세션에 닿지 않음"
        return f"세션에 닿지 않음 → 메인 대리 기록({self.note})" if self.note else "세션에 닿지 않음 → 메인 대리 기록"


FEATURE_HEAD = "feat-"  # 기능 코디네이터 허브 이름 머리(feature_open.sh 의 HUB_SLUG)


def feature_lookup(hub: str) -> tuple:
    """기능 코디네이터 허브 이름 → (기능 카드, 코디네이터 작업 공간). 지연 가져오기는 session_sender 와 같은 이유"""
    import feedback_route
    return tuple(feedback_route.feature_session(hub))


def session_sender(card: str, ws: str, ts: str, text: str, kind: str) -> str:
    """작업 카드 세션 전달의 기본 구현(스레드 글 답과 같은 길). 지연 가져오기: 작업판 도구를 쓰는 모듈이라 이 길을 탈 때만 읽음"""
    import feedback_route
    return feedback_route.answer_to_ws(card, ws, ts, text, kind)


LEAKED = ("main", "proxy", "lost")  # 세션에 닿지 않고 끝난 전달 상태. unknown 은 닿았을 수 있어 제외


def inbox_refs(path: str) -> set:
    """받은 편지함 파일에 적힌 ref 전부. 파일이 없으면 빈 집합, 다른 읽기 오류는 예외(모르는 채 다시 보내지 않게)"""
    refs = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    refs.add(r.get("ref"))
    except FileNotFoundError:
        pass
    return refs


def session_block(card: str, ws: str, ts: str) -> str:
    """작업 카드 세션에 이 답을 다시 보내면 안 되는 이유. 보내도 되면 ''"""
    import feedback_route as fr
    if not os.path.isdir(ws):
        return "작업 공간 없음(끝난 허브)"
    row = fr.card_row(card)
    if not row or not (fr.feature_alive(row) if fr.is_feature(row) else fr.is_open(row)):
        return "작업 카드 끝남"
    path = main_inbox.inbox_path(box=fr.answer_box(card, ws))
    # 상자에 쓴 뒤 카드 코멘트에서 실패한 답은 전달 기록이 main 이어도 세션에 닿은 것(11-25 b8bfba1 이전)
    if f"{card} slack:{ts}" in inbox_refs(path) | inbox_refs(path + ".1"):
        return "이미 세션 상자에 있음"
    return ""


class Router:
    """to_main: 메인 받은 편지함 기록 함수(text) -> 성공 여부. deliver: 허브 전송 함수(slug, text) -> (상태, 시도 수, 실패 코드, 후보 수)"""

    def __init__(self, store: answer_store.AnswerStore, to_main: Callable[[str], bool], deliver: Callable = actions.deliver,
                 redeliver: Callable = actions.redeliver, to_ws: Callable | None = None) -> None:
        self.store, self.to_main, self.deliver, self.redeliver = store, to_main, deliver, redeliver
        self.to_ws = to_ws or session_sender  # (카드, 작업 공간, ts, 본문, 종류) -> 상자 이름. 실패는 예외
        self.feature_of = feature_lookup      # 기능 코디네이터 허브 이름 -> (기능 카드, 작업 공간). 없으면 ('', '')
        self.block_of = session_block         # (카드, 작업 공간, ts) -> 다시 보내면 안 되는 이유. 보내도 되면 ''

    def ws_of(self, did: str) -> tuple:
        """결정을 올린 보고에 적힌 (작업 카드, 작업 공간). 없으면 ('', ''). 같은 번호를 다시 올렸으면 최근 보고.
        작업 카드로 올린 결정의 답은 그 세션으로 바로 감: 새로 연 코디네이터는 허브 목록(hubs.tsv)에 없어 옛 길로는 메인에서 끝남"""
        hit = ("", "")
        try:
            with open(os.path.join(self.store.dir, REPORTS_NAME), encoding="utf-8", errors="replace") as f:
                for ln in f:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("decision_id") == did and isinstance(r.get("card"), str) and r["card"] and isinstance(r.get("ws"), str) and r["ws"]:
                        hit = (r["card"], r["ws"])
        except OSError:
            pass
        return hit

    def raised_by(self, did: str) -> str:
        """결정을 올린 허브 이름 그대로(허브 목록에 없어도). 보고 기록이 우선, 없으면 보낸 카드의 tag. 못 읽으면 ''"""
        hub = ""
        for name, key in ((REPORTS_NAME, "hub"), (SENT_NAME, "tag")):
            try:
                with open(os.path.join(self.store.dir, name), encoding="utf-8", errors="replace") as f:
                    for ln in f:
                        try:
                            r = json.loads(ln)
                        except ValueError:
                            continue
                        if isinstance(r, dict) and r.get("decision_id") == did and isinstance(r.get(key), str) and r[key]:
                            hub = r[key]
            except OSError:
                pass
            if hub:
                break
        return hub

    def send_to(self, did: str, ts: str, source: str, body: str, done: Callable[["Outcome"], None] | None = None, kind: str = "answer") -> Outcome:
        """받을 곳을 정해 전달: 작업 카드로 올린 결정이면 그 세션, 아니면 허브 목록 → 메인(옛 길). 세션 전달이 실패하면 메인에 원문을 남김"""
        card, ws = self.ws_of(did)
        if not card:  # 기능 코디네이터가 작업 카드 없이 올린 결정(옛 방식): 허브 이름으로 그 기능의 카드와 세션을 찾음
            hub = self.raised_by(did)
            if hub.startswith(FEATURE_HEAD):
                try:
                    card, ws = self.feature_of(hub)
                except Exception:  # noqa: BLE001 — 작업판 조회 실패는 옛 길로
                    card, ws = "", ""
        if not card:
            return self.send_hub(self.hub_of(did), ts, source, body, done)
        src = answer_store.SOURCE_NAME.get(source, source)
        line = HUB_LINE.format(body=body, source=src)
        try:
            self.to_ws(card, ws, ts, line, kind)
            return Outcome(True, "ws", card)
        except Exception as err:  # noqa: BLE001 — 답을 잃지 않는 것이 먼저. 종류만 남기고 메인으로
            ok = self.to_main(MAIN_LINE.format(source=src, body=f"(작업 카드 {card} 세션에 전달하지 못해 대리 전달: {type(err).__name__}) " + line, ts=ts))
            return Outcome(ok, "main" if ok else "lost", card, f"작업 카드 {card} 세션 전달 실패({type(err).__name__}: {str(err)[:120]})" + ("" if ok else " · 메인 대리 전달도 실패"))

    def hub_of(self, did: str) -> str:
        """결정을 올린 허브 slug. 기록을 못 읽거나 근거가 없으면 빈 문자열(= 메인으로)"""
        import ci_review
        import panel_collect
        import progress_collect as pc
        import todo_facts
        p = lambda name: os.path.join(self.store.dir, name)  # noqa: E731
        names = ci_review.load_hub_names(p("hubs.tsv"))
        known = set(names) | {pc.legacy_slug(s) for s in names}  # 이미 쌓인 보고의 hub 값에 옛 별칭(server)이 남아 있음. 새 보고는 slug
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
        out = self.send_to(did, ts, source, " ".join(body.split()), done)
        if out.via == "pending":
            self.store.record_delivery(did, ts, answer_store.PENDING, False)  # 재시도 시작 시각 갱신(pending 만료 계산 기준)
        else:
            self.store.record_delivery(did, ts, out.via, out.ok)  # 즉시 끝난 결과는 호출자가 반환값으로 처리(on_done 은 재시도 끝에만)
        return out

    def leaked_answers(self, did: str) -> list:
        """결정 하나의 버튼 답마다 {did, ts, card, ws, source, body, skip}. skip 이 빈 것만 다시 보낼 대상"""
        answers = [r for r in self.store.read_inbox() if r.get("decision_id") == did and r.get("type") == "button" and r.get("parent_ts")]
        card, ws = self.ws_of(did)
        rows = []
        for r in answers:
            ts = r["parent_ts"]
            row = {"did": did, "ts": ts, "card": card, "ws": ws, "source": r.get("source") or answer_store.SLACK,
                   "body": " ".join(said_text(did, r, str(r.get("option_text") or "")).split())}
            row["skip"] = "같은 결정에 더 나중 답이 있음" if r is not answers[-1] else self.skip_reason(row)
            rows.append(row)
        # 같은 번호로 다시 올린 카드에 같은 답을 눌러 하나만 닿은 경우. 받는 세션에는 같은 문장이라 다시 보내면 중복
        if rows and not rows[-1]["skip"] and any(x["body"] == rows[-1]["body"] and self.last_state(did, x["ts"]) in ("ws", "hub") for x in rows[:-1]):
            rows[-1]["skip"] = "같은 답이 다른 카드로 이미 전달됨"
        return rows

    def last_state(self, did: str, ts: str) -> str:
        return (self.store.deliveries(did, ts) or [{}])[-1].get("state") or ""

    def skip_reason(self, row: dict) -> str:
        last = self.last_state(row["did"], row["ts"])
        if last not in LEAKED:
            return f"샌 답 아님(마지막 전달 {last or '기록 없음'})"
        if not row["card"]:
            return "받을 세션 기록 없음"
        try:
            return self.block_of(row["card"], row["ws"], row["ts"])
        except Exception as err:  # noqa: BLE001 — 세션 상태를 모르면 보내지 않음
            return f"세션 상태 확인 실패({type(err).__name__})"

    def resend(self, row: dict) -> Outcome:
        """leaked_answers 가 고른 답 하나를 세션에 다시 전달. 실패해도 메인에는 다시 남기지 않음(처음 샐 때 이미 남음).
        잠금 안에서 마지막 기록을 다시 보고 pending 을 남겨, 겹친 두 실행 중 하나만 보냄"""
        did, ts, card = row["did"], row["ts"], row["card"]
        with actions.flock(self.store.lock):
            if self.last_state(did, ts) not in LEAKED:
                return Outcome(False, "skip", card, "다른 실행이 이미 처리함")
            self.store.mark(did, ts, answer_store.PENDING, None, retry=True)
        line = HUB_LINE.format(body=row["body"], source=answer_store.SOURCE_NAME.get(row["source"], row["source"]))
        try:
            self.to_ws(card, row["ws"], ts, line, "answer")
            out = Outcome(True, "ws", card)
        except Exception as err:  # noqa: BLE001 — lost 로 남겨 다음 실행에서 다시 시도 가능
            out = Outcome(False, "lost", card, f"작업 카드 {card} 세션 전달 실패({type(err).__name__})")
        self.store.record_delivery(did, ts, out.via, out.ok)
        return out

    def ask(self, did: str, ts: str, source: str, body: str) -> Outcome:
        """질문 한 건을 허브(없으면 메인)에 전달. 답이 아니므로 전달 기록(answer-delivery)을 남기지 않음 — 나중 답의 재전달 판정을 흐리지 않기 위함"""
        return self.send_to(did, ts, source, " ".join(body.split()), kind="question")

    def send(self, did: str, ts: str, source: str, body: str) -> Outcome:
        return self.send_to(did, ts, source, body)

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


def main_line_sender(hub_dir: str | None = None) -> Callable[[str], bool]:
    """메인 받은 편지함(main.jsonl)에 한 줄 남기는 전송(문서 사이트용). 메인 터미널 입력창에는 쓰지 않음. 쓰기 실패는 False"""
    return main_inbox.sender("answer_route", "answer", hub=hub_dir)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="메인으로 샌 결정 답을 그 결정을 올린 세션에 다시 전달. 먼저 --dry-run 으로 대상 확인")
    sub = ap.add_subparsers(dest="cmd", required=True).add_parser("redeliver")
    sub.add_argument("--dry-run", action="store_true", help="보내지 않고 대상과 제외 사유만 출력")
    sub.add_argument("dids", nargs="+", metavar="결정번호")
    args = ap.parse_args(argv)
    router, failed = Router(answer_store.AnswerStore(), lambda _text: False), False
    for did in dict.fromkeys(args.dids):
        rows = router.leaked_answers(did)
        if not rows:
            print("\t".join(("제외", did, "-", "-", "버튼 답 기록 없음")))
        for row in rows:
            mark, note = "제외", row["skip"]
            if not note and args.dry_run:
                mark, note = "대상", row["body"]
            elif not note:
                out = router.resend(row)
                mark, note = "보냄" if out.ok else "제외" if out.via == "skip" else "실패", out.note or out.result()
                failed = failed or mark == "실패"
            print("\t".join((mark, did, row["ts"], row["card"] or "-", note)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
