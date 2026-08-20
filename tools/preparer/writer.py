"""
Privileged SD writer. Runs as root, launched once via the macOS authorization dialog.

Everything that needs privilege happens inside a single invocation — image write,
custom.toml placement, eject — so the user authenticates exactly once and never sees a
terminal. Progress is published as JSON to a file the GUI polls, because a root process
launched through osascript has no usable pipe back to the parent.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time


def publish(path, **kw):
    """Atomically replace the status file so a reader never sees a half-written line."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.replace(tmp, path)


def run(args):
    dev = "/dev/%s" % args.dev
    rdev = "/dev/r%s" % args.dev          # raw device: markedly faster than buffered
    st = args.progress

    publish(st, phase="unmount", message="Unmounting the card")
    subprocess.run(["diskutil", "unmountDisk", dev], capture_output=True)

    total = args.total
    publish(st, phase="write", written=0, total=total, rate=0, eta=0,
            message="Writing the operating system")

    xz = subprocess.Popen(["xz", "-dc", args.image], stdout=subprocess.PIPE)
    dd = subprocess.Popen(["dd", "of=%s" % rdev, "ibs=1m", "obs=4m"],
                          stdin=subprocess.PIPE,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    written, t0, last = 0, time.time(), 0.0
    try:
        while True:
            chunk = xz.stdout.read(4 << 20)
            if not chunk:
                break
            dd.stdin.write(chunk)
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
    except BrokenPipeError:
        pass
    finally:
        try:
            dd.stdin.close()
        except Exception:
            pass
        dd_rc = dd.wait()
        xz_rc = xz.wait()

    err = (dd.stderr.read().decode("utf-8", "replace") if dd.stderr else "")
    if dd_rc != 0 or xz_rc != 0:
        publish(st, phase="error",
                message="The write failed before it finished.",
                detail=(err.strip() or "dd exited %s, xz exited %s" % (dd_rc, xz_rc)))
        return 1
    if total and written < total:
        publish(st, phase="error",
                message="The write ended early — the card may be faulty.",
                detail="wrote %d of %d bytes" % (written, total))
        return 1

    publish(st, phase="flush", written=written, total=total,
            message="Flushing to the card")
    subprocess.run(["sync"])

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
        # Fall back to any FAT volume — on some readers the mount line omits the device.
        mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
        for line in mounts.splitlines():
            if "msdos" in line and "/Volumes/" in line:
                boot = line.split(" on ")[1].split(" (")[0]
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
