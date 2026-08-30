#!/usr/bin/env python3
"""Cross-session isolation check for pending_tasks.py.

Two consoles launched from $HOME must never see or close each other's tasks. Run: python3 this_file
"""
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_tasks.py")
HOMEDIR = tempfile.mkdtemp()
os.makedirs(os.path.join(HOMEDIR, ".claude", "pending"), exist_ok=True)


def run(session, argv, stdin=""):
    env = dict(os.environ, HOME=HOMEDIR, CLAUDE_CODE_SESSION_ID=session)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run([sys.executable, HOOK] + argv, input=stdin,
                          capture_output=True, text=True, env=env).stdout


def submit(session, prompt):
    return run(session, ["submit"],
               json.dumps({"prompt": prompt, "cwd": HOMEDIR, "session_id": session}))


def board(session):
    return run(session, ["list"])


submit("aaaaaaaa", "fa deploy la site")
assert "fa deploy la site" in board("aaaaaaaa"), board("aaaaaaaa")

submit("bbbbbbbb", "alt task complet diferit")
b = board("bbbbbbbb")
assert "fa deploy" not in b, "BLEED: session B inherited session A's task:\n" + b
assert "alt task" in b, b

a = board("aaaaaaaa")
assert "alt task" not in a, "BLEED: session A sees session B's task:\n" + a
assert "fa deploy" in a, a

# `done 1` typed in B (no CLAUDE_PROJECT_DIR, unreliable cwd) must close B's task 1, never A's.
run("bbbbbbbb", ["done", "1"])
assert "● 1. alt task" in board("bbbbbbbb"), board("bbbbbbbb")
assert "fa deploy" in board("aaaaaaaa"), "BLEED: B's `done 1` closed A's task"

print("OK: no cross-session bleed")

# --- bg: a task waiting on something detached frees the console ------------------------------
submit("cccccccc", "porneste buildul lung")
submit("cccccccc", "scrie documentatia")
submit("cccccccc", "deploy dupa build")
out = run("cccccccc", ["bg", "1", "--blocks", "3"])
assert "console is free" in out, out
assert "Start task 2 next" in out, "bg must hand over the next independent task:\n" + out
assert "task 3" not in out.split("Start task")[1].split("\n")[0], "blocked task offered:\n" + out
# the suggestion engine must agree
assert run("cccccccc", ["next"]).strip().startswith("scrie documentatia"), run("cccccccc", ["next"])
# a bg task is still open and still on the board
board_c = run("cccccccc", ["list"])
assert "porneste buildul lung" in board_c and "⏳" in board_c, board_c
# finishing the background task unblocks its dependant
run("cccccccc", ["done", "1"])
assert run("cccccccc", ["next"]).strip().startswith("scrie documentatia")
run("cccccccc", ["done", "2"])
assert run("cccccccc", ["next"]).strip().startswith("deploy dupa build"), run("cccccccc", ["next"])

print("OK: bg frees the console without offering dependent tasks")

# --- board must not print the same task twice ------------------------------------------------
submit("dddddddd", "primul task")
submit("dddddddd", "al doilea task")
b = run("dddddddd", ["board"])
assert b.count("primul task") == 1, "board duplicates the suggested task:\n" + b
assert "> 1. primul task" in b and "[ . ]" in b, b
# a recorded step IS different text, so it keeps its own line
run("dddddddd", ["step", "1", "fa exact pasul asta"])
b = run("dddddddd", ["board"])
assert b.count("primul task") == 1, b
assert "> fa exact pasul asta   [ . ]" in b, b

print("OK: board shows each task once")

# --- pending items carry a circle, not blank space --------------------------------------------
submit("eeeeeeee", "unu")
submit("eeeeeeee", "doi")
run("eeeeeeee", ["doing", "1"])
lst = run("eeeeeeee", ["list"])
assert "▶ 1. unu" in lst, lst
assert "○ 2. doi" in lst, "pending item must be marked with a circle:\n" + lst
b = run("eeeeeeee", ["board"])
assert "○ 2. doi" in b, b

print("OK: pending marked ○")

# --- a closed task is shown once, as ●, and gone as soon as work moves on -------------------
submit("ffffffff", "primul")
submit("ffffffff", "al doilea")
run("ffffffff", ["done", "1"])
b = board("ffffffff")
assert "● 1. primul" in b, "the task just closed must be visible as ●:\n" + b
run("ffffffff", ["doing", "2"])
b = board("ffffffff")
assert "primul" not in b, "the ● must go once the next task starts:\n" + b
run("ffffffff", ["done", "2"])
assert "● 2. al doilea" in board("ffffffff") and "primul" not in board("ffffffff")
print("OK: ● marks the task just closed, cleared when work moves on")

# --- a follow-up about a task already queued is folded into it, not queued twice --------------
submit("gggggggg", "repara build-ul iOS pentru subpiata pe GitHub Actions")
out = submit("gggggggg", "de ce cade build-ul iOS subpiata pe GitHub Actions?")
assert "part of task 1" in out, "the follow-up must merge into task 1:\n" + out
b = board("gggggggg")
assert "(+1)" in b, "the merged ask must show as +1 on the board:\n" + b
assert "2." not in b, "no second task may be opened for the same work:\n" + b
out = submit("gggggggg", "adauga o pagina de contact pe site-ul teolia")
assert "part of task" not in out, "an unrelated ask must stay its own task:\n" + out
assert "○ 2. adauga o pagina de contact" in board("gggggggg"), board("gggggggg")
print("OK: related ask merged, unrelated ask queued separately")
