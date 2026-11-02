"""The menu-bar icon (macOS) / tray icon (Windows, Linux).

It shows live usage and the latest incident, opens the dashboard, and lets
you flag "something went wrong" without a terminal. It also serves the
dashboard itself, so clicking "Open Freezeframe" is instant.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .store import Store
from .tray import TrayState, tray_state

log = logging.getLogger("freezeframe.menubar")

REFRESH_EVERY = 5.0
DEBUG = os.environ.get("FREEZEFRAME_DEBUG_MENU") == "1"
PREFERRED_PORT = 8765


def icon_image(attention: bool, template: bool, size: int = 64):
    """Draws the Freezeframe glyph: a frame with a pulse line, plus a badge when something happened."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 28
    pulse = [(5 * s, 17 * s), (9 * s, 17 * s), (11 * s, 11 * s), (14 * s, 21 * s), (16 * s, 7 * s), (18 * s, 17 * s), (23 * s, 17 * s)]
    if template:
        # macOS tints template images to match light and dark menu bars; only the alpha channel matters.
        ink = (0, 0, 0, 255)
        d.rounded_rectangle((2 * s, 4 * s, 26 * s, 24 * s), radius=3 * s, outline=ink, width=max(2, round(2 * s)))
        d.line(pulse, fill=ink, width=max(2, round(2 * s)), joint="curve")
        if attention:
            d.ellipse((18 * s, 0, 28 * s, 10 * s), fill=ink)
    else:
        # Taskbars can be light or dark: a solid tile reads on both.
        d.rounded_rectangle((1 * s, 3 * s, 27 * s, 25 * s), radius=4 * s, fill=(19, 32, 46, 255))
        d.line(pulse, fill=(255, 255, 255, 255), width=max(2, round(2.2 * s)), joint="curve")
        if attention:
            d.ellipse((17 * s, 0, 28 * s, 11 * s), fill=(208, 59, 59, 255), outline=(255, 255, 255, 255), width=max(1, round(s)))
    return img


def _machine_info(store: Store):
    meta = store.meta()
    if meta.get("demo") and meta.get("machine"):
        return lambda: meta["machine"]
    from .insights import machine

    cache: dict[str, object] = {}

    def get():
        if "t" not in cache or time.time() - cache["t"] > 300:
            cache["t"], cache["v"] = time.time(), machine()
        return cache["v"]

    return get


def run(db: Path) -> None:
    try:
        import pystray
    except ImportError:
        print("The menu-bar icon needs two extra packages. Install them with:\n"
              "  pip install 'freezeframe[menubar]'")
        sys.exit(1)

    from .recorder import mark
    from .server import start_server

    if not db.exists():
        Store(db).close()
    try:
        httpd, url = start_server(db, PREFERRED_PORT)
    except OSError:
        httpd, url = start_server(db, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    is_mac = sys.platform == "darwin"
    on_main = lambda fn: fn()  # noqa: E731
    if is_mac:
        import AppKit
        from PyObjCTools import AppHelper

        # A menu-bar-only app: no Dock icon, no app switcher entry.
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        on_main = AppHelper.callAfter  # AppKit must only be touched from the main thread

        class Icon(pystray.Icon):
            def _assert_image(self):
                super()._assert_image()
                if self._icon_image is not None:
                    self._icon_image.setTemplate_(True)
    else:
        Icon = pystray.Icon

    machine = _machine_info(Store(db, readonly=True))
    state: dict[str, TrayState] = {}

    def read_state() -> TrayState:
        s = Store(db, readonly=True)
        try:
            return tray_state(s, machine())
        finally:
            s.close()

    def open_dashboard(fragment: str = "") -> None:
        webbrowser.open(url + fragment)

    def flag_problem(icon, item) -> None:  # noqa: ARG001
        s = Store(db)
        try:
            e = mark(s)
        finally:
            s.close()
        open_dashboard(f"#incident={e.id}")

    def menu_items():
        st = state.get("now")
        items = []
        if st is None:
            items.append(pystray.MenuItem("Loading…", None, enabled=False))
        else:
            for line in st.lines:
                items.append(pystray.MenuItem(line, None, enabled=False))
            if st.latest is not None:
                latest_id = st.latest.id
                items.append(pystray.Menu.SEPARATOR)
                items.append(pystray.MenuItem("Latest incident", None, enabled=False))
                items.append(pystray.MenuItem(st.latest_text, lambda icon, item: open_dashboard(f"#incident={latest_id}")))
            if st.tip:
                items.append(pystray.Menu.SEPARATOR)
                items.append(pystray.MenuItem(st.tip, lambda icon, item: open_dashboard()))
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Freezeframe", lambda icon, item: open_dashboard(), default=True),
            pystray.MenuItem("Something went wrong…", flag_problem),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit menu bar icon" if is_mac else "Quit tray icon", lambda icon, item: icon.stop()),
        ]
        return items

    icon = Icon("freezeframe", icon_image(False, template=is_mac), "Freezeframe", menu=pystray.Menu(menu_items))

    def refresh_forever() -> None:
        shown_attention = False
        while True:
            try:
                st = read_state()
                state["now"] = st
                changed_badge = st.attention != shown_attention
                shown_attention = st.attention

                def apply(st=st, changed_badge=changed_badge):
                    if changed_badge:
                        icon.icon = icon_image(st.attention, template=is_mac)
                    icon.title = st.tooltip
                    icon.update_menu()
                    if DEBUG:
                        built = [str(i.text) for i in icon.menu]
                        if is_mac and icon._status_item.menu() is not None:
                            built = [str(m.title()) or "---" for m in icon._status_item.menu().itemArray()]
                        log.warning("menu: %s | badge=%s | tooltip=%s", built, st.attention, st.tooltip)

                on_main(apply)
            except Exception:
                log.exception("refresh failed")
            time.sleep(REFRESH_EVERY)

    def setup(icon) -> None:
        icon.visible = True
        threading.Thread(target=refresh_forever, daemon=True).start()

    icon.run(setup=setup)
    httpd.shutdown()
