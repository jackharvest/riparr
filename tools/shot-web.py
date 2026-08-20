#!/usr/bin/env python3
"""Screenshot a page in a real WebKit view, from the command line.

For looking at the appliance's own web interface without a browser — checking a CSS
fix landed, catching a layout that only breaks at a certain width, or capturing what a
screen looked like when something went wrong.

    tools/shot-web.py http://riparr.local:9797/ --out /tmp/riparr.png
    tools/shot-web.py http://riparr.local:9797/ --eval "document.title"
    tools/shot-web.py http://riparr.local:9797/ --js "showGate()" --out /tmp/gate.png

Two traps this exists to avoid, both of which make a working page look broken:

  * `screencapture -l <windowid>` returns an empty backing store for a window that is
    occluded or behind another app. WKWebView's own takeSnapshotWithConfiguration:
    renders regardless.
  * A WKWebView in a window that was never brought to the front does not run CSS
    animations, so anything whose visibility depends on animation-fill-mode snapshots
    invisible. --still (on by default) injects a stylesheet disabling animation, which
    also makes repeated shots comparable.

Needs the PyObjC in the Preparer's virtualenv:
    ~/riparr-build/.venv/bin/python tools/shot-web.py …
"""
import argparse
import sys

import objc
from AppKit import (NSApplication, NSWindow, NSApp, NSBitmapImageRep,
                    NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
                    NSWindowStyleMaskTitled, NSMakeRect)
from Foundation import NSObject, NSURL, NSURLRequest, NSTimer
from WebKit import WKWebView, WKWebViewConfiguration, WKSnapshotConfiguration

STILL = ("var s=document.createElement('style');"
         "s.textContent='*{animation:none !important;transition:none !important}';"
         "document.head.appendChild(s);")


class Shooter(NSObject):
    def initWith_(self, opts):
        self = objc.super(Shooter, self).init()
        self.opts = opts
        self.webview = None
        return self

    def go_(self, timer):
        js = ""
        if self.opts.still:
            js += STILL
        if self.opts.js:
            js += self.opts.js + ";"
        if js:
            self.webview.evaluateJavaScript_completionHandler_(js, None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            self.opts.settle, self, "finish:", None, False)

    def finish_(self, timer):
        if self.opts.eval:
            self.webview.evaluateJavaScript_completionHandler_(
                self.opts.eval, self._printed)
            return
        self.webview.takeSnapshotWithConfiguration_completionHandler_(
            WKSnapshotConfiguration.alloc().init(), self._wrote)

    @objc.python_method
    def _printed(self, value, error):
        print(error if error is not None else value, flush=True)
        NSApp().terminate_(None)

    @objc.python_method
    def _wrote(self, image, error):
        if image is None:
            print("snapshot failed: %s" % error, file=sys.stderr, flush=True)
            NSApp().terminate_(None)
            return
        rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
        rep.representationUsingType_properties_(4, {}).writeToFile_atomically_(
            self.opts.out, True)
        print(self.opts.out, flush=True)
        NSApp().terminate_(None)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("url")
    ap.add_argument("--out", default="/tmp/shot-web.png")
    ap.add_argument("--js", default="", help="run before the shot")
    ap.add_argument("--eval", default="", help="print this instead of taking a shot")
    ap.add_argument("--width", type=int, default=1100)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--load", type=float, default=3.0, help="seconds to allow loading")
    ap.add_argument("--settle", type=float, default=0.8, help="seconds after --js")
    ap.add_argument("--no-still", dest="still", action="store_false",
                    help="keep CSS animations (they will not run; see the docstring)")
    a = ap.parse_args()

    app = NSApplication.sharedApplication()
    # Accessory: no Dock icon, and it never steals focus from what you are doing.
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    frame = NSMakeRect(0, 0, a.width, a.height)
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, NSWindowStyleMaskTitled, NSBackingStoreBuffered, False)
    web = WKWebView.alloc().initWithFrame_configuration_(
        frame, WKWebViewConfiguration.alloc().init())
    win.setContentView_(web)
    win.makeKeyAndOrderFront_(None)

    web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(a.url)))

    shooter = Shooter.alloc().initWith_(a)
    shooter.webview = web
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        a.load, shooter, "go:", None, False)
    app.run()


if __name__ == "__main__":
    main()
