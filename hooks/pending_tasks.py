#!/usr/bin/env python3
"""Pending-task queue shared by every Claude Code console.

The user fires several asks in a row while a long job runs. The rule is: the running task is never
abandoned, the new ask is queued, and nothing is forgotten. This keeps that queue OUTSIDE the
conversation, so it survives compaction, a session restart and a second console in the same
project, and shows it in two places the user always sees -- the statusline, and the context
injected on every prompt.

Modes:
    submit          UserPromptSubmit hook: stdin is the hook JSON. Queues the prompt and prints the
                    queue back as context. A prompt that carries several asks ("1: ... 2: ...", or a
                    bulleted list) is split into one task per ask, so a three-ask message cannot be
                    half-answered and forgotten. Explicit "stop everything" phrasing is flagged, not
                    obeyed here -- obeying it is the assistant's job, remembering it is this file's.
    line            one short line for the statusline (empty when the queue is empty)
    board           the whole open queue, one item per line, for the statusline's extra rows
    scroll N        move the board's 5-row window: +N / -N to step, a bare number to jump, or
                    top / end. `down [N]` and `up [N]` are the short forms. The statusline is
                    rendered and never focused, so it cannot take a keypress -- the offset is
                    stored next to the queue and the next render shows the new window.
    list            the queue, numbered, for a human or the assistant to read
    add TEXT        queue an item by hand
                    A new ask that is really about a task already on the board is folded INTO that
                    task instead of opening a second row for one piece of work; the row then shows
                    `(+N)`. Match is on shared content words, so it needs the ask to name the same
                    subject, not merely be on a similar topic.
    Board markers: >  the one to do next (accepts a bare `.`)   ▶  in progress
                   ○  pending, not started   ⊙  pending, added by the prompt splitter
                   ⏳ running in the background, not holding the console
                   ●  just finished; it clears as soon as the next task starts or finishes

    doing N         mark item N as the one being worked on (clears any other 'doing')
    bg N [--blocks IDS]
                    item N now waits on something detached, so it stops holding the console:
                    it stays open, drops out of the next-step suggestion, and the queue hands
                    over the next task that does not need its result. --blocks lists the ids
                    that DO need it, so they are held back too.
    done N          finish item N and remove it from the queue
    drop N          remove item N without doing it
    clear           empty the queue
    step N TEXT     record the concrete next action for item N -- what to actually do next, not
                    the item's title. Shown under the board as `>` and accepted by typing `.`
    next            print that action, for the `.` shorthand and for a human to read
    aisplit F ID    background pass over item ID of queue F: asks a small model how many separate
                    asks the text really carries, and replaces the item with one per ask, marked
                    `*` on the board. Fired detached by `submit` -- the regex split only catches
                    numbered / bulleted messages, this catches prose that runs several asks together.
    stop            Stop hook: when the queue still holds open tasks, refuse the stop and hand the
                    next one back, so a finished task rolls straight into the next instead of
                    waiting for the user to say "continue". When the last one clears, it asks once
                    for a summary of everything done in that run.

Store: ~/.claude/pending/<project>.json, keyed on the project directory, so each project has its
own queue and consoles in the same project share one. The home directory is not a project -- every
console launched from ~ would share one global queue and mix unrelated work -- so queues there are
keyed on the session as well: ~/.claude/pending/home-vrs-<session>.json.
"""
import glob
import json
import os
import re
import sys
import time
import unicodedata

HOME = os.path.expanduser("~")
DIR = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", os.path.join(HOME, ".claude")), "pending")

# Phrases that mean "drop what you are doing" -- the one case where the current task yields. Only
# flagged for the assistant to act on; the rest of the queue still stands.
PREEMPT = re.compile(r"\b(stop (everything|tot|totul)|opre[sș]te tot|las[aă] tot|drop everything)\b",
                     re.I)
# Prompts that are not tasks: slash commands and bare continuations ("go on", "yes", "ok").
SKIP = re.compile(r"^\s*(/|!|#)|^\s*(continu[aă]|continue|go on|mai departe|da|ok|okay|yes|nu|no|"
                  r"stop|mersi|thanks|multumesc|mul[țt]umesc)\s*[.!]?\s*$", re.I)
# The one-keystroke accept. Typing it is not a new ask -- it means "do the next step you already
# proposed", so it must never be queued as a task of its own.
ACCEPT = re.compile(r"^\s*[.>]\s*$")


# One message, several asks. Two shapes cover what the user actually writes: inline numbering
# ("1: ... 2: ..."), and a bulleted / numbered list one per line. Anything else stays one task --
# splitting on every sentence would turn a paragraph of context into a fake backlog.
NUM = re.compile(r"(?:(?<=^)|(?<=[\s;,]))(\d{1,2})\s*[:).]\s+")
BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d{1,2}[.)])\s+(.+)$", re.M)
MIN_WORDS = 3          # a fragment shorter than this is a scrap, not an ask


def split_tasks(prompt):
    """[task text, ...] -- one entry per ask the prompt carries, the whole prompt if just one."""
    one = " ".join(prompt.split())
    hits = list(NUM.finditer(one))
    if len(hits) >= 2:
        segs = []
        for k, m in enumerate(hits):
            end = hits[k + 1].start() if k + 1 < len(hits) else len(one)
            seg = one[m.end():end].strip(" ,;.")
            if len(seg.split()) >= MIN_WORDS:
                segs.append(seg)
        if len(segs) >= 2:
            return segs
    lines = [" ".join(m.group(1).split()) for m in BULLET.finditer(prompt)]
    # A line ending in the ellipsis is a row of this very board pasted back into the chat, not a
    # new ask -- queueing it would re-queue the queue.
    lines = [x for x in lines if len(x.split()) >= MIN_WORDS and not x.endswith("…")]
    if len(lines) >= 2:
        return lines
    return [one]


# A new ask that is really about a task already on the board must not open a second row: the user
# ends up with two half-tasks for one piece of work. Relatedness is measured on content words only
# -- shared stopwords ("cum", "the", "fac") say nothing about the subject.
STOP = set("""a ai al ale am ar are as asa au ca care cat ce cu de din do does doar dupa e ei el
ea este esti eu fac face faci fara fi fie fost hai iar il imi in intr into is it la le li lui mai
mi mie ne ni nu o pe pentru poate prin sa sau se si sunt sa ta te ti tot tu un una une unde va vor
vrei a an and any are as at be but by can could did do for from get got has have how i if in is it
its just make me my no not of on or our so than that the their them then there these they this to
was we what when where which who why will with would you your task tasks
about again also another ask asks first second other please still now thing things question
intrebare intrebarea ceva lucru lucrul chestie chestia asta asa acolo aici""".split())
# Two thresholds, because a three-word follow-up and a thirty-word task can never share much of the
# longer one: score on the SHORTER side, and demand three real shared words. Fewer, and two asks
# glue together on one common noun.
# ponytail: bag-of-words overlap, no stemming -- "build" and "build-ul" count as different words.
# Real follow-ups repeat the proper nouns (subpiata, GitHub Actions, the file name), which is what
# carries the match. Move to embeddings only if misses show up in practice.
REL_MIN = 0.6
REL_WORDS = 3


def keywords(t):
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z0-9_./-]{3,}", t) if w not in STOP}


def related(d, text):
    """The open item this ask belongs to, or None -- highest overlap wins, newest breaks a tie."""
    k = keywords(text)
    if len(k) < REL_WORDS:
        return None
    best, score = None, 0.0
    for i in open_items(d):
        o = keywords(" ".join([i["text"]] + (i.get("more") or [])))
        shared = k & o
        if len(shared) < REL_WORDS:
            continue
        s = len(shared) / min(len(k), len(o))
        if s >= REL_MIN and s >= score:
            best, score = i, s
    return best


def sid():
    """Short session id, or "" when this is not running inside a Claude session."""
    return (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]


def path_for(cwd, session=None):
    d = (cwd or HOME).rstrip("/")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", d).strip("-") or "root"
    # ~ is a catch-all, not a project: two consoles started there are usually on different things,
    # so they get one queue each instead of one shared global board.
    if os.path.abspath(d or HOME) == HOME:
        session = session if session is not None else sid()
        if session:
            slug += "-" + session
    return os.path.join(DIR, slug + ".json")


LAST = os.path.join(DIR, ".last")


def last_pointer():
    """Per-session pointer file: two consoles must not overwrite each other's `.last`."""
    s = sid()
    return LAST + "-" + s if s else LAST


def queue_path(cwd=None):
    """The queue the CLI modes act on.

    The hook runs with CLAUDE_PROJECT_DIR set, so `submit` always lands on the project's queue and
    records it in `.last`. A `done N` typed from a Bash tool call has neither that variable nor a
    reliable cwd -- the shell sits wherever the last `cd` left it -- so it follows `.last`, i.e. the
    queue the most recent prompt went into. Ids are per-queue and not unique across projects, so
    never hunt for an id across files: that deleted an unrelated project's task once.
    """
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        return path_for(os.environ["CLAUDE_PROJECT_DIR"])
    # Inside a session, only that session's pointer counts. The shared `.last` is written by every
    # console, so reading it would hand this session whichever queue got the most recent prompt
    # anywhere on the machine -- that is how one console's `done N` closed another's task.
    pointers = (last_pointer(),) if sid() else (LAST,)
    for f in pointers:
        try:
            p = open(f).read().strip()
            if p and os.path.exists(p):
                return p
        except OSError:
            pass
    cwd = cwd or os.getcwd()
    d = os.path.abspath(cwd)
    while True:
        p = path_for(d)
        if os.path.exists(p) and open_items(load(p)):
            return p
        parent = os.path.dirname(d)
        if parent == d or d == HOME:
            break
        d = parent
    return path_for(cwd)


def load(p):
    try:
        with open(p) as f:
            d = json.load(f)
    except Exception:
        return {"next": 1, "items": []}
    d.setdefault("items", [])
    return d


def save(p, d):
    os.makedirs(DIR, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, p)


def open_items(d):
    return [i for i in d["items"] if i["state"] != "done"]


def display_items(d):
    """What a board shows: the open queue plus the task just finished, still marked ●.

    Seeing the item you just closed is the confirmation that it closed; keeping it after work has
    moved on would be clutter, so `close_finished` drops it as soon as the next task starts or
    finishes."""
    return sorted(d["items"], key=lambda i: i["id"])


def close_finished(d, keep=None):
    """Forget every finished item except `keep` -- work has moved on, so the ● has served its turn."""
    d["items"] = [i for i in d["items"] if i["state"] != "done" or i["id"] == keep]


def set_state(d, mode, n):
    """Move item `n` to `mode` ("doing", "done" or "drop"). False if there is no such item.

    The board TUI drives the queue through this too, so a click and a `done N` cannot drift apart."""
    if not any(i["id"] == n for i in d["items"]):
        return False
    for i in d["items"]:
        if mode == "doing" and i["state"] == "doing" and i["id"] != n:
            i["state"] = "pending"
        if i["id"] != n:
            continue
        if mode == "drop":
            d["items"] = [x for x in d["items"] if x["id"] != n]
        elif mode == "done":
            # Kept, marked ●, until the next task starts or finishes: that is the receipt for the
            # one just closed. `log` still carries the text for the run summary.
            d.setdefault("log", []).append({"text": i["text"], "at": int(time.time())})
            i["state"] = "done"
            close_finished(d, keep=n)
        else:
            i["state"] = "doing"
            close_finished(d)
    return True


def blocked_ids(d):
    """Ids that must wait: everything a backgrounded task was declared to block."""
    out = set()
    for i in open_items(d):
        if i["state"] == "bg":
            out.update(i.get("blocks") or [])
    return out


def free_items(d):
    """Open tasks the console can actually start now: not already backgrounded, not blocked by one."""
    stuck = blocked_ids(d)
    return [i for i in open_items(d) if i["state"] != "bg" and i["id"] not in stuck]


AI_SYSTEM = ("You split a message into the tasks it asks for. You never answer the message, never "
             "ask questions, never use tools. You output task lines and nothing else.")
AI_PROMPT = (
    "Split the user message below into the separate tasks it asks for. One line per task, "
    "imperative, keeping the exact words and language of the message, no translation, no "
    "numbering, no commentary. Emit exactly one line "
    "if it is a single ask -- most messages are. Never invent work the message does not ask for.\n"
    "---\n%s\n---"
)


def spawn_aisplit(p, item_id, text):
    """Detached second opinion on how many asks the message carried.

    The regex splitter only sees numbering and bullets; a message like "fix X and then also deploy Y"
    is two asks in one sentence. Running it inline would block the prompt, so it runs after the fact
    and rewrites the item -- the board picks the change up on the next statusline render.
    """
    if len(text.split()) < 8:
        return
    try:
        import subprocess
        subprocess.Popen([sys.executable, __file__, "aisplit", p, str(item_id)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


def cmd_aisplit(p, item_id):
    import subprocess
    d = load(p)
    item = next((i for i in d["items"] if i["id"] == item_id), None)
    if not item or item["state"] != "pending":
        return
    try:
        # Isolated: no project CLAUDE.md, no settings, no MCP, no hooks -- with the session's own
        # context loaded the model answers the message instead of splitting it.
        out = subprocess.run(
            ["claude", "-p", "--model", "haiku", "--setting-sources", "", "--strict-mcp-config",
             "--exclude-dynamic-system-prompt-sections", "--system-prompt", AI_SYSTEM,
             AI_PROMPT % item["text"]],
            capture_output=True, text=True, timeout=90, cwd=DIR).stdout
    except Exception:
        return
    parts = [" ".join(x.split()) for x in out.splitlines()]
    parts = [x.lstrip("-*0123456789.) ").strip() for x in parts if len(x.split()) >= MIN_WORDS]
    if len(parts) < 2 or len(parts) > 9:
        return
    # Re-read: the queue may have moved on while the model was thinking.
    d = load(p)
    idx = next((k for k, i in enumerate(d["items"]) if i["id"] == item_id
                and i["state"] == "pending"), None)
    if idx is None:
        return
    new = []
    for text in parts:
        new.append({"id": d["next"], "text": text[:300], "state": "pending",
                    "at": int(time.time()), "auto": True})
        d["next"] += 1
    d["items"][idx:idx + 1] = new
    save(p, d)


def sig(d):
    """What the queue looks like right now. Same signature twice in a row across a stop means the
    run made no progress, so pushing it again would only spin."""
    return "%s|%d" % (",".join(str(i["id"]) for i in open_items(d)), len(d.get("log", [])))


def cmd_stop():
    try:
        hook = json.load(sys.stdin)
    except Exception:
        hook = {}
    session = (hook.get("session_id") or "")[:8] or sid()
    p = path_for(cwd_of(hook), session)
    d = load(p)
    items = open_items(d)
    if items:
        # Silenced per user request: never block/nag on stop. Keep bookkeeping (guard + summary
        # flag) so the empty-queue run-summary still works, but emit nothing to the console.
        d["guard"] = sig(d)
        d["summary"] = True
        save(p, d)
        return
    if d.get("summary"):
        done = d.get("log", [])
        d["summary"] = False
        d["log"] = []
        d["guard"] = ""
        save(p, d)
        # Silenced per user request: no stop-hook block here either.


def label(i):
    """Row text, with a +N when later asks were folded into this task."""
    n = len(i.get("more") or [])
    return i["text"] + (" (+%d)" % n if n else "")


def render(d):
    out = []
    for i in display_items(d):
        mark = {"doing": "▶ ", "bg": "⏳ ", "done": "● "}.get(
            i["state"], "⊙ " if i.get("auto") else "○ ")
        out.append("%s%d. %s" % (mark, i["id"], label(i)))
    return "\n".join(out)


def cwd_of(hook):
    # The project root, not the shell's cwd: one `cd` into a subdir must not open a second queue.
    return (os.environ.get("CLAUDE_PROJECT_DIR") or hook.get("cwd") or os.getcwd())


PRUNE_AGE = 7 * 24 * 3600


def prune(keep=()):
    """Drop finished session queues and pointers to files that are gone.

    One queue and one pointer are created per ~ session, so without this the directory grows
    forever (it reached 347 files before this was added). Only empty, cold queues are touched.
    """
    now = time.time()
    for f in glob.glob(os.path.join(DIR, "*.json")):
        try:
            if f in keep or now - os.path.getmtime(f) < PRUNE_AGE or open_items(load(f)):
                continue
            os.remove(f)
        except OSError:
            pass
    for f in glob.glob(LAST + "-*"):
        try:
            t = open(f).read().strip()
            if t not in keep and not os.path.exists(t):
                os.remove(f)
        except OSError:
            pass


def cmd_submit():
    try:
        hook = json.load(sys.stdin)
    except Exception:
        hook = {}
    prompt = (hook.get("prompt") or "").strip()
    cwd = cwd_of(hook)
    session = (hook.get("session_id") or "")[:8] or sid()
    p = path_for(cwd, session)
    # Leave a pointer for CLI calls: a Bash `done N` gets no CLAUDE_PROJECT_DIR and no dependable
    # cwd, so the queue of the most recent prompt is the only reliable answer.
    for f in (last_pointer(), LAST):
        try:
            open(f, "w").write(p)
        except OSError:
            pass
    d = load(p)
    if ACCEPT.match(prompt or ""):
        it, action = next_step(d)
        print("<pending-tasks>")
        if it:
            print("`.` means: do the next step already on the board, which is task %d -- %s"
                  % (it["id"], action))
            print("Do exactly that now. Nothing else changed.")
        else:
            print("`.` was typed but the queue is empty -- ask what to do next.")
        print("</pending-tasks>")
        return
    queued, merged = [], []
    if prompt and not SKIP.match(prompt):
        for text in split_tasks(prompt):
            text = text[:300]
            if any(i["text"] == text for i in open_items(d)):
                continue
            into = related(d, text)
            if into is not None:
                into.setdefault("more", []).append(text)
                merged.append((into, text))
                continue
            queued.append({"id": d["next"], "text": text, "state": "pending",
                           "at": int(time.time())})
            d["items"].append(queued[-1])
            d["next"] += 1
        if queued or merged:
            save(p, d)
            # Only a genuinely new task gets the second-opinion splitter: re-splitting an ask that
            # was folded into an existing task would undo the merge.
            if len(queued) == 1 and not merged:
                spawn_aisplit(p, queued[0]["id"], queued[0]["text"])
    prune(keep=(p,))
    items = open_items(d)
    if not items:
        return
    print("<pending-tasks>")
    if len(queued) > 1:
        print("That message carried %d asks; queued as tasks %s. Do them one at a time and finish "
              "the task in progress first -- do not abandon it."
              % (len(queued), ", ".join(str(q["id"]) for q in queued)))
    elif queued:
        print("Queued as task %d. Finish the task in progress first -- do not abandon it." % queued[0]["id"])
    for into, text in merged:
        print("That ask is part of task %d (%s), not a new one -- it was folded in there: %s"
              % (into["id"], into["text"], text))
    if merged and not queued:
        print("Nothing new was queued. Handle it inside that task.")
    if prompt and PREEMPT.search(prompt):
        print("The user explicitly said to drop everything: switch to the newest task NOW, and keep "
              "the rest of this queue.")
    bg = [i for i in items if i["state"] == "bg"]
    if bg:
        free = [i for i in free_items(d) if i["state"] != "doing"]
        print("Task%s %s %s in the background and %s hold the console."
              % ("" if len(bg) == 1 else "s", ", ".join(str(i["id"]) for i in bg),
                 "runs" if len(bg) == 1 else "run", "does not" if len(bg) == 1 else "do not"))
        if free:
            print("Carry on with task %d meanwhile; only wait if it actually needs that result."
                  % free[0]["id"])
    print(render(d))
    print("When a task hands off to something detached (a build, a long remote job) and the console "
          "is free again, run `bg <n>` and start the next independent task instead of idling. Add "
          "`--blocks <ids>` for the tasks that do need its result.")
    print("Mark progress with: python3 %s doing|bg|done|drop <n>   (queue file %s)" % (__file__, p))
    print("</pending-tasks>")


def _items():
    return open_items(load(queue_path()))


def cut(t, n):
    return t[:n] + "…" if len(t) > n else t


def next_step(d=None):
    """(item, action) -- the step the console should offer, or (None, None).

    The item being worked on owns the suggestion; with nothing in progress it is the top of the
    queue. The action is whatever was recorded with `step`, falling back to the item's own text,
    because an item with no step still has an obvious next move: start it.
    """
    d = d if d is not None else load(queue_path())
    # A task running in the background does not hold the console, so it is never the thing to
    # suggest -- the point of backgrounding it is to get on with something else.
    items = free_items(d)
    if not items:
        return None, None
    doing = [i for i in items if i["state"] == "doing"]
    it = (doing or items)[0]
    return it, (it.get("step") or it["text"])


def cmd_next():
    it, action = next_step()
    if not it:
        return
    print(action)


def cmd_line():
    items = _items()
    if not items:
        return
    doing = [i for i in items if i["state"] == "doing"]
    print("todo:%d %s" % (len(items), cut((doing or items)[0]["text"], 76)))


def offset_path():
    """The board's scroll position lives beside the queue, not inside it: every other command
    rewrites the queue file, and a view offset has no business racing real task state."""
    return queue_path() + ".offset"


def read_offset():
    try:
        return int(open(offset_path()).read().strip())
    except Exception:
        return 0


def write_offset(n):
    try:
        with open(offset_path(), "w") as f:
            f.write(str(n))
    except Exception:
        pass


def cmd_scroll(arg, rows=5):
    """Move the board's window. The statusline is rendered, never focused, so it cannot take a
    keypress -- scrolling it means moving a stored offset and letting the next render show the
    new window."""
    items = display_items(load(queue_path()))
    top = max(0, len(items) - rows)
    cur = read_offset()
    a = (arg or "").strip()
    if a in ("top", "home"):
        off = 0
    elif a in ("end", "bottom"):
        off = top
    elif a.startswith(("+", "-")):
        off = cur + int(a)
    else:
        off = int(a)
    write_offset(max(0, min(off, top)))
    cmd_board()


def cmd_board(width=176, rows=5):
    """The queue itself, one task per row, under the statusline -- the user asked to see the whole
    list on screen at all times, not just a count."""
    items = display_items(load(queue_path()))
    if not items:
        return
    # Clamp on render, not on write: tasks finish between renders, and a stale offset left over
    # from a longer queue would otherwise scroll the board off into nothing.
    off = max(0, min(read_offset(), max(0, len(items) - rows)))
    if off != read_offset():
        write_offset(off)
    it, action = next_step()
    # The suggestion marks the row it belongs to instead of being repeated underneath: printing the
    # same text twice, once plain and once with "> ", made the board look like two open tasks.
    same = it is not None and action == it["text"]
    if off:
        print("   ↑%d more" % off)
    for i in items[off:off + rows]:
        if it is not None and i["id"] == it["id"] and same:
            mark = ">"
        else:
            mark = {"doing": "▶", "bg": "⏳", "done": "●"}.get(
                i["state"], "⊙" if i.get("auto") else "○")
        row = "%s %d. %s" % (mark, i["id"], cut(label(i), width))
        print(row + "   [ . ]" if mark == ">" else row)
    if len(items) > off + rows:
        print("   ↓%d more   [ !q down ]" % (len(items) - off - rows))
    # A recorded `step` is a different sentence from the task title -- that one is worth its own
    # line, because it says what to actually do next rather than restating the task.
    if it is not None and not same:
        print("> %s   [ . ]" % cut(action, width - 12))


def main():
    args = sys.argv[1:]
    if not args:
        args = ["list"]
    mode = args[0]
    if mode == "submit":
        return cmd_submit()
    if mode == "line":
        return cmd_line()
    if mode == "board":
        return cmd_board()
    if mode == "scroll":
        return cmd_scroll(args[1] if args[1:] else "+1")
    if mode == "down":
        return cmd_scroll("+" + (args[1] if args[1:] else "1"))
    if mode == "up":
        return cmd_scroll("-" + (args[1] if args[1:] else "1"))
    if mode == "next":
        return cmd_next()
    if mode == "stop":
        return cmd_stop()
    if mode == "aisplit" and len(args) == 3:
        return cmd_aisplit(args[1], int(args[2]))
    p = queue_path()
    d = load(p)
    if mode == "step" and len(args) >= 3:
        n = int(args[1])
        for i in d["items"]:
            if i["id"] == n:
                i["step"] = " ".join(args[2:])[:300]
                break
        else:
            sys.exit("no item %d in %s" % (n, p))
    elif mode == "add" and args[1:]:
        d["items"].append({"id": d["next"], "text": " ".join(args[1:])[:300], "state": "pending",
                           "at": int(time.time())})
        d["next"] += 1
    elif mode in ("doing", "done", "drop"):
        if args[1:]:
            n = int(args[1])
        else:
            # No id: the item being worked on is the only one it can mean.
            cur = [i for i in d["items"] if i["state"] == "doing"]
            if mode == "doing" or not cur:
                sys.exit("%s needs an item id (%s)" % (mode, p))
            n = cur[0]["id"]
        if not set_state(d, mode, n):
            sys.exit("no item %d in %s" % (n, p))
    elif mode == "bg":
        # A task that is now waiting on something detached (a build, a long run, a remote job).
        # It stays open and owned, but it no longer occupies the console, so the queue hands the
        # next independent task over immediately instead of idling until the background job lands.
        if not args[1:]:
            sys.exit("bg needs an item id (%s)" % p)
        n = int(args[1])
        blocks = []
        if "--blocks" in args:
            blocks = [int(x) for x in args[args.index("--blocks") + 1].replace(",", " ").split()]
        for i in d["items"]:
            if i["id"] == n:
                i["state"] = "bg"
                if blocks:
                    i["blocks"] = blocks
                break
        else:
            sys.exit("no item %d in %s" % (n, p))
        save(p, d)
        nxt = [i for i in free_items(d) if i["state"] != "doing"]
        print("<pending-tasks>")
        print("Task %d now runs in the background; the console is free." % n)
        if nxt:
            print("Start task %d next -- %s" % (nxt[0]["id"], nxt[0].get("step") or nxt[0]["text"]))
            print("Do not wait for task %d. Skip any task that needs its result; mark those with "
                  "`bg %d --blocks <ids>` so the board stops offering them."
                  % (n, n))
        elif blocked_ids(d):
            print("Everything else on the board depends on it -- wait for it, nothing to start.")
        else:
            print("Nothing else is queued -- wait for it or ask what is next.")
        print(render(d) or "(queue empty)")
        print("</pending-tasks>")
        return
    elif mode == "clear":
        d["items"] = []
    elif mode != "list":
        sys.exit(__doc__)
    save(p, d)
    print(render(d) or "(queue empty)")


if __name__ == "__main__":
    main()
