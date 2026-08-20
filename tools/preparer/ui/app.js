/* Riparr Preparer — screen flow and bridge calls.
   window.riparr.<method>(...) is injected by app.py and returns a Promise. */

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  boot: null,
  disk: null,
  net: null,          // {ssid, secure, hidden, bands}
  wifiPw: "",
  hostname: "riparr",
  screen: "card",
  poll: null,
};

const ORDER = ["card", "wifi", "name", "review", "write"];

function show(name) {
  state.screen = name;
  $$(".screen").forEach(s => s.classList.toggle("on", s.dataset.screen === name));
  const idx = ORDER.indexOf(name);
  $$("#steps li").forEach(li => {
    const i = ORDER.indexOf(li.dataset.step);
    li.classList.toggle("active", i === idx);
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
    el.innerHTML = `<div class="empty">No SD card found.<br>
      Insert one and choose <b>Rescan</b>.</div>`;
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
      if (n.bands.includes("2.4") && n.bands.length > 1) tags.push(`<span class="tag good">2.4 + 5 GHz</span>`);
      else if (n.bands.includes("2.4")) tags.push(`<span class="tag good">2.4 GHz</span>`);
      else tags.push(`<span class="tag warn">band unknown</span>`);
      if (n.saved && !n.seen) tags.push(`<span class="tag">saved, out of range</span>`);
      else if (n.saved) tags.push(`<span class="tag">saved</span>`);
      if (!n.secure) tags.push(`<span class="tag warn">open</span>`);
    }
    const sub = n.pi_ok ? tags.join("")
      : `<span class="sub">5 GHz only — the Pi Zero 2W has no radio for this</span>`;
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
  if (needsPw) $("#wifi-pw").focus();
  updateWifiNext();
  $("#wifi-hint").textContent = (!n.bands || !n.bands.length)
    ? "Band unknown — if this is 5 GHz the box will not connect." : "";
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
    user: "riparr",
    password: state.boot.password,
    ssid: state.net.ssid,
    wifi_pw: state.wifiPw,
    secure: state.net.secure !== false,
    hidden: !!state.net.hidden,
    country: state.boot.country,
    timezone: state.boot.timezone,
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
    ["Reachable at", `http://${esc(c.hostname)}.local`],
    ["System account", `${esc(c.user)} · password saved in user_password.txt`],
    ["SSH", state.boot.has_key ? "enabled · key + password" : "enabled · password"],
    ["Region", `${esc(c.country)} · ${esc(c.timezone)}`],
  ];
  $("#summary").innerHTML = rows
    .map(([k, v]) => `<div class="r"><div class="k">${k}</div><div class="v">${v}</div></div>`)
    .join("");
  const t = await riparr.preview_toml(c);
  $("#toml").textContent = t.toml;
}

/* ── step 5: write ──────────────────────────────────────── */
const PHASE_TITLE = {
  auth: "Waiting for permission",
  unmount: "Preparing the card",
  write: "Writing your card",
  flush: "Finishing the write",
  mount: "Almost there",
  provision: "Applying your settings",
  eject: "Ejecting",
};

function fmtBytes(n) { return (n / 1e9).toFixed(2) + " GB"; }
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
      $("#done-url").textContent = `${state.hostname}.local`;
      show("done");
      $$("#steps li").forEach(li => li.classList.add("done"));
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
    if (phase === "write" && st.total) {
      const pct = Math.min(100, (st.written / st.total) * 100);
      fill.classList.remove("indet");
      fill.style.width = pct.toFixed(1) + "%";
      $("#write-pct").textContent = pct.toFixed(0) + "%";
      $("#write-detail").textContent =
        `${fmtBytes(st.written)} of ${fmtBytes(st.total)} · ` +
        `${(st.rate / 1e6).toFixed(0)} MB/s · ${fmtEta(st.eta)}`;
    } else {
      fill.classList.add("indet");
      $("#write-pct").textContent = "";
      $("#write-detail").textContent = "";
    }
  }, 350);
}

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
$("#manual-wifi").onclick = () => {
  const ssid = prompt("Network name (SSID)");
  if (!ssid) return;
  $$("#wifi-list .item").forEach(n => n.classList.remove("sel"));
  pickNet({ ssid, bands: [], rssi: null, secure: true, pi_ok: true, hidden: true });
};
$("#wifi-pw").oninput = (e) => { state.wifiPw = e.target.value; updateWifiNext(); };
$("#wifi-pw").onkeydown = (e) => {
  if (e.key === "Enter" && !$("#wifi-next").disabled) $("#wifi-next").click();
};
$("#wifi-next").onclick = () => show("name");

$("#hostname").oninput = (e) => {
  const v = e.target.value.trim().toLowerCase();
  state.hostname = v;
  const okName = validHost(v);
  $("#host-preview").innerHTML = okName
    ? `Reachable at <b>http://${esc(v)}.local</b>`
    : `<span style="color:var(--danger)">Lowercase letters, digits and hyphens only</span>`;
  $("#name-next").disabled = !okName;
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

$("#open-box").onclick = () => riparr.open_url(`http://${state.hostname}.local`);
$("#again").onclick = () => { state.disk = null; show("card"); loadDisks(); };
$("#retry").onclick = () => { show("card"); loadDisks(); };
$$("[data-back]").forEach(b => b.onclick = () => show(b.dataset.back));

init();
