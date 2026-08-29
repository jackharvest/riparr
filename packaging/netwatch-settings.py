"""Read the watchdog's thresholds out of Riparr's database, as shell assignments.

A separate file rather than a heredoc inside netwatch.sh, because the shell script is
already the awkward half of this and embedding Python in it makes both harder to read
and impossible to lint on its own.

Read-only, and it never fails: netwatch.sh keeps working defaults, and a watchdog that
stops guarding because it could not read a preference is worse than one guarding on
sensible numbers. Printing nothing is how this says "use yours".
"""
import json
import sqlite3
import sys


def get(conn, key, default):
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
    except Exception:
        return default


def main():
    if len(sys.argv) < 2:
        return 0
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
    except Exception:
        return 0

    enabled = get(conn, "netwatch_enabled", True)
    reboot = get(conn, "netwatch_reboot", True)

    # Clamped, because this arrives from a web form. Under a minute the watchdog would
    # fight every momentary blip; over two hours it is not a watchdog.
    try:
        n = int(get(conn, "netwatch_minutes", 3))
    except (TypeError, ValueError):
        n = 3
    n = max(1, min(120, n))

    # The ladder is derived from one number rather than stored as three, so the three
    # can never drift into an order that makes no sense (reload before re-associate).
    #
    # "Never reboot" is expressed as a threshold nothing can reach rather than as a flag,
    # so the escalation in netwatch.sh needs no special case for it.
    print("ENABLED=%d" % (1 if enabled else 0))
    print("FAIL_ASSOC=%d" % n)
    print("FAIL_RELOAD=%d" % (2 * n))
    print("FAIL_REBOOT=%d" % ((4 * n) if reboot else 10 ** 9))
    return 0


if __name__ == "__main__":
    sys.exit(main())
