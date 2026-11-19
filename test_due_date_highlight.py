import os
import unittest
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QBrush, QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTableWidget, QTableWidgetItem

from app import DueDateHighlightDelegate, due_date_highlight_color

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


class DueDateHighlightRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def render_cell(self, color, selected=False):
        table = QTableWidget(1, 1)
        item = QTableWidgetItem("2026-11-24")
        item.setBackground(QBrush(QColor(color)))
        item.setForeground(QBrush(QColor("#4a2525")))
        table.setItem(0, 0, item)

        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 120, 40)
        option.palette = table.palette()
        option.font = table.font()
        option.state = QStyle.State_Enabled
        if selected:
            option.state |= QStyle.State_Selected

        image = QImage(120, 40, QImage.Format_ARGB32)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        DueDateHighlightDelegate(table).paint(painter, option, table.model().index(0, 0))
        painter.end()
        return image

    def test_highlight_color_is_actually_painted(self):
        image = self.render_cell("#ffc2c2")
        self.assertEqual(image.pixelColor(2, 20), QColor("#ffc2c2"))
        self.assertEqual(image.pixelColor(118, 20), QColor("#ffc2c2"))

    def test_yellow_highlight_color_is_actually_painted(self):
        image = self.render_cell("#fff0a6")
        self.assertEqual(image.pixelColor(2, 20), QColor("#fff0a6"))

    def test_selected_cell_uses_selection_background(self):
        image = self.render_cell("#ffc2c2", selected=True)
        self.assertNotEqual(image.pixelColor(2, 20), QColor("#ffc2c2"))


if __name__ == "__main__":
    unittest.main()
