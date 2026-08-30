# 5. Library Layout & Naming

[← Connect your library](04-connect-your-library.md) · [Guide index](README.md) · [Next: Ripping discs →](06-ripping-discs.md)

**About 1 minute.** The defaults are correct for Plex and Jellyfin. If you use either,
accept them and move on.

---

## The defaults

**Movies**
```
Movies/
  Blade Runner (1982)/
    Blade Runner (1982).mkv
```

**TV**
```
TV/
  Twin Peaks (1990)/
    Season 01/
      Twin Peaks - S01E01 - Pilot.mkv
```

These follow the conventions Plex, Jellyfin, and Emby all expect. Files land already
matched — no "fix match" pass in Plex afterward.

## Naming templates

If you name things your own way, the templates are editable, Sonarr-style:

| Token | Becomes | Built? |
|---|---|---|
| `{Title}` | `Blade Runner` | yes |
| `{Year}` | `1982` | yes |
| `{Source}` | `DVD`, `Bluray`, `UHD` | yes |
| `{Season:00}` | `01` | yes |
| `{Episode:00}` | `01` | yes |
| `{EpisodeTitle}` | `Pilot` | yes |
| `{Quality}` | `Bluray-1080p` | **not yet** |
| `{Edition}` | `Director's Cut` | **not yet** |

The zeroes set the padding: `{Season:0}` gives `1`, `{Season:000}` gives `001`.

A file holding two episodes — a double-length premiere or finale — expands
`E{Episode:00}` to `E01-E02` by itself. That is the form Plex and Jellyfin both read as
one file containing two episodes, and you do not need to change the template to get it.

A token that is not built is left in the filename as written, rather than blanked — a
template with a typo should produce a visibly odd name, not a file called ` ().mkv`.
`{Quality}` waits on Riparr reading the video stream, which it does not do.

## Two copies of the same film

The DVD and the Blu-ray of one film produce the same title, so the default template
sends them to the same filename. **Riparr will not overwrite the first with the
second.** When the destination already exists and it was written by a *different* disc,
the new rip is saved alongside with its source on the end:

```
Movies/Arthur Christmas (2011)/
    Arthur Christmas (2011).mkv          ← the Blu-ray, ripped first
    Arthur Christmas (2011) - DVD.mkv    ← the DVD, ripped later
```

Plex and Jellyfin both read several files in one movie folder as **versions** of the
same film, so you get a "play version" choice rather than two entries. The rip that was
renamed says so on its History row.

Re-ripping the *same* disc still replaces its own file, which is what Re-rip is for.

To tag every rip from the start instead, put `{Source}` in the template:
`{Title} ({Year})/{Title} ({Year}) - {Source}.mkv`.

## How Riparr identifies a disc

**The name comes off the disc label.** Most Blu-rays carry a usable one —
`BLADE_RUNNER_2049` becomes `Blade Runner 2049`. Riparr tidies it and files it under
that.

**DVDs are rougher.** DVD volume labels are frequently garbage like `LOGICAL_VOLUME_ID`.
When the label gives nothing a person would accept as a name, Riparr asks rather than
inventing one.

**It does not guess a year.** A year only appears in a filename if it was in brackets on
the disc label or you typed one into the prompt. `Blade Runner 2049.mkv` is a name Plex
matches and claims nothing untrue; `Blade Runner (2049).mkv` would be a confident lie.

**It only asks once per disc.** Riparr remembers your correction against that specific
disc's fingerprint. Rip the same disc on the same box a year later and it already knows.

There is no film-metadata lookup. Riparr names films from the disc and lets Plex,
Jellyfin or Emby do the matching, which they are better at and already do. The one
exception is television, below, where the numbers have to be right *before* the file is
written.

## Two cases worth knowing about

**TV season discs.** Six titles of about 42 minutes each is a season disc, not a film
with five decoys. Riparr rips all six, in order, numbered and named.

The order comes from the disc. Most Blu-ray season discs carry a hidden "play all"
playlist, and that playlist is the disc's own record of what order its episodes go in —
when it's there, the order is a fact and Riparr just uses it. When it isn't, the order
comes from the disc's playlist numbering, which is right on almost every disc but not
all of them.

**Riparr shows you the plan once per season** — on the first disc, where one correction
fixes every disc after it — and again on any later disc whose order it couldn't read off
the disc itself.

The names come from [TVmaze](https://www.tvmaze.com), which needs no account.

**Disc order and broadcast order don't always agree.** Firefly is the famous one: the
disc opens with "Serenity", but it aired second, so every episode guide numbers "The
Train Job" as S01E01. Riparr always keeps the *disc's* order and only takes *names* from
the lookup — so when the two disagree you see it immediately, on the plan, before
anything is written. Shifting the first episode number renumbers and renames the whole
disc in one move.

**Later discs of the same season need no answer.** Correct disc one, and disc two
carries on from where it stopped.

**A disc that carries each episode twice** — once with the "next time" trailer, once
without — is normal, and Riparr keeps one of each. So is a season welded into a single
four-hour title, which Riparr will tell you about and rip as one file; splitting that
needs MKVToolNix on another machine.

**Decoy titles.** Some studios — Disney and Warner especially — put around a hundred
near-identical fake playlists on a disc specifically to defeat "pick the longest one."
Riparr has heuristics for it and will tell you when it's uncertain rather than silently
ripping a 90-minute loop of the same scene.

## What tracks get kept

Sensible defaults: your language, the main audio track, forced subtitles, no commentary.

Adjustable in [settings](07-settings-reference.md#track-selection) — keeping every dub and
commentary track can easily double file size.

---

[← Connect your library](04-connect-your-library.md) · [Guide index](README.md) · [Next: Ripping discs →](06-ripping-discs.md)
