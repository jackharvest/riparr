#!/usr/bin/env python3
"""
Riparr Flasher — prepares an SD card for a Riparr appliance.

Scans for nearby Wi-Fi, filters to what a Pi Zero 2W can actually join,
provisions hostname/user/SSH/Wi-Fi via custom.toml, and writes the image.
"""
import os, re, sys, json, time, hashlib, plistlib, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "preparer"))
import core          # single source of truth for PSK, $6$ hashing and the TOML schema
from riparr_ui import *
from riparr_ui import NoTTY
import riparr_ui as U

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = HERE          # where the image, ssh key and password file live
IMG = None             # resolved in main()
STEPS = 5

# ─────────────────────────────── Wi-Fi ───────────────────────────────

class Net:
    def __init__(self, ssid, bands=None, rssi=None, secure=True, saved=False, seen=True):
        self.ssid, self.bands, self.rssi = ssid, set(bands or []), rssi
        self.secure, self.saved, self.seen = secure, saved, seen
    @property
    def pi_ok(self):
        # Pi Zero 2W is 2.4GHz-only. Unknown band => allow, warn later.
        return ("2.4" in self.bands) or not self.bands

def scan_corewlan():
    """Real scan: SSID, band, RSSI, security. Requires pyobjc-framework-CoreWLAN."""
    import CoreWLAN
    iface = CoreWLAN.CWWiFiClient.sharedWiFiClient().interface()
    if iface is None: return {}
    nets, _ = iface.scanForNetworksWithSSID_error_(None, None)
    agg = {}
    for n in (nets or []):
        ssid = n.ssid()
        if not ssid: continue
        ch = n.wlanChannel()
        band = {1: "2.4", 2: "5", 3: "6"}.get(ch.channelBand() if ch else 0)
        try: secure = not n.supportsSecurity_(0)   # kCWSecurityNone
        except Exception: secure = True
        e = agg.get(ssid)
        if e is None:
            agg[ssid] = Net(ssid, [band] if band else [], n.rssiValue(), secure)
        else:
            if band: e.bands.add(band)
            if n.rssiValue() is not None and (e.rssi is None or n.rssiValue() > e.rssi):
                e.rssi = n.rssiValue()
    return agg

def scan_system_profiler():
    """Fallback. SSIDs may be '<redacted>' without Location Services."""
    try:
        out = subprocess.run(["system_profiler", "SPAirPortDataType"],
                             capture_output=True, text=True, timeout=45).stdout
    except Exception:
        return {}
    agg, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"^\s{12}([^\s:][^:]*):\s*$", line)
        if m:
            cur = m.group(1).strip()
            if cur.startswith("<"): cur = None
            elif cur not in agg: agg[cur] = Net(cur, [], None, True)
            continue
        if cur and "Channel:" in line:
            b = re.search(r"\((2|5|6)GHz", line)
            if b: agg[cur].bands.add({"2": "2.4", "5": "5", "6": "6"}[b.group(1)])
        if cur and "Security:" in line and "None" in line:
            agg[cur].secure = False
    return agg

def saved_networks():
    try:
        out = subprocess.run(["networksetup", "-listpreferredwirelessnetworks", "en0"],
                             capture_output=True, text=True, timeout=15).stdout
        return [l.strip() for l in out.splitlines()[1:] if l.strip()]
    except Exception:
        return []

def gather_networks():
    agg, method = {}, None
    try:
        agg = scan_corewlan()
        if agg: method = "live scan"
    except Exception:
        pass
    if not agg:
        agg = scan_system_profiler()
        if agg: method = "system_profiler"
    for s in saved_networks():
        if s in agg: agg[s].saved = True
        else: agg[s] = Net(s, [], None, True, saved=True, seen=False)
    if agg and not method: method = "saved networks"
    nets = list(agg.values())
    nets.sort(key=lambda n: (not n.pi_ok, n.rssi is None, -(n.rssi or -100), n.ssid.lower()))
    return nets, (method or "none")

def render_net(n, sel):
    label = n.ssid if len(n.ssid) <= 25 else n.ssid[:24] + "\u2026"
    name = f"{BOLD}{label}{RESET}" if sel else label
    if not n.pi_ok:
        return f"{GRY}{pad(label, 26)}5 GHz only — Pi Zero 2W can't see this{RESET}"
    bars = signal_bars(n.rssi)
    tags = []
    if "2.4" in n.bands and "5" in n.bands: tags.append(f"{GRN}2.4 + 5 GHz{RESET}")
    elif "2.4" in n.bands:                  tags.append(f"{GRN}2.4 GHz{RESET}")
    elif not n.bands:                       tags.append(f"{YEL}band unknown{RESET}")
    if n.saved and not n.seen: tags.append(f"{GRY}saved, not in range{RESET}")
    elif n.saved:              tags.append(f"{GRY}saved{RESET}")
    if not n.secure:           tags.append(f"{YEL}open{RESET}")
    return f"{bars}  {pad(name, 26)} {' · '.join(tags)}"

def choose_wifi(a=None):
    step(2, STEPS, "Wi-Fi network")
    env_pw = os.environ.get("RIPARR_WIFI_PASSWORD")
    if a is not None and a.ssid:
        ok(f"Network: {BOLD}{a.ssid}{RESET}  {GRY}(from --ssid){RESET}")
        if a.open:
            info("Open network — no password.")
            return a.ssid, "", a.hidden, False
        if not env_pw:
            err("--ssid needs the password in RIPARR_WIFI_PASSWORD (or pass --open).")
            sys.exit(2)
        return a.ssid, env_pw, a.hidden, True
    with Spinner("Scanning for nearby networks…"):
        nets, method = gather_networks()
    usable = [n for n in nets if n.pi_ok]
    if method == "live scan":
        ok(f"Found {len(nets)} networks · {len(usable)} usable by a Pi Zero 2W")
    elif method == "none":
        warn("Could not scan. You can still type the network name.")
    else:
        warn(f"Limited scan via {method} — band info may be incomplete")
    note("The Pi Zero 2W has no 5 GHz radio, so 5 GHz networks are shown greyed out.")
    print()
    MANUAL = Net("  Type a network name manually…", ["2.4"])
    MANUAL.manual = True
    items = nets + [MANUAL]
    i = menu(items,
             lambda n, s: (f"{CYN}✎ {n.ssid.strip()}{RESET}" if getattr(n, "manual", False)
                           else render_net(n, s)),
             footer="↑↓ move · enter select · esc quit",
             disabled=lambda n: not n.pi_ok)
    if i is None: sys.exit(1)
    chosen = items[i]
    if getattr(chosen, "manual", False):
        print()
        ssid = prompt("Network name (SSID)", validate=lambda v: None if v else "SSID cannot be empty")
        chosen = Net(ssid, [], None, True)
        hidden = confirm(f"Is “{ssid}” a hidden network?")
    else:
        hidden = False
    print()
    ok(f"Network: {BOLD}{chosen.ssid}{RESET}")
    if not chosen.bands:
        warn("Band unknown — if this is 5 GHz, the Pi will not connect.")
    if not chosen.secure:
        info("Open network — no password needed.")
        return chosen.ssid, "", hidden, False
    pw = ""
    while not pw:
        pw = password("Wi-Fi password")
        if len(pw) < 8:
            err("WPA passwords are at least 8 characters."); pw = ""
    return chosen.ssid, pw, hidden, True

# ─────────────────────────────── Disks ───────────────────────────────

def list_disks():
    out = subprocess.run(["diskutil", "list", "-plist", "external", "physical"],
                         capture_output=True).stdout
    try: disks = plistlib.loads(out).get("AllDisksAndPartitions", [])
    except Exception: return []
    res = []
    for d in disks:
        ident = d.get("DeviceIdentifier")
        info = plistlib.loads(subprocess.run(["diskutil", "info", "-plist", ident],
                                             capture_output=True).stdout)
        if info.get("VirtualOrPhysical") == "Virtual": continue
        if not info.get("Ejectable", False) and not info.get("RemovableMedia", False): continue
        res.append({
            "id": ident,
            "size": info.get("TotalSize", 0),
            "name": info.get("MediaName", "?").strip(),
            "internal": info.get("Internal", False),
        })
    return res

def choose_disk(preset=None):
    step(1, STEPS, "SD card")
    with Spinner("Looking for removable drives…"):
        disks = list_disks()
    disks = [d for d in disks if 4e9 < d["size"] < 7e10 and not d["internal"]]
    if not disks:
        err("No removable card found between 4 GB and 70 GB.")
        note("Insert the SD card and run this again.")
        sys.exit(1)
    if preset:
        for d in disks:
            if d["id"] == preset: 
                ok(f"Using /dev/{d['id']}"); return d
        err(f"{preset} is not an eligible removable disk."); sys.exit(1)
    ok(f"Found {len(disks)} removable drive(s)")
    note("Only external, removable drives between 4 GB and 70 GB are listed.")
    print()
    def render_disk(d, sel):
        name = "/dev/" + d["id"]
        if sel: name = BOLD + name + RESET
        size = "%.1f GB" % (d["size"] / 1e9)
        return pad(name, 16) + pad(size, 12) + GRY + d["name"] + RESET
    i = menu(disks, render_disk, footer="↑↓ move · enter select · esc quit")
    if i is None: sys.exit(1)
    return disks[i]

# ──────────────────────────── Provisioning ────────────────────────────

def sha512_crypt(pw):
    """Delegates to core so the GUI and this script can never disagree on hashing."""
    try:
        return core.sha512_crypt(pw)
    except ImportError:
        err("passlib not installed in this interpreter.")
        note("Run with the bundled venv:  ./.venv/bin/python riparr-flash.py")
        sys.exit(1)

def build_toml(cfg):
    """Delegates to core.build_toml — the schema is verified there, once."""
    kp = os.path.join(ASSETS, "riparr_key.pub")
    return core.build_toml(dict(cfg, authorized_key=(open(kp).read().strip()
                                                     if os.path.exists(kp) else None)))


# ───────────────────────────── Flashing ─────────────────────────────

def uncompressed_size(path):
    try:
        out = subprocess.run(["xz", "--robot", "--list", path], capture_output=True, text=True).stdout
        for line in out.splitlines():
            f = line.split("\t")
            if f[0] == "file": return int(f[4])
    except Exception: pass
    return 0

def flash(disk, toml_text):
    dev, rdev = f"/dev/{disk['id']}", f"/dev/r{disk['id']}"
    total = uncompressed_size(IMG)
    print()
    info("Administrator access is needed to write to the card.")
    if subprocess.run(["sudo", "-v"]).returncode != 0:
        err("Could not get administrator access."); sys.exit(1)
    with Spinner(f"Unmounting {dev}…"):
        subprocess.run(["diskutil", "unmountDisk", dev], capture_output=True)
    print()
    xz = subprocess.Popen(["xz", "-dc", IMG], stdout=subprocess.PIPE)
    dd = subprocess.Popen(["sudo", "dd", f"of={rdev}", "ibs=1m", "obs=4m"],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
    written, t0, last = 0, time.time(), 0
    try:
        while True:
            chunk = xz.stdout.read(4 << 20)
            if not chunk: break
            dd.stdin.write(chunk)
            written += len(chunk)
            now = time.time()
            if now - last > 0.15:
                el = now - t0
                rate = written / el / 1e6 if el else 0
                eta = (total - written) / (written / el) if written and el else 0
                progress(written, total, "Writing",
                         f"{written/1e9:.2f}/{total/1e9:.2f} GB · {rate:.0f} MB/s · {int(eta//60)}m{int(eta%60):02d}s left")
                last = now
    finally:
        try: dd.stdin.close()
        except Exception: pass
        dd.wait(); xz.wait()
    progress(total, total, "Writing", f"{total/1e9:.2f} GB done            ")
    print("\n")
    with Spinner("Flushing to card…"):
        subprocess.run(["sync"])
    boot = None
    with Spinner("Waiting for boot partition…"):
        for _ in range(40):
            mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
            for line in mounts.splitlines():
                if "msdos" in line and "/Volumes/" in line:
                    boot = line.split(" on ")[1].split(" (")[0]; break
            if boot: break
            time.sleep(1.5)
    if not boot:
        err("Boot partition did not mount. Unplug and replug the card, then re-run with --toml-only.")
        sys.exit(1)
    ok(f"Boot partition mounted at {boot}")
    with open(os.path.join(boot, "custom.toml"), "w") as f:
        f.write(toml_text)
    subprocess.run(["sync"])
    ok("Wrote custom.toml provisioning")
    with Spinner("Ejecting…"):
        subprocess.run(["diskutil", "eject", dev], capture_output=True)
    ok("Card ejected — safe to remove")

# ─────────────────────────────── Main ───────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disk"); ap.add_argument("--toml-only", action="store_true")
    ap.add_argument("--hostname", default="riparr"); ap.add_argument("--user", default="riparr")
    ap.add_argument("--assets", default=HERE,
                    help="directory holding the .img.xz, riparr_key.pub and user_password.txt")
    ap.add_argument("--image", help="path to the .img.xz (default: newest one in --assets)")
    ap.add_argument("--ssid", help="skip the Wi-Fi picker; password from $RIPARR_WIFI_PASSWORD")
    ap.add_argument("--hidden", action="store_true", help="--ssid names a hidden network")
    ap.add_argument("--open", action="store_true", help="--ssid names an open network")
    ap.add_argument("--yes", action="store_true", help="skip confirmation prompts")
    a = ap.parse_args()

    global ASSETS, IMG
    ASSETS = os.path.abspath(os.path.expanduser(a.assets))
    if a.image:
        IMG = os.path.abspath(os.path.expanduser(a.image))
    else:
        cands = sorted((f for f in os.listdir(ASSETS) if f.endswith(".img.xz")),
                       key=lambda f: os.path.getmtime(os.path.join(ASSETS, f)), reverse=True)
        IMG = os.path.join(ASSETS, cands[0]) if cands else os.path.join(ASSETS, "missing.img.xz")

    banner("Riparr Flasher", "Prepare an SD card for your Riparr appliance")
    if not a.toml_only and not os.path.exists(IMG):
        err(f"Image not found: {IMG}")
        note("Pass --image /path/to/raspios.img.xz or --assets /dir/with/image"); sys.exit(1)

    disk = None if a.toml_only else choose_disk(a.disk)
    print()
    ssid, wifi_pw, hidden, secure = choose_wifi(a)

    banner("Riparr Flasher", "Prepare an SD card for your Riparr appliance")
    step(3, STEPS, "Name and account")
    hostname = prompt("Hostname", a.hostname,
                      validate=lambda v: None if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", v)
                                         else "Lowercase letters, digits and hyphens only",
                      hint="You'll reach the box at this name + .local")
    ok(f"Will be reachable at {BOLD}http://{hostname}.local{RESET}")
    print()
    pwfile = os.path.join(ASSETS, "user_password.txt")
    if os.path.exists(pwfile):
        acct_pw = open(pwfile).read().strip()
        info(f"Using the generated account password in {os.path.basename(pwfile)}")
    else:
        import secrets, string
        acct_pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
        open(pwfile, "w").write(acct_pw + "\n"); os.chmod(pwfile, 0o600)
        info(f"Generated an account password and saved it to {os.path.basename(pwfile)}")
    note(f"user: {a.user}   password: {acct_pw}")

    cfg = dict(ssid=ssid, wifi_pw=wifi_pw, secure=secure, hidden=hidden,
               hostname=hostname, user=a.user, pw_hash=sha512_crypt(acct_pw),
               country=os.environ.get("RIPARR_COUNTRY", "US"),
               timezone=(os.readlink("/etc/localtime").split("zoneinfo/")[-1]
                         if os.path.islink("/etc/localtime") else "UTC"),
               keymap="us")
    toml_text = build_toml(cfg)

    banner("Riparr Flasher", "Review before writing")
    step(4, STEPS, "Review")
    rows = [("Wi-Fi network", ssid + ("  (hidden)" if hidden else "")),
            ("Wi-Fi password", "•" * len(wifi_pw) + f"  {GRY}stored as PBKDF2 PSK, not plaintext{RESET}" if secure else "none (open)"),
            ("Hostname", f"{hostname}  →  http://{hostname}.local"),
            ("Account", f"{a.user}  (password in user_password.txt)"),
            ("SSH", "enabled, key + password auth"),
            ("Region", f"{cfg['country']} · {cfg['timezone']}")]
    if disk: rows.insert(0, ("Card", f"/dev/{disk['id']}  ({disk['size']/1e9:.1f} GB, {disk['name']})"))
    for k, v in rows: print(f"  {GRY}{pad(k, 16)}{RESET}{v}")
    print()
    if disk:
        warn(f"Everything on /dev/{disk['id']} will be erased.")
        print()
        if not (a.yes or confirm("Write the card now?", danger=True)):
            info("Nothing was written."); sys.exit(0)
    if a.toml_only:
        out = os.path.join(ASSETS, "custom.toml")
        open(out, "w").write(toml_text)
        ok(f"Wrote {out}"); return

    banner("Riparr Flasher", "Writing")
    step(5, STEPS, "Flash")
    flash(disk, toml_text)

    print()
    print(f"  {rule()}")
    print(f"  {GRN}{BOLD}✓ Your Riparr card is ready.{RESET}\n")
    print(f"  {BOLD}1.{RESET} Put the card in the Pi")
    print(f"  {BOLD}2.{RESET} Plug in the USB-C cable")
    print(f"  {BOLD}3.{RESET} Wait about two minutes, then open {CYN}http://{hostname}.local{RESET}")
    print()
    cfgp = os.path.join(ASSETS, "ssh_config")
    if os.path.exists(cfgp): note(f"ssh -F {cfgp} riparr")
    print()

TTY_HELP = """
  This wizard needs a real terminal — it uses arrow keys, hidden password entry
  and a sudo prompt, none of which work through a pipe.

  Open Terminal.app (or iTerm) and run it there:

    {cmd}

  Or drive it without prompts:

    export RIPARR_WIFI_PASSWORD='...'
    {cmd} --ssid 'YourNetwork' --disk diskN --yes
"""

if __name__ == "__main__":
    try: main()
    except NoTTY as e:
        sys.stdout.write(SHOW)
        print()
        err(f"Can't ask for {e} — stdin is not a terminal.")
        cmd = f"{sys.executable} {os.path.abspath(__file__)}"
        if "--assets" in sys.argv:
            cmd += f" --assets {sys.argv[sys.argv.index('--assets')+1]}"
        print(TTY_HELP.format(cmd=cmd))
        sys.exit(2)
    except KeyboardInterrupt:
        sys.stdout.write(SHOW); print("\n\n  Cancelled. Nothing was written.\n"); sys.exit(130)
