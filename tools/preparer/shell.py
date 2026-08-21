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

import webview

import bridge as _bridge
import core
import hostos

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")

WIDTH, HEIGHT = 940, 680
MIN_SIZE = (860, 600)


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

    window = webview.create_window(
        "Riparr Preparer",
        url=index,
        js_api=shell.bridge,
        width=WIDTH, height=HEIGHT,
        min_size=MIN_SIZE,
        background_color="#1c1c1e",
        # The interface draws its own selection rules; letting the platform add text
        # selection on top makes a native window feel like a page again.
        text_select=False,
        confirm_close=False,          # handled by on_closing, which knows what is busy
    )
    shell.window = window
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
