import tempfile
import unittest
from pathlib import Path

from app import backup, database


class DashboardSearchTests(unittest.TestCase):
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
        from app.main import app
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def tearDown(self):
        database.DB_DIR = self.old_db_dir
        database.DB_PATH = self.old_db_path
        database.BACKUP_DIR = self.old_backup_dir
        self.temp.cleanup()

    def _create_order(self, customer, product, material, created_date, note=""):
        conn = database.connect()
        order_id = database.create_order_from_payload(conn, {
            "customer_name": customer,
            "created_date": created_date,
            "mode": "normal",
            "status": "完結",
            "items": [{
                "product_name": product,
                "material": material,
                "size": "90×54mm",
                "finishing": "霧膜",
                "quantity": 5,
                "unit": "盒",
                "unit_price": 600,
                "note": note,
            }],
        })
        conn.commit()
        conn.close()
        return order_id

    def test_relaxed_multi_token_search_ranks_by_match_count(self):
        best_id = self._create_order("瑞塔的小紅屋", "名片", "象牙卡", "2026-08-18", "再版使用")
        self._create_order("瑞塔的小紅屋", "海報", "銅版紙", "2026-08-17")
        self._create_order("其他客戶", "名片", "象牙卡", "2026-08-16")

        response = self.client.get("/api/dashboard/order-search", query_string={"q": "瑞塔 + 名片 + 象牙卡"})
        self.assertEqual(response.status_code, 200)
        rows = response.get_json()
        self.assertEqual([row["matched_token_count"] for row in rows], [3, 2, 1])
        self.assertEqual(rows[0]["order_id"], best_id)
        self.assertEqual(rows[0]["query_token_count"], 3)
        self.assertEqual(rows[0]["matched_tokens"], ["瑞塔", "名片", "象牙卡"])
        self.assertEqual(rows[0]["items"][0]["product_name"], "名片")
        self.assertEqual(rows[0]["items"][0]["subtotal"], 3000)
        self.assertEqual(rows[0]["items"][0]["note"], "再版使用")

    def test_space_and_plus_have_same_meaning(self):
        self._create_order("瑞塔的小紅屋", "名片", "象牙卡", "2026-08-18")
        plus_rows = self.client.get("/api/dashboard/order-search", query_string={"q": "瑞塔+名片"}).get_json()
        space_rows = self.client.get("/api/dashboard/order-search", query_string={"q": "瑞塔 名片"}).get_json()
        self.assertEqual(
            [(row["order_id"], row["matched_token_count"]) for row in plus_rows],
            [(row["order_id"], row["matched_token_count"]) for row in space_rows],
        )
        full_width_rows = self.client.get(
            "/api/dashboard/order-search",
            query_string={"q": "瑞塔＋名片＋瑞塔"},
        ).get_json()
        self.assertEqual(full_width_rows[0]["query_token_count"], 2)
        self.assertEqual(full_width_rows[0]["matched_token_count"], 2)

    def test_full_detail_api_and_home_modal(self):
        order_id = self._create_order("瑞塔的小紅屋", "名片", "象牙卡", "2026-08-18", "保留色樣")
        detail = self.client.get(f"/api/orders/{order_id}/history-detail")
        self.assertEqual(detail.status_code, 200)
        payload = detail.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["items"][0]["note"], "保留色樣")
        self.assertIn("paid_total", payload)
        self.assertIn("balance", payload)

        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="dashboard-order-modal"', page.data)
        self.assertIn("查看完整內容".encode(), page.data)
        self.assertIn("上一筆".encode(), page.data)
        self.assertIn("下一筆".encode(), page.data)

        health = self.client.get("/healthz").get_json()
        self.assertTrue(health["ok"])
        self.assertEqual(health["app"], "PrintShop")
        self.assertEqual(health["version"], "3.7.1")
        self.assertTrue(health["data_root"])


if __name__ == "__main__":
    unittest.main()
