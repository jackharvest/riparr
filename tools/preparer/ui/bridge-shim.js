/* One calling convention, two window shells.

   app.js has always called `window.riparr.<method>(...)`, and it should keep doing so:
   a port is not a reason to churn every call site.

   - Under app.py (macOS, PyObjC) `window.riparr` is already installed at document-start
     by the WKUserScript in BOOTSTRAP_JS. This file sees it and does nothing.
   - Under shell.py (pywebview, any OS) the bridge arrives as `window.pywebview.api`,
     and may not exist yet when this runs. So install a Proxy that queues every call
     behind the `pywebviewready` event.

   It has to be a separate file loaded before app.js rather than injected by the shell:
   app.js calls init() -- and therefore riparr.boot() -- the moment it is parsed, and
   pywebview cannot run script that early. Injecting on its `loaded` event produced a
   window that rendered perfectly with every value null. */
(function () {
  if (window.riparr) return;

  /* This shell gives the window a real, OS-drawn titlebar above the content, where
     app.py runs the content up underneath its own (NSWindowStyleMaskFullSizeContentView)
     and draws a matching bar in CSS. Both are right for their shell; drawing both at
     once gives you two title bars stacked, so say which one this is and let app.css
     decide. */
  document.documentElement.classList.add('native-titlebar');

  const ready = new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) return resolve();
    window.addEventListener('pywebviewready', () => resolve(), { once: true });
  });

  window.riparr = new Proxy({}, {
    get: (_, method) => (...args) =>
      ready.then(() => {
        const fn = window.pywebview.api[method];
        if (!fn) throw new Error('no such bridge method: ' + String(method));
        return fn(...args);
      }),
  });
})();
