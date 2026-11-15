"""One file to ask for when something does not work on somebody else's network.

Every support conversation used to start the same way: screenshots of the settings
page, then "can you SSH in and run...", which most people cannot, then guessing. The
facts that decide a NAS problem -- what the mount actually said, whether the name
resolves, whether port 445 answers, which SMB version the NAS speaks -- were all on the
box, mostly in places only root could read, and none of them on any page.

So: one button, one zip, everything we would ask for, with every secret the box knows
removed before it is written. Nothing is sent anywhere. The person downloads it and
chooses to send it.

Everything here is best effort and bounded. A diagnostics download that hangs on the
very NAS it is meant to diagnose would be worse than none, so every probe has a short
timeout, and every section that fails says why in the place its output would have been.
"""
import io
import json
import os
import re
import socket
import struct
import subprocess
import time
import zipfile

from . import __version__, db
from . import platform as P
from . import system as SY

# Settings whose values are secrets, or are somebody's address. Matched on the key name,
# so a setting added later with "token" or "password" in its name is covered without
# anyone remembering this file exists.
_SECRET_KEY = re.compile(
    r"pass|token|secret|webhook|^makemkv_key$|^smtp_(username|from|to)$|_url$|email",
    re.I)
REDACTED = "[redacted]"


def _secrets():
    """Every secret value the box holds, so it can be scrubbed from logs as well.

    Logs are written by code that does not know it is being collected, so redacting the
    settings is not enough: the value has to be removed wherever it appears.
    """
    found = set()
    try:
        for r in db.conn().execute("SELECT password FROM shares"):
            if r[0]:
                found.add(str(r[0]))
    except Exception:
        pass
    for k, v in db.all_settings().items():
        if _SECRET_KEY.search(k) and isinstance(v, str) and v:
            found.add(v)
    # Wi-Fi: the box's own settings file holds the passphrase.
    for path in ("/boot/firmware/riparr.conf", "/boot/riparr.conf"):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    k, _, v = line.partition("=")
                    if re.search(r"PASS|PSK|KEY|TOKEN|SECRET", k, re.I):
                        v = v.strip().strip("'\"")
                        if v:
                            found.add(v)
        except OSError:
            pass
    # Short values would scrub ordinary words out of the logs ("admin", "1234" in a
    # timestamp); a password that short is not worth protecting from its owner's own
    # support request, and redacting it everywhere would make the logs unreadable.
    return sorted((s for s in found if len(s) >= 5), key=len, reverse=True)


def scrub(text, secrets):
    for s in secrets:
        text = text.replace(s, REDACTED)
    # Credentials inside URLs and key=value pairs written by anything we did not foresee.
    text = re.sub(r"(?i)\b(password|passwd|pass|psk|token|secret)(\s*[=:]\s*)\S+",
                  r"\1\2" + REDACTED, text)
    text = re.sub(r"(//[^/\s:@]+:)[^@\s/]+@", r"\1" + REDACTED + "@", text)
    # MakeMKV keys have a fixed shape; catch one that came from somewhere unexpected.
    text = re.sub(r"\bT-[A-Za-z0-9@_]{40,}", "T-" + REDACTED, text)
    return text


def _cmd(argv, timeout=6):
    """Run something and return what it said -- including that it could not run."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode:
            out += "\n(exit %d)" % r.returncode
        return out.rstrip() or "(no output)"
    except FileNotFoundError:
        return "(%s is not installed)" % argv[0]
    except subprocess.TimeoutExpired:
        return "(timed out after %ds)" % timeout
    except Exception as e:                        # noqa: BLE001 -- diagnostics never raise
        return "(failed: %s)" % e


def _read(path, limit=200000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError as e:
        return "(%s)" % e.strerror


def _section(title, body):
    return "=== %s ===\n%s\n\n" % (title, body.rstrip())


# ───────────────────────────── the network, per NAS ─────────────────────────────

def nbns_lookup(name, timeout=2.0):
    """A NetBIOS name query, broadcast. The same one tools/mount-library.sh makes.

    Separate from DNS on purpose: the gap between the two is the most common reason a
    share tests fine and will not mount, so the bundle has to show both answers.
    """
    n = name.upper()[:15].ljust(15) + "\x00"
    enc = "".join(chr(0x41 + (ord(c) >> 4)) + chr(0x41 + (ord(c) & 15)) for c in n)
    tid = int.from_bytes(os.urandom(2), "big")
    q = (struct.pack(">HHHHHH", tid, 0x0110, 1, 0, 0, 0)
         + b"\x20" + enc.encode("latin-1") + b"\x00" + struct.pack(">HH", 0x20, 1))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        s.sendto(q, ("255.255.255.255", 137))
        while True:
            data, _ = s.recvfrom(1024)
            if len(data) < 62 or struct.unpack(">H", data[:2])[0] != tid:
                continue
            if not (data[2] & 0x80) or data[3] & 0x0F:
                continue
            return socket.inet_ntoa(data[58:62])
    except OSError:
        return None
    finally:
        s.close()


def _tcp(addr, port, timeout=3.0):
    t = time.time()
    try:
        with socket.create_connection((addr, port), timeout=timeout):
            return "open (%d ms)" % ((time.time() - t) * 1000)
    except socket.timeout:
        return "no answer in %ds" % timeout
    except OSError as e:
        return e.strerror or str(e)


def probe_host(host):
    """What this box can find out about one NAS without a password."""
    lines = ["host: %s" % host]
    addr = None
    if re.fullmatch(r"[0-9.]+", host):
        addr = host
        lines.append("typed as an IP address")
    else:
        try:
            addr = socket.getaddrinfo(host, 445, socket.AF_INET)[0][4][0]
            lines.append("DNS:      %s" % addr)
        except OSError as e:
            lines.append("DNS:      not found (%s)" % (e.strerror or e))
        nb = nbns_lookup(host)
        lines.append("NetBIOS:  %s" % (nb or "no answer"))
        addr = addr or nb
    if not addr:
        lines.append("Cannot test the ports: the name did not resolve either way.")
        return "\n".join(lines)
    lines.append("port 445 (SMB 2/3): %s" % _tcp(addr, 445))
    lines.append("port 139 (SMB 1 / NetBIOS): %s" % _tcp(addr, 139))
    lines.append("ping: " + _cmd(["ping", "-c", "2", "-W", "2", addr], timeout=6)
                 .splitlines()[-1])
    return "\n".join(lines)


# ───────────────────────────────── the bundle ─────────────────────────────────

def _summary():
    shares = db.list_shares()
    status = P.mount_status()
    out = []
    out.append(_section("Riparr", "\n".join([
        "version: %s" % __version__,
        "made:    %s" % time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "mock:    %s" % P.MOCK,
    ])))
    out.append(_section("Board and OS", "\n".join([
        _read("/proc/device-tree/model").strip("\x00\n") or "(no device-tree model)",
        _cmd(["uname", "-a"]),
        _read("/etc/os-release"),
        _cmd(["uptime"]),
    ])))
    out.append(_section("Memory and storage", "\n".join([
        _cmd(["free", "-m"]), _cmd(["df", "-h"])])))

    dest = []
    for kind in db.KINDS:
        share, folder = db.destination(kind)
        lib = P.library_status(share)
        dest.append("%-6s share=%s folder=%r mount=%s mounted=%s smb=%s" % (
            kind, share["id"] if share else None, folder, lib.get("mount"),
            lib.get("mounted"), lib.get("smb")))
        if lib.get("error"):
            dest.append("       last error: %s" % lib["error"])
            dest.append("       which means: %s" % lib.get("reason"))
    out.append(_section("Destinations", "\n".join(dest)))

    rows = []
    for s in shares:
        st = status.get(s["id"]) or {}
        rows.append("#%s %s  host=%s path=%r user=%r default=%s verified_at=%s" % (
            s["id"], s["name"], s["host"], s["path"], s["username"], s["is_default"],
            s["verified_at"]))
        if st:
            rows.append("    last mount attempt: ok=%s smb=%s addr=%s at=%s error=%s" % (
                st["ok"], st["smb"], st["addr"],
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["at"]))
                if st["at"] else None, st["error"]))
        else:
            rows.append("    no mount attempt recorded")
    out.append(_section("Shares (passwords not included)", "\n".join(rows) or "none"))

    hosts = sorted({s["host"] for s in shares if s.get("host")})
    out.append(_section("Can this box reach each NAS?",
                        "\n\n".join(probe_host(h) for h in hosts) or "no shares"))

    out.append(_section("Mounted network shares",
                        "\n".join(l for l in _read("/proc/mounts").splitlines()
                                  if " cifs " in l or "/srv/" in l) or "none"))
    out.append(_section("Network", "\n\n".join([
        _cmd(["ip", "-br", "addr"]), _cmd(["ip", "route"]),
        _read("/etc/resolv.conf"), _cmd(["iwconfig"], timeout=3)])))
    out.append(_section("SMB tools", "\n".join([
        _cmd(["mount.cifs", "-V"]), _cmd(["smbclient", "--version"]),
        _cmd(["modinfo", "-F", "version", "cifs"])])))

    comp = P.system_components()
    out.append(_section("System parts", "\n".join(
        "%-8s %s" % (c["state"], c["name"]) for c in comp.get("components", []))
        or json.dumps(comp)))

    units = ["riparr", "riparr-library", "riparr-remount", "riparr-netwatch",
             "riparr-provision"]
    out.append(_section("Services", _cmd(
        ["systemctl", "--no-pager", "--lines=0", "status"] + units, timeout=8)))
    return "".join(out)


def _settings():
    s = db.all_settings()
    return json.dumps({k: (REDACTED if _SECRET_KEY.search(k) and v else v)
                       for k, v in sorted(s.items())}, indent=2, default=str)


def _events():
    rows = SY.events(limit=1000).get("events", [])
    return "\n".join("%s %-7s %-12s %s" % (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("at") or 0)),
        r.get("level"), r.get("component"), r.get("message")) for r in reversed(rows))


def _journal():
    """The root side's own words, if this account may read them.

    It usually may not -- the service is not in systemd-journal -- which is why the
    mount script now writes mount.txt as well. Asked anyway: on a box where it works
    it also has the boot-time mounts from before the log file existed.
    """
    return _cmd(["journalctl", "--no-pager", "-b", "-n", "400", "-o", "short-iso",
                 "-u", "riparr-library", "-u", "riparr-remount", "-u", "riparr-provision",
                 "-u", "riparr-netwatch"], timeout=10)


def build():
    """(filename, bytes) of the diagnostics zip."""
    secrets = _secrets()
    parts = [
        ("README.txt",
         "Riparr diagnostics, version %s.\n\n"
         "Passwords, keys, tokens and webhook addresses have been replaced with %s.\n"
         "Machine names and IP addresses on your network are included, because they\n"
         "are usually the answer. Nothing has been sent anywhere: this file goes only\n"
         "where you choose to send it.\n\n"
         "summary.txt   the box, its shares, and whether it can reach each NAS\n"
         "mount.txt     every attempt to connect a share, with the NAS's own error\n"
         "events.txt    the Events page\n"
         "settings.json settings, secrets removed\n"
         "journal.txt   the system log for the mount services, where readable\n"
         "logs/         Riparr's own logs\n" % (__version__, REDACTED)),
    ]
    sections = [("summary.txt", _summary), ("settings.json", _settings),
                ("events.txt", _events), ("journal.txt", _journal),
                ("tasks.json", lambda: json.dumps(SY.task_history(limit=50), indent=2,
                                                  default=str))]
    for name, fn in sections:
        try:
            parts.append((name, fn()))
        except Exception as e:                    # noqa: BLE001
            parts.append((name, "(could not be collected: %r)" % e))

    mlog = os.path.join(SY.LOG_DIR, "mount.txt")
    parts.append(("mount.txt", _read(mlog) if os.path.exists(mlog) else
                  "(no mount log yet: the mount script that writes it arrives with the "
                  "system update. Press Reconnect once and download this again.)"))
    for f in SY.log_files():
        if f["name"] == "mount.txt":
            continue
        # The current files and the most recent rotation of each: enough to see the
        # problem happen, and the zip stays small enough to attach to an email.
        if re.search(r"\.txt(\.1)?$", f["name"]):
            parts.append(("logs/" + f["name"], _read(os.path.join(SY.LOG_DIR, f["name"]),
                                                     limit=2 * 1024 * 1024)))

    host = socket.gethostname().split(".")[0] or "riparr"
    stamp = time.strftime("%Y-%m-%d_%H%M")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            z.writestr("riparr-diagnostics/" + name, scrub(text, secrets))
    return "riparr-diagnostics_%s_v%s_%s.zip" % (host, __version__, stamp), buf.getvalue()
