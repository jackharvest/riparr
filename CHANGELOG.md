# Changelog

What changed, in the order it changed, in terms of what you'd actually notice.

The section for each version is pulled straight into that version's release notes, so
this file is the release notes. Write it for somebody who wants to know whether to
bother updating.

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
