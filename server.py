#!/usr/bin/env python3
"""
An Xtream Codes panel backed by iptv-org's catalogue of free, publicly
available channels.

Its whole reason to exist is App Review. ProperIpTv is useless without a
subscription, so Apple requires a demo account — and handing them the
developer's own line is a bad trade: those accounts allow one simultaneous
connection, so a reviewer signing in kills the owner's stream and gets written
up as "app not functional", and the credentials then sit in App Store Connect
in plaintext indefinitely.

This serves the same API a real panel does, over channels that are already
public, with credentials that protect nothing.

    python3 server.py            # http://127.0.0.1:8100   demo / demo
    PORT=8000 python3 server.py  # for hosts that inject $PORT

Data comes from iptv-org (https://iptv-org.github.io/api/), fetched once at
boot and held in memory.
"""

import base64
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT", "8100"))
USERNAME = os.environ.get("DEMO_USER", "demo")
PASSWORD = os.environ.get("DEMO_PASS", "demo")

API = "https://iptv-org.github.io/api"
UA = "Mozilla/5.0 (compatible; ProperIpTv-ReviewPanel/1.0)"

# Categories worth showing a reviewer: enough breadth that the country and
# genre facets have something to do, without pulling in 10,000 dead streams.
KEEP = {"news", "business", "sports", "music", "documentary", "science",
        "travel", "weather", "culture", "education", "entertainment",
        "movies", "kids", "comedy", "lifestyle"}


def fetch(path):
    req = urllib.request.Request(f"{API}/{path}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def build():
    print("fetching iptv-org catalogue…", flush=True)
    channels = {c["id"]: c for c in fetch("channels.json")}
    streams = fetch("streams.json")
    countries = {c["code"]: c for c in fetch("countries.json")}

    # One stream per channel: the first is generally the maintained one.
    best = {}
    for s in streams:
        cid = s.get("channel")
        if not cid or cid not in channels or cid in best:
            continue
        if not (s.get("url") or "").startswith("http"):
            continue
        best[cid] = s

    cats, cat_ids, live = [], {}, []
    sid = 1000
    for cid, s in best.items():
        ch = channels[cid]
        if ch.get("is_nsfw"):
            continue
        kinds = [c for c in (ch.get("categories") or []) if c in KEEP]
        if not kinds:
            continue
        genre = kinds[0]
        cc = (ch.get("country") or "INT").upper()
        cname = (countries.get(cc, {}).get("name") or cc)
        # "SE | NEWS" is the shape real panels use, and the shape the app's
        # country/genre parsing is built to read.
        label = f"{cc} | {genre.upper()}"
        if label not in cat_ids:
            cat_ids[label] = str(len(cat_ids) + 1)
            cats.append({"category_id": cat_ids[label],
                         "category_name": label, "parent_id": 0})
        sid += 1
        quality = ""
        if s.get("height"):
            h = s["height"]
            quality = " 4K" if h >= 2000 else " FHD" if h >= 1080 else " HD" if h >= 720 else " SD"
        live.append({
            "num": sid,
            "name": f"{cc} | {ch['name']}{quality}",
            "stream_type": "live",
            "stream_id": sid,
            "stream_icon": ch.get("logo") or "",
            "epg_channel_id": cid,
            "added": "1700000000",
            "category_id": cat_ids[label],
            "tv_archive": 0,
            "_url": s["url"],
            "_referrer": s.get("referrer") or "",
            "_ua": s.get("user_agent") or "",
        })

    print(f"ready: {len(live)} channels in {len(cats)} categories "
          f"across {len({c['category_name'].split(' | ')[0] for c in cats})} countries", flush=True)
    return cats, live


LIVE_CATS, LIVE = build()
BY_ID = {c["stream_id"]: c for c in LIVE}

ACCOUNT = {
    "user_info": {
        "username": USERNAME, "password": PASSWORD, "auth": 1, "status": "Active",
        "exp_date": str(int(time.time()) + 86400 * 365), "is_trial": "0",
        "active_cons": "0", "max_connections": "10",
        "allowed_output_formats": ["m3u8", "ts"],
    },
    "server_info": {"url": "", "port": str(PORT), "https_port": "",
                    "server_protocol": "http", "timezone": "UTC"},
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]

        if u.path in ("/", "/health"):
            return self._json({"ok": True, "channels": len(LIVE),
                               "sign_in": {"server": "this URL",
                                           "username": USERNAME, "password": PASSWORD}})

        # /live/<user>/<pass>/<id>.m3u8 — hand the player the real stream.
        if parts and parts[0] in ("live", "movie", "series"):
            try:
                sid = int(parts[-1].split(".")[0])
            except (ValueError, IndexError):
                self.send_error(404); return
            row = BY_ID.get(sid)
            if not row:
                self.send_error(404); return
            self.send_response(302)
            self.send_header("Location", row["_url"])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        q = parse_qs(u.query)
        if q.get("username", [""])[0] != USERNAME or q.get("password", [""])[0] != PASSWORD:
            return self._json({"user_info": {"auth": 0}})

        action = q.get("action", [""])[0]
        if action == "":
            return self._json(ACCOUNT)
        if action == "get_live_categories":
            return self._json(LIVE_CATS)
        if action == "get_live_streams":
            cid = q.get("category_id", [None])[0]
            rows = [c for c in LIVE if not cid or c["category_id"] == cid]
            return self._json([{k: v for k, v in c.items() if not k.startswith("_")} for c in rows])
        if action == "get_short_epg":
            return self._json({"epg_listings": []})
        # No VOD here: these are live streams only, and an empty list is how a
        # panel without a movie package answers.
        return self._json([])


if __name__ == "__main__":
    print(f"Xtream review panel on :{PORT}  ({USERNAME}/{PASSWORD})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
