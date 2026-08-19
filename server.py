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
import pathlib
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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

MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "1400"))


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
        url = s.get("url") or ""
        if not url.startswith("https://"):
            continue          # plain HTTP is blocked by ATS on a redirect target
        if s.get("referrer") or s.get("user_agent"):
            continue          # headers cannot be carried through a redirect
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

    # A reviewer needs a few working channels, not every channel on the
    # internet. Capping the candidates keeps the verification pass short
    # enough that a cold start is not a two-minute wait.
    live = verify(live[:MAX_CANDIDATES])
    kept = {c["category_id"] for c in live}
    cats = [c for c in cats if c["category_id"] in kept]
    print(f"ready: {len(live)} verified channels in {len(cats)} categories "
          f"across {len({c['category_name'].split(' | ')[0] for c in cats})} countries", flush=True)
    return cats, live


def probe(row):
    """True when the origin actually serves this stream to an anonymous client."""
    req = urllib.request.Request(row["_url"], headers={"User-Agent": "VLC/3.0.20 LibVLC/3.0.20"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            if r.status != 200:
                return False
            head = r.read(1024)
            # An HLS playlist, or at least not an HTML error page.
            return b"#EXTM3U" in head or not head.lstrip()[:1] == b"<"
    except Exception:                                # noqa: BLE001
        return False


def verify(rows):
    """Keep only channels that respond right now.

    A reviewer opens two or three channels and judges the app on them. Roughly
    a third of any community-maintained list is dead at any moment, so serving
    the raw list means a real chance the first thing they tap fails and the app
    gets marked "not functional".
    """
    print(f"verifying {len(rows)} candidate streams…", flush=True)
    with ThreadPoolExecutor(max_workers=128) as pool:
        alive = list(pool.map(probe, rows))
    kept = [r for r, ok in zip(rows, alive) if ok]
    print(f"  {len(kept)} of {len(rows)} responded", flush=True)
    return kept


LIVE_CATS, LIVE, BY_ID = [], [], {}
READY = threading.Event()


SNAPSHOT = pathlib.Path(__file__).with_name("catalogue.json")


def publish(cats, live):
    global LIVE_CATS, LIVE, BY_ID
    LIVE_CATS, LIVE = cats, live
    BY_ID = {c["stream_id"]: c for c in live}
    READY.set()


def load_in_background():
    """Serve the baked snapshot at once, then refresh behind it.

    **This is what got the macOS build rejected.** `build()` fetches the whole
    iptv-org catalogue and then probes ~1400 streams to drop the dead ones —
    27 seconds on a warm laptop and considerably worse on a free instance. Every
    catalogue request waited on it (`READY.wait(timeout=75)`), and this runs on
    a tier that suspends after fifteen minutes idle, so a reviewer's first
    sign-in always paid the full rebuild. App Review saw "the app loaded
    indefinitely upon login" and, because nothing had loaded, a menu bar of
    disabled commands — reported as a second bug, but the same one.

    The snapshot is generated by `make-catalogue.py` and committed, so start-up
    is a 317 KB file read and `READY` is set before the first request arrives.
    The refresh still runs, because a community list rots — it just no longer
    stands between a reviewer and the app.
    """
    if SNAPSHOT.exists():
        try:
            snap = json.loads(SNAPSHOT.read_text())
            publish(snap["categories"], snap["live"])
            print(f"snapshot: {len(LIVE)} channels in {len(LIVE_CATS)} categories "
                  f"(generated {snap.get('generated', 'unknown')})", flush=True)
        except Exception as exc:                     # noqa: BLE001
            print(f"snapshot unreadable ({exc}); falling back to a live build", flush=True)

    # With a snapshot loaded, refreshing on boot does more harm than good here.
    #
    # `verify()` runs 128 probe threads. That is fine on a laptop and ruinous on
    # a free instance with a tenth of a CPU: the probes starve the HTTP handler,
    # and measured against the deployed panel every catalogue request took ~45
    # seconds for as long as the refresh ran — with /health answering instantly
    # and reporting ready:true the whole time, which is what made it look like a
    # cold start rather than CPU starvation.
    #
    # The snapshot is committed and regenerated deliberately by
    # `make-catalogue.py`, so the refresh is a convenience, not a requirement.
    # Set REFRESH_ON_BOOT=1 to restore it.
    if READY.is_set() and os.environ.get("REFRESH_ON_BOOT", "") != "1":
        print("serving the snapshot; boot refresh disabled "
              "(REFRESH_ON_BOOT=1 to enable)", flush=True)
        return

    for attempt in range(3):
        try:
            cats, live = build()
            publish(cats, live)
            return
        except Exception as exc:                     # noqa: BLE001
            print(f"catalogue refresh failed ({exc}); retry {attempt + 1}/3", flush=True)
            time.sleep(5)
    # Only fatal when there was no snapshot to fall back on.
    if not READY.is_set():
        print("catalogue could not be loaded", flush=True)

# --- Guide -------------------------------------------------------------------
#
# **App Review could not reach the calendar feature without this.**
#
# The panel answered `get_short_epg` with an empty list and `get_simple_data_table`
# with `[]`, so every one of the 1,059 channels rendered as "No guide data". With
# no programme rows there is nothing to pin, My Calendar stays empty, and "Add to
# Calendar" — the one control that exercises
# `com.apple.security.personal-information.calendars` — is unreachable. The macOS
# rejection under 2.4.5 asked us to demonstrate exactly that entitlement, and the
# demo account we supplied made it impossible to demonstrate.
#
# The schedule is synthesised rather than fetched. iptv-org publishes guide data
# only as XMLTV behind third-party URLs: tens of megabytes, frequently stale or
# offline, and fetching it at start-up would reintroduce precisely the cold-start
# that got the build rejected under 2.1(a). This costs nothing at boot and is
# generated per request from the stream id, so it is stable across calls — a
# programme does not rename itself between the guide and the calendar event.
#
# Titles follow the channel's own genre so the guide reads plausibly. The
# reviewer notes say the schedule is sample data; it is a review fixture, not a
# claim about what is really on air.

SLOT_MINUTES = 60

GENRE_SLOTS = {
    "news":          ["World News", "Headlines", "The Briefing", "News Hour", "Newsroom Live"],
    "business":      ["Market Open", "Business Today", "The Ledger", "Closing Bell"],
    "sports":        ["Live Sport", "Match of the Day", "Sports Desk", "Full Time"],
    "music":         ["The Playlist", "Live Sessions", "Chart Show", "Late Night Music"],
    "documentary":   ["Living Planet", "The Deep Field", "Frontier", "Witness"],
    "science":       ["Horizons", "The Method", "Cosmos Explained", "Lab Notes"],
    "travel":        ["Far Places", "The Slow Road", "City Guide", "Wayfarer"],
    "weather":       ["Weather Watch", "The Forecast", "Storm Track"],
    "culture":       ["Gallery", "Stage Door", "The Long Read", "Arts Review"],
    "education":     ["Classroom", "First Principles", "The Lecture", "Study Hall"],
    "entertainment": ["The Late Show", "Prime Time", "Talk of the Town", "Encore"],
    "movies":        ["Feature Presentation", "Matinee", "Cinema Classics", "Double Bill"],
    "kids":          ["Morning Cartoons", "Storytime", "Playroom", "Adventure Club"],
    "comedy":        ["Stand-Up Hour", "The Sketch Show", "Comedy Club", "Punchline"],
    "lifestyle":     ["The Kitchen", "Home & Garden", "Weekend Living", "Made by Hand"],
}
DEFAULT_SLOTS = ["Programming", "Continuous Coverage", "On Air"]


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _genre_for(row):
    """The genre half of a "SE | NEWS" category label."""
    for c in LIVE_CATS:
        if c["category_id"] == row.get("category_id"):
            parts = c["category_name"].split(" | ")
            if len(parts) == 2:
                return parts[1].lower()
    return ""


def epg_listings(row, start, count):
    """`count` consecutive slots from the slot containing `start`.

    Both guide endpoints share this shape: `get_simple_data_table` is the same
    JSON as `get_short_epg` with more rows, which is what the app already
    expects (see `XtreamClient.fullEPG`).
    """
    titles = GENRE_SLOTS.get(_genre_for(row), DEFAULT_SLOTS)
    step = SLOT_MINUTES * 60
    first = (int(start) // step) * step
    now = int(time.time())
    sid = int(row["stream_id"])
    name = row["name"].split(" | ", 1)[-1]

    out = []
    for i in range(count):
        begin = first + i * step
        end = begin + step
        # Deterministic in the stream id and the absolute slot, so the same
        # programme keeps the same name on every request.
        title = titles[(sid + begin // step) % len(titles)]
        out.append({
            "id": str(sid * 100000 + (begin // step) % 100000),
            "epg_id": str(sid),
            "title": _b64(title),
            "lang": "",
            "start": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(begin)),
            "end": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(end)),
            "description": _b64(f"{title} on {name}. Sample schedule supplied by the "
                                f"ProperIpTv review server."),
            "channel_id": row.get("epg_channel_id") or str(sid),
            "start_timestamp": str(begin),
            "stop_timestamp": str(end),
            "now_playing": 1 if begin <= now < end else 0,
            "has_archive": 0,
        })
    return {"epg_listings": out}


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
            return self._json({"ok": True, "ready": READY.is_set(), "channels": len(LIVE),
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
            READY.wait(timeout=75)
            return self._json(LIVE_CATS)
        if action == "get_live_streams":
            READY.wait(timeout=75)
            cid = q.get("category_id", [None])[0]
            rows = [c for c in LIVE if not cid or c["category_id"] == cid]
            return self._json([{k: v for k, v in c.items() if not k.startswith("_")} for c in rows])
        if action in ("get_short_epg", "get_simple_data_table"):
            try:
                sid = int(q.get("stream_id", ["0"])[0])
            except ValueError:
                sid = 0
            row = BY_ID.get(sid)
            if not row:
                return self._json({"epg_listings": []})
            if action == "get_short_epg":
                # What is on now and next. The app asks for `limit`.
                try:
                    limit = max(1, min(48, int(q.get("limit", ["12"])[0])))
                except ValueError:
                    limit = 12
                return self._json(epg_listings(row, time.time(), limit))
            # The full published schedule: yesterday evening through tomorrow,
            # so there are always future programmes available to pin.
            start = time.time() - 12 * 3600
            return self._json(epg_listings(row, start, 48))
        # No VOD here: these are live streams only, and an empty list is how a
        # panel without a movie package answers.
        return self._json([])


if __name__ == "__main__":
    threading.Thread(target=load_in_background, daemon=True).start()
    print(f"Xtream review panel listening on :{PORT}  ({USERNAME}/{PASSWORD})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
