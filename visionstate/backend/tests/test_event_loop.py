"""Nothing waits on the database on the event loop: a busy SQLite (``timeout`` 30 s) would stop
every sensor, MQTT and the API with it. Database work in an ``async def`` goes through
``asyncio.to_thread`` (a function defined inside it and run that way is fine)."""

import ast
from pathlib import Path

import visionstate

PACKAGE = Path(visionstate.__file__).parent

# Methods that open a database session themselves.
BLOCKING = {"session", "get_setting", "set_setting", "get_text", "get_dict"}  # on .db
BLOCKING_RUNTIME = {"load_sensor", "frame_for_view", "heads_load"}


def _own_calls(fn: ast.AsyncFunctionDef) -> list[ast.Call]:
    """The calls in a function's own body, not in functions or lambdas defined inside it."""
    calls, stack = [], list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(node, ast.Call):
            calls.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return calls


def _blocking(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    target = func.value
    on_db = (
        isinstance(target, ast.Attribute) and target.attr == "db" or isinstance(target, ast.Name) and target.id == "db"
    )
    return on_db and func.attr in BLOCKING or func.attr in BLOCKING_RUNTIME


def blocking_calls(source: str, name: str = "<source>") -> list[str]:
    found = []
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, ast.AsyncFunctionDef):
            found += [f"{name}:{c.lineno} {fn.name}: {ast.unparse(c.func)}" for c in _own_calls(fn) if _blocking(c)]
    return found


def test_the_check_finds_a_blocking_call():
    planted = """
async def endpoint(rt):
    def inside():
        with rt.db.session() as s:  # fine: run in a thread
            pass
    await asyncio.to_thread(inside)
    await asyncio.to_thread(rt.load_sensor, 1)  # fine: passed, not called
    with rt.db.session() as s:  # blocks the loop
        pass
    rt.load_sensor(1)  # blocks the loop
"""
    assert sorted(blocking_calls(planted)) == [
        "<source>:10 endpoint: rt.load_sensor",
        "<source>:8 endpoint: rt.db.session",
    ]


def test_no_database_work_on_the_event_loop():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        found += blocking_calls(path.read_text(encoding="utf-8"), str(path.relative_to(PACKAGE)))
    assert not found, "Run these through asyncio.to_thread:\n" + "\n".join(found)
