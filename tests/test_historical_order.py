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

    def _create(self, name: str, created_date: str, delivery_date: str = ""):
        conn = database.connect()
        order_id = database.create_order_from_payload(conn, {
            "customer_name": name,
            "created_date": created_date,
            "delivery_date": delivery_date,
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

    def test_order_detail_hides_whole_quantity_decimal_and_keeps_real_decimals(self):
        conn = database.connect()
        order_id = database.create_order_from_payload(conn, {
            "customer_name": "數字格式測試客戶",
            "created_date": "2026-08-20",
            "mode": "normal",
            "items": [
                {
                    "product_name": "整數數量品項",
                    "quantity": 200,
                    "unit": "張",
                    "unit_price": 2.5,
                },
                {
                    "product_name": "小數數量品項",
                    "quantity": 1.5,
                    "unit": "式",
                    "unit_price": 4.5,
                },
            ],
        })
        conn.commit()
        conn.close()

        from app.main import app

        app.config.update(TESTING=True)
        page = app.test_client().get(f"/orders/{order_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"<td>200</td>", page.data)
        self.assertNotIn(b"<td>200.0</td>", page.data)
        self.assertIn(b"<td>1.5</td>", page.data)
        self.assertIn(b"<td>2.5</td>", page.data)
        self.assertIn(b"<td>4.5</td>", page.data)

    def test_home_completed_orders_use_delivery_date_before_created_date(self):
        recent_delivery = self._create("近期交貨客戶", "2026-08-01", "2026-08-26")
        historical_delivery = self._create("歷史交貨客戶", "2026-08-20", "2026-08-21")

        from app.main import app

        app.config.update(TESTING=True)
        page = app.test_client().get("/")
        self.assertEqual(page.status_code, 200)
        self.assertLess(
            page.data.index(recent_delivery["order_number"].encode()),
            page.data.index(historical_delivery["order_number"].encode()),
        )


if __name__ == "__main__":
    unittest.main()
