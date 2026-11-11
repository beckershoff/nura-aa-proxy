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
          "BLANK": "1", "IDLE": "5"}
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
        try:
            d = int(st["DPI"])
            self.dpi_row.set_selected(self.dpi_vals.index(d) if d in self.dpi_vals else 0)
        except ValueError:
            pass
        self._guard = False
        return True


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="eco.nura.CarMode",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        Panel(self).present()


if __name__ == "__main__":
    App().run(None)
