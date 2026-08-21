from datetime import datetime, timedelta
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from app import backup, database


class HistoricalOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old_db_dir = database.DB_DIR
        self.old_db_path = database.DB_PATH
        self.old_backup_dir = database.BACKUP_DIR
        database.DB_DIR = root / "database"
        database.DB_PATH = database.DB_DIR / "printshop.db"
        database.BACKUP_DIR = root / "backups"
        backup._last_automatic_date = None
        database.init_database()

    def tearDown(self):
        database.DB_DIR = self.old_db_dir
        database.DB_PATH = self.old_db_path
        database.BACKUP_DIR = self.old_backup_dir
        self.temp.cleanup()

    def _create(self, name: str, created_date: str):
        conn = database.connect()
        order_id = database.create_order_from_payload(conn, {
            "customer_name": name,
            "created_date": created_date,
            "mode": "normal",
            "status": "完結",
            "items": [{
                "product_name": "名片",
                "quantity": 1,
                "unit": "盒",
                "unit_price": 500,
            }],
        })
        conn.commit()
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        conn.close()
        return order

    def test_historical_time_controls_number_and_utc_storage(self):
        first = self._create("歷史客戶甲", "2024-03-15")
        second = self._create("歷史客戶乙", "2024-03-15")

        self.assertEqual(first["order_number"], "24031501")
        self.assertEqual(second["order_number"], "24031502")
        self.assertEqual(first["created_at"], "2024-03-15 04:00:00")
        self.assertEqual(database.display_date(first["created_at"]), "2024-03-15 12:00")

    def test_future_time_is_rejected(self):
        future = (datetime.now(ZoneInfo("Asia/Taipei")) + timedelta(days=1)).strftime("%Y-%m-%d")
        conn = database.connect()
        with self.assertRaisesRegex(ValueError, "不可晚於今天"):
            database.create_order_from_payload(conn, {
                "customer_name": "未來客戶",
                "created_date": future,
                "mode": "normal",
                "items": [],
            })
        conn.close()

    def test_new_order_page_has_datetime_field(self):
        from app.main import app

        app.config.update(TESTING=True)
        response = app.test_client().get("/orders/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'type="date"', response.data)
        self.assertIn(b'name="created_date"', response.data)
        self.assertNotIn(b'type="datetime-local"', response.data)


if __name__ == "__main__":
    unittest.main()
