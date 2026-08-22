"""
The optical drives Riparr runs with -- and the one buying decision that software
cannot rescue.

`boards.py` in the Preparer does this job for boards. This does it for drives, for the
same reason: the answer belongs in code the interface can read, not in a document that
goes stale the week after it is written.

## Why this file has to exist at all

**A drive that reads Blu-ray does not necessarily read 4K UHD Blu-ray, and nothing the
drive says will tell you which it is.** There is no MMC profile for UHD; a UHD disc
identifies as `BD-ROM` exactly like a 1080p one, and a drive that will never decrypt a
UHD disc advertises `BD-ROM` in its profile list exactly like one that will
(`optical.py` says the same thing from the hardware side). The difference is AACS 2.0,
and it is settled by the drive's chipset and firmware.

There are two ways to get past AACS 2.0, and only one of them exists here:

  * **The official path** -- an "UHD-Friendly" drive plus AACS-licensed player software
    on an Intel CPU with SGX, on Windows. Riparr is an ARM board running Linux. This
    path is not merely inconvenient, it is unavailable, and no amount of work on our
    side opens it.
  * **MakeMKV's LibreDrive** -- talks to the drive's own chipset beneath its firmware.
    This is the path Riparr uses, and it works on a specific and finite set of drives.

So the honest product answer to "which drive do I buy for 4K" is a *list*, published
before someone spends the money. `docs/guide/01-what-you-need.md` has promised that
list since the guide was written. This is it.

## Two axes, and people conflate them

**What it reads** -- DVD, Blu-ray, UHD. This is the capability question.

**How it is powered** -- slim (laptop-style) drives are 5V-only; full-size 5.25" drives
need 12V as well. This is what forks the build into Riparr Slim and Riparr Full
(`docs/design/hardware.md`), and it is *independent* of capability: there are slim UHD
drives and full-size DVD drives. Somebody who buys a full-size UHD drive for a Slim
enclosure has bought the right capability and the wrong box.

## What this file is not

It is not the authority on whether the drive in front of you will rip a UHD disc.
**MakeMKV is**, at runtime, and it is asked -- see `libredrive_status()`. This registry
is for the question asked *before* the drive is bought, when there is no drive to ask.
Where the two disagree, MakeMKV wins and the interface says so.

Firmware revisions are deliberately not enumerated. They change, MakeMKV's own
compatibility list is the living copy, and a stale version number printed with
confidence is worse than a pointer to the list -- the same rule R5 applies to disc
titles, applied to hardware.
"""

# Where the living list lives. Printed to the user rather than paraphrased.
LIBREDRIVE_LIST = "https://forum.makemkv.com/forum/viewtopic.php?f=19&t=19634"

# `uhd` values, worst to best:
#   "no"        the drive cannot read UHD media at all
#   "firmware"  the hardware can, but only on particular firmware -- may need flashing
#   "yes"       known-good with the firmware it normally ships with
#   "unknown"   not in this registry; MakeMKV is the only way to find out
UHD_LABEL = {
    "yes": "Reads 4K UHD",
    "firmware": "4K UHD on the right firmware",
    "no": "No 4K UHD",
    "unknown": "4K UHD unknown",
}


DRIVES = [
    {
        "id": "lg-bu40n",
        "name": "LG BU40N",
        "match": ["BU40N"],
        "form": "slim",
        "reads": ["dvd", "bluray", "uhd"],
        "uhd": "yes",
        "tier": "recommended",
        "note": "The one to buy for a Slim build that has to do 4K. A 5V slim drive "
                "with no 12V rail, and the most commonly confirmed LibreDrive UHD "
                "reader in this form factor.",
    },
    {
        "id": "lg-bu50n",
        "name": "LG BU50N",
        "match": ["BU50N"],
        "form": "slim",
        "reads": ["dvd", "bluray", "uhd"],
        "uhd": "firmware",
        "tier": "known",
        "note": "The BU40N's successor and the same shape. Newer units ship firmware "
                "that closes LibreDrive, so check the list before buying rather than "
                "after.",
    },
    {
        "id": "lg-wh16ns40",
        "name": "LG WH16NS40 / WH16NS60",
        "match": ["WH16NS40", "WH16NS60", "BH16NS55", "BH16NS40"],
        "form": "full",
        "reads": ["dvd", "bluray", "uhd"],
        "uhd": "firmware",
        "tier": "known",
        "note": "The full-size 5.25\" answer, and the drive most of the UHD ripping "
                "community runs. Needs the 12V rail -- a Riparr Full build -- and "
                "usually needs crossflashing to a specific firmware first.",
    },
    {
        "id": "asus-bw16d1ht",
        "name": "ASUS BW-16D1HT",
        "match": ["BW-16D1HT", "BW-16D1X"],
        "form": "full",
        "reads": ["dvd", "bluray", "uhd"],
        "uhd": "firmware",
        "tier": "known",
        "note": "Full-size, and firmware-dependent in the same way as the LG units. "
                "The external BW-16D1X-U is the same drive in a shell.",
    },
    {
        "id": "generic-bd",
        "name": "Any Blu-ray reader",
        "match": [],
        "form": "either",
        "reads": ["dvd", "bluray"],
        "uhd": "no",
        "tier": "fine",
        "note": "1080p Blu-ray and DVD are not firmware-sensitive: any working "
                "Blu-ray drive does both. This is the whole product for most people, "
                "and it is the cheap half of the shelf.",
    },
    {
        "id": "generic-dvd",
        "name": "Any DVD drive",
        "match": [],
        "form": "either",
        "reads": ["dvd"],
        "uhd": "no",
        "tier": "fine",
        "note": "Cheapest possible build. Slim DVD drives are 5V-only and make the "
                "enclosure and the power design markedly simpler.",
    },
]


def buying_guide():
    """The registry as the guide and the interface want it -- no matcher internals."""
    fields = ("id", "name", "form", "reads", "uhd", "tier", "note")
    return [{k: d[k] for k in fields} for d in DRIVES]


def match(vendor, model):
    """Identify a connected drive from what sysfs reports, or None.

    Matched on substrings of `vendor model` combined, because the two fields are split
    inconsistently and a USB-to-SATA bridge frequently rewrites the vendor to its own
    name -- so `HL-DT-ST` / `BD-RE  WH16NS40` and `ASMT` / `WH16NS40` have to reach the
    same answer. The model token is the part that is always somewhere in the string.
    """
    hay = ("%s %s" % (vendor or "", model or "")).upper().replace("-", "")
    for d in DRIVES:
        for token in d["match"]:
            if token.upper().replace("-", "") in hay:
                return d
    return None


def expectation(vendor, model, can_read_bluray=None):
    """What to expect of this drive for UHD, before MakeMKV has been asked.

    `can_read_bluray` is `optical.capabilities()`'s answer. It is worth having because
    it settles the negative case with certainty: a drive with no BD profile at all
    cannot read UHD media, whatever any list says, and telling somebody their DVD drive
    is "unknown for 4K" when it is flatly incapable is a worse answer than no answer.
    """
    known = match(vendor, model)
    if known:
        return known["uhd"], known
    if can_read_bluray is False:
        return "no", None
    return "unknown", None


# ─────────────────────────── what MakeMKV actually says ───────────────────────────

# MakeMKV reports LibreDrive per drive in its own drive information. In robot mode
# (`-r`) that arrives as free-text MSG lines rather than a structured field, so this
# reads the text -- which is why it is a small, tolerant matcher and not a parser.
#
# UNVERIFIED AGAINST A REAL DRIVE. Written from MakeMKV's documented output, and the
# first thing to check when the adapter arrives: run
#     makemkvcon -r --cache=1 info disc:0
# with a Blu-ray drive attached and confirm the wording below appears. If it does not,
# only the three constants here change -- every caller asks `libredrive_status()` and
# does not care how it knew.
_LIBREDRIVE_HINTS = (
    ("enabled", ("libredrive mode", "libredrive: enabled", "status: enabled")),
    ("possible", ("libredrive: possible", "status: possible")),
    ("no", ("libredrive: no", "status: no", "libredrive is not supported")),
)


def parse_libredrive(text):
    """`"enabled" | "possible" | "no" | None` from makemkvcon output.

    None means the output said nothing about LibreDrive, which is the common case on a
    DVD-only drive and is not a failure.
    """
    low = (text or "").lower()
    for verdict, hints in _LIBREDRIVE_HINTS:
        if any(h in low for h in hints):
            return verdict
    return None
