# Changelog

What changed, in the order it changed, in terms of what you'd actually notice.

The section for each version is pulled straight into that version's release notes, so
this file is the release notes. Write it for somebody who wants to know whether to
bother updating.

---

## 0.3.9

**A 3D Blu-ray no longer rips the 3D version by mistake.** A 3D disc carries both cuts
of the film at exactly the same length, and Riparr picked the longest title — so it
picked the 3D one, which is roughly twice the size and which most players will not use.
It now takes the smaller of two titles that are the same length, which is the 2D cut.
**Settings → Ripping → On a disc with a 3D version** switches it back if you want 3D.

**Riparr stops less.** "When a disc can't be identified" was one setting doing two
jobs — what to call the film, *and* which title on the disc is the film. So a 3D disc
stopped the queue and asked, saying the studio was probably hiding the real title,
which was not what was happening.

They are two settings now:

- **Ripping → When Riparr can't tell which title is the film.** New, and it defaults to
  **use the most likely one** rather than asking. Riparr remembers what you pick for a
  disc, so a wrong guess costs one correction, not one per disc.
- **Naming → When a disc can't be identified.** Now only about the name, and it now
  defaults to **use the disc label**. Most rips land somewhere that gets tidied later
  rather than straight into a library, and a folder you rename in ten seconds beats a
  queue that stopped overnight waiting for you.

**Both defaults only apply to a box that never set them.** If you have been through
setup, your existing choices stand — change them on those two pages if you want the new
behaviour.

**And when it does ask, it says which thing is unclear** — whether it can't name the
disc, or can't tell which title is the film, and whether the two titles it is looking at
are a 2D/3D pair rather than a studio hiding something.

---

## 0.3.8

**The page now waits for the box and reloads itself after an update.** It used to hand
back "Riparr is restarting" and then sit there showing the old version number, so the
only way to see whether it had worked was to reload by hand — which looks exactly like
the bug where it genuinely hadn't.

It covers the page while the box is away and comes back on the new version by itself.
A service restart is about two seconds and a whole-box restart about a minute, and it
now waits the right amount for each rather than always assuming the slow one.

**Also fixes the "restart it yourself" button never appearing.** On a box that can't
restart itself, the update is finished and one restart away — that case had a button and
the button was wired into the wrong place on the page, so nobody ever saw it.

---

## 0.3.7

**The Wi-Fi recovery is yours to tune now.** It was fixed at three minutes, which is a
guess about your network rather than a fact about it. **Settings → Network → If the
connection drops** has a switch, a number of minutes, and a separate switch for whether
Riparr may restart the box as a last resort.

Everything follows from the one number: it reconnects at **N** minutes, reloads the
Wi-Fi driver at **2N**, and restarts the box at **4N**.

**Shorten it if your access point changes channel often.** On 5 GHz the higher channels
are shared with radar, and an access point on one has to move when it detects any —
this board's Wi-Fi driver does not reliably follow, which leaves the box sitting on a
channel nothing is using. **Lengthen it if your router reboots on a schedule**, or the
box will spend its time recovering from outages that were going to end by themselves.

Changes apply within the minute; nothing needs restarting. And a restart still never
interrupts a rip — it waits for the disc.

---

## 0.3.6

**Fixes an update that failed with "Could not install packages due to an OSError".**
Nothing was wrong with the box, the virtualenv or the download — the update was being
run from a directory that no longer existed.

Riparr runs from `/opt/riparr/server`, and installing an update *moves* that directory
aside before putting the new one in place. A running program follows the directory it is
standing in, so Riparr ended up inside the backup copy — and the next update deleted that
backup, leaving it standing nowhere. Everything it then tried to run failed instantly,
including `pip`, so the update rolled itself back.

It now steps somewhere stable before it starts, which also protects rips: `makemkvcon`
would have failed the same way in the same window.

**If you already have a box in this state**, restart Riparr once from the account menu
before updating — that puts it back on solid ground. This release then prevents it
recurring.

This only bit boxes where the previous bug (0.3.5) stopped updates restarting, because
that is what left the old program running long enough to have its floor removed twice.

---

## 0.3.5

**In-place updates from the web page actually restart now.** They have been swapping the
code correctly and then not restarting, so the box kept serving the old version and the
page kept showing the old version number. The message said *"Riparr is restarting"* and
nothing was restarting.

The cause: Riparr runs as an unprivileged account and cannot restart itself — both
`systemctl restart` and `systemd-run` come back *Access denied*. The updater tried those
two and fell back to a backgrounded shell, which **exits 0 whether or not the command
inside it works**, with the refusal sent to `/dev/null`. So it always looked like it had
succeeded. Three updates in a row failed this way with three successes in the log.

Restarting now goes through a privileged request, like every other thing Riparr needs
root for. If that isn't installed yet it restarts the whole box instead, which works on
every box. And if neither is possible it **says so** and gives you a Restart button,
rather than claiming a restart it never arranged.

Nothing was ever lost to this. The new version was always correctly on the box — it just
wasn't running yet, and the next restart would have picked it up.

---

## 0.3.4

**System → Tasks now checks itself.** Riparr is two halves: the application, which
updates itself, and a set of systemd units and root-side scripts that mount your share,
recover the Wi-Fi and let the web page ask for privileged things. Riparr cannot update
that second half on its own — it runs unprivileged and cannot write a system file — so
until now the two could drift apart with nothing anywhere saying so.

That is not hypothetical. `riparr-library.service` had never been installed by any
installer, on any box, so no share was mounted at boot — and the only symptom was a
perfectly good share reported as permanently lost, which looks like a network fault and
sends you to re-enter credentials that were never wrong.

There's a **System components** panel at the top of the Tasks page now. It lists any
part that is missing or out of date, says what each one does in plain terms, and has a
button that installs them. No terminal.

**The one case the button can't fix, and why.** If the part that installs the other
parts is itself missing, it cannot install itself — that's the security model, not an
oversight: Riparr runs as an unprivileged account with no sudo, and every privileged
action goes through a fixed, root-owned script it cannot modify. Weakening that would
mean a web service that can run anything as root. On such a box the panel says so and
points you at the fix, which is **"Update it in place" in the Riparr Preparer** on your
computer — a button in the app you already have, not an SSH session. It keeps your
settings, shares and history.

Boxes set up with 0.3.4 or later never hit that case.

---

## 0.3.3

**Your share can be reconnected from the page now.** If the NAS slept, the router
rebooted, or a cable moved, a configured share went to "not mounted" and stayed there —
and the only way back the interface offered was deleting the share and typing its
password in again, to fix something that was never wrong with it. There's a
**Reconnect** button on the Library page and next to the warning on the Queue page. It
takes about a second.

**And the underlying reason it dropped is fixed.** The unit that mounts your shares at
boot, `riparr-library.service`, existed but *nothing had ever installed it* — on the
reference box `/srv/library` did not exist at all. So shares were mounted only by
whatever had mounted them last, and never came back after a reboot. They mount at boot
now, and after every update.

**The Wi-Fi can die without telling anyone, and now the box notices.** On the reference
unit the radio stopped passing traffic and stayed that way for 17.5 hours. The box was
never down — it logged an unbroken hourly heartbeat the whole time — but nothing could
reach it. The kernel logged nothing about the radio in a seven-day boot, and
systemd-networkd logged nothing either: the driver leaves the link looking connected
when its firmware wedges, so there is no event to react to.

There's now a watchdog that pings your router once a minute. If nothing answers it
re-associates after 3 minutes, reloads the Wi-Fi driver after 5, and reboots after 10.
**It will not reboot during a rip** — it waits for the disc to finish, because the box
is already unreachable and rebooting would additionally cost you the work in progress.

Wi-Fi power saving was already off, and wasn't the cause. If this keeps happening, check
whether your router has put the 5 GHz band on a **DFS channel** (100–140): a radar event
makes the access point change channel, and this board's Wi-Fi driver does not always
follow. Channels 36–48 or 149–165 avoid it.

**Rips now go straight to your library by default.** They used to land on the SD card
first and get sent afterwards. Writing to your share directly is about twice as fast on
this hardware (18 MB/s against the card's 9.4), there's no size ceiling — a 22 GB Blu-ray
never did fit on a 32 GB card — and the card stops having tens of gigabytes pushed
through it every disc.

Nothing to set up, and nothing to switch back. **If your library isn't connected when a
disc goes in, that rip stages on the card by itself** and is sent when the share
reappears. If you'd rather always stage — a NAS that sleeps, patchy Wi-Fi — *Each rip
goes → onto the card first* on the Queue page. If you'd already chosen a mode, yours is
kept. **So an 8 GB card is genuinely enough**, and a bigger one buys nothing unless you
want deep verification.

**Deep verification is no longer offered when rips go straight to your library.** It
works by reading the file back off the share and comparing it against the original, and
going direct leaves one copy rather than two — so it was hashing a file against itself
and reporting a success it hadn't earned. It's still there for staged rips. Choosing it
alongside direct rips now switches it to the size check and says so.

**Storage says the card isn't the limit** when rips go direct, instead of counting how
many Blu-rays fit on a card the films never touch. And a disc refused for space tells
you your share is away, rather than telling you to buy a bigger card.

**Updates from the web page now install system changes.** This is the one behind several
of the above. The in-app updater replaced Riparr itself but never touched systemd units
or the root-side helper scripts — so any release that added one shipped the file and
installed nothing, on every box that updated from the web page. A freshly written card
got it; an updated box didn't. That is why the mount unit was missing. There's one
definition now, used by both the installer and the updater.

**One-time step for existing boxes.** Yours predates the piece that applies system
changes, so this update will tell you to finish it by running the installer once over
SSH: `sudo bash /opt/riparr/tools/install.sh`. It keeps your database, settings and
shares, and it's what installs the mount unit, the Reconnect bridge and the watchdog.
From the next release onward this is automatic.

**Guide corrected.** It described a "Riparr Slim" and a "Riparr Full" — two products that
were never built — and quoted 30 W and 100 W supplies, both guesses. The measured unit
peaks at **18 W**, and what matters is voltage: the trigger board needs to ask your brick
for 15 V or 20 V, which USB-C only guarantees at **45 W and above**. 12 V isn't a standard
USB-C voltage at all. The parts are all named now, including the data-only SATA bridge
and the right-angle cable.

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
