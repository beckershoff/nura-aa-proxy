#!/bin/sh
# install.sh - install the aa-proxy car mode on the eqs (run as root on the
# phone, from a copy of the phone/ directory with the aa-proxy-rs binary next to it).
# Does not start anything: `systemctl start aa-proxy.target` does.
set -eu
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -x ./aa-proxy-rs ] || { echo "missing ./aa-proxy-rs binary" >&2; exit 1; }

install -D -m 755 aa-proxy-rs /usr/local/bin/aa-proxy-rs
install -D -m 755 aa-proxy-ap0 /usr/local/libexec/aa-proxy-ap0
install -m 644 hostapd.conf.in /etc/hostapd.conf.in
install -m 644 dnsmasq.conf.in /etc/dnsmasq.conf.in
install -D -m 644 interfaces.in /etc/network/interfaces.in
for u in systemd/*; do install -m 644 "$u" /etc/systemd/system/; done
mkdir -p /var/lib/aa-proxy-rs/crashes
install -m 644 60_aa-proxy.nft /etc/nftables.d/60_aa-proxy.nft
systemctl reload nftables 2>/dev/null || systemctl restart nftables

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
systemctl daemon-reload
nmcli general reload conf 2>/dev/null || true
echo "installed. start: systemctl start aa-proxy.target   stop: systemctl stop aa-proxy.target"
