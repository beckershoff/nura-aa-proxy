#!/usr/bin/env python3
"""Tiny ELM327 emulator (TCP) with SYNTHETIC Ioniq-5-like BMS answers, for testing
ev/ioniq5-bms.py without a car. Usage: test/elm-emu.py 35000, then
ev/ioniq5-bms.py --tcp 127.0.0.1:35000 --calibrate 74 (or --once --dry-run).
Encodes SoC 72.5 % as 0x91 at 220101 byte 4 and display SoC 74.0 % (0x94) at
220105 byte 30. Not real car data."""
import socket, sys, threading

def isotp_lines(payload, rx="7EC"):
    if len(payload) <= 7:
        return ["%s %02X %s" % (rx, len(payload), " ".join("%02X" % b for b in payload))]
    out = ["%s 1%01X %02X %s" % (rx, len(payload) >> 8, len(payload) & 0xFF, " ".join("%02X" % b for b in payload[:6]))]
    rest, seq = payload[6:], 1
    while rest:
        chunk, rest = rest[:7], rest[7:]
        chunk = chunk + bytes(7 - len(chunk)) if len(chunk) < 7 else chunk
        out.append("%s 2%01X %s" % (rx, seq & 0xF, " ".join("%02X" % b for b in chunk)))
        seq += 1
    return out

d0101 = bytearray(range(0x10, 0x10 + 59)); d0101[4] = 0x91
d0105 = bytearray(range(0x40, 0x40 + 42)); d0105[30] = 0x94
ANS = {"220101": bytes.fromhex("620101") + d0101, "220105": bytes.fromhex("620105") + d0105}

def handle(c):
    buf = b""
    while True:
        d = c.recv(256)
        if not d: return
        buf += d
        while b"\r" in buf:
            line, buf = buf.split(b"\r", 1)
            cmd = line.decode().strip().upper().replace(" ", "")
            if cmd == "ATZ": resp = ["ELM327 v1.5"]
            elif cmd.startswith("AT"): resp = ["OK"]
            elif cmd in ANS: resp = isotp_lines(ANS[cmd])
            elif cmd.startswith("22"): resp = isotp_lines(bytes([0x7F, 0x22, 0x31]))
            else: resp = ["?"]
            c.sendall(("\r".join(resp) + "\r\r>").encode())

s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(4)
while True:
    c, _ = s.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
