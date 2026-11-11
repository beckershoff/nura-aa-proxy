#!/usr/bin/env python3
"""ioniq5-bms.py - read the Ioniq 5 battery state of charge through an ELM327
OBD adapter (vLinker MC+ over Bluetooth RFCOMM) and feed it to aa-proxy-rs.

aa-proxy-rs starts this as `ev_battery_logger` when Android Auto asks for EV
data and stops it with SIGTERM when the session ends. Every --interval
seconds it reads the BMS (CAN 7E4 -> 7EC, UDS service 0x22) and POSTs
{"battery_level_percentage": .., "battery_capacity_wh": ..} to /battery.

Byte positions of the SoC in the BMS answers are NOT verified yet. Calibrate
once in the car (ignition on, adapter paired):

    ioniq5-bms.py --mac AA:BB:CC:DD:EE:FF --calibrate 72
        (72 = the SoC % the car shows) -> prints matching byte positions;
        put them into SOC_SOURCES below or pass --soc-pid/--soc-byte.

Other modes:
    --dump        print every answer with byte indices, no POST
    --dry-run     decode and print, no POST
    --tcp HOST:PORT   talk to an ELM327 emulator instead of Bluetooth (tests)
"""
import argparse
import json
import signal
import socket
import sys
import time
import urllib.request

# Candidate SoC sources: (did, byte index in the reassembled answer *after*
# "62 <did-hi> <did-lo>", scale). Index/scale are best guesses from community
# PID lists for Hyundai/Kia E-GMP and MUST be confirmed with --calibrate.
# Display SoC (what the car shows, relative to usable capacity) is preferred.
SOC_SOURCES = [
    ("0105", None, 0.5),  # SoC display: index unknown until calibrated
    ("0101", 4, 0.5),     # SoC BMS (guess: Torque letter "E" / 2)
]

BMS_HEADER = "7E4"        # BMS request ID; answers come from 7EC
CAPACITY_WH = 70000       # Ioniq 5 72.6 kWh gross, ~70.0 kWh usable


class Elm:
    """Minimal ELM327 command/response over a stream socket."""

    def __init__(self, sock, verbose=False):
        self.s = sock
        self.s.settimeout(6)
        self.verbose = verbose

    def cmd(self, c, timeout=6.0):
        self.s.sendall((c + "\r").encode())
        buf = b""
        end = time.monotonic() + timeout
        while not buf.endswith(b">"):
            if time.monotonic() > end:
                raise TimeoutError("ELM timeout on %r (got %r)" % (c, buf))
            chunk = self.s.recv(1024)
            if not chunk:
                raise ConnectionError("adapter closed the connection")
            buf += chunk
        text = buf[:-1].decode(errors="replace").replace("\r", "\n")
        lines = [l.strip() for l in text.split("\n") if l.strip() and l.strip() != c]
        if self.verbose:
            print("  >", c, "->", lines, file=sys.stderr)
        return lines

    def init(self):
        self.cmd("ATZ", timeout=10)
        # echo off, no linefeeds, spaces + CAN headers on (needed for ISO-TP
        # reassembly), ISO 15765-4 CAN 11 bit 500 kbit/s, requests to the BMS
        for c in ("ATE0", "ATL0", "ATS1", "ATH1", "ATSP6", "ATSH" + BMS_HEADER):
            r = self.cmd(c)
            if not any("OK" in l for l in r):
                raise RuntimeError("adapter rejected %s: %r" % (c, r))


def reassemble(lines, rx_id="7EC"):
    """ISO-TP reassembly of ELM327 lines with headers on (ATH1, ATS1):
    '7EC 10 3E 62 01 01 FF F7 E7', '7EC 21 ..', ... -> payload bytes."""
    frames = []
    for l in lines:
        parts = l.split()
        if len(parts) < 2 or parts[0].upper() != rx_id:
            continue
        try:
            frames.append([int(p, 16) for p in parts[1:]])
        except ValueError:
            continue
    if not frames:
        raise ValueError("no answer from %s: %r" % (rx_id, lines))
    first = frames[0]
    pci = first[0] >> 4
    if pci == 0:                      # single frame
        n = first[0] & 0x0F
        return bytes(first[1:1 + n])
    if pci != 1:
        raise ValueError("unexpected first frame %r" % first)
    total = ((first[0] & 0x0F) << 8) | first[1]
    data = list(first[2:])
    seq = 1
    for f in frames[1:]:
        if f[0] >> 4 != 2 or (f[0] & 0x0F) != (seq & 0x0F):
            raise ValueError("bad consecutive frame %r (expected seq %d)" % (f, seq & 0x0F))
        data += f[1:]
        seq += 1
    if len(data) < total:
        raise ValueError("short answer: %d of %d bytes" % (len(data), total))
    return bytes(data[:total])


def read_did(elm, did):
    """UDS ReadDataByIdentifier 22 <did>; returns the bytes after 62 <did>."""
    p = reassemble(elm.cmd("22" + did))
    if p[0] == 0x7F:
        raise ValueError("negative response for %s: %s" % (did, p.hex(" ")))
    if p[0] != 0x62 or p[1:3] != bytes.fromhex(did):
        raise ValueError("unexpected answer for %s: %s" % (did, p.hex(" ")))
    return p[3:]


def decode_soc(answers, sources):
    for did, idx, scale in sources:
        d = answers.get(did)
        if d is None or idx is None or idx >= len(d):
            continue
        v = d[idx] * scale
        if 0.0 <= v <= 100.0:
            return v, did, idx
    return None, None, None


def connect(args):
    if args.tcp:
        host, port = args.tcp.rsplit(":", 1)
        return socket.create_connection((host, int(port)), timeout=10)
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.settimeout(15)
    s.connect((args.mac, args.channel))
    return s


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mac", help="vLinker Bluetooth address")
    ap.add_argument("--channel", type=int, default=1, help="RFCOMM channel (default 1)")
    ap.add_argument("--tcp", help="HOST:PORT of an ELM327 emulator instead of Bluetooth")
    ap.add_argument("--url", default="http://10.0.0.1/battery")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--capacity-wh", type=int, default=CAPACITY_WH)
    ap.add_argument("--soc-pid", help="override: DID holding the SoC, e.g. 0105")
    ap.add_argument("--soc-byte", type=int, help="override: byte index after 62 xx xx")
    ap.add_argument("--dump", action="store_true", help="print answers with byte indices")
    ap.add_argument("--dry-run", action="store_true", help="decode only, no POST")
    ap.add_argument("--calibrate", type=float, metavar="SOC",
                    help="SoC %% shown by the car; list byte positions that match")
    ap.add_argument("--once", action="store_true", help="one reading, then exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    if not a.mac and not a.tcp:
        ap.error("--mac or --tcp is required")

    sources = SOC_SOURCES
    if a.soc_pid and a.soc_byte is not None:
        sources = [(a.soc_pid, a.soc_byte, 0.5)]

    running = True

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    elm = None
    while running:
        try:
            if elm is None:
                elm = Elm(connect(a), verbose=a.verbose)
                elm.init()
                print("connected to adapter", file=sys.stderr)
            answers = {}
            for did in sorted({s[0] for s in sources} | ({"0101", "0105"} if (a.dump or a.calibrate is not None) else set())):
                try:
                    answers[did] = read_did(elm, did)
                except (ValueError, RuntimeError) as e:
                    print("22%s: %s" % (did, e), file=sys.stderr)
            if a.dump or a.calibrate is not None:
                for did, d in sorted(answers.items()):
                    print("22%s (%d bytes):" % (did, len(d)))
                    for i in range(0, len(d), 16):
                        print("  %3d: %s" % (i, " ".join("%02X" % b for b in d[i:i + 16])))
            if a.calibrate is not None:
                print("bytes whose value/2 is within 1.5 %% of %.1f %%:" % a.calibrate)
                for did, d in sorted(answers.items()):
                    for i, b in enumerate(d):
                        if abs(b / 2 - a.calibrate) <= 1.5:
                            print("  22%s byte %d = 0x%02X -> %.1f %%" % (did, i, b, b / 2))
                return 0
            soc, did, idx = decode_soc(answers, sources)
            if soc is None:
                print("no SoC decoded (calibrate first: --calibrate <car SoC>)", file=sys.stderr)
            else:
                payload = {"battery_level_percentage": round(soc, 1),
                           "battery_capacity_wh": a.capacity_wh}
                print("SoC %.1f %% (22%s byte %d)" % (soc, did, idx), file=sys.stderr)
                if not a.dry_run and not a.dump:
                    try:
                        post(a.url, payload)
                    except OSError as e:  # aa-proxy-rs not reachable: keep the adapter link
                        print("POST %s failed: %s" % (a.url, e), file=sys.stderr)
            if a.once:
                return 0
        except (OSError, TimeoutError, ConnectionError, RuntimeError) as e:
            print("adapter error: %s; reconnecting" % e, file=sys.stderr)
            try:
                if elm:
                    elm.s.close()
            except OSError:
                pass
            elm = None
        end = time.monotonic() + a.interval
        while running and time.monotonic() < end:
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
