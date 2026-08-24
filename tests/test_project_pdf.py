from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader

from app import backup, database


class ProjectPdfTests(unittest.TestCase):
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
    def _item(name, quantity, unit_price):
        return {
            "product_name": name,
            "material": "象牙卡 240g",
            "size": "A5",
            "finishing": "雙面彩色、裁切",
            "quantity": quantity,
            "unit": "張",
            "unit_price": unit_price,
            "note": "專案分組 PDF 測試",
        }

    def _snapshot(self):
        conn = database.connect()
        result = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in (
                "customers",
                "projects",
                "orders",
                "order_work_units",
                "order_items",
                "order_payments",
            )
        }
        conn.close()
        return result

    def test_project_pdf_groups_units_excludes_void_orders_and_is_read_only(self):
        conn = database.connect()
        customer_id = conn.execute(
            """
            INSERT INTO customers(name,customer_type,contact_person,phone,category)
            VALUES ('幸福學校教育處','school','林老師','05-1234-5678','學校')
            """
        ).lastrowid
        project_id = conn.execute(
            """
            INSERT INTO projects(customer_id,project_name,note)
            VALUES (?,?,?)
            """,
            (customer_id, "114 年度招生印刷專案", "請依工作單位分組請款"),
        ).lastrowid
        conn.commit()

        order_one = database.create_order_from_payload(
            conn,
            {
                "customer_id": customer_id,
                "customer_name": "幸福學校教育處",
                "project_id": project_id,
                "project_name": "114 年度招生印刷專案",
                "created_date": "2026-08-18",
                "mode": "project",
                "status": "印製中",
                "tax_mode": "tax",
                "work_units": [
                    {
                        "name": "A 單位",
                        "note": "招生組",
                        "items": [
                            self._item(f"A 組招生單 {index:02d}", 1, 100)
                            for index in range(1, 31)
                        ],
                    },
                    {
                        "name": "B 單位",
                        "note": "教務組",
                        "items": [
                            self._item("B 組海報", 1, 250),
                            self._item("B 組邀請卡", 1, 250),
                        ],
                    },
                ],
            },
        )
        conn.execute(
            "INSERT INTO order_payments(order_id,amount,note) VALUES (?,?,?)",
            (order_one, 500, "訂金"),
        )
        conn.commit()

        database.create_order_from_payload(
            conn,
            {
                "customer_id": customer_id,
                "customer_name": "幸福學校教育處",
                "project_id": project_id,
                "project_name": "114 年度招生印刷專案",
                "created_date": "2026-08-19",
                "mode": "normal",
                "status": "設計中",
                "tax_mode": "none",
                "items": [self._item("臨時追加布條", 3, 200)],
            },
        )
        conn.commit()

        database.create_order_from_payload(
            conn,
            {
                "customer_id": customer_id,
                "customer_name": "幸福學校教育處",
                "project_id": project_id,
                "project_name": "114 年度招生印刷專案",
                "created_date": "2026-08-20",
                "mode": "normal",
                "status": "廢單",
                "items": [self._item("廢單測試品項", 1, 9999)],
            },
        )
        conn.commit()
        conn.close()

        detail = self.client.get(f"/projects/{project_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("匯出專案 PDF".encode("utf-8"), detail.data)

        before = self._snapshot()
        response = self.client.get(f"/projects/{project_id}/pdf")
        after = self._snapshot()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(before, after)

        reader = PdfReader(BytesIO(response.data))
        self.assertGreaterEqual(len(reader.pages), 2)
        page_texts = [page.extract_text() or "" for page in reader.pages]
        all_text = "\n".join(page_texts)

        for page_text in page_texts:
            self.assertIn("專案訂單", page_text)
            self.assertIn("請款單位", page_text)
            self.assertIn("幸福學校教育處", page_text)
            self.assertIn("商品敘述", page_text)

        self.assertIn("114 年度招生印刷專案", all_text)
        self.assertIn("A 單位", all_text)
        self.assertIn("B 單位", all_text)
        self.assertIn("未分工作單位", all_text)
        self.assertIn("（續）", all_text)
        self.assertEqual(all_text.count("單位小計"), 3)
        self.assertIn("整案品項小計", page_texts[-1])
        self.assertIn("整案總計", page_texts[-1])
        self.assertIn("4,275", page_texts[-1])
        self.assertIn("已收款", all_text)
        self.assertIn("500", all_text)
        self.assertNotIn("狀態：", all_text)
        self.assertNotIn("印製中", all_text)
        self.assertNotIn("設計中", all_text)
        self.assertNotIn("廢單測試品項", all_text)
        self.assertNotIn("整案總計", page_texts[0])
        self.assertNotIn("不同設備顯色方式不同", page_texts[0])
        self.assertIn("不同設備顯色方式不同", page_texts[-1])


if __name__ == "__main__":
    unittest.main()
