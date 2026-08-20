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
                    NSBitmapImageRep,
                    NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
                    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
                    NSWindowStyleMaskFullSizeContentView, NSWindowTitleHidden,
                    NSMakeRect, NSMakeSize, NSWorkspace)
from Foundation import NSObject, NSURL, NSString, NSTimer
from WebKit import (WKWebView, WKWebViewConfiguration, WKUserContentController,
                    WKUserScript, WKPreferences, WKSnapshotConfiguration)

import core
import finish

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
        # ── the second half ──
        self.setup_path = os.path.join(RUNDIR, "setup.json")
        self.finisher = None
        self.setup_thread = None

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
            "makemkv": os.path.isdir(os.path.join(self.assets, "makemkv")),
            "ssh_config": (os.path.join(self.assets, "ssh_config")
                           if os.path.exists(os.path.join(self.assets, "ssh_config"))
                           else None),
            "default_port": core.DEFAULT_PORT,
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
        return {"toml": self._toml(cfg), "conf": core.build_conf(cfg)}

    def check_port(self, port):
        ok, message = core.check_port(port)
        return {"ok": ok, "message": message}

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

        conf_path = os.path.join(RUNDIR, "riparr.conf")
        with open(conf_path, "w") as f:
            f.write(core.build_conf(cfg))

        total = core.uncompressed_size(image)
        sha = core.expected_sha256(image) or ""
        verify = bool(cfg.get("verify", True))
        mkv = os.path.join(self.assets, "makemkv")
        mkv = mkv if os.path.isdir(mkv) else ""
        core_publish(self.progress_path, phase="auth",
                     message="Waiting for your administrator password")

        self.write_error = None
        self.write_thread = threading.Thread(
            target=self._run_privileged,
            args=(image, disk["id"], toml_path, total, sha, verify, mkv, conf_path),
            daemon=True)
        self.write_thread.start()
        return {"ok": True, "total": total, "verify": verify,
                "kind": core.image_kind(image)}

    def _run_privileged(self, image, dev, toml_path, total, sha="", verify=True, mkv="",
                        conf=""):
        """One authorization dialog covers write, provision and eject.

        Elevation is `sudo -A`, deliberately, and not osascript's `with administrator
        privileges`. The latter runs the helper through security_authtrampoline, which
        re-parents it away from the launching application — and macOS attributes disk
        and removable-volume consent to the *responsible* application, not to the user
        and not to root. A trampolined helper therefore inherits no consent at all and
        is refused with EPERM on /dev/rdiskN even as root, while the terminal it was
        launched from can write the very same card. sudo keeps the writer a direct
        descendant, so the consent that is already granted still applies.
        """
        script = os.path.join(RUNDIR, "write.sh")
        with open(script, "w") as f:
            f.write("#!/bin/sh\nexec %s %s --image %s --dev %s --toml %s "
                    "--progress %s --total %d --sha256 %s%s\n" % (
                        _q(sys.executable), _q(os.path.join(HERE, "writer.py")),
                        _q(image), _q(dev), _q(toml_path),
                        _q(self.progress_path), total, _q(sha),
                        (" --verify" if verify else "")
                        + ((" --makemkv " + _q(mkv)) if mkv else "")
                        + ((" --conf " + _q(conf)) if conf else "")))
        os.chmod(script, 0o700)

        # sudo asks for the password up to three times. The sentinel makes a cancelled
        # dialog cancel the whole write instead of asking twice more.
        cancel = os.path.join(RUNDIR, "cancelled")
        askpass = os.path.join(RUNDIR, "askpass.sh")
        with open(askpass, "w") as f:
            f.write(
                '#!/bin/sh\n'
                '[ -f %s ] && exit 1\n'
                'pw=$(osascript -e \'display dialog "Riparr needs permission to write '
                'to your SD card." with title "Riparr Preparer" default answer "" '
                'with hidden answer with icon caution\' -e \'text returned of result\' '
                '2>/dev/null) || { : > %s; exit 1; }\n'
                'printf %%s "$pw"\n' % (_q(cancel), _q(cancel)))
        os.chmod(askpass, 0o700)

        env = dict(os.environ, SUDO_ASKPASS=askpass)
        p = subprocess.run(["sudo", "-A", "/bin/sh", script],
                           capture_output=True, text=True, env=env)
        if p.returncode != 0:
            err = (p.stderr or "").strip()
            if os.path.exists(cancel):
                core_publish(self.progress_path, phase="cancelled",
                             message="Cancelled. Nothing was written to the card.")
            else:
                # If the helper itself failed it already published a real error.
                cur = self.write_status()
                if cur.get("phase") not in ("error", "done"):
                    core_publish(self.progress_path, phase="error",
                                 message="Could not get permission to write the card.",
                                 detail=err or "sudo exited %d." % p.returncode)

    # ── the second half: from a powered-on board to a running Riparr ──
    def start_setup(self, cfg):
        """Kick off remote setup. Returns immediately; poll setup_status().

        No elevation here, and no password prompt: everything happens on the box over
        SSH, authenticated by the key the card already carries. That is the whole
        reason this can be automatic rather than a console the user has to drive.
        """
        if self.setup_thread and self.setup_thread.is_alive():
            return {"ok": False, "error": "Setup is already running."}

        key = os.path.join(self.assets, "riparr_key")
        if not os.path.exists(key):
            return {"ok": False,
                    "error": "The SSH key is missing from your build folder, so the "
                             "box cannot be reached. Prepare a card again."}

        self.finisher = finish.Finisher(
            {"hostname": cfg.get("hostname", "riparr"),
             "port": int(cfg.get("port", core.DEFAULT_PORT)),
             "user": cfg.get("remote_user", "root"),
             "key": key,
             "known_hosts": os.path.join(self.assets, "known_hosts"),
             "repo": os.path.abspath(os.path.join(HERE, "..", ".."))},
            self.setup_path)
        core_publish(self.setup_path, phase="running", message="Starting",
                     pct=0, steps=[], log=[])
        self.setup_thread = threading.Thread(target=self.finisher.run, daemon=True)
        self.setup_thread.start()
        return {"ok": True}

    def setup_status(self):
        try:
            with open(self.setup_path) as f:
                return json.load(f)
        except Exception:
            return {"phase": "idle"}

    def cancel_setup(self):
        if self.finisher:
            self.finisher.cancel.set()
        return {"ok": True}

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


# Sample state for --shot. Driving the real flow needs a card, a network and a board;
# this paints the same DOM from fixed data so a screen can be looked at on demand.
SHOTS = {
    "handoff": """
      show('handoff');
      document.querySelector('#handoff-skip').innerHTML =
        '<a href="#">Skip — I\\'ll set it up myself</a>';
    """,
    "setup": """
      state.hostname = 'riparr'; state.port = 9797;
      show('setup');
      renderTasks([
        {id:'find',      title:'Finding your Riparr',   detail:'Looking for the box on your network', state:'done'},
        {id:'connect',   title:'Connecting',            detail:'Opening a secure connection', state:'done'},
        {id:'copy',      title:'Copying Riparr across', detail:'Sending the software to the box', state:'done'},
        {id:'bootstrap', title:'Preparing the system',  detail:'Installing build tools and recording what the hardware is', state:'done'},
        {id:'install',   title:'Installing Riparr',     detail:'Building the Python environment — the slowest part, several minutes', state:'running'},
        {id:'verify',    title:'Checking it answers',   detail:'Making sure the web interface is really up', state:'waiting'}
      ]);
      document.querySelector('#setup-fill').style.width = '62%';
      document.querySelector('#setup-pct').textContent = '62%';
      document.querySelector('#setup-detail').textContent = 'riparr.local';
      document.querySelector('#setup-hint').textContent = 'Installing Riparr';
      document.querySelector('#log-reveal').open = true;
      document.querySelector('#setup-log').textContent =
        ['$ cd /root/riparr && sudo bash tools/install.sh',
         'Installing Riparr',
         '  port 9797 · /opt/riparr · OrangePi Zero 2W',
         '1/6  Packages',
         '  \u2713 avahi owns riparr.local (resolved\u2019s responder stood down)',
         '  \u2713 dependencies present',
         '2/6  Account',
         '  \u2713 user \u2018riparr\u2019 ready; staging at /srv/staging',
         '3/6  Riparr',
         '  \u2713 Riparr 0.1.0 in /opt/riparr',
         '4/6  Python environment',
         '  installing dependencies (a few minutes on a Zero 2 W)'].join('\\n');
    """,
    "done": """
      state.hostname = 'riparr'; state.port = 9797; state.elapsed = 571;
      show('done'); renderDone(true);
    """,
    "done-skipped": """
      state.hostname = 'riparr'; state.port = 9797;
      state.boot = state.boot || {}; state.boot.ssh_config = null;
      show('done'); renderDone(false);
    """,
}


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
    STILL = ("var s=document.createElement('style');"
             "s.textContent='*{animation:none !important;transition:none !important}"
             ".screen.on{opacity:1 !important;transform:none !important}';"
             "document.head.appendChild(s);")

    def fire_(self, timer):
        js = self.STILL + SHOTS.get(self.screen, "show('%s');" % self.screen)
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
    ap.add_argument("--shot", default="",
                    help="render one screen to a PNG and exit: %s"
                         % ", ".join(sorted(SHOTS)))
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
