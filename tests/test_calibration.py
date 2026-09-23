from copy import deepcopy
from decimal import Decimal
from io import BytesIO
import json
import sqlite3
import unittest
from pypdf import PdfReader
from tests import test_variant_count as support
from app import database
from app.pricing import calibrate, whole


class CalibrationTests(unittest.TestCase):
    setUp = support.VariantCountTests.setUp
    tearDown = support.VariantCountTests.tearDown

    def payload(self, mode="normal"):
        items = [
            {"product_name": "精裝書", "quantity": 20, "variant_count": 1, "unit": "本", "unit_price": 350},
            {"product_name": "膠裝書", "quantity": 10, "variant_count": 1, "unit": "本", "unit_price": 91},
        ]
        return {"customer_name": "校準學校", "tax_mode": "calibrated", "mode": mode,
                "project_name": "校準專案" if mode == "project" else "",
                "items": items if mode == "normal" else [],
                "work_units": [{"name": "教務處", "items": items}] if mode == "project" else [],
                "target_total": 7910, "adjustment_index": 1}

    def confirmed(self, payload):
        response = self.client.post("/api/pricing/calibrate", json=payload)
        self.assertEqual(response.status_code, 200, response.data)
        result = response.get_json()
        payload["calibration_signature"] = result["signature"]
        return result

    def test_calculation_price_amount_and_rejection(self):
        data = self.payload()
        result = calibrate(data)
        self.assertEqual((result["subtotal"], result["tax_amount"], result["total"]), (7533, 377, 7910))
        self.assertEqual([r["subtotal"] for r in result["items"]], [6667, 866])
        self.assertEqual(result["items"][1]["unit_price"], "86.6")
        self.assertEqual(result["adjustment"], -1)
        for row in result["items"]:
            self.assertEqual(whole(Decimal(row["unit_price"]) * Decimal(row["quantity"]) * row["variant_count"]), row["subtotal"])
        for total in (10, 10.5, -1, "NaN"):
            with self.subTest(total=total), self.assertRaises(ValueError):
                calibrate({**data, "target_total": total})
        bad = deepcopy(data)
        bad["items"][0]["quantity"] = 0
        with self.assertRaises(ValueError):
            calibrate(bad)
        data["items"][0]["variant_count"] = 2
        data["target_total"] = ""
        self.assertEqual(calibrate(data)["total"], 14910)

    def test_normal_and_project_create_edit_convert_and_pdf(self):
        for mode in ("normal", "project"):
            data = self.payload(mode)
            result = self.confirmed(data)
            response = self.client.post("/quotes/new", data={"payload_json": json.dumps(data)})
            quote_id = int(response.location.rsplit("/", 1)[-1])
            conn = database.connect()
            try:
                draft = database.quote_to_payload(conn, quote_id)
                self.assertEqual((draft["items"] if mode == "normal" else draft["work_units"][0]["items"])[1]["unit_price"], 91)
                order_id = database.convert_quote_to_order(conn, quote_id)
                conn.commit()
                self.assertEqual(conn.execute("SELECT calibration_json FROM orders WHERE id=?", (order_id,)).fetchone()[0],
                                 conn.execute("SELECT calibration_json FROM quotes WHERE id=?", (quote_id,)).fetchone()[0])
            finally:
                conn.close()
            # Reopen and resave original gross inputs: never divide a second time.
            self.confirmed(draft)
            draft["tax_mode"] = "calibrated"
            response = self.client.post(f"/orders/{order_id}/edit", data={"payload_json": json.dumps(draft)})
            self.assertEqual(response.location, f"/orders/{order_id}")
            for table, ident in (("quote", quote_id), ("order", order_id)):
                conn = database.connect()
                rows = conn.execute(f"SELECT subtotal, unit_price FROM {table}_items WHERE {table}_id=? ORDER BY sort_order", (ident,)).fetchall()
                conn.close()
                self.assertEqual([r["subtotal"] for r in rows], [6667, 866])
                page = self.client.get(f"/{table}s/{ident}")
                self.assertIn(b"7,910", page.data)
                pdf = self.client.get(f"/{table}s/{ident}/pdf")
                self.assertEqual(pdf.status_code, 200)
                text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf.data)).pages)
                for expected in ("333.33", "86.6", "7,533", "377", "7,910"):
                    self.assertIn(expected, text)
                self.assertNotIn("尾差", text)
            conn = database.connect()
            project_id = conn.execute("SELECT project_id FROM orders WHERE id=?", (order_id,)).fetchone()[0]
            conn.close()
            if project_id:
                page = self.client.get(f"/projects/{project_id}")
                self.assertIn(b"7,910", page.data)
                self.client.post(f"/projects/{project_id}/payment")
                conn = database.connect()
                paid = conn.execute("SELECT SUM(amount) FROM order_payments WHERE order_id=?", (order_id,)).fetchone()[0]
                conn.close()
                self.assertEqual(paid, 7910)

    def test_stale_confirmation_rolls_back_and_form_post_saves(self):
        data = self.payload()
        self.confirmed(data)
        form = {"customer_name": data["customer_name"], "mode": "normal", "tax_mode": "calibrated",
                "target_total": "7910", "adjustment_index": "1", "calibration_signature": data["calibration_signature"],
                "items_json": json.dumps(data["items"]), "work_units_json": "[]"}
        response = self.client.post("/orders/new", data=form)
        ident = int(response.location.rsplit("/", 1)[-1])
        data["items"][1]["unit_price"] = 92
        form["items_json"] = json.dumps(data["items"])
        self.client.post(f"/orders/{ident}/edit", data=form)
        conn = database.connect()
        saved = conn.execute("SELECT SUM(subtotal) FROM order_items WHERE order_id=?", (ident,)).fetchone()[0]
        conn.close()
        self.assertEqual(saved, 7533)

    def test_minimum_precision_for_every_item(self):
        for qty, gross_price, expected in ((800, 187.5, "178.571"), (2000, 75, "71.4286"), (20, 350, "333.33")):
            with self.subTest(qty=qty):
                payload = {"items": [{"product_name": "書", "quantity": qty, "unit_price": gross_price}]}
                row = calibrate(payload)["items"][0]
                self.assertEqual(row["unit_price"], expected)
                self.assertEqual(whole(Decimal(expected) * qty), row["subtotal"])
        # A non-adjustment row must also use minimum precision.
        self.assertEqual(calibrate(self.payload())["items"][0]["unit_price"], "333.33")
        payload = {"items": [{"product_name": "書", "quantity": 100, "variant_count": 20, "unit_price": 75}]}
        self.assertEqual(calibrate(payload)["items"][0]["unit_price"], "71.4286")

    def test_schema_four_upgrade_preserves_values_and_backs_up(self):
        conn = database.connect()
        ident = database.create_order_from_payload(conn, {"customer_name":"舊客戶", "items":[{"product_name":"舊品項", "quantity":2, "unit_price":350}]})
        conn.execute("ALTER TABLE quotes DROP COLUMN calibration_json")
        conn.execute("ALTER TABLE orders DROP COLUMN calibration_json")
        conn.execute("DELETE FROM schema_migrations WHERE version=5")
        conn.commit()
        conn.close()
        database.init_database()
        conn = database.connect()
        self.assertEqual(conn.execute("SELECT subtotal FROM order_items WHERE order_id=?", (ident,)).fetchone()[0], 700)
        self.assertEqual(conn.execute("SELECT calibration_json FROM orders WHERE id=?", (ident,)).fetchone()[0], "")
        conn.close()
        backups = list(database.BACKUP_DIR.glob("printshop_migration_*.db"))
        self.assertEqual(len(backups), 1)
        saved = sqlite3.connect(backups[0])
        self.assertEqual(saved.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 4)
        saved.close()
