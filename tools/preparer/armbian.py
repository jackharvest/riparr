"""
Headless provisioning for Armbian on the Orange Pi Zero 2W.

Armbian has a preset mechanism -- PRESET_* variables in /root/.not_logged_in_yet -- and
it is useless to us. `armbian-firstlogin` gates every one of those presets, including
the automated network configuration, behind `tty -s`. On a box with no screen and no
serial adapter nothing ever calls it, so nothing is ever applied. PRESET_USER_KEY is
worse still: the script curls it, so it wants a URL, which needs the network that the
preset was supposed to bring up.

There is also nowhere to drop a file the way Raspberry Pi OS lets you. Allwinner images
are a single ext4 partition with U-Boot in the raw sectors ahead of it -- no FAT boot
partition, and macOS cannot mount ext4.

So we write the configuration into the filesystem directly with debugfs, which reads and
writes ext4 without mounting anything and without a kernel driver. The result is a card
that joins WiFi and accepts our SSH key on its very first boot, with no console.
"""
import os
import re
import shutil
import subprocess
import tempfile

# Homebrew keeps e2fsprogs keg-only, so it is not on PATH by default.
DEBUGFS_CANDIDATES = [
    "/opt/homebrew/opt/e2fsprogs/sbin/debugfs",
    "/usr/local/opt/e2fsprogs/sbin/debugfs",
    "/opt/homebrew/sbin/debugfs",
    "/sbin/debugfs",
]


def find_debugfs():
    for p in DEBUGFS_CANDIDATES:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return shutil.which("debugfs")


class DebugfsError(RuntimeError):
    pass


def _run(debugfs, target, script):
    """Feed a command script to debugfs -w and fail loudly on any error it reports.

    debugfs exits 0 even when individual commands fail, so the output has to be read.
    Silent partial provisioning is the exact failure this whole module exists to avoid.
    """
    cmd = [debugfs, "-w", "-f", "/dev/stdin", target]
    p = subprocess.run(cmd, input=script, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    bad = [ln for ln in out.splitlines()
           if any(k in ln for k in ("File not found", "File exists", "Filesystem not open",
                                    "Could not", "error", "Error", "Permission denied",
                                    "read-only", "Invalid"))
           and "debugfs:" not in ln.split(":")[0].lower()[:9]]
    real = [ln for ln in bad if not ln.startswith("debugfs 1.")]
    if real:
        raise DebugfsError("\n".join(real[:12]) + "\n\n--- full output ---\n" + out[-1500:])
    return out


def _run_lenient(debugfs, target, script):
    """Run commands whose failure is expected and fine -- deleting what may not exist."""
    subprocess.run([debugfs, "-w", "-f", "/dev/stdin", target],
                   input=script, capture_output=True, text=True)


def _put(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w") as f:
        f.write(content)
    return path


def wpa_conf(ssid, psk_hex, country="US"):
    """wpa_supplicant config. An unquoted psk is a 64-hex PSK, so no passphrase lands
    on the card -- same property the Raspberry Pi path had."""
    return ('ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev\n'
            'update_config=1\n'
            'country=%s\n'
            '\n'
            'network={\n'
            '\tssid="%s"\n'
            '\tpsk=%s\n'
            '\tscan_ssid=1\n'
            '}\n' % (country, ssid.replace('"', '\\"'), psk_hex))


def network_unit():
    """systemd-networkd is the stack on this image -- NetworkManager is not installed."""
    return ("[Match]\n"
            "Name=wlan0\n"
            "\n"
            "[Network]\n"
            "DHCP=yes\n"
            "\n"
            "[DHCPv4]\n"
            "RouteMetric=20\n")


def hosts_file(hostname):
    return ("127.0.0.1\tlocalhost\n"
            "127.0.1.1\t%s\n"
            "::1\t\tlocalhost %s ip6-localhost ip6-loopback\n"
            "fe00::0\t\tip6-localnet\n"
            "ff00::0\t\tip6-mcastprefix\n"
            "ff02::1\t\tip6-allnodes\n"
            "ff02::2\t\tip6-allrouters\n" % (hostname, hostname))


def cfg_from_custom_toml(toml_path, port=9797):
    """Reuse the Preparer's own custom.toml as the source of truth.

    The GUI already collects and derives everything this needs -- hostname, SSID, the
    PBKDF2 PSK, the public key -- and writes it into custom.toml. Parsing it back is
    better than a second input path that could drift out of step with the first.
    """
    src = open(toml_path).read()

    def val(section, key, default=None):
        m = re.search(r"\[%s\](.*?)(?=\n\[|\Z)" % section, src, re.S)
        if not m:
            return default
        m2 = re.search(r'^%s\s*=\s*"(.*)"\s*$' % key, m.group(1), re.M)
        return m2.group(1) if m2 else default

    m = re.search(r'authorized_keys\s*=\s*\[\s*"(.*?)"', src, re.S)
    cfg = {
        "hostname": val("system", "hostname", "riparr"),
        "ssid": val("wlan", "ssid"),
        "psk": val("wlan", "password"),
        "country": val("wlan", "country", "US"),
        "authorized_key": m.group(1) if m else "",
        "port": port,
    }
    missing = [k for k in ("ssid", "psk", "authorized_key") if not cfg[k]]
    if missing:
        raise DebugfsError("custom.toml is missing %s, so the card cannot be "
                           "provisioned headlessly." % ", ".join(missing))
    return cfg


def copy_makemkv(target, srcdir, debugfs=None):
    """Put the MakeMKV tarballs on the card, as the Raspberry Pi path did.

    There is no FAT partition to drop them on, so they go into /root/makemkv inside the
    root filesystem. ~25 MB against ~180 MB free before the first-boot resize.
    """
    debugfs = debugfs or find_debugfs()
    names = sorted(n for n in os.listdir(srcdir) if n.endswith(".tar.gz"))
    if not names:
        return []
    _run_lenient(debugfs, target,
                 "".join("rm /root/makemkv/%s\n" % n for n in names) + "mkdir /root/makemkv\n")
    lines = ["sif /root/makemkv mode 040755",
             "sif /root/makemkv uid 0", "sif /root/makemkv gid 0"]
    for n in names:
        lines += ["write %s /root/makemkv/%s" % (os.path.join(srcdir, n), n),
                  "sif /root/makemkv/%s mode 0100644" % n,
                  "sif /root/makemkv/%s uid 0" % n,
                  "sif /root/makemkv/%s gid 0" % n]
    _run(debugfs, target, "\n".join(lines) + "\n")
    return names


def provision(target, cfg, debugfs=None):
    """Write headless configuration into an ext4 root filesystem.

    `target` is the root partition -- /dev/rdiskNsM, or a file holding just that
    partition. `cfg` needs: hostname, ssid, psk, country, authorized_key, port.
    """
    debugfs = debugfs or find_debugfs()
    if not debugfs:
        raise DebugfsError(
            "debugfs is not installed, so the card cannot be provisioned.\n\n"
            "    brew install e2fsprogs\n\n"
            "It reads and writes ext4 without mounting it, which is the only way to "
            "configure an Allwinner image from macOS.")

    tmp = tempfile.mkdtemp(prefix="riparr-armbian-")
    try:
        f_host = _put(tmp, "hostname", cfg["hostname"] + "\n")
        f_hosts = _put(tmp, "hosts", hosts_file(cfg["hostname"]))
        f_wpa = _put(tmp, "wpa.conf", wpa_conf(cfg["ssid"], cfg["psk"],
                                               cfg.get("country", "US")))
        f_net = _put(tmp, "10-wlan0.network", network_unit())
        f_keys = _put(tmp, "authorized_keys", cfg["authorized_key"].strip() + "\n")
        f_conf = _put(tmp, "riparr.conf",
                      "# Generated by the Riparr Preparer.\n"
                      "RIPARR_PORT=%d\nRIPARR_HOSTNAME=%s\n"
                      % (int(cfg.get("port", 9797)), cfg["hostname"]))

        # debugfs `write` refuses to overwrite an existing name, so everything is
        # unlinked first. This pass is allowed to fail -- on a fresh card most of these
        # do not exist yet -- and running it makes provisioning idempotent, so a retry
        # after a partial failure works instead of dying on "File exists".
        targets = ["/etc/hostname", "/etc/hosts",
                   "/etc/wpa_supplicant/wpa_supplicant-wlan0.conf",
                   "/etc/systemd/network/10-wlan0.network",
                   "/etc/systemd/system/multi-user.target.wants/wpa_supplicant@wlan0.service",
                   "/root/.ssh/authorized_keys", "/boot/riparr.conf",
                   "/root/.not_logged_in_yet"]
        _run_lenient(debugfs, target, "".join("rm %s\n" % t for t in targets))
        _run_lenient(debugfs, target, "mkdir /root/.ssh\n")   # fine if it already exists

        script = """
write {host} /etc/hostname
sif /etc/hostname mode 0100644
sif /etc/hostname uid 0
sif /etc/hostname gid 0

write {hosts} /etc/hosts
sif /etc/hosts mode 0100644
sif /etc/hosts uid 0
sif /etc/hosts gid 0

write {wpa} /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
sif /etc/wpa_supplicant/wpa_supplicant-wlan0.conf mode 0100600
sif /etc/wpa_supplicant/wpa_supplicant-wlan0.conf uid 0
sif /etc/wpa_supplicant/wpa_supplicant-wlan0.conf gid 0

write {net} /etc/systemd/network/10-wlan0.network
sif /etc/systemd/network/10-wlan0.network mode 0100644
sif /etc/systemd/network/10-wlan0.network uid 0
sif /etc/systemd/network/10-wlan0.network gid 0

symlink /etc/systemd/system/multi-user.target.wants/wpa_supplicant@wlan0.service /lib/systemd/system/wpa_supplicant@.service

sif /root/.ssh mode 040700
sif /root/.ssh uid 0
sif /root/.ssh gid 0
write {keys} /root/.ssh/authorized_keys
sif /root/.ssh/authorized_keys mode 0100600
sif /root/.ssh/authorized_keys uid 0
sif /root/.ssh/authorized_keys gid 0

write {conf} /boot/riparr.conf
sif /boot/riparr.conf mode 0100644
sif /boot/riparr.conf uid 0
sif /boot/riparr.conf gid 0
""".format(host=f_host, hosts=f_hosts, wpa=f_wpa, net=f_net, keys=f_keys, conf=f_conf)
        return _run(debugfs, target, script)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verify(target, cfg, debugfs=None):
    """Read back every file that was written. Returns a list of (label, ok, detail)."""
    debugfs = debugfs or find_debugfs()
    checks = []

    def cat(path):
        p = subprocess.run([debugfs, "-R", "cat %s" % path, target],
                           capture_output=True, text=True)
        return p.stdout

    def stat(path):
        p = subprocess.run([debugfs, "-R", "stat %s" % path, target],
                           capture_output=True, text=True)
        return p.stdout

    checks.append(("hostname", cat("/etc/hostname").strip() == cfg["hostname"],
                   cat("/etc/hostname").strip()))
    w = cat("/etc/wpa_supplicant/wpa_supplicant-wlan0.conf")
    checks.append(("wifi ssid", ('ssid="%s"' % cfg["ssid"]) in w, cfg["ssid"]))
    checks.append(("wifi psk (64-hex, no passphrase)",
                   ("psk=" + cfg["psk"]) in w and cfg["psk"].isalnum() and len(cfg["psk"]) == 64,
                   "%d hex chars" % len(cfg["psk"])))
    checks.append(("wpa conf mode 0600", "Mode:  0600" in
                   stat("/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"), ""))
    checks.append(("wlan0 network unit", "DHCP=yes" in
                   cat("/etc/systemd/network/10-wlan0.network"), ""))
    st = stat("/etc/systemd/system/multi-user.target.wants/wpa_supplicant@wlan0.service")
    checks.append(("wpa_supplicant@wlan0 enabled", "symlink" in st.lower(), ""))
    k = cat("/root/.ssh/authorized_keys")
    checks.append(("root ssh key", cfg["authorized_key"].strip() in k, k.strip()[:40]))
    checks.append(("authorized_keys mode 0600", "Mode:  0600" in
                   stat("/root/.ssh/authorized_keys"), ""))
    checks.append(("riparr.conf port", "RIPARR_PORT=%d" % int(cfg.get("port", 9797)) in
                   cat("/boot/riparr.conf"), ""))
    checks.append((".not_logged_in_yet removed (no login wizard)",
                   "File not found" in stat("/root/.not_logged_in_yet") or
                   not stat("/root/.not_logged_in_yet").strip(), ""))
    return checks
