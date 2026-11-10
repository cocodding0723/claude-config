"""수집기에 삭제·종료 호출이 없는지 구문 트리로 확인. 사용: no_delete_check.py <panel_collect.py>
문자열 grep 은 os.remove·os.kill·쪼갠 문자열을 못 잡아(리뷰 변이 M5a·M5c) 호출 자체를 봄.
허용은 함수·호출·첫 인자 조합으로만: 자기 임시 파일·잠금·로그 회전·시간 초과 자식 묶음 정리. 그 밖은 위치를 출력하고 종료 1."""
import ast
import sys

# 어느 객체의 메서드든 삭제·종료로 보는 이름(문자열·리스트에 같은 이름의 무해한 메서드가 없음)
ANY_BASE = {"unlink", "rmdir", "removedirs", "rmtree", "kill", "killpg", "terminate", "send_signal", "truncate"}
# os·shutil 모듈에서만 삭제로 보는 이름(str.replace·list.remove 와 구분)
MODULE_ONLY = {"remove", "replace", "rename", "renames", "move", "system", "popen", "execv", "execvp", "spawnv"}
MODULES = {"os", "shutil", "pathlib"}
ALLOW = {
    ("write_atomic", "os.remove", "tmp"),
    ("write_atomic", "os.replace", "tmp"),
    ("log", "os.replace", "LOG"),
    ("acquire", "os.rmdir", "LOCK"),
    ("main", "os.rmdir", "LOCK"),
    ("run_text", "os.killpg", "p.pid"),
}

src = open(sys.argv[1], encoding="utf-8").read()
tree = ast.parse(src)
bad = []


def danger(func):
    if isinstance(func, ast.Attribute):
        if func.attr in ANY_BASE:
            return True
        return func.attr in MODULE_ONLY and isinstance(func.value, ast.Name) and func.value.id in MODULES
    return isinstance(func, ast.Name) and func.id in ANY_BASE | MODULE_ONLY


def walk(node, fn_name):
    for child in ast.iter_child_nodes(node):
        name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn_name
        if isinstance(child, ast.ImportFrom) and child.module in MODULES | {"signal", "subprocess"}:
            hit = [a.name for a in child.names if a.name in ANY_BASE | MODULE_ONLY or a.name == "*"]
            if hit:
                bad.append(f"{child.lineno}: from {child.module} import {','.join(hit)}")
        if isinstance(child, ast.Call):
            f = child.func
            if isinstance(f, ast.Name) and f.id == "getattr" and len(child.args) > 1 and isinstance(child.args[1], ast.Constant) \
                    and str(child.args[1].value) in ANY_BASE | MODULE_ONLY:
                bad.append(f"{child.lineno}: getattr(..., {child.args[1].value!r})")
            if danger(f):
                key = (fn_name, ast.unparse(f), ast.unparse(child.args[0]) if child.args else "")
                if key not in ALLOW:
                    bad.append(f"{child.lineno}: {key[0] or '<모듈>'} 안 {key[1]}({key[2]})")
        walk(child, name)


walk(tree, None)
if bad:
    print("FAIL " + "; ".join(bad[:3]))
    sys.exit(1)
print("ok")
