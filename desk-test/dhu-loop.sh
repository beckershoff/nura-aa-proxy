#!/bin/bash
# dhu-loop.sh - play the car: whenever the phone's aa-proxy gadget (Google VID 18d1)
# shows up on USB, start Google's Desktop Head Unit in USB mode; log every start/exit.
#   DHU_DIR  unpacked desktop-head-unit (default ~/Software/desktop-head-unit)
#   DHU_INI  head unit profile (default: dhu-ioniq5.ini next to this script)
# DHU reads console commands from stdin and exits on EOF, so it gets a FIFO
# opened read-write: it never sees EOF, and killing DHU really ends it.
set -u
DHU_DIR=${DHU_DIR:-$HOME/Software/desktop-head-unit}
DHU_INI=${DHU_INI:-$(cd "$(dirname "$0")" && pwd)/dhu-ioniq5.ini}
cd "$DHU_DIR" || exit 1
fifo=$(mktemp -u "${XDG_RUNTIME_DIR:-/tmp}/dhu-stdin.XXXX"); mkfifo "$fifo"; exec 3<>"$fifo"; rm -f "$fifo"
while true; do
	until lsusb | grep -q 'ID 18d1:'; do sleep 0.5; done
	echo "$(date +%T) DHU start ($(lsusb | grep -o 'ID 18d1:[0-9a-f]*'))"
	LD_LIBRARY_PATH=. ./desktop-head-unit --usb -c "$DHU_INI" <&3 > /dev/null 2>&1
	echo "$(date +%T) DHU exit $?"
	sleep 1
done
