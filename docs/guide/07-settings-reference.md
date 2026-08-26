# 7. Settings Reference

[← Ripping discs](06-ripping-discs.md) · [Guide index](README.md) · [Next: Troubleshooting →](08-troubleshooting.md)

Every setting, and whether you should care. **Most people never open this page.** The one
exception is [the MakeMKV key](#makemkv-key).

---

## MakeMKV key

**The only setting that needs periodic attention, and Riparr tries hard to make it not
your problem.**

MakeMKV does the actual disc reading. Its free key **expires roughly every 60 days.**

| Setting | Notes |
|---|---|
| **Key** | Paste a free beta key or a purchased permanent key |
| **Expires** | Shown as a date and a countdown |
| **Warn me before expiry** | Default: 7 days |
| **Notify via** | Web banner, plus any [notification channel](#notifications) you've set up |

**Riparr warns you before it breaks, never after.** It will not let a key silently expire
mid-rip on a box with no screen.

**Buy the permanent key** if you rip regularly. It's a one-time purchase and it removes
the only recurring chore in the entire product.

> MakeMKV is made by GuinpinSoft, not by Riparr, and its licence agreement is between
> you and them. Riparr asks you to read and accept it during setup, then downloads and
> installs it for you. See [D14](../../DECISIONS.md).

## Library

| Setting | Notes |
|---|---|
| **Share** | Where finished rips go. Re-run discovery to change it. |
| **Credentials** | Username/password for the share |
| **Movie folder** | e.g. `/Media/Movies` |
| **TV folder** | e.g. `/Media/TV` |
| **Test write** | Re-run the setup check any time. Run this first if anything looks wrong. |

## Naming

| Setting | Default |
|---|---|
| **Movie template** | `{Title} ({Year})/{Title} ({Year}).mkv` |
| **TV template** | `{Title} ({Year})/Season {Season:00}/{Title} - S{Season:00}E{Episode:00} - {EpisodeTitle}.mkv` |
| **On unknown disc** | Ask me *(recommended)* / Use disc label / Skip |

`Ask me` is the default because a wrongly named file silently pollutes your library, which
is worse than one that's waiting for ten seconds of your attention.

## Track selection

Bigger effect on file size than anything else here.

| Setting | Default |
|---|---|
| **Audio languages** | Your locale language + original |
| **Subtitle languages** | Your locale language |
| **Keep forced subtitles** | On — these are the subtitles for alien/foreign dialogue |
| **Keep commentary tracks** | Off |
| **Minimum title length** | 120 seconds — filters menus and logo stings |
| **Rip mode** | Main title *(default)* / All titles / Full disc backup |

Keeping every language and commentary can roughly double file size for no benefit most
people notice.

## Storage & transfer

Mostly informational. The defaults adapt to your card.

| Setting | Notes |
|---|---|
| **Mode** | `Automatic` *(recommended)* — streams when space is tight, rips at full speed and ejects early when there's room |
| **Verify after transfer** | `Quick` *(default)* — asks your library how big the file is and compares it with what was sent. Nearly free, and it catches what actually goes wrong: a truncated transfer, a share that filled up, a write that was refused. `Deep` also reads every byte back and hashes it, so it catches silent corruption too — but it downloads the whole film again, roughly doubling the time after a rip and needing as much free space on the card as the film itself. `Don't verify` trusts the upload. The choice applies to automatic and manual rips alike. |
| **Keep local copy** | On. Riparr retains the local copy until it needs the room, so a downstream problem is a re-copy instead of a re-rip. |
| **Space remaining** | Shown in discs, not gigabytes |

> **Resolved 2026-08-26.** Deep verification is no longer the only option, and it is no
> longer the default. It reads the whole file back, so on top of the time it needs as much
> free space as the film — the read-back has to land somewhere, because `smbclient` cannot
> stream it. Quick verification catches the failures that actually happen and costs
> nothing, so it is the default; deep is there for an archive you never intend to re-rip.

## Handoff

For sending finished rips somewhere else — a transcoder, an automation.

| Setting | Notes |
|---|---|
| **Watch folder mode** | Write to a staging path instead, for Tdarr / Unmanic to pick up |

The completion webhook moved to **Notifications** below, where the rest of the ways
Riparr can reach you now live.

**Riparr does not transcode.** This class of board would take days and the result would be poor.
Hand off to a real machine.

## Notifications

On **Settings → Connect**. This is how the box reaches you once you have walked away
from it — the LED only helps if you are in the room.

| Setting | Notes |
|---|---|
| **Tell me when** | Which events are worth a notification. Four are on by default |
| **ntfy** | Least work by far: pick an unguessable topic, install the app, subscribe. No account |
| **Discord** | Paste a webhook URL from Server Settings → Integrations |
| **Email** | SMTP. The most configuration, and works everywhere |
| **Webhook** | POSTs JSON — event, title, body, hostname — for Home Assistant, n8n, anything |

Each has a **Save and send a test** button, which saves what is on screen before it
sends, so you are testing what you just typed.

**The one worth having on:** *The MakeMKV key is about to expire*. A lapsed key is the
usual way a working box quietly stops working, and it always happens while you are not
looking at the web page.

## Disc history

Every disc Riparr has seen, by fingerprint.

| Action | Effect |
|---|---|
| **Re-rip** | Put the disc back in the tray and rip it again, duplicate flag and all |
| **Forget** | Drops Riparr's memory of the disc entirely, including any correction you made |

This is what makes Riparr only ask you about a problem disc once, ever.

## System

| Setting | Notes |
|---|---|
| **Export settings** | Downloads a JSON file. **Do this once you're set up.** Rebuilding a card becomes a 30-second job. |
| **Import settings** | Restore from that file |
| **Change password** | Web interface login. Forgotten it? Put an empty file called `riparr-reset` on the card's boot partition — no re-flash, and nothing else is lost |
| **Update** | Check for and install a new Riparr version |
| **Logs** | Download a log bundle for bug reports |
| **Restart / shut down** | In the account menu (the person icon, top right), not on a page. Rarely needed — the box is built to survive losing power, but shutting down here is the safe way to stop it before unplugging |

---

[← Ripping discs](06-ripping-discs.md) · [Guide index](README.md) · [Next: Troubleshooting →](08-troubleshooting.md)
