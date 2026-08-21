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
        self.nosleep = NoSleep()

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
            # The setup-only route needs the *private* key, not the public one that
            # goes onto the card -- different file, different question. Without it
            # "set up a box I already wrote a card for" can only fail, so the welcome
            # screen needs to know before it offers the button.
            "can_setup": os.path.exists(os.path.join(self.assets, "riparr_key")),
            "size_guide": core.size_guide(),
            "timezone": core.host_timezone(),
            "country": os.environ.get("RIPARR_COUNTRY", "US"),
        }

    def scan_wifi(self):
        nets, method = core.scan_networks()
        return {"networks": nets, "method": method}

    def keychain_password(self, ssid):
        """Fetch a Wi-Fi passphrase this Mac already knows.

        Never called on its own -- only when the user presses the button offering it,
        so the keychain dialog macOS raises is always in response to something they
        just did.
        """
        pw, err = core.keychain_wifi_password(ssid)
        return {"ok": bool(pw), "password": pw, "error": err}

    def refresh_disks(self):
        return {"disks": core.list_disks()}

    def check_update(self):
        return core.check_for_update(VERSION)

    def preview_toml(self, cfg):
        return {"toml": self._toml(cfg), "conf": core.build_conf(cfg)}

    def check_tools(self, image=None):
        """Tools this write needs that are not installed. See core.missing_tools."""
        return {"missing": core.missing_tools(image)}

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
        # allow_other is set only by the user revealing "other disks" and picking one
        # there. Anything else, including a device that has re-classified itself since
        # the list was drawn, is refused rather than written to.
        disk = core.validate_disk(cfg["disk"], allow_any=bool(cfg.get("allow_other")))
        if not disk:
            return {"ok": False,
                    "error": "That card is no longer available. Reinsert it and rescan."}
        image = cfg.get("image") or (core.find_images(self.assets) or [{}])[0].get("path")
        if not image or not os.path.exists(image):
            return {"ok": False, "error": "The operating system image is missing."}

        # Before the password dialog, and before anything is unmounted. Finding this
        # out afterwards costs the user an admin password and an ejected card, and
        # arrives as "The write stopped unexpectedly" plus an errno.
        missing = core.missing_tools(image)
        if missing:
            return {
                "ok": False,
                "error": "Riparr needs %s, which %s not installed on this Mac."
                         % (" and ".join(m["tool"] for m in missing),
                            "is" if len(missing) == 1 else "are"),
                "detail": "\n\n".join(
                    "%s — %s\n    %s" % (m["tool"], m["why"], m["fix"])
                    for m in missing)
                + "\n\nNothing has been written and your card is untouched.",
            }

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
        self.nosleep.hold("writing a card")
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
        self.nosleep.hold("setting up the box")
        core_publish(self.setup_path, phase="running", message="Starting",
                     pct=0, steps=[], log=[])
        self.setup_thread = threading.Thread(target=self.finisher.run, daemon=True)
        self.setup_thread.start()
        return {"ok": True}

    def setup_status(self):
        try:
            with open(self.setup_path) as f:
                st = json.load(f)
        except Exception:
            return {"phase": "idle"}
        if st.get("phase") in ("done", "error", "cancelled"):
            self._release_if_idle()
        return st

    def name_taken(self, hostname):
        """Is something already answering to this name on the network?

        People build more than one of these -- concept.md says to assume it -- and two
        boxes both called `riparr` means mDNS renames one to `riparr-2.local` behind
        your back. Cheaper to say so while it is still a text field.
        """
        name = "%s.local" % (hostname or "").strip().lower()
        try:
            ip = socket.gethostbyname(name)
        except Exception:
            return {"taken": False}
        return {"taken": True, "address": ip, "name": name}

    def cancel_setup(self):
        if self.finisher:
            self.finisher.cancel.set()
        return {"ok": True}

    def write_status(self):
        try:
            with open(self.progress_path) as f:
                st = json.load(f)
        except Exception:
            return {"phase": "idle"}
        if st.get("phase") in ("done", "error", "cancelled"):
            self._release_if_idle()
        return st

    def _release_if_idle(self):
        if not self.busy_reason():
            self.nosleep.release()

    def busy_reason(self):
        """Whether quitting right now would break something, and what to say about it.

        Called from the app delegate, not from JavaScript.
        """
        phase = (self.write_status() or {}).get("phase")
        if phase in ("write", "verify-card", "provision", "auth", "eject"):
            return {
                "title": "Your card is still being written.",
                "body": ("Quitting now leaves it half-written, which means a card that "
                         "boots into nothing and has to be prepared again from the "
                         "start. Take the card out only after this finishes."),
                "quit": "Quit and ruin the card",
            }
        if self.setup_thread and self.setup_thread.is_alive():
            return {
                "title": "Riparr is still being set up on the box.",
                "body": ("Quitting stops it partway. The card is fine and the box is "
                         "fine — you can open this app again and pick up from "
                         "\u201cIt's plugged in\u201d."),
                "quit": "Quit anyway",
            }
        return ""


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
    "welcome": "setRail(null); show('welcome');",
    # One real card, one device we classified as a drive, and the size guide open --
    # the three things that changed about this screen, all visible at once.
    "card": """
      setRail('card'); show('card');
      renderGuide(%s);
      renderDisks([
        {id:'disk4', name:'SDXC Card', protocol:'Secure Digital', size_gb:128,
         kind:'sd', kind_label:'SD card', is_card:true,
         why:"in this Mac's card slot",
         advice:{headline:'A comfortable Blu-ray evening',
                 detail:'Bursts 2 Blu-rays back to back, streams UHD.'}},
        {id:'disk6', name:'My Passport 25E2', protocol:'USB', size_gb:4000,
         kind:'disk', kind_label:'External drive', is_card:false,
         why:'the name reads as an external drive, not a card', advice:{}}
      ]);
      document.querySelector('#size-guide').open = true;
    """ % json.dumps(core.size_guide()),
    # The state the reveal exists for: no card recognised, but something removable is
    # attached. Worth being able to look at, because it is the screen a person hits
    # when their reader reports itself as a fixed disk.
    "card-other": """
      setRail('card'); show('card');
      renderGuide(%s);
      renderDisks([
        {id:'disk6', name:'Samsung PSSD T7', protocol:'USB', size_gb:1000,
         kind:'disk', kind_label:'External drive', is_card:false,
         why:'the name reads as an external drive, not a card', advice:{}},
        {id:'disk7', name:'USB 3.0 Device', protocol:'USB', size_gb:64,
         kind:'disk', kind_label:'External drive', is_card:false,
         why:'fixed media — this may be an external drive',
         advice:{headline:'Bursts one Blu-ray'}}
      ]);
      document.querySelector('#other-disks').open = true;
    """ % json.dumps(core.size_guide()),
    "connect": """
      state.boot = state.boot || {};
      state.boot.can_setup = true;
      state.boot.assets = '/Users/you/riparr-build';
      state.boot.ssh_config = '/Users/you/riparr-build/ssh_config';
      setRail('connect'); show('connect'); connectPreview();
      document.querySelector('#connect-manual').open = true;
      document.querySelector('#connect-found').className = 'micro good';
      document.querySelector('#connect-found').textContent =
        'riparr.local is answering at 192.168.3.143.';
    """,
    # A network chosen, so the password field and the keychain offer are both on
    # screen -- the state that actually needs looking at.
    "wifi": """
      setRail('card'); show('wifi');
      renderNets([
        {ssid:'Harvest House', bands:['2.4','5'], rssi:-43, secure:true, pi_ok:true, saved:true, seen:true},
        {ssid:'Harvest House 5G', bands:['5'], rssi:-51, secure:true, pi_ok:true, saved:false, seen:true},
        {ssid:'BTWiFi-with-FON', bands:['2.4'], rssi:-78, secure:false, pi_ok:true, saved:false, seen:true},
        {ssid:'NEIGHBOUR-6E', bands:['6'], rssi:-60, secure:true, pi_ok:false, saved:false, seen:true}
      ], 'live');
      pickNet({ssid:'Harvest House', bands:['2.4','5'], rssi:-43, secure:true, pi_ok:true, saved:true});
    """,
    "handoff": """
      setRail('card'); show('handoff');
      document.querySelector('#handoff-skip').innerHTML =
        '<a href="#">Skip — I\\'ll set it up myself</a>';
    """,
    "setup": """
      state.hostname = 'riparr'; state.port = 9797;
      setRail('card'); show('setup');
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
      setRail('card'); show('done'); renderDone(true);
    """,
    # The message people are most likely to actually read, so it is worth being able
    # to look at without failing a real setup.
    "failed": """
      setRail('card'); show('failed');
      document.querySelector('#fail-title').textContent =
        "Couldn't find your Riparr on the network.";
      document.querySelector('#fail-msg').textContent = '';
      document.querySelector('#fail-detail').textContent =
        "Checked riparr.local and swept this network for 300 seconds. In order of likelihood:\\n\\n" +
        "1. The Wi-Fi password is wrong. Nothing before this point can check it, and the box cannot tell you: it boots perfectly and never joins. Write the card again, and use the keychain button on the Wi-Fi step.\\n" +
        "2. The box is on a network this Mac can't see \\u2014 a guest network, or a band your router keeps on a separate subnet.\\n" +
        "3. It is still starting. A first boot resizes the card and can take a few minutes; if it has been under five, wait and try again.\\n" +
        "4. It has no power. The light on the board should be on.";
    """,
    "done-skipped": """
      state.hostname = 'riparr'; state.port = 9797;
      state.boot = state.boot || {}; state.boot.ssh_config = null;
      setRail('card'); show('done'); renderDone(false);
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


# Tall enough to read as a titlebar rather than a coincidence, and matched by
# `.titlebar` in app.css -- the visible half of the same strip.
TITLEBAR_H = 38.0


class NoSleep:
    """Hold the Mac awake while a card is being written or a box set up.

    Both of these are long, unattended, and fail badly when interrupted: a display
    sleep is harmless but a system sleep drops the SSH session mid-install and closes
    the disk being written. `caffeinate -i` asserts only the idle-sleep assertion, so
    the screen still dims and locks normally -- this prevents the machine going to
    sleep, not the user's screensaver.

    Closing the lid still sleeps regardless; nothing in userspace can prevent that, and
    that limitation is what the setup screen's copy has to be honest about.
    """

    def __init__(self):
        self.proc = None

    def hold(self, why):
        if self.proc and self.proc.poll() is None:
            return
        try:
            self.proc = subprocess.Popen(
                ["caffeinate", "-i", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self.proc = None

    def release(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None


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

    bridge = Bridge(assets)
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
