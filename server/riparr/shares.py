"""
Finding the user's network share, and proving we can actually write to it.

"My share path was wrong" is the dominant support burden for this class of device, and
it usually surfaces at 3am on the first rip rather than during setup. So discovery is
automatic and a real file is written and read back before setup is allowed to continue.
"""
import concurrent.futures
import hashlib
import os
import re
import socket
import subprocess
import tempfile
import threading
import time

from . import platform as P

SMB_PORT = 445

# The tools this module shells out to. Both come from Debian packages that are not
# installed by default, and a missing one used to surface as a 500 with
# FileNotFoundError('smbclient') in the log and nothing at all in the interface.
SMBCLIENT = "smbclient"


class SmbToolMissing(Exception):
    """smbclient is not on this box. Actionable, so say so rather than crashing."""

    message = (
        "The tools for talking to network shares aren't installed on this box. "
        "Re-run the installer to add them:\n\n"
        "    sudo bash /opt/riparr/tools/install.sh\n\n"
        "Then try again.")


def _auth_file(username, password):
    """Credentials in a 0600 file, not on the command line.

    `-U user%password` has two problems. A '%' anywhere in the password truncates it
    there, so a perfectly correct password fails authentication for no visible reason.
    And argv is world-readable through /proc, so every password typed into the setup
    wizard would be visible to `ps` for the life of the call.

    An authentication file also gives somewhere to put the domain, which matters:
    accounts are commonly given as DOMAIN\\user or user@domain, and the domain has to be
    split out rather than passed through as part of the username.
    """
    domain = ""
    if username and "\\" in username:
        domain, username = username.split("\\", 1)
    elif username and "@" in username:
        username, domain = username.split("@", 1)

    f = tempfile.NamedTemporaryFile("w", suffix=".auth", delete=False)
    os.chmod(f.name, 0o600)
    f.write("username = %s\npassword = %s\n" % (username, password))
    if domain:
        f.write("domain = %s\n" % domain)
    f.close()
    return f.name


def _auth_args(username, password):
    """Returns (args, path_to_clean_up). Anonymous when no username is given."""
    if not username:
        return ["-N"], None
    path = _auth_file(username, password)
    return ["-A", path], path


def _forget(path):
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def discover(timeout=2.5):
    """mDNS first, then a bounded sweep of the local /24. Returns hosts, not shares."""
    if P.MOCK:
        time.sleep(0.6)
        return [
            {"host": "TOWER.local", "address": "192.168.1.20", "via": "mdns"},
            {"host": "synology.local", "address": "192.168.1.31", "via": "mdns"},
            {"host": "192.168.1.44", "address": "192.168.1.44", "via": "scan"},
        ]
    found = {}
    for h in _mdns_hosts():
        found[h["address"]] = h
    for h in _sweep(timeout):
        found.setdefault(h["address"], h)
    return sorted(found.values(), key=lambda h: h["address"])


def _mdns_hosts():
    out = P._run(["avahi-browse", "-artp", "_smb._tcp"], timeout=6) or ""
    hosts = []
    for line in out.splitlines():
        f = line.split(";")
        if len(f) > 7 and f[0] == "=":
            hosts.append({"host": f[6], "address": f[7], "via": "mdns"})
    return hosts


def _sweep(timeout):
    ip = P._ip()
    if not ip:
        return []
    base = ".".join(ip.split(".")[:3])

    def probe(n):
        addr = "%s.%d" % (base, n)
        s = socket.socket()
        s.settimeout(timeout)
        try:
            s.connect((addr, SMB_PORT))
            try:
                name = socket.gethostbyaddr(addr)[0]
            except Exception:
                name = addr
            return {"host": name, "address": addr, "via": "scan"}
        except Exception:
            return None
        finally:
            s.close()

    hits = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        for r in ex.map(probe, range(1, 255)):
            if r:
                hits.append(r)
    return hits


def list_shares(host, username="", password=""):
    """Enumerate the shares a host offers. Guest first, since most NAS boxes allow it."""
    if P.MOCK:
        return {"ok": True, "shares": ["Media", "Movies", "TV", "Backups", "public"]}
    auth, tmp = _auth_args(username, password)
    cmd = [SMBCLIENT, "-L", "//%s" % host, "-g"] + auth
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        raise SmbToolMissing()
    except subprocess.TimeoutExpired:
        return {"ok": False, "shares": [],
                "error": "%s did not answer within 20 seconds." % host}
    finally:
        _forget(tmp)
    if p.returncode != 0:
        return {"ok": False, "error": _clean(p.stderr or p.stdout), "shares": []}
    shares = [l.split("|")[1] for l in p.stdout.splitlines()
              if l.startswith("Disk|") and len(l.split("|")) > 1]
    return {"ok": True, "shares": [s for s in shares if not s.endswith("$")]}


def test_write(host, share, path, username="", password=""):
    """Write a real file, read it back, delete it.

    An SMB write that returns success is not proof of a good file (D6). The read-back
    is the point of this function; without it the check is theatre.
    """
    token = "riparr-write-test-%d" % int(time.time())
    body = "Riparr write test. Safe to delete.\n%s\n" % token

    if P.MOCK:
        time.sleep(0.9)
        if "bad" in (share or "").lower() or "bad" in (path or "").lower():
            return {"ok": False, "stage": "write",
                    "error": "NT_STATUS_ACCESS_DENIED writing to //%s/%s" % (host, share)}
        return {"ok": True, "wrote": token, "verified": True,
                "target": "//%s/%s/%s" % (host, share, path.strip("/"))}

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(body)
        local = f.name
    remote_dir = (path or "").strip("/")
    remote = "%s/%s.txt" % (remote_dir, token) if remote_dir else "%s.txt" % token
    auth, authfile = _auth_args(username, password)
    base = [SMBCLIENT, "//%s/%s" % (host, share)] + auth + ["-c"]

    try:
        p = subprocess.run(base + ['put "%s" "%s"' % (local, remote)],
                           capture_output=True, text=True, timeout=45)
        if p.returncode != 0:
            return {"ok": False, "stage": "write", "error": _clean(p.stderr or p.stdout)}

        back = local + ".back"
        p = subprocess.run(base + ['get "%s" "%s"' % (remote, back)],
                           capture_output=True, text=True, timeout=45)
        if p.returncode != 0:
            return {"ok": False, "stage": "readback", "error": _clean(p.stderr or p.stdout)}
        verified = os.path.exists(back) and open(back).read() == body

        subprocess.run(base + ['del "%s"' % remote], capture_output=True, timeout=30)
        for f in (local, back):
            try:
                os.unlink(f)
            except OSError:
                pass

        if not verified:
            return {"ok": False, "stage": "verify",
                    "error": "The file read back different from what was written."}
        return {"ok": True, "wrote": token, "verified": True,
                "target": "//%s/%s/%s" % (host, share, remote_dir)}
    except FileNotFoundError:
        raise SmbToolMissing()
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "timeout",
                "error": "The share did not respond within 45 seconds."}
    finally:
        _forget(authfile)
        try:
            os.unlink(local)
        except OSError:
            pass


def _clean(s):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s[:300] or "unknown error"


# ─────────────────────────── moving a finished rip ───────────────────────────
#
# Everything below is the transfer half of the rip pipeline (D11). It deliberately
# stays on `smbclient` rather than taking an SMB library: requirements.txt already
# refuses native builds on this hardware, and `cryptography` -- which every pure-Python
# SMB client depends on -- is exactly the kind of wheel whose absence turns into a box
# that installed fine and cannot upload anything.
#
# The cost of that choice is that smbclient has no way to append to a remote file, so
# **byte-level follow-copy is not implemented here**. D11's window needs a transport
# that can write at an offset, and it is gated on R8 anyway (whether MakeMKV writes
# linearly at all). What exists today is whole-file transfer with honest progress; the
# seam for follow-copy is `Transport.supports_follow_copy`.

MOCK_SHARE_ROOT = "/tmp/riparr-mock-share"


def _mock_path(host, share, remote):
    p = os.path.join(MOCK_SHARE_ROOT, host or "host", share or "share",
                     (remote or "").strip("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


class Transport:
    """One share, opened once, used for the length of a transfer.

    Every call shells out to a fresh `smbclient`, which is slower than holding a
    session open and is the right trade here: a long rip must not be holding a socket
    to a NAS that is allowed to go to sleep mid-job (D11 backpressure assumes the
    share can vanish and come back).
    """

    supports_follow_copy = False        # see the note above; gated on R8

    def __init__(self, share):
        self.host = share["host"]
        self.share = share["path"].strip("/").split("/")[0]
        sub = share["path"].strip("/").split("/")[1:]
        self.root = "/".join(sub)
        self.username = share.get("username") or ""
        self.password = share.get("password") or ""

    # ── plumbing ──

    def _remote(self, name):
        return "%s/%s" % (self.root, name.strip("/")) if self.root else name.strip("/")

    def _run(self, command, timeout=120):
        auth, authfile = _auth_args(self.username, self.password)
        try:
            p = subprocess.run(
                [SMBCLIENT, "//%s/%s" % (self.host, self.share)] + auth + ["-c", command],
                capture_output=True, text=True, timeout=timeout)
            return p.returncode, (p.stdout or ""), (p.stderr or "")
        except FileNotFoundError:
            raise SmbToolMissing()
        except subprocess.TimeoutExpired:
            return 1, "", "The share did not respond within %ds." % timeout
        finally:
            _forget(authfile)

    # ── operations ──

    def mkdirs(self, relative_dir):
        """Create a directory path one segment at a time.

        smbclient's `mkdir` is not recursive and returns an error for a directory that
        already exists, so both are expected outcomes and neither is worth reporting.
        """
        parts = [p for p in self._remote(relative_dir).split("/") if p]
        if P.MOCK:
            os.makedirs(_mock_path(self.host, self.share, "/".join(parts)), exist_ok=True)
            return True
        path = ""
        cmds = []
        for part in parts:
            path = "%s/%s" % (path, part) if path else part
            cmds.append('mkdir "%s"' % path)
        if cmds:
            self._run(";".join(cmds))
        return True

    def size(self, name):
        """Bytes currently at the destination, or None if it is not there.

        Called while a put is in flight to drive the progress bar: SMB writes stream
        into the file, so a partial size is a real answer, not a lie.
        """
        if P.MOCK:
            p = _mock_path(self.host, self.share, self._remote(name))
            return os.path.getsize(p) if os.path.exists(p) else None
        remote = self._remote(name)
        rc, out, _ = self._run('ls "%s"' % remote, timeout=45)
        if rc != 0:
            return None
        base = remote.split("/")[-1]
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith(base):
                continue
            m = re.search(r"\s(\d+)\s+\w{3}\s+\w{3}", line)
            if m:
                return int(m.group(1))
        return None

    def put(self, local_path, name, progress=None, cancel=None, poll=4.0):
        """Upload a whole file, reporting progress by watching it grow.

        `progress(bytes_sent, bytes_total)` is called from a watcher thread roughly
        every `poll` seconds. Without it the queue shows a bar that does not move for
        three hours, which reads as a hang and gets the cable pulled -- the exact
        failure the amber LED exists to prevent.
        """
        total = os.path.getsize(local_path)
        remote = self._remote(name)
        self.mkdirs(os.path.dirname(name) or "")

        stop = threading.Event()

        def watch():
            while not stop.wait(poll):
                try:
                    n = self.size(name)
                except Exception:
                    continue
                if n is not None and progress:
                    progress(min(n, total), total)

        watcher = threading.Thread(target=watch, name="riparr-put-watch", daemon=True)
        watcher.start()
        try:
            if P.MOCK:
                dest = _mock_path(self.host, self.share, remote)
                with open(local_path, "rb") as src, open(dest, "wb") as out:
                    while True:
                        if cancel is not None and cancel.is_set():
                            return {"ok": False, "error": "Cancelled"}
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        out.flush()
                        time.sleep(0.02)      # a visible, unhurried mock transfer
                if progress:
                    progress(total, total)
                return {"ok": True, "bytes": total, "target": self.describe(name)}

            rc, out, err = self._run('put "%s" "%s"' % (local_path, remote),
                                     timeout=max(600, int(total / (200 * 1024)) + 600))
            if rc != 0:
                return {"ok": False, "error": _clean(err or out)}
            if progress:
                progress(total, total)
            return {"ok": True, "bytes": total, "target": self.describe(name)}
        finally:
            stop.set()

    def fetch(self, name, local_path):
        if P.MOCK:
            src = _mock_path(self.host, self.share, self._remote(name))
            if not os.path.exists(src):
                return {"ok": False, "error": "Not found on the share"}
            with open(src, "rb") as a, open(local_path, "wb") as b:
                b.write(a.read())
            return {"ok": True}
        rc, out, err = self._run('get "%s" "%s"' % (self._remote(name), local_path),
                                 timeout=1800)
        if rc != 0:
            return {"ok": False, "error": _clean(err or out)}
        return {"ok": True}

    def delete(self, name):
        if P.MOCK:
            p = _mock_path(self.host, self.share, self._remote(name))
            if os.path.exists(p):
                os.unlink(p)
            return True
        self._run('del "%s"' % self._remote(name), timeout=60)
        return True

    def reachable(self):
        if P.MOCK:
            return True
        rc, _, _ = self._run("ls", timeout=20)
        return rc == 0

    def describe(self, name):
        return "//%s/%s/%s" % (self.host, self.share, self._remote(name))


def verify_remote(transport, name, local_path, expect_sha=None, progress=None):
    """Read the file back off the share and prove it matches (D6).

    An SMB write returning success is not evidence of a good file -- which is the whole
    reason the setup write-test reads back too. This is the same argument applied to
    the thing that actually matters.

    Size is checked first because it is nearly free and catches the common case (a
    truncated transfer). The hash is the expensive half and is what catches the
    uncommon one.
    """
    total = os.path.getsize(local_path)
    remote_size = transport.size(name)
    if remote_size is None:
        return {"ok": False, "error": "The file isn't on the share."}
    if remote_size != total:
        return {"ok": False,
                "error": "The share has %d bytes; the rip is %d." % (remote_size, total)}

    if expect_sha is None:
        expect_sha = sha256_file(local_path)

    # Next to the rip, not in /tmp. On the appliance /tmp is tmpfs -- 485 MB of RAM --
    # and this reads the *whole file* back to hash it, so a 4.5 GB DVD filled the RAM
    # disk and smbclient reported NT_STATUS_DISK_FULL. That reads as "your NAS is
    # full", which is the wrong machine entirely: the upload had already succeeded and
    # the size check had already passed.
    #
    # Streaming the read-back through the hash without storing it would avoid the
    # second copy, but smbclient's parallel_read writes at offsets and needs a
    # seekable destination, so a pipe is not an option.
    #
    # Consequence worth knowing: verification needs as much free space as the title
    # itself, on top of the rip. Peak staging is 2x the largest title, not 1x.
    fd, tmp = tempfile.mkstemp(suffix=".verify",
                               dir=os.path.dirname(local_path) or None)
    os.close(fd)
    try:
        r = transport.fetch(name, tmp)
        if not r.get("ok"):
            return {"ok": False, "error": "Couldn't read it back: %s" % r.get("error")}
        got = sha256_file(tmp, progress=progress)
        if got != expect_sha:
            return {"ok": False,
                    "error": "The file on the share doesn't match what was written."}
        return {"ok": True, "sha256": got}
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def sha256_file(path, progress=None, chunk=1 << 20):
    h = hashlib.sha256()
    done = 0
    total = os.path.getsize(path)
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
            done += len(b)
            if progress:
                progress(done, total)
    return h.hexdigest()
