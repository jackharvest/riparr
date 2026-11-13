#!/usr/bin/env python3
"""An empty MakeMKV scan must not turn a video disc into a data-disc ISO.

When MakeMKV refuses to work (an expired beta key) its `info` output has no titles,
which used to send an encrypted DVD down the data-disc path: a sector copy, mostly
zeros, filed in the library as an ISO. These check the guard that now stands in front
of that path.

Run: python3 server/notitles-guard.test.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_tmp = tempfile.mkdtemp(prefix="riparr-notitles-test-")
os.environ["RIPARR_DB"] = os.path.join(_tmp, "test.db")
os.environ["RIPARR_APPLIANCE"] = "0"

from riparr import rip  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, wanted %r" % (name, got, want))
        failures.append(name)


EXPIRED_SCAN = "\n".join([
    'MSG:1005,0,1,"MakeMKV v2.0.0 linux(arm64-release) started","%1 started",'
    '"MakeMKV v2.0.0 linux(arm64-release)"',
    'MSG:5021,260,1,"This application version is too old. Please download the latest '
    'version at http://www.makemkv.com/ or enter a registration key to continue using '
    'the current version.","%1","x"',
    'DRV:0,2,999,1,"BD-RE","Example Disc","/dev/sr0"',
])

DATA_SCAN = "\n".join([
    'MSG:1005,0,1,"MakeMKV v2.0.0 linux(arm64-release) started","%1 started","x"',
    'MSG:5010,0,0,"Failed to open disc","Failed to open disc"',
])

print("Reading MakeMKV's messages")
msgs = rip.scan_messages(EXPIRED_SCAN)
check("two MSG lines kept", len(msgs), 2)
check("code comes first", msgs[1].split(": ")[0], "5021")
check("an expired key is a refusal", bool(rip.scan_refusal(msgs)), True)
check("a disc that won't open is not a refusal", rip.scan_refusal(
    rip.scan_messages(DATA_SCAN)), None)
check("text alone is enough", bool(rip.scan_refusal(
    ["9999: The beta key has expired"])), True)

print("Video folders in a disc listing")
check("DVD", rip.no_titles_folders("VIDEO_TS/\nVIDEO_TS/VIDEO_TS.IFO\nAUDIO_TS/\n"),
      ["VIDEO_TS"])
check("Blu-ray, ./-prefixed", rip.no_titles_folders("./BDMV/index.bdmv\n./CERTIFICATE/\n"),
      ["BDMV"])
check("lower case still counts", rip.no_titles_folders("video_ts/vts_01_1.vob\n"),
      ["VIDEO_TS"])
check("a disc of files", rip.no_titles_folders("setup.exe\nDATA/\nREADME.TXT\n"), [])
check("an empty VIDEO_TS is a disc of files",
      rip.no_titles_folders("VIDEO_TS/\nAUDIO_TS/\nphotos/a.jpg\n"), [])

print("Each scan starts with no messages")
rip._last_scan["messages"] = ["5021: This application version is too old."]
_P_MOCK = rip.P.MOCK
rip.P.MOCK = True
rip.read_titles("/dev/sr0")
rip.P.MOCK = _P_MOCK
check("the last disc's refusal doesn't carry over", rip._last_scan["messages"], [])

print("The guard")
renewals = []
rip._try_key_renewal = lambda: renewals.append(1) or "Put the disc back in."


def guard(messages, folders):
    rip._last_scan["messages"] = messages
    rip.video_folders = lambda device: folders
    try:
        rip.guard_no_titles("/dev/sr0")
    except rip.RipFailed as e:
        return str(e)
    return None


err = guard(msgs, [])
check("refused scan stops the job", err is not None, True)
check("and says MakeMKV refused", "MakeMKV wouldn't read this disc" in (err or ""), True)
check("and tried to renew the key", len(renewals), 1)
err = guard([], ["VIDEO_TS"])
check("unreadable DVD stops the job", "This is a DVD" in (err or ""), True)
err = guard([], ["BDMV"])
check("unreadable Blu-ray stops the job", "This is a Blu-ray" in (err or ""), True)
check("a real data disc goes on to be saved", guard([], []), None)

print()
if failures:
    print("%d failed: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("All passed.")
