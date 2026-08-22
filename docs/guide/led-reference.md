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
| 🔴 **Solid red** | Disc failed, or this drive can't read it — check `riparr.local` | ✅ |
| 🟣 **Purple** | Already ripped this disc | ✅ |
| 🟠 **Blinking amber** | Waiting for you to answer something, or can't reach your library | ✅ |
| 🟠 **Blinking amber (at boot)** | Couldn't join WiFi — the box is not on your network | ✅ |

---

**The one that matters: 🟠 amber means it's still working.** The disc is out and the drive
is silent, but the movie may still be traveling to your library. Don't unplug it.

**Anything unexpected → `riparr.local`.** The LED tells you *that* something happened; the
web page tells you *what*.

**Rough timings:** DVD ~30 min · Blu-ray ~3 hr · 4K most of a day. Limited by WiFi, not
the drive.

**Wiring one up:** the LED hangs off SPI, which has to be turned on first —
`sudo armbian-config` → System → Hardware → tick `spi-spidev`, then reboot. Riparr picks
it up without being restarted.

**No LED?** Riparr works fine without one — the web page is then the only place status
appears. **System → Status** says whether one is detected and has a **Test the LED**
button that walks it through red, green, blue, white.
