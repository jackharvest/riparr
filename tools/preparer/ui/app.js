/* Riparr Preparer — screen flow and bridge calls.
   window.riparr.<method>(...) is injected by app.py and returns a Promise. */

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  boot: null,
  verify: true,
  disk: null,
  board: null,        // selected board id
  boardImage: null,   // the downloaded OS image for that board, or null
  net: null,          // {ssid, secure, hidden, bands}
  wifiPw: "",
  hostname: "riparr",
  port: 9797,
  screen: "welcome",
  poll: null,
  logLen: -1,        // so the first poll always paints the log
  setupUrl: null,
  elapsed: null,
  route: "card",     // "card" = write then set up · "connect" = set up only
  allowOther: false, // the user revealed and chose a device we classified as a drive
};

// Two routes, two step lists. Someone who came in to set up a box that already has a
// card should not be shown a card-writing checklist they are never going to do.
const ROUTES = {
  card:    [["card", "SD card"], ["wifi", "Wi-Fi"], ["name", "Name & account"],
            ["review", "Review"], ["write", "Write"], ["setup", "Set up"]],
  connect: [["connect", "Find the box"], ["setup", "Set up"]],
};
let ORDER = ROUTES.card.map(s => s[0]);

function setRail(route) {
  const el = $("#steps");
  if (!route) { el.innerHTML = ""; ORDER = []; return; }   // welcome: no checklist yet
  state.route = route;
  ORDER = ROUTES[route].map(s => s[0]);
  el.innerHTML = ROUTES[route].map(([id, label]) =>
    `<li data-step="${id}"><span class="dot"></span>${esc(label)}</li>`).join("");
}

// Screens that are not themselves rail steps still have a place in the rail. Without
// this, reaching the handoff or the finish line clears every tick the user just
// earned, because indexOf returns -1 and nothing counts as behind it.
const RAIL_AS = { handoff: "setup", failed: null, done: "__all__", welcome: null };

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
/* Two lists, not one filtered list. core.classify_disk decides which -- the SCSI
   removable-medium bit, the IOKit media icon, and a Rufus-style score over the device
   name. Cards go in the main list; anything that reads like an external drive goes
   behind a reveal, because hiding it outright just produces someone who is certain
   they inserted a card and cannot see it. */

/* A 4 TB backup drive shown as "4000 GB" reads like a typo, and this list now
   deliberately contains devices that large. */
function capLabel(gb) {
  return gb >= 1000 ? (gb / 1000).toFixed(gb % 1000 ? 1 : 0) + " TB" : gb + " GB";
}

function diskRow(d, i, other) {
  const adv = d.advice || {};
  const tag = other
    ? `<span class="tag warn">${esc(d.kind_label)}</span>`
    : (d.kind === "sd" ? `<span class="tag good">SD card</span>`
                       : `<span class="tag">${esc(d.kind_label)}</span>`);
  return `<div class="item" data-i="${i}" data-other="${other ? 1 : 0}">
      <div class="grow">
        <div class="title">${esc(d.name || "SD card")}${tag}</div>
        <div class="sub">/dev/${esc(d.id)}${d.protocol ? " · " + esc(d.protocol) : ""}
          · ${esc(d.why)}</div>
      </div>
      <div class="right">
        <div class="cap">${capLabel(d.size_gb)}</div>
        <div class="verdict">${esc(adv.headline || "")}</div>
      </div>
    </div>`;
}

function renderDisks(disks) {
  disks = disks || [];
  const cards = disks.filter(d => d.is_card);
  const other = disks.filter(d => !d.is_card);
  const el = $("#disk-list");

  if (!cards.length) {
    // Saying only "insert one" is unhelpful to the person who *has*. Now that the list
    // is classified rather than capped, the reasons are different from before -- and
    // one of them is that their card is sitting in the other list.
    el.innerHTML = `<div class="empty">No SD card found.<br>
      Insert one and choose <b>Rescan</b>.
      <div class="empty-why">Already inserted? ${other.length
        ? `${other.length} removable ${other.length === 1 ? "disk was" : "disks were"}
           found but ${other.length === 1 ? "did" : "did"} not identify
           ${other.length === 1 ? "itself" : "themselves"} as
           ${other.length === 1 ? "a card" : "cards"} — open
           <b>Show other removable disks</b> below and check.`
        : `Some readers report themselves as fixed disks rather than card readers. Try a
           different reader, or a direct slot if your Mac has one.`}</div></div>`;
  } else {
    el.innerHTML = cards.map((d, i) => diskRow(d, i, false)).join("");
  }

  $("#other-disks-wrap").classList.toggle("hidden", !other.length);
  $("#other-disks-sum").textContent = other.length === 1
    ? "Show 1 other removable disk" : `Show ${other.length} other removable disks`;
  $("#other-list").innerHTML = other.map((d, i) => diskRow(d, i, true)).join("");

  const pick = (d, isOther) => {
    $$("#disk-list .item, #other-list .item").forEach(n => n.classList.remove("sel"));
    state.disk = d;
    state.allowOther = isOther;
    updateCardNext();
    const adv = d.advice || {};
    $("#card-hint").innerHTML = isOther
      ? `<b class="warn">/dev/${esc(d.id)} is not a card.</b> Everything on it will be
         erased. Make sure this is not a drive with your files on it.`
      : `Everything on /dev/${esc(d.id)} will be erased.` +
        (adv.detail ? ` <span class="dim">${esc(adv.detail)}</span>` : "");
  };

  // pick() clears .sel across both lists, so the highlight has to go on *after* it,
  // not before -- otherwise the row is selected in state and unmarked on screen.
  $$("#disk-list .item").forEach((node, i) => {
    node.onclick = () => { pick(cards[i], false); node.classList.add("sel"); };
  });
  $$("#other-list .item").forEach((node, i) => {
    node.onclick = () => { pick(other[i], true); node.classList.add("sel"); };
  });

  if (!cards.length) {
    state.disk = null;
    updateCardNext();
    $("#card-hint").textContent = "";
  } else if (cards.length === 1) {
    // Auto-select a single *card*. Never auto-select out of the other list: picking
    // someone's backup drive for them is exactly the mistake this split prevents.
    $("#disk-list .item").click();
  }
}

function renderGuide(rows) {
  if (!rows || !rows.length) return;
  $("#guide-rows").innerHTML = rows.map(r => `
    <div class="g-row">
      <div class="g-size">${esc(r.label)}</div>
      <div class="g-body">
        <div class="g-head">${esc(r.headline)}</div>
        <div class="g-detail">${esc(r.detail)}</div>
      </div>
      <div class="g-stage">${r.staging_gib} GiB<small>staging</small></div>
    </div>`).join("");
}

async function loadDisks() {
  $("#disk-list").innerHTML = `<div class="empty">Looking for cards…</div>`;
  const r = await riparr.refresh_disks();
  renderDisks(r.disks);
}

/* ── the hardware picker ─────────────────────────────────
   The board decides which OS image gets written; provisioning is the same across the
   supported boards (writer.py branches on the card's layout, not on the board). So this
   dropdown is really "which operating system", and its job is to make sure the right one
   is downloaded before the write. See docs/design/board-support.md. */
function renderBoards() {
  const boards = state.boot.boards || [];
  if (!boards.length) { $("#hardware").classList.add("hidden"); return; }
  const sel = $("#board-select");
  sel.innerHTML = boards.map(b =>
    `<option value="${esc(b.id)}">${esc(b.name)} — ${esc(b.soc)}</option>`).join("");
  state.board = state.boot.default_board || boards[0].id;
  sel.value = state.board;
  sel.onchange = () => selectBoard(sel.value);
  selectBoard(state.board);
}

function boardById(id) {
  return (state.boot.boards || []).find(b => b.id === id);
}

function selectBoard(id) {
  state.board = id;
  const b = boardById(id);
  if (!b) return;
  const badge = b.tier === "verified"
    ? `<span class="tag good">tested</span>`
    : `<span class="tag warn">beta</span>`;
  $("#board-info").innerHTML =
    `${badge} <b>${esc(b.ram)} RAM</b> · ${esc(b.note || "")}`
    + (b.ram_warn ? `<div class="micro warn">${esc(b.ram_warn)}</div>` : "")
    + (b.tier !== "verified"
        ? `<div class="micro">Beta: this board should work but hasn't been confirmed on
           real hardware yet — you'd be helping confirm it.</div>` : "");
  refreshBoardImage();
}

/* Continue used to be enabled by picking a disk alone, so a failed OS download left the
   user free to walk through Wi-Fi and hostname and only discover at the end that there
   was nothing to write. Both facts are required, and the one that is missing is named. */
function updateCardNext() {
  const btn = $("#card-next");
  if (!btn) return;
  const why = !state.disk ? "Choose the card to write to."
            : !state.boardImage ? "Download the operating system for this board first."
            : "";
  btn.disabled = !!why;
  const note = $("#card-blocked");
  if (note) { note.textContent = why; note.hidden = !why; }
}

async function refreshBoardImage() {
  const box = $("#board-image");
  box.innerHTML = `<div class="micro">Checking for the OS image…</div>`;
  let img = null;
  try { img = (await riparr.board_image(state.board)).image; } catch (e) { /* offline */ }
  state.boardImage = img;
  updateCardNext();
  const b = boardById(state.board);
  if (img) {
    // "just stopped at 100%" is not a completion. Say it finished, say it was checked
    // against the vendor's checksum -- which is the part that took the last few seconds
    // -- and make it look different from a bar that has stalled.
    box.innerHTML =
      `<div class="hw-ready ok">
         <span class="tick" aria-hidden="true">\u2713</span>
         <span class="grow"><b>Operating system ready</b>
           <span class="micro">${esc(img.name)}</span>
           <span class="micro dim">Downloaded and checked against the vendor's
             published checksum.</span></span>
       </div>`;
    updateCardNext();
    return;
  }
  box.innerHTML =
    `<button class="ghost" id="os-download">Download the ${esc(b ? b.name : "board")} OS</button>
     <span class="micro">Fetched from ${b && b.os === "raspios"
        ? "raspberrypi.com" : "armbian.com"} and checked against its published checksum.</span>
     <div id="os-progress"></div>`;
  $("#os-download").onclick = downloadOS;
}

async function downloadOS() {
  const b = boardById(state.board);
  $("#os-download").disabled = true;
  const prog = $("#os-progress");
  prog.innerHTML =
    `<div class="track slim"><div class="fill indet" id="os-fill"></div></div>
     <div class="micro" id="os-detail">Starting…</div>`;
  const started = state.board;
  let r;
  try { r = await riparr.download_image(state.board); }
  catch (e) { prog.innerHTML = `<div class="micro warn">${esc(e.message)}</div>`; return; }
  if (!r.ok) {
    $("#os-download").disabled = false;
    prog.innerHTML = `<div class="micro warn">${esc(r.error || "Couldn't start.")}</div>`;
    return;
  }
  clearInterval(state.poll);
  state.poll = setInterval(async () => {
    // A board change mid-download abandons this poller; the download itself keeps going.
    if (state.board !== started) { clearInterval(state.poll); return; }
    let st;
    try { st = await riparr.image_status(); } catch (e) { return; }
    const phase = st.phase || "idle";
    if (phase === "done") {
      clearInterval(state.poll);
      refreshBoardImage();
      return;
    }
    if (phase === "error") {
      clearInterval(state.poll);
      $("#os-download").disabled = false;
      prog.innerHTML = `<div class="micro warn">${esc(st.message || "The download failed.")}</div>`
        + (st.detail ? `<div class="micro">${esc(st.detail)}</div>` : "");
      return;
    }
    const fill = $("#os-fill");
    const detail = $("#os-detail");
    if (st.total && fill) {
      const pct = Math.min(100, (st.done / st.total) * 100);
      fill.classList.remove("indet");
      fill.style.width = pct.toFixed(1) + "%";
      // The last bytes are not the last work: the checksum comparison and the move into
      // place happen after the stream ends. Without this the bar reaches 100% and sits
      // there with no explanation, which reads as a hang.
      if (detail) detail.textContent = pct >= 99.95
        ? "Checking the download against the vendor's checksum…"
        : `${fmtBytes(st.done)} of ${fmtBytes(st.total)} · ${pct.toFixed(0)}%`;
    } else if (detail) {
      detail.textContent = `${fmtBytes(st.done || 0)} downloaded…`;
    }
  }, 500);
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

function paintWifi(r) {
  renderNets(r.networks, r.method);
  const usable = r.networks.filter(n => n.pi_ok).length;
  $("#wifi-hint").textContent = r.method === "live"
    ? `${r.networks.length} networks · ${usable} usable`
    : r.method === "none" ? "Couldn't scan — enter the name manually."
    : `${r.networks.length} saved networks · band unknown`;

  // Only ask when asking would change anything. Granted, or a platform where the
  // question does not arise, and the offer never appears.
  const d = r.detail || {};
  const offer = $("#wifi-detail-offer");
  const note = $("#wifi-detail-note");
  const askable = d.available && !d.granted && d.why === "notDetermined";
  const refused = d.available && !d.granted && d.why !== "notDetermined";
  if (offer) offer.hidden = !askable;
  if (note) {
    note.hidden = !refused;
    if (refused) note.innerHTML =
      `Location Services is off for this app, so band and signal cannot be shown. ` +
      `You can still pick or type your network. To turn it on: <b>System Settings → ` +
      `Privacy &amp; Security → Location Services → Riparr Preparer</b>.`;
  }
}

async function loadWifi() {
  $("#wifi-list").innerHTML = `<div class="empty">Scanning…</div>`;
  paintWifi(await riparr.scan_wifi());
}

async function enableWifiDetail() {
  const btn = $("#wifi-detail-btn");
  if (btn) { btn.disabled = true; btn.textContent = "Waiting for permission…"; }
  $("#wifi-list").innerHTML = `<div class="empty">Scanning…</div>`;
  let r;
  try { r = await riparr.enable_wifi_detail(); }
  catch (e) { r = null; }
  if (btn) { btn.disabled = false; btn.textContent = "Show bands and signal"; }
  if (r) paintWifi(r); else loadWifi();
}

/* ── step 3: name ───────────────────────────────────────── */
function validHost(v) { return /^[a-z0-9][a-z0-9-]{0,62}$/.test(v); }

/* ── step 4: review ─────────────────────────────────────── */
function cfg() {
  return {
    disk: state.disk ? state.disk.id : null,
    // The image is the one downloaded for the selected board, not just whatever is newest
    // in the folder — with several boards supported, "newest .img.xz" is the wrong one.
    image: state.boardImage ? state.boardImage.path : null,
    board: state.board,
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
    // Set only by choosing something out of "other removable disks". core.validate_disk
    // refuses a non-card without it, so this cannot be reached by accident.
    allow_other: state.allowOther,
  };
}

async function buildReview() {
  const c = cfg();
  const img = state.boardImage;
  const rows = [
    [state.allowOther ? "Disk" : "Card", state.disk
      ? `${esc(state.disk.name)} · /dev/${esc(state.disk.id)} · ${capLabel(state.disk.size_gb)}`
        + (state.allowOther
            ? ' <span class="tag warn">not identified as a card</span>'
            : (state.disk.advice && state.disk.advice.headline
                ? ` <span class="tag">${esc(state.disk.advice.headline)}</span>` : ""))
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
    // Say where it came from. "Copied onto the card" on its own reads as though Riparr
    // ships MakeMKV, and it does not: nothing is redistributed and no release asset
    // contains it. The app downloads the official tarballs from makemkv.com into your
    // build folder, checked against the pinned hashes in packaging/makemkv-manifest.json,
    // and the write copies them across so the box does not have to fetch them itself.
    ["MakeMKV", state.boot.makemkv
      ? 'copied from your build folder <span class="tag">your own download from makemkv.com</span>'
      : '<span class="tag warn">not downloaded yet — nothing will be copied</span>'],
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
    "The OS image for this board hasn't been downloaded yet, so there is nothing to "
    + "write. Go back to the <b>SD card</b> step and choose <b>Download the OS</b>. You "
    + "can still save the settings file and copy it onto a card that is already flashed.";

  // Named here as well as refused in start_write, because the useful moment is before
  // somebody commits to the red button -- and it is a two-minute fix they can go and do.
  if (haveImage) {
    let tools = { missing: [] };
    try { tools = await riparr.check_tools(img.path); } catch (e) { /* non-fatal */ }
    if (tools.missing && tools.missing.length) {
      $("#do-write").disabled = true;
      $("#review-warn").innerHTML =
        "This Mac is missing " + tools.missing.map(m => `<b>${esc(m.tool)}</b>`).join(" and ")
        + ", which " + (tools.missing.length === 1 ? "is" : "are") + " needed to write a "
        + "card — neither ships with macOS. Install "
        + (tools.missing.length === 1 ? "it" : "them") + ", then choose Rescan on the "
        + "SD card step:<br>"
        + tools.missing.map(m => `<span class="ssh-line">${esc(m.fix)}</span>`).join(" ");
    }
  }

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
      // into the box — and let the network, not a promise, say when it has happened.
      show("handoff");
      enterHandoff();
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

/* ── handoff: the half only a person can do ──────────────
   Between writing the card and setting the box up sits a physical step no software can
   perform: somebody has to carry a card across a room. The old screen handled that with
   a button meaning "I promise I did it", which people pressed on the way to doing it —
   and then setup ran against a box that was not there yet.

   So the app asks the network instead. `name_taken` is the same mDNS question the naming
   step asks; here the answer means the box has booted and joined the Wi-Fi, which is
   exactly the condition setup needs. The button is not what decides the box is ready.

   The escape hatch matters as much as the detection. Plenty of networks block mDNS
   between clients, and on those a perfectly healthy box will never answer — so after a
   grace period the button unlocks regardless. A gate that cannot be opened is worse than
   no gate. */
const HANDOFF_GRACE_MS = 40000;

function stopHandoff() {
  if (state.handoffPoll) { clearInterval(state.handoffPoll); state.handoffPoll = null; }
}

async function enterHandoff() {
  const host = (state.hostname || "riparr").toLowerCase();

  // The receipt. Naming what is on the card is what makes the left column read as
  // finished work rather than as decoration around a button.
  const rows = [
    ["Card", state.disk ? esc(state.disk.name) : "written and checked"],
    ["Reachable at", `${esc(host)}.local:${state.port}`],
    ["Wi-Fi", state.net ? esc(state.net.ssid) : "—"],
    ["MakeMKV", state.boot && state.boot.makemkv
      ? "copied on" : `<span class="tag warn">not included</span>`],
  ];
  $("#handoff-recap").innerHTML = rows
    .map(([k, v]) => `<div class="r"><div class="k">${k}</div><div class="v">${v}</div></div>`)
    .join("");

  $("#handoff-skip").innerHTML =
    `<a href="#" id="skip-setup">Skip — I'll set it up myself</a>`;
  $("#skip-setup").onclick = (e) => {
    e.preventDefault();
    stopHandoff();
    show("done");
    renderDone(false);
  };

  const btn    = $("#begin-setup");
  const pulse  = $("#handoff-pulse");
  const status = $("#handoff-status");
  const sub    = $("#handoff-sub");

  btn.disabled = true;
  btn.textContent = "Waiting for the box…";
  pulse.className = "pulse";
  status.textContent = "Waiting for you to plug it in";
  sub.textContent = "Nothing to click yet — the card has to move first.";

  const unlock = (label, note) => {
    btn.disabled = false;
    btn.textContent = label;
    if (note) sub.textContent = note;
  };

  // One probe before the card can possibly be in the box. If something already answers
  // to this name — an older box still running, or a plain collision — then finding it
  // later proves nothing, so say so and hand the decision back rather than unlocking on
  // the strength of somebody else's mDNS record.
  let preexisting = false;
  try { const p = await riparr.name_taken(host); preexisting = !!(p && p.taken); }
  catch (e) { preexisting = false; }

  if (preexisting) {
    status.textContent = `Something already answers to ${host}.local`;
    sub.textContent = "So this app can't tell the new box apart from it. Plug yours in, " +
                      "then continue.";
    unlock("It's plugged in — continue");
    return;
  }

  const began = Date.now();
  let found = false, looking = false;

  stopHandoff();
  state.handoffPoll = setInterval(async () => {
    if (found) return;
    const waited = Date.now() - began;

    // A beat before the app starts claiming to look for anything. Announcing a search
    // for a box the user has not stood up yet is noise dressed as progress.
    if (!looking && waited > 6000) {
      looking = true;
      pulse.className = "pulse looking";
      status.textContent = `Listening for ${host}.local`;
    }
    if (!looking) return;

    let r = null;
    try { r = await riparr.name_taken(host); } catch (e) { r = null; }
    if (r && r.taken) {
      found = true;
      stopHandoff();
      pulse.className = "pulse found";
      status.textContent = `${host}.local is answering`;
      unlock("Set it up now", "The box is up. The rest is automatic.");
      return;
    }

    sub.textContent = waited < 120000
      ? "First start takes a couple of minutes — it resizes the card and joins your Wi-Fi."
      : "Still nothing. Check the USB-C cable, and that the card is seated.";

    if (btn.disabled && waited > HANDOFF_GRACE_MS) {
      unlock("It's plugged in — continue");
    }
  }, 2000);
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
      // step_verify already established which address answers from *this* Mac and
      // publishes both plus the verdict. Trust it: where mDNS does not cross to the
      // box (guest VLANs, mesh routers with the relay off, multicast-blocked wifi)
      // setup still succeeds via the IP, and .local is a dead link.
      state.setupUrl = (st.by_name === false && st.url_address) || st.url
                       || st.url_address || null;
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
  // Display and open the address that was actually verified, not an assumed name.
  const addr = (state.setupUrl || `http://${host}.local:${state.port}`)
                 .replace(/^https?:\/\//, "");

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
    // This used to say "run this app again to finish", which was true only in the
    // sense that the app would reopen -- there was no way back to the setup half from
    // a fresh start. There is now, and it is worth naming so the promise is real.
    $("#done-lede").textContent =
      "Riparr isn't installed on the box yet. Put the card in and power it on, then "
      + "come back to this and choose \u201cSet up a box that already has a card\u201d "
      + "\u2014 or do it yourself over SSH.";
    $("#done-steps").innerHTML = [
      "Slide the card into the box",
      "Plug in the single USB-C cable",
      `Wait about two minutes, then connect over SSH`,
    ].map(t => `<li>${t}</li>`).join("");
    // Always the explicit -i form. An ssh_config in the build folder is not written
    // by this app and cannot be trusted to fit: a `Host riparr` block does not match
    // the `riparr.local` we print here, so -F silently contributes no key and no
    // known_hosts, and a stale one may still say `User riparr` from the Pi-era image.
    // The card only ever has root, and the key is right there next to it.
    const ssh = `ssh -i ${esc(state.boot.assets)}/riparr_key root@${esc(host)}.local`;
    $("#done-note").innerHTML = `<span class="ssh-line">${ssh}</span>`;
    $("#done-actions").innerHTML =
      `<button class="primary" id="resume-setup">Set it up for me</button>`
      + `<button class="ghost" id="again">Prepare another card</button>`;
  }

  const open = $("#open-box");
  if (open) open.onclick = () => riparr_open(`http://${addr}`);
  const resume = $("#resume-setup");
  if (resume) resume.onclick = () => show("handoff");
  $("#again").onclick = () => {
    state.disk = null; state.allowOther = false;
    setRail(null); show("welcome");
  };
}

function riparr_open(url) { riparr.open_url(url); }

/* ── the setup half, entered on its own ─────────────────── */
/* start_setup() only ever needed a hostname, a port and the SSH key sitting in the
   build folder. None of those come from writing a card, so this route is a way in to
   work that already existed rather than a second implementation of it. */

function connectPreview() {
  const h = ($("#connect-host").value || "").trim().toLowerCase() || "riparr";
  const p = parseInt($("#connect-port").value, 10) || 9797;
  $("#connect-preview").innerHTML = `Looking for <b>${esc(h)}.local</b>`;
  const ok = validHost(h);
  $("#connect-next").disabled = !ok || !state.boot.can_setup;
  $("#connect-warn").innerHTML = state.boot.can_setup ? (ok ? "" :
      "Names can use letters, numbers and hyphens only.")
    : "The SSH key <b>riparr_key</b> is missing from your build folder, so there is no "
      + "way to get into the box. Use the build folder that wrote the card, or prepare "
      + "a card again.";
  const ssh = `ssh -i ${state.boot.assets}/riparr_key root@${h}.local`;
  $("#connect-ssh").textContent = ssh;
  return { host: h, port: p, ok };
}

/* Cheap and worth doing: mDNS either answers or it does not, and knowing which before
   committing to a ten-minute setup turns "it failed" into "it is not there yet". */
async function connectProbe() {
  const { host } = connectPreview();
  $("#connect-found").textContent = "";
  let r;
  try { r = await riparr.name_taken(host); } catch (e) { return; }
  $("#connect-found").className = "micro " + (r.taken ? "good" : "");
  $("#connect-found").textContent = r.taken
    ? `${host}.local is answering at ${r.address}.`
    : `Nothing is answering to ${host}.local yet — that is normal if you have just `
      + `plugged it in, and the sweep below will find it anyway.`;
}

async function startConnect() {
  const { host, port, ok } = connectPreview();
  if (!ok) return;
  state.hostname = host;
  state.port = port;
  setRail("connect");
  const r = await riparr.start_setup({ hostname: host, port: port });
  if (!r.ok) {
    $("#connect-warn").textContent = r.error || "Setup could not be started.";
    return;
  }
  state.logLen = -1;
  $("#setup-title").textContent = "Setting up your Riparr";
  show("setup");
  pollSetup();
}

/* ── updates ────────────────────────────────────────────── */
/* Updating is one click and then nothing. The app downloads the release for this
   operating system, checks it against the published checksum, replaces itself and
   starts again -- the window closing and reopening is the update finishing.

   Opening the releases page in a browser, which is what this used to do, is not an
   update. It is homework. The link survives only for the case where this copy genuinely
   cannot replace itself: a source checkout, or a release with no build for this
   platform. */
async function checkUpdate() {
  if (!(state.boot.prefs || {}).auto_check_updates) return;
  let u;
  try { u = await riparr.check_update(); } catch (e) { return; }
  if (u.status !== "update") return;

  const slot = $("#update-slot");
  slot.innerHTML =
    `<button class="update-pill"><b>Version ${esc(u.version)} available</b>
     ${u.can_install ? "Click to update" : "You have " + esc(u.current)}</button>`;

  if (!u.can_install) {
    slot.querySelector(".update-pill").onclick = () => riparr.open_url(u.url);
    if (u.why_not) slot.querySelector(".update-pill").title = u.why_not;
    return;
  }
  slot.querySelector(".update-pill").onclick = () => runUpdate(u);
}

async function runUpdate(u) {
  const slot = $("#update-slot");
  const paint = (msg, pct) => {
    slot.innerHTML =
      `<div class="update-pill busy"><b>${esc(msg)}</b>
       <span class="update-bar"><i style="width:${pct == null ? 0 : pct}%"></i></span></div>`;
  };
  paint("Starting", 0);

  /* Poll the same way the card write does: the download runs on a thread and the page
     would otherwise sit silent through the largest part of the wait. */
  let polling = true;
  (async function tick() {
    while (polling) {
      let st;
      try { st = await riparr.update_status(); } catch (e) { st = null; }
      if (st && st.phase === "downloading" && st.total) {
        paint(st.message || "Downloading", Math.round((st.done / st.total) * 100));
      } else if (st && st.message) {
        paint(st.message, st.phase === "installing" || st.phase === "restarting" ? 100 : null);
      }
      await new Promise(r => setTimeout(r, 300));
    }
  })();

  let r;
  try { r = await riparr.install_update(); } catch (e) { r = { ok: false, message: String(e) }; }
  polling = false;

  if (r.ok) {
    /* The app is about to be replaced and relaunched under us. Say so and stop -- there
       is deliberately nothing to click, because there is nothing left to do. */
    paint("Restarting into " + (r.version || "the new version"), 100);
    return;
  }
  slot.innerHTML =
    `<button class="update-pill warn"><b>${esc(r.message || "The update failed")}</b>
     ${esc(r.detail || "Nothing was changed.")}</button>`;
  slot.querySelector(".update-pill").onclick = () => riparr.open_url(u.url);
}

/* ── wiring ─────────────────────────────────────────────── */
async function init() {
  state.boot = await riparr.boot();
  $("#ver").textContent = state.boot.version;
  $("#acct-pw").textContent = state.boot.password;
  $("#acct-note").textContent = state.boot.password_generated
    ? "Generated just now — 20 random characters — and saved to your build folder."
    : "Read back from your build folder, so it is the same one this box already has.";
  const file = $("#acct-file");
  if (file && state.boot.assets) file.textContent = state.boot.assets + "/user_password.txt";

  state.port = state.boot.default_port || 9797;
  $("#port").value = state.port;
  $("#connect-port").value = state.port;
  renderDisks(state.boot.disks);
  renderBoards();
  renderGuide(state.boot.size_guide);

  // Card writing runs on macOS, Linux and Windows. Where it does not -- an operating
  // system with no hostos backend -- say so here, before anything is chosen, rather
  // than failing at the last step with a Wi-Fi password entered and possibly a card
  // half written. The setup half works everywhere and is the longer half.
  const host = state.boot.host || {};
  if (host.write_card === false) {
    const card = $("#go-card");
    card.disabled = true;
    card.classList.add("unavailable");
    $("#host-note").textContent = host.write_note;
    $("#host-note").hidden = false;
  }

  // The second route can only work with the private key in the build folder. Offer it
  // greyed with the reason rather than letting someone walk into a dead end.
  if (!state.boot.can_setup) {
    $("#go-connect").disabled = true;
    $("#welcome-note").innerHTML =
      "Setting up an existing box needs the SSH key <b>riparr_key</b> from the build "
      + "folder that wrote its card. It isn't in " + esc(state.boot.assets) + ".";
  }
  connectPreview();

  setRail(null);
  show("welcome");
  const auto = $("#auto-check");
  auto.checked = !!(state.boot.prefs || {}).auto_check_updates;
  auto.onchange = async () => {
    state.boot.prefs = state.boot.prefs || {};
    state.boot.prefs.auto_check_updates = auto.checked;
    await riparr.set_pref("auto_check_updates", auto.checked);
    if (auto.checked) checkUpdate(); else $("#update-slot").innerHTML = "";
  };
  checkUpdate();
}

$("#go-card").onclick = () => { setRail("card"); show("card"); loadDisks(); };
$("#go-connect").onclick = () => {
  setRail("connect"); show("connect"); connectPreview(); connectProbe();
};
$("#connect-host").oninput = connectPreview;
$("#connect-port").oninput = connectPreview;
$("#connect-next").onclick = startConnect;

$("#rescan-disks").onclick = loadDisks;
$("#card-next").onclick = () => { show("wifi"); loadWifi(); };
$("#wifi-detail-btn").onclick = enableWifiDetail;

/* A value you might need and cannot select is a value you have to retype by eye. The
   window disables text selection globally (it is an app, not a page); these few fields
   opt back in, and the button works regardless of that. */
$("#acct-copy").onclick = async () => {
  const btn = $("#acct-copy");
  const pw = ($("#acct-pw").textContent || "").trim();
  if (!pw || pw === "\u2014") return;
  let ok = false;
  try { await navigator.clipboard.writeText(pw); ok = true; }
  catch (e) {
    try {                                    // clipboard API needs a secure context
      const t = document.createElement("textarea");
      t.value = pw; document.body.appendChild(t); t.select();
      ok = document.execCommand("copy");
      t.remove();
    } catch (e2) { ok = false; }
  }
  btn.textContent = ok ? "Copied" : "Press \u2318C";
  setTimeout(() => { btn.textContent = "Copy"; }, 1600);
};

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
    $("#fail-detail").textContent = r.detail || "";
    show("failed");
    return;
  }
  pollWrite();
};

$("#begin-setup").onclick = async () => {
  stopHandoff();
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
  // The setup-only route first: there is no card in that story at all, so the handoff
  // screen ("slide the card into the box") would be answering a question nobody asked.
  if (state.route === "connect") { show("connect"); connectProbe(); return; }
  if (state.screen === "failed" && state.triedSetup) { show("handoff"); return; }
  show("card"); loadDisks();
};
// Going back to the welcome screen unpicks the route as well as the screen, or the
// rail keeps showing a checklist for a flow the user has just stepped out of.
$$("[data-back]").forEach(b => b.onclick = () => {
  if (b.dataset.back === "welcome") setRail(null);
  show(b.dataset.back);
});

/* A window that fails to start must say so. When init() threw -- most often because the
   bridge never arrived -- the promise rejected into nothing and the app sat there as an
   empty coloured rectangle, which reads as "hung" and taught people to quit and reopen
   until it caught. */
function bootDone() {
  const b = document.getElementById("booting");
  if (!b) return;
  b.classList.add("gone");
  setTimeout(() => b.remove(), 220);
}

init().then(bootDone).catch((e) => {
  bootDone();
  const box = document.createElement("div");
  box.style.cssText =
    "position:fixed;inset:0;display:flex;align-items:center;justify-content:center;" +
    "padding:40px;font:14px/1.6 -apple-system,Segoe UI,system-ui,sans-serif;" +
    "color:#cfcbd8;background:#1c1b22;text-align:center;z-index:9999";
  box.innerHTML =
    '<div style="max-width:420px">' +
    '<div style="font-size:17px;color:#fff;margin-bottom:10px">Riparr Preparer could not start</div>' +
    '<div style="color:#8b8598">' + String((e && e.message) || e) + "</div>" +
    '<div style="color:#6d6880;margin-top:16px">Quit and open it again. If it keeps ' +
    "happening, that is a bug worth reporting.</div></div>";
  document.body.appendChild(box);
});
