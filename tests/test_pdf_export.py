from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader

from app import backup, database


class PdfExportTests(unittest.TestCase):
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

    @staticmethod
    def _item(index: int, *, long_note: bool = False):
        note = "確認繁體中文、換行及多頁內容完整。"
        if long_note:
            note *= 80
        return {
            "product_name": f"測試印刷品項 {index:02d}",
            "material": "象牙卡 240g",
            "size": "90x54",
            "finishing": "雙面彩色、裁切",
            "quantity": index,
            "unit": "盒",
            "unit_price": 100 + index,
            "note": note,
        }

    def _database_snapshot(self):
        conn = database.connect()
        snapshot = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
            for table in ("quotes", "quote_items", "orders", "order_items", "order_payments")
        }
        conn.close()
        return snapshot

    def test_quote_pdf_download_is_read_only_and_contains_fixed_notes(self):
        conn = database.connect()
        quote_id = database.create_quote_from_payload(conn, {
            "customer_name": "報價測試客戶",
            "customer_contact": "林小姐",
            "customer_phone": "05-2222-333",
            "mode": "normal",
            "tax_mode": "tax",
            "items": [self._item(index) for index in range(1, 4)],
        })
        conn.commit()
        conn.close()

        before = self._database_snapshot()
        response = self.client.get(f"/quotes/{quote_id}/pdf")
        after = self._database_snapshot()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertTrue(response.data.startswith(b"%PDF"))
        self.assertEqual(before, after)

        reader = PdfReader(BytesIO(response.data))
        self.assertEqual(len(reader.pages), 1)
        text = reader.pages[0].extract_text() or ""
        self.assertIn("報價單", text)
        self.assertIn("報價測試客戶", text)
        self.assertNotIn("報價中", text)
        self.assertNotIn("狀態：", text)
        self.assertIn("規格", text)
        self.assertNotIn("規格(mm)", text)
        self.assertIn("不同設備顯色方式不同", text)
        self.assertIn("成品裁切、成套、對位等可能有 ±2mm 誤差", text)
        self.assertIn("以上已包含排版製作稿件", text)

    def test_order_pdf_repeats_fixed_areas_and_shows_paid_balance(self):
        conn = database.connect()
        order_id = database.create_order_from_payload(conn, {
            "customer_name": "多頁訂單客戶",
            "customer_contact": "陳小姐",
            "customer_phone": "05-1234-567",
            "created_date": "2026-08-18",
            "delivery_date": "2026-08-25",
            "mode": "normal",
            "status": "印製中",
            "tax_mode": "tax",
            "items": [self._item(index, long_note=index == 12) for index in range(1, 31)],
        })
        conn.execute(
            "INSERT INTO order_payments (order_id,amount,note) VALUES (?,?,?)",
            (order_id, 5000, "訂金"),
        )
        conn.commit()
        conn.close()

        before = self._database_snapshot()
        detail = self.client.get(f"/orders/{order_id}")
        response = self.client.get(f"/orders/{order_id}/pdf")
        after = self._database_snapshot()

        self.assertEqual(detail.status_code, 200)
        self.assertIn("匯出 PDF".encode("utf-8"), detail.data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(before, after)

        reader = PdfReader(BytesIO(response.data))
        self.assertGreaterEqual(len(reader.pages), 3)
        page_texts = [page.extract_text() or "" for page in reader.pages]
        for page_text in page_texts:
            self.assertIn("訂單編號", page_text)
            self.assertIn("多頁訂單客戶", page_text)
            self.assertIn("商品敘述", page_text)
            self.assertIn("規格", page_text)
            self.assertNotIn("規格(mm)", page_text)
            self.assertIn("應付", page_text)
            self.assertIn("已收款", page_text)
            self.assertIn("5,000", page_text)
            self.assertNotIn("印製中", page_text)
            self.assertNotIn("狀態：", page_text)
        self.assertNotIn("不同設備顯色方式不同", page_texts[0])
        self.assertNotIn("未收", "\n".join(page_texts))
        self.assertEqual(page_texts[-1].count("已收款"), 1)
        self.assertIn("不同設備顯色方式不同", page_texts[-1])

        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 595.28, places=1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.height), 841.89, places=1)

    def test_order_pdf_hides_paid_hint_when_payment_is_zero(self):
        conn = database.connect()
        order_id = database.create_order_from_payload(conn, {
            "customer_name": "零付款客戶",
            "created_date": "2026-08-20",
            "mode": "normal",
            "status": "設計中",
            "items": [self._item(1)],
        })
        conn.commit()
        conn.close()

        response = self.client.get(f"/orders/{order_id}/pdf")
        self.assertEqual(response.status_code, 200)
        reader = PdfReader(BytesIO(response.data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("應付", text)
        self.assertNotIn("已收款", text)
        self.assertNotIn("未收", text)
        self.assertNotIn("設計中", text)
        self.assertNotIn("狀態：", text)

    def test_person_customer_name_is_not_repeated_as_contact(self):
        conn = database.connect()
        order_id = database.create_order_from_payload(conn, {
            "customer_name": "Chloe Wu",
            "customer_contact": "Chloe Wu",
            "created_date": "2026-08-20",
            "mode": "normal",
            "status": "設計中",
            "items": [self._item(1)],
        })
        conn.commit()
        conn.close()

        response = self.client.get(f"/orders/{order_id}/pdf")
        self.assertEqual(response.status_code, 200)
        reader = PdfReader(BytesIO(response.data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertEqual(text.count("Chloe Wu"), 1)


if __name__ == "__main__":
    unittest.main()
