from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader

from app import backup, database


class VariantCountTests(unittest.TestCase):
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
    def _sticker_item(variant_count=12):
        return {
            "product_name": "貼紙",
            "material": "一般珠光／霧膜",
            "size": "C25x25mm",
            "quantity": 3000,
            "variant_count": variant_count,
            "unit": "張",
            "unit_price": 0.27,
        }

    def _edit_form_data(self, document_type, mode, variant_count, project_id=None):
        item = self._sticker_item(variant_count)
        work_units = []
        items = [item]
        if mode == "project":
            items = []
            work_units = [{"name": "A單位", "items": [item]}]
        return {
            "document_type": document_type,
            "customer_name": "款數修改測試客戶",
            "mode": mode,
            "project_id": str(project_id or ""),
            "project_name": "款數修改測試專案" if mode == "project" else "",
            "tax_mode": "none",
            "items_json": json.dumps(items, ensure_ascii=False),
            "work_units_json": json.dumps(work_units, ensure_ascii=False),
        }

    def test_one_quote_item_calculates_multiple_variants_and_converts_to_order(self):
        conn = database.connect()
        quote_id = database.create_quote_from_payload(conn, {
            "customer_name": "多款測試客戶",
            "mode": "normal",
            "items": [self._sticker_item()],
        })
        quote_item = conn.execute(
            "SELECT * FROM quote_items WHERE quote_id=?", (quote_id,)
        ).fetchone()
        self.assertEqual(quote_item["variant_count"], 12)
        self.assertAlmostEqual(quote_item["subtotal"], 9720)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM quote_items WHERE quote_id=?", (quote_id,)).fetchone()[0],
            1,
        )

        order_id = database.convert_quote_to_order(conn, quote_id)
        order_item = conn.execute(
            "SELECT * FROM order_items WHERE order_id=?", (order_id,)
        ).fetchone()
        conn.commit()
        conn.close()

        self.assertEqual(order_item["variant_count"], 12)
        self.assertAlmostEqual(order_item["subtotal"], 9720)

        quote_page = self.client.get(f"/quotes/{quote_id}")
        order_page = self.client.get(f"/orders/{order_id}")
        for page in (quote_page, order_page):
            self.assertEqual(page.status_code, 200)
            self.assertIn("每款數量".encode(), page.data)
            self.assertIn("12 款".encode(), page.data)
            self.assertIn("9,720".encode(), page.data)

    def test_forms_show_variant_count_and_multiple_total_without_copy_rows(self):
        for path in ("/quotes/new", "/orders/new"):
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200)
                self.assertIn(b'class="js-variant-count"', page.data)
                self.assertIn("單款小計".encode(), page.data)
                self.assertIn("多款總計".encode(), page.data)

    def test_multiline_material_survives_edit_conversion_and_pdf(self):
        material = "封面／250p 霧膜\n內頁／80g 道林紙"
        for mode in ("normal", "project"):
            item = self._sticker_item(1)
            item["material"] = material
            payload = {"customer_name": "書籍客戶", "mode": mode, "project_name": "書籍專案" if mode == "project" else ""}
            if mode == "project":
                payload["work_units"] = [{"name": "教務處", "items": [item]}]
            else:
                payload["items"] = [item]
            conn = database.connect()
            quote_id = database.create_quote_from_payload(conn, payload)
            conn.commit()
            conn.close()
            self.client.post(f"/quotes/{quote_id}/edit", data={"payload_json": json.dumps(payload)})
            conn = database.connect()
            order_id = database.convert_quote_to_order(conn, quote_id)
            conn.commit()
            conn.close()
            self.client.post(f"/orders/{order_id}/edit", data={"payload_json": json.dumps(payload)})
            conn = database.connect()
            for kind, ident in (("quote", quote_id), ("order", order_id)):
                saved = conn.execute(f"SELECT material FROM {kind}_items WHERE {kind}_id=?", (ident,)).fetchone()[0]
                self.assertEqual(saved, material)
                page = self.client.get(f"/{kind}s/new" if kind == "quote" else f"/{kind}s/{ident}/edit")
                self.assertIn(b'<textarea class="js-material"', page.data)
                detail = self.client.get(f"/{kind}s/{ident}")
                self.assertIn(material.encode(), detail.data)
                pdf = self.client.get(f"/{kind}s/{ident}/pdf")
                text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf.data)).pages)
                self.assertIn("封面／250p霧膜", "".join(text.split()))
                self.assertIn("內頁／80g道林紙", "".join(text.split()))
            conn.close()

    def test_inclusive_tax_create_edit_convert_pdf_and_project(self):
        from app.main import calculate_totals
        self.assertEqual(calculate_totals([{"subtotal": 19600}], "inclusive"), (18667, 933, 19600))
        self.assertEqual(calculate_totals([{"subtotal": 19600}], "tax"), (19600, 980, 20580))
        self.assertEqual(calculate_totals([{"subtotal": 19600}], "none"), (19600, 0, 19600))
        for mode in ("normal", "project"):
            payload = {
                "customer_name": "學校測試", "mode": mode,
                "project_name": "精裝書專案" if mode == "project" else "",
                "tax_mode": "inclusive",
                "items": [{"product_name": "精裝書", "quantity": 56, "unit_price": 350, "unit": "本"}],
            }
            if mode == "project":
                payload["work_units"] = [{"name": "教務處", "items": payload.pop("items")}]
            response = self.client.post("/quotes/new", data={"payload_json": json.dumps(payload)})
            self.assertEqual(response.status_code, 302)
            quote_id = int(response.location.rstrip("/").split("/")[-1])
            response = self.client.post(f"/quotes/{quote_id}/edit", data={"payload_json": json.dumps(payload)})
            self.assertEqual(response.status_code, 302)
            conn = database.connect()
            order_id = database.convert_quote_to_order(conn, quote_id)
            conn.commit()
            conn.close()
            response = self.client.post(f"/orders/{order_id}/edit", data={"payload_json": json.dumps(payload)})
            self.assertEqual(response.status_code, 302)
            conn = database.connect()
            order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            self.assertEqual(order["tax_mode"], "inclusive")
            conn.close()
            for route, record_id in (("quotes", quote_id), ("orders", order_id)):
                page = self.client.get(f"/{route}/{record_id}")
                self.assertIn("19,600".encode(), page.data)
                self.assertIn("內含稅額".encode(), page.data)
                pdf = self.client.get(f"/{route}/{record_id}/pdf")
                self.assertEqual(pdf.status_code, 200)
                content = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf.data)).pages)
                for expected in ("含稅單價", "含稅金額", "19,600"):
                    self.assertIn(expected, content)
                self.assertNotIn("內含稅額", content)
                self.assertNotIn("未稅金額", content)
            if mode == "project":
                page = self.client.get(f"/projects/{order['project_id']}")
                self.assertIn("19,600".encode(), page.data)
                pdf = self.client.get(f"/projects/{order['project_id']}/pdf")
                self.assertEqual(pdf.status_code, 200)
                content = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf.data)).pages)
                self.assertIn("19,600", content)
                self.assertIn("18,667", content)

    def test_editing_variant_count_persists_for_quote_and_order_in_both_modes(self):
        for document_type, table, item_table, route_name in (
            ("quote", "quotes", "quote_items", "quotes"),
            ("order", "orders", "order_items", "orders"),
        ):
            for mode in ("normal", "project"):
                with self.subTest(document_type=document_type, mode=mode):
                    conn = database.connect()
                    create = (
                        database.create_quote_from_payload
                        if document_type == "quote"
                        else database.create_order_from_payload
                    )
                    record_id = create(conn, {
                        "customer_name": "款數修改測試客戶",
                        "mode": mode,
                        "project_name": "款數修改測試專案" if mode == "project" else "",
                        "items": [self._sticker_item(12)] if mode == "normal" else [],
                        "work_units": (
                            [{"name": "A單位", "items": [self._sticker_item(12)]}]
                            if mode == "project"
                            else []
                        ),
                    })
                    project_id = conn.execute(
                        f"SELECT project_id FROM {table} WHERE id=?", (record_id,)
                    ).fetchone()[0]
                    conn.commit()
                    conn.close()

                    response = self.client.post(
                        f"/{route_name}/{record_id}/edit",
                        data=self._edit_form_data(document_type, mode, 5, project_id),
                    )
                    self.assertEqual(response.status_code, 302)

                    conn = database.connect()
                    item = conn.execute(
                        f"SELECT variant_count, subtotal FROM {item_table} "
                        f"WHERE {document_type}_id=?",
                        (record_id,),
                    ).fetchone()
                    item_count = conn.execute(
                        f"SELECT COUNT(*) FROM {item_table} WHERE {document_type}_id=?",
                        (record_id,),
                    ).fetchone()[0]
                    conn.close()

                    self.assertEqual(item_count, 1)
                    self.assertEqual(item["variant_count"], 5)
                    self.assertAlmostEqual(item["subtotal"], 4050)

    def test_quote_pdf_marks_variant_count_and_uses_multiple_total(self):
        conn = database.connect()
        quote_id = database.create_quote_from_payload(conn, {
            "customer_name": "多款 PDF 客戶",
            "mode": "normal",
            "items": [self._sticker_item()],
        })
        conn.commit()
        conn.close()

        response = self.client.get(f"/quotes/{quote_id}/pdf")
        self.assertEqual(response.status_code, 200)
        text = "\n".join((page.extract_text() or "") for page in PdfReader(BytesIO(response.data)).pages)
        self.assertIn("共 12 款", text)
        self.assertIn("9,720", text)

    def test_variant_count_defaults_to_one_and_rejects_non_integer(self):
        conn = database.connect()
        order_id = database.create_order_from_payload(conn, {
            "customer_name": "單款測試客戶",
            "mode": "normal",
            "items": [{
                "product_name": "名片",
                "quantity": 2,
                "unit": "盒",
                "unit_price": 500,
            }],
        })
        item = conn.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchone()
        self.assertEqual(item["variant_count"], 1)
        self.assertEqual(item["subtotal"], 1000)
        conn.commit()

        try:
            with self.assertRaisesRegex(ValueError, "款數必須"):
                database.create_order_from_payload(conn, {
                    "customer_name": "錯誤款數測試客戶",
                    "mode": "normal",
                    "items": [{
                        "product_name": "貼紙",
                        "quantity": 100,
                        "variant_count": 1.5,
                        "unit_price": 1,
                    }],
                })
            conn.rollback()
            with self.assertRaisesRegex(ValueError, "款數必須"):
                database.create_order_from_payload(conn, {
                    "customer_name": "零款數測試客戶",
                    "mode": "normal",
                    "items": [{
                        "product_name": "貼紙",
                        "quantity": 100,
                        "variant_count": 0,
                        "unit_price": 1,
                    }],
                })
        finally:
            conn.rollback()
            conn.close()

    def test_schema_three_database_adds_variant_columns_in_place(self):
        conn = database.connect()
        quote_id = database.create_quote_from_payload(conn, {
            "customer_name": "升級測試客戶",
            "mode": "normal",
            "items": [{
                "product_name": "舊報價品項",
                "quantity": 10,
                "unit_price": 5,
            }],
        })
        conn.commit()
        order_id = database.create_order_from_payload(conn, {
            "customer_name": "升級測試客戶",
            "mode": "normal",
            "items": [{
                "product_name": "舊訂單品項",
                "quantity": 20,
                "unit_price": 5,
            }],
        })
        conn.commit()
        conn.execute("ALTER TABLE quote_items DROP COLUMN variant_count")
        conn.execute("ALTER TABLE order_items DROP COLUMN variant_count")
        conn.execute("DELETE FROM schema_migrations WHERE version>=4")
        conn.commit()
        conn.close()

        database.init_database()
        migration_backups = list(database.BACKUP_DIR.glob("printshop_migration_*.db"))
        self.assertEqual(len(migration_backups), 1)

        conn = database.connect()
        quote_item = conn.execute(
            "SELECT * FROM quote_items WHERE quote_id=?", (quote_id,)
        ).fetchone()
        order_item = conn.execute(
            "SELECT * FROM order_items WHERE order_id=?", (order_id,)
        ).fetchone()
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        conn.close()

        self.assertEqual(quote_item["variant_count"], 1)
        self.assertEqual(order_item["variant_count"], 1)
        self.assertEqual(quote_item["subtotal"], 50)
        self.assertEqual(order_item["subtotal"], 100)
        self.assertEqual(version, database.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
