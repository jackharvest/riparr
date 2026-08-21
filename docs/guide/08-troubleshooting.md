# 8. Troubleshooting

[← Settings reference](07-settings-reference.md) · [Guide index](README.md)

Organized by **what you actually observed**, since there's no screen to read.

---

## `riparr.local` doesn't load

**First, wait two full minutes after plugging in.** First boot is slower than later boots.

**Check the LED:**

| LED | Meaning | Do this |
|---|---|---|
| Nothing at all | No power | Try another USB-C cable and supply. Cheap cables that are charge-only are a common culprit. |
| Slow white pulse | Booting or awaiting setup | Keep waiting, then retry the address |
| Blinking amber | **Couldn't join WiFi** | See below |
| Solid green | It's up — this is a name resolution problem | See below |

### Couldn't join WiFi (blinking amber)

Riparr creates its own network called **`Riparr-Setup`**. Connect a phone to it, and a
setup page opens. Choose your network and enter the password.

Most common causes, in order:

1. **Wrong password** — the usual one
2. **You picked a 5GHz network.** The Pi Zero 2W has no 5GHz radio. It must be 2.4GHz.
3. **No WiFi country set** in Imager — WiFi won't start without it
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
| **Room to work** | Amber only if the card is too full to rip safely, in which case discs are refused before they start |

A red row disables the switch entirely and links to the page that fixes it. An amber row
leaves Auto Rip on but explains why a disc you just put in might not get ripped.

**All green and still nothing?** Check the switch is actually on — the checklist tells you
Riparr *could* rip, not that you've asked it to. Then read the next section.

## Nothing happens when I insert a disc

**LED stays solid green:** the disc wasn't detected.

- Wait 30 seconds — spin-up and reading the table of contents takes a moment
- Try a different disc. A badly scratched or dirty one may not mount at all.
- **Disc upside down.** It happens.

**LED flickers, then goes back to green:** the drive saw it but couldn't read it. Clean the
disc.

**Nothing on any disc:** the drive may not be enumerating. Check the web page — the
dashboard reports whether a drive is present at all. If it isn't, that's a cable or power
problem inside the box.

## The box resets when I insert a disc

**Power problem, not software.** Optical drives pull a big surge when they spin up, and
it's browning out the Pi.

- Use a higher-wattage USB-C supply — 30W minimum for a Slim build, 100W PD for Full
- Try a shorter, thicker cable
- If you built it yourself: the Pi and the drive need **separate buck outputs**, and the
  5V rail needs bulk capacitance. See [`docs/design/hardware.md`](../design/hardware.md).

## Red LED — the rip failed

The web page says how far it got and why.

- **Clean the disc.** Soft cloth, center outward, never circular.
- **Check for scratches** on the data side
- **Retry.** Riparr already retried internally, but a reseat sometimes helps.
- **Some discs are genuinely unreadable** by some drives. A different drive may work.

Nothing partial is written to your library — you won't get a broken file.

## Purple LED — it ejected immediately

Not an error. You've ripped this disc before, and Riparr saved you three hours.

To rip it anyway: **Settings → Disc history → Force re-rip**.

## Blinking amber — paused

Riparr can't reach your library share. The rip is safe and paused mid-flight.

1. **Is the NAS awake?** Drive spin-down and sleep timers are the usual cause.
2. **Settings → Library → Test write.** This tells you exactly which part is failing.
3. **Did credentials change?** A NAS password rotation will do this.
4. **Is the share full?**

Fix it and Riparr resumes where it stopped. Nothing is lost.

## Everything is slow

**Probably not a fault.** A Blu-ray takes about three hours and a 4K disc most of a day,
because the Pi Zero 2W's WiFi moves roughly 4 MB/s and the movie has to travel over it.

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

## Starting over

**Settings → System → Export settings** first, if you can reach the box. Then re-flash the
card and import the file after setup. Takes about a minute.

## Reporting a bug

**Settings → System → Logs** downloads a bundle. Include:

- What you saw the LED doing
- The disc (title, DVD/BD/UHD, studio)
- Your drive model
- Your card size

---

[← Settings reference](07-settings-reference.md) · [Guide index](README.md)
