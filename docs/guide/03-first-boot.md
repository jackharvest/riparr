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
**separate** from the system login you set in Imager.

Nothing else is reachable until you do this — the box will not sit on your network
unprotected.

## What the LED is telling you

| LED | Meaning |
|---|---|
| **Slow white pulse** | Booting, or waiting for you to finish setup |
| **Blinking blue** | Joining WiFi |
| **Solid green** | Ready. Feed it a disc. |
| **Blinking orange** | Couldn't join WiFi — see below |

Full list: [LED reference card](led-reference.md).

## If WiFi didn't work

A mistyped WiFi password is the most common setup failure, and re-flashing the card over a
typo would be miserable. So Riparr doesn't make you.

**If the box can't join your network, it creates its own** — a WiFi network named
`Riparr-Setup`. Connect any phone or laptop to it and a setup page opens automatically.
Pick your network, enter the password, and the box reboots onto it.

**[unresolved]** Whether the fallback network is open or uses a printed default password
isn't decided yet.

## Building more than one?

They coexist fine. Give each a different hostname in Imager and reach them at
`riparr-office.local`, `riparr-den.local`, and so on.

---

[← Prepare the SD card](02-prepare-sd-card.md) · [Guide index](README.md) · [Next: Connect your library →](04-connect-your-library.md)
