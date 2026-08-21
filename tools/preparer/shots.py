"""Sample state for `--shot`, and the stylesheet that makes a shot reproducible.

Lives here rather than in a window shell because it is neither: it is UI state, painted
into the same DOM whichever shell is hosting it. Both `app.py` (macOS/PyObjC) and
`shell.py` (pywebview) render from this one set, so a screen cannot look one way in a
screenshot and another in the app.

Driving the real flow needs a card, a network and a board. These paint the same DOM from
fixed data so a screen can be looked at on demand.
"""
import json

import core


# A WKWebView in a window that was never brought to the front does not run CSS
# animations, and `.screen` starts at opacity 0 relying on `animation: rise ... forwards`
# to appear -- so the pane snapshots blank while the sidebar renders perfectly. Killing
# animation first fixes that, and also makes shots byte-stable between runs.
STILL = ("var s=document.createElement('style');"
         "s.textContent='*{animation:none !important;transition:none !important}"
         ".screen.on{opacity:1 !important;transform:none !important}';"
         "document.head.appendChild(s);")


def script_for(screen):
    """The JS that paints one screen, stilled. Unknown names just switch to them."""
    return STILL + SHOTS.get(screen, "show('%s');" % screen)


# Sample state for --shot. Driving the real flow needs a card, a network and a board;
# this paints the same DOM from fixed data so a screen can be looked at on demand.
SHOTS = {
    "welcome": "setRail(null); show('welcome');",
    # One real card, one device we classified as a drive, and the size guide open --
    # the three things that changed about this screen, all visible at once.
    "card": """
      setRail('card'); show('card');
      renderGuide(%s);
      renderDisks([
        {id:'disk4', name:'SDXC Card', protocol:'Secure Digital', size_gb:128,
         kind:'sd', kind_label:'SD card', is_card:true,
         why:"in this Mac's card slot",
         advice:{headline:'A comfortable Blu-ray evening',
                 detail:'Bursts 2 Blu-rays back to back, streams UHD.'}},
        {id:'disk6', name:'My Passport 25E2', protocol:'USB', size_gb:4000,
         kind:'disk', kind_label:'External drive', is_card:false,
         why:'the name reads as an external drive, not a card', advice:{}}
      ]);
      document.querySelector('#size-guide').open = true;
    """ % json.dumps(core.size_guide()),
    # The state the reveal exists for: no card recognised, but something removable is
    # attached. Worth being able to look at, because it is the screen a person hits
    # when their reader reports itself as a fixed disk.
    "card-other": """
      setRail('card'); show('card');
      renderGuide(%s);
      renderDisks([
        {id:'disk6', name:'Samsung PSSD T7', protocol:'USB', size_gb:1000,
         kind:'disk', kind_label:'External drive', is_card:false,
         why:'the name reads as an external drive, not a card', advice:{}},
        {id:'disk7', name:'USB 3.0 Device', protocol:'USB', size_gb:64,
         kind:'disk', kind_label:'External drive', is_card:false,
         why:'fixed media — this may be an external drive',
         advice:{headline:'Bursts one Blu-ray'}}
      ]);
      document.querySelector('#other-disks').open = true;
    """ % json.dumps(core.size_guide()),
    "connect": """
      state.boot = state.boot || {};
      state.boot.can_setup = true;
      state.boot.assets = '/Users/you/riparr-build';
      state.boot.ssh_config = '/Users/you/riparr-build/ssh_config';
      setRail('connect'); show('connect'); connectPreview();
      document.querySelector('#connect-manual').open = true;
      document.querySelector('#connect-found').className = 'micro good';
      document.querySelector('#connect-found').textContent =
        'riparr.local is answering at 192.168.3.143.';
    """,
    # A network chosen, so the password field and the keychain offer are both on
    # screen -- the state that actually needs looking at.
    "wifi": """
      setRail('card'); show('wifi');
      renderNets([
        {ssid:'Harvest House', bands:['2.4','5'], rssi:-43, secure:true, pi_ok:true, saved:true, seen:true},
        {ssid:'Harvest House 5G', bands:['5'], rssi:-51, secure:true, pi_ok:true, saved:false, seen:true},
        {ssid:'BTWiFi-with-FON', bands:['2.4'], rssi:-78, secure:false, pi_ok:true, saved:false, seen:true},
        {ssid:'NEIGHBOUR-6E', bands:['6'], rssi:-60, secure:true, pi_ok:false, saved:false, seen:true}
      ], 'live');
      pickNet({ssid:'Harvest House', bands:['2.4','5'], rssi:-43, secure:true, pi_ok:true, saved:true});
    """,
    "handoff": """
      setRail('card'); show('handoff');
      document.querySelector('#handoff-skip').innerHTML =
        '<a href="#">Skip — I\\'ll set it up myself</a>';
    """,
    "setup": """
      state.hostname = 'riparr'; state.port = 9797;
      setRail('card'); show('setup');
      renderTasks([
        {id:'find',      title:'Finding your Riparr',   detail:'Looking for the box on your network', state:'done'},
        {id:'connect',   title:'Connecting',            detail:'Opening a secure connection', state:'done'},
        {id:'copy',      title:'Copying Riparr across', detail:'Sending the software to the box', state:'done'},
        {id:'bootstrap', title:'Preparing the system',  detail:'Installing build tools and recording what the hardware is', state:'done'},
        {id:'install',   title:'Installing Riparr',     detail:'Building the Python environment — the slowest part, several minutes', state:'running'},
        {id:'verify',    title:'Checking it answers',   detail:'Making sure the web interface is really up', state:'waiting'}
      ]);
      document.querySelector('#setup-fill').style.width = '62%';
      document.querySelector('#setup-pct').textContent = '62%';
      document.querySelector('#setup-detail').textContent = 'riparr.local';
      document.querySelector('#setup-hint').textContent = 'Installing Riparr';
      document.querySelector('#log-reveal').open = true;
      document.querySelector('#setup-log').textContent =
        ['$ cd /root/riparr && sudo bash tools/install.sh',
         'Installing Riparr',
         '  port 9797 · /opt/riparr · OrangePi Zero 2W',
         '1/6  Packages',
         '  \u2713 avahi owns riparr.local (resolved\u2019s responder stood down)',
         '  \u2713 dependencies present',
         '2/6  Account',
         '  \u2713 user \u2018riparr\u2019 ready; staging at /srv/staging',
         '3/6  Riparr',
         '  \u2713 Riparr 0.1.0 in /opt/riparr',
         '4/6  Python environment',
         '  installing dependencies (a few minutes on a Zero 2 W)'].join('\\n');
    """,
    "done": """
      state.hostname = 'riparr'; state.port = 9797; state.elapsed = 571;
      setRail('card'); show('done'); renderDone(true);
    """,
    # The message people are most likely to actually read, so it is worth being able
    # to look at without failing a real setup.
    "failed": """
      setRail('card'); show('failed');
      document.querySelector('#fail-title').textContent =
        "Couldn't find your Riparr on the network.";
      document.querySelector('#fail-msg').textContent = '';
      document.querySelector('#fail-detail').textContent =
        "Checked riparr.local and swept this network for 300 seconds. In order of likelihood:\\n\\n" +
        "1. The Wi-Fi password is wrong. Nothing before this point can check it, and the box cannot tell you: it boots perfectly and never joins. Write the card again, and use the keychain button on the Wi-Fi step.\\n" +
        "2. The box is on a network this Mac can't see \\u2014 a guest network, or a band your router keeps on a separate subnet.\\n" +
        "3. It is still starting. A first boot resizes the card and can take a few minutes; if it has been under five, wait and try again.\\n" +
        "4. It has no power. The light on the board should be on.";
    """,
    "done-skipped": """
      state.hostname = 'riparr'; state.port = 9797;
      state.boot = state.boot || {}; state.boot.ssh_config = null;
      setRail('card'); show('done'); renderDone(false);
    """,
}
