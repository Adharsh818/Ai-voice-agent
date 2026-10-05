"""
dateparse: the full table from docs/IMPLEMENTATION_PLAN.md section 5.2.

Each row: (text, today, expecting, expected date, expected time, expected issue codes)
    date  None | "YYYY-MM-DD" | ("range"|"earliest", start, end) | ("set", [dates])
    time  None | "HH:MM" (exact) | ("window", start, end) | ("ambiguous", t1, t2) | "any"
"""

import unittest
from datetime import date

from dateparse import parse_when

WED = date(2026, 9, 30)    # Wednesday, month-end
SUN = date(2026, 10, 4)    # Sunday
MON = date(2026, 10, 5)    # Monday
YEAR_END = date(2026, 12, 30)  # Wednesday
FEB = date(2027, 2, 27)    # Saturday, not a leap year

CASES = [
    # --- relative days
    ("today", WED, None, "2026-09-30", None, []),
    ("tomorrow", WED, None, "2026-10-01", None, []),
    ("day after tomorrow", WED, None, "2026-10-02", None, []),
    ("tmrw", WED, None, "2026-10-01", None, []),
    ("yesterday", WED, None, None, None, ["PAST"]),
    ("in 3 days", WED, None, "2026-10-03", None, []),
    ("in 2 weeks", WED, None, "2026-10-14", None, []),
    ("after 5 days", WED, None, "2026-10-05", None, []),
    ("3 days from now", WED, None, "2026-10-03", None, []),
    ("in a week", WED, None, "2026-10-07", None, []),
    ("in a couple of days", WED, None, ("range", "2026-10-02", "2026-10-03"), None, []),
    # --- weekdays
    ("Monday", WED, None, "2026-10-05", None, []),
    ("next Monday", WED, None, "2026-10-05", None, []),
    ("coming Monday", WED, None, "2026-10-05", None, []),
    ("this Monday", WED, None, "2026-10-05", None, []),
    ("Wednesday", WED, None, "2026-10-07", None, []),
    ("this Wednesday", WED, None, "2026-09-30", None, []),
    ("next Wednesday", WED, None, "2026-10-07", None, []),
    ("Monday after next", WED, None, "2026-10-12", None, []),
    ("next to next Monday", WED, None, "2026-10-12", None, []),
    ("next week Monday", WED, None, "2026-10-05", None, []),
    ("Monday next week", WED, None, "2026-10-05", None, []),
    ("next week Friday", WED, None, "2026-10-09", None, []),
    ("Friday", WED, None, "2026-10-02", None, []),
    ("Saturday", WED, None, "2026-10-03", None, []),
    ("Sunday", WED, None, None, None, ["SUNDAY"]),
    ("next Sunday", WED, None, None, None, ["SUNDAY"]),
    ("Monday or Tuesday", WED, None, ("set", ["2026-10-05", "2026-10-06"]), None, []),
    ("Saturday or Sunday", WED, None, "2026-10-03", None, ["SUNDAY"]),
    ("Thursday or Friday afternoon", WED, None, ("set", ["2026-10-01", "2026-10-02"]),
     ("window", "12:00", "16:00"), []),
    # --- day numbers
    ("the 3rd", WED, None, "2026-10-03", None, []),
    ("3rd", WED, None, "2026-10-03", None, []),
    ("on the 26th", WED, None, "2026-10-26", None, []),
    ("the 30th", WED, None, "2026-09-30", None, []),
    ("the 29th", WED, None, "2026-10-29", None, []),
    ("31st", WED, None, "2026-10-31", None, []),
    ("twenty-first", WED, None, "2026-10-21", None, []),
    ("the first", WED, None, "2026-10-01", None, []),
    ("26", WED, "date", "2026-10-26", None, []),
    ("on the 9", WED, "date", "2026-10-09", None, []),
    # --- month and day
    ("fifth of October", WED, None, "2026-10-05", None, []),
    ("5 October", WED, None, "2026-10-05", None, []),
    ("October 5", WED, None, "2026-10-05", None, []),
    ("October 5th 2026", WED, None, "2026-10-05", None, []),
    ("5th Oct", WED, None, "2026-10-05", None, []),
    ("25 October", WED, None, None, None, ["SUNDAY"]),
    ("15 September", WED, None, None, None, ["BEYOND_HORIZON"]),
    ("31 September", WED, None, None, None, ["INVALID_DAY"]),
    ("30 February", WED, None, None, None, ["INVALID_DAY"]),
    ("16 November", WED, None, "2026-11-16", None, []),
    ("10 December", WED, None, None, None, ["BEYOND_HORIZON"]),
    ("the 2nd of May", WED, None, None, None, ["BEYOND_HORIZON"]),
    # --- numeric dates: day-first
    ("05/10", WED, None, "2026-10-05", None, []),
    ("5/10/2026", WED, None, "2026-10-05", None, []),
    ("05-10-26", WED, None, "2026-10-05", None, []),
    ("13/10", WED, None, "2026-10-13", None, []),
    ("10/13", WED, None, None, None, ["INVALID_DAY"]),
    ("1/9/2026", WED, None, None, None, ["PAST"]),
    # --- weeks, weekends, months
    ("next week", WED, None, ("range", "2026-10-05", "2026-10-10"), None, []),
    ("the week after next", WED, None, ("range", "2026-10-12", "2026-10-17"), None, []),
    ("this week", WED, None, ("range", "2026-09-30", "2026-10-03"), None, []),
    ("later this week", WED, None, ("range", "2026-10-01", "2026-10-03"), None, []),
    ("end of the week", WED, None, ("range", "2026-10-02", "2026-10-03"), None, []),
    ("this weekend", WED, None, "2026-10-03", None, []),
    ("next weekend", WED, None, "2026-10-10", None, []),
    ("end of the month", WED, None, "2026-09-30", None, []),
    ("next month", WED, None, ("range", "2026-10-01", "2026-10-31"), None, []),
    ("early next month", WED, None, ("range", "2026-10-01", "2026-10-07"), None, []),
    ("2nd week of October", WED, None, ("range", "2026-10-08", "2026-10-14"), None, []),
    ("last week of October", WED, None, ("range", "2026-10-25", "2026-10-31"), None, []),
    ("earliest", WED, None, ("earliest", "2026-09-30", "2026-11-29"), "any", []),
    ("as soon as possible", WED, None, ("earliest", "2026-09-30", "2026-11-29"), "any", []),
    ("any day", WED, None, ("earliest", "2026-09-30", "2026-11-29"), "any", []),
    # --- on a Sunday
    ("today", SUN, None, None, None, ["SUNDAY"]),
    ("tomorrow", SUN, None, "2026-10-05", None, []),
    ("this week", SUN, None, ("range", "2026-10-05", "2026-10-10"), None, []),
    ("next week", SUN, None, ("range", "2026-10-05", "2026-10-10"), None, []),
    ("Monday", SUN, None, "2026-10-05", None, []),
    ("this Sunday", SUN, None, None, None, ["SUNDAY"]),
    # --- on a Monday
    ("Monday", MON, None, "2026-10-12", None, []),
    ("this Monday", MON, None, "2026-10-05", None, []),
    ("next Monday", MON, None, "2026-10-12", None, []),
    ("Monday next week", MON, None, "2026-10-12", None, []),
    # --- year end
    ("the 4th", YEAR_END, None, "2027-01-04", None, []),
    ("the 3rd", YEAR_END, None, None, None, ["SUNDAY"]),
    ("5 January", YEAR_END, None, "2027-01-05", None, []),
    ("tomorrow", YEAR_END, None, "2026-12-31", None, []),
    ("next month", YEAR_END, None, ("range", "2027-01-01", "2027-01-31"), None, []),
    ("31st", YEAR_END, None, "2026-12-31", None, []),
    ("early next month", YEAR_END, None, ("range", "2027-01-01", "2027-01-07"), None, []),
    # --- short month, non-leap year
    ("30th", FEB, None, "2027-03-30", None, []),
    ("29 February", FEB, None, None, None, ["INVALID_DAY"]),
    ("the 28th", FEB, None, None, None, ["SUNDAY"]),
    ("end of the month", FEB, None, ("range", "2027-02-27", "2027-02-28"), None, []),
    # --- clock times
    ("5 pm", WED, None, None, "17:00", []),
    ("5:30 pm", WED, None, None, "17:30", []),
    ("5.30 pm", WED, None, None, "17:30", []),
    ("5 30 pm", WED, None, None, "17:30", []),
    ("5pm", WED, None, None, "17:00", []),
    ("5 p.m.", WED, None, None, "17:00", []),
    ("one pm", WED, None, None, "13:00", []),
    ("at 5", WED, None, None, "17:00", []),
    ("around 11", WED, None, None, "11:00", []),
    ("at 12", WED, None, None, "12:00", []),
    ("at 7", WED, None, None, ("ambiguous", "07:00", "19:00"), []),
    ("at 8:30", WED, None, None, ("ambiguous", "08:30", "20:30"), []),
    ("at 7 in the evening", WED, None, None, "19:00", []),
    ("7 in the morning", WED, None, None, "07:00", []),
    ("8 pm", WED, None, None, "20:00", []),
    ("8:30 pm", WED, None, None, "20:30", []),
    ("9 pm", WED, None, None, None, ["OUTSIDE_HOURS"]),
    ("10 pm", WED, None, None, None, ["OUTSIDE_HOURS"]),
    ("6 am", WED, None, None, None, ["OUTSIDE_HOURS"]),
    ("noon", WED, None, None, "12:00", []),
    ("midnight", WED, None, None, None, ["OUTSIDE_HOURS"]),
    ("half past four", WED, None, None, "16:30", []),
    ("quarter past ten", WED, None, None, "10:15", []),
    ("quarter to five", WED, None, None, "16:45", []),
    ("5 o'clock", WED, None, None, "17:00", []),
    ("evening 5", WED, None, None, "17:00", []),
    ("5 in the evening", WED, None, None, "17:00", []),
    ("4:15", WED, None, None, "16:15", []),
    ("I am free at 5", WED, None, None, "17:00", []),
    ("5", WED, "time", None, "17:00", []),
    ("7", WED, "time", None, ("ambiguous", "07:00", "19:00"), []),
    ("11", WED, "time", None, "11:00", []),
    ("4 30", WED, "time", None, "16:30", []),
    # --- windows
    ("morning", WED, None, None, ("window", "07:00", "12:00"), []),
    ("evening", WED, None, None, ("window", "16:00", "21:00"), []),
    ("afternoon", WED, None, None, ("window", "12:00", "16:00"), []),
    ("after lunch", WED, None, None, ("window", "14:30", "17:00"), []),
    ("early morning", WED, None, None, ("window", "07:00", "09:00"), []),
    ("first thing in the morning", WED, None, None, ("window", "07:00", "09:00"), []),
    ("after school", WED, None, None, ("window", "16:00", "21:00"), []),
    ("tonight", WED, None, "2026-09-30", ("window", "18:00", "21:00"), []),
    ("this evening", WED, None, "2026-09-30", ("window", "16:00", "21:00"), []),
    ("after 5", WED, None, None, ("window", "17:00", "21:00"), []),
    ("after 7", WED, None, None, ("window", "19:00", "21:00"), []),
    ("before 11 am", WED, None, None, ("window", "07:00", "11:00"), []),
    ("between 4 and 6", WED, None, None, ("window", "16:00", "18:00"), []),
    ("between 10 and 12", WED, None, None, ("window", "10:00", "12:00"), []),
    ("from 11 to 1", WED, None, None, ("window", "11:00", "13:00"), []),
    ("5-6 pm", WED, None, None, ("window", "17:00", "18:00"), []),
    ("5 to 6 pm", WED, None, None, ("window", "17:00", "18:00"), []),
    ("any time", WED, None, None, "any", []),
    # --- date and time together
    ("next Monday around 5", WED, None, "2026-10-05", "17:00", []),
    ("tomorrow at 10:30 am", WED, None, "2026-10-01", "10:30", []),
    ("on the 5th at 5", WED, None, "2026-10-05", "17:00", []),
    ("Friday evening", WED, None, "2026-10-02", ("window", "16:00", "21:00"), []),
    ("sometime next week, evenings", WED, None, ("range", "2026-10-05", "2026-10-10"),
     ("window", "16:00", "21:00"), []),
    ("October twenty first at four thirty pm", WED, None, "2026-10-21", "16:30", []),
    ("day after tomorrow morning", WED, None, "2026-10-02", ("window", "07:00", "12:00"), []),
    ("Saturday at 9", WED, None, "2026-10-03", "09:00", []),
    ("tomorrow after 5", WED, None, "2026-10-01", ("window", "17:00", "21:00"), []),
    ("whenever is earliest", WED, None, ("earliest", "2026-09-30", "2026-11-29"), "any", []),
    ("Sunday at 5", WED, None, None, "17:00", ["SUNDAY"]),
    # --- punctuation from the recogniser around number words
    ("Next Monday around five.", WED, None, "2026-10-05", "17:00", []),
    ("The twenty sixth, in the evening.", WED, None, "2026-10-26", ("window", "16:00", "21:00"), []),
    ("Seven.", WED, "time", None, ("ambiguous", "07:00", "19:00"), []),
    ("On the fifth?", WED, None, "2026-10-05", None, []),
    # --- things that are not dates or times
    ("the first one", WED, None, None, None, []),
    ("I want a cleaning", WED, None, None, None, []),
    ("one second please", WED, None, None, None, []),
    ("my name is Priya", WED, None, None, None, []),
    ("at 4 may work", WED, None, None, "16:00", []),
    ("no one told me", WED, None, None, None, []),
    ("7", WED, None, None, None, []),
    ("26", WED, None, None, None, []),
    ("9876543210", WED, None, None, None, []),
    ("9876543210", WED, "time", None, None, []),
    ("which one is earlier", WED, None, None, None, []),
    ("", WED, None, None, None, []),
]


def _fmt_date(dc):
    if dc is None:
        return None
    if dc.kind == "exact":
        return dc.start.isoformat()
    if dc.kind == "set":
        return ("set", [d.isoformat() for d in dc.only])
    return (dc.kind, dc.start.isoformat(), dc.end.isoformat())


def _hm(t):
    return t.strftime("%H:%M")


def _fmt_time(tc):
    if tc is None:
        return None
    if tc.kind == "exact":
        return _hm(tc.start)
    if tc.kind == "window":
        return ("window", _hm(tc.start), _hm(tc.end))
    if tc.kind == "ambiguous":
        return ("ambiguous",) + tuple(_hm(c) for c in tc.candidates)
    return "any"


class DateParseTableTests(unittest.TestCase):
    def test_table(self):
        self.assertGreaterEqual(len(CASES), 120)
        for text, today, expecting, want_date, want_time, want_issues in CASES:
            with self.subTest(text=text, today=today, expecting=expecting):
                w = parse_when(text, today=today, expecting=expecting)
                self.assertEqual(_fmt_date(w.date), want_date)
                self.assertEqual(_fmt_time(w.time), want_time)
                self.assertEqual(sorted(i.code for i in w.issues), sorted(want_issues))

    def test_off_grid_times_are_flagged_for_nearest_slots(self):
        self.assertFalse(parse_when("4:15", today=WED).time.on_grid)
        self.assertTrue(parse_when("4:30 pm", today=WED).time.on_grid)

    def test_uses_the_clinic_clock_by_default(self):
        from datetime import datetime
        import clock
        with clock.frozen(datetime(2026, 10, 1, 0, 30)):    # still 30 Sep in UTC
            self.assertEqual(parse_when("tomorrow").date.start, date(2026, 10, 2))


if __name__ == "__main__":
    unittest.main()
