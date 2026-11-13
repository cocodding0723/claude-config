"""동시 호출 시험용 자식 프로세스: 시작 신호(파일)를 기다렸다가 같은 결정에 claim N번 시도하고 결과 줄을 출력함."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts"))
import answer_store as st  # noqa: E402

source, go, rounds = sys.argv[1], sys.argv[2], int(sys.argv[3])
store = st.AnswerStore()
while not os.path.exists(go):
    time.sleep(0.001)
for i in range(rounds):
    c = store.claim(st.Answer(f"D-C{i:03d}", f"ts.{i}", "C1", "제목", f"{i}. 안", source, {"action": "pick", "pick": 1}))
    print(f"{i} {c.status} {c.by or '-'}", flush=True)
