# 4. Connect Your Library

[← First boot](03-first-boot.md) · [Guide index](README.md) · [Next: Library layout →](05-library-layout.md)

**About 1 minute.** This is where your finished rips will land — your NAS, your server,
wherever Plex or Jellyfin already reads from.

---

## Pick your share

Riparr looks around your network and lists the SMB shares it can find. Pick yours from
the list rather than typing a path — most setup problems are typo'd paths, and this skips
them entirely.

Not listed? Enter it manually. Shares can be hidden from discovery for legitimate
reasons.

**Share and folder are two different boxes, and it matters which is which.** The
**share** is the top-level name your server publishes — one word, no slashes. The
**folder** is everything below it, and it can be several levels deep. So a NAS
publishing `Media`, with your films in `Media/Movies/4K`, is:

| Box | What goes in it |
|---|---|
| Server | `tower.local` |
| Share | `Media` |
| Folder | `Movies/4K` |

Riparr shows the whole path adding up as you type, under the boxes. If you paste a full
path into the Share box it sorts it out for you — but the preview is where you would
notice. Backslashes are fine and are converted. If the folder does not exist yet, Riparr
creates it.

> **If you get `NT_STATUS_LOGON_FAILURE`, check the share name before you check the
> password.** Many NAS boxes answer a share name they do not recognise with the same
> error they use for a bad password, rather than confirm which shares exist. Riparr's
> error message says this too, along with the exact path it was trying to write to.

## Credentials

Enter the username and password for that share. If it allows guest access, leave them
blank.

A domain account goes in as `DOMAIN\name`, `DOMAIN/name` or `name@domain` — all three
work, and Riparr splits the domain out for you.

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

## Where films and television go

**Settings → Library → Where things go** has one block per kind of disc, and each block
names a **share** and a **folder inside it**. That means all four of these are the same
control:

| What you want | Films | Television |
|---|---|---|
| Everything in one place | `Media` · `Movies` | `Media` · `TV` |
| Two folders, one server | `Media` · `Films/Bluray` | `Media` · `Shows` |
| Two different machines | `Media` on the NAS | `Video` on the spare box |
| Straight into an existing library | `Media` · `Movies` | `Media` · `TV Shows` |

Add a second share from the **Shares** section on the same page, then pick it in the
dropdown. Riparr creates per-title folders inside whatever you choose, and does not touch
anything already there.

Each block also says whether that share is **mounted** — which is what lets Riparr write
a rip straight into your library instead of staging it on the card first. On the
reference board that is about twice as fast and removes the card as a size limit. A share
you added since the last restart is mounted on the next one.

## What happens if the share goes offline later

Nothing dramatic, by design.

- **Mid-rip:** the rip keeps going, buffering onto the SD card. If the share comes back
  within the hour or so of buffer, you'll never know it happened.
- **Buffer fills:** the rip **pauses** and holds the disc. It does not fail and does not
  lose your work. The web page explains what happened.
- **Share returns:** everything resumes where it stopped.

You will not come back to a bricked box or a half-written file in your library.

---

[← First boot](03-first-boot.md) · [Guide index](README.md) · [Next: Library layout →](05-library-layout.md)
