# 7. Settings Reference

[← Ripping discs](06-ripping-discs.md) · [Guide index](README.md) · [Next: Troubleshooting →](08-troubleshooting.md)

Every setting, and whether you should care. **Most people never open this page.** The one
exception is [the MakeMKV key](#makemkv-key).

---

## MakeMKV key

**The only setting that needs periodic attention, and Riparr tries hard to make it not
your problem.**

MakeMKV does the actual disc reading. Its free beta key **expires on a month boundary** —
not a fixed number of days after you enter one, so a key issued mid-month may last a day
or five weeks. Riparr says which month yours is good for rather than counting down to a
date it cannot know, and fetches the current key from GuinpinSoft's forum so replacing it
is one click.

| Setting | Notes |
|---|---|
| **Key** | Paste a free beta key or a purchased permanent key |
| **Expires** | The month this key is good for, and the date it stops. Never a countdown to a date GuinpinSoft has not published |
| **Warn me before expiry** | Default: 7 days |
| **Notify via** | Web banner, plus any [notification channel](#notifications) you've set up |

**Riparr warns you before it breaks, never after.** It will not let a key silently expire
mid-rip on a box with no screen.

**Buy the permanent key** if you rip regularly. It's a one-time purchase and it removes
the only recurring chore in the entire product.

> MakeMKV is made by GuinpinSoft, not by Riparr, and its licence agreement is between
> you and them. Riparr asks you to read and accept it during setup, then downloads and
> installs it for you.

**makemkv.com goes down for weeks at a time**, so Riparr does not depend on it. It tries
makemkv.com first, then Launchpad's `~heyarje/makemkv-beta` PPA, then the Internet
Archive's capture of makemkv.com's own download URL — in that order, until one works.
Every download is checked against a checksum pinned in Riparr's source, so a mirror can
only give Riparr the right file or none at all.

**Settings → General** also tracks makemkv.com and its forum separately, because they are
different machines and fail independently. The forum is where the free key lives, and it
is usually up when the site is not — which is the difference between "you are stuck" and
"go here and copy the key".

## Library

### Where things go

One block per kind of disc, each naming a **share** and a **folder inside it**. Because
both halves are choosable, "two folders on one server" and "two completely different
machines" are the same control.

| Setting | Notes |
|---|---|
| **Films → Share** | Which share. Defaults to the one you set up first |
| **Films → Folder** | The folder inside it, e.g. `Movies` or `Films/Bluray`. Several levels deep is fine |
| **Television → Share** | Can be the same share, or a different one entirely |
| **Television → Folder** | e.g. `TV` or `Shows` |

Under each block Riparr shows the full path it adds up to, and whether that share is
**mounted** — which is what lets a rip be written straight into your library instead of
being staged on the card first. A share added since the last restart is mounted on the
next one.

### Shares

| Setting | Notes |
|---|---|
| **Server** | A name or an address. `.local` names work if your NAS advertises one |
| **Share** | The top-level name your server publishes. One word, no slashes |
| **Folder** | Everything below it. Riparr creates it if it does not exist |
| **Username** | Optional. A domain account goes in as `DOMAIN\name`, `DOMAIN/name` or `name@domain` |
| **Test and save** | Writes a real file into the folder, reads it back, compares it, deletes it. A share is not saved until that passes |

The first share you add becomes the default, and adding more does not change that — so
adding a share for box sets cannot quietly redirect your films.

> **`NT_STATUS_LOGON_FAILURE` usually is not the password.** Many NAS boxes answer a
> share name they do not recognise with the same error they use for a bad password,
> rather than confirm which shares exist. Check the **Share** box first. Riparr's error
> message says so, and prints the exact path it was trying to write to.

## Naming

| Setting | Default |
|---|---|
| **Film template** | `{Title} ({Year})/{Title} ({Year}).mkv` |
| **TV template** | `{Title} ({Year})/Season {Season:00}/{Title} - S{Season:00}E{Episode:00} - {EpisodeTitle}.mkv` |
| **On unknown disc** | **Use the disc label** *(default)* / Ask me / Skip |

**This is only about the *name*.** Which title gets ripped is a separate setting, under
[Ripping](#ripping) — they used to be one, and answering the naming question also
silently answered the other one.

`Use the disc label` is the default because the box's promise is that you put a disc in
and walk away, and most rips land in a folder that gets tidied or compressed later
rather than straight into a library somebody is browsing. A folder named off the disc
label is something you can fix in ten seconds; a queue that stopped at 2am waiting for
you is not. Switch it to `Ask me` if your rips go directly into a library you browse.

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
| **When Riparr can't tell which title is the film** | **Use the most likely one** *(default)* / Ask me |
| **On a disc with a 3D version** | **Rip the 2D version** *(default)* / Rip the 3D version |

## Television

| Setting | Notes |
|---|---|
| **Look for season discs** | On. A disc with several titles of the same length is read as a season rather than a film with decoys. Turn it off if you only own films. |
| **Before ripping a season** | **Show me the plan when Riparr isn't sure** *(default)* / Always show me / Never. Most Blu-ray season discs carry a "play all" playlist, which is the disc's own record of its episode order — when that's present the order is a fact and there is nothing to check. When it isn't, the order is a good guess. The default asks only in the second case. **A disc with no season number always asks**, whatever this is set to, because there is no answer to get on with. |
| **Look up episode names** | On. From [TVmaze](https://www.tvmaze.com), no account needed. Off gives correctly numbered files with no names — Plex and Jellyfin match on the numbers, so they still land correctly. |
| **Specials go in** | `Season 00` *(default)* / `Specials`. Both are read as season zero. Set the season to 0 on the episode plan to file a disc here. |

The episode plan is a table, one row per file that will be written. Untick a row to skip
it, use the arrows to move an episode, type over a name to correct it, or change **first
episode** to shift the whole disc — the numbers and names update together as you go.
Later discs of the same season carry on from where the last one stopped without asking.

Keeping every language and commentary can roughly double file size for no benefit most
people notice.

## Storage & transfer

| Setting | Notes |
|---|---|
| **Mode** | `Straight to your library` *(default)* — MakeMKV writes onto your share as the disc is read, and nothing is staged on the card. Faster on this hardware (about 18 MB/s against the card's 9.4), no size ceiling, and no wear on the card. If the share isn't mounted when a disc goes in, that rip stages on the card by itself and is sent when the share comes back — so it is safe to leave on. `Onto the card first` is the answer if your NAS sleeps, your Wi-Fi is patchy, or you want deep verification. `Always burst` and `always stream` force one staged behaviour or the other. |
| **Verify after transfer** | `Quick` *(default)* — asks your library how big the file is and compares it with what was sent. Nearly free, and it catches what actually goes wrong: a truncated transfer, a share that filled up, a write that was refused. `Deep` also reads every byte back and hashes it, so it catches silent corruption too. `Don't verify` trusts the upload. Applies to automatic and manual rips alike. |
| **Keep local copy** | On. Riparr retains the local copy until it needs the room, so a downstream problem is a re-copy instead of a re-rip. |
| **Space remaining** | Shown in discs, not gigabytes |

**Deep verification is only offered when rips stage on the card.** It works by reading
the file back off the share and comparing it with the original, so it needs two copies.
Going straight to your library leaves one — hashing it against itself would pass every
time and prove nothing. Switch **Mode** to *onto the card first* if you want it, and give
the card room for two copies of the largest title you rip: the read-back has to land
somewhere, because `smbclient` cannot stream it.

That second copy is the only reason to buy a card bigger than 8 GB.

## If the connection drops

This board's Wi-Fi can stop passing traffic **while still reporting itself as
connected**. The radio wedges, the link still looks up, nothing is logged, and the box
sits there unreachable while otherwise running perfectly — on the reference unit, once,
for 17.5 hours. There is no event to react to, so the only way to catch it is to send a
packet and see whether anything answers.

Riparr pings your router once a minute and works up a ladder if nothing comes back.

| Setting | Notes |
|---|---|
| **Recover the connection automatically** | On. Off means Riparr notices nothing and does nothing — only worth it if something else on your network already watches this box |
| **Wait this long first** | `3` minutes *(default)*. Minutes of no answer before Riparr acts. Everything else follows from it: reconnect at **N**, reload the Wi-Fi driver at **2N**, restart the box at **4N** |
| **Restart the box as a last resort** | On. Only after reconnecting and reloading the driver have both failed |

**A restart never interrupts a rip.** If a disc is in progress Riparr waits for it to
finish — the box is already unreachable, so waiting costs nothing that isn't already
lost, whereas restarting would cost you the rip as well.

### Which way to move the number

**Shorter if your access point changes channel often.** On 5 GHz the higher channels are
shared with weather and military radar, and an access point using one is required to
move whenever it detects any. This board's Wi-Fi driver does not reliably follow that
move, so it can be left sitting on a channel nothing is using any more. If your router
picks channels automatically it may be doing this without telling you. A shorter wait
gets the box back sooner.

**Longer if your router reboots on a schedule**, or if your network drops briefly and
often for reasons you already know about. Otherwise the box spends its time recovering
from outages that were going to end on their own — and at the bottom of the ladder, that
means restarting itself for no reason.

Changes take effect within the minute. Nothing needs restarting.

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
from it — anything on the box itself only helps if you are in the room.

**Tell me when** is the list of events, at the top of the page. Riparr sends every one
you tick to every channel you set up. Four are on by default.

**Channels** below it is four rows, each wearing the mark of the service it configures.
Click a row to open its setup instructions and fields; only one is open at a time. A
channel Riparr has what it needs for shows its mark in full colour with a tick on the
corner, and the row says what it is pointed at — the topic, the address, the host — so
you can see which account you wired it to without opening it.

| Channel | Notes |
|---|---|
| **ntfy** | Least work by far: pick an unguessable topic, install the app, subscribe. No account, no signup |
| **Discord** | A webhook URL, plus your own user ID if you want it to ping you — see below |
| **Email** | SMTP. The most configuration, and works everywhere. Almost certainly needs an *app password*, not your normal one |
| **Webhook** | POSTs JSON — event, title, body, hostname — for Home Assistant, n8n, anything |

Each has a **Save and send a test** button, which saves what is on screen before it
sends, so you are testing what you just typed.

One channel is enough. Setting up two is only worth it if you want a copy of everything
somewhere permanent as well as a notification on your phone.

**The one worth having on:** *The MakeMKV key is about to expire*. A lapsed key is the
usual way a working box quietly stops working, and it always happens while you are not
looking at the web page.

*A new version of Riparr is available* is on by default too, and is quiet by nature —
each version is announced once, so it amounts to a handful of messages a year. It is
sent at a lower priority than the rest, because nothing is broken.

### Discord, if you want the box to tell *you*

A Discord webhook posts into a **channel**, which is a thing you find later. What
makes a phone buzz is being **mentioned**. Riparr does both, and they are separate
settings.

1. **Somewhere to post.** In Discord, **+** at the bottom of the server list →
   **Create My Own** → **For me and my friends**. Nobody else can see it, and a channel
   in it is a private feed your phone treats like any other.
2. **The webhook.** Hover the channel → the gear (**Edit Channel**) → **Integrations**
   → **Create Webhook** → **Copy Webhook URL**. *That URL is a password* — anyone
   holding it can post as Riparr.
3. **Check it.** Press **Check this webhook**. Riparr asks Discord whether it is real
   and tells you what it posts as. A URL that lost its tail in a clipboard otherwise
   fails silently forever.
4. **Mention me.** Turn on **Developer Mode** (User Settings → Advanced), right-click
   your own name → **Copy User ID**, paste it in. For a household, use a *role* ID with
   an `&` in front: `&123…`.

**Ping me for** is deliberately not the same list as **Tell me when**. Everything you
enable above still posts to the channel; only these light up your phone. *A rip
finished* is off by default because it is good news, and good news can wait.

## History

Every attempt, newest first — one row per try, not one per film. If a disc took five
goes, all five are here with what went wrong each time.

| Column | Notes |
|---|---|
| **Attempt** | "try 3 of 5" — the other four are rows you can read |
| **Size** | What actually landed |
| **Took** | Work done, not wall clock: retrying a job a day later does not make it a day long |
| **Where the time went** | The five stages, to scale. The bar under *Typical on this box* is the key |

**Where the time went** is the interesting column. About half of a DVD rip happens
before a single byte is written — MakeMKV decrypts in software and this class of board
is the constraint — so a box that looks idle for ten minutes is a box working hard, and
this is where you can see that.

### The four retries

Each one appears only when Riparr can actually do it.

| Button | When it shows | What it costs |
|---|---|---|
| **Retry upload** | The rip is still on the card | A re-copy. Minutes, and the disc stays on the shelf |
| **Retry fast verification** | The file is on your library | Seconds. Compares the size — catches a truncated transfer |
| **Retry deep verification** | The file is on your library | As long as the upload took, plus as much free space again as the film. Reads it all back and hashes it |
| **Retry rip** | The rip is gone or never finished | The whole thing. Put the disc back in the tray first |

## Discs

Every disc Riparr has seen, by fingerprint, with its poster.

| Action | Effect |
|---|---|
| **Re-rip** | Rips it again, duplicate flag and all. Leave the disc on the tray — Riparr pulls the tray in itself |
| **Forget** | Drops Riparr's memory of the disc entirely, including any correction you made |

This is what makes Riparr only ask you about a problem disc once, ever. A tile with a
warning triangle is a disc Riparr has seen but never finished a verified rip of.

**Put an already-ripped disc back in** and this page is where you land, with the film
highlighted and the date it went into your library. See
[Ripping discs](06-ripping-discs.md#putting-a-disc-back-in-that-youve-already-ripped) for
what the box does when no browser is open — including why the drive's front light can be
blinked but never switched on.

### Already-ripped discs

On **Settings → Ripping**.

| Setting | Notes |
|---|---|
| **Tell me with** | `The drive's own light` (default), `The tray`, `Both`, or `Nothing — just eject` |
| **Try the light** / **Try the tray** | Fires the signal now, with whatever disc is in the tray. Riparr cannot see the result, so watching it is the only test |

## Network

On **Settings → Network**. Riparr keeps a **list** of Wi-Fi networks rather than one, in
the order it should try them, the way a phone does.

The reason is the box is meant to be carried. Taking it to a friend's house to show it
off means their network has to be in it *before* you get there — there is no screen, and
you cannot type a password into a box that cannot reach the network your browser is on.

| Setting | Notes |
|---|---|
| **Known networks** | Best first. The box joins the highest one it can see |
| **Add a network** | Type a name and password for a network you are nowhere near. It joins when it can see it |
| **↑ ↓** | Reorder. Nothing is written until you press **Save this order** |
| **Forget** | Removes one. Riparr refuses to remove the last, which would strand the box |
| **Scan** | Everything in range now. Anything already saved says so |

The network the card was written with appears in this list automatically, so adding a
second one cannot lose it. Passwords are turned into a key before they are stored and
the password itself is never written down.

> Changing Wi-Fi is the one setting that can move the box somewhere your browser cannot
> follow. If nothing associates within twenty seconds, Riparr puts the previous settings
> back by itself.

## System

| Setting | Notes |
|---|---|
| **Export settings** | Downloads a JSON file. **Do this once you're set up.** Rebuilding a card becomes a 30-second job. |
| **Import settings** | Restore from that file |
| **Change password** | Web interface login. Forgotten it? Put an empty file called `riparr-reset` on the card's boot partition — no re-flash, and nothing else is lost |
| **Update** | Check now, and install. See [Updates](#updates) below |
| **Logs** | Download a log bundle for bug reports |
| **Restart / shut down** | In the account menu (the person icon, top right), not on a page. Rarely needed — the box is built to survive losing power, but shutting down here is the safe way to stop it before unplugging |

### Updates

Riparr looks for a new version every six hours and **tells you when it finds one — once
per version, not every six hours.** It does not install anything on its own.

When you say yes, on **System → Updates**, the box downloads the new version, checks it
against the checksum published with the release, swaps it in and restarts itself. If the
download does not match, nothing is changed. If the new version cannot start, the
previous one is put back. You do not need a terminal and you do not need to be near it.

| Setting | Notes |
|---|---|
| **Check for updates automatically** | On by default. Turning it off stops the checking *and* the notification — nothing else changes, and **System → Updates** still works whenever you go looking |
| *A new version of Riparr is available* | A notification event like any other, on by default, and it can be turned off on its own under Notifications if you would rather find updates yourself |

The Preparer works the same way: it tells you in the corner when there is a newer
version, and one click downloads it, verifies it, replaces itself and reopens. The
checkbox next to the version number turns the checking off.

> **Updating is always a nudge, never a surprise.** Nothing is downloaded and nothing is
> replaced until you click. A box that quietly replaced its own software while you were
> halfway through a disc would not be doing you a favour.

---

[← Ripping discs](06-ripping-discs.md) · [Guide index](README.md) · [Next: Troubleshooting →](08-troubleshooting.md)
