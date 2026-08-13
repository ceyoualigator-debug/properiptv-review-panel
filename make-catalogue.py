#!/usr/bin/env python3
"""Regenerates catalogue.json, the snapshot the panel serves at start-up.

Run this when the list has gone stale, then commit the result:

    python3 make-catalogue.py && git commit -am "Refresh catalogue"

The panel refreshes in the background anyway; the snapshot exists so that a cold
instance can answer immediately rather than making the first caller wait out a
full fetch-and-probe of ~1400 streams.
"""
import importlib.util, json, pathlib, time

spec = importlib.util.spec_from_file_location("panel", pathlib.Path(__file__).with_name("server.py"))
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)

started = time.time()
cats, live = panel.build()
pathlib.Path(__file__).with_name("catalogue.json").write_text(json.dumps(
    {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
     "categories": cats, "live": live},
    separators=(",", ":")))
print(f"{len(live)} channels in {len(cats)} categories, built in {time.time() - started:.0f}s")
