#!/bin/sh
# aa-monitor.sh - sample the phone every 10 s during a session; CSV on stdout.
#   EDGE=user@<phone-ip> ./aa-monitor.sh > run.csv
# Columns: proxy sessions/errors so far, aa-proxy-rs RSS and CPU, load, max CPU/GPU/battery
# temperature, battery %, daily phone's Wi-Fi signal/bitrates/tx failures on ap0, data rates.
: "${EDGE:?set EDGE=user@<phone-ip>}"
SSH="ssh -o BatchMode=yes -o ControlMaster=auto -o ControlPath=${XDG_RUNTIME_DIR:-/tmp}/aa-mon-%C -o ControlPersist=60 $EDGE"
echo "time,sessions,errors,rss_mb,cpu_pct,load1,cpu_max_c,gpu_c,batt_c,batt_pct,batt_status,sta_signal_dbm,sta_tx_mbit,sta_rx_mbit,tx_retries,tx_failed,rates"
while true; do
	$SSH 'L=$(sudo sed "s/\x1b\[[0-9;]*m//g" /var/log/aa-proxy-rs.log)
	s=$(echo "$L" | grep -c "Starting to proxy"); e=$(echo "$L" | grep -c "Connection error")
	pid=$(pgrep -x aa-proxy-rs | head -1)
	rss=$(awk "/VmRSS/{printf \"%.0f\", \$2/1024}" /proc/$pid/status 2>/dev/null)
	cpu=$(top -b -n1 | awk -v p=$pid "\$1==p{print \$7}")
	load=$(cut -d" " -f1 /proc/loadavg)
	cmax=0; for z in /sys/class/thermal/thermal_zone*; do case $(cat $z/type) in cpu*) t=$(cat $z/temp); [ "$t" -gt "$cmax" ] && cmax=$t;; esac; done
	gpu=$(for z in /sys/class/thermal/thermal_zone*; do [ "$(cat $z/type)" = gpu-top-thermal ] && cat $z/temp; done)
	b=/sys/class/power_supply/battery; bt=$(cat $b/temp 2>/dev/null); bp=$(cat $b/capacity 2>/dev/null); bs=$(cat $b/status 2>/dev/null)
	st=$(iw dev ap0 station dump 2>/dev/null)
	sig=$(echo "$st" | awk "/^\tsignal:/{print \$2; exit}"); tx=$(echo "$st" | awk "/tx bitrate/{print \$3; exit}"); rx=$(echo "$st" | awk "/rx bitrate/{print \$3; exit}")
	rt=$(echo "$st" | awk "/tx retries/{print \$3; exit}"); tf=$(echo "$st" | awk "/tx failed/{print \$3; exit}")
	rate=$(echo "$L" | grep "phone -> car" | tail -1 | sed -e "s/.*phone -> car[^(]*(\s*/up /" -e "s/).*car -> phone[^(]*(\s*/ down /" -e "s/).*//" | tr "," ";")
	echo "$(date +%T),$s,$e,$rss,$cpu,$load,$((cmax/1000)),$((${gpu:-0}/1000)),$((${bt:-0}/10)),$bp,$bs,$sig,$tx,$rx,$rt,$tf,$rate"' 2>/dev/null || echo "$(date +%T),ssh-failed"
	sleep 10
done
