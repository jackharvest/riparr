# Changelog

What changed, in the order it changed, in terms of what you'd actually notice.

The section for each version is pulled straight into that version's release notes, so
this file is the release notes. Write it for somebody who wants to know whether to
bother updating.

---

## 0.3.2

**Rips now go straight to your library by default.** They used to land on the SD card
first and get sent afterwards. Writing to your share directly is about twice as fast on
this hardware (18 MB/s against the card's 9.4), there's no size ceiling — a 22 GB Blu-ray
never did fit on a 32 GB card — and the card stops having tens of gigabytes pushed
through it every disc.

Nothing to set up, and nothing to switch back. **If your library isn't mounted when a
disc goes in, that rip stages on the card by itself** and is sent when the share
reappears. If you'd rather always stage — a NAS that sleeps, patchy Wi-Fi — *Each rip
goes → onto the card first* on the Queue page. If you'd already chosen a mode, yours is
kept.

**So an 8 GB card is genuinely enough**, and a bigger one buys nothing unless you want
deep verification.

**Deep verification is no longer offered when rips go straight to your library.** It
works by reading the file back off the share and comparing it against the original, and
going direct leaves one copy rather than two — so it was hashing a file against itself
and reporting a success it hadn't earned. It's still there for staged rips, where it
means something. Choosing it alongside direct rips now switches it to the size check and
says so, rather than silently doing less than you asked.

**Storage now says the card isn't the limit** when rips go direct, instead of counting
how many Blu-rays fit on a card the films never touch. And a disc refused for space
tells you your share is away, rather than telling you to buy a bigger card.

**Guide corrected.** It described a "Riparr Slim" and a "Riparr Full" — two products that
were never built — and quoted 30 W and 100 W supplies, both guesses. The measured unit
peaks at **18 W**, and what actually matters is voltage: the trigger board needs to ask
your brick for 15 V or 20 V, which USB-C only guarantees at **45 W and above**. 12 V isn't
a standard USB-C voltage at all, which is why setting the trigger to 12 V and deleting a
buck doesn't work. The parts are all named now, including the data-only SATA bridge and
the right-angle cable.

---

## 0.3.1

**Small SD cards work now.** The Preparer was reserving 8.5 GB for the system and calling
anything under about 12 GB "too small". The system actually uses 2.3 GB — measured on a
running box, where a 32 GB card has 26 GB free. **An 8 GB card runs Riparr fine.** Card
size only decides how much room there is to stage a rip, and if you set *Each rip goes →
straight to your library* it stops mattering at all.

**Guide rewritten** around building one: what it costs, what's inside, how it's wired,
then the walkthrough. The reference pages are still there, further down, for when
something surprises you.

**The macOS install instructions were wrong.** Right-click → Open hasn't been enough for a
while. You have to let it get refused, then approve it in System Settings → Privacy &
Security.

---

## 0.3.0

**One version number.** The box and the Preparer used to have separate versions, which
was confusing and caused three update failures in a row. They're the same number now.

**The Preparer tells you what it's about to do.** Point it at a box that's already
running and it now says which version is on there, which version it's going to install,
and that your login, settings, rip history and MakeMKV build all survive. Button reads
**Update to 0.3.0** instead of a vague "set it up".

**Check for updates without restarting the app.** There's a **now** button next to the
checkbox in the Preparer's sidebar. It answers even when there's nothing to install.

**Updating the box actually works.** The old updater couldn't replace itself and, worse,
could delete the box's Python environment while reporting that nothing had changed. A box
that hit that kept running but wouldn't have survived its next reboot. Fixed, and covered
by a test that runs on every build.

**Card writing works from the packaged app.** It never had — the app couldn't reach its
own card writer, and reported it as a permissions problem, which it wasn't.

**Wi-Fi bands show up.** The button that asks macOS for Location Services was there all
along and was being hidden. Remembered networks are folded away separately from the ones
actually in range, so you're not scrolling past thirty hotel networks.

**The "your card is ready" screen makes sense now.** What's done on the left, what you
need to do on the right, and the Continue button stays locked until the box actually
appears on the network — no more clicking it before you've plugged anything in.

**macOS will say your card is unreadable after writing it.** That's normal and it's now
said up front. Click **Ignore**. Never **Initialise**.

---

## 0.2.x

Groundwork, mostly invisible: self-update on both halves, the appliance payload shipped
inside the Preparer, MakeMKV fetched and verified at setup instead of by hand, and a
first pass at the Wi-Fi and update plumbing that 0.3.0 finished.

---

## 0.1.x

First public releases. Card writing, unattended setup over SSH, the web interface, Auto
Rip, library shares, notifications, and the LED.
