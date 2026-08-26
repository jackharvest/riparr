/* Riparr web UI. A client of the same public API everything else uses. */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const api = {
  async call(method, path, body) {
    const r = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (r.status === 401) { showGate(); throw new Error("Not signed in"); }
    const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
    if (!r.ok) throw new Error(data?.detail || `Request failed (${r.status})`);
    return data;
  },
  get:  (p)    => api.call("GET", p),
  post: (p, b) => api.call("POST", p, b),
  put:  (p, b) => api.call("PUT", p, b),
  del:  (p)    => api.call("DELETE", p),
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 250); }, 3200);
}

const state = { status: null, settings: null };

/* ── formatting: the user should never see a gigabyte ───── */
/* ── the current beta key ───────────────────────────────── */
/* Fetched from the forum GuinpinSoft publishes it on, so the user can paste it in one
   click and can see when it lapses instead of finding out mid-rip. Every failure ends
   at "here is the link", which is exactly where they were before this existed. */
async function offerBetaKey(intoSel, inputSel, opts = {}) {
  const into = $(intoSel);
  if (!into) return;
  into.innerHTML = `<span class="muted">Looking up the current beta key…</span>`;
  let r;
  try { r = await api.get("/api/makemkv/beta-key" + (opts.refresh ? "?refresh=true" : "")); }
  catch (e) { r = { error: e.message }; }

  if (!r.key) {
    into.innerHTML = `<span class="muted">${esc(r.error || "Couldn't fetch the beta key.")}
      </span> <a href="${esc(r.source || "https://forum.makemkv.com/forum/viewtopic.php?t=1053")}"
      target="_blank" rel="noopener">Open the forum post</a>`;
    return;
  }
  into.innerHTML = `
    <div class="keyoffer">
      <div class="grow">
        <div class="keyval mono">${esc(r.key)}</div>
        <div class="muted">Current beta key${
          r.expires ? ` · valid until <b>${esc(r.expires)}</b>` : ""}
          · <a href="${esc(r.source)}" target="_blank" rel="noopener">source</a></div>
      </div>
      <button class="btn primary" id="mk-usekey">Use this key</button>
    </div>`;
  $("#mk-usekey").onclick = () => {
    const el = $(inputSel);
    if (!el) return;
    el.value = r.key;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    toast("Beta key filled in" + (r.expires ? ` — valid until ${r.expires}` : ""), "ok");
  };
}

/* Poll until the box answers again after a restart, then reload. The service is gone
   for most of this, so every failure here is expected and silent. */
async function waitForBoxBack() {
  const started = Date.now();
  const tick = async () => {
    if (Date.now() - started > 5 * 60 * 1000) {
      showWaiting("Still not back. Check the box has power, then reload this page.",
                  { retry: true, spin: false });
      return;
    }
    try {
      const r = await fetch("/api/setup/state", { cache: "no-store" });
      if (r.ok) { location.reload(); return; }
    } catch (e) { /* expected while it is down */ }
    setTimeout(tick, 3000);
  };
  // Do not start polling instantly: the box is still up for the first few seconds and
  // would answer immediately, reloading into a page about to be torn down.
  setTimeout(tick, 12000);
}

function signalBars(pct) {
  const n = pct == null ? 0 : pct >= 75 ? 4 : pct >= 55 ? 3 : pct >= 35 ? 2 : 1;
  return `<span class="sig">${[1, 2, 3, 4]
    .map(i => `<i class="${i <= n ? "lit" : ""}"></i>`).join("")}</span>`;
}

function capacityPhrase(st) {
  // The server decides the wording, because the meaning depends on D11's rip mode and
  // on how many of each kind of disc actually fit. Re-deriving it here is how the
  // interface ended up saying "1 more disc" without ever saying which kind — a number
  // that silently meant Blu-ray and was wrong eightfold for a DVD.
  if (!st) return "";
  if (st.phrase) {
    const m = st.phrase.match(/^Room for (\d+) (.*)$/);
    return m ? `Room for <b>${m[1]}</b> ${esc(m[2])}` : esc(st.phrase);
  }
  if (st.mode === "stream") return `<b>Streaming</b> — discs are never refused for space`;
  if (st.mode === "full") return `<b>Not enough room</b> for another disc`;
  return `<b>Not enough room</b> to rip safely`;
}
/* ── formatting for the System tables ─────────────────────
   Prowlarr's wording, because these tables are read the same way: a relative time for
   anything within a few days, an absolute one past that. */
function since(ts) {
  if (!ts) return "—";
  const d = ts - Date.now() / 1000;
  const fut = d > 0, s = Math.abs(d);
  if (s < 45) return fut ? "in a moment" : "just now";
  const n = (v, u) => `${Math.round(v)} ${u}${Math.round(v) === 1 ? "" : "s"}`;
  const t = s < 3600 ? n(s / 60, "minute")
          : s < 86400 ? n(s / 3600, "hour")
          : n(s / 86400, "day");
  return fut ? `in ${t}` : `${t} ago`;
}

function hms(sec) {
  if (sec == null) return "—";
  const s = Math.max(0, Math.round(sec));
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`;
}

function interval(sec) {
  if (!sec) return "—";
  if (sec % 86400 === 0) return `${sec / 86400} day${sec === 86400 ? "" : "s"}`;
  if (sec % 3600 === 0) return `${sec / 3600} hour${sec === 3600 ? "" : "s"}`;
  return `${Math.round(sec / 60)} minutes`;
}

/* Today gets a clock time, yesterday gets a word, anything older gets a date -- the
   same three-way split the *arrs use, and the reason their tables stay scannable. */
function stamp(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000), now = new Date();
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = (day(now) - day(d)) / 86400000;
  if (diff === 0) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
                          .replace(" ", "").toLowerCase();
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function filesize(b) {
  if (b == null) return "—";
  if (b < 1024) return `${b} B`;
  const u = ["KiB", "MiB", "GiB", "TiB"];
  let v = b / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
}

function uptime(sec) {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}
function ago(ts) {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} hours ago`;
  return `${Math.round(s / 86400)} days ago`;
}

/* ════════════════════ sign in ════════════════════ */
function showGate(sub) {
  $("#shell").classList.add("hidden");
  $("#wizard").classList.add("hidden");
  $("#gate").classList.remove("hidden");
  $("#gate-waiting")?.classList.add("hidden");
  $("#login-form")?.classList.remove("hidden");
  if (sub) $("#gate-sub").textContent = sub;
}

$("#login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("#gate-err").textContent = "";
  try {
    await api.post("/api/auth/login", {
      username: $("#gate-user").value, password: $("#gate-pass").value,
    });
    await boot();
  } catch (err) { $("#gate-err").textContent = err.message; }
};

/* ════════════════════ first run ════════════════════ */
const wizard = {
  step: 0,
  data: { username: "", password: "", host: "", share: "", path: "", user: "", pass: "" },
  steps: ["account", "makemkv", "share", "layout", "done"],

  render() {
    const name = this.steps[this.step];
    $("#gate").classList.add("hidden");
    $("#shell").classList.add("hidden");
    const w = $("#wizard");
    w.classList.remove("hidden");
    w.innerHTML = `
      <div class="wz-head">
        <div class="logo"><img class="logo-mark" src="/static/img/riparr-mark.png" alt=""> <span class="logo-word">Riparr</span></div>
        <div class="wz-rail">${this.steps.map((_, i) =>
          `<div class="${i <= this.step ? "done" : ""}"></div>`).join("")}</div>
      </div>
      <div class="wz-body" id="wz-body"></div>`;
    this[name]();

    // Enter finishes the step. These inputs are not in a <form> -- each step is plain
    // markup with an onclick on one primary button -- so nothing submits on its own,
    // and typing the last field then pressing Enter appeared to do nothing.
    // Bound on the body rather than per-input so every step gets it, including the
    // async ones that fill this element after we return.
    $("#wz-body").onkeydown = (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      // A field with its own Enter handler (the share step has two) has already acted
      // and called preventDefault; do not also fire the step's primary button.
      if (e.defaultPrevented) return;
      const t = e.target;
      if (!t || t.tagName !== "INPUT" || t.type === "checkbox" || t.type === "radio") return;
      const btn = $("#wz-body").querySelector(".wz-actions .btn.primary:not([disabled])");
      if (btn) { e.preventDefault(); btn.click(); }
    };
  },

  next() { this.step++; this.render(); },

  account() {
    $("#wz-body").innerHTML = `
      <div class="wz-step">Step 1 of 5</div>
      <h1>Create your login</h1>
      <p class="muted">This protects the web interface. It is separate from the system
        account you set when preparing the card.</p>
      <div class="section"><div>
        <label class="f"><span>Username</span><input id="w-user" value="admin"></label>
        <label class="f"><span>Password</span><input id="w-pass" type="password">
          <span class="help">At least 8 characters.</span></label>
        <label class="f"><span>Confirm password</span><input id="w-pass2" type="password"></label>
        <div class="result bad hidden" id="w-err"></div>
      </div></div>
      <div class="wz-actions">
        <div class="grow"></div>
        <button class="btn primary" id="w-go">Create account</button>
      </div>`;
    $("#w-go").onclick = async () => {
      const u = $("#w-user").value.trim(), p = $("#w-pass").value, p2 = $("#w-pass2").value;
      const err = $("#w-err");
      const fail = (m) => { err.textContent = m; err.classList.remove("hidden"); };
      if (!u) return fail("Pick a username.");
      if (p.length < 8) return fail("Use at least 8 characters.");
      if (p !== p2) return fail("The two passwords don't match.");
      try {
        await api.post("/api/setup/user", { username: u, password: p });
        this.next();
      } catch (e) { fail(e.message); }
    };
  },

  async makemkv() {
    const i = await api.get("/api/makemkv");
    const st = i.status;
    // The drive belongs on this step. Setup used to run to completion without ever
    // mentioning it, so someone whose drive was in the socket that cannot host got
    // five green steps and an empty tray at the end -- with the explanation sitting on
    // a dashboard they had not reached yet.
    let opt = null;
    try { opt = (await api.get("/api/status")).optical; } catch (e) {}
    const ready = st.installed && st.eula_accepted;
    $("#wz-body").innerHTML = `
      <div class="wz-step">Step 2 of 5</div>
      <h1>Disc reading</h1>
      <p class="muted">Riparr doesn't read discs itself — <b>MakeMKV</b> does, and it's
        made by GuinpinSoft, not by us. Its licence is an agreement between you and them,
        so Riparr won't download it until you've accepted it.</p>

      <div class="section"><h2>MakeMKV
        <span class="grow"></span>
        <span class="badge ${ready ? "ok" : "warn"}">${ready ? "Installed" : "Not installed"}</span></h2>
        <div>
          ${ready ? `<div class="kv">
              <div class="k">Version</div><div class="v">${esc(st.version || "—")}</div>
              <div class="k">Key</div><div class="v">${st.key_expires
                ? `${esc(st.key_type || "beta")} — expires ${esc(st.key_expires)} (${st.days_left} days)`
                : "none yet"}</div>
            </div>`
          : `<p class="muted" style="margin-bottom:12px">Version ${esc(i.manifest.version)}
               will be downloaded from
               <a href="${esc(i.homepage)}" target="_blank" rel="noopener">makemkv.com</a>
               and checked against a known checksum.</p>
             <ul class="terms">${i.eula_points.map(t => `<li>${esc(t)}</li>`).join("")}</ul>
             <p class="muted" style="font-size:12px">This is a summary.
               <a href="${esc(i.eula_url)}" target="_blank" rel="noopener">Read the full
               licence agreement</a> before accepting.</p>
             <label class="switch" style="margin-top:16px">
               <input type="checkbox" id="mk-accept"><span class="track"></span>
               <span class="lbl">I have read and accept MakeMKV's licence agreement</span>
             </label>
             <div class="btn-row">
               <button class="btn primary" id="mk-install" disabled>Download and install</button>
             </div>
             ${i.installable ? "" : `<p class="help muted" style="margin-top:8px">
               This process isn't running on the appliance, so the install will stop
               after the checks.</p>`}
             <div id="mk-progress"></div>`}
        </div>
      </div>

      ${opt ? `<div class="section"><h2>Your drive
        <span class="grow"></span>
        <span class="badge ${opt.drives && opt.drives.length ? "ok" : "warn"}">${
          opt.drives && opt.drives.length ? "Found" : "Not found"}</span></h2>
        <div>${opt.drives && opt.drives.length
          ? `<div class="kv">
               <div class="k">Drive</div><div class="v">${esc(
                 [opt.drives[0].vendor, opt.drives[0].model].filter(Boolean).join(" ")
                 || opt.drives[0].device)}</div>
               <div class="k">Reads</div><div class="v">${esc(
                 opt.drives[0].reads || "—")}</div>
             </div>`
          : `<p class="muted">Riparr can't see an optical drive yet. You can finish
               setting up and come back to this — but nothing can be ripped until a
               drive appears.</p>
             ${opt.hint ? `<p class="why">${mdBold(opt.hint)}</p>` : ""}
             ${opt.fixable === "usb-host" ? `<div class="btn-row">
               <button class="btn" id="wz-usb-fix">Make both USB-C sockets work</button>
             </div>
             <p class="help">Reconfigures the second socket and restarts the box, so it
                stops mattering which one you used.</p>` : ""}`}
        </div>
      </div>` : ""}

      <div class="section"><h2>Key</h2><div>
        <div class="f"><span></span><div class="grow" id="wz-key-offer"></div></div>
        <label class="f"><span>MakeMKV key</span>
          <input id="w-key" placeholder="Paste a beta or purchased key">
          <span class="help">The free beta key expires about every 60 days, and Riparr
            warns you before it breaks rather than after. A
            <a href="${esc(i.homepage)}" target="_blank" rel="noopener">purchased key</a>
            removes the only recurring chore in the product. You can add this later.</span>
        </label>
      </div></div>

      <div class="wz-actions">
        <div class="grow"></div>
        <button class="btn" id="w-skip">Do this later</button>
        <button class="btn primary" id="w-go">Continue</button>
      </div>`;

    offerBetaKey("#wz-key-offer", "#w-key");

    const wzUsbFix = $("#wz-usb-fix");
    if (wzUsbFix) wzUsbFix.onclick = async () => {
      if (!confirm("Make both USB-C sockets work?\n\nThe box will restart and setup "
                   + "will pick up where it left off.")) return;
      wzUsbFix.disabled = true;
      showWaiting("Reconfiguring the USB-C sockets\u2026");
      try { await api.post("/api/system/usb-host", {}); }
      catch (e) { showWaiting(e.message, { retry: true, spin: false }); return; }
      showWaiting("Restarting. This page will come back on its own in a minute or two.",
                  { spin: true });
      waitForBoxBack();
    };

    const accept = $("#mk-accept");
    if (accept) {
      accept.onchange = () => { $("#mk-install").disabled = !accept.checked; };
      $("#mk-install").onclick = async () => {
        $("#mk-install").disabled = true;
        try {
          await api.post("/api/makemkv/install", { accept_eula: accept.checked });
        } catch (e) {
          $("#mk-progress").innerHTML = `<div class="result bad">${esc(e.message)}</div>`;
          return;
        }
        pollMakeMKV();
      };
    }

    const save = async () => {
      const k = $("#w-key").value.trim();
      if (k) { try { await api.post("/api/makemkv/key", { key: k }); } catch (e) {} }
      this.next();
    };
    $("#w-go").onclick = save;
    $("#w-skip").onclick = () => this.next();
  },

  share() {
    $("#wz-body").innerHTML = `
      <div class="wz-step">Step 3 of 5</div>
      <h1>Where should finished rips go?</h1>
      <p class="muted">Riparr looks for network shares on your LAN, then writes a real
        test file and reads it back. A wrong path found now is a wrong path you never
        discover at 3am on your first rip.</p>
      <div class="section"><h2>Network shares<span class="grow"></span><button class="btn" id="w-scan">Scan again</button></h2>
        <div class="body" id="w-hosts"><div class="result busy"><span class="spin"></span>Looking for shares…</div></div>
        <div class="manual-row">
          <input id="w-manual" placeholder="server name or IP — e.g. mothership.example.lan">
          <button class="btn" id="w-manual-go">Use this server</button>
        </div>
        <p class="help">Discovery only finds servers that advertise themselves. Type one
          in if yours doesn't, or if it's on another subnet.</p>
      </div>
      <div id="w-detail"></div>
      <div class="wz-actions">
        <div class="grow"></div>
        <button class="btn" id="w-skip">Set this up later</button>
        <button class="btn primary" id="w-go" disabled>Continue</button>
      </div>`;
    $("#w-skip").onclick = () => this.next();
    $("#w-go").onclick = () => this.next();
    $("#w-scan").onclick = () => this.scanHosts();
    $("#w-manual-go").onclick = () => this.pickHost($("#w-manual").value.trim());
    $("#w-manual").onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); $("#w-manual-go").click(); }
    };
    this.scanHosts();
  },

  async scanHosts() {
    const box = $("#w-hosts");
    box.innerHTML = `<div class="result busy"><span class="spin"></span>Looking for shares…</div>`;
    let hosts = [];
    try { hosts = (await api.post("/api/shares/discover")).hosts; } catch (e) {}
    if (!hosts.length) {
      box.innerHTML = `<div class="result">Nothing advertised itself. Type the server
        name below — discovery finding nothing does not mean there is nothing there.</div>`;
      return;
    }
    box.innerHTML = hosts.map(h => `
      <div class="rowitem" data-host="${esc(h.host)}">
        <div class="grow">
          <div class="t">${esc(h.host)}</div>
          <div class="s">${esc(h.address)} · found by ${esc(h.via === "mdns" ? "Bonjour" : "network scan")}</div>
        </div>
        <span class="badge">SMB</span>
      </div>`).join("");
    $$("#w-hosts .rowitem").forEach(n => n.onclick = () => {
      $$("#w-hosts .rowitem").forEach(x => x.classList.remove("on"));
      n.classList.add("on");
      this.pickHost(n.dataset.host);
    });
  },

  async pickHost(host, creds) {
    if (!host) return;
    this.data.host = host;
    // Carry credentials across a re-browse. The share list itself is behind
    // authentication on most servers, so asking anonymously and only then offering a
    // username means the list is empty exactly when it matters.
    const user = creds ? creds.user : (this.data.suser || "");
    const pass = creds ? creds.pass : (this.data.spass || "");
    this.data.suser = user;
    this.data.spass = pass;

    const d = $("#w-detail");
    // Same shell as the loaded state below -- header, .card, .body. Rendering a
    // .section here and a .card a moment later made the panel change component
    // type mid-flight, which reads as a jump exactly where the eye is waiting.
    d.innerHTML = `<div class="card">
      <header><h3>${esc(host)}</h3></header>
      <div class="body">
        <div class="result busy"><span class="spin"></span>Asking ${esc(host)} what it offers…</div>
      </div></div>`;
    let res;
    try {
      res = await api.post("/api/shares/browse",
                           { host, username: user, password: pass });
    } catch (e) { res = { ok: false, error: e.message, shares: [] }; }

    const needsAuth = !res.ok && /LOGON_FAILURE|ACCESS_DENIED|NT_STATUS_ACCESS/i.test(res.error || "");
    d.innerHTML = `<div class="card">
      <header><h3>${esc(host)}</h3></header>
      <div class="body">
        ${res.ok ? "" : `<div class="result ${needsAuth ? "" : "bad"}"><b>${
            needsAuth ? "This server wants a username and password"
                      : "Couldn't list shares"}</b>
           <div class="why">${esc(res.error)}</div></div>`}
        <label class="f"><span>Username</span>
          <input id="w-suser" value="${esc(user)}" autocomplete="off"
                 placeholder="DOMAIN\\user or user"></label>
        <label class="f"><span>Password</span>
          <input id="w-spass" type="password" value="${esc(pass)}"
                 autocomplete="new-password"></label>
        <div class="btn-row">
          <button class="btn" id="w-recheck">List shares with these credentials</button>
        </div>
        <label class="f"><span>Share</span>
          <input id="w-share" list="w-sharelist" placeholder="OTHER">
          <datalist id="w-sharelist">${
            res.shares.map(s => `<option value="${esc(s)}"></option>`).join("")}</datalist>
          <span class="help">${res.shares.length
            ? "Pick one, or type a share that wasn't listed."
            : "Type the share name — it doesn't have to be one we could list."}</span>
        </label>
        <label class="f"><span>Folder inside the share</span>
          <input id="w-path" placeholder="RiparrDumps">
          <span class="help">Leave empty to use the top level.</span></label>
        <div class="btn-row">
          <button class="btn primary" id="w-test">Test write</button>
        </div>
        <div id="w-testres"></div>
      </div></div>`;

    // Re-ask the server, this time as somebody. Keeps whatever share and folder were
    // already typed, so entering a password does not throw the rest away.
    $("#w-recheck").onclick = () => {
      this.data.share = $("#w-share").value.trim();
      this.data.path = $("#w-path").value.trim();
      this.pickHost(host, { user: $("#w-suser").value.trim(),
                            pass: $("#w-spass").value });
    };
    if (this.data.share) $("#w-share").value = this.data.share;
    if (this.data.path) $("#w-path").value = this.data.path;
    $$("#w-detail input").forEach(i => {
      i.onkeydown = (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        (i.id === "w-suser" || i.id === "w-spass" ? $("#w-recheck") : $("#w-test")).click();
      };
    });

    $("#w-test").onclick = async () => {
      const body = {
        host, share: $("#w-share").value.trim(), path: $("#w-path").value.trim(),
        username: $("#w-suser").value.trim(), password: $("#w-spass").value,
      };
      if (!body.share) {
        $("#w-testres").innerHTML =
          `<div class="result bad"><b>Which share?</b>
           Enter the share name — for \\\\server\\OTHER\\RiparrDumps that is
           <code>OTHER</code>, with <code>RiparrDumps</code> as the folder.</div>`;
        return;
      }
      const out = $("#w-testres");
      out.innerHTML = `<div class="result busy"><span class="spin"></span>Writing a test
        file and reading it back…</div>`;
      try {
        const r = await api.post("/api/shares/test", body);
        if (r.ok) {
          out.innerHTML = `<div class="result ok"><b>The share works</b>
            Wrote a file to ${esc(r.target)}, read it back and deleted it.</div>`;
          await api.post("/api/shares", { ...body, name: `${host}/${body.share}` });
          $("#w-go").disabled = false;
          toast("Share saved", "ok");
        } else {
          out.innerHTML = `<div class="result bad"><b>That didn't work</b>
            Failed at the ${esc(r.stage)} step.<div class="why">${esc(r.error)}</div></div>`;
        }
      } catch (e) {
        out.innerHTML = `<div class="result bad"><b>That didn't work</b>
          <div class="why">${esc(e.message)}</div></div>`;
      }
    };
  },

  async layout() {
    const s = await api.get("/api/settings");
    $("#wz-body").innerHTML = `
      <div class="wz-step">Step 4 of 5</div>
      <h1>How should files be named?</h1>
      <p class="muted">These defaults follow Plex and Jellyfin conventions. You can
        change them later in Settings.</p>
      <div class="section"><div>
        <div class="grid2">
          <label class="f"><span>Movie folder</span>
            <input id="s-movie-folder" value="${esc(s.movie_folder)}"></label>
          <label class="f"><span>TV folder</span>
            <input id="s-tv-folder" value="${esc(s.tv_folder)}"></label>
        </div>
        <label class="f"><span>Movie file name</span>
          <input id="s-movie" value="${esc(s.movie_template)}"></label>
        <label class="f"><span>Episode file name</span>
          <input id="s-tv" value="${esc(s.tv_template)}"></label>
        <label class="f"><span>When Riparr can't identify a disc</span>
          <select id="s-unknown">
            <option value="ask" ${s.on_unknown_disc === "ask" ? "selected" : ""}>Ask me (recommended)</option>
            <option value="label" ${s.on_unknown_disc === "label" ? "selected" : ""}>Use the disc label</option>
            <option value="skip" ${s.on_unknown_disc === "skip" ? "selected" : ""}>Skip the disc</option>
          </select>
          <span class="help">A wrongly named file quietly pollutes your library, which is
            worse than one waiting ten seconds for your attention.</span></label>
      </div></div>
      <div class="wz-actions">
        <div class="grow"></div>
        <button class="btn primary" id="w-go">Continue</button>
      </div>`;
    $("#w-go").onclick = async () => {
      await api.put("/api/settings", {
        movie_folder: $("#s-movie-folder").value, tv_folder: $("#s-tv-folder").value,
        movie_template: $("#s-movie").value, tv_template: $("#s-tv").value,
        on_unknown_disc: $("#s-unknown").value,
      });
      this.next();
    };
  },

  done() {
    $("#wz-body").innerHTML = `
      <div class="wz-step">All set</div>
      <h1>Riparr is ready</h1>
      <p class="muted">From here the loop is: insert a disc, close the tray, walk away.
        The disc ejects when it's done.</p>
      <div class="section"><div>
        <div class="kv">
          <div class="k">Insert a disc</div><div class="v">Riparr identifies it and starts on its own</div>
          <div class="k">Watch the LED</div><div class="v">Green when it worked, orange when it didn't</div>
          <div class="k">Everything else</div><div class="v">Lives in Settings, and most people never open it</div>
        </div>
      </div></div>
      <div class="wz-actions">
        <div class="grow"></div>
        <button class="btn primary" id="w-go">Open Riparr</button>
      </div>`;
    $("#w-go").onclick = async () => {
      await api.post("/api/setup/complete");
      location.hash = "#/queue";
      await boot();
    };
  },
};

function pollMakeMKV(into = "#mk-progress") {
  const box = $(into);
  if (!box) return;
  const timer = setInterval(async () => {
    let st;
    try { st = await api.get("/api/makemkv/install"); } catch (e) { return; }
    if (st.phase === "done") {
      clearInterval(timer);
      box.innerHTML = `<div class="result ok"><b>Installed</b>${esc(st.message)}</div>`;
      toast("MakeMKV installed", "ok");
      return;
    }
    if (st.phase === "error") {
      clearInterval(timer);
      box.innerHTML = `<div class="result bad"><b>${esc(st.message)}</b>
        ${st.detail ? `<div class="why">${esc(st.detail)}</div>` : ""}</div>`;
      const btn = $("#mk-install");
      if (btn) btn.disabled = false;
      return;
    }
    box.innerHTML = `<div class="result busy"><span class="spin"></span>${esc(st.message || "Working…")}
      <div class="bar" style="margin-top:10px"><i style="width:${(st.progress * 100).toFixed(0)}%"></i></div></div>`;
  }, 700);
}

/* ════════════════════ views ════════════════════ */
const views = {};

/* One page, one thing. The toolbar row of icon-over-label buttons that used to sit
   between Auto Rip and the table is now two actions in the page header: Options and
   Status were links to pages already one click away in the sidebar, and Refresh and
   Eject are the only two verbs this page actually owns.

   The queue and the tray are also one card rather than two sections. "Drive" repeated
   the disc name the empty state had just said, which is the kind of duplication that
   reads as an interface describing its own data model. */
views.queue = async () => {
  // Fetch the drive state rather than reading the snapshot boot() took. The tray is
  // the one thing on this page that changes without the user doing anything -- a disc
  // goes in and nothing on screen knew, and the Refresh button re-rendered the same
  // stale snapshot, so it looked broken rather than slow.
  const [q, st, ar] = await Promise.all([
    api.get("/api/queue"),
    api.get("/api/status"),
    api.get("/api/autorip"),
  ]);
  // A refused duplicate leaves nothing on this page to look at -- no job, no file --
  // so the page it belongs on is Discs, next to the disc in question and the button
  // that overrides the refusal. Checked here because this is the only view that keeps
  // polling, and putting a disc in is the one thing that happens with no user action.
  if (st.duplicate && st.duplicate.fingerprint) {
    goToDuplicate(st.duplicate);
    // Not an empty string. Acknowledging the duplicate is a round trip, and a page
    // that goes blank for even a moment reads as a crash rather than as a redirect.
    return `<div class="card"><div class="empty-state">
      <div class="big">${icon("compact-disc")}</div>
      <h2>You've already ripped ${esc(st.duplicate.title || "this disc")}</h2>
      <p>Taking you to it…</p></div></div>`;
  }
  const jobs = q.jobs;
  const sending = q.sending || [];
  state.typical = q.typical_seconds;
  state.typicalN = q.typical_samples;
  state.typicalStages = q.typical_stages || {};
  state.stageLabels = q.stage_labels || {};
  state.stageOrder = q.stage_order || [];
  state.status = st;
  const drives = state.status.drives || [];
  const inTray = drives.find(d => d.present);
  setDiscArt(inTray && inTray.label);       // fire and forget; never blocks the render
  const busy = jobs.some(j => j.state !== "needs_input");
  const loaded = drives.find(d => d.present);
  return `
    ${head("Queue", "Ripping and uploading happen as one overlapping operation.",
           `<button class="tool" id="t-refresh"><span class="ti">${icon("arrows-rotate")}</span>Refresh</button>
            <button class="tool" id="t-eject" ${drives.length && !busy ? "" : "disabled"}>
              <span class="ti">${icon("eject")}</span>Eject</button>`)}
    ${autoRipPanel(ar)}
    <div class="card disc-cell${artState.image ? " has-art" : ""}">
      ${artState.image ? `<div class="tray-art" role="presentation"
           style="background-image:url('${artState.image}')"></div>` : ""}
      ${jobs.length ? `${jobs.map(jobRow).join("")}
        ${trayStrip(drives, state.status.optical)}`
      : tray(drives, state.status.optical, loaded && !busy)}
    </div>
    ${sendingStrip(sending)}`;
};

/* ── a job in flight ──
   A table row cannot hold a question, and `needs_input` has to be able to ask one, so
   a job is a block rather than a `<tr>`. That also buys room for the phase line, which
   is the difference between a bar that is moving and a box that has hung -- the
   distinction that decides whether somebody pulls the cable. */

/* ── which disc is this, at a glance ──
   Riparr's three families, in the words on the box. The Queue, History and Discs all
   render this identically, because "is that my DVD or my Blu-ray of the same film" is
   a question the answer to must not change shape depending on where it is asked. */
const FAMILY = {
  dvd:    { label: "DVD",     cls: "fam-dvd" },
  bluray: { label: "Blu-ray", cls: "fam-bluray" },
  uhd:    { label: "4K UHD",  cls: "fam-uhd" },
};

function familyTag(family, extra = "") {
  const f = FAMILY[family];
  if (!f) return "";
  return `<span class="fam ${f.cls}${extra ? " " + extra : ""}">${esc(f.label)}</span>`;
}

const STATE_LABEL = {
  queued: "Waiting", identifying: "Reading the disc", ripping: "Ripping",
  transferring: "Uploading", verifying: "Verifying", needs_input: "Needs you",
};

/* ── still crossing the network ──
   A disc whose rip is on the card has already been handed back, and the next one may
   well be spinning. These jobs are finished as far as the user is concerned -- they
   just are not *there* yet -- so they get a quiet strip under the drive rather than a
   panel competing with the disc actually in the machine. */
function sendingStrip(sending) {
  if (!sending.length) return "";
  return `<div class="sending">
    <div class="sending-head">${icon("upload")}
      <span>${sending.length === 1 ? "Still crossing to your library"
                                   : `${sending.length} still crossing to your library`}</span>
      <span class="grow"></span>
      <span class="muted">the drive is free — put the next disc in</span></div>
    ${sending.map(j => {
      const done = j.state === "verifying" ? 100 : Number(pct(j.bytes_sent, j.bytes_total));
      return `<div class="send-row">
        <span class="send-name" title="${esc(j.title || j.disc_label || "")}">${
          esc(j.title || j.disc_label || "Unknown disc")}</span>
        ${familyTag(j.disc_family)}
        <div class="send-bar"><i style="width:${done}%"></i></div>
        <span class="send-pct">${
          j.state === "verifying" ? "checking"
          : done > 0 ? `${Math.round(done)}%` : "waiting"}</span>
        <span class="send-size muted">${esc(filesize(j.bytes_total))}</span>
        <button class="icon-btn" data-cancel="${j.id}" title="Cancel">${icon("xmark")}</button>
      </div>`;
    }).join("")}
  </div>`;
}


function jobRow(j) {
  if (j.state === "needs_input") return identifyPrompt(j);
  const ripPct = pct(j.bytes_ripped, j.bytes_total);
  const sentPct = pct(j.bytes_sent, j.bytes_total);
  const verPct = pct(j.bytes_verified, j.bytes_total);
  // The bar shows the stage that is happening now, which is what a stepper promises.
  // stage_pct is reported by every stage including identification, where there are no
  // bytes to count -- reading an encrypted disc is minutes of CPU before a file exists,
  // and that was the stretch with nothing on screen at all.
  const stage = typeof j.stage_pct === "number" ? j.stage_pct * 100 : null;
  const byBytes = j.state === "ripping" ? ripPct
                : j.state === "transferring" ? sentPct
                : j.state === "verifying" ? verPct : 0;
  const active = stage !== null ? stage : byBytes;
  // Reading the disc reports nothing, and MakeMKV is silent until its first progress
  // line, so a real rip opens with a bar sitting at zero. Sweep it instead: "moving,
  // but I cannot tell you how far" is a different message from "stopped".
  const working = active <= 0
    && ["identifying", "queued", "ripping", "transferring", "verifying"].includes(j.state);

  // Three steps, always all three, so the shape of the job is legible before it starts
  // and the user can see what is still to come. The old version was three spans of
  // 11.5px muted text distinguished only by colour -- the active one was technically
  // marked and practically invisible.
  // In direct mode the bytes coming off the disc are going onto the share *as they
  // are read* -- there is no separate upload, only a rename at the end. So Rip and
  // Upload are genuinely one operation and the stepper says so: both light together,
  // joined, and both carry the same number, because it is the same number. This is
  // what D11 promised and the mount delivered by another route.
  const together = j.mode === "direct";
  const steps = [
    { key: "ripping", label: "Rip", pct: ripPct },
    { key: "transferring", label: together ? "To library" : "Upload",
      pct: together ? ripPct : sentPct },
    { key: "verifying", label: "Verify", pct: verPct },
  ];
  const order = ["queued", "identifying", "ripping", "transferring", "verifying"];
  const at = order.indexOf(j.state);
  // Identification belongs to Rip as far as anyone watching is concerned -- it is the
  // box reading the disc. Without this map no step matched `identifying` at all, so
  // for the first ten minutes of every rip all three pills sat grey and the interface
  // looked idle while the drive was audibly working.
  const stageOf = { queued: "ripping", identifying: "ripping", ripping: "ripping",
                    transferring: "transferring", verifying: "verifying" };
  const nowKey = stageOf[j.state];
  const stepHtml = steps.map((st, i) => {
    const mine = order.indexOf(st.key);
    // Direct mode: while the disc is being read the film is already landing on the
    // share, so "ripping" lights the transfer step too.
    const isNow = nowKey === st.key
      || (together && st.key === "transferring" && nowKey === "ripping")
      || (together && st.key === "ripping" && nowKey === "transferring");
    const done = st.pct >= 100 || (at > mine && at !== -1);
    const cls = [isNow ? "now" : done ? "done" : "todo"];
    if (together && i < 2) cls.push(i === 0 ? "pair-a" : "pair-b");
    const mark = done ? icon("circle-check") : isNow ? `<span class="pip"></span>`
                                                     : `<span class="pip hollow"></span>`;
    // The live pill shows the stage's own number, which during identification is the
    // only number there is.
    const live = isNow && stage !== null ? stage : st.pct;
    const val = isNow && live > 0 ? `${Math.round(live)}%` : "";
    return `<div class="step ${cls.join(" ")}">${mark}<span class="step-l">${st.label}</span>
      ${val ? `<span class="step-v">${val}</span>` : ""}</div>`;
  }).join("");

  return `
    <div class="job">
      <div class="job-head">
        <div class="grow">
          <div class="job-title">${esc(j.title || j.disc_label || "Unknown disc")}</div>
          <div class="job-phase">${esc(j.phase || STATE_LABEL[j.state] || j.state)}</div>
        </div>
        ${familyTag(j.disc_family)}
        ${j.mode ? `<span class="badge ${j.mode === "burst" ? "burst" : ""}">${esc(j.mode)}</span>` : ""}
        <span class="badge state">${esc(STATE_LABEL[j.state] || j.state)}</span>
        <button class="icon-btn" data-cancel="${j.id}" title="Cancel">${icon("xmark")}</button>
      </div>
      ${j.warning ? `<div class="job-warn">${icon("triangle-exclamation")}
        <span>${esc(j.warning)}</span></div>` : ""}
      <div class="job-meter">
        <div class="bar${working ? " working" : ""}"><i style="width:${active}%"></i></div>
        <div class="job-figs">
          <span class="job-pct">${active > 0 ? `${Math.round(active)}%` : ""}</span>
          <span class="grow"></span>
          ${j.eta_seconds
            ? `<span class="job-eta">${esc(duration(j.eta_seconds))} left</span>`
            // No percentage to show means a stage that cannot report one -- reading an
            // encrypted disc is minutes of CPU and MakeMKV emits no progress at all
            // during it. Elapsed time is not progress, but it is true, it moves every
            // second, and it is the difference between "working" and "hung".
            : j.started_at
            ? `<span class="job-eta">${esc(duration(Math.max(0, (Date.now() / 1000) - j.started_at)))} so far${
                state.typical
                  ? ` · <span class="job-guess">usually done by ${esc(clockAt(j.started_at + state.typical))}</span>`
                  : ""}</span>`
            : ""}
        </div>
      </div>
      <div class="job-steps${together ? " paired" : ""}">${stepHtml}${
        together ? `<span class="pair-note">at once</span>` : ""}</div>
      ${stageClock(j)}
    </div>`;
}

/* Point the browser at the disc it already has, once. The acknowledgement is what
   stops it happening again on the next poll -- without it the page would bounce back
   to Discs every five seconds and the user could never leave. */
async function goToDuplicate(dupe) {
  try { await api.post("/api/duplicate/ack", {}); } catch (e) { /* show it anyway */ }
  location.hash = `#/discs/${encodeURIComponent(dupe.fingerprint)}`;
}

/* ── the counting timer ──
   MakeMKV cannot say how far through a disc scan it is, and the kernel cannot see the
   reads because they go through /dev/sg0 -- so for the sixteen minutes before a byte
   is written there is no percentage to show and never will be. What there is: this box
   has done this before and took about as long each time.

   So the slow stages get a clock instead of a bar. It counts up (which is always true)
   against the median (which is a guess, and says so), and the remaining stages are
   added on to answer the question actually being asked -- when can I come back. */
function stageClock(j) {
  const med = state.typicalStages || {};
  const order = state.stageOrder || [];
  const name = j.stage_name;
  if (!name || !j.stage_started) return "";
  const label = (state.stageLabels || {})[name] || name;
  const elapsed = Math.max(0, Date.now() / 1000 - j.stage_started);
  const mine = med[name] && med[name].seconds;

  // Everything after this stage, at its usual cost. Verification is skipped when it
  // is off, because promising a stage that will not run is worse than a vaguer number.
  const at = order.indexOf(name);
  const rest = at < 0 ? [] : order.slice(at + 1)
    .filter(k => med[k] && !(k === "verify" && state.settings
                             && state.settings.verify_mode === "off"));
  const restSecs = rest.reduce((a, k) => a + med[k].seconds, 0);

  if (!mine) {
    return `<div class="clock">
      <span class="clock-stage">${esc(label)}</span>
      <span class="clock-el">${esc(duration(elapsed))}</span>
      <span class="grow"></span>
      <span class="muted">no history for this stage yet</span></div>`;
  }
  const left = mine - elapsed;
  const over = left < 0;
  // A stage that has run long is not a stage that has failed, and saying "0 min left"
  // for six minutes is how an interface loses the user's trust. Say the true thing.
  const rem = over ? `${duration(-left)} over the usual ${duration(mine)}`
                   : `about ${duration(left)} left in this stage`;
  const total = over ? null : left + restSecs;
  return `<div class="clock${over ? " over" : ""}">
    <span class="clock-stage"><i class="sg-${esc(name)}"></i>${esc(label)}</span>
    <span class="clock-el">${esc(duration(elapsed))} of ~${esc(duration(mine))}</span>
    <span class="grow"></span>
    <span class="clock-est">${esc(rem)}${
      total != null && restSecs > 0
        ? ` · <b>done around ${esc(clockAt(Date.now() / 1000 + total))}</b>` : ""}</span>
  </div>
  <div class="clock-bar"><i class="sg-${esc(name)}"
       style="width:${Math.min(100, (elapsed / mine) * 100).toFixed(1)}%"></i></div>`;
}

function verifyNote() {
  const s = state.settings || {};
  return s.verify_mode === "deep"
    ? "Reads the whole film back off the share — roughly doubles the time and needs as much free space again."
    : s.verify_mode === "off"
    ? "Nothing is checked after the upload."
    : "Compares the size on your library with what was sent.";
}

const leg = (p) => (p >= 100 ? "\u2713" : p > 0 ? `${Math.round(p)}%` : "");

/* Time, not bytes. concept.md says the user should never see a gigabyte and says the
   same about progress and time estimates; the queue did the first half and showed two
   bars that answered "how far" but never "how long". */
/* A wall-clock time, because "usually done by 07:12" is a thing a person can plan
   around and "about 26 minutes" is a thing they have to do arithmetic on. */
function clockAt(epochSeconds) {
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function duration(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 90) return `${sec}s`;
  const m = Math.round(sec / 60);
  if (m < 90) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/* ── the identify prompt ──
   `on_unknown_disc` has defaulted to "ask" since the beginning and nothing anywhere
   asked. This is the asking. */
function identifyPrompt(j) {
  const titles = (j.titles || []).filter(t => t.seconds >= 60);
  return `
    <div class="job needs">
      <div class="job-head">
        <div class="grow">
          <div class="job-title">${esc(j.disc_label || "A disc")}</div>
          <div class="job-phase">${esc(j.question || "Riparr needs a hand with this one.")}</div>
        </div>
        <span class="badge warn">Needs you</span>
      </div>
      <label class="f wide"><span>What is this film?</span>
        <input id="ni-name" value="${esc(j.title || "")}" placeholder="e.g. The Matrix (1999)">
        <span class="help">A year in brackets is used as the year. Without one Riparr
          won't invent it.</span></label>
      ${titles.length > 1 ? `
        <div class="ni-titles">
          <div class="ni-label">Which title is the film?</div>
          ${titles.map(t => `
            <label class="ni-title">
              <input type="radio" name="ni-title" value="${t.index}"
                     ${t.index === j.chosen_title ? "checked" : ""}>
              <span class="ni-dur">${esc(duration(t.seconds))}</span>
              <span class="ni-name">${esc(t.name || `Title ${t.index}`)}</span>
              <span class="ni-size muted">${t.bytes ? esc(gb(t.bytes)) : ""}</span>
            </label>`).join("")}
        </div>` : ""}
      <div class="btn-row">
        <button class="btn primary" data-answer="${j.id}">Rip it</button>
        <button class="btn" data-skip="${j.id}">Skip this disc</button>
      </div>
    </div>`;
}

const gb = (b) => `${(b / 1073741824).toFixed(1)} GB`;

/* ── the tray ──
   The disc and the drive holding it are one fact, so they are drawn once. Which of
   the three shapes below applies depends only on how far up the chain something is
   missing: no drive at all, a drive with an open tray, or a disc sitting in one. */

function driveName(d) {
  return d.known_as || [d.vendor, d.model].filter(Boolean).join(" ") || "Optical drive";
}

/* ── what the drive can read ──
   Three chips, always all three, lit or not. Showing only what a drive *can* do would
   answer the question people ask ("what have I got?") and not the one that costs them
   money ("can it do the discs on my shelf?") — and 4K is the one that costs money, so
   it is never folded into Blu-ray however tempting the width saving is.

   The server decides the 4K chip. It is the one of the three that hardware cannot
   self-report: there is no MMC profile for UHD, so the answer comes from the drive
   registry and from MakeMKV, and neither of those is in the browser. */
function driveTags(d) {
  const uhdOn = d.uhd === "yes" || d.libredrive === "enabled";
  const chips = [
    ["DVD", !!d.reads_dvd, null],
    ["Blu-ray", !!d.reads_bluray, null],
    ["4K UHD", uhdOn, d.reads_bluray ? d.uhd_label : null],
  ];
  return `<span class="drive-tags">${chips.map(([label, on, title]) =>
    `<span class="tag ${on ? "on" : ""}"${title ? ` title="${esc(title)}"` : ""}>${label}</span>`
  ).join("")}</span>`;
}

/* The drive, its device node and what it reads — one line, used by every tray shape
   so the three cannot drift apart. */
function driveLine(d) {
  return `<p class="tray-drive">${esc(driveName(d))}
    <span class="dev">${esc(d.device)}</span>${driveTags(d)}</p>`;
}

// The diagnosis hints mark their single actionable sentence with **bold**. Escape
// first, then promote -- never the other way round.
function mdBold(text) {
  return esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
}

function tray(drives, optical, canRip) {
  if (!drives.length) {
    // An empty card used to render as nothing at all, so "no drive" was communicated
    // by absence -- the one case where the user most needs to be told something.
    const hint = optical && optical.hint;
    // When the box can put it right itself, offer the button instead of asking the
    // user to understand device trees. The wrong-socket case is the common one and it
    // is invisible: that port logs nothing at all when you plug something into it.
    const fixable = optical && optical.fixable === "usb-host";
    return `<div class="empty-state tray-none">
      <div class="big">${icon("triangle-exclamation")}</div>
      <h2>No optical drive detected</h2>
      <p>Riparr has nothing to read a disc with, so nothing else on this page can
         happen yet.</p>
      ${hint ? `<p class="why">${mdBold(hint)}</p>` : ""}
      ${fixable ? `<div class="btn-row" style="justify-content:center">
        <button class="btn primary" id="usb-host-fix">Make both USB-C sockets work</button>
      </div>
      <p class="micro">This reconfigures the second socket and restarts the box, so it
         stops mattering which one you used. About a minute.</p>` : ""}
    </div>`;
  }
  const d = drives.find(x => x.present) || drives[0];
  if (!d.present) {
    return `<div class="empty-state">
      <div class="big">${icon("compact-disc")}</div>
      <h2>Nothing in the queue</h2>
      <p>Insert a disc and close the tray. Riparr takes it from there.</p>
      ${driveLine(d)}
    </div>`;
  }
  /* A disc this drive cannot read is not an error state to be discovered three
     minutes into a rip. It is the tray, described accurately, with the reason. */
  if (d.cannot_read) {
    return `<div class="empty-state tray-loaded bad">
      <div class="big">${icon("triangle-exclamation")}</div>
      <h2>${esc(d.label || d.disc_word || "Disc loaded")}</h2>
      <p class="why">${esc(d.cannot_read)}</p>
      ${driveLine(d)}
    </div>`;
  }
  /* "BD-ROM" is what the drive calls it. "4K UHD disc" is what is printed on the box
     the user is holding, and the server works out which of the two this is. */
  const what = d.disc_word || d.media;
  return `<div class="empty-state tray-loaded">
    <div class="big spinning">${icon("compact-disc")}</div>
    <h2>${esc(d.label || "Disc loaded")}</h2>
    <p>${what ? `${esc(what)} \u2014 loaded and ready.` : "Loaded and ready."}</p>
    ${d.space_warning ? `<p class="why warn-text">${esc(d.space_warning)}</p>` : ""}
    ${canRip ? `<div class="btn-row tray-go">
      <button class="btn primary" id="rip-now">${icon("play")} Rip this disc</button>
    </div>` : ""}
    ${driveLine(d)}
  </div>`;
}

/* When there are rips to look at, the tray is a footer on the same card rather than
   the hero -- present, but not competing with the thing that is actually moving. */
function trayStrip(drives, optical) {
  if (!drives.length) {
    return `<div class="tray-strip bad">${icon("triangle-exclamation")}
      <span>No optical drive detected</span></div>`;
  }
  const d = drives.find(x => x.present) || drives[0];
  return `<div class="tray-strip">${icon("compact-disc")}
    <span>${esc(driveName(d))} <span class="dev">${esc(d.device)}</span></span>
    ${driveTags(d)}
    <span class="grow"></span>
    <span class="${d.present ? "loaded" : "muted"}">${
      d.present ? esc(d.label || d.disc_word || "disc loaded") : "tray empty"}</span></div>`;
}

const pct = (a, b) => (b ? Math.min(100, (a / b) * 100).toFixed(1) : 0);

/* The checklist is the answer to "it isn't auto ripping" -- a question asked most
   often with the switch already ON, when something downstream broke afterwards. So
   every prerequisite is listed whether or not it is met, and each row says what
   currently satisfies it rather than only complaining when it doesn't.

   Closed by default once everything passes: five green rows on the landing page are
   noise until the day they aren't. `<details>` rather than a JS disclosure, so the
   open/closed state survives the poll that re-renders this page. */
const CHECK_ICON = { ok: "circle-check", warn: "triangle-exclamation", fail: "circle-exclamation" };

function autoRipPanel(ar) {
  const on = ar.enabled;
  const checks = ar.checks || [];
  const fails = checks.filter(c => c.state === "fail").length;
  const warns = checks.filter(c => c.state === "warn").length;
  return `
    <div class="autorip ${on ? "on" : ar.ready ? "" : "blocked"}">
      <label class="ar-switch ${ar.ready ? "" : "off"}">
        <input type="checkbox" id="autorip" ${on ? "checked" : ""}
               ${ar.ready ? "" : "disabled"}>
        <span class="track"></span>
      </label>
      <div class="ar-text">
        <div class="ar-title">Auto Rip</div>
        <div class="ar-sub">${
          on ? "Insert a disc and walk away. Riparr does the rest and ejects when it's done."
          : ar.ready ? "Turn this on and Riparr starts ripping the moment a disc is inserted."
          : "Not available yet \u2014 see below."}</div>
        ${checkList(checks, fails, warns)}
      </div>
    </div>
    ${ripOptions()}`;
}

/* ── how every rip behaves ──
   These two settings kept being bolted onto the end of the Auto Rip blurb until it was
   a paragraph with form controls buried in it. They are not part of the switch: they
   govern a rip started by hand too. So they get their own strip, two matched cards,
   each with its control on the top line and the consequence underneath -- the same
   shape twice, which is what makes a panel read as designed rather than accumulated. */
function ripOptions() {
  const s = state.settings || {};
  const lib = (state.status && state.status.library) || {};
  const direct = s.transfer_mode === "direct";
  const card = s.card_speed || {};
  const deepOnDirect = direct && s.verify_mode === "deep";
  return `
  <div class="rip-opts">
    <div class="ropt">
      <div class="ropt-head">
        <span class="ropt-k">${icon("hard-drive")} Each rip goes</span>
        <button class="btn sm" id="ar-speedtest"
                title="Measures your card and says which of these suits it">Test my card</button>
        <select id="ar-route" title="Applies to every rip, automatic or started by hand">
          ${opt("direct", "straight to your library", s.transfer_mode)}
          ${opt("auto", "onto the card, then sent", s.transfer_mode)}
        </select>
      </div>
      <p class="ropt-why">${
        direct
          ? `No size limit, so a Blu-ray fits whatever card you have, and the card stops
             wearing out.${card.write_mbs
               ? ` Yours writes at about <b>${esc(String(card.write_mbs))} MB/s</b>.` : ""}`
          : `The rip is safe on the card before anything is sent, so a network that drops
             mid-disc costs a re-send rather than a re-rip.`}</p>
      ${!lib.mounted && direct ? `<p class="ropt-warn">${icon("triangle-exclamation")}
        <span>Your library isn't mounted, so rips will stage on the card until it is.</span></p>` : ""}
      <span class="test-out ropt-out" id="ar-speed-out"></span>
    </div>

    <div class="ropt">
      <div class="ropt-head">
        <span class="ropt-k">${icon("circle-check")} After each rip</span>
        <select id="ar-verify" title="Applies to every rip, automatic or started by hand">
          ${opt("quick", "quick check", s.verify_mode)}
          ${opt("deep", "deep check (slow)", s.verify_mode)}
          ${opt("off", "no check", s.verify_mode)}
        </select>
      </div>
      <p class="ropt-why">${verifyNote()}</p>
      ${deepOnDirect ? `<p class="ropt-warn">${icon("triangle-exclamation")}
        <span>Deep checking needs two copies. Straight-to-library leaves one, so Riparr
        will check the size instead.</span></p>` : ""}
    </div>
  </div>`;
}

function checkList(checks, fails, warns) {
  if (!checks.length) return "";
  const summary =
    fails && warns ? `${fails} to fix, ${warns} to watch`
    : fails ? `${fails} thing${fails === 1 ? "" : "s"} to fix first`
    : warns ? `${warns} thing${warns === 1 ? "" : "s"} worth knowing about`
    : `All ${checks.length} checks pass`;
  const worst = fails ? "fail" : warns ? "warn" : "ok";
  return `<details class="ar-checks" ${fails || warns ? "open" : ""}>
    <summary><span class="cl-sum ${worst}">${icon(CHECK_ICON[worst])} ${esc(summary)}</span></summary>
    <ul>${checks.map(c => `
      <li class="${esc(c.state)}">
        <span class="cl-mark">${icon(CHECK_ICON[c.state] || "circle-info")}</span>
        <span class="cl-what">${esc(c.what)}</span>
        <span class="cl-detail">${c.where && c.state !== "ok"
          ? `<a href="${esc(c.where)}">${esc(c.detail)}</a>` : esc(c.detail)}</span>
        ${c.state !== "ok" && c.why ? `<span class="cl-why">${esc(c.why)}</span>` : ""}
      </li>`).join("")}</ul>
  </details>`;
}

/* ── History: the data page ──
   The two pages had swapped jobs. History was a wall of posters that answered "what
   have I got" -- which Discs already knew, since Discs *is* the record of what this
   box has seen -- and answered "what happened, and how long did it take" not at all.
   A grouped tile saying "5 attempts" is the exact shape of a number with nowhere to
   go: no way to see what the five were, when, or why four of them failed.

   So History is now one row per attempt, newest first, with the stage breakdown the
   rip engine records and the retries the box can actually perform. Posters moved to
   Discs, where the page had four columns of text and nothing to look at. */

const RETRY_ICON = {
  "upload": "upload", "rip": "compact-disc",
  "verify-quick": "circle-check", "verify-deep": "magnifying-glass",
};

views.history = async () => {
  const h = await api.get("/api/history");
  const jobs = h.jobs || [];
  if (!jobs.length) {
    return `${head("History", "Every attempt, what each stage cost, and what can be retried.")}
      <div class="card"><div class="empty-state"><div class="big">${icon("clock-rotate-left")}</div>
        <h2>No rips yet</h2><p>Finished and failed rips are recorded here with a
          breakdown of where the time went.</p></div></div>`;
  }

  // "5 attempts" was the old page's whole answer. The number is only useful if the
  // five are visible, so each row is numbered within its film and every one of them is
  // on screen -- the count becomes "try 4 of 5" on a row you can read the error of.
  const key = (j) => (j.title || j.disc_label || "?")
    .toLowerCase().replace(/[_.]+/g, " ").replace(/\s+/g, " ").trim();
  const tally = new Map();
  for (const j of jobs) tally.set(key(j), (tally.get(key(j)) || 0) + 1);
  const seen = new Map();
  // `jobs` is newest first, so counting down from the total numbers them in the order
  // they actually happened.
  for (const j of jobs) {
    const k = key(j);
    const n = (seen.get(k) || 0) + 1;
    seen.set(k, n);
    j._try = tally.get(k) - n + 1;
    j._tries = tally.get(k);
  }

  // Per family. A Blu-ray is four times the data of a DVD, so one blended median
  // describes neither -- each row is compared against its own kind.
  const byKind = h.typical_by_kind || {};
  const typicalFor = (j) => byKind[j.disc_family] || h.typical_stages || {};
  const typical = h.typical_stages || {};
  const row = (j) => {
    // data-label carries each cell's column heading, so the narrow layout can put the
    // heading back beside the value when the table stops being a table.
    const size = j.state === "done" ? j.bytes_sent || j.bytes_ripped || j.bytes_total
                                    : j.bytes_ripped || 0;
    // Work done, not wall clock. `finished_at` moves every time a job is retried or
    // re-verified, so the span from start to finish on a job checked again an hour
    // later reads "1h" for thirteen seconds of work. The stages know better, and this
    // is then the same number the bar beside it is drawn from.
    const worked = (j.stages || []).reduce((a, st) => a + st.seconds, 0);
    const took = worked || (j.finished_at && j.started_at
                            ? j.finished_at - j.started_at : null);
    return `<tr class="hist ${j.state}" data-job="${j.id}">
      <td class="stat">${icon(j.state === "done" ? "circle-check"
                             : j.state === "cancelled" ? "ban"
                             : "triangle-exclamation")}</td>
      <td class="hist-name">
        <div class="hist-title">${esc(j.title || j.disc_label || "Unknown disc")}${
          familyTag(j.disc_family)}</div>
        ${j.title && j.disc_label && j.title !== j.disc_label
          ? `<div class="hist-sub">${esc(j.disc_label)}</div>` : ""}
        ${j.error ? `<div class="hist-err">${esc(j.error)}</div>` : ""}
      </td>
      <td class="num" data-label="Attempt">${j._tries > 1
        ? `<span title="This film has been attempted ${j._tries} times. Every attempt is a row here.">try ${j._try} of ${j._tries}</span>`
        : `<span class="muted">1</span>`}</td>
      <td class="num" data-label="Size">${size ? esc(filesize(size)) : `<span class="muted">—</span>`}</td>
      <td class="num" data-label="Took">${took != null ? esc(duration(took)) : `<span class="muted">—</span>`}</td>
      <td class="hist-stages" data-label="Where the time went">${
        stageBar(j.stages, typicalFor(j))}</td>
      <td class="num" data-label="When"><span title="${esc(when(j.finished_at))}">${esc(ago(j.finished_at))}</span></td>
      <td class="act">${(j.retries || []).map(r =>
        `<button class="btn tiny" data-hretry="${j.id}" data-haction="${esc(r.action)}"
                 title="${esc(r.why)}">${icon(RETRY_ICON[r.action] || "arrows-rotate")
                 } ${esc(r.label)}</button>`).join("")}</td>
    </tr>
    ${j.dest_path ? `<tr class="hist-dest ${j.state}"><td></td>
      <td colspan="7"><span class="muted">${icon("hard-drive")} ${esc(j.dest_path)}</span>${
        j.verified_mode && j.verified_mode !== "off"
          ? ` <span class="badge ok">${esc(j.verified_mode)} verified</span>` : ""}</td></tr>` : ""}`;
  };

  return `${head("History", "Every attempt, what each stage cost, and what can be retried.",
                 stageLegend(typical, h.stage_order, h.stage_labels))}
    <div class="card"><table class="hist-table">
      <thead><tr>
        <th class="stat"></th><th>Title</th><th class="num">Attempt</th>
        <th class="num">Size</th><th class="num">Took</th><th>Where the time went</th>
        <th class="num">When</th><th class="act"></th>
      </tr></thead>
      <tbody>${jobs.map(row).join("")}</tbody>
    </table></div>
    ${stageNote(byKind)}`;
};

/* A proportional bar of the stages, because the interesting fact about a rip is not
   that it took thirty minutes -- it is that half of that was spent before a single
   byte was written, and no amount of staring at a total tells you that. */
function stageBar(stages, typical) {
  stages = (stages || []).filter(s => s.seconds > 0);
  if (!stages.length) return `<span class="muted">—</span>`;
  const total = stages.reduce((a, s) => a + s.seconds, 0);
  return `<div class="stagebar">${stages.map(s => {
    const med = typical[s.name] && typical[s.name].seconds;
    const vs = med ? ` — usually ${duration(med)}` : "";
    return `<i class="sg-${esc(s.name)}" style="flex:${s.seconds}"
       title="${esc(s.label)}: ${esc(duration(s.seconds))}${vs}${
         s.runs > 1 ? ` across ${s.runs} runs` : ""}"></i>`;
  }).join("")}</div>
  <div class="stagenums">${stages.map(s =>
    `<span><i class="sg-${esc(s.name)}"></i>${esc(duration(s.seconds))}</span>`).join("")}
  </div>`;
}

/* The key for the stage colours. It belongs in the header row rather than under the
   table: it is what makes every bar on the page readable, and a key you have to scroll
   past the data to reach is a key you look at once and then stop using.

   Doubling as "how long does this box normally take" -- the same medians the queue's
   counting timer is built from -- so it earns the space twice. */
const STAGE_SHORT = {
  identify: "Reading", decrypt: "Decrypting", save: "Saving",
  upload: "Uploading", verify: "Verifying",
};

function stageLegend(typical, order, labels) {
  order = order || Object.keys(STAGE_SHORT);
  return `<div class="stage-key" role="group" aria-label="Stage colours">
    ${order.map(k => {
      const t = typical[k];
      const full = (labels || {})[k] || k;
      return `<span class="sk${t ? "" : " unknown"}" title="${esc(full)}${
        t ? ` — usually ${duration(t.seconds)} on this box, over ${t.samples} rip${
              t.samples === 1 ? "" : "s"}`
          : " — no finished rips to average yet"}">
        <i class="sg-${esc(k)}"></i><b>${esc(STAGE_SHORT[k] || full)}</b>${
        t ? `<span class="sk-t">${esc(duration(t.seconds))}</span>` : ""}</span>`;
    }).join("")}
  </div>`;
}

/* The caveat that does not fit in a header chip, and the per-family numbers, which are
   the honest form of "how long does this take" -- a Blu-ray and a DVD are not the same
   job wearing different labels. */
function stageNote(byKind) {
  const kinds = Object.entries(byKind || {}).filter(([, v]) => Object.keys(v).length);
  const order = ["identify", "decrypt", "save", "upload", "verify"];
  return `
    ${kinds.length ? `<div class="card stage-key-full"><h3>Typical on this box</h3>
      ${kinds.map(([k, v]) => `<div class="kind-row">
        ${familyTag(k)}
        <span class="kind-times">${order.filter(n => v[n]).map(n =>
          `<span><i class="sg-${esc(n)}"></i>${esc(duration(v[n].seconds))}
            <span class="muted">${v[n].samples}&times;</span></span>`).join("")}</span>
      </div>`).join("")}</div>` : ""}
    <p class="muted stage-note">${kinds.length
      ? `Medians over this box's own finished rips, kept separate per kind of disc —
         a Blu-ray is several times the data of a DVD, so one blended number would be
         wrong about both.`
      : `Riparr needs two finished rips <b>of the same kind of disc</b> before it can say
         what is normal. Until then the queue counts up rather than down.`}
      MakeMKV cannot report progress during the disc scan at all — the reads go through
      <code>/dev/sg0</code>, where neither the file accounting nor the block layer can see
      them — so what this machine did last time is the only honest estimate there is.</p>`;
}

function when(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

/* A date a person would say out loud: no seconds, no am/pm on something months old. */
function day(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleDateString([],
    { weekday: "short", month: "short", day: "numeric", year: "numeric" });
}

/* ── Discs: the shelf ──
   Every disc this box has seen, and the one page in the product with something worth
   looking at. The record was already here; the artwork was being fetched for the tray
   and thrown away the moment the disc came out. */
views.discs = async (highlight) => {
  const { discs } = await api.get("/api/discs");
  const hit = highlight ? discs.find(d => d.fingerprint === highlight) : null;
  if (!discs.length) {
    return `${head("Discs", "Every disc Riparr has seen. Reinsert one and it is refused rather than re-ripped.")}
      <div class="card"><div class="empty-state"><div class="big">${icon("compact-disc")}</div>
        <h2>No discs recorded</h2>
        <p>Once Riparr rips a disc it remembers it, so reinserting it is refused
           instead of costing you forty minutes — and <b>Re-rip</b> is here for when
           you meant it.</p></div></div>`;
  }
  const card = (d) => {
    const name = d.title || pretty(d.label) || "Unknown disc";
    const me = highlight && d.fingerprint === highlight;
    return `<figure class="rip${me ? " dupe" : ""}" id="${me ? "dupe-tile" : ""}"
                    data-art="${esc(d.title || d.label || "")}">
      <div class="rip-art">
        <span class="rip-fallback">${icon("compact-disc")}</span>
        ${familyTag(d.disc_family, "on-art")}
        ${!d.ripped_at ? `<span class="rip-flag" title="Seen, but never finished a verified rip">${
          icon("triangle-exclamation")}</span>` : ""}
      </div>
      <figcaption>
        <div class="rip-title" title="${esc(name)}">${esc(name)}</div>
        <div class="rip-meta">${d.ripped_at ? esc(ago(d.ripped_at))
                                            : `<span class="bad">never finished</span>`}</div>
      </figcaption>
      <div class="rip-acts">
        <button class="btn tiny" data-rerip="${esc(d.fingerprint)}"
                title="Put this disc back in the tray and read it again from the start.">Re-rip</button>
        <button class="btn tiny" data-forget="${esc(d.fingerprint)}"
                title="Forget this disc, so the next time it goes in it is treated as new.">Forget</button>
      </div>
    </figure>`;
  };
  // The banner is the sentence the box would say out loud. It names the film and the
  // date, because "you already have this" is only convincing with evidence, and it
  // points at the button rather than describing a menu path.
  const banner = hit ? `
    <div class="card dupe-note">
      <div class="dupe-ic">${icon("compact-disc")}</div>
      <div class="grow">
        <h2>You've already ripped ${esc(hit.title || pretty(hit.label) || "this disc")}</h2>
        <p>It went into your library ${esc(ago(hit.ripped_at))}${
          // The exact date earns its place only once "3 days ago" stops being an
          // answer. Next to "2 min ago" it is just a timestamp with seconds in it.
          hit.ripped_at && Date.now() / 1000 - hit.ripped_at > 86400
            ? ` — ${esc(day(hit.ripped_at))}` : ""}, so Riparr gave the disc straight
          back rather than spending another half hour on it.</p>
        <p class="muted">Meant it? <b>Re-rip</b> on the highlighted tile pulls the tray
          back in and starts over.</p>
      </div>
      <button class="icon-btn" id="dupe-dismiss" title="Dismiss">${icon("xmark")}</button>
    </div>` : "";
  return `${head("Discs", "Every disc Riparr has seen. Reinsert one and it is refused rather than re-ripped.")}
    ${banner}
    <div class="rips">${discs.map(card).join("")}</div>`;
};

/* The same tidy-up the server does to a volume label, so ALL_CAPS_1999 does not sit
   under a poster shouting. */
function pretty(label) {
  if (!label) return "";
  return label.replace(/[_.]+/g, " ").replace(/\s+/g, " ").trim()
              .replace(/\b\w/g, c => c.toUpperCase());
}

/* Fill the posters in after the grid is on screen. Each lookup is cached server-side by
   normalised title, and a miss simply leaves the disc icon showing -- a film we cannot
   identify is a tile without a picture, never a broken image. */
async function paintRipArt() {
  const cards = $$(".rip[data-art]");
  for (const el of cards) {
    const label = el.dataset.art;
    if (!label || el.dataset.done) continue;
    el.dataset.done = "1";
    let hit;
    try { hit = await api.get(`/api/artwork?label=${encodeURIComponent(label)}`); }
    catch (e) { continue; }
    if (!hit || !hit.ok) continue;
    const art = el.querySelector(".rip-art");
    if (art) {
      art.style.backgroundImage = `url("${hit.image}")`;
      art.classList.add("has");
      art.title = hit.title;
    }
  }
}

/* ── settings ── */
/* Five pages, not Sonarr's twenty. Riparr has no indexers, no download clients, no
   quality profiles and no custom formats, so the settings surface stays something a
   person can read in one sitting.

   Each page says what it is for rather than repeating a slogan. The five subtitles
   used to be one line -- "Configure once. Anything that needs revisiting is a bug." --
   which told somebody who had just opened Connect for the first time nothing at all
   about what Connect was. A subtitle is the only sentence guaranteed to be read on a
   settings page, so it is worth spending on the page rather than on the product. */
const SETTINGS_TABS = [
  ["library", "Library",
   "Where finished rips are written, what they are called, and which share each kind "
   + "of disc goes to."],
  ["ripping", "Ripping",
   "What Riparr takes off a disc, how it gets to your library, and how thoroughly it "
   + "is checked afterwards."],
  ["connect", "Connect",
   "How the box reaches you when you are not looking at this page, and where finished "
   + "files are handed on."],
  ["network", "Network",
   "Which Wi-Fi networks this box will join, and in what order it tries them."],
  ["general", "General",
   "MakeMKV, the look of this interface, your password, and updates."],
];

views.settings = async (sub = "library") => {
  const s = state.settings = await api.get("/api/settings");
  const body = await (settingsPages[sub] || settingsPages.library)(s);
  const tab = SETTINGS_TABS.find(([k]) => k === sub) || SETTINGS_TABS[0];
  return `${head(tab[1], tab[2])}${body}`;
};

const settingsPages = {};

/* Library — where finished rips go and what they are called. */
settingsPages.library = async (s) => {
  const { shares } = await api.get("/api/shares");
  return `
    <div class="section"><h2>Share
      <span class="grow"></span>
      <button class="btn" id="add-share">Add a share</button></h2>
      <div>
      ${shares.length ? shares.map(sh => `
        <div class="rowitem">
          <div class="grow">
            <div class="t">${esc(sh.name)} ${sh.is_default ? '<span class="badge ok">default</span>' : ""}</div>
            <div class="s">//${esc(sh.host)}/${esc(sh.path)} · last verified ${ago(sh.verified_at)}</div>
          </div>
          <button class="btn danger" data-del-share="${sh.id}">Remove</button>
        </div>`).join("")
      : `<div class="empty-state"><div class="big">${icon("hard-drive")}</div><h2>No share configured</h2>
          <p>Finished rips have nowhere to go until you add one.</p></div>`}
      <div id="share-add"></div>
    </div></div>

    <div class="section"><h2>Folders</h2><div>
      <div class="grid2">
        <label class="f"><span>Movies</span>
          <input data-set="movie_folder" value="${esc(s.movie_folder)}"></label>
        <label class="f"><span>TV</span>
          <input data-set="tv_folder" value="${esc(s.tv_folder)}"></label>
      </div>
    </div></div>

    <div class="section"><h2>Naming</h2><div>
      <label class="f"><span>Movie file name</span>
        <input data-set="movie_template" value="${esc(s.movie_template)}"></label>
      <label class="f"><span>Episode file name</span>
        <input data-set="tv_template" value="${esc(s.tv_template)}"></label>
      <label class="f"><span>When a disc can't be identified</span>
        <select data-set="on_unknown_disc">
          ${opt("ask", "Ask me (recommended)", s.on_unknown_disc)}
          ${opt("label", "Use the disc label", s.on_unknown_disc)}
          ${opt("skip", "Skip it", s.on_unknown_disc)}
        </select>
        <span class="help">A wrongly named file quietly pollutes your library, which is
          worse than one waiting ten seconds for your attention.</span></label>
    </div></div>${saveBar()}`;
};

/* Ripping — what comes off the disc, and how it gets out. */
settingsPages.ripping = (s) => `
  <div class="section"><h2>What to rip</h2><div>
    <label class="f"><span>Titles</span>
      <select data-set="rip_mode">
        ${opt("main", "Main title (default)", s.rip_mode)}
        ${opt("all", "All titles", s.rip_mode)}
        ${opt("backup", "Full disc backup", s.rip_mode)}
      </select></label>
    <label class="f"><span>Minimum title length (seconds)</span>
      <input type="number" data-set="min_title_seconds" value="${s.min_title_seconds}">
      <span class="help">Filters menus and logo stings.</span></label>
  </div></div>

  <div class="section"><h2>Tracks
    <span class="grow"></span>
    <span class="badge">Biggest effect on file size</span></h2><div>
    <label class="f"><span>Audio languages</span>
      <input data-set="audio_languages" data-list value="${esc((s.audio_languages || []).join(", "))}">
      <span class="help">Comma separated ISO codes, e.g. eng, fra.</span></label>
    <label class="f"><span>Subtitle languages</span>
      <input data-set="subtitle_languages" data-list value="${esc((s.subtitle_languages || []).join(", "))}"></label>
    ${sw("keep_forced_subtitles", "Keep forced subtitles", s.keep_forced_subtitles,
        "The subtitles for alien or foreign dialogue. Almost always wanted.")}
    ${sw("keep_commentary", "Keep commentary tracks", s.keep_commentary,
        "Keeping every language and commentary can roughly double file size.")}
  </div></div>

  <div class="section"><h2>Transfer</h2><div>
    <label class="f"><span>Mode</span>
      <select data-set="transfer_mode">
        ${opt("auto", "Automatic (recommended)", s.transfer_mode)}
        ${opt("direct", "Straight to your library — skip the card", s.transfer_mode)}
        ${opt("burst", "Always burst", s.transfer_mode)}
        ${opt("stream", "Always stream", s.transfer_mode)}
      </select>
      <span class="help"><b>Straight to your library</b> writes the film to your share
        as it comes off the disc, so nothing is staged on the card. On the reference
        board that is about <b>18 MB/s against the card's 9.4</b> — so it is roughly
        twice as fast — and it removes the card as a size limit, which is the only
        reason a 22 GB Blu-ray will not fit on a 32 GB card. It also stops writing tens
        of gigabytes per disc through flash that wears out.
        <br><br>The trade: the rip needs the network for its whole length rather than
        only at the end, and there is no second copy, so verification checks the size
        rather than hashing. If your NAS sleeps or your Wi-Fi is patchy, leave this
        off.</span></label>
    <label class="f"><span>Verify after transfer</span>
      <select data-set="verify_mode">
        ${opt("quick", "Quick — check the size", s.verify_mode)}
        ${opt("deep", "Deep — read every byte back", s.verify_mode)}
        ${opt("off", "Don't verify", s.verify_mode)}
      </select>
      <span class="help">Quick asks the share how big the file is and compares it with
        what was sent. It is nearly free and catches what actually goes wrong — a
        truncated transfer, a share that filled up, a write that was refused.
        <b>Deep</b> reads the entire file back and hashes it, so it also catches silent
        corruption of bytes that did arrive. That means downloading the whole rip again:
        it roughly doubles the time after a rip and needs as much free space on the card
        as the film itself. Worth it for an archive you will never re-rip; overkill for
        most.</span></label>
    ${sw("keep_local_copy", "Keep the local copy", s.keep_local_copy,
        "Retains the rip until the space is needed, so a downstream problem is a re-copy rather than a re-rip.")}
  </div></div>

  <div class="section"><h2>Already-ripped discs</h2><div>
    <p class="muted">Put a disc back in that Riparr has already finished and it gives it
      straight back rather than spending another half hour on it. If a browser is open
      it jumps to <b>Discs</b> and points at the film. If nobody is looking at one, this
      is how the box says so.</p>
    <label class="f" style="margin-top:14px"><span>Tell me with</span>
      <select data-set="duplicate_signal">
        ${opt("flash", "The drive's own light", s.duplicate_signal)}
        ${opt("tray", "The tray — open and close it", s.duplicate_signal)}
        ${opt("both", "Both", s.duplicate_signal)}
        ${opt("off", "Nothing — just eject", s.duplicate_signal)}
      </select>
      <span class="help">Nothing can address the light on the front of an optical
        drive — there is no such command, in any standard. What the light reports is
        the drive <i>reading</i>, so Riparr reads the disc in a rhythm: three short
        flashes, three times. It works on any drive and needs no vendor knowledge, and
        it is the gentler of the two. <b>The tray</b> is unmissable across a room and
        is machinery, so it does two cycles and stops.</span></label>
    <div class="btn-row">
      <button class="btn" data-signal-test="flash">Try the light</button>
      <button class="btn" data-signal-test="tray">Try the tray</button>
      <span class="test-out" id="signal-out"></span></div>
    <p class="help">Put a disc in first — the light is blinked by reading one. Riparr
      cannot see the result, so this is the only way to find out whether your drive
      blinks the way you would want: watch it.</p>
  </div></div>${saveBar()}`;

/* Connect — how Riparr reaches you, and how finished files reach everything else.
   The notification half exists because a box whose entire promise is "walk away" had
   no way to tell you to come back: an LED covers the person walking past it and
   nothing covered the person at work.

   Four channels, each with its own setup story of six to ten steps in somebody else's
   application. Laid out flat, that is a page you scroll through four times to find the
   one you want, and every reader pays the cost of the three they will never use. So
   each channel is a row wearing its own mark, and opening one is what asks for the
   instructions. The mark matters more than it looks: people recognise Discord's face
   long before they read the word, and a channel that is already working says so on the
   mark itself rather than in a word at the other end of the row. */
const CHANNELS = [
  {key: "ntfy", name: "ntfy", icon: "ntfy",
   blurb: "Push straight to your phone. No account, no signup — the least work of the four."},
  {key: "discord", name: "Discord", icon: "discord",
   blurb: "Posts into a channel, and can @-mention you so your phone actually buzzes."},
  {key: "email", name: "Email", icon: "envelope",
   blurb: "Any SMTP server: your provider's, your NAS's, or your own."},
  {key: "webhook", name: "Webhook", icon: "circle-nodes",
   blurb: "POSTs JSON to anything that speaks HTTP — Home Assistant, n8n, a script of your own."},
];

/* One channel: the row you click, and the panel it opens.

   `summary` is what this channel is actually pointed at — the topic, the address, the
   host. A row that says only "set up" is a row you have to open to check, and the one
   thing somebody comes back to this page for is *which* account they wired it to. */
function channelRow(c, on, summary, body) {
  return `
  <div class="chan ${on ? "on" : ""}" data-chan="${c.key}">
    <button class="chan-head" type="button" aria-expanded="false" aria-controls="chan-${c.key}">
      <span class="chan-ic chan-${c.key}">${icon(c.icon)}${
        on ? `<span class="chan-tick" title="Set up">${icon("circle-check")}</span>` : ""}</span>
      <span class="chan-txt">
        <span class="chan-name">${esc(c.name)}</span>
        <span class="chan-blurb">${esc(on && summary ? summary : c.blurb)}</span>
      </span>
      <span class="chan-state">${on ? "Set up" : "Not set up"}</span>
      <span class="chan-caret">${icon("chevron-down")}</span>
    </button>
    <div class="chan-body" id="chan-${c.key}" hidden>${body}</div>
  </div>`;
}

settingsPages.connect = async (s) => {
  const n = await api.get("/api/notifications");
  const on = new Set(n.enabled || []);
  const ch = n.configured || {};
  const live = Object.values(ch).filter(Boolean).length;

  const summary = {
    ntfy: s.ntfy_topic
      ? `${(s.ntfy_server || "https://ntfy.sh").replace(/^https?:\/\//, "").replace(/\/$/, "")}/${s.ntfy_topic}`
      : "",
    discord: s.discord_webhook
      ? (s.discord_mention ? "A channel webhook, mentioning you" : "A channel webhook, posting quietly")
      : "",
    email: s.smtp_to ? `To ${s.smtp_to} via ${s.smtp_host}` : "",
    webhook: s.webhook_url ? s.webhook_url.replace(/^https?:\/\//, "").slice(0, 60) : "",
  };

  const bodies = {};

  bodies.ntfy = `
    <ol class="steps">
      <li><b>Install ntfy.</b> It is free and on both app stores, or you can leave
        <a href="https://ntfy.sh/app" target="_blank" rel="noopener">ntfy.sh/app</a>
        open in a browser tab.</li>
      <li><b>Invent a topic.</b> A topic is just a name, and there is no password on
        one — anyone who guesses it reads your notifications. So make it
        <i>unguessable</i> rather than memorable: <code>riparr-3f9a2b7c</code>, not
        <code>riparr</code>.</li>
      <li><b>Subscribe to it</b> in the app: <b>+</b> → type the same topic → Subscribe.</li>
      <li><b>Paste it below</b> and send a test. The test arrives on your phone or it
        does not, which is the whole of the answer.</li>
    </ol>
    <label class="f"><span>Topic</span>
      <input data-set="ntfy_topic" value="${esc(s.ntfy_topic || "")}" placeholder="riparr-3f9a2b7c">
      <span class="help">Must match what you subscribed to in the app, exactly.</span></label>
    <label class="f"><span>Server</span>
      <input data-set="ntfy_server" value="${esc(s.ntfy_server || "")}">
      <span class="help">Leave this alone unless you run your own ntfy — a self-hosted
        one on your NAS works and never leaves your network.</span></label>
    <label class="f"><span>Access token</span>
      <input data-set="ntfy_token" type="password" value="${esc(s.ntfy_token || "")}"
             placeholder="only for a private server">
      <span class="help">Public ntfy.sh topics need no token. A private server that
        requires sign-in does.</span></label>
    ${testRow("ntfy")}`;

  bodies.discord = `
    <p class="muted">A Discord webhook posts into a <b>channel</b>. If you want the box
      to tell <i>you</i> — a notification on your phone rather than a line in a channel
      somebody might read on Tuesday — make a server of one and have Riparr mention you
      in it. Both halves are below.</p>
    <ol class="steps">
      <li><b>Make somewhere for it to post.</b> In Discord, click <b>+</b> at the bottom
        of the server list → <b>Create My Own</b> → <b>For me and my friends</b>. Call it
        anything. Nobody else can see it. A channel there is a private feed, and Discord
        pushes it to your phone like any other.</li>
      <li><b>Make the webhook.</b> Hover the channel → the gear (<b>Edit Channel</b>) →
        <b>Integrations</b> → <b>Create Webhook</b> → <b>Copy Webhook URL</b>. That URL
        is the password: anyone holding it can post as Riparr, so treat it like one.</li>
      <li><b>Paste it below</b> and press Check. Riparr asks Discord whether the webhook
        is real before trusting it — a URL that got truncated on the way through a
        clipboard fails silently forever otherwise.</li>
      <li><b>Optional but the point:</b> turn on <b>Developer Mode</b>
        (User Settings → Advanced), then right-click your own name →
        <b>Copy User ID</b>, and paste that in "Mention me". Riparr will @-mention you,
        which is the thing that actually buzzes a phone.</li>
    </ol>
    <label class="f"><span>Webhook URL</span>
      <input data-set="discord_webhook" id="dc-url" value="${esc(s.discord_webhook || "")}"
             placeholder="https://discord.com/api/webhooks/…">
      <span class="help">Nothing is sent anywhere until this is filled in.</span></label>
    <div class="btn-row"><button class="btn" id="dc-check">Check this webhook</button>
      <span class="test-out" id="dc-out"></span></div>

    <label class="f"><span>Mention me</span>
      <input data-set="discord_mention" value="${esc(s.discord_mention || "")}"
             placeholder="your Discord user ID, e.g. 218411284957167616">
      <span class="help">A user ID pings you. Prefix a <b>role</b> ID with
        <code>&amp;</code> — <code>&amp;123…</code> — to ping a role instead, for a
        household that shares the box. Leave empty to post quietly.</span></label>

    <div class="dc-when">
      <div class="dc-when-l">Ping me for</div>
      <div class="notify-events">
        ${n.events.map(e => `
          <label class="switch"><input type="checkbox" data-set="discord_mention_events"
                  data-multi value="${esc(e.key)}"
                  ${(s.discord_mention_events || []).includes(e.key) ? "checked" : ""}>
            <span class="track"></span><span class="lbl">${esc(e.label)}</span></label>`).join("")}
      </div>
      <p class="help">Everything else still posts to the channel — it just does not
        make your phone light up. "A rip finished" is off by default for exactly that
        reason: it is good news, and good news can wait.</p>
    </div>
    ${testRow("discord")}`;

  bodies.email = `
    <ol class="steps">
      <li><b>Find your provider's outgoing (SMTP) server.</b> Gmail is
        <code>smtp.gmail.com</code>, Outlook <code>smtp.office365.com</code>, Fastmail
        <code>smtp.fastmail.com</code>. Your NAS almost certainly has one too.</li>
      <li><b>Make an app password.</b> Any account with two-factor turned on — which is
        all of them now — will reject your ordinary password here and give no useful
        reason. Google calls it an <i>App password</i>; most others use the same words.
        Use that, not the password you sign in with.</li>
      <li><b>Port 587 with STARTTLS</b> is the usual pairing. If your provider says port
        <b>465</b>, use it and turn STARTTLS <i>off</i>: 465 is encrypted from the first
        byte, and asking it to start again fails.</li>
      <li><b>From</b> normally has to be the same address you signed in as. Providers
        refuse to send mail claiming to be somebody else.</li>
    </ol>
    <div class="grid2">
      <label class="f"><span>SMTP server</span>
        <input data-set="smtp_host" value="${esc(s.smtp_host || "")}" placeholder="smtp.gmail.com"></label>
      <label class="f"><span>Port</span>
        <input data-set="smtp_port" type="number" value="${esc(String(s.smtp_port ?? 587))}"></label>
      <label class="f"><span>Username</span>
        <input data-set="smtp_username" value="${esc(s.smtp_username || "")}"></label>
      <label class="f"><span>Password</span>
        <input data-set="smtp_password" type="password" value="${esc(s.smtp_password || "")}"></label>
      <label class="f"><span>From</span>
        <input data-set="smtp_from" value="${esc(s.smtp_from || "")}" placeholder="riparr@example.com"></label>
      <label class="f"><span>To</span>
        <input data-set="smtp_to" value="${esc(s.smtp_to || "")}" placeholder="you@example.com"></label>
    </div>
    ${sw("smtp_tls", "Use STARTTLS", s.smtp_tls, "Leave on unless the port is 465, which is TLS from the start.")}
    ${testRow("email")}`;

  bodies.webhook = `
    <p class="muted">The general-purpose escape hatch. Every event is POSTed as JSON to
      one URL, so anything that can receive an HTTP request can act on it — a Home
      Assistant automation, an n8n flow, a shell script behind a tiny listener.</p>
    <ol class="steps">
      <li><b>Get a URL that accepts a POST.</b> In Home Assistant that is a webhook
        trigger; in n8n, a Webhook node; anywhere else, whatever you already use.</li>
      <li><b>Paste it below and send a test.</b> The body looks like this:
        <code class="block">{"event":"done","title":"Arthur Christmas",
"body":"Ripped and verified","hostname":"riparr"}</code></li>
      <li><b>Events are the ones ticked at the top of this page.</b> <code>event</code>
        is one of <code>${n.events.map(e => e.key).join("</code>, <code>")}</code>.</li>
    </ol>
    <label class="f"><span>URL</span>
      <input data-set="webhook_url" value="${esc(s.webhook_url)}" placeholder="https://…">
      <span class="help">Must be <code>http://</code> or <code>https://</code>. A
        service on your own network is fine and is the common case.</span></label>
    ${testRow("webhook")}`;

  return `
  <div class="section"><h2>Tell me when</h2><div>
    <p class="muted">Riparr sends every event ticked here to every channel set up
      below. Until at least one channel is configured, nothing is sent anywhere and
      these have no effect.</p>
    <div class="notify-events">
      ${n.events.map(e => `
        <label class="switch"><input type="checkbox" data-set="notify_events" data-multi
                value="${esc(e.key)}" ${on.has(e.key) ? "checked" : ""}>
          <span class="track"></span><span class="lbl">${esc(e.label)}</span></label>`).join("")}
    </div>
  </div></div>

  <div class="section"><h2>Channels
    <span class="grow"></span>
    <span class="badge ${live ? "ok" : "warn"}">${
      live ? `${live} of ${CHANNELS.length} set up` : "none set up"}</span></h2><div>
    <p class="muted">Pick whichever you already use — one is enough, and setting up two
      is only worth it if you want a copy somewhere permanent. Open a channel to see
      what it needs. ${live ? "A tick on the mark means Riparr has what it needs to "
        + "send; the test button is how you find out whether it arrives."
      : "Nothing is set up yet, so the box currently has no way to reach you when you "
        + "are not on this page."}</p>
    <div class="channels">
      ${CHANNELS.map(c => channelRow(c, !!ch[c.key], summary[c.key], bodies[c.key])).join("")}
    </div>
  </div></div>

  <div class="section"><h2>Handoff</h2><div>
    <p class="muted">Not a notification: this is where finished files go <i>next</i>.
      Riparr does not transcode — a board this size would take days and the result
      would be poor — so if you run something that does, write the rip where it is
      watching for work instead of straight into your library.</p>
    <label class="f" style="margin-top:14px"><span>Watch folder</span>
      <input data-set="watch_folder" value="${esc(s.watch_folder)}" placeholder="/Media/_incoming">
      <span class="help">A path on your library share. Tdarr and Unmanic both work this
        way: they pick the file up, transcode it, and put the result wherever they are
        configured to. Leave this empty to write straight to the folders on the
        <a href="#/settings/library">Library</a> page.</span></label>
  </div></div>${saveBar()}`;
};

/* ── is MakeMKV's own infrastructure up? ──
   Not decoration. makemkv.com has been down for weeks, and the free key is published
   on the *forum*, which is a different host and is usually fine. "The site is down but
   the forum is up" is the difference between "you are stuck" and "go here, copy the
   key, paste it above" -- so the two are tracked separately and each says what its
   being down actually costs the person reading.

   The panel draws before the answer exists. It used to draw *after*, which meant
   opening General waited on two of somebody else's web servers -- and a host that is
   down burns the whole timeout, so the sidebar link looked broken for ten seconds and
   people clicked it again. Now the page appears immediately, this says "checking", and
   the answer arrives when it arrives. */
function sitesPanel(mk) {
  return `
  <div class="section" id="sites-panel"><h2>MakeMKV's website
    <span class="grow"></span>
    ${sitesBadge(mk.sites, mk.sites_checking)}</h2><div>
    <p class="muted">Riparr checks these because they are how MakeMKV gets installed and
      how its free key gets renewed. Neither affects a copy that is already working.</p>
    <div id="sites-inner">${sitesBody(mk.sites, mk.sites_checking, mk.key_topic)}</div>
    <div class="btn-row"><button class="btn" id="sites-recheck">Check again</button>
      <span class="test-out" id="sites-out"></span></div>
  </div></div>`;
}

function sitesBadge(sites, checking) {
  if (!sites || !sites.length)
    return `<span class="badge" id="sites-badge">${checking ? "checking…" : "not checked"}</span>`;
  const down = sites.filter(x => !x.up).length;
  return `<span class="badge ${down ? "warn" : "ok"}" id="sites-badge">${
    down === 0 ? "both reachable"
    : down === sites.length ? "both unreachable"
    : `${down} unreachable`}</span>`;
}

function sitesBody(sites, checking, keyTopic) {
  if (!sites || !sites.length)
    return `<div class="sites-wait">${checking
      ? `<span class="spin"></span>Asking makemkv.com and its forum whether they are
         answering. A host that is down takes a few seconds to admit it.`
      : `Nothing checked yet.`}</div>`;
  return `
    <div class="sites">
      ${sites.map(x => `
        <div class="site ${x.up ? "up" : "down"}">
          <span class="site-dot">${icon(x.up ? "circle-check" : "circle-exclamation")}</span>
          <div class="grow">
            <div class="site-name"><a href="${esc(x.url)}" target="_blank" rel="noopener">${
              esc(x.name)}</a>
              <span class="site-state">${x.up
                ? (x.note ? esc(x.note) : `answering in ${x.ms} ms`)
                : "not answering"}</span></div>
            <div class="site-why">${esc(x.why)}</div>
            ${!x.up && x.key === "site" ? `
              <div class="site-do">${icon("circle-info")} <span>An installed MakeMKV
                keeps working — this only stops Riparr <b>installing or updating</b> it.
                If you need a key, the forum below is a separate machine and is usually
                still up.</span></div>` : ""}
            ${!x.up && x.key === "forum" ? `
              <div class="site-do">${icon("circle-info")} <span>This is where the free
                key lives. If it is down and your key has lapsed, Blu-ray decryption
                will stop until it comes back. DVDs are unaffected.</span></div>` : ""}
          </div>
          ${x.key === "forum" && x.up ? `<a class="btn" href="${esc(keyTopic)}"
             target="_blank" rel="noopener">Get the current key</a>` : ""}
        </div>`).join("")}
    </div>`;
}

/* Replace the panel's contents in place rather than re-rendering the page. General
   holds a key field and a password field; blowing the page away underneath somebody
   who is halfway through typing one is not an acceptable price for a status dot. */
function paintSites(r, keyTopic) {
  const inner = $("#sites-inner"), badge = $("#sites-badge");
  if (!inner) return false;
  inner.innerHTML = sitesBody(r.sites, r.checking, keyTopic);
  if (badge) badge.outerHTML = sitesBadge(r.sites, r.checking);
  return true;
}

/* Poll until the probe finishes. Bounded: after a minute something is wrong with the
   probe itself, and a page that polls forever is a page that keeps a dead box busy. */
async function followSites(keyTopic) {
  for (let i = 0; i < 20; i++) {
    await new Promise(r => setTimeout(r, 1500));
    if (!$("#sites-inner")) return;            // navigated away
    let r;
    try { r = await api.get("/api/makemkv/sites"); } catch (e) { return; }
    if (!paintSites(r, keyTopic)) return;
    if (!r.checking) return;
  }
}

/* Save first, then send. A test button that tests the values already stored rather
   than the ones on screen answers a question nobody asked. */
function testRow(channel) {
  return `<div class="btn-row"><button class="btn" data-test-notify="${channel}">
    Save and send a test</button><span class="test-out" id="test-${channel}"></span></div>`;
}

settingsPages.network = async () => {
  const w = await api.get("/api/wifi");
  return `
    <div class="section"><h2>Connection
      <span class="grow"></span>
      <span class="badge ${w.connected ? "ok" : "bad"}">${w.connected ? "Connected" : "Offline"}</span></h2>
      <div><div class="kv">
        <div class="k">Network</div><div class="v">${esc(w.ssid || "—")}</div>
        <div class="k">Signal</div><div class="v">${
          w.signal == null ? "—"
            : `${signalBars(w.signal)} ${w.signal}%${
                w.signal_dbm != null ? ` <span class="muted">(${w.signal_dbm} dBm)</span>` : ""}`}</div>
        <div class="k">Band</div><div class="v">${
          w.band ? `${esc(w.band)} GHz${w.freq_mhz ? ` <span class="muted">· ${w.freq_mhz} MHz</span>` : ""}` : "—"}</div>
        <div class="k">Link rate</div><div class="v">${
          w.bitrate_mbps ? `${w.bitrate_mbps} Mbit/s` : "—"}</div>
        <div class="k">Address</div><div class="v">${esc(w.ip || "—")}${
          w.iface ? ` <span class="muted">· ${esc(w.iface)}</span>` : ""}</div>
      </div></div>
    </div>
    <div class="section"><h2>Join a different network
      <span class="grow"></span>
      <button class="btn" id="wifi-scan">Scan</button></h2>
      <div id="wifi-results">
        <p class="muted">Wi-Fi is configured when the card is written. Changing it here
          needs the box to be reachable, so it can only move to a network it can
          already see.</p>
      </div>
    </div>`;
};

settingsPages.general = async (s) => {
  const mk = await api.get("/api/makemkv");
  const st = mk.status;
  state.mkKeyTopic = mk.key_topic;
  const expiringSoon = st.days_left != null && st.days_left < 8;
  const themes = ["servarr", "organizr", "dark", "nord", "dracula", "plex",
                  "space-gray", "aquamarine", "hotline", "hotpink", "maroon", "overseerr"];
  return `
    <div class="section"><h2>MakeMKV
      <span class="grow"></span>
      <span class="badge ${!st.installed ? "bad" : expiringSoon ? "warn" : "ok"}">${
        !st.installed ? "Not installed"
        : st.days_left != null ? `${st.days_left} days left` : "Installed"}</span></h2>
      <div>
      ${st.installed ? "" : `
        <p class="muted" style="margin-bottom:10px">MakeMKV is made by GuinpinSoft. Its
          licence is between you and them.
          <a href="${esc(mk.eula_url)}" target="_blank" rel="noopener">Read it</a>.
          Riparr installs version <b>${esc(mk.manifest.version)}</b>, built on this
          device — the build is the slow part, around half an hour.</p>
        ${mk.local_source ? `
        <p class="muted" style="margin-bottom:10px">${icon("circle-check", "ok")}
          A copy is already on this device, at <code>${esc(mk.local_source)}</code>.
          Nothing will be downloaded.</p>`
        : `
        <p class="muted" style="margin-bottom:10px">Downloaded from
          <b>${(mk.manifest.sources || []).map(esc).join("</b>, <b>")}</b> — in that
          order, until one works. makemkv.com goes down for weeks at a time, which is
          why there is more than one. Every download is checked against a checksum
          pinned in Riparr's own source, so a mirror can only give Riparr the right
          file or none at all.</p>`}
        <label class="switch"><input type="checkbox" id="mk-accept"><span class="track"></span>
          <span class="lbl">I have read and accept MakeMKV's licence agreement</span></label>
        <div class="btn-row"><button class="btn primary" id="mk-install" disabled>
          Download and install</button></div>
        <div id="mk-progress"></div>`}
      <label class="f" style="margin-top:${st.installed ? 0 : 16}px"><span>Key</span>
        <input data-set="makemkv_key" id="mk-key-input" value="${esc(s.makemkv_key)}" placeholder="Beta or purchased key">
        <span class="help">MakeMKV is free while it is in beta, behind a key GuinpinSoft
          publishes on the forum. ${esc((mk.key_advice || {}).note || "")}</span></label>
      <div class="f"><span></span><div class="grow" id="mk-key-offer"></div></div>
    </div></div>

    ${sitesPanel(mk)}

    <div class="section"><h2>Appearance</h2><div>
      <p class="muted">Riparr uses the theme.park variable set, so a theme you already run
        on your *arr stack applies here too.</p>
      <label class="f" style="margin-top:14px"><span>Theme</span>
        <select id="theme-pick">${themes.map(t =>
          `<option value="${t}" ${s.theme === t ? "selected" : ""}>${t}</option>`).join("")}</select>
      </label>
    </div></div>

    <div class="section"><h2>Password</h2><div>
      <label class="f"><span>Current password</span><input type="password" id="pw-cur"></label>
      <label class="f"><span>New password</span><input type="password" id="pw-new"></label>
      <label class="f"><span>Confirm new password</span><input type="password" id="pw-new2"></label>
      <div id="pw-res"></div>
      <div class="btn-row"><button class="btn" id="pw-go">Change password</button></div>
    </div></div>

    <div class="section"><h2>Updates</h2><div>
      ${sw("auto_check_updates", "Check for updates automatically", s.auto_check_updates,
          "Checks the official repository once a day. Nothing installs without you asking.")}
      <div class="btn-row"><a class="btn" href="#/system/updates">Open updates</a></div>
    </div></div>${saveBar()}`;
};

/* ── system ──────────────────────────────────────────────────────────────────
   Six pages in Prowlarr's order. The shape is copied deliberately: full-width
   sections stacked down the page, a 21px heading with a rule under it, a toolbar of
   icon-over-label buttons where a page has actions, and tables at 14px with bold
   sentence-case headers. The *arrs put nothing side by side here and neither do we. */
/* Mirrors `led.STATES` and docs/guide/led-reference.md. A printed card cannot import
   a constant, so the next best thing is that all three use the same names. */
const LED_WORDS = {
  booting: "White, pulsing slowly — booting or waiting for setup",
  joining: "Blue, blinking — joining Wi-Fi",
  ready: "Solid green — ready for a disc",
  ripping: "Blue, breathing — ripping",
  uploading: "Amber, pulsing — uploading to your library",
  verifying: "Amber, pulsing — verifying",
  done: "Green flash — done and verified",
  failed: "Solid red — the disc failed",
  duplicate: "Purple — already ripped this one",
  needs_you: "Amber, blinking — waiting for you",
  no_share: "Amber, blinking — can't reach your library",
  no_wifi: "Amber, blinking — no Wi-Fi",
};

const SYSTEM_TABS = [
  ["status",  "Status"],
  ["tasks",   "Tasks"],
  ["backup",  "Backup"],
  ["updates", "Updates"],
  ["events",  "Events"],
  ["logs",    "Log Files"],
];

views.system = async (sub = "status") => {
  const body = await (systemPages[sub] || systemPages.status)();
  const label = (SYSTEM_TABS.find(([k]) => k === sub) || SYSTEM_TABS[0])[1];
  return `${head(`System — ${label}`, "")}${body}`;
};

const systemPages = {};

/* ── Status ── */
systemPages.status = async () => {
  const st = await api.get("/api/status");
  state.status = st;
  const sys = st.system, m = st.makemkv, s = st.storage;

  const health = healthMessages(st);
  const healthRows = health.length
    ? health.map(h => `<tr>
        <td class="stat">${icon(h.level === "bad" ? "circle-exclamation" : "triangle-exclamation",
                                h.level === "bad" ? "bad" : "warn")}</td>
        <td>${h.message}</td>
        <td class="act">${h.href
          ? `<a class="icon-btn" href="${h.href}" title="${esc(h.action || "Fix")}"
               >${icon("gears")}</a>` : ""}</td></tr>`).join("")
    : `<tr><td class="stat">${icon("circle-check", "ok")}</td>
         <td colspan="2">No issues. Everything Riparr can check is working.</td></tr>`;

  return `
    ${sys.mock ? `<div class="alert warn"><b>Development mode.</b>
      This process isn't running on Pi hardware, so system, drive and share readings
      are simulated.</div>` : ""}

    <div class="section"><h2>Health</h2>
      <table><tbody>${healthRows}</tbody></table>
      <div class="alert">Health checks re-run every six hours, and whenever you open
        this page. You can force one from
        <a href="#/system/tasks">Tasks</a>; anything logged along the way is on
        <a href="#/system/events">Events</a>.</div>
    </div>

    <div class="section"><h2>About</h2>
      <div class="kv">
        <div class="k">Version</div><div class="v">${esc(st.version)}</div>
        <div class="k">Model</div><div class="v">${esc(sys.model)}</div>
        ${sys.board ? `<div class="k">Prepared as</div><div class="v">${esc(sys.board)}</div>` : ""}
        <div class="k">Operating system</div><div class="v">${esc(sys.os)}</div>
        <div class="k">Kernel</div><div class="v">${esc(sys.kernel || "—")}</div>
        <div class="k">Mode</div><div class="v">${sys.mock ? "Development (simulated)" : "Appliance"}</div>
        <div class="k">Memory</div><div class="v">${sys.memory_used_mb} of ${sys.memory_total_mb} MB</div>
        <div class="k">Temperature</div><div class="v">${sys.cpu_temp_c ?? "—"} °C
          ${sys.throttled ? '<span class="badge warn">throttled</span>' : ""}</div>
        <div class="k">Uptime</div><div class="v">${uptime(sys.uptime_seconds)}</div>
      </div>
    </div>

    <div class="section"><h2>Storage</h2>
      <div class="kv">
        <div class="k">Capacity</div><div class="v">${capacityPhrase(s)}</div>
        <div class="k">Staging path</div><div class="v">${esc(s.path || "—")}
          ${s.dedicated === false ? '<span class="badge bad">shared with root</span>' : ""}</div>
        <div class="k">Used</div><div class="v">${filesize(s.used_bytes)}
          of ${filesize(s.total_bytes)}</div>
      </div>
      <div class="bar" style="margin:4px 0 8px"><i style="width:${pct(s.used_bytes, s.total_bytes)}%"></i></div>
      <p class="muted" style="font-size:13px">Buffer, not permanent storage — files
        leave as they're written.</p>
      ${s.dedicated === false ? `<div class="alert bad"><b>No staging partition.</b>
        Rips are sharing the system filesystem, so a stalled upload queue could fill the
        root filesystem and take the box down.</div>` : ""}
    </div>

    <div class="section"><h2>Disc reading<span class="grow"></span>
      <span class="badge ${m.installed ? "ok" : "bad"}">${m.installed ? "Ready" : "Missing"}</span></h2>
      <div class="kv">
        <div class="k">MakeMKV</div><div class="v">${m.installed ? esc(m.version || "installed") : "not installed"}</div>
        <div class="k">Key</div><div class="v">${m.key_expires
          ? `expires ${esc(m.key_expires)} — ${m.days_left} days`
          : "none"}</div>
        <div class="k">Drive</div><div class="v">${(st.drives && st.drives.length)
          ? st.drives.map(d => `${esc(driveName(d))} <span class="muted">· ${esc(d.reads || "capability unknown")}</span>`).join("<br>")
          : `<span class="muted">${esc((st.optical && st.optical.summary) || "no drive detected")}</span>`}</div>
        <div class="k">4K UHD</div><div class="v">${(() => {
          const d = (st.drives || [])[0];
          if (!d) return `<span class="muted">—</span>`;
          if (!d.reads_bluray) return `<span class="muted">Not applicable — this is a DVD drive</span>`;
          if (d.libredrive === "enabled") return `Yes — MakeMKV reports LibreDrive is active`;
          if (d.libredrive === "no") return `<span class="muted">No — MakeMKV can't get underneath this drive's firmware</span>`;
          if (d.uhd === "yes") return `Expected to work — this drive is on Riparr's list`;
          if (d.uhd === "firmware") return `<span class="muted">Depends on firmware — check MakeMKV's LibreDrive list</span>`;
          return `<span class="muted">Unconfirmed — 4K needs a specific drive, and this one isn't on Riparr's list</span>`;
        })()}</div>
      </div>
    </div>

    <div class="section"><h2>Status LED<span class="grow"></span>
      <button class="btn sm" id="led-test">Test the LED</button></h2>
      <div class="kv">
        <div class="k">Wiring</div><div class="v">${st.led && st.led.detected
          ? `Detected on <span class="muted">${esc(st.led.device)}</span>`
          : `<span class="muted">Not detected. Riparr works without one — the web
             interface is then the only place status appears. SPI has to be enabled in
             the device tree before ${esc((st.led && st.led.device) || "the device node")}
             exists.</span>`}</div>
        <div class="k">Showing</div><div class="v">${st.led
          ? `${esc(LED_WORDS[st.led.state] || st.led.state)}` : "—"}</div>
      </div>
      <div class="test-out" id="led-out"></div>
    </div>

    <div class="section"><h2>Network</h2>
      <div class="kv">
        <div class="k">Wi-Fi</div><div class="v">${st.wifi.ssid
          ? `${esc(st.wifi.ssid)}${st.wifi.band ? ` <span class="muted">· ${esc(st.wifi.band)} GHz</span>` : ""}`
          : "offline"}</div>
        <div class="k">Signal</div><div class="v">${st.wifi.signal == null ? "—"
          : `${signalBars(st.wifi.signal)} ${st.wifi.signal}%`}</div>
        <div class="k">Address</div><div class="v">${esc(st.wifi.ip || "—")}</div>
        <div class="k">Hostname</div><div class="v">${esc(st.hostname)}.local</div>
        <div class="k">Share</div><div class="v">${st.share
          ? `//${esc(st.share.host)}/${esc(st.share.path)}` : "not configured"}</div>
      </div>
    </div>

    <div class="section"><h2>More Info</h2>
      <div class="kv">
        <div class="k">Source</div><div class="v">
          <a href="https://github.com/jackharvest/riparr" target="_blank" rel="noopener">github.com/jackharvest/riparr</a></div>
        <div class="k">Feature requests</div><div class="v">
          <a href="https://github.com/jackharvest/riparr/issues" target="_blank" rel="noopener">github.com/jackharvest/riparr/issues</a></div>
        <div class="k">MakeMKV</div><div class="v">
          <a href="https://www.makemkv.com/forum/" target="_blank" rel="noopener">makemkv.com/forum</a></div>
      </div>
    </div>`;
};

/* Health is derived here rather than server-side because every message needs a link to
   the screen that fixes it, and only the client knows the routes. */
function healthMessages(st) {
  const out = [];

  /* The clock goes first, because it invalidates several of the messages below it.
     This board has no RTC and D4 says the power gets pulled, so an unsynchronised
     boot is routine rather than exotic -- and every "N days left" and "3 hours ago"
     in the interface is a subtraction against it. */
  const clk = st.clock;
  if (clk && !clk.plausible)
    out.push({ level: "bad", message: `The system clock reads ${
      new Date(clk.now * 1000).toLocaleString()}, which can't be right. Dates and key
      expiry are meaningless until it syncs — check this box can reach the internet.`,
      href: "#/settings/network", action: "Network settings" });
  else if (clk && clk.synced === false)
    out.push({ level: "warn", message: "The clock hasn't synchronised with a time "
      + "server yet, so dates may be slightly out.",
      href: "#/system/status", action: "Details" });

  const m = st.makemkv;
  if (!m.installed)
    out.push({ level: "bad", message: "MakeMKV is not installed, so no disc can be read.",
               href: "#/settings/makemkv", action: "Install MakeMKV" });
  else if (m.days_left != null && st.clock && !st.clock.plausible)
    out.push({ level: "warn", message: "Riparr can't tell whether the MakeMKV key is "
      + "still valid, because the clock is wrong.",
      href: "#/settings/makemkv", action: "Update the key" });
  else if (m.days_left != null && m.days_left <= 0)
    out.push({ level: "bad", message: "The MakeMKV key has expired. Rips will fail until it is replaced.",
               href: "#/settings/makemkv", action: "Update the key" });
  else if (m.days_left != null && m.days_left <= (state.settings?.warn_key_days ?? 7))
    out.push({ level: "warn", message: `The MakeMKV key expires in ${m.days_left} day(s).`,
               href: "#/settings/makemkv", action: "Update the key" });

  if (!st.drives || !st.drives.length)
    out.push({ level: "bad", message: `No optical drive detected — ${
      esc((st.optical && st.optical.summary) || "nothing is on the USB bus")}.`,
      href: "#/system/status", action: "Details" });

  if (!st.share)
    out.push({ level: "warn", message: "No network share configured, so finished rips have nowhere to go.",
               href: "#/settings/share", action: "Add a share" });

  if (!st.wifi.connected)
    out.push({ level: "bad", message: "Wi-Fi is not connected.",
               href: "#/settings/network", action: "Network settings" });

  if (st.storage.dedicated === false)
    out.push({ level: "warn", message: "Rips are staged on the system filesystem, not a dedicated partition.",
               href: "#/system/status", action: "Details" });

  if (st.storage.mode === "degraded")
    out.push({ level: "warn", message: "Not enough free space to rip safely.",
               href: "#/system/status", action: "Details" });

  return out;
}

/* ── Tasks ── */
systemPages.tasks = async () => {
  const t = await api.get("/api/system/tasks");
  const rows = t.scheduled.map(s => `<tr>
      <td>${esc(s.label)}</td>
      <td>${interval(s.interval)}</td>
      <td>${since(s.last_execution)}</td>
      <td>${s.last_duration == null ? "—" : hms(s.last_duration)}</td>
      <td>${since(s.next_execution)}</td>
      <td class="act"><button class="icon-btn" data-task="${esc(s.name)}"
          title="Run now">${icon("arrows-rotate")}</button></td>
    </tr>`).join("");

  const queue = t.queue.length ? t.queue.map(q => `<tr>
      <td class="stat">${q.error ? icon("circle-exclamation", "bad")
                                 : q.ended_at ? icon("check", "ok") : icon("clock")}</td>
      <td>${esc(q.label)}</td>
      <td>${since(q.queued_at)}</td>
      <td>${since(q.started_at)}</td>
      <td>${since(q.ended_at)}</td>
      <td>${q.ended_at && q.started_at ? hms(q.ended_at - q.started_at) : "—"}</td>
      <td>${q.error ? `<span class="badge bad">${esc(q.error)}</span>` : ""}</td>
    </tr>`).join("")
    : `<tr><td colspan="7" class="muted">Nothing has run yet.</td></tr>`;

  return `
    <div class="section"><h2>Scheduled</h2>
      <table>
        <thead><tr><th>Name</th><th>Interval</th><th>Last Execution</th>
          <th>Last Duration</th><th>Next Execution</th><th class="act"></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="section"><h2>Queue</h2>
      <table>
        <thead><tr><th class="stat"></th><th>Name</th><th>Queued</th><th>Started</th>
          <th>Ended</th><th>Duration</th><th></th></tr></thead>
        <tbody>${queue}</tbody>
      </table>
    </div>`;
};

/* ── Backup ── */
systemPages.backup = async () => {
  const b = await api.get("/api/system/backups");
  const rows = b.backups.length ? b.backups.map(x => `<tr>
      <td class="stat">${icon(x.kind === "scheduled" ? "clock" : "file-zipper")}</td>
      <td><a href="/api/system/backups/${encodeURIComponent(x.name)}">${esc(x.name)}</a></td>
      <td>${filesize(x.size)}</td>
      <td>${stamp(x.modified)}</td>
      <td class="act">
        <button class="icon-btn" data-restore="${esc(x.name)}" title="Restore">${icon("clock-rotate-left")}</button>
        <button class="icon-btn" data-delbackup="${esc(x.name)}" title="Delete">${icon("trash-can")}</button>
      </td></tr>`).join("")
    : `<tr><td colspan="5" class="muted">No backups yet.</td></tr>`;

  return `
    <div class="toolbar">
      <button class="tool" id="bk-now"><span class="ti">${icon("file-zipper")}</span>Backup<br>Now</button>
      <button class="tool" id="bk-upload"><span class="ti">${icon("upload")}</span>Restore<br>Backup</button>
      <input type="file" id="bk-file" accept=".zip,application/zip,.json,application/json" class="hidden">
    </div>
    <div class="alert">Backups are written to <code>${esc(b.path)}</code> and hold your
      settings, shares and disc history — everything that is not re-derivable. A backup
      runs on its own every seven days and the last ${b.keep} are kept.
      <br>Share passwords are deliberately left out, so a restore asks for them again.</div>
    <div class="section"><h2>Backups</h2>
      <table>
        <thead><tr><th class="stat"></th><th>Name</th><th>Size</th><th>Time</th>
          <th class="act"></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
};

/* ── Updates ── */
systemPages.updates = async () => {
  const u = await api.get("/api/update");
  const kind = u.status === "update" ? "warn" : u.status === "current" ? "ok" : "";
  return `
    <div class="toolbar">
      <button class="tool" id="upd-check"><span class="ti">${icon("arrows-rotate")}</span>Check<br>Again</button>
      <button class="tool" id="upd-install" ${u.can_install ? "" : "disabled"}>
        <span class="ti">${icon("download")}</span>Install<br>Latest</button>
    </div>
    <div class="section"><h2>Riparr updates<span class="grow"></span>
      <span class="badge ${kind}">${esc(u.status)}</span></h2>
      <div class="kv">
        <div class="k">Installed</div><div class="v">${esc(u.current)}
          <span class="badge ok">Currently Installed</span></div>
        <div class="k">Latest</div><div class="v">${esc(u.latest || "—")}</div>
        <div class="k">Source</div><div class="v">
          <a href="https://github.com/${esc(u.repo)}" target="_blank" rel="noopener">github.com/${esc(u.repo)}</a></div>
      </div>
      <div class="alert ${u.status === "update" ? "warn" : ""}">${esc(u.message || "")}</div>
      ${!u.can_install && u.status === "update"
        ? `<p class="muted" style="font-size:13px">Updates install on the appliance
           itself. This process is running in development mode.</p>` : ""}
    </div>
    ${u.notes ? `<div class="section"><h2>Release notes</h2>
      <pre class="notes">${esc(u.notes)}</pre></div>` : ""}`;
};

/* ── Events ── */
const EVENT_LEVELS = { info: ["circle-info", ""], warning: ["triangle-exclamation", "warn"],
                       warn: ["triangle-exclamation", "warn"], error: ["circle-exclamation", "bad"],
                       critical: ["circle-exclamation", "bad"], debug: ["circle", "muted"] };

systemPages.events = async () => {
  const e = await api.get("/api/system/events?limit=100");
  const rows = e.events.length ? e.events.map(x => {
    const [ic, cls] = EVENT_LEVELS[x.level] || EVENT_LEVELS.info;
    return `<tr>
      <td class="stat">${icon(ic, cls)}</td>
      <td>${stamp(x.at)}</td>
      <td>${esc(x.component)}</td>
      <td>${esc(x.message)}</td></tr>`;
  }).join("")
    : `<tr><td colspan="4" class="muted">Nothing logged yet.</td></tr>`;

  return `
    <div class="toolbar">
      <button class="tool" id="ev-refresh"><span class="ti">${icon("arrows-rotate")}</span>Refresh</button>
      <button class="tool" id="ev-clear"><span class="ti">${icon("trash-can")}</span>Clear</button>
    </div>
    <div class="section"><h2>Events<span class="grow"></span>
      <span class="muted" style="font-size:13px">${e.total} recorded</span></h2>
      <table>
        <thead><tr><th class="stat"></th><th>Time</th><th>Component</th><th>Message</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
};

/* ── Log Files ── */
systemPages.logs = async () => {
  const l = await api.get("/api/system/logs");
  const rows = l.files.length ? l.files.map(f => `<tr>
      <td>${esc(f.name)}</td>
      <td>${filesize(f.size)}</td>
      <td>${stamp(f.modified)}</td>
      <td class="act"><a href="/api/system/logs/${encodeURIComponent(f.name)}"
        download>Download</a></td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">No log files yet.</td></tr>`;

  return `
    <div class="toolbar">
      <button class="tool" id="lg-refresh"><span class="ti">${icon("arrows-rotate")}</span>Refresh</button>
      <button class="tool" id="lg-delete"><span class="ti">${icon("trash-can")}</span>Delete</button>
    </div>
    <div class="alert">Log files are in <code>${esc(l.path)}</code>.
      <br><code>riparr.txt</code> is the ordinary record; <code>riparr.debug.txt</code>
      keeps everything and is the one to send if you are asking for help. Each is capped
      at 1 MB and rotated five times, because every write is a write to the SD card.</div>
    <div class="section"><h2>Files</h2>
      <table>
        <thead><tr><th>Filename</th><th>Size</th><th>Last Write Time</th>
          <th class="act"></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
};

/* ── shared fragments ── */
/* `acts` is raw markup for the right-hand side -- the *arr page toolbar, moved into
   the header row rather than given a band of its own under it. */
function head(title, sub, acts) {
  return `<div class="page-head"><div><h1>${esc(title)}</h1>
    ${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div>
    ${acts ? `<div class="head-acts">${acts}</div>` : ""}</div>`;
}
function opt(v, label, cur) {
  return `<option value="${v}" ${cur === v ? "selected" : ""}>${esc(label)}</option>`;
}
function sw(key, label, on, help) {
  return `<label class="switch"><input type="checkbox" data-set="${key}" ${on ? "checked" : ""}>
    <span class="track"></span><span class="lbl">${esc(label)}
    ${help ? `<small>${esc(help)}</small>` : ""}</span></label>`;
}
function saveBar() {
  return `<div class="btn-row"><button class="btn primary" id="save-settings">Save changes</button></div>`;
}


/* ════════════════════ navigation ════════════════════
   *arr apps put sub-navigation in the sidebar, expanded under the active section,
   rather than in a tab strip above the content. */
const NAV = [
  { id: "queue",   label: "Queue",   icon: "table", href: "#/queue" },
  { id: "history", label: "History", icon: "clock-rotate-left", href: "#/history" },
  { id: "discs",   label: "Discs",   icon: "compact-disc", href: "#/discs" },
  { id: "settings", label: "Settings", icon: "gears", href: "#/settings/library",
    children: SETTINGS_TABS.map(([k, l]) => ({ key: k, label: l, href: `#/settings/${k}` })) },
  { id: "system",  label: "System",  icon: "laptop", href: "#/system/status",
    children: SYSTEM_TABS.map(([k, l]) => ({ key: k, label: l, href: `#/system/${k}` })) },
];

function navBadges() {
  const st = state.status, b = {};
  if (!st) return b;
  let sys = 0;
  const m = st.makemkv;
  if (!m.installed) sys++;
  else if (m.days_left != null && m.days_left < 8) sys++;
  if (!st.share) sys++;
  if (!st.wifi.connected) sys++;
  if (sys) b.system = sys;
  return b;
}

function renderSidebar(section, sub) {
  const badges = navBadges();
  const st = state.status;
  $("#sidebar").innerHTML = NAV.map(n => {
    const on = n.id === section;
    const badge = badges[n.id] ? `<span class="nav-badge">${badges[n.id]}</span>` : "";
    let html = `<a class="nav-top ${on ? "on" : ""}" href="${n.href}">
      <span class="ico">${icon(n.icon)}</span>${n.label}${badge}</a>`;
    if (on && n.children) {
      html += n.children.map(c =>
        `<a class="nav-sub ${c.key === sub ? "on" : ""}" href="${c.href}">${c.label}</a>`).join("");
    }
    return html;
  }).join("") + `<div class="side-foot"><div class="cap">${
    st ? `${capacityPhrase(st.storage)}<br><span class="muted">${esc(st.hostname)}.local</span>` : ""
  }</div></div>`;
}

/* ════════════════════ router ════════════════════ */
async function route() {
  const hash = location.hash.replace(/^#\//, "") || "queue";
  const [section, sub] = hash.split("/");
  const view = views[section] || views.queue;

  renderSidebar(section, sub);

  const content = $("#content");
  try {
    content.innerHTML = await view(sub);
  } catch (e) {
    content.innerHTML = `<div class="card"><div class="empty-state">
      <div class="big">${icon("triangle-exclamation")}</div><h2>Couldn't load that</h2><p>${esc(e.message)}</p></div></div>`;
    return;
  }
  paintIcons(content);
  wireContent(section, sub);
  $("#sidebar").classList.remove("open");
  scheduleLiveRefresh(section);
}

/* ── live refresh ──
   A progress bar that only moves when you press Refresh is not a progress bar. The
   queue re-renders itself while something is actually moving, and stops the moment
   nothing is -- an appliance with 512 MB should not be polling itself for no reason.

   `needs_input` deliberately does NOT keep the timer alive: that state has a form in
   it, and re-rendering underneath somebody halfway through typing a film title is a
   worse bug than a stale page. */
let liveTimer = null;

/* ── disc artwork ──
   Plex's trick: the film's poster behind the disc panel, so the box visibly knows what
   you put in. Deliberately quiet -- see .tray-art in app.css.

   It lives *inside* the disc cell rather than behind the whole viewport. Two reasons.
   A page-wide backdrop only shows where the page happens to be empty, so it vanished
   on a narrow window and had to be switched off on mobile entirely; and anchored to
   the panel it is composed against something, which is what makes it read as design
   rather than as a picture that happens to be behind the text.

   State, not DOM: the lookup result is cached here and `tray()` paints it on every
   render. The queue re-renders every 2.5s during a rip, so anything that faded itself
   in on each render would strobe. Painting the same background-image is a no-op for
   the browser, so it simply sits there. */
let artState = { label: null, image: null };

async function setDiscArt(label) {
  if (!label) { artState = { label: null, image: null }; return; }
  if (label === artState.label) return;      // same disc, already decided
  artState = { label: label, image: null };
  let hit;
  try { hit = await api.get(`/api/artwork?label=${encodeURIComponent(label)}`); }
  catch (e) { return; }                       // offline: no backdrop, no complaint
  if (artState.label !== label) return;       // disc changed while we were asking
  if (!hit || !hit.ok) return;                // not sure enough: show nothing
  // Decode before painting, so it appears complete rather than in bands, and so a
  // failed image never leaves a half-painted panel.
  await new Promise((res) => {
    const img = new Image();
    img.onload = img.onerror = res;
    img.src = hit.image;
  });
  if (artState.label !== label) return;
  artState.image = hit.image;
  artState.title = hit.title;
  if (location.hash.replace(/^#\//, "").split("/")[0] === "queue") route();
}

function scheduleLiveRefresh(section) {
  clearTimeout(liveTimer);
  if (section !== "queue") return;
  // Still true: a job waiting for input has a form in it, and re-rendering underneath
  // somebody mid-sentence is worse than a stale page.
  if ($$(".job.needs").length) return;
  // An *idle* queue has to keep looking too. Putting a disc in is the one thing on this
  // page that happens with no user action, and this used to return early whenever the
  // queue was empty -- so the tray stayed empty until the user clicked something, and
  // Refresh re-rendered the same stale snapshot. Slower when idle: nothing is racing.
  // 1.2s while a job is live. The phase line, the legs and the ETA all move on their
  // own during a rip, and at 2.5s the numbers visibly stepped rather than counted.
  const delay = $$(".job").length ? 1200 : 5000;
  liveTimer = setTimeout(() => {
    // A hidden tab must keep the loop alive, not end it. This used to just skip the
    // refresh and never reschedule, so switching away during a rip killed polling for
    // good: you came back to a page frozen on "Saving to MKV file" while the box had
    // long since finished uploading. Nothing was wrong with the box, and nothing was
    // wrong with the job -- the page had simply stopped asking.
    if (document.hidden) { scheduleLiveRefresh(section); return; }
    route();
  }, delay);
}

/* Coming back to the tab should show now, not in a second and a bit. */
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && (location.hash.replace(/^#\//, "").split("/")[0] || "queue") === "queue") {
    route();
  }
});

function collectSettings() {
  const out = {};
  $$("[data-set]").forEach(el => {
    const k = el.dataset.set;
    // `data-multi` means several checkboxes share one key and collect into a list --
    // the notification event switches, where each is a member rather than a boolean.
    if (el.dataset.multi !== undefined) {
      out[k] = out[k] || [];
      if (el.checked) out[k].push(el.value);
    } else if (el.type === "checkbox") out[k] = el.checked;
    else if (el.dataset.list !== undefined)
      out[k] = el.value.split(",").map(s => s.trim()).filter(Boolean);
    else if (el.type === "number") out[k] = Number(el.value);
    else out[k] = el.value;
  });
  return out;
}

function wireContent(section, sub) {
  const autorip = $("#autorip");
  if (autorip) autorip.onchange = async () => {
    const want = autorip.checked;
    try {
      const r = await api.post("/api/autorip", { enabled: want });
      toast(r.enabled ? "Auto Rip is on" : "Auto Rip is off", r.enabled ? "ok" : "");
      route();
    } catch (e) {
      autorip.checked = !want;
      toast(e.message, "bad");
    }
  };

  /* The channel accordion on Connect. One open at a time: these panels are ten steps
     of somebody else's instructions each, and two of them open at once is the flat
     page this replaced. Nothing is destroyed by closing one -- the inputs stay in the
     DOM, so `collectSettings` still reads a closed panel and Save still saves it. */
  $$(".chan .chan-head").forEach(head => {
    head.onclick = () => {
      const row = head.closest(".chan");
      const body = row.querySelector(".chan-body");
      const opening = body.hidden;
      $$(".chan").forEach(other => {
        other.classList.remove("open");
        other.querySelector(".chan-body").hidden = true;
        other.querySelector(".chan-head").setAttribute("aria-expanded", "false");
      });
      if (opening) {
        row.classList.add("open");
        body.hidden = false;
        head.setAttribute("aria-expanded", "true");
      }
    };
  });

  // Check the URL that is on screen, not the one that is stored. The whole point is
  // to catch a paste that went wrong, and a check that reads the database would pass
  // on the old value and tell the user nothing.
  const dcCheck = $("#dc-check");
  if (dcCheck) dcCheck.onclick = async () => {
    const out = $("#dc-out");
    dcCheck.disabled = true;
    out.className = "test-out";
    out.textContent = "Asking Discord…";
    try {
      const r = await api.post("/api/notifications/discord/check",
                               { url: ($("#dc-url") || {}).value || "" });
      if (r.ok) {
        out.className = "test-out ok";
        out.textContent = `Real webhook — posts as “${r.name}”. Save, then send a test.`;
      } else {
        out.className = "test-out bad";
        out.textContent = r.error;
      }
    } catch (e) {
      out.className = "test-out bad";
      out.textContent = e.message;
    }
    dcCheck.disabled = false;
  };

  // Nobody inside the software can see the drive's light, so this button exists for a
  // person to watch. The result reports sectors actually read, which is the only proof
  // available that the reads reached the drive rather than the page cache.
  $$("[data-signal-test]").forEach(b => b.onclick = async () => {
    const out = $("#signal-out");
    const mode = b.dataset.signalTest;
    $$("[data-signal-test]").forEach(x => x.disabled = true);
    out.className = "test-out";
    out.textContent = mode === "tray" ? "Watch the tray…" : "Watch the drive…";
    try {
      const r = await api.post("/api/drive/signal-test", { mode });
      out.className = "test-out " + (r.ok ? "ok" : "warn");
      out.textContent = r.message;
    } catch (e) {
      out.className = "test-out bad";
      out.textContent = e.message;
    }
    $$("[data-signal-test]").forEach(x => x.disabled = false);
  });

  $$("[data-test-notify]").forEach(b => b.onclick = async () => {
    const channel = b.dataset.testNotify;
    const out = $("#test-" + channel);
    b.disabled = true;
    out.className = "test-out";
    out.textContent = "Saving…";
    try {
      await api.put("/api/settings", collectSettings());
      out.textContent = "Sending…";
      const r = await api.post("/api/notifications/test", { channel });
      out.className = "test-out ok";
      out.textContent = r.message;
    } catch (e) {
      out.className = "test-out bad";
      out.textContent = e.message;
    }
    b.disabled = false;
  });

  /* ── System: Tasks, Backup, Events, Log Files ── */
  $$("[data-task]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      const r = await api.post(`/api/system/tasks/${b.dataset.task}`);
      toast(r.error ? `${r.label} failed: ${r.error}` : `${r.label} finished`,
            r.error ? "bad" : "ok");
    } catch (e) { toast(e.message, "bad"); }
    route();
  });

  const ledTest = $("#led-test");
  if (ledTest) ledTest.onclick = async () => {
    const out = $("#led-out");
    ledTest.disabled = true;
    out.className = "test-out";
    out.textContent = "Walking red, green, blue, white…";
    try {
      const r = await api.post("/api/system/led/test");
      // `detected: false` is not an error and must not be dressed as a success. A
      // box that says "OK" at an LED that never lit is the least debuggable result
      // this button could produce.
      out.className = `test-out ${r.detected ? "ok" : "warn"}`;
      out.textContent = r.message;
    } catch (e) {
      out.className = "test-out bad";
      out.textContent = e.message;
    }
    ledTest.disabled = false;
  };

  const bkNow = $("#bk-now");
  if (bkNow) bkNow.onclick = async () => {
    bkNow.disabled = true;
    try {
      const r = await api.post("/api/system/backups");
      toast(`Backup written — ${filesize(r.size)}`, "ok");
    } catch (e) { toast(e.message, "bad"); }
    route();
  };

  const bkUpload = $("#bk-upload"), bkFile = $("#bk-file");
  if (bkUpload) {
    bkUpload.onclick = () => bkFile.click();
    bkFile.onchange = async () => {
      const f = bkFile.files[0];
      if (!f) return;
      if (!confirm(`Restore from ${f.name}?\n\nThis overwrites your current settings.`)) {
        bkFile.value = "";
        return;
      }
      const fd = new FormData();
      fd.append("file", f);
      try {
        // FormData, so no JSON Content-Type -- the browser has to set the boundary.
        const r = await fetch("/api/system/backups/upload", { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || "Restore failed");
        toast(`Restored ${d.settings} setting(s). ${d.note}`, "ok");
      } catch (e) { toast(e.message, "bad"); }
      bkFile.value = "";
      route();
    };
  }

  $$("[data-restore]").forEach(b => b.onclick = async () => {
    const name = b.dataset.restore;
    if (!confirm(`Restore ${name}?\n\nThis overwrites your current settings. `
                 + `Share passwords are not in a backup and will have to be re-entered.`)) return;
    try {
      const r = await api.post(`/api/system/backups/${encodeURIComponent(name)}/restore`);
      toast(`Restored ${r.settings} setting(s). ${r.note}`, "ok");
    } catch (e) { toast(e.message, "bad"); }
    route();
  });

  $$("[data-delbackup]").forEach(b => b.onclick = async () => {
    const name = b.dataset.delbackup;
    if (!confirm(`Delete ${name}?`)) return;
    try { await api.del(`/api/system/backups/${encodeURIComponent(name)}`); }
    catch (e) { toast(e.message, "bad"); }
    route();
  });

  const evRefresh = $("#ev-refresh");
  if (evRefresh) evRefresh.onclick = () => route();
  const evClear = $("#ev-clear");
  if (evClear) evClear.onclick = async () => {
    if (!confirm("Clear the event log?\n\nThe log files on disk are not touched.")) return;
    try { await api.del("/api/system/events"); toast("Event log cleared", "ok"); }
    catch (e) { toast(e.message, "bad"); }
    route();
  };

  const lgRefresh = $("#lg-refresh");
  if (lgRefresh) lgRefresh.onclick = () => route();
  const lgDelete = $("#lg-delete");
  if (lgDelete) lgDelete.onclick = async () => {
    if (!confirm("Delete the rotated log files?\n\nThe two files currently being "
                 + "written to are kept.")) return;
    try {
      const r = await api.del("/api/system/logs");
      toast(r.deleted ? `Deleted ${r.deleted} file(s)` : "Nothing to delete", "ok");
    } catch (e) { toast(e.message, "bad"); }
    route();
  };

  const refresh = $("#t-refresh");
  if (refresh) refresh.onclick = () => route();
  const eject = $("#t-eject");
  if (eject) eject.onclick = async () => {
    const r = await api.post("/api/drive/eject");
    toast(r.message, r.ok ? "ok" : "bad");
  };

  // Offered when the diagnosis says the drive is probably in the socket that cannot
  // host. Reboots, so it borrows the same full-screen overlay as restart.
  if ($(".rips")) paintRipArt();

  // Bring the tile to the user rather than making them find it. `center` because a
  // tile scrolled to the very top of the viewport reads as "the first one", not as
  // "this one".
  const dupeTile = $("#dupe-tile");
  if (dupeTile) {
    dupeTile.scrollIntoView({ behavior: "smooth", block: "center" });
    // The blink is attention, not decoration, so it stops. A tile that pulses forever
    // becomes part of the furniture and stops meaning anything.
    setTimeout(() => dupeTile.classList.remove("dupe"), 9000);
  }
  const dupeX = $("#dupe-dismiss");
  if (dupeX) dupeX.onclick = () => { location.hash = "#/discs"; };

  const arRoute = $("#ar-route");
  if (arRoute) arRoute.onchange = async () => {
    try {
      await api.put("/api/settings", { transfer_mode: arRoute.value });
      if (state.settings) state.settings.transfer_mode = arRoute.value;
      toast(arRoute.value === "direct" ? "Rips go straight to your library"
                                       : "Rips are cached on the card first", "ok");
      route();
    } catch (e) { toast(e.message, "bad"); }
  };

  // Measured, not asserted. The recommendation is only worth showing because it comes
  // from this card rather than from an opinion about cards in general.
  const speed = $("#ar-speedtest");
  if (speed) speed.onclick = async () => {
    const out = $("#ar-speed-out");
    speed.disabled = true;
    out.className = "test-out";
    out.textContent = "Writing 64 MB…";
    try {
      const r = await api.post("/api/storage/speedtest", {});
      const c = r.card || {};
      out.className = "test-out " + (r.recommend === "direct" ? "warn" : "ok");
      out.textContent = `${c.write_mbs ?? "?"} MB/s write, ${c.read_mbs ?? "?"} read. ${r.why || ""}`;
      if (state.settings) state.settings.card_speed = c;
      if (r.recommend && r.recommend !== (state.settings || {}).transfer_mode) {
        if (confirm(`${r.why}\n\nSwitch to ${
            r.recommend === "direct" ? "writing straight to your library" : "caching on the card"}?`)) {
          await api.put("/api/settings", { transfer_mode: r.recommend });
          if (state.settings) state.settings.transfer_mode = r.recommend;
          route();
          return;
        }
      }
    } catch (e) {
      out.className = "test-out bad";
      out.textContent = e.message;
    }
    speed.disabled = false;
  };

  const arVerify = $("#ar-verify");
  if (arVerify) arVerify.onchange = async () => {
    try {
      await api.put("/api/settings", { verify_mode: arVerify.value });
      if (state.settings) state.settings.verify_mode = arVerify.value;
      toast("Saved", "ok");
      route();
    } catch (e) { toast(e.message, "bad"); }
  };

  const usbFix = $("#usb-host-fix");
  if (usbFix) usbFix.onclick = async () => {
    if (!confirm("Make both USB-C sockets work?\n\nThe box will restart. "
                 + "Your drive can then be in either one.")) return;
    usbFix.disabled = true;
    showWaiting("Reconfiguring the USB-C sockets\u2026");
    try {
      await api.post("/api/system/usb-host", {});
    } catch (e) {
      showWaiting(e.message, { retry: true, spin: false });
      return;
    }
    showWaiting("Restarting. This page will come back on its own in a minute or two.",
                { spin: true });
    waitForBoxBack();
  };

  const ripNow = $("#rip-now");
  if (ripNow) ripNow.onclick = async () => {
    // Say something immediately. Starting a rip reads the disc before the job exists,
    // and on a real encrypted DVD that scan is *nine minutes*, not the few seconds
    // this comment used to claim -- so `POST /api/rip` is a nine-minute request. The
    // spinner is not what saves it; the queue's own poll is. The job row appears
    // within a second or two and replaces this whole panel, button and all.
    ripNow.disabled = true;
    const was = ripNow.innerHTML;
    ripNow.innerHTML = `<span class="spin"></span> Starting\u2026`;
    try {
      await api.post("/api/rip", {});
    } catch (e) {
      toast(e.message, "bad");
      ripNow.innerHTML = was;
      ripNow.disabled = false;
      return;
    }
    route();   // the job now exists, so the panel becomes the progress view
  };

  $$("[data-cancel]").forEach(b => b.onclick = async () => {
    if (!confirm("Cancel this rip?\n\nAnything done so far is discarded.")) return;
    try { await api.post(`/api/queue/${b.dataset.cancel}/cancel`, {}); }
    catch (e) { toast(e.message, "bad"); }
    route();
  });

  $$("[data-retry]").forEach(b => b.onclick = async () => {
    try {
      const r = await api.post(`/api/queue/${b.dataset.retry}/retry`, {});
      toast(r.message, "ok");
    } catch (e) { toast(e.message, "bad"); }
    route();
  });

  // The four retries on History. Each one is offered only when the box can actually
  // do it -- the server works that out, because whether the staged file is still on
  // the card is not something the browser can see.
  $$("[data-hretry]").forEach(b => b.onclick = async () => {
    const id = b.dataset.hretry, act = b.dataset.haction;
    const asks = {
      "rip": "Read this disc again from the start?\n\nLeave the disc on the tray — "
           + "Riparr will pull it in. This is the expensive one; the whole disc gets "
           + "read again.",
      "verify-deep": "Read the whole file back off your library and hash it?\n\n"
           + "This takes about as long as the upload did and needs as much free space "
           + "again as the film.",
    };
    if (asks[act] && !confirm(asks[act])) return;
    b.disabled = true;
    const wasLabel = b.innerHTML;
    if (act === "rip") b.innerHTML = `<span class="spin"></span> Starting…`;
    try {
      const r = act === "upload" ? await api.post(`/api/queue/${id}/retry`, {})
              : act === "rip"    ? await api.post(`/api/queue/${id}/rerip`, {})
              : await api.post(`/api/queue/${id}/verify`,
                               { mode: act === "verify-deep" ? "deep" : "quick" });
      toast(r.message || "Started", "ok");
      if (act === "rip" || act === "upload") location.hash = "#/queue";
      else route();
    } catch (e) {
      toast(e.message, "bad");
      b.innerHTML = wasLabel;
      b.disabled = false;
    }
  });

  $$("[data-answer]").forEach(b => b.onclick = async () => {
    const picked = $('input[name="ni-title"]:checked');
    const body = { name: ($("#ni-name") || {}).value || "" };
    if (picked) body.title_index = Number(picked.value);
    if (!body.name.trim() && !picked) {
      toast("Give it a name, or pick which title is the film.", "bad");
      return;
    }
    b.disabled = true;
    try { await api.post(`/api/queue/${b.dataset.answer}/answer`, body); }
    catch (e) { toast(e.message, "bad"); b.disabled = false; return; }
    route();
  });

  $$("[data-skip]").forEach(b => b.onclick = async () => {
    if (!confirm("Skip this disc?\n\nIt will be ejected without being ripped.")) return;
    try { await api.post(`/api/queue/${b.dataset.skip}/answer`, { skip: true }); }
    catch (e) { toast(e.message, "bad"); }
    route();
  });

  $$("[data-rerip]").forEach(b => b.onclick = async () => {
    if (!confirm("Re-rip this disc?\n\nLeave it on the tray — Riparr will pull the "
                 + "tray in. This reads the whole disc again and overwrites what's "
                 + "in your library.")) return;
    // Closing the tray and waiting for the drive to find the disc takes up to half a
    // minute, and a button that sits there looking clickable for half a minute is a
    // button somebody clicks twice.
    b.disabled = true;
    const was = b.innerHTML;
    b.innerHTML = `<span class="spin"></span> Starting…`;
    try {
      await api.post(`/api/discs/${encodeURIComponent(b.dataset.rerip)}/rerip`, {});
      toast("Started", "ok");
      location.hash = "#/queue";
    } catch (e) {
      toast(e.message, "bad");
      b.innerHTML = was;
      b.disabled = false;
    }
  });

  // Fetching is a network round trip to somebody else's forum, so it happens after
  // the page is on screen rather than blocking it.
  if ($("#mk-key-offer")) offerBetaKey("#mk-key-offer", "#mk-key-input");

  /* The panel was drawn from whatever was already known, which on a first visit is
     nothing. Follow the probe that the page load kicked off. */
  if ($("#sites-inner")) followSites(state.mkKeyTopic);

  const recheck = $("#sites-recheck");
  if (recheck) recheck.onclick = async () => {
    const out = $("#sites-out");
    recheck.disabled = true;
    out.className = "test-out";
    out.textContent = "Checking…";
    try {
      // The one call that is allowed to wait: the user asked for it by pressing this.
      const r = await api.post("/api/makemkv/sites", {});
      paintSites(r, state.mkKeyTopic);
      out.textContent = "";
    } catch (e) {
      out.className = "test-out bad";
      out.textContent = e.message;
    }
    recheck.disabled = false;
  };

  const mkAccept = $("#mk-accept");
  if (mkAccept) {
    mkAccept.onchange = () => { $("#mk-install").disabled = !mkAccept.checked; };
    $("#mk-install").onclick = async () => {
      $("#mk-install").disabled = true;
      try { await api.post("/api/makemkv/install", { accept_eula: mkAccept.checked }); }
      catch (e) {
        $("#mk-progress").innerHTML = `<div class="result bad">${esc(e.message)}</div>`;
        return;
      }
      pollMakeMKV();
    };
  }

  const save = $("#save-settings");
  if (save) save.onclick = async () => {
    try {
      await api.put("/api/settings", collectSettings());
      toast("Settings saved", "ok");
    } catch (e) { toast(e.message, "bad"); }
  };

  const themePick = $("#theme-pick");
  if (themePick) themePick.onchange = async () => {
    $("#theme").href = `/static/themes/${themePick.value}.css`;
    await api.put("/api/settings", { theme: themePick.value });
    toast(`Theme set to ${themePick.value}`, "ok");
  };

  $$("[data-forget]").forEach(b => b.onclick = async () => {
    await api.del(`/api/discs/${encodeURIComponent(b.dataset.forget)}`);
    toast("Disc forgotten"); route();
  });

  $$("[data-del-share]").forEach(b => b.onclick = async () => {
    await api.del(`/api/shares/${b.dataset.delShare}`);
    toast("Share removed"); route();
  });

  const scan = $("#wifi-scan");
  if (scan) scan.onclick = async () => {
    const box = $("#wifi-results");
    box.innerHTML = `<div class="result busy"><span class="spin"></span>Scanning…</div>`;
    const { networks, note } = await api.post("/api/wifi/scan");
    box.innerHTML = `<p class="muted" style="margin-bottom:10px">${esc(note)}</p>` +
      networks.map((n, i) => `
      <div class="rowitem" data-join="${i}"><div class="grow">
        <div class="t">${esc(n.ssid)}</div>
        <div class="s">${n.signal}%${n.band ? ` · ${n.band} GHz` : ""}${n.secure ? "" : " · open"}</div>
      </div><span class="badge">join</span></div>`).join("") +
      `<div id="join-res"></div>`;

    $$("#wifi-results [data-join]").forEach(node => node.onclick = async () => {
      const n = networks[+node.dataset.join];
      const res = $("#join-res");
      let pass = "";
      if (n.secure) {
        pass = prompt(`Password for ${n.ssid}`) ?? "";
        if (!pass) return;
      }
      res.innerHTML = `<div class="result busy"><span class="spin"></span>
        Joining ${esc(n.ssid)}… if this succeeds the connection drops briefly.</div>`;
      try {
        const r = await api.post("/api/wifi/connect", { ssid: n.ssid, password: pass });
        res.innerHTML = `<div class="result ${r.ok ? "ok" : "bad"}">
          <b>${r.ok ? "Joined" : "Couldn't join"}</b>${esc(r.message || "")}</div>`;
        if (r.ok) toast(`Joined ${n.ssid}`, "ok");
      } catch (e) {
        res.innerHTML = `<div class="result bad"><b>Couldn't join</b>
          <div class="why">${esc(e.message)}</div></div>`;
      }
    });
  };

  const pwGo = $("#pw-go");
  if (pwGo) pwGo.onclick = async () => {
    const res = $("#pw-res");
    if ($("#pw-new").value !== $("#pw-new2").value) {
      res.innerHTML = `<div class="result bad">The two passwords don't match.</div>`;
      return;
    }
    try {
      await api.post("/api/auth/password", {
        current_password: $("#pw-cur").value, new_password: $("#pw-new").value });
      res.innerHTML = `<div class="result ok">Password changed.</div>`;
    } catch (e) { res.innerHTML = `<div class="result bad">${esc(e.message)}</div>`; }
  };

  const check = $("#upd-check");
  if (check) check.onclick = () => route();
  const inst = $("#upd-install");
  if (inst) inst.onclick = async () => {
    inst.disabled = true;
    const r = await api.post("/api/update/install");
    toast(r.message, r.ok ? "ok" : "bad");
    inst.disabled = false;
  };

  const imp = $("#import-btn");
  if (imp) {
    imp.onclick = () => $("#import-file").click();
    $("#import-file").onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      await api.post("/api/config/import", JSON.parse(await f.text()));
      toast("Settings imported", "ok"); route();
    };
  }

  const addShare = $("#add-share");
  if (addShare) addShare.onclick = () => {
    $("#share-add").innerHTML = `<div class="section"><h2>Add a share</h2><div>
      <p class="muted">Riparr writes a test file and reads it back before saving.</p>
      <div class="grid2">
        <label class="f"><span>Server</span><input id="a-host" placeholder="tower.local"></label>
        <label class="f"><span>Share</span><input id="a-share" placeholder="Media"></label>
        <label class="f"><span>Folder</span><input id="a-path" placeholder="Movies"></label>
        <label class="f"><span>Username</span><input id="a-user"></label>
        <label class="f"><span>Password</span><input id="a-pass" type="password"></label>
      </div>
      <div class="btn-row"><button class="btn primary" id="a-go">Test and save</button></div>
      <div id="a-res"></div></div></div>`;
    $("#a-go").onclick = async () => {
      const body = {
        host: $("#a-host").value.trim(), share: $("#a-share").value.trim(),
        path: $("#a-path").value.trim(), username: $("#a-user").value.trim(),
        password: $("#a-pass").value, name: "",
      };
      const res = $("#a-res");
      res.innerHTML = `<div class="result busy"><span class="spin"></span>Testing…</div>`;
      try {
        await api.post("/api/shares", body);
        toast("Share added", "ok"); route();
      } catch (e) { res.innerHTML = `<div class="result bad">${esc(e.message)}</div>`; }
    };
  };

  $$("[data-test-share]").forEach(b => b.onclick = async () => {
    toast("Testing the share…");
  });
}

/* ════════════════════ chrome ════════════════════ */
function renderChrome() {
  const st = state.status;
  const pills = [];
  const m = st.makemkv;
  if (!m.installed) pills.push(`<span class="pill bad">MakeMKV missing</span>`);
  else if (m.days_left != null && m.days_left < 8)
    pills.push(`<span class="pill warn">Key expires in ${m.days_left}d</span>`);
  if (!st.share) pills.push(`<span class="pill warn">No share</span>`);
  if (!st.wifi.connected) pills.push(`<span class="pill bad">Offline</span>`);
  $("#health-pills").innerHTML = pills.join("");
}

$("#hamburger").onclick = () => $("#sidebar").classList.toggle("open");
$("#user-btn").onclick = (e) => { e.stopPropagation(); $("#user-menu").classList.toggle("hidden"); };
document.addEventListener("click", () => $("#user-menu")?.classList.add("hidden"));
$("#logout").onclick = async (e) => {
  e.preventDefault();
  await api.post("/api/auth/logout");
  location.reload();
};

/* ── power ──
   Restarting and shutting down live in the account menu, not on a page: they are
   things you do to the appliance, not to the queue. Progress goes on the same
   full-screen overlay that covers a cold start, because the service is about to stop
   answering and any in-page element saying so is about to be unreachable anyway. */
async function powerAction(action, label, after) {
  showWaiting(`${label}\u2026`);
  try {
    await api.post("/api/system/power", { action });
  } catch (e) {
    showWaiting(e.message, { retry: true, spin: false });
    return;
  }
  showWaiting(after, { spin: action === "reboot" });
  if (action === "reboot") waitForBoxBack();
}

$("#sys-reboot").onclick = (e) => {
  e.preventDefault();
  if (!confirm("Restart Riparr?\n\nAny rip in progress will be lost.")) return;
  powerAction("reboot", "Restarting",
              "Restarting. This page will come back on its own in a minute or two.");
};
$("#sys-poweroff").onclick = (e) => {
  e.preventDefault();
  if (!confirm("Shut down Riparr?\n\nThere is no power button \u2014 you will have to "
               + "unplug the cable and plug it back in to start it again.")) return;
  powerAction("poweroff", "Shutting down",
              "Shutting down. Wait for the light to settle, then it is safe to unplug. "
              + "To start it again, plug the cable back in.");
};
window.addEventListener("hashchange", route);
$("#gate-retry").onclick = () => location.reload();

/* ════════════════════ boot ════════════════════ */
/* The service takes a moment to answer after the box powers on, and the page is very
   often loaded during exactly that window. Treating a failed fetch as "sign in" is
   the most misleading answer available: a login form asserts that an account exists,
   which sends people off to reset a password they never set — or to reflash a card
   that was working perfectly. Wait, say so, and only then give up. */
function showWaiting(msg, { retry = false, spin = true } = {}) {
  $("#shell").classList.add("hidden");
  $("#wizard").classList.add("hidden");
  $("#gate").classList.remove("hidden");
  $("#login-form").classList.add("hidden");
  $("#gate-waiting").classList.remove("hidden");
  $("#gate-wait-msg").textContent = msg;
  $("#gate-spin").classList.toggle("hidden", !spin);
  $("#gate-retry").classList.toggle("hidden", !retry);
}

function hideWaiting() {
  $("#gate-waiting").classList.add("hidden");
  $("#login-form").classList.remove("hidden");
}

const showStarting = (attempt) => showWaiting(
  attempt < 3 ? "Starting up…"
              : "Still starting — this can take a minute after power-on.");

const showUnreachable = () => showWaiting(
  "Can't reach the Riparr service on this box. It may still be starting; if this "
  + "keeps happening, check `systemctl status riparr` over SSH.",
  { retry: true, spin: false });

async function boot() {
  paintIcons();          // the static chrome in index.html
  let setup;
  for (let attempt = 0; ; attempt++) {
    try { setup = await api.get("/api/setup/state"); break; }
    catch (e) {
      // A real 401 already showed the gate and means something quite different.
      if (e.message === "Not signed in") return;
      if (attempt >= 8) { showUnreachable(); return; }
      showStarting(attempt);
      await new Promise(r => setTimeout(r, 1500));
    }
  }
  hideWaiting();

  if (!setup.has_users) { wizard.step = 0; wizard.render(); return; }

  let me;
  try { me = await api.get("/api/auth/me"); } catch (e) { showGate(); return; }
  if (!me.username) { showGate(); return; }
  $("#menu-who").textContent = me.username;

  if (!setup.complete) {
    wizard.step = 1;                       // account exists; resume at MakeMKV
    wizard.render();
    return;
  }

  try { state.status = await api.get("/api/status"); }
  catch (e) { showGate(); return; }
  state.settings = await api.get("/api/settings");
  $("#theme").href = `/static/themes/${state.settings.theme || "servarr"}.css`;

  $("#gate").classList.add("hidden");
  $("#wizard").classList.add("hidden");
  $("#shell").classList.remove("hidden");
  renderChrome();
  if (!location.hash) location.hash = "#/queue";
  route();
}

boot();
