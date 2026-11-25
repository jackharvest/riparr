"""install.sh reads riparr.conf without running it.

It used to source the file, so a Wi-Fi name with a space in it ran the second word as a
command and `set -e` ended the install (#13). Runs the real block out of install.sh.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "install.sh"), encoding="utf-8").read()
m = re.search(r"(?ms)^PORT=9797\n.*?^fi\n", src)
assert m, "the riparr.conf block moved; fix this test"
fails = 0
with tempfile.TemporaryDirectory() as d:
    pwned = os.path.join(d, "pwned")
    for conf, want in (
            ("RIPARR_PORT=9898\nRIPARR_WIFI_SSID=Toucan Sandwich\n", "9898"),
            ("RIPARR_WIFI_SSID=Bob's Wi-Fi\nRIPARR_WIFI_PSK=a$b`c\"d\n", "9797"),
            ("RIPARR_WIFI_PSK=$(touch %s)\nRIPARR_PORT=9001\r\n" % pwned, "9001"),
            ('RIPARR_PORT="9002"\n', "9002"),
            ("RIPARR_PORT=1;touch %s\n" % pwned, "9797"),
            ("", "9797")):
        with open(os.path.join(d, "riparr.conf"), "w", newline="") as f:
            f.write(conf)
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + m.group(0) + 'echo "$PORT"'],
                           env=dict(os.environ, BOOT=d), capture_output=True, text=True)
        ok = r.returncode == 0 and r.stdout.strip() == want and not os.path.exists(pwned)
        fails += not ok
        print("  %s  %r -> %s" % ("PASS" if ok else "FAIL", conf, r.stdout.strip() or r.stderr))
sys.exit(1 if fails else 0)
