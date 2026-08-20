"""
Privileged SD writer. Runs as root, launched once via the macOS authorization dialog.

Everything that needs privilege happens inside a single invocation — image write,
custom.toml placement, eject — so the user authenticates exactly once and never sees a
terminal. Progress is published as JSON to a file the GUI polls, because a root process
launched through osascript has no usable pipe back to the parent.
"""
import argparse
import errno
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(path, **kw):
    """Atomically replace the status file so a reader never sees a half-written line."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.replace(tmp, path)


def _read_back(rdev, count, st):
    """Read `count` bytes back off the raw device and hash them."""
    h = hashlib.sha256()
    done = 0
    t0 = time.time()
    try:
        with open(rdev, "rb") as f:
            while done < count:
                chunk = f.read(min(4 << 20, count - done))
                if not chunk:
                    return None
                h.update(chunk)
                done += len(chunk)
                el = time.time() - t0
                publish(st, phase="verify-card", written=done, total=count,
                        rate=(done / el) if el else 0,
                        eta=((count - done) / (done / el)) if done and el else 0,
                        message="Checking the card reads back correctly")
    except OSError:
        return None
    return h.hexdigest()


def _unmount(dev):
    """Unmount every volume on the disk, and confirm it actually happened.

    diskutil returning is not the same as the volumes having released. Writing to the
    raw device while anything is still mounted fails with EBUSY, which previously
    surfaced only as a dead `dd` and no explanation.
    """
    p = subprocess.run(["diskutil", "unmountDisk", dev], capture_output=True, text=True)
    if p.returncode != 0:
        p = subprocess.run(["diskutil", "unmountDisk", "force", dev],
                           capture_output=True, text=True)
        if p.returncode != 0:
            return False, ((p.stderr or p.stdout).strip()
                           + "\n\nClose anything using the card and try again.")

    ident = os.path.basename(dev)
    for _ in range(20):
        mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
        if not any(("/dev/%s" % ident) in line for line in mounts.splitlines()):
            break
        time.sleep(0.5)
    else:
        return False, ("Volumes on %s are still mounted after ten seconds." % dev)

    # Disappearing from `mount` is NOT the same as the device being free. On macOS 26
    # FAT volumes are served by a userspace FSKit extension
    # (com.apple.fskit.msdos.appex), which keeps /dev/rdiskNs1 open and can outlive the
    # unmount by a few seconds. The old check passed here and dd then died on EBUSY.
    # The only honest test of "can we write this" is to open it.
    rdev = "/dev/r" + ident
    last = ""
    for _ in range(30):
        try:
            fd = os.open(rdev, os.O_WRONLY)
            os.close(fd)
            return True, ""
        except OSError as e:
            last = str(e)
            if e.errno == errno.EBUSY:
                time.sleep(0.5)     # something still holds it; give it a moment
                continue
            # Not busy, but not openable either -- a permission refusal, most likely.
            # The unmount genuinely did succeed, and saying "could not be unmounted"
            # here would send the user hunting the wrong problem. Hand off to the
            # pre-open probe below, which routes the real errno through _explain().
            return True, ""
    return False, ("%s is still busy fifteen seconds after unmounting.\n\n"
                   "Something still has the card open. Ejecting it in Finder and "
                   "re-inserting it clears this.\n\n%s" % (rdev, last))


def _responsible_app():
    """The application macOS holds responsible for what this process does.

    TCC attributes consent to the nearest enclosing .app bundle up the process tree, so
    that is the one the user has to grant — naming it saves them guessing which of the
    dozen entries in the list matters.
    """
    pid, found = os.getpid(), None
    for _ in range(12):
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                             capture_output=True, text=True).stdout.strip()
        if not out:
            break
        parent, _, comm = out.partition(" ")
        if ".app/Contents/MacOS/" in comm:
            # Keep walking rather than returning here: an interpreter run from a
            # terminal sits inside Python.app, which owns no consent. The application
            # that matters is the outermost one, nearest launchd.
            found = comm.split(".app/")[0].rsplit("/", 1)[-1] + ".app"
        try:
            pid = int(parent)
        except ValueError:
            break
        if pid <= 1:
            break
    return found or "the application you launched this from"


def _explain(err, xerr, rc, rdev):
    """Turn a terse dd failure into something the user can act on."""
    blob = (str(err) + " " + str(xerr)).lower()
    if "operation not permitted" in blob or "not permitted" in blob:
        return ("macOS blocked access to %s.\n\n"
                "This is a privacy refusal, not a file permission — running as root "
                "does not lift it. macOS grants disk access to the *application* the "
                "write is attributed to, which here is %s.\n\n"
                "Grant it in System Settings > Privacy & Security > Full Disk Access, "
                "quit that application completely, reopen it and run this again.\n\n%s"
                % (rdev, _responsible_app(), err or "(no detail)"))
    if "resource busy" in blob or "busy" in blob or "errno 16" in blob:
        return ("%s is still in use.\n\nThe card had not finished unmounting. Eject it "
                "in Finder, re-insert it, and try again.\n\n%s" % (rdev, err or ""))
    if "no such file" in blob:
        return ("%s disappeared.\n\nThe card was removed or the reader dropped it.\n\n%s"
                % (rdev, err or ""))
    return (err or xerr or "dd exited %s having written nothing." % rc)


def _partition_layout(rdev):
    """Read the MBR and classify the image we just wrote.

    Raspberry Pi images are FAT boot + Linux root, and are provisioned by dropping a
    file onto the FAT partition. Allwinner images -- Armbian on the Orange Pi Zero 2W --
    are a single ext4 partition with U-Boot in the raw sectors ahead of it. There is no
    FAT partition to write to and macOS cannot mount ext4, so those are provisioned
    through debugfs instead. Getting this wrong means a card that boots and does
    nothing, which is exactly how the first night was lost.
    """
    try:
        with open(rdev, "rb") as f:
            mbr = f.read(512)
    except OSError:
        return "unknown", None
    if len(mbr) < 512:
        return "unknown", None
    parts = []
    for i in range(4):
        e = mbr[0x1BE + 16 * i:0x1BE + 16 * i + 16]
        if e[4]:
            parts.append(e[4])
    if not parts:
        return "unknown", None
    if parts[0] in (0x0b, 0x0c):
        return "fat-boot", 1          # Raspberry Pi style
    if parts[0] == 0x83 and len(parts) == 1:
        return "single-ext4", 1       # Armbian / Allwinner style
    return "unknown", None


def _looks_like_pi_boot(path):
    """A Raspberry Pi boot partition always carries these. An unrelated FAT volume won't."""
    return (os.path.exists(os.path.join(path, "config.txt"))
            and os.path.exists(os.path.join(path, "cmdline.txt")))


def run(args):
    dev = "/dev/%s" % args.dev
    rdev = "/dev/r%s" % args.dev          # raw device: markedly faster than buffered
    st = args.progress

    if args.sha256:
        publish(st, phase="verify-image", message="Checking the operating system image")
        actual = _sha256(args.image)
        if actual != args.sha256:
            publish(st, phase="error",
                    message="The operating system image is damaged. Nothing was written.",
                    detail="expected %s\ngot      %s" % (args.sha256[:32], actual[:32]))
            return 1

    publish(st, phase="unmount", message="Unmounting the card")
    ok, why = _unmount(dev)
    if not ok:
        publish(st, phase="error",
                message="The card could not be unmounted, so nothing was written.",
                detail=why)
        return 1

    # Open the raw device before building the pipeline. A failure here names the real
    # reason -- busy, permissions, gone -- instead of leaving it to be inferred from a
    # child process that has already exited.
    try:
        probe = os.open(rdev, os.O_WRONLY)
        os.close(probe)
    except OSError as e:
        publish(st, phase="error",
                message="The card could not be opened for writing.",
                detail=_explain(str(e), "", e.errno, rdev))
        return 1

    total = args.total
    publish(st, phase="write", written=0, total=total, rate=0, eta=0,
            message="Writing the operating system")

    xz = subprocess.Popen(["xz", "-dc", args.image], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    dd = subprocess.Popen(["dd", "of=%s" % rdev, "ibs=1m", "obs=4m"],
                          stdin=subprocess.PIPE,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    # Drain both stderr streams on threads. Left unread, a child that fails early fills
    # its stderr pipe and blocks, and its message -- the one thing that explains the
    # failure -- is never seen.
    errs = {}

    def drain(name, stream):
        try:
            errs[name] = stream.read().decode("utf-8", "replace")
        except Exception:
            errs[name] = ""

    threads = [threading.Thread(target=drain, args=(n, f), daemon=True)
               for n, f in (("dd", dd.stderr), ("xz", xz.stderr))]
    for t in threads:
        t.start()

    written, t0, last = 0, time.time(), 0.0
    digest = hashlib.sha256()
    broke = False
    try:
        while True:
            chunk = xz.stdout.read(4 << 20)
            if not chunk:
                break
            try:
                dd.stdin.write(chunk)
            except (BrokenPipeError, OSError):
                # dd is gone. Do not keep feeding a dead pipe.
                broke = True
                break
            digest.update(chunk)
            written += len(chunk)
            now = time.time()
            if now - last > 0.2:
                el = now - t0
                rate = written / el if el else 0
                publish(st, phase="write", written=written, total=total,
                        rate=rate,
                        eta=((total - written) / rate) if rate else 0,
                        message="Writing the operating system")
                last = now
    finally:
        # Order matters. Closing dd's stdin lets it finish; closing xz's stdout stops it
        # blocking on a pipe nobody is draining, which is what used to hang forever.
        try:
            dd.stdin.close()
        except Exception:
            pass
        try:
            dd_rc = dd.wait(timeout=120)
        except subprocess.TimeoutExpired:
            dd.kill()
            dd_rc = -1
        try:
            xz.stdout.close()
        except Exception:
            pass
        try:
            xz_rc = xz.wait(timeout=15)
        except subprocess.TimeoutExpired:
            xz.terminate()
            try:
                xz_rc = xz.wait(timeout=10)
            except subprocess.TimeoutExpired:
                xz.kill()
                xz_rc = -1
        for t in threads:
            t.join(timeout=5)

    err = (errs.get("dd") or "").strip()
    xerr = (errs.get("xz") or "").strip()

    if broke or written == 0:
        publish(st, phase="error",
                message="The card could not be written to.",
                detail=_explain(err, xerr, dd_rc, rdev))
        return 1
    if dd_rc != 0 or xz_rc != 0:
        publish(st, phase="error",
                message="The write failed before it finished.",
                detail=(err or xerr
                        or "dd exited %s, xz exited %s" % (dd_rc, xz_rc)))
        return 1
    if total and written < total:
        publish(st, phase="error",
                message="The write ended early — the card may be faulty.",
                detail="wrote %d of %d bytes" % (written, total))
        return 1

    publish(st, phase="flush", written=written, total=total,
            message="Flushing to the card")
    subprocess.run(["sync"])

    if args.verify:
        # Failing and counterfeit cards accept writes happily and return garbage on read.
        # The source image was already checked; this checks what actually landed.
        expect = digest.hexdigest()
        got = _read_back(rdev, written, st)
        if got is None:
            publish(st, phase="error",
                    message="Could not read the card back to check it.",
                    detail="The write itself reported success. Re-seat the card and "
                           "verify manually, or re-run without checking.")
            return 1
        if got != expect:
            publish(st, phase="error",
                    message="The card did not read back what was written to it.",
                    detail="This card is failing or counterfeit. Do not use it.\n"
                           "wrote %s\nread  %s" % (expect[:32], got[:32]))
            return 1

    layout, partno = _partition_layout(rdev)

    if layout == "single-ext4":
        # Armbian on Allwinner. No FAT partition exists; configuration is written into
        # the ext4 root filesystem directly. See tools/preparer/armbian.py for why
        # Armbian's own PRESET_* mechanism cannot be used on a headless box.
        publish(st, phase="provision", message="Applying your settings")

        # After a raw whole-disk write, macOS has to notice the new partition table
        # before /dev/*s1 exists. It usually rescans when the device is closed, but not
        # always instantly, and provisioning a node that is not there yet would fail for
        # a reason nobody could act on.
        base = "%ss%d" % (dev, partno)          # buffered, e.g. /dev/disk4s1
        raw = "%ss%d" % (rdev, partno)          # raw, e.g. /dev/rdisk4s1
        for attempt in range(20):
            if os.path.exists(base) or os.path.exists(raw):
                break
            if attempt == 4:
                # Nudge the kernel into re-reading the partition table.
                subprocess.run(["diskutil", "list", dev], capture_output=True)
            time.sleep(0.5)
        else:
            publish(st, phase="error",
                    message="The card was written, but its partitions never appeared.",
                    detail="Expected %s. Unplug and replug the card, then try again."
                           % base)
            return 1

        try:
            import armbian
            port = 9797
            if args.conf and os.path.exists(args.conf):
                for line in open(args.conf):
                    if line.startswith("RIPARR_PORT="):
                        port = int(line.split("=", 1)[1].strip())
            cfg = armbian.cfg_from_custom_toml(args.toml, port)

            # Prefer the raw node -- it is much faster for the MakeMKV copy -- but fall
            # back to the buffered one. Raw devices on macOS demand aligned IO, and this
            # path has never been exercised on hardware, so it does not get to be the
            # single point of failure.
            rootpart = None
            first_err = None
            for cand in ([raw] if os.path.exists(raw) else []) + \
                        ([base] if os.path.exists(base) else []):
                try:
                    armbian.provision(cand, cfg)
                    rootpart = cand
                    break
                except Exception as e:
                    first_err = first_err or e
            if rootpart is None:
                raise first_err or RuntimeError("no usable partition device")

            failed = [lbl for lbl, ok, _ in armbian.verify(rootpart, cfg) if not ok]
            if failed:
                publish(st, phase="error",
                        message="Settings did not verify after writing.",
                        detail="These did not read back correctly:\n  "
                               + "\n  ".join(failed))
                return 1

            if args.makemkv and os.path.isdir(args.makemkv):
                publish(st, phase="extras", message="Copying MakeMKV onto the card")
                try:
                    armbian.copy_makemkv(rootpart, args.makemkv)
                except Exception as e:
                    # Not fatal: the packages can still be copied over later by hand.
                    publish(st, phase="extras",
                            message="Could not copy MakeMKV onto the card", detail=str(e))
                    time.sleep(1.5)
        except Exception as e:
            publish(st, phase="error",
                    message="The card was written, but could not be configured.",
                    detail=str(e))
            return 1

        subprocess.run(["sync"])
        publish(st, phase="eject", message="Ejecting")
        subprocess.run(["diskutil", "eject", dev], capture_output=True)
        publish(st, phase="done", written=written, total=total,
                message="Your Riparr card is ready")
        return 0

    # diskutil remounts the FAT32 boot partition on its own once the write settles.
    publish(st, phase="mount", message="Waiting for the boot partition")
    boot = None
    for _ in range(40):
        mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
        for line in mounts.splitlines():
            if "msdos" in line and "/Volumes/" in line and args.dev in line:
                boot = line.split(" on ")[1].split(" (")[0]
                break
        if boot:
            break
        time.sleep(1.5)
    if not boot:
        # Some readers omit the device from the mount line, so fall back to scanning FAT
        # volumes — but ONLY ones that actually look like a freshly written Pi boot
        # partition. The old code took any msdos volume, which on a machine with a camera
        # card or USB stick attached would have written custom.toml (containing the
        # derived Wi-Fi PSK and the account password hash) onto unrelated removable media.
        mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
        for line in mounts.splitlines():
            if "msdos" not in line or "/Volumes/" not in line:
                continue
            cand = line.split(" on ")[1].split(" (")[0]
            if not _looks_like_pi_boot(cand):
                continue
            boot = cand
            break
    if not boot:
        publish(st, phase="error",
                message="The card was written, but its boot partition never mounted.",
                detail="Unplug and replug the card, then use Apply settings only.")
        return 1

    publish(st, phase="provision", message="Applying your settings")
    dest = os.path.join(boot, "custom.toml")
    with open(args.toml) as f:
        body = f.read()
    with open(dest, "w") as f:
        f.write(body)
    subprocess.run(["sync"])

    # Read it back. A FAT32 write that returns success is not proof of a good file,
    # and this one file is the difference between a box that joins the network and a
    # box that needs re-flashing.
    with open(dest) as f:
        if f.read() != body:
            publish(st, phase="error",
                    message="Settings did not verify after writing.",
                    detail="custom.toml read back differently than it was written")
            return 1

    # Carry MakeMKV across on the card itself. It is ~25 MB against a 512 MiB boot
    # partition, and it removes an scp step from the first session. The service looks
    # for /boot/firmware/makemkv before it tries to download anything.
    if args.makemkv and os.path.isdir(args.makemkv):
        publish(st, phase="extras", message="Copying MakeMKV onto the card")
        dest = os.path.join(boot, "makemkv")
        try:
            os.makedirs(dest, exist_ok=True)
            for name in sorted(os.listdir(args.makemkv)):
                if not name.endswith(".tar.gz"):
                    continue
                src = os.path.join(args.makemkv, name)
                with open(src, "rb") as a, open(os.path.join(dest, name), "wb") as b:
                    while True:
                        chunk = a.read(4 << 20)
                        if not chunk:
                            break
                        b.write(chunk)
            subprocess.run(["sync"])
        except OSError as e:
            # Not fatal: the packages can still be copied over later by hand.
            publish(st, phase="extras",
                    message="Could not copy MakeMKV onto the card", detail=str(e))
            time.sleep(1.5)

    if args.conf:
        with open(os.path.join(boot, "riparr.conf"), "w") as f:
            f.write(open(args.conf).read())
        subprocess.run(["sync"])

    publish(st, phase="eject", message="Ejecting")
    subprocess.run(["diskutil", "eject", dev], capture_output=True)

    publish(st, phase="done", written=written, total=total,
            message="Your Riparr card is ready")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--dev", required=True, help="diskN (no /dev prefix)")
    ap.add_argument("--toml", required=True)
    ap.add_argument("--progress", required=True)
    ap.add_argument("--total", type=int, default=0)
    ap.add_argument("--sha256", default="", help="expected image checksum")
    ap.add_argument("--conf", default="", help="riparr.conf to place on the boot partition")
    ap.add_argument("--makemkv", default="",
                    help="directory of MakeMKV tarballs to copy onto the boot partition")
    ap.add_argument("--verify", action="store_true",
                    help="read the card back after writing and compare")
    args = ap.parse_args()

    if not args.dev.startswith("disk") or "/" in args.dev:
        print("refusing: bad device identifier", file=sys.stderr)
        return 2
    try:
        return run(args)
    except Exception as e:
        publish(args.progress, phase="error",
                message="The write stopped unexpectedly.", detail=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
