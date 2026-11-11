#!/usr/bin/env python3
"""Car mode - a small libadwaita control panel for the aa-proxy Android Auto dongle.
Reads status directly; all privileged changes go through `sudo -n aa-proxy-ctl`."""
import gi
import os
import subprocess
import tempfile
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio  # noqa: E402

CONF = "/etc/aa-proxy-rs/car-mode.conf"
CTL = ["sudo", "-n", "/usr/local/bin/aa-proxy-ctl"]
IDLE_CHOICES = [3, 5, 10, 15, 30, 60]


def sh(args, timeout=8):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def unit_active(unit):
    r = sh(["systemctl", "is-active", "--quiet", unit])
    return bool(r) and r.returncode == 0


def phone_connected():
    if os.path.exists("/run/aa-proxy-phone-connected"):
        return True
    r = sh(["iw", "dev", "ap0", "station", "dump"])
    return bool(r) and "Station" in (r.stdout or "")


def get_state():
    """One privileged call for ssid/password/mitm/ev/dpi (config.toml is root-only)."""
    st = {"SSID": "", "PASS": "", "MITM": "false", "EV": "false", "DPI": "0",
          "DEBUG": "false", "BLANK": "1", "IDLE": "5"}
    r = sh(CTL + ["get-state"])
    if r and r.returncode == 0:
        for line in r.stdout.splitlines():
            if "\t" in line:
                k, _, v = line.partition("\t")
                if k in st:
                    st[k] = v
    return st


def wifi_qr_png(ssid, pw):
    """Render a WIFI: QR to a temp PNG via qrencode; return path or None."""
    def esc(s):
        for c in ("\\", ";", ",", ":", '"'):
            s = s.replace(c, "\\" + c)
        return s
    payload = "WIFI:T:WPA;S:%s;P:%s;;" % (esc(ssid), esc(pw))
    path = os.path.join(tempfile.gettempdir(), "aa-proxy-wifi-qr.png")
    r = sh(["qrencode", "-m", "2", "-s", "6", "-o", path, payload])
    return path if (r and r.returncode == 0 and os.path.exists(path)) else None


class Panel(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Car mode")
        self.set_default_size(360, 640)
        self._guard = False
        self._pw = ""

        self.toast = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.restart_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                      tooltip_text="Restart car mode")
        self.restart_btn.connect("clicked", lambda *_: self._ctl("restart"))
        header.pack_end(self.restart_btn)
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()

        # --- status + master switch ---
        g_status = Adw.PreferencesGroup()
        self.status_row = Adw.ActionRow(title="Car mode", subtitle="…")
        self.master = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.master.connect("notify::active", self.on_master)
        self.status_row.add_suffix(self.master)
        self.status_row.set_activatable_widget(self.master)
        g_status.add(self.status_row)
        page.add(g_status)

        # --- while a phone is connected ---
        g_conn = Adw.PreferencesGroup(title="While a phone is connected")
        self.blank_row = Adw.ActionRow(title="Turn the screen off",
                                       subtitle="Blank the panel while projecting")
        self.blank_sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.blank_sw.connect("notify::active", self.on_blank)
        self.blank_row.add_suffix(self.blank_sw)
        self.blank_row.set_activatable_widget(self.blank_sw)
        g_conn.add(self.blank_row)

        self.idle_row = Adw.ComboRow(title="Auto-stop on battery",
                                     subtitle="When idle with no phone")
        idle_model = Gtk.StringList()
        for m in IDLE_CHOICES:
            idle_model.append("%d min" % m)
        self.idle_row.set_model(idle_model)
        self.idle_row.connect("notify::selected", self.on_idle)
        g_conn.add(self.idle_row)
        page.add(g_conn)

        # --- phone pairing ---
        g_wifi = Adw.PreferencesGroup(
            title="Connect your phone",
            description="Join this Wi-Fi, then pair Bluetooth “eqs-aa”.")
        self.ssid_row = Adw.ActionRow(title="Wi-Fi name", subtitle="…")
        self.ssid_row.add_css_class("property")
        g_wifi.add(self.ssid_row)
        self.pw_row = Adw.ActionRow(title="Password", subtitle="…")
        self.pw_row.add_css_class("property")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text="Copy password")
        copy.connect("clicked", self.on_copy_pw)
        self.pw_row.add_suffix(copy)
        g_wifi.add(self.pw_row)
        page.add(g_wifi)

        g_qr = Adw.PreferencesGroup()
        self.qr = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, height_request=200)
        g_qr.add(self.qr)
        page.add(g_qr)

        # --- advanced (car / MITM) ---
        g_adv = Adw.PreferencesGroup(title="Advanced")
        exp = Adw.ExpanderRow(title="Car tweaks (MITM)",
                              subtitle="Needs certificates; test in the car")
        self.mitm_sw = self._switch_row(exp, "MITM mode",
                                        "Required for DPI and EV data", self.on_mitm)
        self.ev_sw = self._switch_row(exp, "EV routing",
                                      "Feed battery level to Maps", self.on_ev)
        self.dpi_row = Adw.ComboRow(title="Screen density (DPI)",
                                    subtitle="Applies with MITM")
        self.dpi_vals = [0, 130, 140, 160]
        dm = Gtk.StringList()
        for d in self.dpi_vals:
            dm.append("Car default" if d == 0 else str(d))
        self.dpi_row.set_model(dm)
        self.dpi_row.connect("notify::selected", self.on_dpi)
        exp.add_row(self.dpi_row)
        g_adv.add(exp)
        page.add(g_adv)

        # --- diagnostics ---
        g_diag = Adw.PreferencesGroup(title="Diagnostics",
                                      description="For checking MITM and calls in the car.")
        dbg_row = Adw.ActionRow(title="Detailed logging",
                                subtitle="Verbose aa-proxy log (uses more space)")
        self.debug_sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.debug_sw.connect("notify::active", self.on_debug)
        dbg_row.add_suffix(self.debug_sw)
        dbg_row.set_activatable_widget(self.debug_sw)
        g_diag.add(dbg_row)
        log_row = Adw.ActionRow(title="View logs", subtitle="Live log with filters")
        log_row.add_prefix(Gtk.Image(icon_name="document-text-symbolic"))
        log_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        log_row.set_activatable(True)
        log_row.connect("activated", lambda *_: LogWindow(self).present())
        g_diag.add(log_row)
        page.add(g_diag)

        toolbar.set_content(Gtk.ScrolledWindow(child=page, vexpand=True))
        self.toast.set_child(toolbar)
        self.set_content(self.toast)

        self._refresh_wifi()
        self.refresh()
        GLib.timeout_add_seconds(2, self.refresh)

    def _switch_row(self, parent, title, subtitle, cb):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        sw.connect("notify::active", cb)
        row.add_suffix(sw)
        row.set_activatable_widget(sw)
        parent.add_row(row)
        return sw

    # ---- actions ----
    def _ctl(self, *args):
        r = sh(CTL + list(args))
        if not r or r.returncode != 0:
            self._toast("Failed: " + " ".join(args))
        self.refresh()
        return r

    def on_master(self, sw, _):
        if not self._guard:
            self._ctl("start" if sw.get_active() else "stop")

    def on_blank(self, sw, _):
        if not self._guard:
            self._ctl("set-conf", "BLANK_ON_CONNECT", "1" if sw.get_active() else "0")

    def on_idle(self, row, _):
        if not self._guard:
            self._ctl("set-conf", "IDLE_STOP_MIN", str(IDLE_CHOICES[row.get_selected()]))

    def on_mitm(self, sw, _):
        if not self._guard:
            self._ctl("set-aa", "mitm", "true" if sw.get_active() else "false")

    def on_ev(self, sw, _):
        if not self._guard:
            self._ctl("set-aa", "ev", "true" if sw.get_active() else "false")

    def on_dpi(self, row, _):
        if not self._guard:
            self._ctl("set-aa", "dpi", str(self.dpi_vals[row.get_selected()]))

    def on_debug(self, sw, _):
        if not self._guard:
            self._ctl("set-aa", "debug", "true" if sw.get_active() else "false")

    def on_copy_pw(self, *_):
        self.get_clipboard().set(self._pw)
        self._toast("Password copied")

    def _toast(self, text):
        self.toast.add_toast(Adw.Toast(title=text, timeout=2))

    # ---- status ----
    def _refresh_wifi(self):
        st = get_state()
        self._pw = st["PASS"]
        self.ssid_row.set_subtitle(st["SSID"] or "unknown")
        self.pw_row.set_subtitle(st["PASS"] or "unknown")
        png = wifi_qr_png(st["SSID"], st["PASS"]) if st["SSID"] else None
        if png:
            self.qr.set_filename(png)
        return st

    def refresh(self):
        self._guard = True
        active = unit_active("aa-proxy.target")
        self.master.set_active(active)
        if active:
            self.status_row.set_subtitle(
                "On — " + ("phone connected" if phone_connected()
                               else "waiting for phone"))
        else:
            self.status_row.set_subtitle("Off")
        self.restart_btn.set_sensitive(active)
        st = get_state()
        self._pw = st["PASS"]
        self.blank_sw.set_active(st["BLANK"] == "1")
        try:
            m = int(st["IDLE"])
            self.idle_row.set_selected(IDLE_CHOICES.index(m) if m in IDLE_CHOICES else 1)
        except ValueError:
            pass
        self.mitm_sw.set_active(st["MITM"] == "true")
        self.ev_sw.set_active(st["EV"] == "true")
        self.debug_sw.set_active(st["DEBUG"] == "true")
        try:
            d = int(st["DPI"])
            self.dpi_row.set_selected(self.dpi_vals.index(d) if d in self.dpi_vals else 0)
        except ValueError:
            pass
        self._guard = False
        return True


FILTERS = {
    "All": None,
    "MITM": ("ssl", "mitm", "handshake", "cert", "negotiation", "tls", "cipher"),
    "Calls": ("hsp", "hfp", " sco", "headset", "call", "bt_sco", "ring", "a2dp"),
    "Errors": ("error", "warn", "stall", "failed", "abort"),
}


class LogWindow(Adw.Window):
    def __init__(self, parent):
        super().__init__(title="aa-proxy log", transient_for=parent, modal=False)
        self.set_default_size(420, 620)
        self._filter = "All"
        self._paused = False

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.drop = Gtk.DropDown.new_from_strings(list(FILTERS.keys()))
        self.drop.connect("notify::selected", self._on_filter)
        header.pack_start(self.drop)
        pause = Gtk.ToggleButton(icon_name="media-playback-pause-symbolic",
                                 tooltip_text="Pause auto-refresh")
        pause.connect("toggled", lambda b: setattr(self, "_paused", b.get_active()))
        header.pack_end(pause)
        save = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Save log to a file")
        save.connect("clicked", self._on_save)
        header.pack_end(save)
        tv.add_top_bar(header)

        self.toast = Adw.ToastOverlay()
        self.buf = Gtk.TextBuffer()
        self.view = Gtk.TextView(buffer=self.buf, editable=False, cursor_visible=False,
                                 monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True)
        self.scroller = scroller
        self.toast.set_child(scroller)
        tv.set_content(self.toast)
        self.set_content(tv)

        self.refresh()
        self._timer = GLib.timeout_add_seconds(2, self.refresh)
        self.connect("close-request", self._on_close)

    def _on_filter(self, drop, _):
        self._filter = list(FILTERS.keys())[drop.get_selected()]
        self.refresh()

    def _on_close(self, *_):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        return False

    def _on_save(self, *_):
        r = sh(CTL + ["save-log"])
        if r and r.returncode == 0 and r.stdout.strip():
            self.toast.add_toast(Adw.Toast(title="Saved to " + r.stdout.strip(), timeout=4))
        else:
            self.toast.add_toast(Adw.Toast(title="Save failed", timeout=3))

    def refresh(self):
        if self._paused:
            return True
        r = sh(CTL + ["log", "400"])
        text = r.stdout if (r and r.returncode == 0) else "(no log yet)"
        keys = FILTERS[self._filter]
        if keys:
            text = "\n".join(l for l in text.splitlines()
                             if any(k in l.lower() for k in keys)) or "(no matching lines)"
        # keep it light: last ~250 lines
        lines = text.splitlines()
        if len(lines) > 250:
            lines = lines[-250:]
        self.buf.set_text("\n".join(lines))
        # autoscroll to the end
        GLib.idle_add(lambda: self.view.scroll_to_mark(
            self.buf.create_mark(None, self.buf.get_end_iter(), False), 0, False, 0, 0))
        return True


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="eco.nura.CarMode",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        p = Panel(self)
        if os.environ.get("AA_PANEL_VIEW") == "log":  # for testing/screenshots
            LogWindow(p).present()
        else:
            p.present()


if __name__ == "__main__":
    App().run(None)
