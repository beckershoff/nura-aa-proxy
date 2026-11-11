#!/usr/bin/env python3
"""Stand-in for aa-proxy-rs /battery: prints every POST body."""
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); print("POST", self.path, self.rfile.read(n).decode(), flush=True)
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
