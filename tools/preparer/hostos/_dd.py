"""A `dd` subprocess presented as something you can write bytes into.

macOS and Linux both keep dd for the bulk write: it handles the block alignment a raw
device demands, and on macOS it is the code that has actually produced a card that
booted. Windows has no dd and writes through its own sink instead, so this is the
shared half of that split rather than the universal answer.

The interface is the whole point -- `write(chunk)` and `close() -> (rc, stderr)`. As
long as a platform can offer that, `writer.py` does not care what is underneath.
"""
import subprocess
import threading


class DDSink:
    def __init__(self, target, ibs="1m", obs="4m", conv=""):
        self.target = target
        # conv=fsync makes dd's exit code mean the data reached the medium rather than
        # the page cache. macOS's dd has no such option and does not need one: the write
        # goes to the character device, which does not buffer.
        cmd = ["dd", "of=%s" % target, "ibs=%s" % ibs, "obs=%s" % obs]
        if conv:
            cmd.append("conv=%s" % conv)
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE)
        # Drain stderr on a thread. Left unread, a child that fails early fills its
        # stderr pipe and blocks, and its message -- the one thing that explains the
        # failure -- is never seen.
        self._err = ""
        self._t = threading.Thread(target=self._drain, daemon=True)
        self._t.start()

    def _drain(self):
        try:
            self._err = self.proc.stderr.read().decode("utf-8", "replace")
        except Exception:
            self._err = ""

    def write(self, chunk):
        self.proc.stdin.write(chunk)

    def close(self):
        """Returns (returncode, stderr). Closing stdin is what lets dd flush and exit."""
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            rc = self.proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            rc = -1
        self._t.join(timeout=5)
        return rc, (self._err or "").strip()
