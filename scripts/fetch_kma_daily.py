"""Fetch public KMA monthly calendars and preserve unresolved rain dashes.

Uses only the Python standard library. Each pair of calendar rows is matched
column by column; weekday alignment and every calendar day are checked.
"""
from __future__ import annotations

import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen


class CalendarParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.row = None
        self.cell = None
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and "table-cal" in attrs.get("class", "").split():
            self.active = True
        elif self.active and tag == "tr":
            self.row = []
        elif self.active and tag == "td":
            self.cell = []

    def handle_data(self, data):
        if self.active and self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if not self.active:
            return
        if tag == "td" and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None
        elif tag == "table":
            self.active = False


def parse_month(html: str, year: int, month: int) -> dict:
    if not re.search(rf"인천\(유\)/</span>\s*{year}년\s*{month}월", html):
        raise ValueError(f"Station/year/month heading mismatch: {year}-{month:02d}")
    parser = CalendarParser()
    parser.feed(html)
    if not parser.rows or len(parser.rows) % 2:
        raise ValueError("Calendar must have paired date/data rows")
    days = {}
    for offset in range(0, len(parser.rows), 2):
        labels, values = parser.rows[offset:offset + 2]
        if len(labels) != 7 or len(values) != 7:
            raise ValueError("Calendar row must have seven weekday cells")
        for column, (label, value) in enumerate(zip(labels, values)):
            if not label:
                if value:
                    raise ValueError("Out-of-month cell contains data")
                continue
            match = re.fullmatch(r"(\d{1,2})일", label)
            if not match:
                raise ValueError(f"Unrecognized day cell: {label!r}")
            day = date(year, month, int(match.group(1)))
            if (day.weekday() + 1) % 7 != column:
                raise ValueError(f"Calendar weekday misalignment: {day}")
            rain = re.search(r"일강수량:\s*(-|\d+(?:\.\d+)?)\s*(mm)?", value)
            if not rain:
                raise ValueError(f"Missing precipitation label: {day}")
            raw = rain.group(1)
            if raw != "-" and rain.group(2) != "mm":
                raise ValueError(f"Missing precipitation unit: {day}")
            key = day.isoformat()
            if key in days:
                raise ValueError(f"Duplicate day: {key}")
            days[key] = {"rain_mm": None if raw == "-" else float(raw),
                         "raw": raw, "source_month": month}
    expected = {date(year, month, day).isoformat()
                for day in range(1, calendar.monthrange(year, month)[1] + 1)}
    if set(days) != expected:
        raise ValueError(f"Incomplete calendar: {year}-{month:02d}")
    return days


def fetch_month(year: int, month: int):
    url = ("https://www.weather.go.kr/w/weather/land/past-obs/obs-by-day.do"
           f"?stn=112&yy={year}&mm={month}&obs=1")
    request = Request(url, headers={"User-Agent": "geoscience-research/1.0"})
    with urlopen(request, timeout=30) as response:
        content = response.read()
    days = parse_month(content.decode("utf-8"), year, month)
    source = {"url": url, "sha256": hashlib.sha256(content).hexdigest(),
              "retrieved_date": datetime.now(timezone.utc).date().isoformat(),
              "month": month}
    return source, days


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--months", type=int, nargs="+", default=[1, 2, 3, 7, 8, 9])
    parser.add_argument("--output", type=Path,
                        default=Path("data/kma_daily_precipitation_2025.json"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new --output path; existing source evidence is not overwritten")
    months = sorted(set(args.months))
    if any(month not in range(1, 13) for month in months):
        parser.error("Months must be between 1 and 12")
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda month: fetch_month(args.year, month), months))
    days = {day: value for _, month_days in results for day, value in month_days.items()}
    payload = {"station": "112", "year": args.year,
               "sources": [source for source, _ in results], "days": dict(sorted(days.items()))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for source, month_days in results:
        keys = sorted(month_days)
        numeric = sum(value["rain_mm"] is not None for value in month_days.values())
        print(f"month={source['month']:02d} rows={len(keys)} numeric={numeric} "
              f"dash={len(keys)-numeric} first={keys[0]} last={keys[-1]}")
    print(f"Wrote {len(days)} days to {args.output}; '-' remains null.")


if __name__ == "__main__":
    main()
