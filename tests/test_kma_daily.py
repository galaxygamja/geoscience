import calendar
import unittest

from scripts.fetch_kma_daily import parse_month


def calendar_html(year=2025, month=7):
    html = f"인천(유)/</span> {year}년 {month}월<table class='table-cal'>"
    # KMA calendars use Sunday first.
    for week in calendar.Calendar(firstweekday=6).monthdayscalendar(year, month):
        html += "<tr>" + "".join(f"<td>{str(d) + '일' if d else ''}</td>" for d in week) + "</tr>"
        html += "<tr>" + "".join(
            f"<td>일강수량:{'0.0mm' if d == 1 else '2.3mm' if d == 2 else '-'}</td>" if d else "<td></td>"
            for d in week) + "</tr>"
    return html + "</table>"


class KmaDailyTests(unittest.TestCase):
    def test_calendar_alignment_zero_trace_and_dash(self):
        rows = parse_month(calendar_html(), 2025, 7)
        self.assertEqual(len(rows), 31)
        self.assertEqual(rows["2025-07-01"], {"rain_mm": 0.0, "raw": "0.0", "source_month": 7})
        self.assertEqual(rows["2025-07-02"]["rain_mm"], 2.3)
        self.assertIsNone(rows["2025-07-03"]["rain_mm"])
        self.assertEqual(len(parse_month(calendar_html(2025, 2), 2025, 2)), 28)

    def test_wrong_heading_missing_unit_or_date_rejected(self):
        html = calendar_html()
        with self.assertRaises(ValueError):
            parse_month(html, 2025, 8)
        with self.assertRaises(ValueError):
            parse_month(html.replace("2.3mm", "2.3"), 2025, 7)
        with self.assertRaises(ValueError):
            parse_month(html.replace("31일", "30일"), 2025, 7)


if __name__ == "__main__":
    unittest.main()
