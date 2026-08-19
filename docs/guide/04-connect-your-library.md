# 4. Connect Your Library

[← First boot](03-first-boot.md) · [Guide index](README.md) · [Next: Library layout →](05-library-layout.md)

**About 1 minute.** This is where your finished rips will land — your NAS, your server,
wherever Plex or Jellyfin already reads from.

---

## Pick your share

Riparr looks around your network and lists the SMB shares it can find. Pick yours from
the list rather than typing a path — most setup problems are typo'd paths, and this skips
them entirely.

Not listed? Enter it manually as `\\server\share` or `//server/share`. Shares can be
hidden from discovery for legitimate reasons.

## Credentials

Enter the username and password for that share. If it allows guest access, leave them
blank.

**Use a dedicated account with write access to just your media folders** if your NAS makes
that easy. Riparr only ever needs to write into the library path you choose.

## The test write

Riparr writes a small test file, reads it back, and deletes it. **Don't skip this.**

It's checking three things that all look identical from the outside until 3am on your
first rip:

- The share is reachable
- The credentials work
- Riparr can actually **write** — not just connect

A share that mounts read-only is the classic one. It looks completely fine until the first
rip finishes and has nowhere to go.

## Choose the folder

Point Riparr at where your library lives — typically something like:

```
/Media/Movies
/Media/TV
```

Riparr creates per-title folders inside these. It doesn't touch anything already there.

## What happens if the share goes offline later

Nothing dramatic, by design.

- **Mid-rip:** the rip keeps going, buffering onto the SD card. If the share comes back
  within the hour or so of buffer, you'll never know it happened.
- **Buffer fills:** the rip **pauses** and holds the disc. It does not fail and does not
  lose your work. The LED signals it and the web page explains.
- **Share returns:** everything resumes where it stopped.

You will not come back to a bricked box or a half-written file in your library.

---

[← First boot](03-first-boot.md) · [Guide index](README.md) · [Next: Library layout →](05-library-layout.md)
