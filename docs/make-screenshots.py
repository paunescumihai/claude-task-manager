#!/usr/bin/env python3
"""Regenerate the README screenshots from a mock queue.

Everything shown is invented here: no real project name, path, host, account or task text ever
reaches an image. The board, the TUI and the CLI output are captured from the real code, so the
pictures cannot drift from what the tool actually prints.

    python3 docs/make-screenshots.py

Needs tmux (to run the curses TUI headless) and google-chrome (to rasterise the HTML).
"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")
COLS = 104

TASKS = [
    "fix the failing checkout tests on CI",
    "add rate limiting to the public API",
    "write the release notes for 2.4",
    "bump the staging database to Postgres 16",
]

# 8/16-colour SGR to CSS. Only the codes the board and curses actually emit.
FG = {"30": "#3b4048", "31": "#e06c75", "32": "#98c379", "33": "#e5c07b", "34": "#61afef",
      "35": "#c678dd", "36": "#56b6c2", "37": "#c8ccd4", "39": "#c8ccd4",
      "90": "#5c6370", "91": "#e06c75", "92": "#98c379", "93": "#e5c07b", "94": "#61afef",
      "95": "#c678dd", "96": "#56b6c2", "97": "#ffffff"}
BG = {"40": "#1b1f27", "41": "#e06c75", "42": "#98c379", "43": "#e5c07b", "44": "#61afef",
      "45": "#c678dd", "46": "#56b6c2", "47": "#c8ccd4", "49": ""}


def ansi_to_html(text):
    """SGR-coloured terminal text to spans. Enough of the standard for what we render."""
    out, state = [], {"fg": "", "bg": "", "bold": False, "dim": False, "rev": False}
    open_span = False

    def flush_style():
        nonlocal open_span
        if open_span:
            out.append("</span>")
        fg = state["fg"] or "#c8ccd4"
        bg = state["bg"]
        if state["rev"]:
            fg, bg = bg or "#1b1f27", fg
        css = ["color:%s" % fg]
        if bg:
            css.append("background:%s" % bg)
        if state["bold"]:
            css.append("font-weight:700")
        if state["dim"]:
            css.append("opacity:.55")
        out.append('<span style="%s">' % ";".join(css))
        open_span = True

    flush_style()
    for chunk in re.split(r"(\033\[[0-9;]*m)", text):
        if not chunk:
            continue
        if chunk.startswith("\033["):
            codes = chunk[2:-1].split(";") or ["0"]
            i = 0
            while i < len(codes):
                c = codes[i] or "0"
                if c == "0":
                    state.update(fg="", bg="", bold=False, dim=False, rev=False)
                elif c == "1":
                    state["bold"] = True
                elif c == "2":
                    state["dim"] = True
                elif c == "7":
                    state["rev"] = True
                elif c in ("22", "27"):
                    state.update(bold=False, dim=False, rev=False)
                elif c in FG:
                    state["fg"] = FG[c]
                elif c in BG:
                    state["bg"] = BG[c]
                elif c == "38" and codes[i + 1:i + 2] == ["5"]:
                    state["fg"] = FG.get(codes[i + 2], "#c8ccd4")
                    i += 2
                elif c == "48" and codes[i + 1:i + 2] == ["5"]:
                    state["bg"] = BG.get(codes[i + 2], "#1b1f27")
                    i += 2
                i += 1
            flush_style()
        else:
            out.append(html.escape(chunk))
    out.append("</span>")
    return "".join(out)


PAGE = """<title>%(title)s</title>
<style>
  body { margin:0; background:transparent; font:15px/1.55 "JetBrains Mono","DejaVu Sans Mono",monospace; }
  .shot { display:inline-block; padding:20px 24px; background:#1b1f27; color:#c8ccd4;
          border-radius:10px; margin:18px; box-shadow:0 10px 30px #0007; }
  .bar { margin:-8px -10px 14px; padding:6px 10px 8px; border-bottom:1px solid #2a2f3a; }
  .dot { display:inline-block; width:11px; height:11px; border-radius:50%%; margin-right:6px; }
  pre { margin:0; white-space:pre; }
  .cap { color:#5c6370; font-size:13px; margin:14px 0 4px; }
</style>
<div class="shot"><div class="bar">
  <span class="dot" style="background:#e06c75"></span>
  <span class="dot" style="background:#e5c07b"></span>
  <span class="dot" style="background:#98c379"></span></div>
%(body)s</div>"""


def scrub(text):
    """No real path ever reaches an image: the mock $HOME and this checkout both go generic."""
    text = text.replace(HOOKS, "~/.claude/hooks").replace(TMP, "~")
    text = re.sub(r"tmp-ctm-shots-[a-z0-9]+-", "", text)   # the mock queue's slug, not a real one
    return "\n".join(textwrap.fill(l, COLS, subsequent_indent="  ",
                                   drop_whitespace=False, break_long_words=False) or l
                     for l in text.splitlines())


def shot(name, blocks, title):
    """blocks: list of (caption or None, ansi text)."""
    body = []
    for cap, text in blocks:
        text = scrub(text)
        if cap:
            body.append('<div class="cap">%s</div>' % html.escape(cap))
        body.append("<pre>%s</pre>" % ansi_to_html(text.rstrip("\n")))
    page = os.path.join(TMP, name + ".html")
    open(page, "w").write(PAGE % {"title": title, "body": "\n".join(body)})
    png = os.path.join(HERE, name + ".png")
    subprocess.run(["google-chrome", "--headless", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=2", "--window-size=%d,1400" % (COLS * 10 + 120),
                    "--screenshot=" + png, "--default-background-color=00000000", page],
                   capture_output=True, check=True)
    subprocess.run(["python3", "-c", """import sys
from PIL import Image
im = Image.open(sys.argv[1]).convert("RGBA")
im.crop(im.getbbox()).save(sys.argv[1])""", png], check=True)
    print("wrote", png)


def q(*argv, stdin="", session="demo0001"):
    env = dict(os.environ, HOME=TMP, CLAUDE_CODE_SESSION_ID=session, COLUMNS=str(COLS),
               PENDING_BOARD_ROWS="6")
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("NO_COLOR", None)
    return subprocess.run([sys.executable, os.path.join(HOOKS, "pending_tasks.py")] + list(argv),
                          input=stdin, capture_output=True, text=True, env=env).stdout


def prompt(text):
    """What a Claude Code UserPromptSubmit hook does with a typed prompt."""
    import json
    return q("submit", stdin=json.dumps({"prompt": text, "cwd": os.path.join(TMP, "work", "shop"),
                                         "session_id": "demo0001"}))


def build_queue():
    os.makedirs(os.path.join(TMP, ".claude", "pending"), exist_ok=True)
    os.makedirs(os.path.join(TMP, "work", "shop"), exist_ok=True)
    for t in TASKS:
        prompt(t)
    q("doing", "1")
    q("step", "1", "run the checkout suite with the fixed fixture")
    q("bg", "4")
    live = os.path.join(TMP, ".claude", "pending", sorted(
        f for f in os.listdir(os.path.join(TMP, ".claude", "pending")) if f.endswith(".json"))[0])
    # the TUI header prints the queue's file name -- give it the one a real project would have
    nice = os.path.join(TMP, ".claude", "pending", "shop.json")
    shutil.copy(live, nice)
    return nice


STATUS = ("\033[1m\033[36mdev\033[0m\033[2m·\033[0m\033[36myou\033[0m \033[2m│\033[0m "
          "\033[34m~/work/shop\033[0m \033[2m│\033[0m \033[1m\033[35mOpus 4.6\033[0m \033[2m│\033[0m "
          "\033[2m5h\033[0m \033[32m18%\033[0m \033[2m7d\033[0m \033[32m41%\033[0m \033[2m│\033[0m "
          "\033[2mctx\033[0m \033[33m62%\033[0m \033[2m│\033[0m \033[33mtodo:4 fix the failing "
          "checkout tests on CI\033[0m")


def tui_capture(queue):
    """Run the curses TUI headless in tmux and capture the pane with its colours."""
    sock = "ctm-shot"
    subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)
    subprocess.run(["tmux", "-L", sock, "new-session", "-d", "-x", str(COLS), "-y", "11",
                    "%s %s --queue %s" % (sys.executable, os.path.join(HOOKS, "tasks_tui.py"),
                                          queue)], check=True)
    time.sleep(1.2)
    subprocess.run(["tmux", "-L", sock, "send-keys", "Down"], check=True)
    subprocess.run(["tmux", "-L", sock, "send-keys", "Enter"], check=True)   # open the row menu
    time.sleep(0.8)
    pane = subprocess.run(["tmux", "-L", sock, "capture-pane", "-e", "-p"],
                          capture_output=True, text=True, check=True).stdout
    subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)
    return "\n".join(pane.rstrip("\n").split("\n"))


TMP = tempfile.mkdtemp(prefix="ctm-shots-")
try:
    queue = build_queue()
    shot("board", [(None, STATUS + "\n" + q("board"))], "board")
    shot("tui", [(None, tui_capture(queue))], "tui")

    merged = prompt("also make the rate limit per API key configurable")
    q("done", "1")
    q("done", "2")
    shot("cli", [("a prompt that belongs to a task already on the board",
                  "\033[2m$\033[0m \033[0mecho '{\"prompt\": \"also make the rate limit per API "
                  "key configurable\"}' | pending_tasks.py submit\n" + merged.strip()),
                 ("the queue afterwards", q("list")),
                 ("what actually got done", q("report", "7"))], "cli")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
