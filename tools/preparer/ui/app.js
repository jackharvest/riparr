/* Riparr Preparer — screen flow and bridge calls.
   window.riparr.<method>(...) is injected by app.py and returns a Promise. */

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  boot: null,
  verify: true,
  disk: null,
  net: null,          // {ssid, secure, hidden, bands}
  wifiPw: "",
  hostname: "riparr",
  port: 9797,
  screen: "card",
  poll: null,
  logLen: -1,        // so the first poll always paints the log
  setupUrl: null,
  elapsed: null,
};

const ORDER = ["card", "wifi", "name", "review", "write", "setup"];

// Screens that are not themselves rail steps still have a place in the rail. Without
// this, reaching the handoff or the finish line clears every tick the user just
// earned, because indexOf returns -1 and nothing counts as behind it.
const RAIL_AS = { handoff: "setup", failed: null, done: "__all__" };

function show(name) {
  state.screen = name;
  $$(".screen").forEach(s => s.classList.toggle("on", s.dataset.screen === name));

  const as = name in RAIL_AS ? RAIL_AS[name] : name;
  if (as === null) return;                       // failure: leave the rail alone
  const all = as === "__all__";
  const idx = all ? ORDER.length : ORDER.indexOf(as);
  $$("#steps li").forEach(li => {
    const i = ORDER.indexOf(li.dataset.step);
    li.classList.toggle("active", !all && i === idx);
    li.classList.toggle("done", idx > -1 && i < idx);
  });
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ── signal strength ────────────────────────────────────── */
function bars(rssi) {
  // -50 excellent, -60 good, -70 fair, below that poor.
  let n = 0;
  if (rssi != null) n = rssi >= -55 ? 4 : rssi >= -65 ? 3 : rssi >= -75 ? 2 : 1;
  return `<span class="bars">${[1, 2, 3, 4]
    .map(i => `<i class="${i <= n ? "lit" : ""}"></i>`).join("")}</span>`;
}

/* ── step 1: card ───────────────────────────────────────── */
function renderDisks(disks) {
  const el = $("#disk-list");
  if (!disks.length) {
    // Saying only "insert one" is unhelpful to the person who *has*: the filter is
    // deliberately narrow, so the reasons a real card is missing are worth naming.
    el.innerHTML = `<div class="empty">No SD card found.<br>
      Insert one and choose <b>Rescan</b>.
      <div class="empty-why">Already inserted? Only external, removable cards between
        4 and 70 GB are listed, so a reader that reports itself as a fixed disk, or a
        card larger than 70 GB, won't appear. Try a different reader, or a
        direct slot if your Mac has one.</div></div>`;
    $("#card-next").disabled = true;
    $("#card-hint").textContent = "";
    return;
  }
  el.innerHTML = disks.map(d => `
    <div class="item" data-id="${esc(d.id)}">
      <div class="grow">
        <div class="title">${esc(d.name || "SD card")}</div>
        <div class="sub">/dev/${esc(d.id)}${d.protocol ? " · " + esc(d.protocol) : ""}</div>
      </div>
      <div class="right">${d.size_gb} GB</div>
    </div>`).join("");

  el.querySelectorAll(".item").forEach(node => {
    node.onclick = () => {
      el.querySelectorAll(".item").forEach(n => n.classList.remove("sel"));
      node.classList.add("sel");
      state.disk = disks.find(d => d.id === node.dataset.id);
      $("#card-next").disabled = false;
      $("#card-hint").textContent = `Everything on /dev/${state.disk.id} will be erased.`;
    };
  });
  if (disks.length === 1) el.querySelector(".item").click();
}

async function loadDisks() {
  $("#disk-list").innerHTML = `<div class="empty">Looking for cards…</div>`;
  const r = await riparr.refresh_disks();
  renderDisks(r.disks);
}

/* ── step 2: wi-fi ──────────────────────────────────────── */
function renderNets(nets, method) {
  const el = $("#wifi-list");
  if (!nets.length) {
    el.innerHTML = `<div class="empty">No networks found.<br>
      Use <b>Enter a name manually</b>.</div>`;
    return;
  }
  el.innerHTML = nets.map((n, i) => {
    const tags = [];
    if (n.pi_ok) {
      if (n.bands.includes("2.4") && n.bands.includes("5")) tags.push(`<span class="tag good">2.4 + 5 GHz</span>`);
      else if (n.bands.includes("5")) tags.push(`<span class="tag good">5 GHz — faster</span>`);
      else if (n.bands.includes("2.4")) tags.push(`<span class="tag good">2.4 GHz</span>`);
      else tags.push(`<span class="tag warn">band unknown</span>`);
      if (n.saved && !n.seen) tags.push(`<span class="tag">saved, out of range</span>`);
      else if (n.saved) tags.push(`<span class="tag">saved</span>`);
      if (!n.secure) tags.push(`<span class="tag warn">open</span>`);
    }
    const sub = n.pi_ok ? tags.join("")
      : `<span class="sub">the box has no radio for this band</span>`;
    return `<div class="item ${n.pi_ok ? "" : "off"}" data-i="${i}">
      ${n.pi_ok ? bars(n.rssi) : `<span class="bars"></span>`}
      <div class="grow">
        <div class="title">${esc(n.ssid)}</div>
        <div class="sub">${sub}</div>
      </div>
    </div>`;
  }).join("");

  el.querySelectorAll(".item:not(.off)").forEach(node => {
    node.onclick = () => {
      el.querySelectorAll(".item").forEach(n => n.classList.remove("sel"));
      node.classList.add("sel");
      pickNet(nets[+node.dataset.i]);
    };
  });
}

function pickNet(n) {
  state.net = n;
  state.wifiPw = "";
  const needsPw = n.secure !== false;
  $("#wifi-pw-block").classList.toggle("hidden", !needsPw);
  $("#wifi-pw-for").textContent = n.ssid;
  $("#wifi-pw").value = "";
  $("#wifi-keychain-note").textContent = "";
  $("#wifi-keychain-note").className = "micro";
  // Offered only for a network macOS has actually saved. Showing it for one it has
  // never seen would be a button that can only fail.
  $("#wifi-keychain-row").classList.toggle("hidden", !(needsPw && n.saved));
  if (needsPw) $("#wifi-pw").focus();
  updateWifiNext();
  $("#wifi-hint").textContent = (!n.bands || !n.bands.length)
    ? "Band unknown — the box will try, but cannot confirm it can reach this one." : "";
}

function updateWifiNext() {
  const n = state.net;
  if (!n) { $("#wifi-next").disabled = true; return; }
  const pw = $("#wifi-pw").value;
  $("#wifi-next").disabled = n.secure !== false && pw.length < 8;
}

async function loadWifi() {
  $("#wifi-list").innerHTML = `<div class="empty">Scanning…</div>`;
  const r = await riparr.scan_wifi();
  renderNets(r.networks, r.method);
  const usable = r.networks.filter(n => n.pi_ok).length;
  $("#wifi-hint").textContent = r.method === "live"
    ? `${r.networks.length} networks · ${usable} usable`
    : r.method === "none" ? "Couldn't scan — enter the name manually."
    : `Limited scan (${r.method}) — band information may be missing.`;
}

/* ── step 3: name ───────────────────────────────────────── */
function validHost(v) { return /^[a-z0-9][a-z0-9-]{0,62}$/.test(v); }

/* ── step 4: review ─────────────────────────────────────── */
function cfg() {
  return {
    disk: state.disk ? state.disk.id : null,
    image: state.boot.images.length ? state.boot.images[0].path : null,
    hostname: state.hostname,
    port: state.port,
    user: "riparr",
    password: state.boot.password,
    ssid: state.net.ssid,
    wifi_pw: state.wifiPw,
    secure: state.net.secure !== false,
    hidden: !!state.net.hidden,
    country: state.boot.country,
    timezone: state.boot.timezone,
    verify: state.verify,
  };
}

async function buildReview() {
  const c = cfg();
  const img = state.boot.images[0];
  const rows = [
    ["Card", state.disk
      ? `${esc(state.disk.name)} · /dev/${esc(state.disk.id)} · ${state.disk.size_gb} GB`
      : "—"],
    ["Image", img ? esc(img.name) : "missing"],
    ["Wi-Fi", esc(c.ssid) + (c.hidden ? " (hidden)" : "")],
    ["Wi-Fi password", c.secure
      ? "•".repeat(Math.min(c.wifi_pw.length, 16)) +
        ' <span class="tag">stored as a derived key</span>'
      : "none (open network)"],
    ["Reachable at", img && img.kind === "riparr"
      ? `http://${esc(c.hostname)}.local:${c.port}`
      : `${esc(c.hostname)}.local <span class="tag">over SSH — this image has no Riparr on it</span>`],
    ["Riparr port", `${c.port}`],
    ["System account", `${esc(c.user)} · password saved in user_password.txt`],
    ["SSH", state.boot.has_key ? "enabled · key + password" : "enabled · password"],
    ["MakeMKV", state.boot.makemkv
      ? 'copied onto the card <span class="tag">no scp needed</span>'
      : '<span class="tag warn">not in the build folder</span>'],
    ["Region", `${esc(c.country)} · ${esc(c.timezone)}`],
  ];
  $("#summary").innerHTML = rows
    .map(([k, v]) => `<div class="r"><div class="k">${k}</div><div class="v">${v}</div></div>`)
    .join("");
  // Without an image there is nothing to write, and "Erase & write" was a button that
  // could only ever produce an error dialog. Settings-only still works and is the
  // useful thing to offer instead.
  const haveImage = !!img;
  $("#do-write").disabled = !haveImage;
  $("#review-warn").innerHTML = haveImage ? "" :
    "There's no <b>.img.xz</b> in your build folder, so there is nothing to write. "
    + "You can still save the settings file and copy it onto a card that is already "
    + "flashed.";

  const t = await riparr.preview_toml(c);
  $("#toml").textContent = t.toml;
}

/* ── step 5: write ──────────────────────────────────────── */
const PHASE_TITLE = {
  auth: "Waiting for permission",
  "verify-image": "Checking the image",
  "verify-card": "Checking the card",
  extras: "Adding MakeMKV",
  unmount: "Preparing the card",
  write: "Writing your card",
  flush: "Finishing the write",
  mount: "Almost there",
  provision: "Applying your settings",
  eject: "Ejecting",
};

function fmtBytes(n) { return (n / 1e9).toFixed(2) + " GB"; }
function fmtDur(s) {
  if (!s || !isFinite(s)) return "";
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return m > 0 ? `${m} min ${sec}s` : `${sec}s`;
}
function fmtEta(s) {
  if (!s || !isFinite(s)) return "";
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return m > 0 ? `about ${m}m ${String(sec).padStart(2, "0")}s left` : `about ${sec}s left`;
}

function pollWrite() {
  clearInterval(state.poll);
  state.poll = setInterval(async () => {
    let st;
    try { st = await riparr.write_status(); } catch (e) { return; }
    const phase = st.phase || "idle";

    if (phase === "done") {
      clearInterval(state.poll);
      // The card is written. That used to be the finish line; now it is halfway.
      // Hand over to the person for the one part only they can do — moving the card
      // into the box — and pick the work back up when they say it is plugged in.
      $("#handoff-skip").innerHTML =
        `<a href="#" id="skip-setup">Skip — I'll set it up myself</a>`;
      $("#skip-setup").onclick = (e) => {
        e.preventDefault();
        show("done");
        renderDone(false);
      };
      show("handoff");
      return;
    }
    if (phase === "error" || phase === "cancelled") {
      clearInterval(state.poll);
      $("#fail-title").textContent =
        phase === "cancelled" ? "Cancelled" : "That didn't work";
      $("#fail-msg").textContent = st.message || "";
      $("#fail-detail").textContent = st.detail || "";
      show("failed");
      return;
    }

    $("#write-title").textContent = PHASE_TITLE[phase] || "Working";
    $("#write-msg").textContent = st.message || "";
    const fill = $("#fill");
    if ((phase === "write" || phase === "verify-card") && st.total) {
      const pct = Math.min(100, (st.written / st.total) * 100);
      fill.classList.remove("indet");
      fill.style.width = pct.toFixed(1) + "%";
      $("#write-pct").textContent = pct.toFixed(0) + "%";
      $("#write-detail").textContent =
        `${fmtBytes(st.written)} of ${fmtBytes(st.total)} · ` +
        `${(st.rate / 1e6).toFixed(0)} MB/s · ${fmtEta(st.eta)}`;
      $("#write-warn").textContent = phase === "verify-card"
        ? "Reading the card back to make sure it kept what was written."
        : "Leave the card in place until this finishes.";
    } else {
      fill.classList.add("indet");
      $("#write-pct").textContent = "";
      $("#write-detail").textContent = "";
    }
  }, 350);
}

/* ── the second half: card written, box plugged in ──────── */
const TASK_ICON = { waiting: "○", running: "◐", done: "✓", failed: "✕" };

function renderTasks(steps) {
  const el = $("#tasks");
  if (!steps || !steps.length) { el.innerHTML = ""; return; }
  el.innerHTML = steps.map(s => `
    <li class="task ${esc(s.state)}">
      <span class="ic">${TASK_ICON[s.state] || "○"}</span>
      <span class="grow">
        <span class="t">${esc(s.title)}</span>
        <span class="d">${esc(s.detail)}</span>
      </span>
    </li>`).join("");
}

function pollSetup() {
  clearInterval(state.poll);
  const logEl = $("#setup-log");
  state.poll = setInterval(async () => {
    let st;
    try { st = await riparr.setup_status(); } catch (e) { return; }
    const phase = st.phase || "idle";

    renderTasks(st.steps);
    const pct = st.pct || 0;
    $("#setup-fill").style.width = pct.toFixed(1) + "%";
    $("#setup-fill").classList.toggle("indet", phase === "running" && !pct);
    $("#setup-pct").textContent = pct.toFixed(0) + "%";
    $("#setup-detail").textContent = st.address
      ? (st.found_by === "name" ? `${esc(state.hostname)}.local` : st.address)
      : "";
    $("#setup-hint").textContent = st.message || "";

    // Only touch the log when it actually changed — this repaints 3x a second.
    if (st.log && st.log.length !== state.logLen) {
      state.logLen = st.log.length;
      logEl.textContent = st.log.join("\n");
      if ($("#log-reveal").open) logEl.scrollTop = logEl.scrollHeight;
    }

    if (phase === "done") {
      clearInterval(state.poll);
      state.setupUrl = st.url;
      state.elapsed = st.elapsed;
      show("done");
      renderDone(true);
    } else if (phase === "error" || phase === "cancelled") {
      clearInterval(state.poll);
      $("#fail-title").textContent = phase === "cancelled"
        ? "Setup stopped" : "Setup didn't finish";
      $("#fail-msg").textContent = st.message || "";
      $("#fail-detail").textContent = st.detail || "";
      show("failed");
    }
  }, 300);
}

/* ── the finish line ────────────────────────────────────── */
function renderDone(installed) {
  const host = state.hostname;
  const addr = `${host}.local:${state.port}`;

  if (installed) {
    // The only ending worth having: it is running, here is the link.
    $("#done-title").textContent = "Riparr is ready";
    $("#done-lede").textContent = state.elapsed
      ? `Set up in ${fmtDur(state.elapsed)}. Everything below is already done.`
      : "Everything below is already done.";
    $("#done-steps").innerHTML = [
      "Card written and checked",
      "Box found on your network",
      "Riparr installed and running",
    ].map(t => `<li class="was-done">${t}</li>`).join("");
    $("#done-note").innerHTML =
      "Open it to create your login and finish the short first-run wizard. "
      + "You won't need this app again unless you prepare another card.";
    $("#done-actions").innerHTML =
      `<button class="primary" id="open-box">Open ${esc(addr)}</button>`
      + `<button class="ghost" id="again">Prepare another card</button>`;
  } else {
    // The card is written but the box was never set up — the user skipped it.
    $("#done-title").textContent = "Your card is ready";
    $("#done-lede").textContent =
      "Riparr isn't installed on the box yet. Put the card in, power it on, and run "
      + "this app again to finish — or do it yourself over SSH.";
    $("#done-steps").innerHTML = [
      "Slide the card into the box",
      "Plug in the single USB-C cable",
      `Wait about two minutes, then connect over SSH`,
    ].map(t => `<li>${t}</li>`).join("");
    const ssh = state.boot.ssh_config
      ? `ssh -F ${esc(state.boot.ssh_config)} root@${esc(host)}.local`
      : `ssh root@${esc(host)}.local`;
    $("#done-note").innerHTML = `<span class="ssh-line">${ssh}</span>`;
    $("#done-actions").innerHTML =
      `<button class="primary" id="resume-setup">Set it up for me</button>`
      + `<button class="ghost" id="again">Prepare another card</button>`;
  }

  const open = $("#open-box");
  if (open) open.onclick = () => riparr_open(`http://${addr}`);
  const resume = $("#resume-setup");
  if (resume) resume.onclick = () => show("handoff");
  $("#again").onclick = () => { state.disk = null; show("card"); loadDisks(); };
}

function riparr_open(url) { riparr.open_url(url); }

/* ── updates ────────────────────────────────────────────── */
async function checkUpdate() {
  let u;
  try { u = await riparr.check_update(); } catch (e) { return; }
  if (u.status !== "update") return;
  $("#update-slot").innerHTML =
    `<button class="update-pill"><b>Version ${esc(u.version)} available</b>
     You have ${esc(u.current)}</button>`;
  $("#update-slot .update-pill").onclick = () => riparr.open_url(u.url);
}

/* ── wiring ─────────────────────────────────────────────── */
async function init() {
  state.boot = await riparr.boot();
  $("#ver").textContent = state.boot.version;
  $("#acct-pw").textContent = state.boot.password;
  $("#acct-note").textContent = state.boot.password_generated
    ? "Generated just now and saved to user_password.txt in your build folder."
    : "Read from user_password.txt in your build folder.";

  state.port = state.boot.default_port || 9797;
  $("#port").value = state.port;
  renderDisks(state.boot.disks);
  if (state.boot.image_missing) {
    $("#card-hint").textContent =
      "No .img.xz found in the build folder — you can still save settings only.";
  }
  show("card");
  checkUpdate();
}

$("#rescan-disks").onclick = loadDisks;
$("#card-next").onclick = () => { show("wifi"); loadWifi(); };

$("#rescan-wifi").onclick = loadWifi;
/* Manual entry is an inline field, not `prompt()`. A WKWebView without a UI delegate
   silently returns null from prompt(), so this button did nothing whatsoever — and it
   is both the only way to reach a hidden network and the thing the empty state tells
   you to use when the scan finds nothing. app.py now has a delegate too, so the
   failure cannot recur silently somewhere else. */
$("#manual-wifi").onclick = () => {
  $("#wifi-manual-block").classList.remove("hidden");
  $("#wifi-manual").value = "";
  $("#wifi-manual").focus();
};
$("#wifi-manual-cancel").onclick = () => $("#wifi-manual-block").classList.add("hidden");
$("#wifi-manual").onkeydown = (e) => { if (e.key === "Enter") $("#wifi-manual-ok").click(); };
$("#wifi-manual-ok").onclick = () => {
  const ssid = $("#wifi-manual").value.trim();
  if (!ssid) { $("#wifi-manual").focus(); return; }
  $("#wifi-manual-block").classList.add("hidden");
  $$("#wifi-list .item").forEach(n => n.classList.remove("sel"));
  pickNet({ ssid, bands: [], rssi: null, secure: true, pi_ok: true, hidden: true,
            saved: true });
};

/* The most expensive mistake this tool allows is a mistyped Wi-Fi password: nothing
   detects it, the card writes perfectly, the box boots perfectly and never appears —
   and the only fix is to write the card again. The correct passphrase is usually
   sitting in the keychain of the Mac running this. */
$("#wifi-keychain").onclick = async () => {
  const btn = $("#wifi-keychain"), note = $("#wifi-keychain-note");
  if (!state.net) return;
  btn.disabled = true;
  note.className = "micro";
  note.textContent = "Asking your keychain…";
  let r;
  try { r = await riparr.keychain_password(state.net.ssid); }
  catch (e) { r = { ok: false, error: String(e) }; }
  btn.disabled = false;
  if (!r.ok) {
    note.className = "micro warn";
    note.textContent = r.error || "Couldn't get it.";
    return;
  }
  $("#wifi-pw").value = r.password;
  state.wifiPw = r.password;
  note.className = "micro good";
  note.textContent = "Filled in from this Mac's keychain.";
  updateWifiNext();
};
$("#wifi-pw").oninput = (e) => { state.wifiPw = e.target.value; updateWifiNext(); };
$("#wifi-pw").onkeydown = (e) => {
  if (e.key === "Enter" && !$("#wifi-next").disabled) $("#wifi-next").click();
};
$("#wifi-next").onclick = () => show("name");

let hostCheckToken = 0;

async function refreshHostPreview() {
  const v = state.hostname, okName = validHost(v);
  const { ok, message } = await riparr.check_port(state.port);
  $("#host-preview").innerHTML = okName && ok
    ? `Reachable at <b>http://${esc(v)}.local:${esc(state.port)}</b>`
    : `<span style="color:var(--danger)">${
        !okName ? "Lowercase letters, digits and hyphens only" : esc(message)}</span>`;

  // Debounced, and never blocking: a name already in use is a warning, not an error.
  // Someone re-flashing the card for the box that is currently answering will see this
  // and should absolutely be allowed to carry on.
  const token = ++hostCheckToken;
  $("#name-taken").textContent = "";
  if (okName) {
    let t;
    try { t = await riparr.name_taken(v); } catch (e) { t = { taken: false }; }
    if (token === hostCheckToken && t.taken) {
      $("#name-taken").innerHTML =
        `Something already answers to <b>${esc(t.name)}</b> at ${esc(t.address)}. `
        + `If that is a different box, pick another name — otherwise they will fight `
        + `over it and one will quietly become <b>${esc(v)}-2.local</b>. `
        + `If it is the box you are re-flashing, carry on.`;
    }
  }
  $("#port-note").textContent = ok && message
    ? message
    : "9797 is Riparr's own port, chosen to sit alongside Radarr on 7878 and Sonarr on "
      + "8989. Change it only if something already uses it.";
  $("#name-next").disabled = !(okName && ok);
}

$("#port").oninput = (e) => {
  state.port = e.target.value.trim();
  refreshHostPreview();
};

$("#hostname").oninput = (e) => {
  state.hostname = e.target.value.trim().toLowerCase();
  refreshHostPreview();
};
$("#name-next").onclick = async () => { show("review"); await buildReview(); };

$("#toml-only").onclick = async () => {
  const r = await riparr.save_toml_only(cfg());
  $("#fail-title").textContent = "Settings saved";
  $("#fail-msg").textContent =
    "custom.toml was written to your build folder. Copy it onto the boot partition " +
    "of a card that is already flashed.";
  $("#fail-detail").textContent = r.path;
  $(".tick.bad") && $(".tick.bad").classList.remove("bad");
  show("failed");
};

$("#do-write").onclick = async () => {
  show("write");
  $("#fill").classList.add("indet");
  $("#write-title").textContent = "Waiting for permission";
  $("#write-msg").textContent = "macOS will ask for your password.";
  const r = await riparr.start_write(cfg());
  if (!r.ok) {
    $("#fail-title").textContent = "Can't start";
    $("#fail-msg").textContent = r.error;
    $("#fail-detail").textContent = "";
    show("failed");
    return;
  }
  pollWrite();
};

$("#begin-setup").onclick = async () => {
  state.logLen = -1;
  $("#setup-log").textContent = "";
  $("#setup-fill").style.width = "0%";
  renderTasks(null);
  $("#setup-hint").textContent = "";
  show("setup");
  state.triedSetup = true;
  const r = await riparr.start_setup({ hostname: state.hostname, port: state.port });
  if (!r.ok) {
    $("#fail-title").textContent = "Can't start setup";
    $("#fail-msg").textContent = r.error;
    $("#fail-detail").textContent = "";
    show("failed");
    return;
  }
  pollSetup();
};

$("#setup-cancel").onclick = async () => {
  $("#setup-cancel").disabled = true;
  $("#setup-hint").textContent = "Stopping…";
  await riparr.cancel_setup();
};

$("#verify-card").onchange = (e) => { state.verify = e.target.checked; };
$("#retry").onclick = () => {
  // A setup that failed leaves a perfectly good card in a running box. Sending the
  // user back to "choose a card and erase it" would be actively wrong, so retry means
  // "try the setup again" whenever that is what failed.
  if (state.screen === "failed" && state.triedSetup) { show("handoff"); return; }
  show("card"); loadDisks();
};
$$("[data-back]").forEach(b => b.onclick = () => show(b.dataset.back));

init();
