import unittest
from datetime import date

from app import due_date_highlight_color


class DueDateHighlightTests(unittest.TestCase):
    TODAY = date(2026, 11, 19)

    def test_overdue_and_near_due_dates_are_highlighted(self):
        self.assertEqual(due_date_highlight_color("2026-11-18", "진행중", self.TODAY), "#ffc2c2")
        self.assertEqual(due_date_highlight_color("2026-11-19", "진행중", self.TODAY), "#ffc2c2")
        self.assertEqual(due_date_highlight_color("2026-11-20", "진행중", self.TODAY), "#ffc2c2")
        self.assertEqual(due_date_highlight_color("2026-11-21", "진행중", self.TODAY), "#ffdda8")
        self.assertEqual(due_date_highlight_color("2026-11-22", "진행중", self.TODAY), "#ffdda8")
        self.assertEqual(due_date_highlight_color("2026-11-23", "진행중", self.TODAY), "#fff0a6")
        self.assertEqual(due_date_highlight_color("2026-11-26", "진행중", self.TODAY), "#fff0a6")

    def test_completed_orders_are_not_highlighted(self):
        self.assertIsNone(due_date_highlight_color("2026-11-18", "완료", self.TODAY))

    def test_missing_or_invalid_dates_are_not_highlighted(self):
        self.assertIsNone(due_date_highlight_color("", "진행중", self.TODAY))
        self.assertIsNone(due_date_highlight_color("invalid", "진행중", self.TODAY))

    def test_dates_more_than_seven_days_away_are_not_highlighted(self):
        self.assertIsNone(due_date_highlight_color("2026-11-27", "진행중", self.TODAY))


if __name__ == "__main__":
    unittest.main()
