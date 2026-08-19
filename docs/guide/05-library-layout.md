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

| Token | Becomes |
|---|---|
| `{Title}` | `Blade Runner` |
| `{Year}` | `1982` |
| `{Season:00}` | `01` |
| `{Episode:00}` | `01` |
| `{EpisodeTitle}` | `Pilot` |
| `{Quality}` | `Bluray-1080p` |
| `{Edition}` | `Director's Cut` |

A live preview shows the result against a real example as you type, so you can't save a
template that produces something broken.

## How Riparr identifies a disc

**Blu-ray usually just works.** Most Blu-rays carry the real title on the disc itself.
Riparr reads it, checks the runtime, matches against TMDB, and names the file.

**DVDs are rougher.** DVD volume labels are frequently garbage like `LOGICAL_VOLUME_ID`.
Riparr falls back to fingerprinting the disc structure — how many titles, how long each
is — and matching that against TMDB runtimes.

**When it isn't sure, it asks rather than guessing.** A wrongly named file is worse than
an unnamed one, because it quietly pollutes your library. Unidentified rips wait in the
queue with a "needs review" flag, and you pick the right match from a short list.

**It only asks once per disc.** Riparr remembers your correction against that specific
disc's fingerprint. Rip the same disc on the same box a year later and it already knows.

## Two cases worth knowing about

**TV season discs.** Six titles of about 42 minutes each is an episode disc. Riparr detects
the pattern and maps the titles onto the TMDB episode list — but disc order and broadcast
order don't always agree, so **check the first disc of a season.** Correct it there and the
rest of the season follows.

Every ripper gets this wrong. Riparr aims to get it right most of the time and to make the
fix take five seconds when it doesn't.

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
