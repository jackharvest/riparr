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

> **[unresolved]** MakeMKV can't be redistributed, so Riparr fetches it during setup
> rather than shipping it in the image. The exact flow isn't finalized.

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
| **Verify after transfer** | On. Reads the file back and checks it matches. Catches silent corruption. |
| **Keep local copy** | On. Riparr retains the local copy until it needs the room, so a downstream problem is a re-copy instead of a re-rip. |
| **Space remaining** | Shown in discs, not gigabytes |

> **[unresolved]** Verification reads the whole file back over WiFi, which on a Blu-ray
> costs about as long as the upload did. Whether it stays default-on is undecided.

## Handoff

For sending finished rips somewhere else — a transcoder, an automation.

| Setting | Notes |
|---|---|
| **Webhook on completion** | POSTs the file path and metadata when a rip is verified |
| **Watch folder mode** | Write to a staging path instead, for Tdarr / Unmanic to pick up |

**Riparr does not transcode.** A Pi Zero 2W would take days and the result would be poor.
Hand off to a real machine.

## Notifications

**[unresolved]** Planned: webhook, Discord, ntfy. "Your disc is done" on your phone.

## Disc history

Every disc Riparr has seen, by fingerprint.

| Action | Effect |
|---|---|
| **Forget disc** | Removes it so it can be re-ripped |
| **Edit correction** | Change the remembered title/episode mapping |
| **Force re-rip** | Rip again despite the duplicate flag |

This is what makes Riparr only ask you about a problem disc once, ever.

## System

| Setting | Notes |
|---|---|
| **Export settings** | Downloads a JSON file. **Do this once you're set up.** Rebuilding a card becomes a 30-second job. |
| **Import settings** | Restore from that file |
| **Change password** | Web interface login |
| **Update** | Check for and install a new Riparr version |
| **Logs** | Download a log bundle for bug reports |
| **Reboot / shut down** | Rarely needed — the box is built to survive losing power |

---

[← Ripping discs](06-ripping-discs.md) · [Guide index](README.md) · [Next: Troubleshooting →](08-troubleshooting.md)
