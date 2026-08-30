#!/usr/bin/env python3
"""One check of the `step` / `next` / `.` accept path added for the next-step suggestion.

The suggestion is only useful if three things agree: the board shows it, `next` prints the same
string, and typing `.` is understood as "do that" rather than queued as a brand new task. That
last one is the failure that would be silently annoying -- a queue slowly filling with dots -- so
it is the one worth a test.

Runs against a throwaway queue file; touches nothing real.
"""
import json, os, subprocess, sys, tempfile

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_tasks.py")
tmp = tempfile.mkdtemp(prefix="pendtest-")
env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp, NO_COLOR="1",
           CLAUDE_CONFIG_DIR=os.path.join(tmp, ".claude"))

# `submit` repoints ~/.claude/pending/.last at whatever queue it just wrote, and a bare CLI
# `done N` follows that pointer. Without this the test would leave every real console aiming at a
# throwaway file in /tmp -- which it did, once.
POINTERS = [os.path.join(os.path.expanduser("~/.claude/pending"), n)
            for n in os.listdir(os.path.expanduser("~/.claude/pending")) if n.startswith(".last")]
SAVED = {f: open(f).read() for f in POINTERS if os.path.isfile(f)}


def restore():
    for f, v in SAVED.items():
        open(f, "w").write(v)


import atexit
atexit.register(restore)


def run(*a, stdin=None):
    r = subprocess.run([sys.executable, HOOK] + list(a), input=stdin, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, (a, r.returncode, r.stderr[-300:])
    return r.stdout


def submit(prompt):
    return run("submit", stdin=json.dumps({"prompt": prompt, "cwd": tmp, "session_id": "testsess"}))


submit("first ask about the thing")
submit("second ask about another thing")
run("doing", "1")
run("step", "1", "run the aggregation and report the halves split")

assert run("next").strip() == "run the aggregation and report the halves split"
assert "> run the aggregation" in run("board"), run("board")

# The whole point: `.` is an accept, not an ask.
out = submit(".")
assert "task 1" in out and "run the aggregation" in out, out
assert "Queued as task" not in out, out
listing = run("list")
assert "." not in [l.strip()[-1:] for l in listing.splitlines() if l.strip().startswith(("▶", " "))] \
    or "3." not in listing, listing
assert "3." not in listing, "the dot got queued: %s" % listing

# With nothing in progress the suggestion is the top of the queue, and an item with no step of its
# own still suggests itself rather than going silent.
run("done", "1")
assert run("next").strip() == "second ask about another thing"

run("done", "2")
assert run("next").strip() == ""
assert "queue is empty" in submit(".")

print("ok  step/next/board/accept: suggestion shown, `.` accepts it, never queued")
