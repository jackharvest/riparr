"""The mount script against a fake NAS, and the diagnostics zip against a leak.

Why each case is here:

  linkstation  A NAS whose Samba predates 4.0 speaks SMB 2.0 at most. The script used
               to stop at 2.1, so smbclient's Test passed while the mount failed on
               every boot, and the page blamed a sleeping NAS. Must mount on 2.0.
  badpw        A wrong password must stop at the first try: every older version only
               says the same thing, and each try is a failed login the NAS may count.
  data         Data discs were added to db.KINDS and never to the script, so a data
               destination on its own share was never mounted.
  diagnostics  The zip is a file people email. No share password, key or token may
               appear anywhere in it -- not in settings, not in a log line that
               happened to print one.

Runs the real tools/mount-library.sh with `mount`, `mountpoint` and friends stubbed on
PATH, so it needs bash and nothing else; no root, no kernel, no NAS.
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "tools", "mount-library.sh")
fails = 0


def check(name, ok, detail=""):
    global fails
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + detail) if detail and not ok else ""))
    fails += 0 if ok else 1


def make_db(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE shares (id INTEGER PRIMARY KEY, name, host, path, username, "
              "password, is_default, verified_at)")
    c.execute("CREATE TABLE settings (key PRIMARY KEY, value)")
    rows = [(1, "dumps", "192.168.1.20", "Share/Riparr/Riparr Dumps", "admin", "pw-one-secret", 1),
            (2, "data", "192.168.1.20", "Share/Riparr/Riparr Dumps/Data", "admin", "pw-one-secret", 0),
            (3, "films", "192.168.1.20", "Riparr/Movies", "admin", "pw-one-secret", 0)]
    c.executemany("INSERT INTO shares VALUES (?,?,?,?,?,?,?,NULL)", rows)
    c.execute("INSERT INTO settings VALUES ('data_share_id', ?)", (json.dumps(2),))
    c.execute("INSERT INTO settings VALUES ('movie_share_id', ?)", (json.dumps(3),))
    c.commit()
    c.close()


FAKE_MOUNT = r'''#!/bin/bash
echo "$*" >> "$MT/calls"
case "$MODE:$*" in
  linkstation:*vers=2.0*) exit 0 ;;
  linkstation:*) echo "mount error(112): Host is down"; exit 32 ;;
  badpw:*) echo "mount error(13): Permission denied"; exit 32 ;;
  gone:*) echo "mount error(115): Operation now in progress"; exit 32 ;;
esac
'''


def run_script(tmp, mode):
    bin_ = os.path.join(tmp, "bin")
    os.makedirs(bin_, exist_ok=True)
    stubs = {"mount": FAKE_MOUNT, "mountpoint": "#!/bin/bash\nexit 1\n",
             "mount.cifs": "#!/bin/bash\nexit 0\n", "dmesg": "#!/bin/bash\n",
             "id": "#!/bin/bash\necho 1000\n", "chown": "#!/bin/bash\nexit 0\n"}
    for n, body in stubs.items():
        p = os.path.join(bin_, n)
        with open(p, "w") as f:
            f.write(body)
        os.chmod(p, 0o755)
    with open(SCRIPT) as f:
        body = f.read().replace("/run/riparr-cred", os.path.join(tmp, "cred"))
    script = os.path.join(tmp, "ml.sh")
    with open(script, "w") as f:
        f.write(body)
    open(os.path.join(tmp, "calls"), "w").close()
    status = os.path.join(tmp, "var", "mount-status.tsv")
    if os.path.exists(status):
        os.remove(status)
    env = dict(os.environ, MT=tmp, MODE=mode, PATH=bin_ + os.pathsep + os.environ["PATH"],
               RIPARR_DB=os.path.join(tmp, "var", "riparr.db"),
               RIPARR_LIBRARY_MOUNT=os.path.join(tmp, "srv", "library"),
               RIPARR_PYTHON=sys.executable)
    out = subprocess.run(["bash", script], env=env, capture_output=True, text=True)
    with open(os.path.join(tmp, "calls")) as f:
        calls = f.read().splitlines()
    rows = {}
    if os.path.exists(status):
        for line in open(status).read().splitlines():
            p = line.split("\t")
            rows[int(p[0])] = p
    return out, calls, rows


def main():
    tmp = tempfile.mkdtemp(prefix="riparr-mount-")
    try:
        os.makedirs(os.path.join(tmp, "var", "logs"))
        make_db(os.path.join(tmp, "var", "riparr.db"))

        print("mount-library.sh")
        out, calls, rows = run_script(tmp, "linkstation")
        check("exit 0", out.returncode == 0, out.stderr)
        check("data share is mounted", 2 in rows, str(rows))
        check("every share mounts on SMB 2.0",
              len(rows) == 3 and all(r[1] == "1" and r[3] == "2.0" for r in rows.values()),
              str(rows))
        check("2.0 is reached after 3.1.1, 3.0, 2.1", len(calls) == 12, "%d calls" % len(calls))
        check("password never on a command line",
              not any("pw-one-secret" in c for c in calls))

        out, calls, rows = run_script(tmp, "badpw")
        check("wrong password: one try per share", len(calls) == 3, "%d calls" % len(calls))
        check("wrong password: error recorded",
              all(r[1] == "0" and "error(13)" in r[5] for r in rows.values()), str(rows))

        out, calls, rows = run_script(tmp, "gone")
        check("unreachable: one try per share", len(calls) == 3, "%d calls" % len(calls))

        log = open(os.path.join(tmp, "var", "logs", "mount.txt")).read()
        check("mount.txt has the NAS's own error", "error(13)" in log and "SMB 2.0" in log)
        check("no credential file left behind",
              not [f for f in os.listdir(tmp) if f.startswith("cred")])

        # ── the service side, against the same files ──
        os.environ["RIPARR_DB"] = os.path.join(tmp, "var", "riparr.db")
        sys.path.insert(0, HERE)
        from riparr import platform as P
        print("platform")
        run_script(tmp, "badpw")
        st = P.mount_status()
        check("mount_status reads the file", st.get(2, {}).get("error", "").startswith("mount error(13)"), str(st))
        check("permission denied explained",
              "username or password" in P.mount_reason("mount error(13): Permission denied"))
        check("dialect failure explained",
              "SMB 2 or 3" in P.mount_reason("mount error(112): Host is down"))
        check("unknown error passed through",
              "weird thing" in P.mount_reason("weird thing"))

        print("diagnostics")
        from riparr import db, diagnostics as D
        db.set("makemkv_key", "T-" + "x" * 64)
        db.set("discord_webhook", "https://discord.com/api/webhooks/1/TOKENVALUE123")
        name, data = D.build()
        z = zipfile.ZipFile(io.BytesIO(data))
        text = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
        check("zip has summary and mount log",
              {"riparr-diagnostics/summary.txt", "riparr-diagnostics/mount.txt"}
              <= set(z.namelist()), str(z.namelist()))
        for secret in ("pw-one-secret", "TOKENVALUE123", "x" * 64):
            check("no %s… in the zip" % secret[:8], secret not in text)
        check("scrub catches password=… in free text",
              "hunter22" not in D.scrub("mount -o password=hunter22,user=a", []))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%s" % ("all pass" if not fails else "%d FAILED" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
