/* The bridge shim, against the three timings that have actually shipped broken.

   This file exists because the same twelve lines were got wrong twice in two releases,
   in opposite directions, and neither could be caught by looking at the app: the failure
   depends on when pywebview happens to attach its api relative to when this script runs,
   which differs between a checkout and a bundle and between a cold and a warm launch.

   Run with `node bridge-shim.test.js`; CI runs it on every push.

     v0.1.4  listened for `pywebviewready` only. If the event fired first the listener
             attached to something that had already happened, every call queued for ever
             and the window sat empty.
     v0.1.6  polled for `window.pywebview.api` existing. pywebview creates that object
             before it attaches the methods, so it resolved too early and the first call
             failed with "no such bridge method: boot" -- every launch.

   Readiness is a populated surface, and a named method is waited for briefly rather than
   failed instantly. Both of those are asserted below. */
// Exercise bridge-shim.js against the three bridge timings that have actually happened.
const fs = require('fs');
const SHIM = fs.readFileSync(require('path').join(__dirname, 'bridge-shim.js'), 'utf8');

function makeEnv() {
  const listeners = {};
  const win = {
    addEventListener: (n, f) => { (listeners[n] = listeners[n] || []).push(f); },
    fire: (n) => (listeners[n] || []).forEach(f => f()),
  };
  win.document = { documentElement: { classList: { add() {} } } };
  global.window = win;
  global.document = win.document;
  return win;
}

function run(name, setup, expect) {
  const win = makeEnv();
  setup.before && setup.before(win);
  // the shim runs
  new Function(SHIM).call(win);
  setup.after && setup.after(win);
  const t0 = Date.now();
  return win.riparr.boot().then(
    (v) => ({ name, ok: v === 'BOOTED', got: v, ms: Date.now() - t0 }),
    (e) => ({ name, ok: false, got: 'REJECTED: ' + e.message, ms: Date.now() - t0 })
  ).then(r => {
    console.log(`  ${r.ok === expect ? 'PASS' : 'FAIL'}  ${r.name}  (${r.ms}ms) -> ${r.got}`);
    return r.ok === expect;
  });
}

const api = { boot: () => Promise.resolve('BOOTED') };

// Sequentially: each scenario replaces the shared global window, so running them
// concurrently just tests the last one four times.
const cases = [
  // The v0.1.4 bug: the ready event fired before the shim was even parsed.
  ['event already fired, api fully present',
   { before: (w) => { w.pywebview = { api }; } }, true],

  // The v0.1.6 bug: the api OBJECT exists early, its METHODS arrive later.
  ['api object early, methods attached later',
   { before: (w) => { w.pywebview = { api: {} }; },
     after:  (w) => { setTimeout(() => { Object.assign(w.pywebview.api, api); }, 400); } },
   true],

  // The normal path: nothing exists yet, then everything arrives with the event.
  ['nothing yet, then api + event',
   { after: (w) => { setTimeout(() => { w.pywebview = { api }; w.fire('pywebviewready'); }, 300); } },
   true],

  // A method that genuinely is not there must still fail, not hang for ever.
  ['method genuinely absent',
   { before: (w) => { w.pywebview = { api: { somethingElse: () => {} } }; } }, false],
];

(async () => {
  let bad = 0;
  for (const [n, setup, expect] of cases) {
    const ok = await run(n, setup, expect);
    if (!ok) bad++;
  }
  console.log(bad ? `\n  ${bad} FAILED` : '\n  all scenarios pass');
  process.exit(bad ? 1 : 0);
})();
