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
import subprocess
import sys
import tempfile
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import objc
from AppKit import (NSApplication, NSWindow, NSApp, NSScreen, NSColor,
                    NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
                    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
                    NSWindowStyleMaskFullSizeContentView, NSWindowTitleHidden,
                    NSMakeRect, NSMakeSize, NSWorkspace)
from Foundation import NSObject, NSURL, NSString
from WebKit import (WKWebView, WKWebViewConfiguration, WKUserContentController,
                    WKUserScript, WKPreferences)

import core

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")

# Sensitive scratch: holds the generated custom.toml between the GUI and the root
# writer. 0700, and removed on exit.
RUNDIR = tempfile.mkdtemp(prefix="riparr-prep-")
os.chmod(RUNDIR, 0o700)


class Bridge:
    """Every method here is callable from JavaScript by name."""

    def __init__(self, assets):
        self.assets = assets
        self.progress_path = os.path.join(RUNDIR, "progress.json")
        self.write_thread = None
        self.write_error = None

    # ── initial state ──
    def boot(self):
        pw, generated = core.ensure_password(self.assets)
        images = core.find_images(self.assets)
        return {
            "version": VERSION,
            "repo": core.RIPARR_REPO,
            "assets": self.assets,
            "images": images,
            "image_missing": not images,
            "disks": core.list_disks(),
            "password": pw,
            "password_generated": generated,
            "has_key": core.public_key(self.assets) is not None,
            "timezone": core.host_timezone(),
            "country": os.environ.get("RIPARR_COUNTRY", "US"),
        }

    def scan_wifi(self):
        nets, method = core.scan_networks()
        return {"networks": nets, "method": method}

    def refresh_disks(self):
        return {"disks": core.list_disks()}

    def check_update(self):
        return core.check_for_update(VERSION)

    def preview_toml(self, cfg):
        return {"toml": self._toml(cfg)}

    def save_toml_only(self, cfg):
        out = os.path.join(self.assets, "custom.toml")
        with open(out, "w") as f:
            f.write(self._toml(cfg))
        return {"path": out}

    def reveal(self, path):
        subprocess.run(["open", "-R", path])
        return {"ok": True}

    def open_url(self, url):
        if url.startswith("http://") or url.startswith("https://"):
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))
        return {"ok": True}

    # ── the write ──
    def _toml(self, cfg):
        return core.build_toml({
            "hostname": cfg["hostname"],
            "user": cfg["user"],
            "pw_hash": core.sha512_crypt(cfg["password"]),
            "ssid": cfg["ssid"],
            "wifi_pw": cfg.get("wifi_pw", ""),
            "secure": cfg.get("secure", True),
            "hidden": cfg.get("hidden", False),
            "country": cfg.get("country", "US"),
            "timezone": cfg.get("timezone", core.host_timezone()),
            "keymap": "us",
            "authorized_key": core.public_key(self.assets),
        })

    def start_write(self, cfg):
        disk = core.validate_disk(cfg["disk"])
        if not disk:
            return {"ok": False,
                    "error": "That card is no longer available. Reinsert it and rescan."}
        image = cfg.get("image") or (core.find_images(self.assets) or [{}])[0].get("path")
        if not image or not os.path.exists(image):
            return {"ok": False, "error": "The operating system image is missing."}

        toml_path = os.path.join(RUNDIR, "custom.toml")
        with open(toml_path, "w") as f:
            f.write(self._toml(cfg))
        os.chmod(toml_path, 0o600)

        total = core.uncompressed_size(image)
        core_publish(self.progress_path, phase="auth",
                     message="Waiting for your administrator password")

        self.write_error = None
        self.write_thread = threading.Thread(
            target=self._run_privileged,
            args=(image, disk["id"], toml_path, total), daemon=True)
        self.write_thread.start()
        return {"ok": True, "total": total}

    def _run_privileged(self, image, dev, toml_path, total):
        """One authorization dialog covers write, provision and eject."""
        script = os.path.join(RUNDIR, "write.sh")
        with open(script, "w") as f:
            f.write("#!/bin/sh\nexec %s %s --image %s --dev %s --toml %s "
                    "--progress %s --total %d\n" % (
                        _q(sys.executable), _q(os.path.join(HERE, "writer.py")),
                        _q(image), _q(dev), _q(toml_path),
                        _q(self.progress_path), total))
        os.chmod(script, 0o700)

        osa = ('do shell script "/bin/sh %s" '
               'with prompt "Riparr needs permission to write to your SD card." '
               'with administrator privileges' % script.replace('\\', '\\\\').replace('"', '\\"'))
        p = subprocess.run(["osascript", "-e", osa], capture_output=True, text=True)
        if p.returncode != 0:
            err = (p.stderr or "").strip()
            if "-128" in err or "User canceled" in err:
                core_publish(self.progress_path, phase="cancelled",
                             message="Cancelled. Nothing was written to the card.")
            else:
                # If the helper itself failed it already published a real error.
                cur = self.write_status()
                if cur.get("phase") not in ("error", "done"):
                    core_publish(self.progress_path, phase="error",
                                 message="Could not get permission to write the card.",
                                 detail=err)

    def write_status(self):
        try:
            with open(self.progress_path) as f:
                return json.load(f)
        except Exception:
            return {"phase": "idle"}


def core_publish(path, **kw):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.replace(tmp, path)


def _q(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


# ─────────────────────────── the bridge plumbing ───────────────────────────

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


class AppDelegate(NSObject):
    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True

    def applicationWillTerminate_(self, note):
        import shutil
        shutil.rmtree(RUNDIR, ignore_errors=True)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Riparr Preparer")
    ap.add_argument("--assets", default=os.path.expanduser("~/riparr-build"),
                    help="directory holding the .img.xz, SSH key and password file")
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
    win.setTitleVisibility_(NSWindowTitleHidden)
    win.setMinSize_(NSMakeSize(860, 600))
    win.center()

    webview = WKWebView.alloc().initWithFrame_configuration_(frame, cfg)
    webview.setAutoresizingMask_(2 | 16)   # width | height
    try:
        webview.setValue_forKey_(False, "drawsBackground")
    except Exception:
        pass

    bridge = Bridge(assets)
    handler = Handler.alloc().initWithBridge_webview_(bridge, webview)
    ucc.addScriptMessageHandler_name_(handler, "riparr")

    index = os.path.join(UI, "index.html")
    webview.loadFileURL_allowingReadAccessToURL_(
        NSURL.fileURLWithPath_(index), NSURL.fileURLWithPath_(UI))

    win.setContentView_(webview)
    win.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
