#!/bin/sh
# install.sh - install the aa-proxy car mode on the eqs (run as root on the
# phone, from a copy of the phone/ directory with the aa-proxy-rs binary next to it).
# Does not start anything: `systemctl start aa-proxy.target` does.
set -eu
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -x ./aa-proxy-rs ] || { echo "missing ./aa-proxy-rs binary" >&2; exit 1; }

install -D -m 755 aa-proxy-rs /usr/local/bin/aa-proxy-rs
install -D -m 755 aa-proxy-toggle /usr/local/bin/aa-proxy-toggle
install -D -m 755 aa-proxy-ctl /usr/local/bin/aa-proxy-ctl
install -D -m 755 aa-proxy-panel.py /usr/local/bin/aa-proxy-panel
for h in aa-proxy-ap0 aa-proxy-screen aa-proxy-notify aa-proxy-sta-event aa-proxy-idle-guard; do
	install -D -m 755 "$h" /usr/local/libexec/"$h"
done
install -m 644 hostapd.conf.in /etc/hostapd.conf.in
install -m 644 dnsmasq.conf.in /etc/dnsmasq.conf.in
install -D -m 644 interfaces.in /etc/network/interfaces.in
install -D -m 644 aa-proxy-car-mode.desktop /usr/share/applications/aa-proxy-car-mode.desktop
install -D -m 644 icons/eco.nura.CarMode.svg /usr/share/icons/hicolor/scalable/apps/eco.nura.CarMode.svg
gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
for u in systemd/*; do install -m 644 "$u" /etc/systemd/system/; done
mkdir -p /var/lib/aa-proxy-rs/crashes
install -m 644 60_aa-proxy.nft /etc/nftables.d/60_aa-proxy.nft
systemctl reload nftables 2>/dev/null || systemctl restart nftables

# let the GUI user drive car mode without a password, through the one audited
# helper (it validates its own args, so exposing it NOPASSWD is safe)
guipid=$(pgrep -x phosh 2>/dev/null | head -1)
guiuser=$(stat -c %U /proc/"$guipid" 2>/dev/null); guiuser=${guiuser:-user}
printf '%s ALL=(root) NOPASSWD: /usr/local/bin/aa-proxy-ctl\n' "$guiuser" > /etc/sudoers.d/60-aa-proxy-toggle
chmod 440 /etc/sudoers.d/60-aa-proxy-toggle
visudo -cf /etc/sudoers.d/60-aa-proxy-toggle >/dev/null || { echo "sudoers rule invalid, removing" >&2; rm -f /etc/sudoers.d/60-aa-proxy-toggle; }

# NetworkManager must leave ap0 alone
printf '[keyfile]\nunmanaged-devices=interface-name:ap0\n' \
	> /etc/NetworkManager/conf.d/90-aa-proxy-ap0.conf

# config: keep an existing one (and its Wi-Fi passphrase)
if [ ! -f /etc/aa-proxy-rs/config.toml ]; then
	pass=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)
	install -d -m 700 /etc/aa-proxy-rs
	sed "s/@PASSPHRASE@/$pass/" config.toml.in > /etc/aa-proxy-rs/config.toml
	chmod 600 /etc/aa-proxy-rs/config.toml
	echo "wrote /etc/aa-proxy-rs/config.toml (new Wi-Fi passphrase)"
else
	echo "kept /etc/aa-proxy-rs/config.toml"
fi
[ -f /etc/aa-proxy-rs/car-mode.conf ] || install -m 644 car-mode.conf /etc/aa-proxy-rs/car-mode.conf
systemctl daemon-reload
nmcli general reload conf 2>/dev/null || true
echo "installed. start: systemctl start aa-proxy.target   stop: systemctl stop aa-proxy.target"
