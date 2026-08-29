# 6. Ripping Discs

[← Library layout](05-library-layout.md) · [Guide index](README.md) · [Next: Settings reference →](07-settings-reference.md)

Setup is done. You should not need to open the settings again.

---

## The whole thing

1. **Insert disc. Close tray.**
2. **Walk away.**
3. **Disc ejects** when it's finished.
4. **Insert the next one.**

That's it. No button, no clicking, no app.

## How long it takes

| Disc | Roughly |
|---|---|
| DVD | **~20–30 minutes** *(measured)* |
| Blu-ray | ~3 hours *(estimate)* |
| 4K UHD | most of a day *(estimate)* |

**Most of that is the box thinking, not the network.** Measured on a retail DVD: about
**sixteen minutes pass before a single byte is written**. Commercial discs are encrypted,
and the box unscrambles them in software — that is arithmetic, and this is a small
computer. Reading and unscrambling takes far longer than writing the film out or sending
it to your library.

So during a rip the drive spins, the box gets warm, and for a while the progress bar has
nothing honest to show you — that stretch is real work, not a fault. Riparr shows a
sweeping bar and a running clock rather than a percentage it would have to invent.

A faster drive will not change these numbers. A faster **card** helps a little: writing
the film out runs at about 9 MB/s on the reference board, which is marginally slower than
the Wi-Fi that sends it onward.

### The five stages, and why two of them cannot show a percentage

| Stage | What is happening | On the reference board, one DVD |
|---|---|---|
| **Reading the disc** | Cataloguing what is on it | ~9 min |
| **Decrypting** | Unscrambling, in software. **Nothing is written yet** | ~7 min |
| **Writing to your library** | The film comes off the disc onto your share | ~9 min |
| **Filing it in your library** | Moving it from the scratch folder into place | instant |
| **Verifying** | Proving it arrived | seconds |

Those middle two are named for what is actually happening, so they read differently if
you have switched rips to stage on the card: **Saving to the card**, then **Uploading**
— which takes about another 5 minutes, because the film gets written twice.

The first two have **no percentage available, and never will**. MakeMKV reports none
during the scan, and the reads go to the drive by a route the operating system cannot
see — so there is no file growing and no disk counter moving to measure. This was checked
rather than assumed.

What Riparr does instead is count. It knows how long **this** box took the last few
times, so the queue shows the stage you are in, how long you have been in it, and
roughly when to come back. Until two rips have finished it says so plainly rather than
guessing. If a stage runs long it says *"3 min over the usual"* — never "0 min left".

**History** shows the same five stages for every finished rip, to scale, so you can see
where your half hour actually went.

## What the eject actually means

**The disc ejects when the file has landed in your library and been checked.** Eject means
done. You can unplug the box the moment the tray opens.

That is the whole rule, and it is the same on every card size.

> **[unresolved] — this is meant to become two rules.** The design (D11) has Riparr
> uploading as it rips and ejecting early on a large card, so you can load the next disc
> while the last one is still travelling. That part is not built yet, so today the tray
> stays shut until the job is completely finished. When it lands, this section grows a
> second case: out of the drive, but still travelling.

## What it does when a disc won't fit

A rip is written to the SD card first, so a title has to fit on the card with room to
spare. If it doesn't, **Riparr says so before it starts** rather than failing partway
through. A 4K title is ~66 GB, which is why 4K wants a 256 GB card.

`riparr.local` shows how much room is left in **discs** — "Room for 2 Blu-rays, or 7
DVDs" — not gigabytes.

## What it does with a disc this drive can't read

Puts it straight back out, with the reason on the web page.

A DVD drive cannot read a Blu-ray, and **most Blu-ray drives cannot read a 4K UHD disc** —
4K needs one of a small number of specific drives, which is covered in
[what you need](01-what-you-need.md#which-drive). Riparr checks before it starts, so this
costs you ten seconds rather than forty minutes.

The Queue page tags your drive with what it reads — `DVD` `Blu-ray` `4K UHD` — and
**System → Status** says whether 4K will work on it, asking MakeMKV directly.

## Putting a disc back in that you've already ripped

Riparr recognises it and gives it straight back. It does not spend another half hour
finding out what you already know.

**If a browser is open**, the page jumps to **Discs**, names the film and when it landed
in your library, and highlights it. The **Re-rip** button is right there on the tile: if
you meant it — a bad rip, a changed setting, a better drive — press it. Leave the disc on
the open tray and Riparr pulls the tray back in for you.

**If nobody is looking at a browser**, the box has to say it out loud. Two ways, on
**Settings → Ripping → Already-ripped discs**:

| | |
|---|---|
| **The drive's own light** *(default)* | Three short flashes, three times |
| **The tray** | Opens and closes twice — unmissable across a room |

A word on the light, because it is not what it looks like. **Nothing can switch an
optical drive's front light on.** There is no such command in any standard, and the
handful of manufacturer-specific ones are guesses that have no business running on your
drive. What that light reports is the drive *reading* — so Riparr reads the disc in a
rhythm, and the light follows. It works on every drive and it asks nothing of yours.

Riparr cannot see the result. **Try the light** and **Try the tray** on that settings
page fire the signal on demand with any disc in the tray, so you can watch it once and
decide which you prefer.

## Feeding it a stack

Load discs back to back — rip, eject, next — as long as there is room on the card for the
one you're putting in. `riparr.local` shows how many more fit.

## Reading the box without a browser

The disc itself is the signal. It stays in while there's work to do and comes back out
when there isn't.

| The box is | What that means | Can I unplug? |
|---|---|---|
| Tray shut, drive quiet | Idle, ready for a disc | Yes |
| Tray shut, drive working | Ripping | No |
| Tray shut, drive quiet, still busy on the page | Uploading to your library | **No** |
| Disc ejected | Done, or it gave up — the page says which | Yes |
| Disc ejected almost immediately | You've ripped this one before | Yes |

If you want to know without walking over, set up **notifications** — Discord or a webhook,
on **Settings → Notifications**. That's the honest answer for a box with no screen: it
tells you where you actually are.

## The web page

`riparr.local` shows what's in progress, what's queued, what's waiting on you, and how
much room is left — in **discs**, not gigabytes.

The Queue page is the landing page and it is one screen: the **Auto Rip** switch, then
either the queue or the tray. The tray is the disc currently loaded, the drive holding
it, and — if there is no drive — why not. **Refresh** and **Eject** are top right.

You don't need to watch it. It's there for when a disc comes back out sooner than you
expected and you want to know why.

## When something fails

**Bad disc.** Riparr retried and couldn't read it. Clean the disc, try again.
The web page says how far it got and where it failed. Nothing partial is left in your
library.

**Whatever went wrong, start at History.** Every attempt is a row with its own reason,
and each row offers only the retries that would actually help:

- **Retry upload** — the rip is still on the card, so this skips the disc entirely.
  Minutes, not half an hour. This is the one you want after a network hiccup.
- **Retry fast verification** / **Retry deep verification** — the file reached your
  library but the check did not finish. Neither touches the disc.
- **Retry rip** — the rip is gone. Put the disc back in the tray first.

If only *Retry rip* is offered, the staged copy has been cleaned up and the disc is the
only remaining source.

**Duplicate.** You've ripped this disc before. Ejected immediately rather
than spending three hours doing it again. See below.

**Library unreachable.** NAS asleep, network down, credentials changed.
The rip is safe and paused. Fix the share and it picks up where it stopped.

**Nothing happened at all.** See [troubleshooting](08-troubleshooting.md).

## Yanking the cable

Fine. Riparr expects it. An interrupted rip is detected on the next boot and either
resumes or is failed cleanly. You won't get a corrupt file in your library, and you
won't get a corrupt SD card.

If the web page is in front of you anyway, **Shut down** is in the account menu — the
person icon, top right. There is no button on the enclosure, so that menu is the only
place it lives. It is the tidier way to stop the box; it is not the required one.

---

[← Library layout](05-library-layout.md) · [Guide index](README.md) · [Next: Settings reference →](07-settings-reference.md)
