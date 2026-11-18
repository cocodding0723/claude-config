"""디스코드 결정 답(반응·답장) → 결정을 올린 허브로 직접 전달. discord_poll.py 가 inbox 를 쓴 뒤 부름.

슬랙은 버튼 답만 answer_route 로 허브에 바로 가고 반응은 메인 작업판 모음 카드로만 감. 디스코드는 버튼이 없어
반응이 곧 결정 답이므로 버튼과 같은 경로(answer_route.Router)로 보냄. 판정은 answer_store.judge(충돌·다른 선택이면 보내지 않음).
같은 카드는 전달 기록(answer-delivery.jsonl)이 있으면 다시 보내지 않음(폴러 회차마다 불려도 한 번).
"""
import answer_route
import answer_store

ANSWER_ACTIONS = ("pick", "approve")


def candidates(items: list) -> list:
    """새 inbox 항목 중 결정 답 후보 (결정 번호, 카드 메시지 ID). 같은 카드는 한 번"""
    out, seen = [], set()
    for it in items:
        did, ts = it.get("decision_id"), it.get("parent_ts")
        if it.get("kind") == "decide" and did and ts and it.get("action") in ANSWER_ACTIONS and (did, ts) not in seen:
            seen.add((did, ts))
            out.append((did, ts))
    return out


def route_new(hub: str, items: list, router=None, log=print) -> list:
    """새 항목의 결정 답을 허브로 보냄. 반환 [(결정 번호, 전달 경로, 성공)]. 전달 실패는 기록만 하고 폴러를 멈추지 않음"""
    store = answer_store.AnswerStore(hub)
    router = router or answer_route.Router(store, answer_route.main_line_sender(hub))
    done = []
    for did, ts in candidates(items):
        by, rec = store.find_answer(did, ts)
        if not by or store.deliveries(did, ts):  # 충돌·미확정이거나 이미 전달함
            continue
        extra = {"action": rec.get("action"), "pick": rec.get("pick")}
        body = answer_route.said_text(did, extra, rec.get("option_text") or "")
        try:
            out = router.route(did, ts, answer_store.DISCORD, body)
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 나머지 답 전달을 막지 않음
            log(f"결정 {did} 허브 전달 예외 {type(e).__name__} — 메인 작업판 기록으로만 남음")
            continue
        done.append((did, out.via, out.ok))
    return done
