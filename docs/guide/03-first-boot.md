# 3. First Boot

[← Prepare the SD card](02-prepare-sd-card.md) · [Guide index](README.md) · [Next: Connect your library →](04-connect-your-library.md)

**About 1 minute of your time.** Two minutes of waiting.

---

## Steps

**1. Slide the card into the box**

**2. Plug in the single USB-C cable**

That's the only cable. There is no power switch — plugging it in turns it on.

**3. Wait about two minutes**

First boot does more work than later boots: it expands onto your card, sets up storage,
and joins WiFi. Later boots take well under a minute.

**4. Browse to `riparr.local`**

Type it into any browser on the same network.

If that doesn't resolve, see [name not found](08-troubleshooting.md#riparrlocal-doesnt-load).

**5. Set your Riparr password**

The first page asks you to create a username and password for the web interface. This is
**separate** from the system login the Preparer set on the card.

Nothing else is reachable until you do this — the box will not sit on your network
unprotected.

## If WiFi didn't work

A mistyped WiFi password is the most common setup failure. The box has no screen and no
Ethernet, so if it can't join your network there is nothing to see and nowhere to log in.

**Today the fix is to write the card again** in the Preparer, with the password corrected.
It takes a couple of minutes and nothing else is lost — the box has no state on it yet.

Before you do, rule out the cheaper causes:

- **The password.** Retype it rather than trusting the one that was remembered.
- **The band.** Check the network you picked actually exists on a band your board has.
  Every supported board does 2.4 GHz; only some do 5 GHz.
- **The name.** A network whose name differs by a space or a case is a different network.

> **[unresolved] — this is meant to be better.** The design has the box putting up its own
> `Riparr-Setup` network when it can't join yours, so you fix it from a phone instead of
> re-writing the card. It is [core] on the backlog and **not built.** Until it is, the
> re-write above is the whole answer.

## Building more than one?

They coexist fine. Give each a different hostname in the Preparer and reach them at
`riparr-office.local`, `riparr-den.local`, and so on.

---

[← Prepare the SD card](02-prepare-sd-card.md) · [Guide index](README.md) · [Next: Connect your library →](04-connect-your-library.md)
