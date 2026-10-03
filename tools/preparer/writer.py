"""
Privileged SD writer. Runs as root, launched once through the host's own authorization
dialog — macOS's sudo askpass, polkit on Linux, UAC on Windows.

Everything that needs privilege happens inside a single invocation — image write,
custom.toml placement, eject — so the user authenticates exactly once and never sees a
terminal. Progress is published as JSON to a file the GUI polls, because a root process
launched through an elevation prompt has no usable pipe back to the parent. That was a
macOS constraint originally and turned out to be the only design that survives all
three: UAC in particular hands back a process handle and nothing else.

**This file is the sequence, not the mechanics.** Verify the image, unmount, write,
verify the card, provision, eject — in that order, on every platform. Every operation
that differs between operating systems lives in `hostos/`, behind the contract written
down in `hostos/__init__.py`. Nothing below should ever name a device path, a command,
or an errno directly; if it starts to, the fix is a new function there, not a branch
here.
"""
import argparse
import hashlib
import lzma
import os
import shutil
import sys
import tempfile
import time

import hostos
import statusfile


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(path, **kw):
    """Atomically replace the status file; see statusfile for why it is shared."""
    statusfile.publish(path, **kw)


def _read_back(dev, count, st):
    """Read `count` bytes back off the card and hash them."""
    h = hashlib.sha256()
    done = 0
    t0 = time.time()
    try:
        f = hostos.open_reader(dev)
    except OSError:
        return None
    try:
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
    finally:
        try:
            f.close()
        except Exception:
            pass
    return h.hexdigest()


def _partition_layout(dev):
    """Read the MBR and classify the image we just wrote.

    Raspberry Pi images are FAT boot + Linux root, and are provisioned by dropping a
    file onto the FAT partition. Allwinner images -- Armbian on the Orange Pi Zero 2W --
    are a single ext4 partition with U-Boot in the raw sectors ahead of it. There is no
    FAT partition to write to and neither macOS nor Windows can mount ext4, so those are
    provisioned through debugfs instead. Getting this wrong means a card that boots and
    does nothing, which is exactly how the first night was lost.
    """
    try:
        f = hostos.open_reader(dev)
    except OSError:
        return "unknown", None
    try:
        mbr = f.read(512)
    except OSError:
        return "unknown", None
    finally:
        try:
            f.close()
        except Exception:
            pass
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


def _copy_makemkv(destdir, srcdir, st):
    """Carry MakeMKV across on the card itself.

    It is ~25 MB against a 512 MiB boot partition, and it removes an scp step from the
    first session. The service looks for /boot/firmware/makemkv before it tries to
    download anything. Not fatal if it fails: the packages can still be copied over by
    hand later, and a card that boots and joins the network is worth far more than one
    that does not exist because a nice-to-have threw.
    """
    publish(st, phase="extras", message="Copying MakeMKV onto the card")
    try:
        os.makedirs(destdir, exist_ok=True)
        for name in sorted(os.listdir(srcdir)):
            if not name.endswith(".tar.gz"):
                continue
            with open(os.path.join(srcdir, name), "rb") as a, \
                    open(os.path.join(destdir, name), "wb") as b:
                while True:
                    chunk = a.read(4 << 20)
                    if not chunk:
                        break
                    b.write(chunk)
        hostos.flush()
    except OSError as e:
        publish(st, phase="extras",
                message="Could not copy MakeMKV onto the card", detail=str(e))
        time.sleep(1.5)


def _first_partition(f):
    """(MBR type, byte offset) of the first partition in an image, from an open file."""
    mbr = f.read(512)
    if len(mbr) < 512:
        return 0, 0
    for i in range(4):
        e = mbr[0x1BE + 16 * i:0x1BE + 16 * i + 16]
        if e[4]:
            return e[4], int.from_bytes(e[8:12], "little") * 512
    return 0, 0


def _ext4_cfg(args):
    import armbian
    port = 9797
    if args.conf and os.path.exists(args.conf):
        for line in open(args.conf, encoding="utf-8"):
            if line.startswith("RIPARR_PORT="):
                port = int(line.split("=", 1)[1].strip())
    return armbian.cfg_from_custom_toml(args.toml, port)


def _configure_image(args, st):
    """Expand the image to a file and write the settings into it. -> path, or None.

    For platforms that cannot reach the card's ext4 partition after the write (see
    hostos.PROVISION_IN_IMAGE). Everything here happens before the card is unmounted,
    so a failure leaves the card exactly as it was.
    """
    import armbian
    publish(st, phase="provision", message="Applying your settings")
    work_dir = tempfile.gettempdir()
    need = (args.total or 0) + (256 << 20)
    free = shutil.disk_usage(work_dir).free
    if args.total and free < need:
        publish(st, phase="error",
                message="Not enough free space to prepare the card. Nothing was written.",
                detail="The settings are written into a copy of the image first, which "
                       "needs %.1f GB free in %s; there is %.1f GB."
                       % (need / 1e9, work_dir, free / 1e9))
        return None
    fd, work = tempfile.mkstemp(prefix="riparr-card-", suffix=".img", dir=work_dir)
    os.close(fd)
    try:
        with lzma.open(args.image, "rb") as src, open(work, "wb") as dst:
            shutil.copyfileobj(src, dst, 4 << 20)
        with open(work, "rb") as f:
            ptype, offset = _first_partition(f)
        if ptype != 0x83:
            raise RuntimeError("expected one Linux partition, found type 0x%02x" % ptype)
        target = armbian.image_target(work, offset)
        cfg = _ext4_cfg(args)
        armbian.provision(target, cfg)
        failed = [lbl for lbl, ok, _ in armbian.verify(target, cfg) if not ok]
        if failed:
            raise RuntimeError("These did not read back correctly:\n  "
                               + "\n  ".join(failed))
        if args.makemkv and os.path.isdir(args.makemkv):
            publish(st, phase="extras", message="Adding MakeMKV")
            try:
                armbian.copy_makemkv(target, args.makemkv)
            except Exception as e:
                publish(st, phase="extras",
                        message="Could not add MakeMKV to the card", detail=str(e))
                time.sleep(1.5)
        return work
    except Exception as e:
        try:
            os.remove(work)
        except OSError:
            pass
        publish(st, phase="error",
                message="Your settings could not be applied. Nothing was written.",
                detail=str(e))
        return None


def run(args):
    st = args.progress

    if args.sha256:
        publish(st, phase="verify-image", message="Checking the operating system image")
        actual = _sha256(args.image)
        if actual != args.sha256:
            publish(st, phase="error",
                    message="The operating system image is damaged. Nothing was written.",
                    detail="expected %s\ngot      %s" % (args.sha256[:32], actual[:32]))
            return 1

    work = None
    if hostos.PROVISION_IN_IMAGE:
        try:
            with lzma.open(args.image, "rb") as f:
                ptype, _ = _first_partition(f)
        except Exception:
            ptype = 0                   # left for the write to report properly
        if ptype == 0x83:
            work = _configure_image(args, st)
            if work is None:
                return 1
    try:
        return _write(args, st, work)
    finally:
        if work:
            try:
                os.remove(work)
            except OSError:
                pass


def _write(args, st, work):
    """Unmount, write, verify, provision, eject. `work` is an already-configured image
    file, or None to write the compressed image and configure the card afterwards."""
    dev = args.dev
    node = hostos.raw_device(dev)         # for messages; hostos does the opening

    publish(st, phase="unmount", message="Unmounting the card")
    ok, why = hostos.unmount_disk(dev)
    if not ok:
        publish(st, phase="error",
                message="The card could not be unmounted, so nothing was written.",
                detail=why)
        return 1

    # Before building the pipeline. A failure here names the real reason -- busy,
    # permissions, gone -- instead of leaving it to be inferred from a write that has
    # already half happened.
    ok, why = hostos.probe_writable(dev)
    if not ok:
        publish(st, phase="error",
                message="The card could not be opened for writing.", detail=why)
        return 1

    total = os.path.getsize(work) if work else args.total
    publish(st, phase="write", written=0, total=total, rate=0, eta=0,
            message="Writing the operating system")

    # Decompression is the standard library's, not xz(1).
    #
    # xz is Homebrew-only on macOS and does not exist on Windows at all, and Python has
    # linked liblzma -- the same library macOS already ships inside libarchive -- since
    # forever. Dropping the subprocess removed a dependency on macOS and removed the
    # question entirely from the Windows and Linux ports.
    #
    # Measured on the real image, 1.54 GB expanded, hashing as it goes:
    #   xz -dc      2.1s   (~735 MB/s)
    #   lzma.open  10.5s   (~147 MB/s)
    # Five times slower and it does not matter: the SD card takes ~20 MB/s, so the
    # decompressor still has seven times the headroom it needs and the write stays
    # gated on the card exactly as before. Worth writing down so the number is not
    # rediscovered as a regression.
    try:
        src = open(work, "rb") if work else lzma.open(args.image, "rb")
    except Exception as e:
        publish(st, phase="error",
                message="The operating system image could not be opened.",
                detail="%s\n\nThe download may be incomplete or corrupt." % e)
        return 1

    try:
        sink = hostos.open_sink(dev, total)
    except Exception as e:
        src.close()
        publish(st, phase="error",
                message="The card could not be opened for writing.",
                detail=hostos.explain_write_error(str(e), "", 1, node))
        return 1

    written, t0, last = 0, time.time(), 0.0
    digest = hashlib.sha256()
    broke = False
    read_err = ""
    try:
        while True:
            try:
                chunk = src.read(4 << 20)
            except Exception as e:
                # A truncated or corrupt .xz raises here rather than at open time.
                read_err = str(e)
                broke = True
                break
            if not chunk:
                break
            try:
                sink.write(chunk)
            except (BrokenPipeError, OSError):
                # The sink is gone. Do not keep feeding it.
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
        sink_rc, err = sink.close()
        try:
            src.close()
        except Exception:
            pass

    if broke or written == 0:
        publish(st, phase="error",
                message="The card could not be written to.",
                detail=hostos.explain_write_error(err, read_err, sink_rc, node))
        return 1
    if sink_rc != 0:
        publish(st, phase="error",
                message="The write failed before it finished.",
                detail=(err or read_err or "the writer exited %s." % sink_rc))
        return 1
    if total and written < total:
        publish(st, phase="error",
                message="The write ended early — the card may be faulty.",
                detail="wrote %d of %d bytes" % (written, total))
        return 1

    publish(st, phase="flush", written=written, total=total,
            message="Flushing to the card")
    hostos.flush()

    if args.verify:
        # Failing and counterfeit cards accept writes happily and return garbage on read.
        # The source image was already checked; this checks what actually landed.
        expect = digest.hexdigest()
        got = _read_back(dev, written, st)
        if got is None:
            publish(st, phase="error",
                    message="Could not read the card back to check it.",
                    detail="The write itself reported success. Re-seat the card and "
                           "verify manually, or re-run without checking.")
            return 1
        if got != expect:
            publish(st, phase="error",
                    message="The card did not read back what was written to it.",
                    detail="Most often the card is failing or counterfeit. A loose or "
                           "faulty card reader can do this too, so try once more in a "
                           "different reader or port before giving up on the card.\n"
                           "wrote %s\nread  %s" % (expect[:32], got[:32]))
            return 1

    layout, partno = _partition_layout(dev)

    if layout == "single-ext4":
        # Already configured, and the read-back above proved the card holds it.
        rc = 0 if work else _provision_ext4(args, dev, partno, st)
    else:
        rc = _provision_fat(args, dev, partno or 1, st)
    if rc:
        return rc

    hostos.flush()
    publish(st, phase="eject", message="Ejecting")
    hostos.eject(dev)
    publish(st, phase="done", written=written, total=total,
            message="Your Riparr card is ready")
    return 0


def _provision_ext4(args, dev, partno, st):
    """Armbian on Allwinner. No FAT partition exists; configuration is written into the
    ext4 root filesystem directly. See tools/preparer/armbian.py for why Armbian's own
    PRESET_* mechanism cannot be used on a headless box.
    """
    publish(st, phase="provision", message="Applying your settings")

    # After a raw whole-disk write the OS has to notice the new partition table before
    # the partition node exists. It usually rescans when the device is closed, but not
    # always instantly, and provisioning a node that is not there yet would fail for a
    # reason nobody could act on.
    hostos.rescan_partitions(dev)
    cands = hostos.partition_devices(dev, partno)
    if not cands:
        publish(st, phase="error",
                message="The card was written, but could not be configured.",
                detail="This image keeps its settings in a Linux filesystem, and %s "
                       "has no way to write into one. Use a Riparr image with a FAT "
                       "boot partition." % hostos.NAME)
        return 1

    present = []
    for _ in range(20):
        present = [c for c in cands if os.path.exists(c)]
        if present:
            break
        time.sleep(0.5)
    else:
        publish(st, phase="error",
                message="The card was written, but its partitions never appeared.",
                detail="Expected %s. Unplug and replug the card, then try again."
                       % cands[0])
        return 1

    # A card that was written but not configured is a card that boots, joins nothing,
    # and cannot be reached — there is no Ethernet on this board and no second way in.
    # So the desktop is given no chance to remount the partition underneath debugfs.
    hostos.unmount_disk(dev)

    try:
        import armbian
        cfg = _ext4_cfg(args)

        # Try each candidate in turn -- on macOS the raw node is much faster for the
        # MakeMKV copy but demands aligned IO, and that path has never been exercised on
        # hardware, so it does not get to be the single point of failure.
        rootpart, first_err = None, None
        for cand in present:
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
                    detail="These did not read back correctly:\n  " + "\n  ".join(failed))
            return 1

        if args.makemkv and os.path.isdir(args.makemkv):
            publish(st, phase="extras", message="Copying MakeMKV onto the card")
            try:
                armbian.copy_makemkv(rootpart, args.makemkv)
            except Exception as e:
                publish(st, phase="extras",
                        message="Could not copy MakeMKV onto the card", detail=str(e))
                time.sleep(1.5)
    except Exception as e:
        publish(st, phase="error",
                message="The card was written, but could not be configured.",
                detail=str(e))
        return 1
    return 0


def _write_cloud_init(boot, toml_body):
    """Write user-data, network-config and meta-data, and add the NoCloud pointer and
    Wi-Fi country to cmdline.txt. Returns the name of a file that did not read back,
    or None. Harmless on an image without cloud-init: it ignores all four."""
    import rpi_cloudinit
    seed = rpi_cloudinit.build(toml_body)
    files = {n: seed[n] for n in ("user-data", "network-config", "meta-data") if seed[n]}
    cmdline = os.path.join(boot, "cmdline.txt")
    if os.path.exists(cmdline):
        with open(cmdline, encoding="utf-8", newline="") as f:
            files["cmdline.txt"] = rpi_cloudinit.patch_cmdline(
                f.read(), seed["instance_id"], seed["country"])
    for name, text in files.items():
        with open(os.path.join(boot, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    hostos.flush()
    for name, text in files.items():
        with open(os.path.join(boot, name), encoding="utf-8", newline="") as f:
            if f.read() != text:
                return name
    return None


def _provision_fat(args, dev, partno, st):
    """Raspberry Pi style, and what the Riparr image will be: drop files onto the FAT
    boot partition. This is the path that works the same on all three operating systems,
    which is the entire argument behind D25.
    """
    publish(st, phase="mount", message="Waiting for the boot partition")
    hostos.rescan_partitions(dev)
    boot, release, why = hostos.mount_boot(dev, partno)
    if not boot:
        publish(st, phase="error",
                message="The card was written, but its boot partition never mounted.",
                detail=why)
        return 1

    try:
        publish(st, phase="provision", message="Applying your settings")
        with open(args.toml, encoding="utf-8") as f:
            body = f.read()
        dest = os.path.join(boot, "custom.toml")
        with open(dest, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        hostos.flush()

        # Read it back. A FAT32 write that returns success is not proof of a good file,
        # and this one file is the difference between a box that joins the network and a
        # box that needs re-flashing.
        with open(dest, encoding="utf-8", newline="") as f:
            if f.read() != body:
                publish(st, phase="error",
                        message="Settings did not verify after writing.",
                        detail="custom.toml read back differently than it was written")
                return 1

        # And the same settings as cloud-init seed files, which is what Raspberry Pi OS
        # Trixie actually reads -- it removed the custom.toml handler (rpi_cloudinit.py).
        bad = _write_cloud_init(boot, body)
        if bad:
            publish(st, phase="error",
                    message="Settings did not verify after writing.",
                    detail="%s read back differently than it was written" % bad)
            return 1

        if args.makemkv and os.path.isdir(args.makemkv):
            _copy_makemkv(os.path.join(boot, "makemkv"), args.makemkv, st)

        if args.conf:
            with open(os.path.join(boot, "riparr.conf"), "w", encoding="utf-8",
                      newline="\n") as f:
                f.write(open(args.conf, encoding="utf-8").read())
            hostos.flush()
    finally:
        # Whatever happened, give the volume back. On Linux this is our own mount in a
        # temp directory and leaving it behind would hold the card open for good.
        release()
    return 0


def main(argv=None):
    """The privileged half. `argv` is None from a checkout and explicit when frozen.

    A packaged app is not a Python interpreter: `sys.executable` is the application
    binary, so there is no way to hand it a script. The frozen build therefore re-invokes
    *itself* with `--write-card`, and `shell.py` routes that here before it imports
    anything graphical. See `Bridge._run_privileged`.
    """
    ap = argparse.ArgumentParser(prog="riparr-writer")
    ap.add_argument("--image", required=True)
    ap.add_argument("--dev", required=True,
                    help=r"whole-disk identifier: disk4, sdb, \\.\PHYSICALDRIVE2")
    ap.add_argument("--toml", required=True)
    ap.add_argument("--progress", required=True)
    ap.add_argument("--total", type=int, default=0)
    ap.add_argument("--sha256", default="", help="expected image checksum")
    ap.add_argument("--conf", default="", help="riparr.conf to place on the boot partition")
    ap.add_argument("--makemkv", default="",
                    help="directory of MakeMKV tarballs to copy onto the boot partition")
    ap.add_argument("--verify", action="store_true",
                    help="read the card back after writing and compare")
    args = ap.parse_args(argv)

    # The device identifier arrives from a GUI that already validated it, and is about
    # to be handed to something that writes raw sectors. It is checked again here,
    # against a per-platform whitelist, because this is the process with the privilege.
    if not hostos.valid_device_id(args.dev):
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
