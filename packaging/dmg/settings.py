# dmgbuild settings for the Riparr Preparer disk image.
#
# Not create-dmg: that drives Finder through AppleScript to set the icon view, and on
# this machine it silently produced backgroundType 0 and iconSize 48 -- a white box with
# two default-sized icons, which is exactly what it looked like. dmgbuild writes the
# .DS_Store itself, so the result does not depend on a Finder session existing or
# co-operating, which also makes it work on a headless CI runner.
import os

# dmgbuild exec()s this file, so there is no __file__ to hang paths off.
app = os.environ.get("RIPARR_APP", "tools/preparer/dist/Riparr Preparer.app")
here = os.environ.get("RIPARR_DMG_DIR", "packaging/dmg")
appname = os.path.basename(app)

format = "UDZO"
files = [app]
symlinks = {"Applications": "/Applications"}
icon = os.environ.get("RIPARR_VOLICON", os.path.join(here, "riparr.icns"))
background = os.path.join(here, "background.png")

window_rect = ((200, 120), (660, 400))
default_view = "icon-view"
icon_size = 128
text_size = 12
icon_locations = {appname: (165, 175), "Applications": (495, 175)}

show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
arrange_by = None
label_pos = "bottom"
show_item_info = False
show_icon_preview = False
