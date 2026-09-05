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
    env = dict(os.environ, HOME=HOMEDIR, CLAUDE_CODE_SESSION_ID=session, NO_COLOR="1")
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

# --- relatedness is judged on the subject, through Romanian inflection ------------------------
import importlib.util
_spec = importlib.util.spec_from_file_location("pt", HOOK)
pt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pt)
QUEUE = {"items": [
    {"id": 1, "text": "modifica hook: daca ii dau un task sau o intrebare care e legata de alt "
                      "pending task sa le comaseze in acelasi task", "state": "pending"},
    {"id": 2, "text": "fa deploy la site-ul teolia si verifica DNS", "state": "pending"},
    {"id": 3, "text": "repara build-ul iOS pentru subpiata pe GitHub Actions", "state": "pending"},
], "next": 4}
for ask, want in [
        ("si hook-ul sa comaseze si intrebarile despre pending task", 1),
        ("cum decide hook-ul ca doua taskuri pending sunt legate?", 1),   # legate/legata, taskuri/task
        ("scrie testul pentru comasarea taskurilor din hook", 1),
        ("de ce nu merge deploy-ul la teolia?", 2),                       # only two shared words
        ("build-ul iOS de la subpiata inca pica", 3),
        ("adauga tailscale pe oracle-milan", None),
        ("modifica statusline sa arate ora", None),                       # shares "modifica" only
        ("verifica DNS la german-meister", None),
        ("fa un build android pentru spark", None)]:
    got = pt.related({"items": [dict(i) for i in QUEUE["items"]], "next": 4}, ask)
    got = got["id"] if got else None
    assert got == want, "related(%r) = %s, expected %s" % (ask, got, want)
print("OK: follow-ups match their task, unrelated asks match nothing")

# --- edit / undo / multi-id close / report ----------------------------------------------------
submit("hhhhhhhh", "curata cache-ul npm de pe laptop")
submit("hhhhhhhh", "trimite factura catre clientul din Cluj")
submit("hhhhhhhh", "reporneste imprimanta din birou")
run("hhhhhhhh", ["edit", "1", "curata cache-ul npm si pnpm"])
assert "1. curata cache-ul npm si pnpm" in board("hhhhhhhh"), board("hhhhhhhh")

run("hhhhhhhh", ["drop", "2"])
assert "factura" not in board("hhhhhhhh")
out = run("hhhhhhhh", ["undo"])
assert "restored 2" in out, out
assert "factura" in board("hhhhhhhh"), "undo must put a dropped task back:\n" + board("hhhhhhhh")

run("hhhhhhhh", ["doing", "1"])
run("hhhhhhhh", ["done", "1", "2"])
b = board("hhhhhhhh")
assert "cache-ul" not in b, "both ids must close in one call:\n" + b
assert "● 2. trimite factura" in b, "the last of the batch keeps the ● receipt:\n" + b
out = run("hhhhhhhh", ["undo"])
assert "reopened 2" in out, out
assert "factura" in board("hhhhhhhh")

rep = run("hhhhhhhh", ["report"])
assert "curata cache-ul npm si pnpm" in rep, "report must list what was finished:\n" + rep
assert "factura" not in rep, "undo must take the task back out of the report:\n" + rep
print("OK: edit keeps the id, undo restores, done takes several ids, report reads the log")

# --- what Claude said when the task closed is kept with the task ------------------------------
submit("iiiiiiii", "muta serverul de mail pe portul 587")
run("iiiiiiii", ["doing", "1"])
run("iiiiiiii", ["done", "1"])
transcript = os.path.join(HOMEDIR, "transcript.jsonl")
with open(transcript, "w") as fh:
    fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "go"}}) + "\n")
    fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Port changed to 587 and TLS forced.\nStill to do: rotate the "
                                 "relay password."}]}}) + "\n")
run("iiiiiiii", ["stop"], json.dumps({"session_id": "iiiiiiii", "cwd": HOMEDIR,
                                      "transcript_path": transcript}))
rep = run("iiiiiiii", ["report"])
assert "rotate the relay password" in rep, "the closing message must be kept with the task:\n" + rep

# a note can be corrected by hand, and a task closed later does not inherit the old note
submit("iiiiiiii", "reinstaleaza certificatul wildcard")
run("iiiiiiii", ["done", "2"])
run("iiiiiiii", ["note", "2", "renewed until March, DNS-01 via the API token"])
rep = run("iiiiiiii", ["report"])
assert "renewed until March" in rep, rep
assert rep.count("rotate the relay password") == 1, "the note leaked onto another task:\n" + rep
print("OK: the closing message is recorded against the task it closed")


def test_judge_ids():
    from pending_tasks import judge_ids
    assert judge_ids("none", [1, 2]) == []
    assert judge_ids("1\n3\n", [1, 2, 3]) == [1, 3]
    assert judge_ids("Task 2 is done.", [1, 2]) == [2]
    assert judge_ids("7", [1, 2]) == []
