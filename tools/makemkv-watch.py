#!/usr/bin/env python3
"""Notice a new MakeMKV release and prepare the pin for it.

Run daily by .github/workflows/makemkv-watch.yml, and by hand whenever you like:

    python3 tools/makemkv-watch.py            # check, and rewrite the pin if newer
    python3 tools/makemkv-watch.py --check    # only say what it would do

A box only ever installs the MakeMKV whose checksums are pinned in
packaging/makemkv-manifest.json. That is the safety property: a mirror, or makemkv.com
itself, can only hand a box the right bytes or none. So a new MakeMKV does not reach a
box by the box noticing it -- it reaches the box in a Riparr release, through the
Updates page, like any other change. This script is the part of that a person used to
do by hand:

  - find the newest version makemkv.com lists;
  - fetch both tarballs from makemkv.com *and* from Launchpad's PPA, and accept them
    only if the two agree byte for byte (and the Internet Archive too, if it has them);
  - compare the licence, the EULA and the disc attribute IDs Riparr reads against the
    version pinned now, so a reviewer sees what changed rather than having to look;
  - rewrite the manifest and the fallback copy in server/riparr/makemkv.py.

It never makes a release. Building on an arm64 board is the one check it cannot do.

Exit status: 0 nothing to do or pin written; 3 a newer version exists but cannot be
verified yet (one source only, or they disagree); 1 something broke. Writes a Markdown
report to --report (default makemkv-report.md) and, under GitHub Actions, sets the
`version` and `changed` outputs.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import urllib.request
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MANIFEST = os.path.join(ROOT, "packaging", "makemkv-manifest.json")
FALLBACK = os.path.join(ROOT, "server", "riparr", "makemkv.py")

DOWNLOAD_PAGE = "https://www.makemkv.com/download/"
SITE = "https://www.makemkv.com/download/makemkv-{kind}-{v}.tar.gz"
PPA_POOL = ("https://ppa.launchpadcontent.net/heyarje/makemkv-beta/ubuntu/pool/main/m/"
            "makemkv-{kind}/")
PPA = PPA_POOL + "makemkv-{kind}_{v}.orig.tar.gz"
PPA_FILES = ("https://launchpad.net/~heyarje/+archive/ubuntu/makemkv-beta/+files/"
             "makemkv-{kind}_{v}.orig.tar.gz")
CDX = ("https://web.archive.org/cdx/search/cdx?url=makemkv.com/download/"
       "makemkv-{kind}-{v}.tar.gz&output=json&filter=statuscode:200")
ARCHIVE = "https://web.archive.org/web/{ts}id_/" + SITE
UA = {"User-Agent": "Mozilla/5.0 (compatible; riparr-makemkv-watch)"}
KINDS = ("oss", "bin")


def vtuple(v):
    return tuple(int(x) for x in v.split("."))


def fetch(url, timeout=300):
    """Bytes at a URL, undoing Content-Encoding -- makemkv.com gzips its .tar.gz again."""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            data = zlib.decompress(data, 16 + zlib.MAX_WBITS)
    return data


def try_fetch(url, timeout=300):
    try:
        return fetch(url, timeout), None
    except Exception as e:
        return None, str(e)[:120]


def latest_listed():
    """The newest version makemkv.com lists, else the newest in the PPA. None if neither."""
    found = []
    page, _ = try_fetch(DOWNLOAD_PAGE, 60)
    if page:
        found += re.findall(r"MakeMKV\s+v?(\d+\.\d+\.\d+)", page.decode("utf-8", "replace"))
    if not found:
        pool, _ = try_fetch(PPA_POOL.format(kind="oss"), 60)
        if pool:
            found += re.findall(r"makemkv-oss_(\d+\.\d+\.\d+)\.orig", pool.decode())
    return max(found, key=vtuple) if found else None


def sha(b):
    return hashlib.sha256(b).hexdigest()


def tar_text(blob, suffix):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
        for m in t.getmembers():
            if m.name.endswith(suffix):
                return t.extractfile(m).read().decode("utf-8", "replace")
    return None


def tar_has(blob, suffix):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
        return any(m.name.endswith(suffix) for m in t.getmembers())


def attribute_ids(blob):
    """Every `ap_iaName=N` in the OSS tree. Riparr reads some of these by number."""
    ids = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
        for m in t.getmembers():
            if m.isfile() and m.name.endswith(".h"):
                txt = t.extractfile(m).read().decode("utf-8", "replace")
                ids.update(re.findall(r"\b(ap_ia\w+)\s*=\s*(\d+)", txt))
    return ids


def without_copyright(text):
    return "\n".join(l for l in (text or "").splitlines() if "Copyright" not in l)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    ap.add_argument("--report", default="makemkv-report.md")
    a = ap.parse_args()

    with open(MANIFEST) as f:
        manifest = json.load(f)
    current = manifest["version"]
    latest = latest_listed()
    lines = []

    def done(code, changed=False, version=""):
        with open(a.report, "w") as f:
            f.write("\n".join(lines) + "\n")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write("changed=%s\nversion=%s\n" % ("true" if changed else "false", version))
        print("\n".join(lines))
        sys.exit(code)

    if not latest:
        lines.append("Could not read a MakeMKV version from makemkv.com or Launchpad.")
        done(1)
    if vtuple(latest) <= vtuple(current):
        lines.append("MakeMKV %s is pinned and is the newest listed (%s). Nothing to do."
                     % (current, latest))
        done(0)

    v = latest
    lines += ["# MakeMKV %s is out" % v, "",
              "Pinned now: **%s**. Riparr only installs a pinned version, so boxes keep "
              "%s until this is merged and released." % (current, current), "",
              "## Checksums", ""]

    new_pkgs, blobs, problems = [], {}, []
    for kind in KINDS:
        name = "makemkv-%s-%s.tar.gz" % (kind, v)
        site, site_err = try_fetch(SITE.format(kind=kind, v=v))
        ppa, ppa_err = try_fetch(PPA.format(kind=kind, v=v))
        if site is None or ppa is None:
            problems.append("%s: makemkv.com %s, Launchpad %s" % (
                name, "ok" if site else "failed (%s)" % site_err,
                "ok" if ppa else "not there yet (%s)" % ppa_err))
            continue
        if sha(site) != sha(ppa):
            problems.append("%s: makemkv.com and Launchpad serve **different files** "
                            "(%s… vs %s…)" % (name, sha(site)[:12], sha(ppa)[:12]))
            continue
        digest = sha(site)
        urls = [{"where": "makemkv.com", "url": SITE.format(kind=kind, v=v)},
                {"where": "Launchpad PPA", "url": PPA.format(kind=kind, v=v)},
                {"where": "Launchpad (files)", "url": PPA_FILES.format(kind=kind, v=v)}]
        agreed = "makemkv.com and Launchpad"
        cdx, _ = try_fetch(CDX.format(kind=kind, v=v), 60)
        rows = json.loads(cdx)[1:] if cdx else []
        if rows:
            ts = rows[-1][1]
            arch, _ = try_fetch(ARCHIVE.format(ts=ts, kind=kind, v=v))
            if arch is not None and sha(arch) == digest:
                urls.append({"where": "Internet Archive",
                             "url": ARCHIVE.format(ts=ts, kind=kind, v=v)})
                agreed += " and the Internet Archive"
        lines.append("- `%s` `%s` — %s agree" % (name, digest, agreed))
        blobs[kind] = site
        new_pkgs.append({"name": name, "sha256": digest, "bytes": len(site), "urls": urls})

    if problems:
        lines += ["", "**Not verified yet.** Nothing has been changed:", ""]
        lines += ["- " + p for p in problems]
        done(3, version=v)

    # What changed, for the reviewer. Against the pinned version, fetched from Launchpad
    # (which keeps old versions; makemkv.com does not).
    lines += ["", "## Against %s" % current, ""]
    old = {k: try_fetch(PPA.format(kind=k, v=current))[0] for k in KINDS}
    if old["oss"] and old["bin"]:
        for label, kind, suffix in (("Licence", "oss", "License.txt"),
                                    ("EULA", "bin", "src/eula_en_linux.txt")):
            same = without_copyright(tar_text(old[kind], suffix)) == \
                   without_copyright(tar_text(blobs[kind], suffix))
            lines.append("- %s: %s" % (label, "unchanged apart from the copyright line"
                                       if same else "**changed — read it before merging**"))
        a_old, a_new = attribute_ids(old["oss"]), attribute_ids(blobs["oss"])
        moved = sorted(k for k in a_old if a_new.get(k) != a_old[k])
        lines.append("- Disc attribute IDs: %s" % (
            "unchanged (%d, including ap_iaSegmentsMap=%s, the play-all order)"
            % (len(a_new), a_new.get("ap_iaSegmentsMap", "?")) if not moved else
            "**changed: %s**" % ", ".join("%s %s→%s" % (k, a_old[k], a_new.get(k, "gone"))
                                         for k in moved)))
    else:
        lines.append("- Could not fetch %s from Launchpad to compare against." % current)
    lines.append("- arm64 `makemkvcon` in the bin package: %s" % (
        "yes" if tar_has(blobs["bin"], "bin/arm64/makemkvcon") else "**missing**"))

    lines += ["", "## Before releasing", "",
              "Building on an arm64 board is the one thing this cannot check. Merge, "
              "release, update a box from **System → Updates**, then press **Accept "
              "licence and upgrade** under MakeMKV and let it build."]

    if a.check:
        lines.insert(0, "_--check: nothing written._\n")
        done(0, version=v)

    manifest["version"] = v
    manifest["verified_against_official"] = True
    manifest["verified_at"] = __import__("datetime").date.today().isoformat()
    manifest["verified_note"] = "Confirmed by tools/makemkv-watch.py: %s." % (
        "; ".join("%s from %s" % (p["name"], ", ".join(u["where"] for u in p["urls"]))
                  for p in new_pkgs))
    manifest["packages"] = new_pkgs
    text = json.dumps(manifest, indent=2, ensure_ascii=False)
    text = re.sub(r'\{\n\s+"where": (".*?"),\n\s+"url": (".*?")\n\s+\}',
                  r'{"where": \1,\n         "url": \2}', text)
    with open(MANIFEST, "w") as f:
        f.write(text + "\n")

    # The fallback copy in makemkv.py: same version, same hashes.
    with open(FALLBACK) as f:
        src = f.read()
    block = re.search(r"_FALLBACK_MANIFEST = \{.*?\n\}\n", src, re.S)
    if not block:
        lines.append("\n**Could not find _FALLBACK_MANIFEST in makemkv.py** — update it by hand.")
        done(1)
    new_block = block.group(0).replace(current, v)
    for kind in KINDS:
        old_sha = re.search(r'makemkv-%s-%s\.tar\.gz",\s*"sha256": "([0-9a-f]{64})"'
                            % (kind, re.escape(v)), new_block)
        if old_sha:
            new_block = new_block.replace(
                old_sha.group(1), next(p["sha256"] for p in new_pkgs if "-%s-" % kind in p["name"]))
    src = src.replace(block.group(0), new_block)
    src = re.sub(r"Paraphrased from makemkv-oss-[\d.]+/License\.txt",
                 "Paraphrased from makemkv-oss-%s/License.txt" % v, src)
    with open(FALLBACK, "w") as f:
        f.write(src)
    done(0, changed=True, version=v)


if __name__ == "__main__":
    main()
