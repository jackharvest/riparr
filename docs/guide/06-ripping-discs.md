# 6. Ripping Discs

[← Library layout](05-library-layout.md) · [Guide index](README.md) · [Next: Settings reference →](07-settings-reference.md)

Setup is done. You should not need to open the settings again.

---

## The whole thing

1. **Insert disc. Close tray.**
2. **Walk away.**
3. **Disc ejects.** Green LED means it worked.
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
| **Saving to the card** | The film comes off the disc onto the card | ~9 min |
| **Uploading** | Card to your library | ~5 min |
| **Verifying** | Proving it arrived | seconds, or as long as the upload if set to deep |

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
> second case and the amber LED starts meaning "out of the drive but still uploading".

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

## Feeding it a stack

Load discs back to back — rip, eject, next — as long as there is room on the card for the
one you're putting in. `riparr.local` shows how many more fit.

## Reading the box at a glance

| LED | What's happening | Can I unplug? |
|---|---|---|
| **Solid green** | Idle, ready for a disc | Yes |
| **Breathing blue** | Ripping | No |
| **Pulsing amber** | Uploading to your library | **No** |
| **Green flash then idle** | Done and verified | Yes |
| **Red** | Disc failed — see the web page | Yes |
| **Purple** | Already ripped this one | Yes |
| **Blinking amber** | Paused — can't reach your library | Yes |

Print the [LED reference card](led-reference.md) and tape it inside the lid.

## The web page

`riparr.local` shows what's in progress, what's queued, what's waiting on you, and how
much room is left — in **discs**, not gigabytes.

The Queue page is the landing page and it is one screen: the **Auto Rip** switch, then
either the queue or the tray. The tray is the disc currently loaded, the drive holding
it, and — if there is no drive — why not. **Refresh** and **Eject** are top right.

You don't need to watch it. It's there for when the LED says something went wrong and you
want to know what.

## When something fails

**Red LED — bad disc.** Riparr retried and couldn't read it. Clean the disc, try again.
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

**Purple LED — duplicate.** You've ripped this disc before. Ejected immediately rather
than spending three hours doing it again. Force a re-rip from the web page if you meant it.

**Blinking amber — library unreachable.** NAS asleep, network down, credentials changed.
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
