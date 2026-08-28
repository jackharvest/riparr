"""The Python half of the interface: every method JavaScript can call.

Split out of `app.py` when the Windows and Linux ports needed a second window shell.
There is exactly one Bridge and there must stay exactly one -- the alternative is two
copies of "what happens when you press Erase & write", which is the drift `core.py`
exists to prevent and which `tools/flasher/riparr-flash.py` already demonstrated once.

Nothing here imports AppKit. Anything that needs the desktop -- opening a URL, revealing
a file, holding the machine awake -- goes through `hostos`, which knows which desktop it
is on.

Still macOS-only inside `_run_privileged`: elevation and the raw write are step 6 of
docs/design/cross-platform.md and have not been ported yet. `start_write` says so rather
than failing obscurely.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import traceback

import boards
import core
import finish
import hostos

# Must equal server/riparr/__version__ and the release tag. It used to read "1.0.0"
# against releases tagged v0.1.x, which made every update check answer "you are up to
# date" for ever -- a self-update that never fires is indistinguishable from one that
# was never built. release.yml now fails if these three ever drift apart.
VERSION = "0.1.11"

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")

# Sensitive scratch: holds the generated custom.toml between the GUI and the root
# writer. 0700, and removed on exit.
RUNDIR = tempfile.mkdtemp(prefix="riparr-prep-")
os.chmod(RUNDIR, 0o700)


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
            cmd = hostos.keep_awake_command(os.getpid())
            if not cmd:
                # Windows does it in-process instead; see shell.py.
                self.proc = None
                return
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self.proc = None

    def release(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None


def core_publish(status_file, **kw):
    """Write one status snapshot atomically.

    The first parameter is *not* called `path`. It used to be, and the download's "done"
    publish passes the image's own `path=` as data -- so the two collided, every
    successful download raised TypeError inside its worker thread, and the status file
    stayed on "downloading" for ever. What the user saw was a progress bar sitting at
    100% and a Continue button insisting the image had not been downloaded, while the
    image sat correctly on disk. Only the failure path worked, because it passes no path.
    """
    tmp = status_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.replace(tmp, status_file)


# ─────────────────────────── the bridge plumbing ───────────────────────────


class Bridge:
    """Every method here is callable from JavaScript by name."""

    def __init__(self, assets):
        self.assets = assets
        self.progress_path = os.path.join(RUNDIR, "progress.json")
        self.write_thread = None
        self.write_error = None
        # ── OS image download ──
        self.image_path = os.path.join(RUNDIR, "image.json")
        self.image_thread = None
        # ── the second half ──
        self.setup_path = os.path.join(RUNDIR, "setup.json")
        self.update_path = os.path.join(RUNDIR, "update.json")
        # A shell sets this so the window closes properly rather than the
        # process being shot from under it. shell.py registers window.destroy.
        self.on_quit = None
        self.finisher = None
        self.setup_thread = None
        self.nosleep = NoSleep()

    # ── initial state ──
    def boot(self):
        pw, generated = core.ensure_password(self.assets)
        # Generate the box's SSH keypair now if it does not exist, so both the card
        # write (which embeds the public half) and the setup-over-SSH path (which needs
        # the private half) just work on a fresh build folder. Returns None only when
        # ssh-keygen is absent, in which case the UI falls back to password SSH.
        pubkey = core.ensure_key(self.assets)
        images = core.find_images(self.assets)
        default_board = boards.default_id()
        return {
            "version": VERSION,
            "prefs": core.read_prefs(self.assets),
            "repo": core.RIPARR_REPO,
            "assets": self.assets,
            "images": images,
            "image_missing": not images,
            # The hardware dropdown and which board it starts on. The image is chosen per
            # board (core.image_for_board / download_image), so the board picks the OS.
            "boards": boards.all_boards(),
            "default_board": default_board,
            "disks": core.list_disks(),
            "password": pw,
            "password_generated": generated,
            "has_key": pubkey is not None,
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
            "host": core.host_capabilities(),
            "timezone": core.host_timezone(),
            "country": os.environ.get("RIPARR_COUNTRY", "US"),
        }

    # ── OS image, per board ──
    def board_image(self, board_id):
        """The already-downloaded image for this board, or null.

        Lets the UI decide, per selected board, whether to offer 'Download the OS' or go
        straight to writing.
        """
        return {"image": core.image_for_board(self.assets, board_id)}

    def download_image(self, board_id):
        """Fetch this board's OS image in the background. Poll image_status().

        Returns immediately. A download already running is left alone rather than started
        twice -- the image file is large and two writers to the assets dir is a mess.
        """
        if self.image_thread and self.image_thread.is_alive():
            return {"ok": False, "error": "A download is already running."}
        b = boards.get(board_id)
        if not b:
            return {"ok": False, "error": "Unknown board."}
        core_publish(self.image_path, phase="starting", board=board_id,
                     name=b["name"], done=0, total=0)
        self.nosleep.hold("downloading the OS image")

        def run():
            def progress(done, total):
                core_publish(self.image_path, phase="downloading", board=board_id,
                             name=b["name"], done=done, total=total)
            # The whole body, not just the download. "Never let the thread die silently"
            # was the intent before and it only covered the call: the *publish* below
            # raised, the thread died anyway, and the interface waited for a phase that
            # was never going to arrive. A poller with no terminal state is a hang.
            try:
                try:
                    r = core.download_image(board_id, self.assets, progress=progress)
                except Exception as e:
                    r = {"ok": False, "error": str(e)}
                if r.get("ok"):
                    core_publish(self.image_path, phase="done", board=board_id,
                                 name=r.get("name"), image_path=r.get("path"),
                                 cached=bool(r.get("cached")))
                else:
                    core_publish(self.image_path, phase="error", board=board_id,
                                 message=r.get("error", "The download failed."),
                                 detail=r.get("detail", ""))
            except Exception as e:
                try:
                    core_publish(self.image_path, phase="error", board=board_id,
                                 message="The download failed unexpectedly.",
                                 detail=str(e))
                except Exception:
                    pass
            finally:
                self._release_if_idle()

        self.image_thread = threading.Thread(target=run, daemon=True)
        self.image_thread.start()
        return {"ok": True}

    def image_status(self):
        try:
            with open(self.image_path) as f:
                return json.load(f)
        except Exception:
            return {"phase": "idle"}

    def scan_wifi(self):
        nets, method = core.scan_networks()
        return {"networks": nets, "method": method,
                "detail": core.wifi_detail_status()}

    def enable_wifi_detail(self):
        """Ask macOS for Location Services, then rescan. Only ever user-initiated.

        A live scan needs this permission or every SSID comes back nil, but plenty of
        people know which network they want and do not care which band it is on -- so
        this is a button on the Wi-Fi screen rather than a prompt on the way in.
        """
        ok = core.request_wifi_detail()
        nets, method = core.scan_networks()
        return {"ok": ok, "networks": nets, "method": method,
                "detail": core.wifi_detail_status()}

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

    def set_pref(self, key, value):
        """One preference, written to the build folder. See core.PREF_DEFAULTS."""
        return core.write_pref(self.assets, key, value)

    def check_update(self):
        """What is available, and whether this copy can install it itself."""
        info = core.check_for_update(VERSION)
        if info.get("status") == "update":
            asset = core.update_asset_for_host(info.get("assets"))
            target, why = core.update_install_target()
            info["asset"] = asset
            info["can_install"] = bool(asset) and bool(target)
            info["why_not"] = (
                "" if (asset and target)
                else why if not target
                else "This release has no download for %s." % hostos.NAME)
        return info

    def install_update(self):
        """Download it, check it, swap it, come back. Returns once the swap is armed.

        The last two steps happen after this process is gone -- an app cannot overwrite
        itself while it is running -- so `swap_and_relaunch` hands the work to a detached
        script that waits for this PID, replaces the install and starts it again. The
        window closing and reopening *is* the update finishing.

        Refused outright while a card is being written. Quitting mid-write with a raw
        device open is the one thing here that damages something the user cares about.
        """
        busy = self.busy_reason()
        if busy:
            return {"ok": False,
                    "message": "Riparr is busy: %s" % busy.get("body", "please wait"),
                    "detail": "Updating would interrupt it. Try again when it finishes."}

        info = core.check_for_update(VERSION)
        if info.get("status") != "update":
            return {"ok": False, "message": "There is nothing newer to install."}

        target, why = core.update_install_target()
        if not target:
            return {"ok": False, "message": "This copy cannot update itself.",
                    "detail": why}

        asset = core.update_asset_for_host(info.get("assets"))
        if not asset:
            return {"ok": False,
                    "message": "This release has no download for %s." % hostos.NAME}

        core_publish(self.update_path, phase="checking",
                     message="Checking the release")

        def progress(done, total):
            core_publish(self.update_path, phase="downloading", done=done, total=total,
                         message="Downloading version %s" % info["version"])

        expected = core.published_sha256(info["repo"], info["version"], asset["name"])
        path, err = core.download_update(asset, RUNDIR, expected, progress)
        if not path:
            core_publish(self.update_path, phase="error", message=err)
            return {"ok": False, "message": err}

        core_publish(self.update_path, phase="installing",
                     message="Installing version %s" % info["version"])
        ok, detail = hostos.swap_and_relaunch(path, target, os.getpid(), RUNDIR)
        if not ok:
            core_publish(self.update_path, phase="error", message=detail)
            return {"ok": False, "message": "The update could not be installed.",
                    "detail": detail}

        # `detail` is the swapper's log on success. It is the only record of what
        # happened after this process exits, so name it rather than discard it.
        core_publish(self.update_path, phase="restarting",
                     message="Riparr Preparer is restarting", log=detail)
        # The swapper is waiting on this process to exit, so quitting is the last step
        # of the update rather than a separate thing the user has to do.
        self._quit_soon()
        return {"ok": True, "version": info["version"],
                "message": "Riparr Preparer is restarting into version %s."
                           % info["version"]}

    def update_status(self):
        """Polled while an update runs, the same way the card write is."""
        try:
            with open(self.update_path) as f:
                return json.load(f)
        except Exception:
            return {"phase": "idle"}

    def _quit_soon(self):
        """Give the reply time to reach the page, then go.

        Quitting inside the call means the JavaScript that asked for the update never
        hears back, and the window vanishes with no explanation of why.
        """
        def go():
            time.sleep(1.2)
            self.nosleep.release()
            try:
                if self.on_quit:
                    self.on_quit()
            except Exception:
                pass
            # And then leave, whatever that did. The graceful path only *asks* the
            # window to close, from a background thread, and if it returns without the
            # process actually ending -- which is what a self-update looked like: stuck
            # on "restarting", for ever -- then the swapper outside is waiting on a pid
            # that never exits, gives up, and reopens an app that never went away.
            # Nothing after this point needs us alive; we are being replaced.
            for _ in range(30):
                time.sleep(0.1)
            os._exit(0)
        threading.Thread(target=go, daemon=True).start()

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
        hostos.reveal(path)
        return {"ok": True}

    def open_url(self, url):
        # Still scheme-checked here rather than in hostos: this is the boundary the
        # untrusted side of the bridge reaches, and `open` will happily launch a
        # file:// or a custom scheme handler if asked.
        if url.startswith("http://") or url.startswith("https://"):
            hostos.open_url(url)
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
        if not hostos.CAN_WRITE:
            return {"ok": False,
                    "error": "Writing a card isn't supported on %s." % hostos.NAME,
                    "detail": "Everything else works here — the disk list, the Wi-Fi "
                              "scan, and setting up a box that already has a card. "
                              "Adding this platform means one module in hostos/; the "
                              "contract is at the top of hostos/__init__.py."}

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
                "error": "Riparr needs %s, which %s not available on this %s."
                         % (" and ".join(m["tool"] for m in missing),
                            "is" if len(missing) == 1 else "are", hostos.NAME),
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
        """One authorization prompt covers write, provision and eject.

        *How* the prompt is raised is the platform's business and lives in
        `hostos.elevate` — sudo with an osascript askpass on macOS, pkexec on Linux, UAC
        on Windows. What is the same everywhere is that the elevated child cannot talk
        back: it publishes to the progress file and this side polls it. That was forced
        by macOS first and turns out to be the only shape UAC allows either.
        """
        argv = [sys.executable, os.path.join(HERE, "writer.py"),
                "--image", image, "--dev", dev, "--toml", toml_path,
                "--progress", self.progress_path, "--total", str(total),
                "--sha256", sha]
        if verify:
            argv.append("--verify")
        if mkv:
            argv += ["--makemkv", mkv]
        if conf:
            argv += ["--conf", conf]

        rc, err, cancelled = hostos.elevate(argv, RUNDIR, self.progress_path)
        if rc == 0:
            return
        if cancelled:
            core_publish(self.progress_path, phase="cancelled",
                         message="Cancelled. Nothing was written to the card.")
            return
        # If the helper itself failed it already published a real error, and that one
        # names the actual cause. Only speak up when nothing did.
        cur = self.write_status()
        if cur.get("phase") not in ("error", "done"):
            core_publish(self.progress_path, phase="error",
                         message="Could not get permission to write the card.",
                         detail=err or "the writer exited %d." % rc)

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

        # Checked before the box is contacted, not after. A build that is missing the
        # appliance tree can do nothing useful here, and saying so now is the difference
        # between an error and ten minutes of watching a progress bar reach a failure.
        ok, missing = core.payload_ok()
        if not ok:
            return {"ok": False,
                    "error": ("This build of the Preparer is missing the Riparr files "
                              "it installs (%s), so there is nothing to put on the box. "
                              "Download the Preparer again, or run it from a checkout."
                              % ", ".join(missing))}

        self.finisher = finish.Finisher(
            {"hostname": cfg.get("hostname", "riparr"),
             "port": int(cfg.get("port", core.DEFAULT_PORT)),
             "user": cfg.get("remote_user", "root"),
             "key": key,
             "known_hosts": os.path.join(self.assets, "known_hosts"),
             "repo": core.payload_root()},
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
        if self.image_thread and self.image_thread.is_alive():
            return {
                "title": "The OS image is still downloading.",
                "body": ("Quitting stops the download. Nothing is harmed — the partial "
                         "file is discarded and you can download it again next time."),
                "quit": "Quit anyway",
            }
        return ""
