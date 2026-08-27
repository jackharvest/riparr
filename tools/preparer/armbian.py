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

# Homebrew keeps e2fsprogs keg-only, so it is not on PATH by default. On Linux it is in
# every distribution and lives in an sbin that an unprivileged PATH often omits -- which
# is the same problem from the other direction, so both are listed rather than trusting
# `which`. There is no entry for Windows because there is no debugfs for Windows; see
# `core.missing_tools`, which refuses that combination before the card is touched.
DEBUGFS_CANDIDATES = [
    "/opt/homebrew/opt/e2fsprogs/sbin/debugfs",     # macOS, Apple silicon
    "/usr/local/opt/e2fsprogs/sbin/debugfs",        # macOS, Intel
    "/opt/homebrew/sbin/debugfs",
    "/sbin/debugfs",                                # Linux, most distributions
    "/usr/sbin/debugfs",                            # Linux, merged-usr
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
    """systemd-networkd is the stack on this image -- Netplan renders to it and no
    NetworkManager binary is installed.

    MulticastDNS=yes is what makes `riparr.local` resolvable. The image has no avahi
    and `nsswitch.conf` has no mdns entry, so systemd-resolved is the only mDNS
    responder available -- and networkd links default it to *off*, per-link, whatever
    the global setting says. Without this line the box joins the network correctly and
    is still unreachable by name, which looks exactly like a boot failure."""
    return ("[Match]\n"
            "Name=wlan0\n"
            "\n"
            "[Network]\n"
            "DHCP=yes\n"
            "MulticastDNS=yes\n"
            "\n"
            "[DHCPv4]\n"
            "RouteMetric=20\n")


def resolved_conf():
    """The global half of the same switch. resolved.conf ships it commented out, so
    this only restates the upstream default -- but it costs one file and removes any
    dependence on what that default happens to be in this build."""
    return ("# Generated by the Riparr Preparer.\n"
            "[Resolve]\n"
            "MulticastDNS=yes\n")


def ramlog_conf():
    """Turn off armbian-ramlog.

    Armbian mounts /var/log as a 50 MB tmpfs and syncs it down to /var/log.hdd on a
    timer and at clean shutdown. That is a good trade for a board you can log into: it
    spares the SD card. It is a terrible trade for a first boot you cannot log into,
    because pulling the power -- the only way to stop a headless box that never came up
    -- discards every line of evidence about why it never came up. The card then reads
    as pristine, which is indistinguishable from never having booted at all.

    This appliance is headless by design (D2), so the first boot is exactly the boot
    that must leave a record. Wear is bounded by the journal caps below."""
    return ("# Managed by the Riparr Preparer.\n"
            "# Logs go to the card, not to a ramdisk -- a headless first boot has to\n"
            "# leave evidence behind that survives losing power. See JOURNAL.md.\n"
            "ENABLED=false\n"
            "SIZE=50M\n"
            "USE_RSYNC=true\n"
            "XTRA_RSYNC_FROM=()\n")


def journald_conf():
    """Persist the journal, and cap it so it cannot eat the card.

    journald ships `Storage=auto`, which persists only when /var/log/journal exists --
    it does, but on a ramlog system that directory is the ramdisk, so `auto` silently
    means volatile. Being explicit costs nothing and removes the coupling."""
    return ("# Generated by the Riparr Preparer.\n"
            "[Journal]\n"
            "Storage=persistent\n"
            "SystemMaxUse=64M\n"
            "SystemMaxFileSize=16M\n"
            "SystemMaxFiles=8\n")


def wpa_dropin():
    """wlan0 does not exist until the Wi-Fi driver is loaded, and on this board that
    happens from aw859a-wifi.service (`modprobe sprdwl_ng`) -- another
    multi-user.target unit, so it races this one. Debian's wpa_supplicant@.service has
    a hard `Requires=` on the interface device, which loses that race permanently.
    Drop the requirement and retry instead."""
    return ("[Unit]\n"
            "Requires=\n"
            "After=\n"
            "\n"
            "[Service]\n"
            "Restart=always\n"
            "RestartSec=5\n")


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
        f_mdns = _put(tmp, "resolved-mdns.conf", resolved_conf())
        f_ram = _put(tmp, "armbian-ramlog", ramlog_conf())
        f_jrnl = _put(tmp, "journald-persistent.conf", journald_conf())
        f_drop = _put(tmp, "wpa-retry.conf", wpa_dropin())
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
                   "/etc/systemd/resolved.conf.d/10-riparr-mdns.conf",
                   "/etc/default/armbian-ramlog",
                   "/etc/systemd/journald.conf.d/10-riparr-persistent.conf",
                   "/etc/systemd/system/wpa_supplicant@wlan0.service.d/10-riparr-retry.conf",
                   "/root/.ssh/authorized_keys", "/boot/riparr.conf",
                   "/root/.not_logged_in_yet"]
        _run_lenient(debugfs, target, "".join("rm %s\n" % t for t in targets))
        # All three are "fine if it already exists" -- see the note above.
        _run_lenient(debugfs, target,
                     "mkdir /root/.ssh\n"
                     "mkdir /etc/systemd/resolved.conf.d\n"
                     "mkdir /etc/systemd/system/wpa_supplicant@wlan0.service.d\n"
                     "mkdir /etc/systemd/journald.conf.d\n")

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

sif /etc/systemd/resolved.conf.d mode 040755
sif /etc/systemd/resolved.conf.d uid 0
sif /etc/systemd/resolved.conf.d gid 0
write {mdns} /etc/systemd/resolved.conf.d/10-riparr-mdns.conf
sif /etc/systemd/resolved.conf.d/10-riparr-mdns.conf mode 0100644
sif /etc/systemd/resolved.conf.d/10-riparr-mdns.conf uid 0
sif /etc/systemd/resolved.conf.d/10-riparr-mdns.conf gid 0

sif /etc/systemd/system/wpa_supplicant@wlan0.service.d mode 040755
sif /etc/systemd/system/wpa_supplicant@wlan0.service.d uid 0
sif /etc/systemd/system/wpa_supplicant@wlan0.service.d gid 0
write {drop} /etc/systemd/system/wpa_supplicant@wlan0.service.d/10-riparr-retry.conf
sif /etc/systemd/system/wpa_supplicant@wlan0.service.d/10-riparr-retry.conf mode 0100644
sif /etc/systemd/system/wpa_supplicant@wlan0.service.d/10-riparr-retry.conf uid 0
sif /etc/systemd/system/wpa_supplicant@wlan0.service.d/10-riparr-retry.conf gid 0

sif /root/.ssh mode 040700
sif /root/.ssh uid 0
sif /root/.ssh gid 0
write {keys} /root/.ssh/authorized_keys
sif /root/.ssh/authorized_keys mode 0100600
sif /root/.ssh/authorized_keys uid 0
sif /root/.ssh/authorized_keys gid 0

write {ram} /etc/default/armbian-ramlog
sif /etc/default/armbian-ramlog mode 0100644
sif /etc/default/armbian-ramlog uid 0
sif /etc/default/armbian-ramlog gid 0

sif /etc/systemd/journald.conf.d mode 040755
sif /etc/systemd/journald.conf.d uid 0
sif /etc/systemd/journald.conf.d gid 0
write {jrnl} /etc/systemd/journald.conf.d/10-riparr-persistent.conf
sif /etc/systemd/journald.conf.d/10-riparr-persistent.conf mode 0100644
sif /etc/systemd/journald.conf.d/10-riparr-persistent.conf uid 0
sif /etc/systemd/journald.conf.d/10-riparr-persistent.conf gid 0

write {conf} /boot/riparr.conf
sif /boot/riparr.conf mode 0100644
sif /boot/riparr.conf uid 0
sif /boot/riparr.conf gid 0
""".format(host=f_host, hosts=f_hosts, wpa=f_wpa, net=f_net, keys=f_keys,
           conf=f_conf, mdns=f_mdns, drop=f_drop, ram=f_ram, jrnl=f_jrnl)
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
    n = cat("/etc/systemd/network/10-wlan0.network")
    checks.append(("wlan0 network unit", "DHCP=yes" in n, ""))
    checks.append(("mDNS responder on wlan0 (riparr.local)", "MulticastDNS=yes" in n, ""))
    checks.append(("mDNS enabled globally", "MulticastDNS=yes" in
                   cat("/etc/systemd/resolved.conf.d/10-riparr-mdns.conf"), ""))
    checks.append(("ramlog off (first boot leaves evidence)", "ENABLED=false" in
                   cat("/etc/default/armbian-ramlog"), ""))
    checks.append(("journal persistent + capped", "Storage=persistent" in
                   cat("/etc/systemd/journald.conf.d/10-riparr-persistent.conf"), ""))
    checks.append(("wpa_supplicant retries until wlan0 exists", "Restart=always" in
                   cat("/etc/systemd/system/wpa_supplicant@wlan0.service.d/"
                       "10-riparr-retry.conf"), ""))
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
