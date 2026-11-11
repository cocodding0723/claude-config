#!/usr/bin/env python3
"""slack_socket.py 시험 구동기. slack_sdk 자리에 가짜 모듈을 넣고 수신기를 __main__ 으로 실행함.

실제 slack_sdk·네트워크·토큰은 쓰지 않음. 동작은 시나리오 JSON(FAKE_SCENARIO)이 정하고 호출 기록은 FAKE_CALLS 에 한 줄씩 남김.
사용: python3 -I driver.py <slack_socket.py 경로>

시나리오 키
  connect    연결 호출 순서별 결과 목록("ok"|"fail"|"silent"), 목록을 넘으면 default
             fail 은 예외, silent 는 실제 SDK 의 웹소켓 단계 실패처럼 예외 없이 is_connected=False
  default    목록 밖 호출의 결과(기본 "ok")
  lifetimes  성공한 연결 순서별 [끊김까지 초, 복구까지 초|null, 다시 끊김까지 초, 다시 복구까지 초|null ...].
             연결 시각 기준 절대 초이며 null 을 만나면 그 상태로 유지
  events     이벤트를 받을 클라이언트가 연결된 뒤 listeners 에 넣을 요청 [{at, type, envelope_id, payload}]
  events_client  이벤트를 받을 클라이언트 번호(기본 1). 새 클라이언트 리스너 누락 검출용
  runner_error   true 면 current_session_runner.shutdown() 이 예외를 냄(정리 실패 삼킴 검출용)
  runner_hang    true 면 current_session_runner.shutdown() 이 영원히 안 돌아옴(정리 시간 상한 검출용)

호출 기록(FAKE_CALLS): new·connect·close·disconnect·terminate·runner_shutdown·ack 를 순서대로 한 줄씩.
실제 SDK 처럼 연결된 적 있는 클라이언트의 runner 는 terminate(state.terminated=True) 없이는 shutdown 이 끝나지 않음.

기본값 확인: python3 -I driver.py --defaults <slack_socket.py 경로>  → Timing.from_env() 와 RETIRE_TIMEOUT_S 출력
"""
import json
import os
import runpy
import sys
import threading
import time
import types

SCEN = json.load(open(os.environ["FAKE_SCENARIO"], encoding="utf-8")) if "FAKE_SCENARIO" in os.environ else {}
CALLS = os.environ.get("FAKE_CALLS", os.devnull)
LOCK = threading.Lock()
COUNT = {"created": 0, "connects": 0, "ok": 0}


def note(line: str) -> None:
    with LOCK:
        with open(CALLS, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {line}\n")


class FakeNetError(Exception):
    pass


class SocketModeRequest:
    def __init__(self, type, envelope_id, payload):
        self.type, self.envelope_id, self.payload = type, envelope_id, payload


class SocketModeResponse:
    def __init__(self, envelope_id):
        self.envelope_id = envelope_id


class FakeState:
    def __init__(self, owner):
        self._owner = owner
        self._terminated = False

    @property
    def terminated(self):
        return self._terminated

    @terminated.setter
    def terminated(self, value):
        self._terminated = value
        if value:
            note(f"terminate {self._owner.n}")


class FakeRunner:
    """실제 IntervalRunner 처럼 shutdown 이 스레드 종료를 기다림. 수신 루프에 들어간 뒤엔 terminated 가 켜져야 끝남"""

    def __init__(self, owner):
        self._owner = owner

    def shutdown(self):
        note(f"runner_shutdown {self._owner.n}")
        if SCEN.get("runner_error"):
            raise FakeNetError("LEAK_DETAIL_SENTINEL")
        if SCEN.get("runner_hang"):
            threading.Event().wait()
        while self._owner.looping and not self._owner.current_session_state.terminated:
            time.sleep(0.05)


class SocketModeClient:
    def __init__(self, app_token, **kwargs):
        with LOCK:
            COUNT["created"] += 1
            self.n = COUNT["created"]
        self.socket_mode_request_listeners = []
        self.up = False
        self.closed = False
        self.looping = False  # 연결된 적이 있어 runner 가 수신 루프 안에 있음
        self.current_session_state = FakeState(self)
        self.current_session_runner = FakeRunner(self)
        note(f"new {self.n}")

    def is_connected(self):
        return self.up

    def _later(self, seconds, fn):
        timer = threading.Timer(seconds, fn)
        timer.daemon = True
        timer.start()

    def _set(self, value):
        if not self.closed:
            self.up = value

    def connect(self):
        with LOCK:
            COUNT["connects"] += 1
            k = COUNT["connects"]
        plan = SCEN.get("connect", [])
        result = plan[k - 1] if k - 1 < len(plan) else SCEN.get("default", "ok")
        note(f"connect {self.n} {result}")
        if result == "fail":
            raise FakeNetError("LEAK_DETAIL_SENTINEL")
        if result == "silent":
            return
        self.up = True
        self.looping = True
        with LOCK:
            idx = COUNT["ok"]
            COUNT["ok"] += 1
        life = SCEN.get("lifetimes", [])
        if idx < len(life) and life[idx]:
            for i, seconds in enumerate(life[idx]):
                if seconds is None:
                    break
                self._later(seconds, lambda up=bool(i % 2): self._set(up))
        if self.n == SCEN.get("events_client", 1):
            for ev in SCEN.get("events", []):
                self._later(ev["at"], lambda ev=ev: self._deliver(ev))

    def _deliver(self, ev):
        req = SocketModeRequest(ev["type"], ev["envelope_id"], ev.get("payload"))
        for listener in list(self.socket_mode_request_listeners):
            listener(self, req)

    def close(self):
        self.closed = True
        self.up = False
        note(f"close {self.n}")

    def disconnect(self):
        self.up = False
        note(f"disconnect {self.n}")

    def send_socket_mode_response(self, response):
        note(f"ack {self.n} {response.envelope_id}")


class WebClient:
    """웹 API 가짜. 호출을 'web 메서드 JSON' 한 줄로 남김. 시나리오 history 가 conversations_history 응답"""

    def __init__(self, token=None, **kwargs):
        pass

    def _call(self, name, kw):
        note("web %s %s" % (name, json.dumps(kw, ensure_ascii=False, sort_keys=True)))
        return {"ok": True}

    def chat_update(self, **kw):
        return self._call("chat_update", kw)

    def views_open(self, **kw):
        return self._call("views_open", kw)

    def conversations_history(self, **kw):
        self._call("conversations_history", kw)
        return {"ok": True, "messages": SCEN.get("history", [])}


def install_fakes() -> None:
    sdk = types.ModuleType("slack_sdk")
    mode = types.ModuleType("slack_sdk.socket_mode")
    request = types.ModuleType("slack_sdk.socket_mode.request")
    response = types.ModuleType("slack_sdk.socket_mode.response")
    mode.SocketModeClient = SocketModeClient
    request.SocketModeRequest = SocketModeRequest
    response.SocketModeResponse = SocketModeResponse
    sdk.socket_mode = mode
    sdk.WebClient = WebClient
    mode.request, mode.response = request, response
    sys.modules.update({
        "slack_sdk": sdk, "slack_sdk.socket_mode": mode,
        "slack_sdk.socket_mode.request": request, "slack_sdk.socket_mode.response": response,
    })


if __name__ == "__main__":
    install_fakes()
    if sys.argv[1] == "--defaults":
        mod = runpy.run_path(sys.argv[2], run_name="slack_socket_defaults")
        print(mod["Timing"].from_env(), f"retire_timeout_s={mod['RETIRE_TIMEOUT_S']}")
        sys.exit(0)
    target = sys.argv[1]
    sys.argv = [target]
    runpy.run_path(target, run_name="__main__")
