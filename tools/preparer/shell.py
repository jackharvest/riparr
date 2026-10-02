"""The Riparr Preparer window, on any operating system.

pywebview (BSD-3-Clause) wraps the platform's own web view -- WKWebView on macOS,
WebView2 on Windows, WebKitGTK on Linux -- so `ui/` is hosted by the same engine the
user's browser already uses and moves across untouched. That was the whole reason for
choosing it: the interface is the part there is most of, and it is the part that did not
need porting.

WHAT THIS REPLACES

`app.py` is 800 lines of PyObjC building an NSWindow by hand. Everything it did that
still matters is here in a fraction of the space:

    NSWindow + WKWebView + WKUserContentController   -> create_window(js_api=...)
    the JS<->Python message handler and its promise   -> pywebview's own bridge
    applicationShouldTerminate: quit guard            -> events.closing
    DragStrip and the drawn titlebar                  -> not needed: this window has a
                                                         real one, on all three systems
    takeSnapshotWithConfiguration: for --shot         -> evaluate_js + the same JS

The bridge shape is kept deliberately: pywebview exposes methods at
`window.pywebview.api.<name>`, and a few injected lines alias that to `window.riparr`,
so not one line of `ui/app.js` changes.
"""
import json
import os
import sys
import threading

# ── the elevated card writer is this same binary, re-invoked ──
#
# A packaged app is not a Python interpreter. `sys.executable` is the application itself,
# so `[sys.executable, "writer.py", ...]` handed the GUI its own argument parser and died
# with "unrecognized arguments" -- which the interface reported, misleadingly, as "Could
# not get permission to write the card". writer.py was not collected into the bundle to
# be run either way. Card writing therefore never worked from the packaged app on any
# platform, only from a checkout, where `sys.executable` really is python3.
#
# This is checked above `import webview` on purpose: the privileged child runs as root
# and has no business loading a web view or reaching for the window server to write a
# card.
WRITE_FLAG = "--write-card"
if len(sys.argv) > 1 and sys.argv[1] == WRITE_FLAG:
    import writer
    sys.exit(writer.main(sys.argv[2:]))

# The same trick for one Windows question: a saved Wi-Fi key is only handed to an
# administrator. `--wifi-key <ssid> <out-file>` runs elevated, asks, and writes the answer
# to a file the unelevated app reads and deletes -- UAC is Windows' keychain dialog.
WIFI_KEY_FLAG = "--wifi-key"
if len(sys.argv) > 3 and sys.argv[1] == WIFI_KEY_FLAG:
    import hostos
    _pw, _ = hostos.saved_network_password(sys.argv[2])
    with open(sys.argv[3], "w", encoding="utf-8") as _f:
        _f.write(_pw or "")
    sys.exit(0 if _pw else 1)

# DPI awareness, declared before anything makes a window.
#
# Without it Windows treats the app as a 96-DPI program and bitmap-stretches it, so the
# text is soft -- and pywebview, which sizes its window in physical pixels on the
# assumption that the process *is* aware, gets stretched a second time on top. At 150%
# the window opened 1.5x too big in every direction, Continue below the taskbar.
# Per-monitor (2) where shcore exists, the system-wide call before that.
if sys.platform == "win32":
    try:
        import ctypes as _ct
        try:
            _ct.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            _ct.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Linux: the system's GTK and WebKit, not copies of the build machine's.
#
# The bundle carries PyGObject and nothing below it -- release.yml strips every library
# PyInstaller copied from the runner's OS. GTK built on one Ubuntu loaded beside WebKit
# from another fails on missing symbols, and WebKit cannot be carried at all: it starts
# its helper processes from paths compiled into the system copy. Every desktop that can
# run this has WebKitGTK already. PyInstaller's runtime hooks have pointed all of these
# at the bundle by now, where nothing is left to find; unset, GTK searches the system.
if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
    for _v in ("GI_TYPELIB_PATH", "GDK_PIXBUF_MODULE_FILE", "GIO_MODULE_DIR",
               "GTK_DATA_PREFIX", "GTK_EXE_PREFIX", "GTK_PATH", "PANGO_LIBDIR",
               "PANGO_SYSCONFDIR"):
        os.environ.pop(_v, None)
    _xdg = [p for p in os.environ.get("XDG_DATA_DIRS", "").split(os.pathsep)
            if p and not p.startswith(getattr(sys, "_MEIPASS", "\0"))]
    os.environ["XDG_DATA_DIRS"] = os.pathsep.join(_xdg or ["/usr/local/share", "/usr/share"])

import webview

import bridge as _bridge
import core
import hostos

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")

# Tall enough for the longest screen (Review) at its default size, so nobody has to
# resize the window to reach the buttons.
WIDTH, HEIGHT = 940, 740
MIN_SIZE = (860, 660)


_ORIGIN = None          # where _fit wants the window, when it knows; else the OS decides


def _fit(size, minimum):
    """The window size, shrunk to fit the screen it opens on.

    940x740 is fine on a laptop at 100%. A 1366x768 panel, or 1080p at 150% scaling --
    common Windows defaults -- has less room than that, and the window opened with its
    footer, and the Continue button in it, below the taskbar.
    """
    try:
        if sys.platform == "win32":
            # The work area -- the screen minus the taskbar -- in physical pixels, since
            # the process is DPI-aware by now, turned back into the logical pixels
            # pywebview takes. webview.screens is not available until the GUI has
            # started, so the first version of this fell back to 940x740 every time.
            import ctypes
            from ctypes import wintypes
            r = wintypes.RECT()
            if not ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0):
                return size, minimum
            try:
                scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
            except Exception:
                scale = 1.0
            w, h = (r.right - r.left) / scale, (r.bottom - r.top) / scale
            fit = (min(size[0], int(w * 0.96)), min(size[1], int(h * 0.94)))
            # Centred in the work area. Left to Windows, the window lands at the cascade
            # offset -- fine for its size, but far enough down that the footer, and the
            # Continue button in it, sat under the taskbar.
            global _ORIGIN
            _ORIGIN = (int(r.left / scale + (w - fit[0]) / 2),
                       int(r.top / scale + (h - fit[1]) / 2))
        else:
            scr = webview.screens() if callable(webview.screens) else webview.screens
            w, h = scr[0].width, scr[0].height
            fit = (min(size[0], int(w * 0.94)), min(size[1], int(h * 0.88)))
    except Exception:
        return size, minimum
    return fit, (min(minimum[0], fit[0]), min(minimum[1], fit[1]))


class Shell:
    """Owns the window, and the one decision pywebview cannot make for us."""

    def __init__(self, assets):
        self.bridge = _bridge.Bridge(assets)
        self.window = None

    # ── the quit guard ──
    # Closing the window used to quit instantly with a root `dd` in flight on the card.
    # That leaves a half-written card that boots into nothing, and the person who did it
    # has no idea that is what they did -- they closed a window. Every other application
    # that can lose your work asks first, and this one has more to lose than most.
    def on_closing(self):
        busy = self.bridge.busy_reason()
        if not busy:
            return True
        keep = self.window.create_confirmation_dialog(busy["title"], busy["body"])
        # The dialog answers "OK to proceed with the close"; invert it, because the
        # safe default here is to keep going rather than to quit.
        return bool(keep)


def _expose(window, shell):
    """Publish every public Bridge method under window.pywebview.api.

    Enumerated rather than listed by hand, for the same reason app.py's handler looked
    methods up by name: a bridge method that exists but was never wired is a button
    that silently does nothing, which this project has already shipped once.
    """
    names = []
    for name in dir(shell.bridge):
        if name.startswith("_"):
            continue
        fn = getattr(shell.bridge, name)
        if callable(fn):
            window.expose(fn)
            names.append(name)
    return names


def build(assets, shot="", evaluate=""):
    shell = Shell(assets)
    index = os.path.join(UI, "index.html")

    (width, height), min_size = _fit((WIDTH, HEIGHT), MIN_SIZE)
    window = webview.create_window(
        "Riparr Preparer",
        url=index,
        js_api=shell.bridge,
        width=width, height=height,
        x=_ORIGIN[0] if _ORIGIN else None, y=_ORIGIN[1] if _ORIGIN else None,
        min_size=min_size,
        background_color="#1c1c1e",
        # The interface draws its own selection rules; letting the platform add text
        # selection on top makes a native window feel like a page again.
        text_select=False,
        confirm_close=False,          # handled by on_closing, which knows what is busy
    )
    shell.window = window
    # Closing the window is how the app quits, so a self-update quits through the same
    # door rather than calling os._exit and skipping every teardown the shell does.
    shell.bridge.on_quit = window.destroy
    window.events.closing += shell.on_closing

    # The `window.riparr` alias is ui/bridge-shim.js, loaded by the page ahead of
    # app.js. It cannot be injected from here: app.js calls init() the moment it is
    # parsed, `loaded` fires after that, and `before_load` fires before the window can
    # run script at all ("Main window failed to start").
    def loaded():
        if shot or evaluate:
            _shoot(window, shot, evaluate)

    window.events.loaded += loaded
    return shell, window


def _shoot(window, screen, evaluate):
    """Paint one screen and report, from the same fixtures app.py uses.

    Importing `app` for these was the obvious shortcut and it does not work: app.py
    defines an Objective-C `AppDelegate`, and pywebview defines one too, so the import
    dies with "AppDelegate is overriding existing Objective-C class". They live in
    shots.py now, which is where UI fixtures belonged anyway.
    """
    import shots
    import time
    # `loaded` fires before init()'s first bridge call has resolved, and before the
    # fixtures have painted. app.py's Shot waits between the two for the same reason;
    # without it a shot catches a half-drawn screen and an --eval reads a null state.
    time.sleep(1.2)
    window.run_js(shots.script_for(screen))
    time.sleep(0.8)
    if evaluate:
        print(window.evaluate_js(evaluate))
    window.destroy()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Riparr Preparer")
    ap.add_argument("--assets", default=os.path.expanduser("~/riparr-build"))
    ap.add_argument("--shot", default="")
    ap.add_argument("--eval", default="")
    ap.add_argument("--debug", action="store_true",
                    help="open the web inspector")
    a = ap.parse_args(argv)

    assets = os.path.abspath(os.path.expanduser(a.assets))
    os.makedirs(assets, exist_ok=True)

    shell, window = build(assets, a.shot, a.eval)
    webview.start(debug=a.debug, private_mode=True)
    # The scratch directory holds custom.toml -- the derived Wi-Fi key among it. Only the
    # superseded app.py ever removed it, so since the move to pywebview every session
    # left one behind in the temp folder.
    import shutil
    shutil.rmtree(_bridge.RUNDIR, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
