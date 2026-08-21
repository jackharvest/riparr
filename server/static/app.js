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
    d.innerHTML = `<div class="section"><div>
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
      <div>
        ${res.ok ? "" : `<div class="result ${needsAuth ? "" : "bad"}"><b>${
            needsAuth ? "This server wants a username and password"
                      : "Couldn't list shares"}</b>
           <div class="why">${esc(res.error)}</div></div>`}
        <div class="grid2">
          <label class="f"><span>Username</span>
            <input id="w-suser" value="${esc(user)}" autocomplete="off"
                   placeholder="DOMAIN\\user or user"></label>
          <label class="f"><span>Password</span>
            <input id="w-spass" type="password" value="${esc(pass)}"
                   autocomplete="new-password"></label>
        </div>
        <div class="btn-row" style="margin:-4px 0 14px">
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
  const { jobs } = await api.get("/api/queue");
  const drives = state.status.drives || [];
  const ar = await api.get("/api/autorip");
  return `
    ${head("Queue", "Ripping and uploading happen as one overlapping operation.",
           `<button class="tool" id="t-refresh"><span class="ti">${icon("arrows-rotate")}</span>Refresh</button>
            <button class="tool" id="t-eject" ${drives.length ? "" : "disabled"}>
              <span class="ti">${icon("eject")}</span>Eject</button>`)}
    ${autoRipPanel(ar)}
    <div class="card">
      ${jobs.length ? `<table>
        <thead><tr><th>Title</th><th>Mode</th><th>Rip</th><th>Upload</th><th>State</th></tr></thead>
        <tbody>${jobs.map(j => `<tr>
          <td>${esc(j.title || j.disc_label || "Unknown disc")}</td>
          <td><span class="badge ${j.mode === "burst" ? "burst" : ""}">${esc(j.mode || "\u2014")}</span></td>
          <td style="width:150px"><div class="bar"><i style="width:${pct(j.bytes_ripped, j.bytes_total)}%"></i></div></td>
          <td style="width:150px"><div class="bar dual"><i class="sent" style="width:${pct(j.bytes_sent, j.bytes_total)}%"></i></div></td>
          <td><span class="badge">${esc(j.state)}</span></td>
        </tr>`).join("")}</tbody></table>
        ${trayStrip(drives, state.status.optical)}`
      : tray(drives, state.status.optical)}
    </div>`;
};

/* ── the tray ──
   The disc and the drive holding it are one fact, so they are drawn once. Which of
   the three shapes below applies depends only on how far up the chain something is
   missing: no drive at all, a drive with an open tray, or a disc sitting in one. */

function driveName(d) {
  return [d.vendor, d.model].filter(Boolean).join(" ") || "Optical drive";
}

function tray(drives, optical) {
  if (!drives.length) {
    // An empty card used to render as nothing at all, so "no drive" was communicated
    // by absence -- the one case where the user most needs to be told something.
    const hint = optical && optical.hint;
    return `<div class="empty-state tray-none">
      <div class="big">${icon("triangle-exclamation")}</div>
      <h2>No optical drive detected</h2>
      <p>Riparr has nothing to read a disc with, so nothing else on this page can
         happen yet.</p>
      ${hint ? `<p class="why">${esc(hint)}</p>` : ""}
    </div>`;
  }
  const d = drives.find(x => x.present) || drives[0];
  if (!d.present) {
    return `<div class="empty-state">
      <div class="big">${icon("compact-disc")}</div>
      <h2>Nothing in the queue</h2>
      <p>Insert a disc and close the tray. Riparr takes it from there.</p>
      <p class="tray-drive">${esc(driveName(d))} <span class="dev">${esc(d.device)}</span></p>
    </div>`;
  }
  return `<div class="empty-state tray-loaded">
    <div class="big spinning">${icon("compact-disc")}</div>
    <h2>${esc(d.label || "Disc loaded")}</h2>
    <p>${d.media ? `${esc(d.media)} \u2014 loaded and ready.` : "Loaded and ready."}
       Nothing is queued yet.</p>
    <p class="tray-drive">${esc(driveName(d))} <span class="dev">${esc(d.device)}</span></p>
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
    <span class="grow"></span>
    <span class="${d.present ? "loaded" : "muted"}">${
      d.present ? esc(d.label || "disc loaded") : "tray empty"}</span></div>`;
}

const pct = (a, b) => (b ? Math.min(100, (a / b) * 100).toFixed(1) : 0);

function autoRipPanel(ar) {
  const on = ar.enabled;
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
          : "Not available yet:"}</div>
        ${ar.ready ? "" : `<ul class="ar-blockers">${ar.blockers.map(b => `
          <li><a href="${esc(b.where)}"><b>${esc(b.what)}</b></a> — ${esc(b.why)}</li>`).join("")}</ul>`}
      </div>
    </div>`;
}

views.history = async () => {
  const { jobs } = await api.get("/api/history");
  return `${head("History", "Everything Riparr has finished or failed.")}
    ${jobs.length ? `<div class="card"><table>
      <thead><tr><th>Title</th><th>Finished</th><th>Result</th></tr></thead>
      <tbody>${jobs.map(j => `<tr>
        <td>${esc(j.title || j.disc_label)}</td>
        <td class="muted">${ago(j.finished_at)}</td>
        <td><span class="badge ${j.state === "done" ? "ok" : "bad"}">${esc(j.state)}</span>
          ${j.error ? `<div class="s muted">${esc(j.error)}</div>` : ""}</td>
      </tr>`).join("")}</tbody></table></div>`
    : `<div class="card"><div class="empty-state"><div class="big">${icon("clock-rotate-left")}</div>
        <h2>No history yet</h2><p>Finished rips will appear here.</p></div></div>`}`;
};

views.discs = async () => {
  const { discs } = await api.get("/api/discs");
  return `${head("Discs", "Every disc Riparr has seen, by fingerprint. This is what makes it ask about a problem disc only once.")}
    ${discs.length ? `<div class="card"><table>
      <thead><tr><th>Title</th><th>Label</th><th>Ripped</th><th></th></tr></thead>
      <tbody>${discs.map(d => `<tr>
        <td>${esc(d.title || "—")}</td><td class="muted">${esc(d.label || "—")}</td>
        <td class="muted">${ago(d.ripped_at)}</td>
        <td><button class="btn" data-forget="${esc(d.fingerprint)}">Forget</button></td>
      </tr>`).join("")}</tbody></table></div>`
    : `<div class="card"><div class="empty-state"><div class="big">${icon("compact-disc")}</div>
        <h2>No discs recorded</h2>
        <p>Once Riparr rips a disc it remembers it, so reinserting it is refused
           instead of costing you forty minutes.</p></div></div>`}`;
};

/* ── settings ── */
/* Five pages, not Sonarr's twenty. Riparr has no indexers, no download clients, no
   quality profiles and no custom formats -- and "configure once" (concept.md) means the
   settings surface should stay something a person can read in one sitting. */
const SETTINGS_TABS = [
  ["library", "Library"], ["ripping", "Ripping"], ["connect", "Connect"],
  ["network", "Network"], ["general", "General"],
];

views.settings = async (sub = "library") => {
  const s = state.settings = await api.get("/api/settings");
  const body = await (settingsPages[sub] || settingsPages.library)(s);
  const label = (SETTINGS_TABS.find(([k]) => k === sub) || SETTINGS_TABS[0])[1];
  return `${head(label, "Configure once. Anything that needs revisiting is a bug.")}${body}`;
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
        ${opt("burst", "Always burst", s.transfer_mode)}
        ${opt("stream", "Always stream", s.transfer_mode)}
      </select>
      <span class="help">Automatic streams when space is tight and rips at full speed,
        ejecting early, when there's room. Throughput is identical either way — Wi-Fi is
        the binding constraint, not the drive.</span></label>
    ${sw("verify_after_transfer", "Verify after transfer", s.verify_after_transfer,
        "Reads the file back from the share and checks it matches. Catches silent corruption.")}
    ${sw("keep_local_copy", "Keep the local copy", s.keep_local_copy,
        "Retains the rip until the space is needed, so a downstream problem is a re-copy rather than a re-rip.")}
  </div></div>${saveBar()}`;

settingsPages.connect = (s) => `
  <div class="section"><h2>Handoff</h2><div>
    <p class="muted">Riparr does not transcode — a Pi Zero 2W would take days and the
      result would be poor. Hand finished files to a real machine instead.</p>
    <label class="f" style="margin-top:14px"><span>Webhook on completion</span>
      <input data-set="webhook_url" value="${esc(s.webhook_url)}" placeholder="https://…">
      <span class="help">POSTs the file path and metadata once a rip is verified.</span></label>
    <label class="f"><span>Watch folder</span>
      <input data-set="watch_folder" value="${esc(s.watch_folder)}" placeholder="/Media/_incoming">
      <span class="help">Write here instead, for Tdarr or Unmanic to pick up.</span></label>
  </div></div>${saveBar()}`;

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
          <a href="${esc(mk.eula_url)}" target="_blank" rel="noopener">Read it</a>.</p>
        <label class="switch"><input type="checkbox" id="mk-accept"><span class="track"></span>
          <span class="lbl">I have read and accept MakeMKV's licence agreement</span></label>
        <div class="btn-row"><button class="btn primary" id="mk-install" disabled>
          Download and install</button></div>
        <div id="mk-progress"></div>`}
      <label class="f" style="margin-top:${st.installed ? 0 : 16}px"><span>Key</span>
        <input data-set="makemkv_key" id="mk-key-input" value="${esc(s.makemkv_key)}" placeholder="Beta or purchased key">
        <span class="help">MakeMKV is free while it is in beta, behind a key that
          GuinpinSoft rolls over roughly monthly.</span></label>
      <div class="f"><span></span><div class="grow" id="mk-key-offer"></div></div>
      <label class="f"><span>Warn me this many days before it expires</span>
        <input type="number" data-set="warn_key_days" value="${s.warn_key_days}"></label>
    </div></div>

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
          ? st.drives.map(d => esc(d.name || d.device)).join(", ")
          : `<span class="muted">${esc((st.optical && st.optical.summary) || "no drive detected")}</span>`}</div>
      </div>
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
  const m = st.makemkv;
  if (!m.installed)
    out.push({ level: "bad", message: "MakeMKV is not installed, so no disc can be read.",
               href: "#/settings/makemkv", action: "Install MakeMKV" });
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
}

function collectSettings() {
  const out = {};
  $$("[data-set]").forEach(el => {
    const k = el.dataset.set;
    if (el.type === "checkbox") out[k] = el.checked;
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

  // Fetching is a network round trip to somebody else's forum, so it happens after
  // the page is on screen rather than blocking it.
  if ($("#mk-key-offer")) offerBetaKey("#mk-key-offer", "#mk-key-input");

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
        <div class="s">${n.signal}% · 2.4 GHz${n.secure ? "" : " · open"}</div>
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
