import http.server, socketserver, json, sqlite3, os, urllib.parse, webbrowser
from pathlib import Path

DB_PATH = Path("./violet_archive.db")
PORT = 5050

def get_db_data():
    if not DB_PATH.exists():
        return {"assigned": [], "unassigned": [], "universes": []}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cu.universe_name, cu.channel_id, COALESCE(c.name, cu.channel_id), COALESCE(c.total_messages, 0)
        FROM channel_universes cu
        LEFT JOIN channels c ON cu.channel_id = c.channel_id
        ORDER BY cu.universe_name ASC, COALESCE(c.total_messages, 0) DESC
    """)
    assigned = [{"universe": r[0], "channel_id": r[1], "name": r[2], "messages": r[3]} for r in cursor.fetchall()]

    cursor.execute("""
        SELECT c.channel_id, c.name, c.total_messages
        FROM channels c
        WHERE c.channel_id NOT IN (SELECT channel_id FROM channel_universes)
        ORDER BY c.total_messages DESC
    """)
    unassigned = [{"universe": "Unassigned", "channel_id": r[0], "name": r[1], "messages": r[2]} for r in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT universe_name FROM channel_universes ORDER BY universe_name ASC")
    universes = [r[0] for r in cursor.fetchall() if r[0]]
    conn.close()
    return {"assigned": assigned, "unassigned": unassigned, "universes": universes}

def set_channel_universe(channel_id, channel_name, universe_name):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    clean_uni = universe_name.strip()
    if clean_uni.lower() in ("unassigned", "global", "none", "reset", ""):
        cursor.execute("DELETE FROM channel_universes WHERE channel_id = ?", (str(channel_id),))
    else:
        cursor.execute(
            "INSERT OR REPLACE INTO channel_universes (channel_id, universe_name, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (str(channel_id), clean_uni)
        )
    conn.commit()
    conn.close()

def bulk_set_universe(channel_ids, universe_name):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    clean_uni = universe_name.strip()
    for cid in channel_ids:
        if clean_uni.lower() in ("unassigned", "global", "none", "reset", ""):
            cursor.execute("DELETE FROM channel_universes WHERE channel_id = ?", (str(cid),))
        else:
            cursor.execute(
                "INSERT OR REPLACE INTO channel_universes (channel_id, universe_name, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (str(cid), clean_uni)
            )
    conn.commit()
    conn.close()

class UniverseHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = open("universe_dashboard.html", encoding="utf-8").read()
            self.wfile.write(html.encode("utf-8"))
        elif parsed.path in ("/api/data", "/universes_export.json"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(get_db_data(), ensure_ascii=False).encode("utf-8"))
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        data = json.loads(body) if body else {}
        if parsed.path == "/api/set":
            set_channel_universe(data.get("channel_id"), data.get("channel_name", ""), data.get("universe_name", ""))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        elif parsed.path == "/api/bulk_set":
            bulk_set_universe(data.get("channel_ids", []), data.get("universe_name", ""))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == "__main__":
    print(f"🌌 Starting Universe Manager on http://localhost:{PORT}")
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass
    with socketserver.TCPServer(("", PORT), UniverseHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
