#!/usr/bin/env python3
"""Open MusicScout immediately and refresh supported charts when online.

Put this file beside MusicScout-V1.html and MusicScout-chart-data.json.
Run: python MusicScout-auto.py
The browser opens at localhost. Keep this window open while using the page.
Offline or failed publisher requests leave the last saved chart data intact.
"""

import html
import argparse
import json
import re
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
DATA = BASE / "MusicScout-chart-data.json"
PAGE = BASE / "MusicScout-V1.html"
TIMEOUT = 6
REFRESH_EVERY_SECONDS = 15 * 60
# These pages publish both a visible chart week and ranked entries in their HTML.
# Additional countries remain on their saved, sourced snapshots until a parser
# is tested for that country's publisher.
SOURCES = {
    "France": "https://www.officialcharts.com/charts/french-singles-chart/",
    "United Kingdom": "https://www.officialcharts.com/charts/singles-chart/",
    "Ireland": "https://www.officialcharts.com/charts/irish-singles-chart/",
}
DATE_RE = re.compile(r"\b(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s*[-–]\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})\b")


class OfficialChartsParser(HTMLParser):
    """Read the publisher's chart-item-content rows; ignore navigation/ads."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.date_text = []
        self.depth = 0
        self.current = None
        self.capture = None

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        classes = set(props.get("class", "").split())
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "chart-item-content" in classes:
                self.depth = 1
                self.current = {"rank": "", "title": "", "artist": ""}
        if self.depth and tag == "strong" and self.current and not self.current["rank"]:
            self.capture = "rank"
        if self.depth and tag == "a" and self.current:
            if "chart-name" in classes:
                self.capture = "title"
            elif "chart-artist" in classes:
                self.capture = "artist"

    def handle_endtag(self, tag):
        if tag in ("strong", "a"):
            self.capture = None
        if tag == "div" and self.depth:
            self.depth -= 1
            if not self.depth and self.current:
                self.rows.append(self.current)
                self.current = None

    def handle_data(self, text):
        clean = html.unescape(text).strip()
        if not clean:
            return
        if self.capture and self.current is not None:
            self.current[self.capture] += clean
        if len(self.date_text) < 2000:
            self.date_text.append(clean)


def fetch_chart(country, url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MusicScout/1.0)"})
    with urlopen(request, timeout=TIMEOUT) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        document = response.read(2_000_000).decode("utf-8", errors="replace")
    parser = OfficialChartsParser()
    parser.feed(document)
    dates = DATE_RE.search(" ".join(parser.date_text))
    if not dates:
        raise ValueError("chart week not found")
    start = datetime.strptime(dates.group(1), "%d %B %Y").date()
    end = datetime.strptime(dates.group(2), "%d %B %Y").date()
    if start > end or start > date.today() or (end - start).days > 14:
        raise ValueError("invalid chart week")
    rows = parser.rows[:4]
    if len(rows) != 4 or [r["rank"] for r in rows] != ["1", "2", "3", "4"]:
        raise ValueError("publisher's top four positions could not be confirmed")
    if any(not r["title"] or not r["artist"] for r in rows):
        raise ValueError("publisher omitted a title or artist")
    return country, {"date": f"{start.strftime('%d %B %Y')} – {end.strftime('%d %B %Y')}",
                     "url": url, "tracks": [[r["title"], r["artist"]] for r in rows]}


def refresh_once():
    # Request only the known supported publishers, in parallel with short timeouts.
    # Never save partial rows or replace another country's data after an error.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch_chart, country, url): country for country, url in SOURCES.items()}
        changes = {}
        for future in as_completed(futures):
            country = futures[future]
            try:
                _, changes[country] = future.result()
            except Exception as exc:
                print(f"{country}: saved chart retained ({exc})", flush=True)
    if not changes:
        return
    data = json.loads(DATA.read_text(encoding="utf-8"))
    updated = False
    for country, result in changes.items():
        previous = data["national"].get(country)
        if not previous:
            continue
        old_week = DATE_RE.search(previous.get("date", ""))
        if old_week:
            old_start = datetime.strptime(old_week.group(1), "%d %B %Y").date()
            new_start = datetime.strptime(result["date"].split(" – ")[0], "%d %B %Y").date()
            if new_start < old_start:
                print(f"{country}: older publisher chart ignored", flush=True)
                continue
        if all(previous.get(key) == value for key, value in result.items()):
            print(f"{country}: already current", flush=True)
            continue
        data["national"][country] = {**previous, **result}
        updated = True
        print(f"{country}: {result['date']} (positions 1–4)", flush=True)
    if not updated:
        return
    temporary = DATA.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(DATA)


def refresh_loop():
    while True:
        refresh_once()
        time.sleep(REFRESH_EVERY_SECONDS)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    if not DATA.is_file() or not PAGE.is_file():
        raise SystemExit("Put MusicScout-auto.py, MusicScout-V1.html and MusicScout-chart-data.json in the same folder.")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    address = f"http://127.0.0.1:{server.server_port}/{PAGE.name}"
    print("MusicScout is ready from saved chart data:", address, flush=True)
    print("Leave this window open while using MusicScout; press Ctrl+C to close.", flush=True)
    threading.Thread(target=refresh_loop, daemon=True).start()
    webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMusicScout stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Open MusicScout or refresh chart JSON once")
    parser.add_argument("--refresh-only", action="store_true", help="Update saved JSON without opening a browser or server")
    arguments = parser.parse_args()
    if arguments.refresh_only:
        if not DATA.is_file():
            raise SystemExit("MusicScout-chart-data.json must be beside this script.")
        refresh_once()
    else:
        main()
