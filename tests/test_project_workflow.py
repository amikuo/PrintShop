from pathlib import Path
import tempfile
import unittest

from app import backup, database


class ProjectWorkflowTests(unittest.TestCase):
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

        conn = database.connect()
        self.customer_id = int(conn.execute(
            "INSERT INTO customers(name,customer_type) VALUES ('流程測試客戶','company')"
        ).lastrowid)
        self.project_id = int(conn.execute(
            "INSERT INTO projects(customer_id,project_name) VALUES (?, '流程測試專案')",
            (self.customer_id,),
        ).lastrowid)
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DB_DIR = self.old_db_dir
        database.DB_PATH = self.old_db_path
        database.BACKUP_DIR = self.old_backup_dir
        self.temp.cleanup()

    @staticmethod
    def _item(price):
        return {
            "product_name": "測試印刷品",
            "quantity": 1,
            "unit": "份",
            "unit_price": price,
        }

    def _create_order(self, *, price, status="完結", tax_mode="none"):
        conn = database.connect()
        order_id = database.create_order_from_payload(
            conn,
            {
                "customer_id": self.customer_id,
                "customer_name": "流程測試客戶",
                "project_id": self.project_id,
                "project_name": "流程測試專案",
                "created_date": "2026-08-20",
                "mode": "project",
                "status": status,
                "tax_mode": tax_mode,
                "work_units": [
                    {"name": "測試單位", "items": [self._item(price)]},
                ],
            },
        )
        conn.commit()
        conn.close()
        return order_id

    def _project_status(self):
        conn = database.connect()
        status = conn.execute(
            "SELECT status FROM projects WHERE id=?", (self.project_id,)
        ).fetchone()["status"]
        conn.close()
        return status

    def test_project_completion_depends_on_progress_not_payment(self):
        order_id = self._create_order(price=100, status="完結")

        from app.main import refresh_project_status

        conn = database.connect()
        self.assertEqual(refresh_project_status(conn, self.project_id), "已完成")
        conn.commit()
        self.assertEqual(
            conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM order_payments WHERE order_id=?",
                (order_id,),
            ).fetchone()[0],
            0,
        )
        conn.close()

        response = self.client.get(f"/projects/{self.project_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._project_status(), "已完成")

        conn = database.connect()
        quote_id = conn.execute(
            """
            INSERT INTO quotes(quote_number,quote_seq,customer_id,project_id,status)
            VALUES ('Q26082099',99,?,?, '報價中')
            """,
            (self.customer_id, self.project_id),
        ).lastrowid
        self.assertEqual(refresh_project_status(conn, self.project_id), "進行中")
        conn.execute("UPDATE quotes SET status='無下訂報價' WHERE id=?", (quote_id,))
        self.assertEqual(refresh_project_status(conn, self.project_id), "已完成")
        conn.commit()
        conn.close()

    def test_project_settlement_pays_each_active_order_once(self):
        first = self._create_order(price=100, status="印製中")
        second = self._create_order(price=200, status="完結", tax_mode="tax")
        voided = self._create_order(price=999, status="廢單")
        conn = database.connect()
        conn.execute(
            "INSERT INTO order_payments(order_id,amount,note) VALUES (?,?,?)",
            (first, 40, "訂金"),
        )
        conn.commit()
        conn.close()

        response = self.client.post(f"/projects/{self.project_id}/payment")
        self.assertEqual(response.status_code, 302)

        conn = database.connect()
        paid = {
            order_id: float(conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM order_payments WHERE order_id=?",
                (order_id,),
            ).fetchone()[0])
            for order_id in (first, second, voided)
        }
        notes = [row[0] for row in conn.execute(
            "SELECT note FROM order_payments WHERE note='專案整案結清' ORDER BY id"
        ).fetchall()]
        statuses = [row[0] for row in conn.execute(
            "SELECT status FROM orders WHERE id IN (?,?) ORDER BY id", (first, second)
        ).fetchall()]
        payment_count = conn.execute("SELECT COUNT(*) FROM order_payments").fetchone()[0]
        conn.close()

        self.assertEqual(paid[first], 100)
        self.assertEqual(paid[second], 210)
        self.assertEqual(paid[voided], 0)
        self.assertEqual(notes, ["專案整案結清", "專案整案結清"])
        self.assertEqual(statuses, ["印製中", "完結"])

        self.client.post(f"/projects/{self.project_id}/payment")
        conn = database.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM order_payments").fetchone()[0], payment_count)
        conn.close()

    def test_order_payment_rejects_overpayment(self):
        order_id = self._create_order(price=100, status="印製中")
        response = self.client.post(
            f"/orders/{order_id}/payment",
            data={"amount": "120"},
            follow_redirects=True,
        )
        self.assertIn("不可超過未收金額".encode("utf-8"), response.data)

        conn = database.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM order_payments").fetchone()[0], 0)
        conn.close()

        self.client.post(f"/orders/{order_id}/payment", data={"amount": "60"})
        self.client.post(f"/orders/{order_id}/payment", data={"amount": "50"})
        conn = database.connect()
        self.assertEqual(
            conn.execute("SELECT COALESCE(SUM(amount),0) FROM order_payments").fetchone()[0],
            60,
        )
        conn.close()

    def test_project_cancel_records_a_void_reason(self):
        order_id = self._create_order(price=100, status="印製中")
        self.client.post(
            f"/projects/{self.project_id}/progress",
            data={"action": "cancel"},
        )
        conn = database.connect()
        order = conn.execute(
            "SELECT status,void_reason FROM orders WHERE id=?", (order_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(order["status"], "廢單")
        self.assertEqual(order["void_reason"], "專案整案取消")
        self.assertEqual(self._project_status(), "已取消")


if __name__ == "__main__":
    unittest.main()
