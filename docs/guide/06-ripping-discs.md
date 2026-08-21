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
| DVD | ~30 minutes |
| Blu-ray | ~3 hours |
| 4K UHD | most of a day |

**This is your WiFi, not your drive.** Riparr sends the movie to your library as it rips,
and a Pi Zero 2W's WiFi moves about 4 MB/s. A faster drive or a bigger SD card won't
change these numbers. Nothing is wrong.

## What the eject actually means

Depends on your card, and the LED tells you which:

**On a 32GB-class card** — the disc ejects when the file has finished landing in your
library. **Eject means done.**

**On a larger card** — Riparr rips at full speed and ejects early so you can load the next
disc, then finishes uploading in the background. **Eject means "ready for the next disc,"
not "finished."**

> ⚠️ **If the LED is amber, it's still uploading.** Don't unplug the box. The disc is out
> and the drive is quiet, but hours of transfer may remain. Amber means working.

## Feeding it a stack

On a larger card you can load discs back to back — rip, eject, next — and let the queue
drain overnight. `riparr.local` shows how many more discs fit before it has to slow down.

On a 32GB card, one at a time. Each disc takes as long as it takes, and you can't get
ahead of it. This is the tradeoff you took for never running out of space, and it doesn't
cost you any discs per day — the network was always the limit.

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
