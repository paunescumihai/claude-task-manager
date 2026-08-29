#!/usr/bin/env python3
"""Interactive board for the pending-task queue.

The Claude Code statusline is a string re-rendered per turn: no mouse, no scroll. So the clickable
board is its own process -- keep it open in a second pane, pointed at the same queue file. It polls
the file, so anything the hook or another console does shows up here within a second, and anything
done here is picked up by the next prompt.

    python3 tasks_tui.py [--queue PATH | --project DIR]

    arrows / j k / wheel   move           PgUp PgDn Home End   scroll
    click a row            open its menu  Enter                open the menu
    Enter on the menu / click an entry    run it
    d  drop     x  done     b  background     space  do now     q  quit
"""
import argparse
import curses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pending_tasks as pt  # noqa: E402

MARK = {"doing": "▶", "bg": "⏳", "done": "●"}
# label, key, what it does to the item -- "do now" is the only one that is not a state change:
# it makes the item the console's next step, which is what `.` in Claude Code accepts.
ACTIONS = [("do now", " ", "now"), ("background", "b", "bg"), ("done", "x", "done"),
           ("cancel (drop)", "d", "drop")]


def put(scr, y, x, text, attr=curses.A_NORMAL):
    """addnstr, minus the ERR curses returns for the very last cell of the screen."""
    h, w = scr.getmaxyx()
    if 0 <= y < h and x < w:
        try:
            scr.addnstr(y, x, text, w - x - (1 if y == h - 1 else 0), attr)
        except curses.error:
            pass


def resolve(args):
    if args.queue:
        return args.queue
    if args.project:
        return pt.path_for(os.path.abspath(args.project))
    return pt.queue_path()


class Board:
    def __init__(self, path):
        self.path = path
        self.sel = 0
        self.top = 0
        self.menu = None          # index of the row whose menu is open
        self.menu_sel = 0
        self.msg = ""
        self.items = []
        self.stamp = 0

    # --- data ---------------------------------------------------------------
    def reload(self, force=False):
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            m = 0
        if not force and m == self.stamp:
            return
        self.stamp = m
        d = pt.load(self.path)
        self.items = pt.display_items(d)
        self.sel = max(0, min(self.sel, len(self.items) - 1))
        if self.menu is not None and self.menu >= len(self.items):
            self.menu = None

    def apply(self, action):
        if not self.items:
            return
        d = pt.load(self.path)
        item = self.items[self.sel]
        n = item["id"]
        if action == "bg":
            for i in d["items"]:
                if i["id"] == n:
                    i["state"] = "bg"
            self.msg = "%d moved to the background" % n
        else:
            # "now" is `doing`: the console's next step, which is what a bare `.` accepts.
            pt.set_state(d, {"now": "doing"}.get(action, action), n)
            self.msg = {"drop": "dropped %d", "done": "done %d",
                        "now": "%d is next -- type `.` in Claude Code to start it"}[action] % n
        pt.save(self.path, d)
        self.menu = None
        self.reload(force=True)

    # --- drawing ------------------------------------------------------------
    def draw(self, scr):
        scr.erase()
        h, w = scr.getmaxyx()
        head = " claude tasks  ·  %s  ·  %d open" % (os.path.basename(self.path), len(self.items))
        put(scr, 0, 0, head.ljust(w), curses.A_REVERSE)

        body_h = max(1, h - 3)
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + body_h:
            self.top = self.sel - body_h + 1
        self.top = max(0, min(self.top, max(0, len(self.items) - body_h)))

        self.rows = {}
        if not self.items:
            put(scr, 2, 2, "queue empty", curses.A_DIM)
        for n, item in enumerate(self.items[self.top:self.top + body_h]):
            y = n + 1
            idx = self.top + n
            self.rows[y] = idx
            mark = MARK.get(item["state"], "⊙" if item.get("auto") else "○")
            attr = curses.A_REVERSE if idx == self.sel else curses.A_NORMAL
            if item["state"] == "doing":
                attr |= curses.A_BOLD
            elif item["state"] == "bg":
                attr |= curses.A_DIM
            put(scr, y, 0, ("%s %3d. %s" % (mark, item["id"], item["text"])).ljust(w), attr)

        if len(self.items) > body_h:
            put(scr, h - 2, 0, " %d-%d of %d " % (self.top + 1,
                min(self.top + body_h, len(self.items)), len(self.items)), curses.A_DIM)
        foot = self.msg or "enter menu · space do now · b background · x done · d drop · q quit"
        put(scr, h - 1, 0, foot.ljust(w), curses.A_DIM)

        if self.menu is not None:
            self.draw_menu(scr, h, w)
        scr.refresh()

    def draw_menu(self, scr, h, w):
        top = min(self.menu - self.top + 2, h - len(ACTIONS) - 2)
        top = max(1, top)
        width = max(len(a[0]) for a in ACTIONS) + 8
        left = min(6, max(0, w - width - 1))
        self.menu_rows = {}
        for n, (label, key, _) in enumerate(ACTIONS):
            y = top + n
            if y >= h - 1:
                break
            self.menu_rows[y] = n
            attr = curses.A_REVERSE if n == self.menu_sel else curses.A_NORMAL
            put(scr, y, left, " %s  %s " % (key.strip() or "␣", label.ljust(width - 6)), attr)

    # --- input --------------------------------------------------------------
    def key(self, ch):
        if self.menu is not None:
            if ch in (curses.KEY_UP, ord("k")):
                self.menu_sel = (self.menu_sel - 1) % len(ACTIONS)
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.menu_sel = (self.menu_sel + 1) % len(ACTIONS)
            elif ch in (curses.KEY_ENTER, 10, 13):
                self.apply(ACTIONS[self.menu_sel][2])
            elif ch == 27:
                self.menu = None
            elif ch == ord("q"):
                return False      # q always quits: an open menu must not trap the board
            return True
        if ch in (curses.KEY_UP, ord("k")):
            self.sel = max(0, self.sel - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.sel = min(len(self.items) - 1, self.sel + 1)
        elif ch == curses.KEY_PPAGE:
            self.sel = max(0, self.sel - 10)
        elif ch == curses.KEY_NPAGE:
            self.sel = min(len(self.items) - 1, self.sel + 10)
        elif ch == curses.KEY_HOME:
            self.sel = 0
        elif ch == curses.KEY_END:
            self.sel = len(self.items) - 1
        elif ch in (curses.KEY_ENTER, 10, 13):
            if self.items:
                self.menu, self.menu_sel = self.sel, 0
        elif ch == ord("q"):
            return False
        else:
            for label, k, action in ACTIONS:
                if ch == ord(k):
                    self.apply(action)
        return True

    def click(self, y, button):
        if button & curses.BUTTON4_PRESSED:
            self.sel = max(0, self.sel - 3)
            return
        if button & getattr(curses, "BUTTON5_PRESSED", 0):
            self.sel = min(len(self.items) - 1, self.sel + 3)
            return
        if self.menu is not None:
            n = getattr(self, "menu_rows", {}).get(y)
            if n is None:
                self.menu = None
            else:
                self.menu_sel = n
                self.apply(ACTIONS[n][2])
            return
        idx = self.rows.get(y)
        if idx is not None:
            self.sel = idx
            self.menu, self.menu_sel = idx, 0


def run(scr, path):
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    try:
        curses.mouseinterval(0)
    except curses.error:
        pass
    scr.timeout(700)                      # doubles as the poll interval for outside changes
    b = Board(path)
    b.reload(force=True)
    while True:
        b.draw(scr)
        ch = scr.getch()
        if ch == -1:
            b.reload()
            continue
        b.msg = ""
        if ch == curses.KEY_MOUSE:
            try:
                _, _, y, _, button = curses.getmouse()
            except curses.error:
                continue
            b.click(y, button)
        elif ch == curses.KEY_RESIZE:
            continue
        elif not b.key(ch):
            return


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", help="queue file to open")
    ap.add_argument("--project", help="project directory whose queue to open")
    args = ap.parse_args()
    path = resolve(args)
    if not os.path.exists(path):
        sys.exit("no queue at %s" % path)
    curses.wrapper(run, path)


if __name__ == "__main__":
    main()
