# 8. Troubleshooting

[← Settings reference](07-settings-reference.md) · [Guide index](README.md)

Organized by **what you actually observed**, since there's no screen to read.

---

## `riparr.local` doesn't load

**First, wait two full minutes after plugging in.** First boot is slower than later boots.

**Then work out which of three things it is:**

| What you see | Meaning | Do this |
|---|---|---|
| Drive tray won't open, nothing at all | No power | Try another USB-C cable and supply. Charge-only cables are the usual culprit, and a brick with no 15V/20V profile is the next one. |
| Drive spins up, box never appears | Booting, or it couldn't join Wi-Fi | Wait two minutes, then see below |
| Another device can reach it by IP but not by name | Name resolution, not the box | See below |

### Couldn't join WiFi (blinking amber)

**Write the card again in the Preparer with the credentials corrected.** The box has no
screen and no Ethernet, so there is no other way in yet — and nothing is lost, because a
box that never joined has no settings on it.

*(An AP-mode fallback — the box putting up its own `Riparr-Setup` network so you can fix
this from a phone — is designed and [core] on the backlog, and is not built.)*

Most common causes, in order:

1. **Wrong password** — the usual one
2. **A 5GHz network on a 2.4GHz-only board.** Most supported boards are dual-band and
   5GHz is fine — but the Raspberry Pi Zero 2 W has no 5GHz radio, so on that board the
   network must be 2.4GHz.
3. **No WiFi country set** — WiFi won't start without it (the Preparer sets this for you)
4. **The network is captive-portal or enterprise** (802.1X) — not supported

### It's up, but the name doesn't resolve (solid green)

`riparr.local` uses mDNS. Works out of the box on macOS, iOS, and most Linux; Windows 10+
usually works.

- **Try `http://riparr.local:8080`** — the port may be needed
- **Find the IP on your router** and browse to it directly. Look for `riparr` in the DHCP
  client list.
- **Some corporate and guest networks block mDNS.** Use the IP address.
- **VLANs:** mDNS doesn't cross subnets. Your computer must be on the same network segment
  as the box.

## It isn't auto ripping

**Open `riparr.local` and read the checklist under the Auto Rip switch.** It lists every
prerequisite whether or not it's met, so the row that isn't green is your answer:

| Row | Means |
|---|---|
| **Riparr can read discs** | MakeMKV is installed |
| **The MakeMKV key is current** | There's a key and it hasn't lapsed. Amber means it lapses within a week — rips fail the day it does |
| **A drive to read them in** | An optical drive is on the USB bus. A working adapter shows up here even with no disc in the tray |
| **Somewhere to put the files** | A library share is configured *and* has been tested |
| **Room to work** | Amber only if the card is too full to rip safely. Rips go to your library by default, so this normally means the share is away and the card is being used as the fallback |

A red row disables the switch entirely and links to the page that fixes it. An amber row
leaves Auto Rip on but explains why a disc you just put in might not get ripped.

**All green and still nothing?** Check the switch is actually on — the checklist tells you
Riparr *could* rip, not that you've asked it to. Then read the next section.

## Nothing happens when I insert a disc

**Nothing happens at all:** the disc wasn't detected.

- Wait 30 seconds — spin-up and reading the table of contents takes a moment
- Try a different disc. A badly scratched or dirty one may not mount at all.
- **Disc upside down.** It happens.

**The drive works at it and gives up:** it saw the disc but couldn't read it. Clean the
disc.

**Nothing on any disc:** the drive may not be enumerating. Check the web page — the
dashboard reports whether a drive is present at all. If it isn't, that's a cable or power
problem inside the box.

## The box resets when I insert a disc

**Power problem, not software.** Optical drives pull a big surge when they spin up, and
it's browning out the Pi.

- **Check what your brick actually offers.** The trigger board can only ask; if the brick
  has no 15V or 20V profile you'll be running on 9V or 5V and browning out. 45W or more
  usually has them, small phone chargers usually don't.
- Try a shorter, thicker cable
- If you built it yourself: the board and the drive need **separate buck outputs**, and
  the 5V rail needs bulk capacitance. Sharing one node is exactly what causes this.

## The box vanishes off the network for hours

**Almost always the radio, not the box.** This board's Wi-Fi can stop passing traffic
while still reporting itself connected — nothing is logged, because from the driver's
point of view nothing happened. The box carries on running perfectly; it just cannot be
reached.

Riparr watches for this itself: it pings your router once a minute and reconnects,
reloads the Wi-Fi driver, and finally restarts the box. Check
**Settings → Network → If the connection drops** is on.

**If it keeps happening**, shorten *Wait this long first*. The usual cause is an access
point on one of the 5 GHz channels shared with radar: it has to change channel when it
detects any, and this board's driver does not always follow.

**To confirm it was the radio and not a crash**, over SSH:

```
journalctl -b -1 | grep fake-hwclock-save | tail -20
```

An unbroken hourly line through the outage means the box never stopped running.

## The rip failed

The web page says how far it got and why.

- **Clean the disc.** Soft cloth, center outward, never circular.
- **Check for scratches** on the data side
- **Retry.** Riparr already retried internally, but a reseat sometimes helps.
- **Some discs are genuinely unreadable** by some drives. A different drive may work.

Nothing partial is written to your library — you won't get a broken file.

## It ejected immediately — three flashes, or the tray twice

Not an error, and not the disc. You have ripped this one before, and Riparr saved you
half an hour.

**With a browser open** you will already be looking at the answer: the page jumps to
**Discs** and highlights the film, with the date it went into your library.

**Without one**, that is what the signal was: three short flashes of the drive's own
light, three times — or two tray cycles, if you set it that way. Change it on
**Settings → Ripping → Already-ripped discs**,
where **Try the light** and **Try the tray** let you see each one on demand.

**To rip it anyway:** the **Re-rip** button on that film's tile in **Discs**. Leave the
disc on the open tray — Riparr pulls the tray back in itself.

**If it says this about a disc you have never ripped**, Riparr has matched it to
something else by mistake. **Forget** on the same tile clears its memory of it, and the
next insertion is treated as new.

## Blinking amber — paused

Riparr can't reach your library share. The rip is safe and paused mid-flight.

1. **Is the NAS awake?** Drive spin-down and sleep timers are the usual cause.
2. **Settings → Library → Test write.** This tells you exactly which part is failing.
3. **Did credentials change?** A NAS password rotation will do this.
4. **Is the share full?**

Fix it and Riparr resumes where it stopped. Nothing is lost.

## Everything is slow

**Probably not a fault.** A Blu-ray takes about three hours and a 4K disc most of a day,
because the board's WiFi moves roughly 4 MB/s and the movie has to travel over it.

Genuinely slower than that:

- **Weak WiFi.** Move the box closer to the router. 2.4GHz is congested in most homes.
- **Microwave ovens and old cordless phones** sit right on 2.4GHz
- **NAS is busy** with something else

No SD card and no drive will make this faster. There's no Ethernet option on this board.

## Names are wrong

- **Check the disc's queue entry** — Riparr flags anything it was unsure about rather than
  guessing
- **TV discs:** correct the first disc of a season and the rest follow
- **Corrections are remembered** per disc, forever. You'll never fix the same disc twice.
- **Consistently wrong across many discs:** check your naming template in
  [settings](07-settings-reference.md#naming)

## It says my card is full

Shouldn't be possible in normal use — Riparr streams rips out as they're made.

If it happens, something downstream is stuck:

1. **Check the library share** — if uploads have been failing, the buffer is holding
   everything
2. **Look for stuck rips** in the queue
3. **Settings → Storage** shows what's held and why

## I unplugged it mid-rip

Fine. Plug it back in.

Riparr detects the interruption on boot and either resumes or cleanly fails that job. Your
library won't have a partial file and your card won't be corrupt — the system is built
assuming this will happen.

## I forgot my password

**You don't need to re-flash.** Riparr accepts a reset from the card itself, which is
proof you own the box in the same way re-flashing it would be — and keeps your settings,
shares and disc history.

1. Shut the box down (account menu → **Shut down**), then unplug it and take the card out
2. Put the card in a computer. The small **boot** partition mounts on its own
3. Create an empty file there called **`riparr-reset`** — no extension needed, and the
   contents are ignored
4. Card back in the box, plug it in, wait a minute
5. `riparr.local` asks you to create an account again

Riparr deletes the file as it acts on it, so the card goes back to normal on its own.
Nothing else is touched: your share, your naming templates and every disc it remembers
are all still there.

## Dates and "days left" look wrong

The box has no battery-backed clock. It learns the time from the internet at every boot,
and until it does, it believes whatever the last thing written to the card said.

If **System → Status** says the clock can't be right, Riparr stops making claims that
depend on it — including how long your MakeMKV key has left — rather than stating a
confident wrong number. It sorts itself out within a minute of the box reaching the
internet. If it doesn't, the box can't get out to a time server: see
[`riparr.local` doesn't load](#riparrlocal-doesnt-load) for the network side.

## Starting over

**Settings → System → Export settings** first, if you can reach the box. Then re-flash the
card and import the file after setup. Takes about a minute.

## Reporting a bug

**Settings → System → Logs** downloads a bundle. Include:

- What the box appeared to be doing
- The disc (title, DVD/BD/UHD, studio)
- Your drive model
- Your card size

---

[← Settings reference](07-settings-reference.md) · [Guide index](README.md)
