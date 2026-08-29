#!/usr/bin/env python3
"""Drive tasks_tui.py through a pty and check the queue file it writes. Run: python3 this_file"""
import json
import os
import pty
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TUI = os.path.join(HERE, "tasks_tui.py")


def queue(items):
    p = os.path.join(tempfile.mkdtemp(), "q.json")
    json.dump({"next": len(items) + 1,
               "items": [{"id": n + 1, "text": t, "state": "pending", "at": 0}
                         for n, t in enumerate(items)]}, open(p, "w"))
    return p


def drive(path, keys, wait=0.35):
    """Type `keys` into the board, one chunk at a time, and return what it painted."""
    m, s = pty.openpty()
    pr = subprocess.Popen([sys.executable, TUI, "--queue", path], stdin=s, stdout=s, stderr=s,
                          env=dict(os.environ, TERM="xterm-256color", LINES="20", COLUMNS="90"))
    os.close(s)
    time.sleep(wait)
    out = b""
    for k in keys:
        os.write(m, k.encode())
        time.sleep(wait)
        try:
            os.set_blocking(m, False)
            out += os.read(m, 1 << 16)
        except OSError:
            pass
        finally:
            os.set_blocking(m, True)
    pr.wait(timeout=5)
    os.close(m)
    return out.decode("utf-8", "replace")


def items(path):
    return {i["id"]: i["state"] for i in json.load(open(path))["items"]}


p = queue(["primul task", "al doilea task", "al treilea task"])
screen = drive(p, ["j", " ", "q"])
assert "al doilea task" in screen, screen[-800:]
assert items(p) == {1: "pending", 2: "doing", 3: "pending"}, items(p)
print("OK: arrow + space makes that row the one to do now")

p = queue(["unu", "doi"])
drive(p, ["\r", "j", "j", "j", "\r", "q"])   # menu -> 4th entry (drop) -> run
assert items(p) == {2: "pending"}, items(p)
print("OK: Enter opens the row menu, Enter on `cancel (drop)` removes the task")

p = queue(["unu", "doi"])
drive(p, ["x", "q"])
d = json.load(open(p))
assert items(p) == {1: "done", 2: "pending"}, items(p)
assert d["log"][0]["text"] == "unu", d
print("OK: `x` closes the task, keeps it as ● and logs it")

p = queue(["unu", "doi", "trei"])
screen = drive(p, ["b", "q"])
assert items(p)[1] == "bg", items(p)
print("OK: `b` backgrounds the task without closing it")
