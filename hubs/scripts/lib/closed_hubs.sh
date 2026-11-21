# 목록 inode 유지: 추가·제거 모두 같은 파일의 flock 사용함.
closed_hubs_registry() {
  "$PY" - "$@" <<'PY'
import fcntl
import os
from pathlib import Path
import sys
import time

action, filename, target, *expected = sys.argv[1:]
target = os.path.realpath(target)
if action == "read" and not os.path.isfile(filename):
    sys.exit(0)
if action != "read":
    Path(filename).parent.mkdir(parents=True, exist_ok=True)
with open(filename, "r" if action == "read" else "a+") as stream:
    fcntl.flock(stream, fcntl.LOCK_SH if action == "read" else fcntl.LOCK_EX)
    stream.seek(0)
    lines = stream.readlines()
    def fields(line):
        path, _, stamp = line.rstrip("\n").partition("\t")
        return os.path.realpath(path), stamp
    if action == "read":
        stamps = [fields(line)[1] for line in lines if fields(line)[0] == target]
        if stamps:
            print(max(map(int, stamps)) if all(s.isascii() and s.isdigit() and int(s) > 0 for s in stamps) else "legacy")
    else:
        kept = [line for line in lines if not (fields(line)[0] == target and
                (action == "set" or fields(line)[1] == expected[0]))]
        if action == "set":
            kept.append(f"{target}\t{int(time.time())}\n")
        stream.seek(0)
        stream.truncate()
        stream.writelines(line if line.endswith("\n") else line + "\n" for line in kept)
PY
}
