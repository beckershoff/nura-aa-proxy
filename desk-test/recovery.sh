#!/bin/sh
# recovery.sh LABEL 'FAULT COMMAND' - inject a fault, then measure until aa-proxy-rs logs a
# new "Starting to proxy" (Android Auto running again). Gives up after 180 s.
#   EDGE=user@<phone-ip> ./recovery.sh "proxy crash" "ssh \$EDGE 'sudo kill -9 \$(pgrep -x aa-proxy-rs)'"
: "${EDGE:?set EDGE=user@<phone-ip>}"
SSH="ssh -o BatchMode=yes -o ControlMaster=auto -o ControlPath=${XDG_RUNTIME_DIR:-/tmp}/aa-mon-%C -o ControlPersist=60 $EDGE"
count() { $SSH 'sudo grep -c "Starting to proxy" /var/log/aa-proxy-rs.log'; }
label=$1; shift
before=$(count); t0=$(date +%s.%N)
echo "$(date +%T) [$label] fault: $*"
sh -c "$*"
while :; do
	el=$(echo "$(date +%s.%N) - $t0" | bc)
	[ "$(count)" -gt "$before" ] && { printf '%s [%s] recovered after %.1f s\n' "$(date +%T)" "$label" "$el"; exit 0; }
	[ "$(echo "$el > 180" | bc)" = 1 ] && { echo "$(date +%T) [$label] NOT recovered within 180 s"; exit 1; }
	sleep 1
done
