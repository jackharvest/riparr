"""
Telling the user something happened when they are not looking at the page.

The product's whole pitch is *insert a disc and walk away*, and until now the only
thing that could reach a person who had walked away was a coloured LED on the box --
which requires them to walk past the box. `webhook_url` has been a field on the
Connect settings page since the beginning and nothing in the codebase read it.

Four channels, all on the standard library: no new dependency, because a native wheel
that fails to build on this hardware turns into a box that installed fine and cannot
tell you anything (see the note in requirements.txt).

Every send is fire-and-forget on its own thread. A notification that blocks a rip is
worse than no notification, and a NAS-adjacent box is exactly where a webhook to some
unreachable host will hang for thirty seconds.
"""
import json
import re
import smtplib
import threading
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

from . import db, platform as P, system as SY

log = SY.component("Notifications")

TIMEOUT = 10

# What Riparr can tell you about, and whether it does by default. The defaults are the
# things you would want to know at the shops; the rest are for people who want a log.
EVENTS = [
    ("done",        "A rip finished",            True),
    ("needs_you",   "A disc needs your input",   True),
    ("failed",      "A rip failed",              True),
    ("share_lost",  "Your library went away",    True),
    ("duplicate",   "A disc was already ripped", False),
    ("key_expiring", "The MakeMKV key is about to expire", True),
]

DEFAULT_EVENTS = [k for k, _, on in EVENTS if on]

# ntfy's priority/emoji vocabulary, so a phone notification looks like it was designed
# rather than dumped.
_TAGS = {
    "done": ("white_check_mark", 3),
    "needs_you": ("raising_hand", 4),
    "failed": ("rotating_light", 4),
    "share_lost": ("warning", 4),
    "duplicate": ("recycle", 2),
    "key_expiring": ("key", 4),
}


def enabled_events():
    got = db.get("notify_events")
    return got if isinstance(got, list) else DEFAULT_EVENTS


def send(event, title="", body="", force=False):
    """Queue a notification on every configured channel. Never raises, never blocks."""
    if not force and event not in enabled_events():
        return
    payload = {"event": event, "title": title, "body": body,
               "hostname": P.hostname()}
    threading.Thread(target=_fanout, args=(event, title, body, payload),
                     name="riparr-notify", daemon=True).start()


def _fanout(event, title, body, payload):
    for name, fn in (("ntfy", _ntfy), ("Discord", _discord),
                     ("webhook", _webhook), ("email", _email)):
        try:
            fn(event, title, body, payload)
        except Exception as e:
            log.warning("%s notification failed: %s", name, e)


class BadWebhookURL(ValueError):
    """A notification target that is not a plain http(s) URL."""


def _check_url(url):
    """Reject anything that is not an http(s) URL with a host.

    These URLs come from settings, so a signed-in user chooses them -- and pointing the
    box at a service on the same LAN (a self-hosted ntfy, a webhook on the NAS) is the
    normal, intended case, so private addresses are deliberately allowed. What is not
    allowed is a non-network scheme: `file://`, `gopher://` and friends turn a webhook
    field into a way to make the box read local files or speak odd protocols, and no
    legitimate notification target needs them.
    """
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise BadWebhookURL("Notification URLs must start with http:// or https://")
    return url


def _post(url, data, headers=None, content_type="application/json"):
    _check_url(url)
    body = data if isinstance(data, bytes) else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", "Riparr")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status


# ─────────────────────────────── channels ───────────────────────────────

def _ntfy(event, title, body, payload):
    topic = (db.get("ntfy_topic") or "").strip()
    if not topic:
        return
    server = (db.get("ntfy_server") or "https://ntfy.sh").strip().rstrip("/")
    tag, priority = _TAGS.get(event, ("cd", 3))
    headers = {"Title": _ascii(title or "Riparr"),
               "Tags": tag, "Priority": str(priority)}
    token = (db.get("ntfy_token") or "").strip()
    if token:
        headers["Authorization"] = "Bearer %s" % token
    _post("%s/%s" % (server, urllib.parse.quote(topic)),
          (body or "").encode("utf-8"), headers, content_type="text/plain")


_DISCORD_RE = re.compile(
    r"^https://(?:\w+\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/(\d+)/([\w-]+)")


def discord_mention_prefix(event):
    """`<@id>` when this event is one the user asked to be pinged for.

    A message in a channel is something you find later. A mention is something your
    phone tells you about, and the whole product is "walk away" -- so the events that
    mean *come back* are the ones that get the ping, and the rest stay quiet. Roles
    (`&`-prefixed IDs) work too, for a household that shares the box.
    """
    who = (db.get("discord_mention") or "").strip()
    if not who:
        return "", None
    want = db.get("discord_mention_events")
    if isinstance(want, list) and event not in want and event != "test":
        return "", None
    if who.startswith("&"):
        return "<@%s> " % who, {"parse": [], "roles": [who[1:]]}
    return "<@%s> " % who, {"parse": [], "users": [who]}


def _discord(event, title, body, payload):
    url = (db.get("discord_webhook") or "").strip()
    if not url:
        return
    colour = {"done": 0x27C24C, "failed": 0xF05050, "needs_you": 0xFF9F1A,
              "share_lost": 0xFF9F1A, "key_expiring": 0xFF9F1A}.get(event, 0x5B5B8A)
    mention, allowed = discord_mention_prefix(event)
    msg = {"username": "Riparr",
           "embeds": [{"title": title or "Riparr",
                       "description": body or "",
                       "color": colour,
                       "footer": {"text": "Riparr on %s" % P.hostname()}}]}
    if mention:
        msg["content"] = mention.strip()
        # Without this Discord will happily render `<@everyone>`-shaped text but will
        # not ping anyone the webhook was not explicitly told to ping.
        msg["allowed_mentions"] = allowed
    _post(url, msg)


def discord_check(url=None):
    """Ask Discord what a webhook URL actually points at, before trusting it.

    A webhook URL is a long opaque string a user pasted, and the failure mode of
    getting it slightly wrong is silence -- notifications that go nowhere, discovered
    weeks later when the one that mattered did not arrive. Discord answers an
    unauthenticated GET on the webhook itself with its name and channel, so the
    settings page can say *what it is connected to* rather than "saved".
    """
    url = (url if url is not None else db.get("discord_webhook") or "").strip()
    if not url:
        return {"ok": False, "error": "Paste the webhook URL first."}
    m = _DISCORD_RE.match(url)
    if not m:
        return {"ok": False,
                "error": "That doesn't look like a Discord webhook URL. It should "
                         "start https://discord.com/api/webhooks/ and Discord gives "
                         "you the whole thing on the Copy Webhook URL button."}
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Riparr")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            info = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            return {"ok": False,
                    "error": "Discord doesn't recognise that webhook. It was probably "
                             "deleted, or the URL got truncated when it was copied."}
        return {"ok": False, "error": "Discord said %s %s" % (e.code, e.reason)}
    except urllib.error.URLError as e:
        return {"ok": False, "error": "Couldn't reach Discord: %s" % e.reason}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True,
            "name": info.get("name") or "Riparr",
            "channel_id": info.get("channel_id"),
            "guild_id": info.get("guild_id")}


def _webhook(event, title, body, payload):
    """The field that has been on the settings page all along, finally connected."""
    url = (db.get("webhook_url") or "").strip()
    if not url:
        return
    _post(url, payload)


def _email(event, title, body, payload):
    host = (db.get("smtp_host") or "").strip()
    to = (db.get("smtp_to") or "").strip()
    if not host or not to:
        return
    msg = EmailMessage()
    msg["Subject"] = "Riparr: %s" % (title or event)
    msg["From"] = (db.get("smtp_from") or "riparr@localhost").strip()
    msg["To"] = to
    msg.set_content("%s\n\n%s\n\n— Riparr" % (title or "", body or ""))

    port = int(db.get("smtp_port") or 587)
    user = (db.get("smtp_username") or "").strip()
    password = db.get("smtp_password") or ""
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT)
    else:
        server = smtplib.SMTP(host, port, timeout=TIMEOUT)
    try:
        if port != 465 and db.get("smtp_tls", True):
            server.starttls()
        if user:
            server.login(user, password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _ascii(s):
    """ntfy puts the title in an HTTP header, and headers are latin-1 at best."""
    return (s or "").encode("ascii", "replace").decode("ascii")


# ─────────────────────────────── the test button ───────────────────────────────

def configured():
    return {
        "ntfy": bool((db.get("ntfy_topic") or "").strip()),
        "discord": bool((db.get("discord_webhook") or "").strip()),
        "webhook": bool((db.get("webhook_url") or "").strip()),
        "email": bool((db.get("smtp_host") or "").strip()
                      and (db.get("smtp_to") or "").strip()),
    }


def test(channel):
    """Send one notification down one channel and report what actually happened.

    Synchronous and error-reporting, unlike `send` -- the entire value of a test button
    is the error message, so this is the one path that must not swallow it.
    """
    fns = {"ntfy": _ntfy, "discord": _discord, "webhook": _webhook, "email": _email}
    fn = fns.get(channel)
    if not fn:
        return {"ok": False, "error": "Unknown channel."}
    if not configured().get(channel):
        return {"ok": False, "error": "That channel isn't configured yet."}
    title = "Riparr test"
    body = "If you're reading this, notifications work."
    payload = {"event": "test", "title": title, "body": body}
    try:
        fn("done", title, body, payload)
        return {"ok": True, "message": "Sent. Check your %s." % channel}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "%s said %s %s" % (channel, e.code, e.reason)}
    except urllib.error.URLError as e:
        return {"ok": False, "error": "Couldn't reach it: %s" % e.reason}
    except Exception as e:
        return {"ok": False, "error": str(e)}
