#!/usr/bin/env python3
"""
Riparr Preparer — a native macOS window that prepares an SD card.

A real NSWindow hosting a WKWebView. Not a browser tab, not Electron: the whole
runtime is PyObjC plus the WebKit framework that is already on every Mac.

The card-writing logic lives in core.py and writer.py. This file is only the shell and
the bridge between JavaScript and Python.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import objc
from AppKit import (NSApplication, NSWindow, NSApp, NSScreen, NSColor, NSView,
                    NSAlert, NSTextField, NSAlertFirstButtonReturn,
                    NSBitmapImageRep, NSViewWidthSizable, NSViewMinYMargin,
                    NSViewHeightSizable,
                    NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
                    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
                    NSWindowStyleMaskFullSizeContentView, NSWindowTitleVisible,
                    NSMakeRect, NSMakeSize, NSWorkspace)
from Foundation import NSObject, NSURL, NSString, NSTimer
from WebKit import (WKWebView, WKWebViewConfiguration, WKUserContentController,
                    WKUserScript, WKPreferences, WKSnapshotConfiguration)

import bridge as _bridge
import core
import shots

# No VERSION here. This module is the superseded hand-built NSWindow (see shell.py),
# and the copy it used to keep said "1.0.0" against releases tagged v0.1.x -- the
# exact drift bridge.py warns about, sitting one import away from the update check.
# release.yml only compares bridge.py, so a second constant here is a landmine with
# no guard on it. bridge.VERSION is the one and only answer.
HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")

# Sensitive scratch: holds the generated custom.toml between the GUI and the root
# writer. 0700, and removed on exit.
RUNDIR = tempfile.mkdtemp(prefix="riparr-prep-")
os.chmod(RUNDIR, 0o700)


BOOTSTRAP_JS = """
window.__riparr_pending = {};
window.__riparr_seq = 0;
window.riparr = new Proxy({}, {
  get: (_, method) => (...args) => new Promise((resolve, reject) => {
    const id = ++window.__riparr_seq;
    window.__riparr_pending[id] = {resolve, reject};
    window.webkit.messageHandlers.riparr.postMessage(
      JSON.stringify({id, method, args}));
  })
});
window.__riparr_settle = (id, ok, payload) => {
  const p = window.__riparr_pending[id];
  if (!p) return;
  delete window.__riparr_pending[id];
  ok ? p.resolve(payload) : p.reject(new Error(payload));
};
"""


class Handler(NSObject):
    def initWithBridge_webview_(self, bridge, webview):
        self = objc.super(Handler, self).init()
        self.bridge = bridge
        self.webview = webview
        return self

    def userContentController_didReceiveScriptMessage_(self, ucc, message):
        try:
            req = json.loads(str(message.body()))
        except Exception:
            return
        threading.Thread(target=self._dispatch, args=(req,), daemon=True).start()

    @objc.python_method
    def _dispatch(self, req):
        rid, method, args = req.get("id"), req.get("method"), req.get("args", [])
        try:
            fn = getattr(self.bridge, method, None)
            if fn is None:
                raise AttributeError("no such method: %s" % method)
            result = fn(*args)
            payload, ok = json.dumps(result), True
        except Exception as e:
            traceback.print_exc()
            payload, ok = json.dumps("%s: %s" % (type(e).__name__, e)), False
        js = "window.__riparr_settle(%d, %s, %s);" % (rid, "true" if ok else "false", payload)
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "evalOnMain:", NSString.stringWithString_(js), False)

    def evalOnMain_(self, js):
        self.webview.evaluateJavaScript_completionHandler_(str(js), None)


class Shot(NSObject):
    """Render one screen to a PNG, then quit.

    Uses WKWebView's own snapshot rather than `screencapture -l`: a window that is
    occluded or behind another app has an empty backing store, so screencapture
    returns a blank image of a view that is rendering perfectly. This does not.
    """

    def initWithWebview_screen_out_eval_(self, webview, screen, out, evaluate):
        self = objc.super(Shot, self).init()
        self.webview = webview
        self.screen = screen
        self.out = out
        self.evaluate = evaluate      # if set, print this instead of snapshotting
        return self

    # A WKWebView in a window that was never brought to the front does not run CSS
    # animations. `.screen` starts at opacity 0 and relies on `animation: rise …
    # forwards` to become visible, so without this the pane snapshots blank while the
    # un-animated sidebar renders perfectly — which looks like a broken layout and is
    # not one. Killing animation also makes shots byte-stable between runs.
    def fire_(self, timer):
        js = shots.script_for(self.screen)
        self.webview.evaluateJavaScript_completionHandler_(js, None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.7, self, "snap:", None, False)

    def snap_(self, timer):
        if self.evaluate:
            self.webview.evaluateJavaScript_completionHandler_(
                self.evaluate, self._printed)
            return
        conf = WKSnapshotConfiguration.alloc().init()
        self.webview.takeSnapshotWithConfiguration_completionHandler_(
            conf, self._write)

    @objc.python_method
    def _printed(self, value, error):
        print(error if error is not None else value, flush=True)
        NSApp().terminate_(None)

    @objc.python_method
    def _write(self, image, error):
        if error is not None or image is None:
            print("snapshot failed: %s" % error, file=sys.stderr, flush=True)
            NSApp().terminate_(None)
            return
        rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
        data = rep.representationUsingType_properties_(4, {})   # 4 = NSPNGFileType
        data.writeToFile_atomically_(self.out, True)
        print(self.out, flush=True)
        NSApp().terminate_(None)


# Tall enough to read as a titlebar rather than a coincidence, and matched by
# `.titlebar` in app.css -- the visible half of the same strip.
TITLEBAR_H = 38.0


class UIDelegate(NSObject):
    """Native panels for `alert`, `confirm` and `prompt`.

    A WKWebView with no UI delegate does not show these -- it silently does nothing and
    hands JavaScript `undefined`/`null`. That is not a theoretical gap: "Enter a name
    manually" was a dead button for the whole life of this tool, and it is the only way
    to reach a hidden network *and* the fallback the empty state tells you to use when
    the scan finds nothing.

    Wired up so the failure cannot recur silently, even though the two places that
    needed it now have proper in-page interfaces instead.
    """

    # PyObjC exports every method on an NSObject subclass as a selector, and a helper
    # with keyword arguments is not one. Same reason tools/shot-web.py marks its own.
    @objc.python_method
    def _alert(self, message, style=1, buttons=("OK",)):
        a = NSAlert.alloc().init()
        a.setMessageText_("Riparr Preparer")
        a.setInformativeText_(message or "")
        a.setAlertStyle_(style)
        for b in buttons:
            a.addButtonWithTitle_(b)
        return a

    def webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(
            self, view, message, frame, handler):
        self._alert(message).runModal()
        handler()

    def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(
            self, view, message, frame, handler):
        r = self._alert(message, buttons=("OK", "Cancel")).runModal()
        handler(r == NSAlertFirstButtonReturn)

    def webView_runJavaScriptTextInputPanelWithPrompt_defaultText_initiatedByFrame_completionHandler_(
            self, view, prompt, default_text, frame, handler):
        a = self._alert(prompt, buttons=("OK", "Cancel"))
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 260, 24))
        field.setStringValue_(default_text or "")
        a.setAccessoryView_(field)
        a.window().setInitialFirstResponder_(field)
        if a.runModal() == NSAlertFirstButtonReturn:
            handler(field.stringValue())
        else:
            handler(None)


class DragStrip(NSView):
    """An invisible strip across the top of the window that drags it.

    `NSWindowStyleMaskFullSizeContentView` runs the web view up under the titlebar,
    which is what makes the window look like an app rather than a browser -- and which
    also means the web view is sitting exactly where the titlebar's drag region used to
    be. WKWebView consumes the mouse events, so the window could not be moved at all.
    A window that cannot be dragged is not a small blemish: it is the first thing
    anybody tries, and failing it makes everything after feel like a web page in a box.

    `mouseDownCanMoveWindow` is the supported way to say "this area behaves like
    titlebar". It has to be reached first, though -- see the note where this is
    installed in `main()`. It goes over the web view as a sibling, never inside it.
    """

    def mouseDownCanMoveWindow(self):
        # Kept because it is the correct declaration -- "this area behaves like
        # titlebar" -- but it is not what does the work. AppKit dispatches mouseDown:
        # to a view that implements it rather than running its own drag, so on its own
        # this returned YES to nobody. -hitTest: reached the strip and the window still
        # did not move.
        return True

    def isOpaque(self):
        return False

    def mouseDown_(self, event):
        # The explicit, supported way to drag a window from a view (10.11+). It runs
        # the real titlebar drag loop -- snapping, Spaces, and the system's
        # double-click-to-zoom or minimise preference all come with it, which is why
        # there is no clickCount() branch here any more.
        self.window().performWindowDragWithEvent_(event)

    def acceptsFirstMouse_(self, event):
        # Drag an inactive window without first clicking to focus it, the way Finder
        # and every native window do.
        return True


class AppDelegate(NSObject):
    """Quitting is guarded while the card is being written.

    Closing the window used to quit immediately, with a root `dd` mid-flight on the
    card. That produces a half-written card that boots into nothing, and the person who
    did it has no idea that is what they did -- they closed a window. Every other app
    that can lose your work asks first; this one has more to lose than most.

    Setting up over SSH is interruptible and harmless by comparison, so it gets a
    lighter question and the box is simply left as it is.
    """

    bridge = None

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True

    def applicationShouldTerminate_(self, app):
        busy = self.bridge.busy_reason() if self.bridge else ""
        if not busy:
            return 1                                  # NSTerminateNow
        a = NSAlert.alloc().init()
        a.setMessageText_(busy["title"])
        a.setInformativeText_(busy["body"])
        a.setAlertStyle_(2)                           # NSAlertStyleCritical
        a.addButtonWithTitle_("Keep going")
        a.addButtonWithTitle_(busy["quit"])
        if a.runModal() == NSAlertFirstButtonReturn:
            return 0                                  # NSTerminateCancel
        return 1

    def applicationWillTerminate_(self, note):
        import shutil
        shutil.rmtree(RUNDIR, ignore_errors=True)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Riparr Preparer")
    ap.add_argument("--assets", default=os.path.expanduser("~/riparr-build"),
                    help="directory holding the .img.xz, SSH key and password file")
    ap.add_argument("--shot", default="",
                    help="render one screen to a PNG and exit: %s"
                         % ", ".join(sorted(shots.SHOTS)))
    ap.add_argument("--shot-out", default="/tmp/riparr-preparer.png")
    ap.add_argument("--eval", default="",
                    help="with --shot: print this JS expression instead of a PNG")
    a = ap.parse_args()
    assets = os.path.abspath(os.path.expanduser(a.assets))
    if not os.path.isdir(assets):
        os.makedirs(assets, exist_ok=True)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)

    ucc = WKUserContentController.alloc().init()
    ucc.addUserScript_(WKUserScript.alloc()
                       .initWithSource_injectionTime_forMainFrameOnly_(
                           BOOTSTRAP_JS, 0, True))
    cfg = WKWebViewConfiguration.alloc().init()
    cfg.setUserContentController_(ucc)
    try:
        cfg.preferences().setValue_forKey_(True, "developerExtrasEnabled")
    except Exception:
        pass

    w, h = 940, 680
    frame = NSMakeRect(0, 0, w, h)
    mask = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
            | NSWindowStyleMaskFullSizeContentView)
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, mask, NSBackingStoreBuffered, False)
    win.setTitle_("Riparr Preparer")
    win.setTitlebarAppearsTransparent_(True)
    # The title was hidden, which left the top of the window as bare page with three
    # traffic lights floating on it and nothing that reads as "grab here". Every Mac
    # app in this style -- Safari, Mail, Notes -- keeps a visibly distinct top strip
    # even when the content runs underneath it. Showing the title is half of that; the
    # `.titlebar` bar in app.css is the other half.
    win.setTitleVisibility_(NSWindowTitleVisible)
    win.setMinSize_(NSMakeSize(860, 600))
    win.center()

    webview = WKWebView.alloc().initWithFrame_configuration_(frame, cfg)
    webview.setAutoresizingMask_(2 | 16)   # width | height
    try:
        webview.setValue_forKey_(False, "drawsBackground")
    except Exception:
        pass

    bridge = _bridge.Bridge(assets)
    # See shell.py: a self-update quits through the app's own terminate path.
    bridge.on_quit = lambda: NSApplication.sharedApplication().terminate_(None)
    delegate.bridge = bridge
    handler = Handler.alloc().initWithBridge_webview_(bridge, webview)
    ucc.addScriptMessageHandler_name_(handler, "riparr")

    ui_delegate = UIDelegate.alloc().init()
    webview.setUIDelegate_(ui_delegate)
    bridge.ui_delegate = ui_delegate            # keep it alive; PyObjC holds weakly

    index = os.path.join(UI, "index.html")
    webview.loadFileURL_allowingReadAccessToURL_(
        NSURL.fileURLWithPath_(index), NSURL.fileURLWithPath_(UI))

    # The strip has to be a *sibling* of the web view, not a subview of it.
    #
    # WKWebView overrides -hitTest: to return itself for every point inside its bounds
    # -- it routes events to its own internal content view rather than through AppKit's
    # subview hierarchy. So a DragStrip added with webview.addSubview_() is never the
    # view AppKit hit-tests: the answer comes back as the WKWebView, whose
    # -mouseDownCanMoveWindow is NO, and the window does not move. The strip was there,
    # correct, and unreachable.
    #
    # This is why "add an invisible drag view" is written down everywhere as working
    # while this one did not: the usual recipe puts the view over the web view, and
    # putting it *inside* looks equivalent and is not. Probed with -hitTest: rather than
    # reasoned about, because reasoning about it is what produced the broken version.
    #
    # A plain container as the content view, web view first, strip second. Added last
    # means topmost among siblings, and the whole container still sits below
    # NSThemeFrame -- so the traffic lights keep working even though the strip spans
    # the full width.
    root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
    webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    root.addSubview_(webview)

    strip = DragStrip.alloc().initWithFrame_(
        NSMakeRect(0, h - TITLEBAR_H, w, TITLEBAR_H))
    strip.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
    root.addSubview_(strip)

    win.setContentView_(root)

    win.setMovableByWindowBackground_(False)   # only the strip; not stray drags on a form
    win.makeKeyAndOrderFront_(None)
    if a.shot:
        shot = Shot.alloc().initWithWebview_screen_out_eval_(
            webview, a.shot, a.shot_out, a.eval)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.4, shot, "fire:", None, False)
    else:
        app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
