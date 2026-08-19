# LED Reference Card

*Print this. Tape it inside the lid.*

---

| LED | Meaning | Unplug? |
|---|---|---|
| ⚪ **Slow white pulse** | Booting, or waiting for setup | ✅ |
| 🔵 **Blinking blue** | Joining WiFi | ✅ |
| 🟢 **Solid green** | Ready — feed it a disc | ✅ |
| 🔵 **Breathing blue** | Ripping | ❌ |
| 🟠 **Pulsing amber** | Uploading to your library | ❌ **Still working** |
| 🟢 **Green flash → idle** | Done and verified | ✅ |
| 🔴 **Solid red** | Disc failed — check `riparr.local` | ✅ |
| 🟣 **Purple** | Already ripped this disc | ✅ |
| 🟠 **Blinking amber** | Paused — can't reach your library | ✅ |
| 🟠 **Blinking amber (at boot)** | Couldn't join WiFi — connect to `Riparr-Setup` | ✅ |

---

**The one that matters: 🟠 amber means it's still working.** The disc is out and the drive
is silent, but the movie may still be traveling to your library. Don't unplug it.

**Anything unexpected → `riparr.local`.** The LED tells you *that* something happened; the
web page tells you *what*.

**Rough timings:** DVD ~30 min · Blu-ray ~3 hr · 4K most of a day. Limited by WiFi, not
the drive.
