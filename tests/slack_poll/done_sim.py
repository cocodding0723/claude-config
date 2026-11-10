"""전달 완료 표시(done_phase)의 장기 운용 시뮬레이션. 사용: done_sim.py <slack_poll.sh> <slack_push.py> <임시 허브 폴더> [회차 수]
폴러 원문의 done_phase 를 그대로 떼어 가짜 api 로, 전달 기록은 slack_push.py 의 record_delivery 로 실제 파일에 씀.
회차마다 새 스레드 1개에 배치 1건을 전달하고 폴러 회차를 1번 돌림. 기록 파일은 1000줄을 넘으면 500줄로 잘림(실제와 같음).
정상이면 회차마다 chat.update 정확히 1회·문구 '(1건)'. 처리 표시가 잘린 기록을 놓치면 옛 기록이 다시 처리돼 호출이 늘고 건수가 부풂.
출력: JSON 한 줄 {"rounds","calls","bad","first_bad"}"""
import importlib.util
import json
import os
import re
import sys
import time

sys.dont_write_bytecode = True  # -I 는 PYTHONDONTWRITEBYTECODE 를 무시함. slack_push 를 import 해도 저장소에 __pycache__ 를 만들지 않게
poll_path, push_path, hub = sys.argv[1:4]
rounds = int(sys.argv[4]) if len(sys.argv) > 4 else 1300
os.environ["LRM_HUB_DIR"] = hub
spec = importlib.util.spec_from_file_location("slack_push_under_test", push_path)
push = importlib.util.module_from_spec(spec)
spec.loader.exec_module(push)

src = open(poll_path, encoding="utf-8").read()
body = src[src.index("def done_phase(state):"):src.index("def fetch_history(")]
consts = "\n".join(re.findall(r"^(?:UPD_MAX|UPD_SEEN_MAX|ACK_MIN_LEFT|DONE_TEXT)\s*=.*$", src, re.M))
calls = []


class ApiError(Exception):
    code = None


def api(method, _post=False, **p):
    calls.append((p["ts"], p["text"]))
    return {"ok": True}


def load_jsonl(path):
    out = []
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            if ln.strip():
                out.append(json.loads(ln))
    return out


g = {"os": os, "time": time, "sys": sys, "ApiError": ApiError, "api": api, "ACK_OFF": frozenset({"off"}), "ACK_DISABLE": frozenset(),
     "DEADLINE": time.time() + 10_000, "PUSH_DELIVERED": push.DELIVERED, "save_state": lambda s: None, "load_jsonl": load_jsonl}
exec(consts, g)
exec(body, g)

state = {"ack_msgs": {}}
bad = []
for i in range(rounds):
    root = f"{1000 + i}.000001"
    state["ack_msgs"][root] = {"ts": f"a{i}", "ch": "D", "n": 0}
    for k in list(state["ack_msgs"])[:-500]:  # 폴러는 acked 상한(500)에 맞춰 최근 스레드만 보관
        del state["ack_msgs"][k]
    push.record_delivery([(i + 1, {"ts": root})], i + 1)
    before = len(calls)
    g["done_phase"](state)
    made = calls[before:]
    if len(made) != 1 or made[0][1] != "📨 메인에 전달했어요 (1건)":
        bad.append(i)
print(json.dumps({"rounds": rounds, "calls": len(calls), "bad": len(bad), "first_bad": bad[:1]}))
