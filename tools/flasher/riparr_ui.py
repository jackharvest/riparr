"""Terminal UI primitives for the Riparr flasher. Stdlib only."""
import os, sys, termios, tty, shutil, time, threading

_NC = os.environ.get("NO_COLOR") is not None or not sys.stdout.isatty()
def _c(n): return "" if _NC else f"\033[{n}m"
RESET, BOLD, DIM, ITAL = _c(0), _c(1), _c(2), _c(3)
RED, GRN, YEL, BLU, MAG, CYN, GRY = (_c(31), _c(32), _c(33), _c(34), _c(35), _c(36), _c(90))
BGBLU, WHT = _c(44), _c(97)
HIDE, SHOW = "\033[?25l", "\033[?25h"

W = min(shutil.get_terminal_size((80, 24)).columns, 74)

def vislen(s):
    out, i = 0, 0
    while i < len(s):
        if s[i] == "\033":
            while i < len(s) and s[i] != "m": i += 1
            i += 1
        else:
            out += 1; i += 1
    return out

def pad(s, n): return s + " " * max(0, n - vislen(s))

def rule(ch="─"): return GRY + ch * W + RESET

def banner(title, sub=""):
    clear()
    print()
    print(f"  {BOLD}{CYN}◉ {title}{RESET}")
    if sub: print(f"  {GRY}{sub}{RESET}")
    print(f"  {rule()}")
    print()

def clear():
    if not _NC: sys.stdout.write("\033[2J\033[H")

def step(n, total, label):
    dots = "".join(f"{CYN}●{RESET}" if i < n else f"{GRY}○{RESET}" for i in range(total))
    print(f"  {dots}  {DIM}step {n} of {total}{RESET} · {BOLD}{label}{RESET}\n")

def info(msg):  print(f"  {BLU}ℹ{RESET}  {msg}")
def ok(msg):    print(f"  {GRN}✓{RESET}  {msg}")
def warn(msg):  print(f"  {YEL}▲{RESET}  {msg}")
def err(msg):   print(f"  {RED}✕{RESET}  {msg}")
def note(msg):  print(f"     {GRY}{msg}{RESET}")

def read_key():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            n = sys.stdin.read(2)
            return {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}.get(n, "ESC")
        if ch in ("\r", "\n"): return "ENTER"
        if ch == "\x03": raise KeyboardInterrupt
        if ch == "\x7f": return "BACKSPACE"
        if ch == "\t": return "TAB"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

def menu(items, render, title=None, footer=None, disabled=None):
    """items: list. render(item, selected)->str. Returns index or None on ESC."""
    disabled = disabled or (lambda i: False)
    idx = 0
    while disabled(items[idx]) and idx < len(items) - 1: idx += 1
    if not sys.stdin.isatty():
        for n, it in enumerate(items, 1): print(f"  {n:2}. {render(it, False)}")
        try:
            raw = input("\n  Number: ").strip()
        except EOFError:
            raise NoTTY("a menu selection")
        return int(raw) - 1 if raw.isdigit() else None
    first = True
    sys.stdout.write(HIDE)
    try:
        while True:
            if not first:
                sys.stdout.write(f"\033[{len(items) + (2 if title else 0) + (2 if footer else 0)}A")
            first = False
            if title: print(f"  {BOLD}{title}{RESET}\n")
            for n, it in enumerate(items):
                sel = n == idx
                line = render(it, sel)
                mark = f"{CYN}❯{RESET}" if sel else " "
                print(f"  {mark} {pad(line, W - 4)}")
            if footer: print(f"\n  {GRY}{footer}{RESET}")
            k = read_key()
            if k == "UP":
                j = idx
                while True:
                    j = (j - 1) % len(items)
                    if not disabled(items[j]) or j == idx: break
                idx = j
            elif k in ("DOWN", "TAB"):
                j = idx
                while True:
                    j = (j + 1) % len(items)
                    if not disabled(items[j]) or j == idx: break
                idx = j
            elif k == "ENTER":
                if not disabled(items[idx]):
                    sys.stdout.write(SHOW); return idx
            elif k == "ESC":
                sys.stdout.write(SHOW); return None
    finally:
        sys.stdout.write(SHOW)

def prompt(label, default="", validate=None, hint=""):
    while True:
        if not sys.stdin.isatty() and not default:
            raise NoTTY(f"input for \u201c{label}\u201d")
        d = f" {GRY}[{default}]{RESET}" if default else ""
        if hint: print(f"  {GRY}{hint}{RESET}")
        try:
            v = input(f"  {BOLD}{label}{RESET}{d}: ").strip() or default
        except EOFError:
            if not default:
                raise NoTTY(f"input for \u201c{label}\u201d")
            print(default)
            v = default
        if validate:
            problem = validate(v)
            if problem:
                err(problem)
                if not sys.stdin.isatty():
                    raise NoTTY(f"a valid value for \u201c{label}\u201d")
                continue
        return v

def password(label="Password"):
    """Hidden entry with a ctrl-R reveal toggle and live length feedback."""
    if not sys.stdin.isatty():
        raise NoTTY("a password")
    buf, reveal = [], False
    sys.stdout.write(f"  {BOLD}{label}{RESET}: ")
    sys.stdout.flush()
    while True:
        k = read_key()
        if k == "ENTER":
            print(); return "".join(buf)
        if k == "BACKSPACE":
            if buf: buf.pop()
        elif k == "\x12":  # ctrl-R
            reveal = not reveal
        elif k in ("UP", "DOWN", "LEFT", "RIGHT", "ESC", "TAB"):
            continue
        else:
            buf.append(k)
        shown = "".join(buf) if reveal else "•" * len(buf)
        tag = f"  {GRY}(ctrl-R {'hide' if reveal else 'show'}){RESET}"
        sys.stdout.write(f"\r\033[K  {BOLD}{label}{RESET}: {shown}{tag}")
        sys.stdout.flush()

def confirm(label, danger=False):
    if not sys.stdin.isatty(): raise NoTTY(f"confirmation for “{label}”")
    col = RED if danger else CYN
    idx = menu([True, False],
               lambda v, s: (f"{col}{BOLD}Yes{RESET}" if v else "No") if s else (f"{'Yes' if v else 'No'}"),
               title=label, footer="↑↓ move · enter select")
    return idx == 0

class NoTTY(Exception):
    """Raised when interactive input is needed but stdin is not a terminal."""

class Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    def __init__(self, msg): self.msg, self._stop = msg, threading.Event()
    def __enter__(self):
        def run():
            i = 0
            while not self._stop.is_set():
                sys.stdout.write(f"\r  {CYN}{self.FRAMES[i % len(self.FRAMES)]}{RESET}  {self.msg}")
                sys.stdout.flush(); i += 1; time.sleep(0.08)
        self.t = threading.Thread(target=run, daemon=True); self.t.start(); return self
    def __exit__(self, *a):
        self._stop.set(); self.t.join(timeout=0.3)
        sys.stdout.write("\r\033[K"); sys.stdout.flush()

def progress(done, total, label="", extra=""):
    frac = 0 if not total else min(1.0, done / total)
    barw = max(10, W - 34)
    filled = int(barw * frac)
    bar = f"{CYN}{'━' * filled}{GRY}{'━' * (barw - filled)}{RESET}"
    sys.stdout.write(f"\r  {label} {bar} {BOLD}{frac*100:5.1f}%{RESET} {GRY}{extra}{RESET}\033[K")
    sys.stdout.flush()

def signal_bars(rssi):
    if rssi is None: return f"{GRY}····{RESET}"
    lvl = 4 if rssi >= -55 else 3 if rssi >= -67 else 2 if rssi >= -75 else 1
    col = GRN if lvl >= 3 else YEL if lvl == 2 else RED
    return col + "▁▃▅▇"[:lvl] + RESET + GRY + "·" * (4 - lvl) + RESET
