#!/usr/bin/env python3
"""Riparr Preparer, second half: from powered-on board to a working web interface.

The first half of the Preparer ends with a card in your hand. Everything after that --
waiting for the box to appear on the network, copying Riparr across, installing it --
was a terminal session you had to know how to drive. That is the seam where an
appliance stops feeling like an appliance.

This module is that session, performed rather than described. It is deliberately not a
console with buttons: a person setting up a disc ripper should be told what is
happening in their own language, and should be able to open the raw log if they want
it, not obliged to read it. Everything here is therefore phrased twice -- once as a
step title for the window, once as the actual command in the log.

Usable three ways, all the same code path:

    from finish import Finisher
    Finisher(cfg, progress_path).run()          # what the GUI calls

    python3 finish.py --host riparr.local       # a real CLI, for scripting and CI
    python3 finish.py --find-only               # just locate the box

Progress is published to a JSON file the window polls, the same contract writer.py
already uses -- see `publish()`. Nothing here writes to stdout unless run as a CLI.
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))


def _default_repo():
    """The tree to install from -- the checkout, or the copy inside the packaged app."""
    try:
        import core
        return core.payload_root()
    except Exception:
        return os.path.abspath(os.path.join(HERE, "..", ".."))


REPO = _default_repo()

# Kept short enough that a stalled step is obvious rather than mysterious, and long
# enough that a slow board is not mistaken for a broken one. pip on a 1 GB A53 is the
# reason `install` is measured in tens of minutes rather than minutes.
TIMEOUTS = {"find": 300, "connect": 60, "copy": 300, "bootstrap": 900, "install": 2400,
            "verify": 180}

# Relative cost of each step, used only to make the progress bar honest. Installing is
# most of the wall clock, so it gets most of the bar.
WEIGHTS = {"find": 8, "connect": 2, "copy": 5, "bootstrap": 20, "install": 60,
           "verify": 5}

STEPS = [
    ("find",      "Finding your Riparr",
                  "Looking for the box on your network"),
    ("connect",   "Connecting",
                  "Opening a secure connection with the key from your card"),
    ("copy",      "Copying Riparr across",
                  "Sending the software to the box"),
    ("bootstrap", "Preparing the system",
                  "Installing build tools and recording what the hardware is"),
    ("install",   "Installing Riparr",
                  "Building the Python environment — the slowest part, several minutes"),
    ("verify",    "Checking it answers",
                  "Making sure the web interface is really up"),
]


class Cancelled(Exception):
    pass


class StepFailed(Exception):
    def __init__(self, step, message, detail=""):
        super().__init__(message)
        self.step = step
        self.message = message
        self.detail = detail


def publish(path, **kw):
    """Atomic write, so a half-written file is never read by the poller."""
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.replace(tmp, path)


def _local_subnet():
    """The /24 this Mac is on. Used only as a fallback when mDNS is silent."""
    for iface in ("en0", "en1"):
        p = subprocess.run(["ipconfig", "getifaddr", iface],
                           capture_output=True, text=True)
        ip = p.stdout.strip()
        if ip.count(".") == 3:
            return ip.rsplit(".", 1)[0]
    return None


def _port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


class Finisher:
    """Drives a freshly booted box from first contact to a running Riparr.

    `cfg` wants: hostname, port, key (path to the private key), user (defaults to
    root -- Armbian has no other account until install.sh makes one), and repo (the
    working tree to install from).
    """

    def __init__(self, cfg, progress_path=None, on_line=None):
        self.cfg = cfg
        self.progress_path = progress_path
        self.on_line = on_line
        self.host = cfg.get("hostname", "riparr")
        self.port = int(cfg.get("port", 9797))
        self.user = cfg.get("user", "root")
        self.key = os.path.expanduser(cfg.get("key", "~/riparr-build/riparr_key"))
        self.repo = cfg.get("repo", REPO)
        self.known_hosts = cfg.get("known_hosts") or os.path.join(
            os.path.dirname(self.key), "known_hosts")
        self.address = None          # resolved IP or hostname we actually reach
        self.found_by = None         # "name" or "address"
        self.reachable_by_name = None  # did <host>.local answer from this Mac?
        self.log = deque(maxlen=400)
        self.state = {k: "waiting" for k, _, _ in STEPS}
        self.current = None
        self.cancel = threading.Event()
        self.error = None

    # ── progress reporting ──────────────────────────────────────────────
    def _pct(self):
        done = sum(WEIGHTS[k] for k, _, _ in STEPS if self.state[k] == "done")
        total = sum(WEIGHTS.values())
        return round(100.0 * done / total, 1)

    def _emit(self, phase, message, **extra):
        publish(self.progress_path,
                phase=phase,
                message=message,
                step=self.current,
                steps=[{"id": k, "title": t, "detail": d, "state": self.state[k]}
                       for k, t, d in STEPS],
                pct=self._pct(),
                address=self.address,
                found_by=self.found_by,
                host=self.host,
                port=self.port,
                log=list(self.log),
                **extra)

    def _say(self, line):
        """One line of raw output, for the disclosure triangle."""
        line = line.rstrip("\n")
        if not line:
            return
        self.log.append(line)
        if self.on_line:
            self.on_line(line)

    def _begin(self, key):
        self.current = key
        self.state[key] = "running"
        title = next(t for k, t, _ in STEPS if k == key)
        self._emit("running", title)

    def _finish_step(self, key):
        self.state[key] = "done"
        self._emit("running", next(t for k, t, _ in STEPS if k == key))

    def _check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled()

    # ── ssh plumbing ────────────────────────────────────────────────────
    def _ssh_base(self, target=None):
        """Host-key policy, stated once.

        A freshly written card carries a brand-new host key, and re-writing the card
        changes it again. Pinning to a global known_hosts would therefore fail on every
        re-flash with a message about a man-in-the-middle, which is both alarming and
        wrong. Instead: a known_hosts file of our own, cleared for this host at the
        start of a run (the card was just written, so the key is *expected* to be new),
        and `accept-new` after that -- so the key is pinned for the rest of the session
        and a genuine mid-session substitution is still refused.
        """
        return [
            "ssh", "-i", self.key,
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=%s" % self.known_hosts,
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4",
            "-o", "LogLevel=ERROR",
            "%s@%s" % (self.user, target or self.address or self.host),
        ]

    def _forget_host_key(self, target):
        if os.path.exists(self.known_hosts):
            subprocess.run(["ssh-keygen", "-R", target, "-f", self.known_hosts],
                           capture_output=True, text=True)

    def _run_remote(self, command, step, timeout, label=None):
        """Run one command on the box, streaming its output into the log."""
        self._check_cancel()
        self._say("$ %s" % (label or command))
        p = subprocess.Popen(self._ssh_base() + [command],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        deadline = time.time() + timeout
        try:
            for line in p.stdout:
                self._say(_strip_ansi(line))
                self._emit("running",
                           next(t for k, t, _ in STEPS if k == step))
                if self.cancel.is_set():
                    p.kill()
                    raise Cancelled()
                if time.time() > deadline:
                    p.kill()
                    raise StepFailed(step, "This step took longer than expected.",
                                     "Timed out after %d minutes." % (timeout // 60))
        finally:
            p.stdout.close()
        rc = p.wait()
        if rc != 0:
            raise StepFailed(step, "That step failed on the box.",
                             "\n".join(list(self.log)[-25:]))
        return rc

    # ── the steps ───────────────────────────────────────────────────────
    def step_find(self):
        """Locate the box by name, then by address.

        mDNS is the intended route and is normally instant. It is not guaranteed --
        the Armbian image ships no avahi, so the responder is systemd-resolved, which
        the Preparer has to switch on per link. A card written before that fix boots
        perfectly and is nameless, which is exactly the failure this fallback exists
        to absorb.
        """
        self._begin("find")
        name = "%s.local" % self.host
        deadline = time.time() + TIMEOUTS["find"]
        swept = False

        while time.time() < deadline:
            self._check_cancel()

            ip = _resolve(name)
            if ip and _port_open(ip, 22):
                self.address, self.found_by = ip, "name"
                self._say("%s resolved to %s" % (name, ip))
                self._finish_step("find")
                return

            # Only sweep once, and only after giving the name a fair chance -- a
            # subnet sweep is slow and touches every host on the network.
            if not swept and time.time() > deadline - TIMEOUTS["find"] + 45:
                swept = True
                self._say("%s is not resolving; looking for it by address instead"
                          % name)
                found = self._sweep()
                if found:
                    self.address, self.found_by = found, "address"
                    self._say("found it at %s" % found)
                    self._finish_step("find")
                    return

            self._emit("running", "Waiting for the box to come up",
                       waiting_for=name)
            time.sleep(3)

        raise StepFailed(
            "find", "Couldn't find your Riparr on the network.",
            # Ordered by what actually goes wrong, which is not what the old message
            # said. It led with "it may still be starting", so the one cause that is
            # both most likely and unrecoverable-without-rewriting -- a mistyped Wi-Fi
            # password -- went unmentioned, and people waited instead of re-writing.
            "Checked %s and swept this network for %d seconds. In order of likelihood:\n"
            "\n"
            "1. The Wi-Fi password is wrong. Nothing before this point can check it, "
            "and the box cannot tell you: it boots perfectly and never joins. Write "
            "the card again, and use the keychain button on the Wi-Fi step.\n"
            "2. The box is on a network this Mac can't see — a guest network, or a "
            "band your router keeps on a separate subnet.\n"
            "3. It is still starting. A first boot resizes the card and can take a few "
            "minutes; if it has been under five, wait and try again.\n"
            "4. It has no power. The light on the board should be on."
            % (name, TIMEOUTS["find"]))

    def _sweep(self):
        """Offer the key to every host with SSH open; the box is the one that takes it.

        Batched rather than all-at-once: hundreds of concurrent connects saturate the
        socket table and produce false negatives, which read as "the box is not there"
        when it is. That mistake cost a full session once already.
        """
        net = _local_subnet()
        if not net:
            return None
        candidates = []
        for start in range(1, 255, 32):
            self._check_cancel()
            batch = range(start, min(start + 32, 255))
            threads, hits = [], []
            lock = threading.Lock()

            def probe(n):
                ip = "%s.%d" % (net, n)
                if _port_open(ip, 22, 1.0):
                    with lock:
                        hits.append(ip)

            for n in batch:
                t = threading.Thread(target=probe, args=(n,), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join(4)
            candidates.extend(hits)

        self._say("%d host%s with SSH open; asking which one is Riparr"
                  % (len(candidates), "" if len(candidates) == 1 else "s"))
        for ip in candidates:
            self._check_cancel()
            self._forget_host_key(ip)
            p = subprocess.run(
                self._ssh_base(ip) + ["cat /etc/hostname 2>/dev/null"],
                capture_output=True, text=True, timeout=15)
            if p.returncode == 0 and p.stdout.strip() == self.host:
                return ip
        return None

    def step_connect(self):
        self._begin("connect")
        self._forget_host_key(self.address)
        p = subprocess.run(
            self._ssh_base() + ["id -un; cat /etc/hostname; uname -sr"],
            capture_output=True, text=True, timeout=TIMEOUTS["connect"])
        if p.returncode != 0:
            raise StepFailed(
                "connect", "The box refused the key from your card.",
                (p.stderr or "").strip()
                + "\n\nThis usually means the card in the box was written by a "
                  "different copy of the Preparer, with a different key.")
        for line in p.stdout.splitlines():
            self._say(line)
        self._finish_step("connect")

    def step_copy(self):
        """Stream the working tree over the existing connection.

        tar over ssh rather than rsync: rsync has to exist on both ends, and this is
        one pipe with no temporary files and nothing left behind on failure.
        """
        self._begin("copy")
        dest = "/root/riparr"
        self._say("$ tar -cf - (working tree) | ssh … tar -xf - -C %s" % dest)
        tar = subprocess.Popen(
            ["tar", "-cf", "-",
             "--exclude", "./.venv", "--exclude", "./.git",
             "--exclude", "__pycache__", "--exclude", "*.pyc",
             "-C", self.repo, "."],
            stdout=subprocess.PIPE)
        ssh = subprocess.Popen(
            self._ssh_base() + ["rm -rf %s && mkdir -p %s && tar -xf - -C %s"
                                % (dest, dest, dest)],
            stdin=tar.stdout, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        tar.stdout.close()
        out, _ = ssh.communicate(timeout=TIMEOUTS["copy"])
        tar.wait()
        for line in (out or "").splitlines():
            self._say(line)
        if ssh.returncode != 0 or tar.returncode != 0:
            raise StepFailed("copy", "Couldn't copy Riparr onto the box.",
                             out or "tar exited %d, ssh exited %d"
                             % (tar.returncode, ssh.returncode))
        self._run_remote("du -sh %s" % dest, "copy", 30)
        self._finish_step("copy")

    def step_bootstrap(self):
        self._begin("bootstrap")
        self._run_remote("cd /root/riparr && bash tools/bootstrap.sh",
                         "bootstrap", TIMEOUTS["bootstrap"])
        self._finish_step("bootstrap")

    def step_install(self):
        self._begin("install")
        self._run_remote("cd /root/riparr && sudo bash tools/install.sh",
                         "install", TIMEOUTS["install"])
        self._finish_step("install")

    def step_verify(self):
        """Ask the service itself, from here, over the network.

        install.sh already checks 127.0.0.1 on the box. That proves the process is
        running; it does not prove the address we are about to put in front of the
        user actually works from their Mac. Those are different claims and only the
        second one matters now.
        """
        self._begin("verify")
        url = "http://%s:%d/api/setup/state" % (self.address, self.port)
        by_name = "http://%s.local:%d/api/setup/state" % (self.host, self.port)
        deadline = time.time() + TIMEOUTS["verify"]
        last = ""
        while time.time() < deadline:
            self._check_cancel()
            for candidate in (by_name, url):
                p = subprocess.run(["curl", "-fsS", "--max-time", "4", candidate],
                                   capture_output=True, text=True)
                if p.returncode == 0:
                    self._say("%s answered" % candidate)
                    self.reachable_by_name = candidate == by_name
                    self._finish_step("verify")
                    return
                last = (p.stderr or "").strip()
            time.sleep(3)
        raise StepFailed("verify",
                         "Riparr installed, but isn't answering yet.",
                         "Tried %s and %s.\n%s" % (by_name, url, last))

    # ── orchestration ───────────────────────────────────────────────────
    def run(self):
        started = time.time()
        try:
            self.step_find()
            self.step_connect()
            self.step_copy()
            self.step_bootstrap()
            self.step_install()
            self.step_verify()
        except Cancelled:
            self._emit("cancelled", "Stopped. The box was left as it is.")
            return False
        except StepFailed as e:
            self.state[e.step] = "failed"
            self.current = e.step
            self.error = e
            self._emit("error", e.message, detail=e.detail)
            return False
        except Exception as e:                                    # unexpected
            self._emit("error", "Something went wrong during setup.",
                       detail="%s: %s" % (type(e).__name__, e))
            return False

        self._emit("done", "Riparr is running.",
                   elapsed=round(time.time() - started),
                   url="http://%s.local:%d" % (self.host, self.port),
                   url_address="http://%s:%d" % (self.address, self.port),
                   by_name=self.reachable_by_name)
        return True


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(s):
    """install.sh prints colour. A log pane is not a terminal."""
    return ANSI.sub("", s)


def _resolve(name):
    try:
        return socket.gethostbyname(name)
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(
        description="Take a freshly booted Riparr from powered-on to running.")
    ap.add_argument("--host", default="riparr", help="hostname without .local")
    ap.add_argument("--port", type=int, default=9797)
    ap.add_argument("--user", default="root",
                    help="Armbian has no other account until install.sh makes one")
    ap.add_argument("--key", default="~/riparr-build/riparr_key")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--progress", default="", help="write progress JSON here")
    ap.add_argument("--find-only", action="store_true",
                    help="just locate the box and print its address")
    args = ap.parse_args()

    cfg = {"hostname": args.host, "port": args.port, "user": args.user,
           "key": args.key, "repo": args.repo}
    f = Finisher(cfg, args.progress or None,
                 on_line=lambda l: print("  " + l, flush=True))

    if args.find_only:
        try:
            f.step_find()
        except StepFailed as e:
            print("\n%s\n%s" % (e.message, e.detail), file=sys.stderr)
            return 1
        print("\n%s is at %s (found by %s)" % (f.host, f.address, f.found_by))
        return 0

    print("Setting up %s\n" % args.host)
    ok = f.run()
    if ok:
        print("\nRiparr is running: http://%s.local:%d" % (args.host, args.port))
        return 0
    if f.error:
        print("\n%s\n%s" % (f.error.message, f.error.detail), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
