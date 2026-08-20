"""
Finding the user's network share, and proving we can actually write to it.

"My share path was wrong" is the dominant support burden for this class of device, and
it usually surfaces at 3am on the first rip rather than during setup. So discovery is
automatic and a real file is written and read back before setup is allowed to continue.
"""
import concurrent.futures
import os
import re
import socket
import subprocess
import tempfile
import time

from . import platform as P

SMB_PORT = 445


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
    cmd = ["smbclient", "-L", "//%s" % host, "-g"]
    cmd += (["-U", "%s%%%s" % (username, password)] if username else ["-N"])
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
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
    auth = ["-U", "%s%%%s" % (username, password)] if username else ["-N"]
    base = ["smbclient", "//%s/%s" % (host, share)] + auth + ["-c"]

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
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "timeout",
                "error": "The share did not respond within 45 seconds."}


def _clean(s):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s[:300] or "unknown error"
