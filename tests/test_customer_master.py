from pathlib import Path
import tempfile
import unittest

from app import backup, database


class CustomerMasterTests(unittest.TestCase):
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
    def _item():
        return {
            "product_name": "名片",
            "material": "象牙卡",
            "size": "90x54",
            "quantity": 1,
            "unit": "盒",
            "unit_price": 100,
        }

    def test_person_customer_clears_duplicate_contact_and_has_edit_button(self):
        response = self.client.post(
            "/customers/new",
            data={
                "customer_type": "person",
                "name": "Chloe Wu",
                "contact_person": " Chloe  Wu ",
                "category": "一般",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        conn = database.connect()
        customer = conn.execute("SELECT * FROM customers").fetchone()
        conn.close()
        self.assertEqual(customer["customer_type"], "person")
        self.assertEqual(customer["contact_person"], "")

        listing = self.client.get("/customers")
        self.assertIn("個人".encode("utf-8"), listing.data)
        self.assertIn("編輯".encode("utf-8"), listing.data)
        self.assertNotIn("<td>Chloe Wu</td>".encode("utf-8"), listing.data)

    def test_rename_keeps_old_alias_for_customer_and_dashboard_search(self):
        conn = database.connect()
        customer_id = conn.execute(
            "INSERT INTO customers(name,customer_type) VALUES (?,?)",
            ("廣達舊名稱", "organization"),
        ).lastrowid
        conn.commit()
        order_id = database.create_order_from_payload(
            conn,
            {
                "customer_id": customer_id,
                "customer_name": "廣達舊名稱",
                "created_date": "2026-08-18",
                "mode": "normal",
                "items": [self._item()],
            },
        )
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/customers/{customer_id}/edit",
            data={
                "customer_type": "organization",
                "name": "廣達新名稱",
                "contact_person": "洪先生",
                "category": "公司",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        conn = database.connect()
        alias = conn.execute(
            "SELECT alias FROM customer_aliases WHERE customer_id=?",
            (customer_id,),
        ).fetchone()
        linked_name = conn.execute(
            "SELECT c.name FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=?",
            (order_id,),
        ).fetchone()["name"]
        conn.close()
        self.assertEqual(alias["alias"], "廣達舊名稱")
        self.assertEqual(linked_name, "廣達新名稱")

        customer_results = self.client.get(
            "/api/customers/search", query_string={"q": "舊名稱"}
        ).get_json()
        self.assertEqual(customer_results[0]["name"], "廣達新名稱")

        order_results = self.client.get(
            "/api/dashboard/order-search", query_string={"q": "舊名稱"}
        ).get_json()
        self.assertEqual(order_results[0]["order_id"], order_id)
        self.assertEqual(order_results[0]["customer_name"], "廣達新名稱")

        editor_results = self.client.get(
            "/api/orders/smart-search", query_string={"customer": "廣達舊名稱"}
        ).get_json()
        self.assertEqual(editor_results[0]["order_id"], order_id)

    def test_transaction_edit_does_not_change_selected_customer_master(self):
        conn = database.connect()
        customer_id = conn.execute(
            """
            INSERT INTO customers(name,customer_type,contact_person,phone)
            VALUES ('固定單位','organization','王小姐','05-1111-222')
            """
        ).lastrowid
        conn.commit()
        database.create_order_from_payload(
            conn,
            {
                "customer_id": customer_id,
                "customer_name": "不應改名",
                "customer_contact": "不應改聯絡人",
                "customer_phone": "0999",
                "created_date": "2026-08-18",
                "mode": "normal",
                "items": [self._item()],
            },
        )
        conn.commit()
        customer = conn.execute(
            "SELECT name,contact_person,phone FROM customers WHERE id=?",
            (customer_id,),
        ).fetchone()
        conn.close()

        self.assertEqual(customer["name"], "固定單位")
        self.assertEqual(customer["contact_person"], "王小姐")
        self.assertEqual(customer["phone"], "05-1111-222")

    def test_schema_two_migration_cleans_existing_duplicate(self):
        conn = database.connect()
        customer_id = conn.execute(
            "INSERT INTO customers(name,customer_type,contact_person) VALUES ('林小美','organization',' 林 小美 ')",
        ).lastrowid
        conn.execute("DELETE FROM schema_migrations WHERE version=2")
        conn.commit()
        conn.close()

        database.init_database()
        conn = database.connect()
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        conn.close()
        self.assertEqual(customer["customer_type"], "person")
        self.assertEqual(customer["contact_person"], "")
        self.assertEqual(version, 2)


if __name__ == "__main__":
    unittest.main()
