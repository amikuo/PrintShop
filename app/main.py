
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from io import BytesIO
from typing import Any

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for

from .backup import (
    DAILY_RETENTION,
    MONTHLY_RETENTION,
    create_backup,
    ensure_automatic_backups,
    list_backups,
    resolve_backup,
    restore_backup,
)

from .database import (
    CUSTOMER_TYPES,
    DATA_ROOT,
    ORDER_STATUSES,
    PROJECT_STATUSES,
    QUOTE_STATUSES,
    connect,
    count_tables,
    create_order_from_payload,
    create_quote_from_payload,
    current_local_date_input,
    update_quote_from_payload,
    convert_quote_to_order,
    clean_customer_contact,
    display_customer_contact,
    display_date,
    init_database,
    list_finishing_options,
    order_to_payload,
    update_order_from_payload,
    quote_to_payload,
    refresh_expired_quotes,
    row_dict,
    rows_dict,
    search_customers,
    search_order_items,
    search_projects,
    search_spec_presets,
    today_key,
    update_customer_master,
)
from .pdf_export import build_document_pdf

app = Flask(__name__)
app.secret_key = "printshop-v2.2-secret"
APP_VERSION = "3.5.0"


@app.before_request
def run_daily_backup_if_needed():
    try:
        ensure_automatic_backups()
    except Exception:
        app.logger.exception("Automatic database backup failed")


def db():
    return connect()


def parse_payload() -> dict[str, Any]:
    """
    Supports both:
    1) legacy payload_json forms
    2) V2.4 rebuilt quote form using items_json/work_units_json
    """
    raw = request.form.get("payload_json", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # V2.4 rebuilt form
    customer_name = request.form.get("customer_name", "").strip()
    customer_id = request.form.get("customer_id", "").strip()
    mode = request.form.get("mode", "normal").strip() or "normal"

    try:
        items = json.loads(request.form.get("items_json", "[]") or "[]")
        work_units = json.loads(request.form.get("work_units_json", "[]") or "[]")
    except json.JSONDecodeError:
        return {}

    if not customer_name and not customer_id:
        return {}

    document_type = request.form.get("document_type", "quote").strip() or "quote"
    default_status = "設計中" if document_type == "order" else "報價中"

    payload = {
        "customer_id": customer_id or None,
        "customer_name": customer_name,
        "customer_contact": request.form.get("customer_contact", "").strip(),
        "customer_tax_id": request.form.get("customer_tax_id", "").strip(),
        "customer_phone": request.form.get("customer_phone", "").strip(),
        "mode": mode,
        "project_id": request.form.get("project_id", "").strip() or None,
        "project_name": request.form.get("project_name", "").strip(),
        "note": request.form.get("note", "").strip(),
        "delivery_date": request.form.get("delivery_date", "").strip(),
        "created_date": request.form.get("created_date", "").strip(),
        "tax_mode": request.form.get("tax_mode", "none").strip() or "none",
        "tax_rate": 5,
        "status": request.form.get("status", default_status).strip() or default_status,
        "items": items if mode != "project" else [],
        "work_units": work_units if mode == "project" else [],
    }
    return payload


def calculate_totals(items, tax_mode="none", tax_rate=5):
    subtotal = sum(float(r["subtotal"] or 0) for r in items)
    tax = int(subtotal * 0.05 + 0.5) if tax_mode == "tax" else 0
    return subtotal, tax, subtotal + tax


def pdf_download_name(document_label: str, document_number: str, customer_name: str) -> str:
    safe_customer = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", customer_name or "").strip()
    safe_customer = re.sub(r"\s+", "_", safe_customer)[:40]
    parts = [document_label, document_number]
    if safe_customer:
        parts.append(safe_customer)
    return "_".join(parts) + ".pdf"


def get_stats():
    conn = db()
    refresh_expired_quotes(conn)
    conn.commit()
    stats = {
        "unfulfilled": conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE status IN ('設計中','印製中','待取貨')"
        ).fetchone()["c"],
        "designing": conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE status = '設計中'"
        ).fetchone()["c"],
        "printing": conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE status = '印製中'"
        ).fetchone()["c"],
        "pickup": conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE status = '待取貨'"
        ).fetchone()["c"],
    }
    conn.close()
    return stats


@app.template_filter("money")
def money(value):
    try:
        return f"{float(value):,.0f}"
    except Exception:
        return "0"


@app.template_filter("display_date")
def display_date_filter(value):
    return display_date(value)


@app.template_filter("display_contact")
def display_contact_filter(contact_person, customer_name):
    return display_customer_contact(customer_name, contact_person)


@app.context_processor
def inject_globals():
    return {
        "customer_categories": ["一般", "學校", "政府", "公司", "宗親會"],
        "customer_types": CUSTOMER_TYPES,
        "quote_statuses": QUOTE_STATUSES,
        "order_statuses": ORDER_STATUSES,
        "project_statuses": PROJECT_STATUSES,
    }


@app.route("/")
def index():
    conn = db()
    refresh_expired_quotes(conn)
    conn.commit()
    stats = get_stats()

    recent_entries = conn.execute(
        """
        SELECT *
        FROM (
            SELECT
                'quote' AS record_type,
                q.id AS record_id,
                q.quote_number AS document_number,
                c.name AS customer_name,
                p.project_name AS project_name,
                q.status AS status,
                q.created_at AS created_at,
                q.delivery_date AS delivery_date,
                1 AS type_priority,
                9 AS delivery_priority
            FROM quotes q
            JOIN customers c ON c.id = q.customer_id
            LEFT JOIN projects p ON p.id = q.project_id
            WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.quote_id = q.id)

            UNION ALL

            SELECT
                'order' AS record_type,
                o.id AS record_id,
                o.order_number AS document_number,
                c.name AS customer_name,
                p.project_name AS project_name,
                o.status AS status,
                o.created_at AS created_at,
                o.delivery_date AS delivery_date,
                CASE WHEN o.status IN ('設計中','印製中','待取貨') THEN 0 ELSE 2 END AS type_priority,
                CASE
                    WHEN o.status NOT IN ('設計中','印製中','待取貨') THEN 9
                    WHEN o.delivery_date IS NULL OR o.delivery_date = '' THEN 8
                    WHEN date(o.delivery_date) < date('now','localtime') THEN 0
                    ELSE 1
                END AS delivery_priority
            FROM orders o
            JOIN customers c ON c.id = o.customer_id
            LEFT JOIN projects p ON p.id = o.project_id
        )
        ORDER BY
            type_priority ASC,
            delivery_priority ASC,
            CASE WHEN type_priority = 0 AND delivery_date IS NOT NULL AND delivery_date <> '' THEN date(delivery_date) END ASC,
            created_at DESC,
            record_id DESC
        LIMIT 12
        """
    ).fetchall()

    conn.close()
    return render_template(
        "index.html",
        stats=stats,
        recent_entries=recent_entries,
    )


@app.get("/api/dashboard/order-search")
def api_dashboard_order_search():
    """
    Homepage smart order search.
    Returns one result per order, even when several items match.
    Search spans order number, customer, project, work unit, product and specs.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    tokens = []
    seen_tokens = set()
    for value in re.split(r"[\s+＋]+", q.replace("　", " ")):
        value = value.strip()
        normalized = value.casefold()
        if value and normalized not in seen_tokens:
            tokens.append(value)
            seen_tokens.add(normalized)
    if not tokens:
        return jsonify([])

    conn = db()
    token_clauses = []
    params: list[Any] = []
    for token in tokens:
        like = f"%{token}%"
        token_clauses.append("""(
            o.order_number LIKE ? OR
            c.name LIKE ? OR
            EXISTS (SELECT 1 FROM customer_aliases ca WHERE ca.customer_id=c.id AND ca.alias LIKE ?) OR
            COALESCE(p.project_name,'') LIKE ? OR
            COALESCE(ow.name,'') LIKE ? OR
            oi.product_name LIKE ? OR
            COALESCE(oi.material,'') LIKE ? OR
            COALESCE(oi.size,'') LIKE ? OR
            COALESCE(oi.finishing,'') LIKE ? OR
            CAST(COALESCE(oi.quantity,'') AS TEXT) LIKE ?
        )""")
        params.extend([like] * 10)

    rows = conn.execute(
        f"""
        SELECT
            o.id AS order_id,
            o.order_number,
            o.status,
            o.created_at,
            o.delivery_date,
            o.note AS order_note,
            c.name AS customer_name,
            COALESCE((SELECT GROUP_CONCAT(ca.alias, ' ') FROM customer_aliases ca WHERE ca.customer_id=c.id),'') AS customer_aliases,
            COALESCE(p.project_name,'') AS project_name,
            oi.id AS item_id,
            COALESCE(oi.product_name,'') AS product_name,
            COALESCE(oi.material,'') AS material,
            COALESCE(oi.size,'') AS size,
            COALESCE(oi.finishing,'') AS finishing,
            oi.quantity,
            COALESCE(oi.unit,'') AS unit,
            oi.unit_price,
            oi.subtotal,
            COALESCE(oi.note,'') AS item_note,
            COALESCE(ow.name,'') AS work_unit_name,
            (SELECT COUNT(*) FROM order_items all_items WHERE all_items.order_id=o.id) AS total_item_count
        FROM orders o
        JOIN customers c ON c.id=o.customer_id
        LEFT JOIN projects p ON p.id=o.project_id
        LEFT JOIN order_items oi ON oi.order_id=o.id
        LEFT JOIN order_work_units ow ON ow.id=oi.work_unit_id
        WHERE o.status != '廢單'
          AND ({' OR '.join(token_clauses)})
        ORDER BY o.created_at DESC, o.id DESC, oi.sort_order, oi.id
        """,
        params,
    ).fetchall()

    def value_score(value: Any, token: str, exact: int, partial: int) -> int:
        text = str(value or "").strip().casefold()
        needle = token.casefold()
        if not text:
            return 0
        if text == needle:
            return exact
        return partial if needle in text else 0

    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        order_id = int(row["order_id"])
        if order_id not in grouped:
            grouped[order_id] = {
                "order_id": order_id,
                "order_number": row["order_number"],
                "status": row["status"],
                "created_at": row["created_at"],
                "created_at_display": display_date(row["created_at"]),
                "delivery_date": row["delivery_date"],
                "order_note": row["order_note"] or "",
                "customer_name": row["customer_name"],
                "project_name": row["project_name"],
                "total_item_count": int(row["total_item_count"] or 0),
                "token_scores": [0] * len(tokens),
                "item_map": {},
            }
        order = grouped[order_id]

        quantity = row["quantity"]
        if quantity is not None and float(quantity).is_integer():
            quantity_text = str(int(quantity))
        else:
            quantity_text = str(quantity or "")

        for index, token in enumerate(tokens):
            scores = [
                value_score(row["order_number"], token, 120, 90),
                value_score(row["customer_name"], token, 110, 80),
                value_score(row["customer_aliases"], token, 105, 75),
                value_score(row["project_name"], token, 90, 65),
                value_score(row["work_unit_name"], token, 85, 60),
                value_score(row["product_name"], token, 100, 75),
                value_score(row["material"], token, 80, 55),
                value_score(row["size"], token, 80, 55),
                value_score(row["finishing"], token, 80, 55),
                value_score(quantity_text, token, 70, 35),
            ]
            order["token_scores"][index] = max(order["token_scores"][index], max(scores))

        if row["item_id"] is not None:
            order["item_map"][int(row["item_id"])] = {
                "item_id": int(row["item_id"]),
                "product_name": row["product_name"],
                "material": row["material"],
                "size": row["size"],
                "finishing": row["finishing"],
                "quantity": row["quantity"],
                "unit": row["unit"],
                "unit_price": row["unit_price"],
                "subtotal": row["subtotal"],
                "note": row["item_note"],
                "work_unit_name": row["work_unit_name"],
            }

    conn.close()
    result = []
    for order in grouped.values():
        scores = order.pop("token_scores")
        items = list(order.pop("item_map").values())
        order["matched_tokens"] = [tokens[i] for i, score in enumerate(scores) if score > 0]
        order["matched_token_count"] = len(order["matched_tokens"])
        order["query_token_count"] = len(tokens)
        order["relevance_score"] = sum(scores)
        order["matched_item_count"] = len(items)
        order["items"] = items[:4]
        order["hidden_preview_count"] = max(len(items) - len(order["items"]), 0)
        result.append(order)

    result.sort(
        key=lambda order: (
            order["matched_token_count"],
            order["relevance_score"],
            order["created_at"] or "",
            order["order_id"],
        ),
        reverse=True,
    )
    return jsonify(result[:30])


# ---------------- Customers ----------------

@app.route("/customers")
def customers_list():
    q = request.args.get("q", "").strip()
    conn = db()
    if q:
        rows = conn.execute(
            """
            SELECT *
            FROM customers c
            WHERE is_active = 1
              AND (
                name LIKE ? OR contact_person LIKE ? OR tax_id LIKE ? OR phone LIKE ? OR email LIKE ?
                OR EXISTS (SELECT 1 FROM customer_aliases ca WHERE ca.customer_id=c.id AND ca.alias LIKE ?)
              )
            ORDER BY name
            """,
            (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM customers WHERE is_active = 1 ORDER BY id DESC").fetchall()
    customers = rows_dict(rows)
    for customer in customers:
        customer["contact_person"] = display_customer_contact(customer["name"], customer["contact_person"])
    conn.close()
    return render_template("customers/list.html", customers=customers, q=q)


@app.route("/customers/new", methods=["GET", "POST"])
def customers_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("客戶名稱不能空白。")
            return redirect(url_for("customers_new"))
        customer_type = request.form.get("customer_type", "organization").strip()
        if customer_type not in CUSTOMER_TYPES:
            customer_type = "organization"
        contact = clean_customer_contact(
            customer_type,
            name,
            request.form.get("contact_person", ""),
        )
        conn = db()
        cur = conn.execute(
            """
            INSERT INTO customers (name, customer_type, contact_person, tax_id, phone, email, category, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                customer_type,
                contact,
                request.form.get("tax_id", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("category", "一般"),
                request.form.get("note", "").strip(),
            ),
        )
        conn.commit()
        cid = int(cur.lastrowid)
        conn.close()
        flash("客戶已建立。")
        return redirect(url_for("customers_edit", customer_id=cid))
    return render_template("customers/form.html", customer=None)


@app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def customers_edit(customer_id):
    conn = db()
    customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if not customer:
        conn.close()
        abort(404)
    if request.method == "POST":
        try:
            update_customer_master(conn, customer_id, request.form.to_dict())
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            conn.close()
            flash(str(exc))
            return redirect(url_for("customers_edit", customer_id=customer_id))
        conn.close()
        flash("客戶資料已更新。")
        return redirect(url_for("customers_list"))
    conn.close()
    return render_template("customers/form.html", customer=customer)


@app.route("/api/customers/search")
def api_customers_search():
    q = request.args.get("q", "").strip()
    conn = db()
    rows = search_customers(conn, q, limit=10) if q else []
    conn.close()
    return jsonify(rows)


# ---------------- Specifications V2.3 ----------------

SPEC_CHILD_TABLES = {
    "materials": ("spec_product_materials", "name"),
    "sizes": ("spec_product_sizes", "name"),
    "quantities": ("spec_product_quantities", "value_text"),
}


def _spec_history_used(kind, name):
    conn = db()
    try:
        if kind == "products":
            q = conn.execute("SELECT 1 FROM quote_items WHERE product_name=? LIMIT 1", (name,)).fetchone()
            o = conn.execute("SELECT 1 FROM order_items WHERE product_name=? LIMIT 1", (name,)).fetchone()
        elif kind == "materials":
            q = conn.execute("SELECT 1 FROM quote_items WHERE material=? LIMIT 1", (name,)).fetchone()
            o = conn.execute("SELECT 1 FROM order_items WHERE material=? LIMIT 1", (name,)).fetchone()
        elif kind == "sizes":
            q = conn.execute("SELECT 1 FROM quote_items WHERE size=? LIMIT 1", (name,)).fetchone()
            o = conn.execute("SELECT 1 FROM order_items WHERE size=? LIMIT 1", (name,)).fetchone()
        elif kind == "quantities":
            q = conn.execute("SELECT 1 FROM quote_items WHERE CAST(quantity AS TEXT)=? LIMIT 1", (name,)).fetchone()
            o = conn.execute("SELECT 1 FROM order_items WHERE CAST(quantity AS TEXT)=? LIMIT 1", (name,)).fetchone()
        elif kind == "finishings":
            like = f"%{name}%"
            q = conn.execute("SELECT 1 FROM quote_items WHERE finishing LIKE ? LIMIT 1", (like,)).fetchone()
            o = conn.execute("SELECT 1 FROM order_items WHERE finishing LIKE ? LIMIT 1", (like,)).fetchone()
        else:
            return False
        return bool(q or o)
    finally:
        conn.close()


@app.route("/specs")
def specs_list():
    conn = db()
    products = conn.execute("""
        SELECT p.*, COALESCE(u.unit_name,'') AS unit_name
        FROM spec_products p
        LEFT JOIN spec_product_unit u ON u.product_id=p.id AND u.is_active=1
        ORDER BY p.is_active DESC,p.id DESC
    """).fetchall()

    materials_by_product, sizes_by_product, quantities_by_product = {}, {}, {}
    for p in products:
        pid = int(p["id"])
        materials_by_product[pid] = conn.execute(
            "SELECT * FROM spec_product_materials WHERE product_id=? ORDER BY is_active DESC,id", (pid,)
        ).fetchall()
        sizes_by_product[pid] = conn.execute(
            "SELECT * FROM spec_product_sizes WHERE product_id=? ORDER BY is_active DESC,id", (pid,)
        ).fetchall()
        quantities_by_product[pid] = conn.execute(
            "SELECT * FROM spec_product_quantities WHERE product_id=? ORDER BY is_active DESC,sort_order,id", (pid,)
        ).fetchall()

    finishings = conn.execute("SELECT * FROM spec_finishings ORDER BY is_active DESC,id").fetchall()
    quick_templates = conn.execute("""
        SELECT t.*,p.name AS product_name,
               COALESCE((
                 SELECT GROUP_CONCAT(finishing_name,'、')
                 FROM spec_quick_template_finishings f
                 WHERE f.template_id=t.id
               ),'') AS finishing_names
        FROM spec_quick_templates t
        JOIN spec_products p ON p.id=t.product_id
        ORDER BY t.is_active DESC,t.id DESC
    """).fetchall()
    conn.close()
    return render_template(
        "products/list.html",
        products=products,
        materials_by_product=materials_by_product,
        sizes_by_product=sizes_by_product,
        quantities_by_product=quantities_by_product,
        finishings=finishings,
        quick_templates=quick_templates,
    )


@app.post("/specs/products")
def specs_product_add():
    name = request.form.get("name","").strip()
    if not name:
        flash("品項名稱不能空白。")
        return redirect(url_for("specs_list"))
    conn = db()
    row = conn.execute("SELECT id FROM spec_products WHERE name=?", (name,)).fetchone()
    if row:
        conn.execute("UPDATE spec_products SET is_active=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
    else:
        conn.execute("INSERT INTO spec_products(name,is_active) VALUES(?,1)", (name,))
    conn.commit(); conn.close()
    return redirect(url_for("specs_list"))


@app.post("/specs/products/<int:product_id>/child/<kind>")
def specs_child_add(product_id, kind):
    info = SPEC_CHILD_TABLES.get(kind)
    if not info:
        abort(404)
    table, field = info
    value = request.form.get("value","").strip()
    if not value:
        flash("內容不能空白。")
        return redirect(url_for("specs_list"))
    conn = db()
    row = conn.execute(f"SELECT id FROM {table} WHERE product_id=? AND {field}=?", (product_id,value)).fetchone()
    if row:
        conn.execute(f"UPDATE {table} SET is_active=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
    elif kind == "quantities":
        n = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM spec_product_quantities WHERE product_id=?", (product_id,)).fetchone()["n"]
        conn.execute("INSERT INTO spec_product_quantities(product_id,value_text,sort_order,is_active) VALUES(?,?,?,1)", (product_id,value,n))
    else:
        conn.execute(f"INSERT INTO {table}(product_id,{field},is_active) VALUES(?,?,1)", (product_id,value))
    conn.commit(); conn.close()
    return redirect(url_for("specs_list"))


@app.post("/specs/products/<int:product_id>/unit")
def specs_unit_save(product_id):
    unit = request.form.get("unit_name","").strip()
    conn = db()
    conn.execute("""
        INSERT INTO spec_product_unit(product_id,unit_name,is_active)
        VALUES(?,?,1)
        ON CONFLICT(product_id) DO UPDATE SET
            unit_name=excluded.unit_name,is_active=1,updated_at=CURRENT_TIMESTAMP
    """, (product_id,unit))
    conn.commit(); conn.close()
    return redirect(url_for("specs_list"))


@app.post("/specs/finishings")
def specs_finishing_add():
    name = request.form.get("name","").strip()
    if not name:
        flash("後加工名稱不能空白。")
        return redirect(url_for("specs_list"))
    conn = db()
    row = conn.execute("SELECT id FROM spec_finishings WHERE name=?", (name,)).fetchone()
    if row:
        conn.execute("UPDATE spec_finishings SET is_active=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
    else:
        conn.execute("INSERT INTO spec_finishings(name,is_active) VALUES(?,1)", (name,))
    conn.commit(); conn.close()
    return redirect(url_for("specs_list"))


@app.post("/specs/manage/<kind>/<int:item_id>/<action>")
def specs_manage(kind, item_id, action):
    table_map = {
        "products": ("spec_products","name"),
        "materials": ("spec_product_materials","name"),
        "sizes": ("spec_product_sizes","name"),
        "quantities": ("spec_product_quantities","value_text"),
        "finishings": ("spec_finishings","name"),
        "quick": ("spec_quick_templates","name"),
    }
    if kind not in table_map:
        abort(404)
    table, field = table_map[kind]
    conn = db()
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
        if not row:
            abort(404)

        if action == "edit":
            new_name = request.form.get("name","").strip()
            if new_name:
                conn.execute(f"UPDATE {table} SET {field}=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_name,item_id))
        elif action == "toggle":
            if kind == "quick":
                flash("常用規格不使用停用／恢復；請直接修改或刪除。")
            else:
                conn.execute(f"UPDATE {table} SET is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (0 if row["is_active"] else 1,item_id))
        elif action == "delete":
            if kind == "quick":
                conn.execute("DELETE FROM spec_quick_templates WHERE id=?", (item_id,))
            elif _spec_history_used(kind, str(row[field])):
                flash("此規格已有歷史使用紀錄，無法直接刪除，可改為停用。")
            else:
                conn.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
        else:
            abort(404)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("specs_list"))


@app.post("/specs/quick")
def specs_quick_add():
    template_id = request.form.get("template_id","").strip()
    name = request.form.get("name","").strip()
    product_id = request.form.get("product_id","").strip()
    if not name or not product_id:
        flash("常用規格名稱與品項為必填。")
        return redirect(url_for("specs_list"))

    conn = db()
    values = (
        name, int(product_id),
        request.form.get("material_text","").strip(),
        request.form.get("size_text","").strip(),
        request.form.get("quantity_text","").strip(),
        request.form.get("unit_text","").strip(),
        request.form.get("unit_price") or None,
        request.form.get("note","").strip(),
    )

    if template_id:
        existing = conn.execute("SELECT id FROM spec_quick_templates WHERE id=?", (int(template_id),)).fetchone()
        if not existing:
            conn.close()
            abort(404)
        conn.execute("""
            UPDATE spec_quick_templates
            SET name=?,product_id=?,material_text=?,size_text=?,quantity_text=?,
                unit_text=?,unit_price=?,note=?,is_active=1,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, values + (int(template_id),))
        tid = int(template_id)
        conn.execute("DELETE FROM spec_quick_template_finishings WHERE template_id=?", (tid,))
    else:
        cur = conn.execute("""
            INSERT INTO spec_quick_templates
            (name,product_id,material_text,size_text,quantity_text,unit_text,unit_price,note,is_active)
            VALUES(?,?,?,?,?,?,?,?,1)
        """, values)
        tid = int(cur.lastrowid)

    for f in request.form.getlist("finishings"):
        if f.strip():
            conn.execute(
                "INSERT OR IGNORE INTO spec_quick_template_finishings(template_id,finishing_name) VALUES(?,?)",
                (tid,f.strip())
            )
    conn.commit()
    conn.close()
    flash("常用規格已修改。" if template_id else "常用規格已建立。")
    return redirect(url_for("specs_list"))


@app.get("/api/specs/product/<int:product_id>")
def api_specs_product(product_id):
    conn = db()
    product = conn.execute("""
        SELECT p.id,p.name,COALESCE(u.unit_name,'') AS unit_name
        FROM spec_products p
        LEFT JOIN spec_product_unit u ON u.product_id=p.id AND u.is_active=1
        WHERE p.id=? AND p.is_active=1
    """, (product_id,)).fetchone()
    if not product:
        conn.close()
        return jsonify({"ok":False}),404
    materials = conn.execute("SELECT id,name FROM spec_product_materials WHERE product_id=? AND is_active=1 ORDER BY id", (product_id,)).fetchall()
    sizes = conn.execute("SELECT id,name FROM spec_product_sizes WHERE product_id=? AND is_active=1 ORDER BY id", (product_id,)).fetchall()
    quantities = conn.execute("SELECT id,value_text FROM spec_product_quantities WHERE product_id=? AND is_active=1 ORDER BY sort_order,id", (product_id,)).fetchall()
    finishings = conn.execute("SELECT id,name FROM spec_finishings WHERE is_active=1 ORDER BY id").fetchall()
    conn.close()
    return jsonify({
        "ok":True,
        "product":dict(product),
        "materials":[dict(x) for x in materials],
        "sizes":[dict(x) for x in sizes],
        "quantities":[dict(x) for x in quantities],
        "finishings":[dict(x) for x in finishings],
    })


@app.get("/api/specs/search")
def api_specs_search():
    q = request.args.get("q","").strip()
    like = f"%{q}%"
    conn = db()
    quick = conn.execute("""
        SELECT t.id,t.name,p.name AS product_name,t.material_text AS material,
               t.size_text AS size,t.quantity_text AS quantity,t.unit_text AS unit,
               t.unit_price,
               COALESCE((
                 SELECT GROUP_CONCAT(finishing_name,',')
                 FROM spec_quick_template_finishings f
                 WHERE f.template_id=t.id
               ),'') AS finishing
        FROM spec_quick_templates t
        JOIN spec_products p ON p.id=t.product_id
        WHERE t.is_active=1
          AND (?='' OR t.name LIKE ? OR p.name LIKE ? OR t.material_text LIKE ? OR t.size_text LIKE ?)
        ORDER BY t.id DESC
        LIMIT 12
    """, (q,like,like,like,like)).fetchall()
    products = conn.execute("""
        SELECT id,name FROM spec_products
        WHERE is_active=1 AND (?='' OR name LIKE ?)
        ORDER BY name LIMIT 20
    """, (q,like)).fetchall()
    conn.close()
    return jsonify({"quick":[dict(x) for x in quick],"products":[dict(x) for x in products]})


@app.get("/api/projects/search")
def api_projects_search():
    q = request.args.get("q", "").strip()
    conn = db()
    rows = search_projects(conn, q, customer_id=None, limit=12) if q else []
    conn.close()
    return jsonify(rows)



def refresh_project_status(conn, project_id):
    """Project is complete only when every non-void order is complete and fully settled."""
    if not project_id:
        return "進行中"

    orders = conn.execute(
        "SELECT id,status,tax_mode,tax_rate FROM orders WHERE project_id=? AND status!='廢單' ORDER BY id",
        (int(project_id),),
    ).fetchall()

    if not orders:
        new_status = "進行中"
    else:
        all_complete = True
        for order in orders:
            if order["status"] != "完結":
                all_complete = False
                break

            items = conn.execute(
                "SELECT subtotal FROM order_items WHERE order_id=?",
                (order["id"],),
            ).fetchall()
            subtotal = sum(float(x["subtotal"] or 0) for x in items)
            tax = int(subtotal * 0.05 + 0.5) if order["tax_mode"] == "tax" else 0
            total = subtotal + tax
            paid = conn.execute(
                "SELECT COALESCE(SUM(amount),0) AS s FROM order_payments WHERE order_id=?",
                (order["id"],),
            ).fetchone()["s"] or 0

            if float(paid) + 0.000001 < float(total):
                all_complete = False
                break

        new_status = "已完成" if all_complete else "進行中"

    conn.execute(
        "UPDATE projects SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (new_status, int(project_id)),
    )
    return new_status


def project_return_url(project_id):
    return url_for("projects_detail", project_id=int(project_id)) if project_id else None


def sync_project_status(conn, project_id: int) -> str:
    project = conn.execute("SELECT id,status FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        return ""
    orders = conn.execute("SELECT status FROM orders WHERE project_id=?", (project_id,)).fetchall()
    quotes = conn.execute("SELECT status,converted_order_id FROM quotes WHERE project_id=?", (project_id,)).fetchall()
    active_orders = [r for r in orders if r["status"] != "廢單"]
    active_quotes = [r for r in quotes if r["status"] not in ("已取消","取消","廢單") and not r["converted_order_id"]]
    if (orders or quotes) and not active_orders and not active_quotes:
        status="已取消"
    elif active_orders and all(r["status"]=="完結" for r in active_orders) and not active_quotes:
        status="已完成"
    else:
        status="進行中"
    if project["status"] != status:
        conn.execute("UPDATE projects SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,project_id))
    return status


# ---------------- Projects ----------------

@app.route("/projects")
def projects_list():
    q = request.args.get("q", "").strip()
    conn = db()
    for _p in conn.execute("SELECT id FROM projects").fetchall():
        sync_project_status(conn, int(_p["id"]))
    conn.commit()
    sql = """
        SELECT p.*, c.name AS customer_name,
               (SELECT COUNT(*) FROM quotes q WHERE q.project_id = p.id) AS quote_count,
               (SELECT COUNT(*) FROM orders o WHERE o.project_id = p.id) AS order_count
        FROM projects p
        JOIN customers c ON c.id = p.customer_id
        WHERE 1 = 1
    """
    params: list[Any] = []
    if q:
        sql += " AND (p.project_name LIKE ? OR c.name LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY p.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template("projects/list.html", projects=rows, q=q)


@app.route("/projects/new", methods=["GET", "POST"])
def projects_new():
    conn = db()
    customers = conn.execute("SELECT id, name, category FROM customers WHERE is_active = 1 ORDER BY name").fetchall()
    if request.method == "POST":
        customer_id = request.form.get("customer_id", "").strip()
        project_name = request.form.get("project_name", "").strip()
        if not customer_id or not project_name:
            flash("客戶與專案名稱皆為必填。")
            conn.close()
            return render_template("projects/detail.html", project=None, customers=customers, quotes=[], orders=[])
        cur = conn.execute(
            """
            INSERT INTO projects (customer_id, project_name, status, note)
            VALUES (?, ?, ?, ?)
            """,
            (
                int(customer_id),
                project_name,
                request.form.get("status", "進行中"),
                request.form.get("note", "").strip(),
            ),
        )
        conn.commit()
        pid = int(cur.lastrowid)
        conn.close()
        flash("專案已建立。")
        return redirect(url_for("projects_detail", project_id=pid))
    conn.close()
    return render_template("projects/detail.html", project=None, customers=customers, quotes=[], orders=[])


@app.route("/projects/<int:project_id>")
def projects_detail(project_id):
    conn = db()

    # Status is derived from current order progress + settlement.
    refresh_project_status(conn, project_id)
    conn.commit()

    project = conn.execute(
        """
        SELECT p.*, c.name AS customer_name
        FROM projects p
        JOIN customers c ON c.id = p.customer_id
        WHERE p.id = ?
        """,
        (project_id,),
    ).fetchone()
    if not project:
        conn.close()
        abort(404)

    sync_project_status(conn, project_id)
    conn.commit()
    project = conn.execute("""
        SELECT p.*, c.name AS customer_name
        FROM projects p JOIN customers c ON c.id=p.customer_id
        WHERE p.id=?
    """, (project_id,)).fetchone()

    quotes = conn.execute(
        """
        SELECT q.*, q.quote_number AS number
        FROM quotes q
        WHERE q.project_id = ?
        ORDER BY q.id DESC
        """,
        (project_id,),
    ).fetchall()

    orders = conn.execute(
        """
        SELECT o.*, o.order_number AS number, c.name AS customer_name
        FROM orders o
        JOIN customers c ON c.id=o.customer_id
        WHERE o.project_id = ?
        ORDER BY o.id ASC
        """,
        (project_id,),
    ).fetchall()

    order_summaries = []
    project_total = 0.0
    project_paid = 0.0
    for order in orders:
        items = conn.execute(
            "SELECT * FROM order_items WHERE order_id=? ORDER BY sort_order,id",
            (order["id"],),
        ).fetchall()
        subtotal, tax_amount, total = calculate_totals(items, order["tax_mode"], order["tax_rate"])
        paid_total = float(conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM order_payments WHERE order_id=?",
            (order["id"],),
        ).fetchone()["s"] or 0)
        balance = max(float(total) - paid_total, 0)
        payment_status = "已結清" if float(total) > 0 and balance <= 0 else ("部分收款" if paid_total > 0 else "未收款")
        order_summaries.append({
            "id": order["id"],
            "order_number": order["order_number"],
            "status": order["status"],
            "delivery_date": order["delivery_date"],
            "total": float(total),
            "paid_total": paid_total,
            "balance": balance,
            "payment_status": payment_status,
        })
        if order["status"] != "廢單":
            project_total += float(total)
            project_paid += paid_total

    project_balance = max(project_total - project_paid, 0)

    # Current project overview: combine all work units from every order under this project_id.
    project_units = []
    for order in orders:
        units = conn.execute(
            """
            SELECT wu.*, ? AS order_number, ? AS order_status, ? AS order_customer
            FROM order_work_units wu
            WHERE wu.order_id=?
            ORDER BY wu.sort_order, wu.id
            """,
            (order["order_number"], order["status"], order["customer_name"], order["id"]),
        ).fetchall()
        for wu in units:
            unit_items = conn.execute(
                """
                SELECT oi.*, ? AS order_number, ? AS order_id
                FROM order_items oi
                WHERE oi.order_id=? AND oi.work_unit_id=?
                ORDER BY oi.sort_order,oi.id
                """,
                (order["order_number"], order["id"], order["id"], wu["id"]),
            ).fetchall()
            project_units.append({
                "name": wu["name"],
                "note": wu["note"] or "",
                "order_number": order["order_number"],
                "order_id": order["id"],
                "order_status": order["status"],
                "order_customer": order["customer_name"],
                "items": [dict(x) for x in unit_items],
            })

        loose_items = conn.execute(
            """
            SELECT oi.*, ? AS order_number, ? AS order_id
            FROM order_items oi
            WHERE oi.order_id=? AND oi.work_unit_id IS NULL
            ORDER BY oi.sort_order,oi.id
            """,
            (order["order_number"], order["id"], order["id"]),
        ).fetchall()
        if loose_items:
            project_units.append({
                "name": "未分工作單位",
                "note": "",
                "order_number": order["order_number"],
                "order_id": order["id"],
                "order_status": order["status"],
                "order_customer": order["customer_name"],
                "items": [dict(x) for x in loose_items],
            })

    provisional_units = []
    if not orders:
        for quote in quotes:
            if quote["converted_order_id"]:
                continue
            units = conn.execute(
                "SELECT * FROM quote_work_units WHERE quote_id=? ORDER BY sort_order,id",
                (quote["id"],),
            ).fetchall()
            for wu in units:
                items = conn.execute(
                    "SELECT * FROM quote_items WHERE quote_id=? AND work_unit_id=? ORDER BY sort_order,id",
                    (quote["id"], wu["id"]),
                ).fetchall()
                provisional_units.append({
                    "name": wu["name"],
                    "note": wu["note"] or "",
                    "quote_number": quote["quote_number"],
                    "quote_id": quote["id"],
                    "quote_status": quote["status"],
                    "items": [dict(x) for x in items],
                })

    conn.close()
    return render_template(
        "projects/detail.html",
        project=project,
        quotes=quotes,
        orders=orders,
        order_summaries=order_summaries,
        project_total=project_total,
        project_paid=project_paid,
        project_balance=project_balance,
        project_units=project_units,
        provisional_units=provisional_units,
        customers=[],
    )




@app.post("/projects/<int:project_id>/progress")
def projects_progress(project_id):
    action=request.form.get("action","").strip()
    conn=db()
    if not conn.execute("SELECT id FROM projects WHERE id=?",(project_id,)).fetchone():
        conn.close(); abort(404)
    if action=="complete":
        conn.execute("UPDATE orders SET status='完結',updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND status!='廢單'",(project_id,))
        conn.execute("UPDATE projects SET status='已完成',updated_at=CURRENT_TIMESTAMP WHERE id=?",(project_id,))
        flash("專案進度已標記完成；收款狀態未變更。")
    elif action=="cancel":
        conn.execute("UPDATE orders SET status='廢單',updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND status!='廢單'",(project_id,))
        conn.execute("UPDATE quotes SET status='已取消',updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND converted_order_id IS NULL",(project_id,))
        conn.execute("UPDATE projects SET status='已取消',updated_at=CURRENT_TIMESTAMP WHERE id=?",(project_id,))
        flash("專案進度已取消；收款紀錄未變更。")
    else:
        conn.close(); flash("不支援的專案進度操作。")
        return redirect(url_for("projects_detail",project_id=project_id))
    conn.commit(); conn.close()
    return redirect(url_for("projects_detail",project_id=project_id))


@app.post("/projects/<int:project_id>/payment")
def projects_payment(project_id):
    conn=db()
    if not conn.execute("SELECT id FROM projects WHERE id=?",(project_id,)).fetchone():
        conn.close(); abort(404)
    cols={r["name"] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
    if "payment_status" in cols:
        conn.execute("UPDATE orders SET payment_status='已收款',updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND status!='廢單'",(project_id,))
    elif "paid_amount" in cols and "grand_total" in cols:
        conn.execute("UPDATE orders SET paid_amount=grand_total,updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND status!='廢單'",(project_id,))
    else:
        conn.close(); flash("目前資料庫尚無可用的訂單收款欄位，未變更任何資料。")
        return redirect(url_for("projects_detail",project_id=project_id))
    conn.commit(); conn.close()
    flash("專案內有效訂單已批次沖帳；訂單進度未變更。")
    return redirect(url_for("projects_detail",project_id=project_id))


# ---------------- Quotes ----------------

@app.route("/quotes")
def quotes_list():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    conn = db()
    refresh_expired_quotes(conn)
    conn.commit()
    sql = """
        SELECT q.*, c.name AS customer_name, p.project_name
        FROM quotes q
        JOIN customers c ON c.id = q.customer_id
        LEFT JOIN projects p ON p.id = q.project_id
        WHERE 1 = 1
    """
    params: list[Any] = []
    if q:
        sql += " AND (q.quote_number LIKE ? OR c.name LIKE ? OR COALESCE(p.project_name, '') LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if status:
        sql += " AND q.status = ?"
        params.append(status)
    sql += " ORDER BY q.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template("quotes/list.html", quotes=rows, q=q, status=status)


@app.route("/quotes/new", methods=["GET", "POST"])
def quotes_new():
    conn = db()
    customers = conn.execute("SELECT id, name, category FROM customers WHERE is_active = 1 ORDER BY name").fetchall()
    projects = conn.execute(
        """
        SELECT p.id, p.customer_id, p.project_name, c.name AS customer_name
        FROM projects p
        JOIN customers c ON c.id = p.customer_id
        ORDER BY p.id DESC
        """
    ).fetchall()
    finishing_options = list_finishing_options(conn)

    if request.method == "POST":
        payload = parse_payload()
        if not payload:
            flash("表單資料有誤。")
            conn.close()
            return redirect(url_for("quotes_new"))
        try:
            quote_id = create_quote_from_payload(conn, payload)
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f"建立報價失敗：{e}")
            return redirect(url_for("quotes_new"))
        conn.close()
        flash("報價已建立。")
        return redirect(url_for("quotes_detail", quote_id=quote_id))

    conn.close()
    return render_template(
        "quotes/form.html",
        customers=customers,
        projects=projects,
        finishing_options=finishing_options,
        mode="normal",
        draft=None,
    )


@app.route("/quotes/<int:quote_id>")
def quotes_detail(quote_id):
    conn = db()
    quote = conn.execute(
        """
        SELECT q.*, c.name AS customer_name, c.contact_person, c.tax_id, c.phone, c.email, p.project_name
        FROM quotes q
        JOIN customers c ON c.id = q.customer_id
        LEFT JOIN projects p ON p.id = q.project_id
        WHERE q.id = ?
        """,
        (quote_id,),
    ).fetchone()
    if not quote:
        conn.close()
        abort(404)
    items = conn.execute(
        "SELECT * FROM quote_items WHERE quote_id = ? ORDER BY sort_order, id",
        (quote_id,),
    ).fetchall()
    work_units = conn.execute(
        "SELECT * FROM quote_work_units WHERE quote_id = ? ORDER BY sort_order, id",
        (quote_id,),
    ).fetchall()
    subtotal, tax_amount, total = calculate_totals(items, quote["tax_mode"], quote["tax_rate"])
    conn.close()
    return render_template("quotes/detail.html", quote=quote, items=items, work_units=work_units,
                           subtotal=subtotal, tax_amount=tax_amount, total=total)



@app.get("/quotes/<int:quote_id>/pdf")
def quotes_pdf(quote_id):
    conn = db()
    quote = conn.execute(
        """
        SELECT q.*, c.name AS customer_name, c.contact_person, c.tax_id, c.phone, c.email,
               p.project_name
        FROM quotes q
        JOIN customers c ON c.id = q.customer_id
        LEFT JOIN projects p ON p.id = q.project_id
        WHERE q.id = ?
        """,
        (quote_id,),
    ).fetchone()
    if not quote:
        conn.close()
        abort(404)
    items = conn.execute(
        "SELECT * FROM quote_items WHERE quote_id=? ORDER BY sort_order,id",
        (quote_id,),
    ).fetchall()
    work_units = conn.execute(
        "SELECT * FROM quote_work_units WHERE quote_id=? ORDER BY sort_order,id",
        (quote_id,),
    ).fetchall()
    subtotal, tax_amount, total = calculate_totals(items, quote["tax_mode"], quote["tax_rate"])
    conn.close()

    pdf_bytes = build_document_pdf(
        "quote",
        quote,
        items,
        work_units,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total=total,
    )
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf_download_name("報價單", quote["quote_number"], quote["customer_name"]),
        max_age=0,
    )


@app.route("/quotes/<int:quote_id>/edit", methods=["GET", "POST"])
def quotes_edit(quote_id):
    conn = db()
    quote = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    if not quote:
        conn.close()
        abort(404)
    if quote["converted_order_id"]:
        conn.close()
        flash("此報價已轉成訂單，請至訂單修改。")
        return redirect(url_for("quotes_detail", quote_id=quote_id))

    if request.method == "POST":
        payload = parse_payload()
        if not payload:
            conn.close()
            flash("表單資料有誤。")
            return redirect(url_for("quotes_edit", quote_id=quote_id))
        try:
            update_quote_from_payload(conn, quote_id, payload)
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f"修改報價失敗：{e}")
            return redirect(url_for("quotes_edit", quote_id=quote_id))
        conn.close()
        flash("報價已更新。")
        return redirect(url_for("quotes_detail", quote_id=quote_id))

    draft = quote_to_payload(conn, quote_id)
    conn.close()
    return render_template(
        "quotes/form.html",
        customers=[],
        projects=[],
        finishing_options=[],
        mode=draft.get("mode", "normal"),
        draft=draft,
        edit_quote_id=quote_id,
    )


@app.post("/quotes/<int:quote_id>/delete")
def quotes_delete(quote_id):
    conn = db()
    quote = conn.execute(
        "SELECT id,quote_number,converted_order_id FROM quotes WHERE id=?",
        (quote_id,)
    ).fetchone()
    if not quote:
        conn.close()
        abort(404)
    if quote["converted_order_id"]:
        conn.close()
        flash("此報價已轉成訂單，不能直接刪除。")
        return redirect(url_for("quotes_detail", quote_id=quote_id))

    try:
        # quote_items and quote_work_units are subordinate quote data.
        conn.execute("DELETE FROM quote_items WHERE quote_id=?", (quote_id,))
        conn.execute("DELETE FROM quote_work_units WHERE quote_id=?", (quote_id,))
        conn.execute("DELETE FROM quotes WHERE id=?", (quote_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        flash(f"刪除報價失敗：{e}")
        return redirect(url_for("quotes_detail", quote_id=quote_id))
    conn.close()
    flash(f"報價 {quote['quote_number']} 已刪除。")
    return redirect(url_for("quotes_list"))



@app.route("/quotes/<int:quote_id>/convert", methods=["POST"])
def quotes_convert(quote_id):
    conn = db()
    quote = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    if not quote:
        conn.close()
        abort(404)
    try:
        order_id = convert_quote_to_order(conn, quote_id)
        converted = conn.execute("SELECT project_id FROM orders WHERE id=?", (order_id,)).fetchone()
        if converted and converted["project_id"]:
            refresh_project_status(conn, converted["project_id"])
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        flash(f"轉訂單失敗：{e}")
        return redirect(url_for("quotes_detail", quote_id=quote_id))
    conn.close()
    flash("已轉成訂單。")
    return redirect(url_for("orders_detail", order_id=order_id))


# ---------------- Orders ----------------

@app.route("/orders")
def orders_list():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    conn = db()
    sql = """
        SELECT o.*, c.name AS customer_name, p.project_name, q.quote_number
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        LEFT JOIN projects p ON p.id = o.project_id
        LEFT JOIN quotes q ON q.id = o.quote_id
        WHERE 1 = 1
    """
    params: list[Any] = []
    if q:
        sql += " AND (o.order_number LIKE ? OR c.name LIKE ? OR COALESCE(p.project_name, '') LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if status:
        sql += " AND o.status = ?"
        params.append(status)
    sql += " ORDER BY o.created_at DESC, o.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template("orders/list.html", orders=rows, q=q, status=status)


@app.route("/orders/new", methods=["GET", "POST"])
def orders_new():
    if request.method == "POST":
        conn = db()
        payload = parse_payload()
        if not payload:
            conn.close()
            flash("表單資料有誤。")
            return redirect(url_for("orders_new"))
        try:
            order_id = create_order_from_payload(conn, payload, source_quote_id=None)
            created_order = conn.execute("SELECT project_id FROM orders WHERE id=?", (order_id,)).fetchone()
            if created_order and created_order["project_id"]:
                refresh_project_status(conn, created_order["project_id"])
            conn.commit()
        except Exception as e:
            conn.rollback(); conn.close()
            flash(f"建立訂單失敗：{e}")
            return redirect(url_for("orders_new"))
        conn.close()
        flash("訂單已建立。")
        return redirect(url_for("orders_detail", order_id=order_id))

    today_local = current_local_date_input()
    return render_template(
        "orders/form.html",
        draft=None,
        edit_order_id=None,
        default_created_date=today_local,
        max_created_date=today_local,
    )


@app.route("/orders/<int:order_id>/edit", methods=["GET", "POST"])
def orders_edit(order_id):
    from_project = request.values.get("from_project", "").strip()
    conn = db()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        conn.close(); abort(404)
    if order["status"] == "廢單":
        conn.close()
        flash("廢單不可直接修改內容。")
        return redirect(url_for("orders_detail", order_id=order_id))

    if request.method == "POST":
        payload = parse_payload()
        if not payload:
            conn.close()
            flash("表單資料有誤。")
            return redirect(url_for("orders_edit", order_id=order_id))
        try:
            update_order_from_payload(conn, order_id, payload)
            updated_order = conn.execute("SELECT project_id FROM orders WHERE id=?", (order_id,)).fetchone()
            if updated_order and updated_order["project_id"]:
                refresh_project_status(conn, updated_order["project_id"])
            conn.commit()
        except Exception as e:
            conn.rollback(); conn.close()
            flash(f"修改訂單失敗：{e}")
            return redirect(url_for("orders_edit", order_id=order_id))
        project_id = order["project_id"]
        conn.close()
        flash("訂單已更新。")
        if from_project and project_id and str(project_id) == str(from_project):
            return redirect(url_for("projects_detail", project_id=project_id))
        return redirect(url_for("orders_detail", order_id=order_id))

    draft = order_to_payload(conn, order_id)
    conn.close()
    return render_template("orders/form.html", draft=draft, edit_order_id=order_id, from_project=from_project)


@app.route("/orders/<int:order_id>")
def orders_detail(order_id):
    from_project = request.args.get("from_project", "").strip()
    conn = db()
    order = conn.execute(
        """
        SELECT o.*, c.name AS customer_name, c.contact_person, c.tax_id, c.phone, c.email,
               p.project_name, q.quote_number
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        LEFT JOIN projects p ON p.id = o.project_id
        LEFT JOIN quotes q ON q.id = o.quote_id
        WHERE o.id = ?
        """,
        (order_id,),
    ).fetchone()
    if not order:
        conn.close(); abort(404)
    items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY sort_order,id", (order_id,)).fetchall()
    work_units = conn.execute("SELECT * FROM order_work_units WHERE order_id=? ORDER BY sort_order,id", (order_id,)).fetchall()
    payments = conn.execute("SELECT * FROM order_payments WHERE order_id=? ORDER BY paid_at,id", (order_id,)).fetchall()
    subtotal, tax_amount, total = calculate_totals(items, order["tax_mode"], order["tax_rate"])
    paid_total = sum(float(x["amount"] or 0) for x in payments)
    balance = max(total - paid_total, 0)
    payment_status = "已結清" if total > 0 and balance <= 0 else ("部分收款" if paid_total > 0 else "未收款")
    conn.close()
    return render_template(
        "orders/detail.html", order=order, items=items, work_units=work_units,
        subtotal=subtotal, tax_amount=tax_amount, total=total,
        payments=payments, paid_total=paid_total, balance=balance, payment_status=payment_status,
        from_project=from_project,
    )


@app.get("/orders/<int:order_id>/pdf")
def orders_pdf(order_id):
    conn = db()
    order = conn.execute(
        """
        SELECT o.*, c.name AS customer_name, c.contact_person, c.tax_id, c.phone, c.email,
               p.project_name, q.quote_number
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        LEFT JOIN projects p ON p.id = o.project_id
        LEFT JOIN quotes q ON q.id = o.quote_id
        WHERE o.id = ?
        """,
        (order_id,),
    ).fetchone()
    if not order:
        conn.close()
        abort(404)
    items = conn.execute(
        "SELECT * FROM order_items WHERE order_id=? ORDER BY sort_order,id",
        (order_id,),
    ).fetchall()
    work_units = conn.execute(
        "SELECT * FROM order_work_units WHERE order_id=? ORDER BY sort_order,id",
        (order_id,),
    ).fetchall()
    paid_total = float(conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM order_payments WHERE order_id=?",
        (order_id,),
    ).fetchone()["total"] or 0)
    subtotal, tax_amount, total = calculate_totals(items, order["tax_mode"], order["tax_rate"])
    balance = max(float(total) - paid_total, 0)
    conn.close()

    pdf_bytes = build_document_pdf(
        "order",
        order,
        items,
        work_units,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total=total,
        paid_total=paid_total,
        balance=balance,
    )
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf_download_name("訂單", order["order_number"], order["customer_name"]),
        max_age=0,
    )


@app.post("/orders/<int:order_id>/status")
def orders_status(order_id):
    status = request.form.get("status", "").strip()
    return_project = request.form.get("return_project", "").strip()
    if status not in ORDER_STATUSES or status == "廢單":
        flash("不支援的訂單狀態。")
        return redirect(url_for("projects_detail", project_id=return_project)) if return_project else redirect(url_for("orders_detail", order_id=order_id))
    conn = db()
    order = conn.execute("SELECT id,project_id FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        conn.close(); abort(404)
    conn.execute("UPDATE orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status,order_id))
    if order["project_id"]:
        refresh_project_status(conn, order["project_id"])
    conn.commit(); conn.close()
    flash("訂單狀態已更新。")
    if return_project:
        return redirect(url_for("projects_detail", project_id=return_project))
    return redirect(url_for("orders_detail", order_id=order_id))


@app.post("/orders/<int:order_id>/payment")
def orders_payment(order_id):
    amount_text = request.form.get("amount","").strip()
    note = request.form.get("note","").strip()
    return_project = request.form.get("return_project", "").strip()
    try:
        amount = float(amount_text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash("沖帳金額必須大於 0。")
        return redirect(url_for("projects_detail", project_id=return_project)) if return_project else redirect(url_for("orders_detail", order_id=order_id))
    conn = db()
    order = conn.execute("SELECT id,status,project_id FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        conn.close(); abort(404)
    if order["status"] == "廢單":
        conn.close()
        flash("廢單不可沖帳。")
        return redirect(url_for("projects_detail", project_id=return_project)) if return_project else redirect(url_for("orders_detail", order_id=order_id))
    conn.execute("INSERT INTO order_payments(order_id,amount,note) VALUES(?,?,?)", (order_id,amount,note))
    if order["project_id"]:
        refresh_project_status(conn, order["project_id"])
    conn.commit(); conn.close()
    flash("沖帳紀錄已新增。")
    if return_project:
        return redirect(url_for("projects_detail", project_id=return_project))
    return redirect(url_for("orders_detail", order_id=order_id))


@app.post("/orders/<int:order_id>/void")
def orders_void(order_id):
    reason = request.form.get("reason","").strip()
    if not reason:
        flash("廢單請填寫原因。")
        return redirect(url_for("orders_detail", order_id=order_id))
    conn = db()
    order = conn.execute("SELECT id,status,project_id FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        conn.close(); abort(404)
    conn.execute(
        "UPDATE orders SET status='廢單',void_reason=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (reason,order_id),
    )
    if order["project_id"]:
        refresh_project_status(conn, order["project_id"])
    conn.commit(); conn.close()
    flash("訂單已標記為廢單，歷史資料仍保留。")
    return redirect(url_for("orders_detail", order_id=order_id))


@app.get("/api/orders/smart-search")
def api_orders_smart_search():
    """
    V2.8.1 Smart search:
    broad recall + relevance ranking.
    Priority: product > specification > customer/work-unit > recency.
    Customer/spec fields are ranking signals, not hard filters.
    """
    customer = request.args.get("customer", "").strip()
    product = request.args.get("product", "").strip()
    material = request.args.get("material", "").strip()
    size = request.args.get("size", "").strip()
    finishing = request.args.get("finishing", "").strip()
    quantity = request.args.get("quantity", "").strip()
    work_unit = request.args.get("work_unit", "").strip()
    linked_customer_id = request.args.get("linked_customer_id", "").strip()
    q = request.args.get("q", "").strip()
    exclude_order_id = request.args.get("exclude_order_id", "").strip()

    if not any([customer, product, material, size, finishing, quantity, work_unit, linked_customer_id, q]):
        return jsonify([])

    conn = db()
    where = ["o.status != '廢單'"]
    where_params = []

    # The typed product is the primary recall condition.
    # Once a product exists, keep all same/similar-product history regardless
    # of customer/spec differences. Other fields only change ranking.
    if product:
        where.append("oi.product_name LIKE ?")
        where_params.append(f"%{product}%")
    elif q:
        like = f"%{q}%"
        where.append("""(
            o.order_number LIKE ? OR c.name LIKE ? OR
            EXISTS (SELECT 1 FROM customer_aliases ca WHERE ca.customer_id=c.id AND ca.alias LIKE ?) OR
            COALESCE(p.project_name,'') LIKE ? OR COALESCE(ow.name,'') LIKE ? OR
            oi.product_name LIKE ? OR COALESCE(oi.material,'') LIKE ? OR
            COALESCE(oi.size,'') LIKE ? OR COALESCE(oi.finishing,'') LIKE ?
        )""")
        where_params.extend([like] * 9)
    elif customer:
        # Before a product is entered, customer history is useful as a preview.
        where.append("""(c.name LIKE ? OR EXISTS (
            SELECT 1 FROM customer_aliases ca WHERE ca.customer_id=c.id AND ca.alias LIKE ?
        ))""")
        where_params.extend([f"%{customer}%", f"%{customer}%"])
    elif work_unit:
        where.append("COALESCE(ow.name,'') LIKE ?")
        where_params.append(f"%{work_unit}%")

    if exclude_order_id:
        try:
            where.append("o.id != ?")
            where_params.append(int(exclude_order_id))
        except ValueError:
            pass

    score_parts = []
    score_params = []

    # 1) Product — dominant weight.
    if product:
        score_parts += [
            "CASE WHEN oi.product_name = ? THEN 10000 ELSE 0 END",
            "CASE WHEN oi.product_name LIKE ? THEN 5000 ELSE 0 END",
        ]
        score_params += [product, f"%{product}%"]

    # 2) Specification — material / size / finishing / quantity.
    if material:
        score_parts += [
            "CASE WHEN COALESCE(oi.material,'') = ? THEN 1200 ELSE 0 END",
            "CASE WHEN COALESCE(oi.material,'') LIKE ? THEN 500 ELSE 0 END",
        ]
        score_params += [material, f"%{material}%"]
    if size:
        score_parts += [
            "CASE WHEN COALESCE(oi.size,'') = ? THEN 1200 ELSE 0 END",
            "CASE WHEN COALESCE(oi.size,'') LIKE ? THEN 500 ELSE 0 END",
        ]
        score_params += [size, f"%{size}%"]
    if finishing:
        score_parts += [
            "CASE WHEN COALESCE(oi.finishing,'') = ? THEN 800 ELSE 0 END",
            "CASE WHEN COALESCE(oi.finishing,'') LIKE ? THEN 350 ELSE 0 END",
        ]
        score_params += [finishing, f"%{finishing}%"]
    if quantity:
        try:
            qn=float(quantity)
            score_parts += [
                "CASE WHEN ABS(COALESCE(oi.quantity,0)-?) < 0.000001 THEN 900 ELSE 0 END",
                """CASE
                    WHEN ? > 0 AND COALESCE(oi.quantity,0) > 0
                     AND ABS(COALESCE(oi.quantity,0)-?) / ? <= 0.25
                    THEN 300 ELSE 0 END"""
            ]
            score_params += [qn, qn, qn, qn]
        except ValueError:
            pass

    # 3) Customer / project work-unit — useful, but never stronger than specs.
    if linked_customer_id:
        try:
            cid=int(linked_customer_id)
            score_parts.append("CASE WHEN ow.linked_customer_id = ? THEN 450 ELSE 0 END")
            score_params.append(cid)
        except ValueError:
            pass
    if work_unit:
        score_parts += [
            "CASE WHEN COALESCE(ow.name,'') = ? THEN 400 ELSE 0 END",
            "CASE WHEN COALESCE(ow.name,'') LIKE ? THEN 180 ELSE 0 END",
        ]
        score_params += [work_unit, f"%{work_unit}%"]
    if customer:
        score_parts += [
            "CASE WHEN c.name = ? THEN 350 ELSE 0 END",
            "CASE WHEN c.name LIKE ? THEN 150 ELSE 0 END",
            "CASE WHEN EXISTS (SELECT 1 FROM customer_aliases ca WHERE ca.customer_id=c.id AND ca.alias = ?) THEN 325 ELSE 0 END",
            "CASE WHEN EXISTS (SELECT 1 FROM customer_aliases ca WHERE ca.customer_id=c.id AND ca.alias LIKE ?) THEN 140 ELSE 0 END",
        ]
        score_params += [customer, f"%{customer}%", customer, f"%{customer}%"]

    # Free-text search is an additional ranking signal when product already drives recall.
    if q and product:
        like=f"%{q}%"
        score_parts.append("""CASE WHEN (
            o.order_number LIKE ? OR c.name LIKE ? OR
            EXISTS (SELECT 1 FROM customer_aliases ca WHERE ca.customer_id=c.id AND ca.alias LIKE ?) OR
            COALESCE(p.project_name,'') LIKE ? OR COALESCE(ow.name,'') LIKE ? OR
            COALESCE(oi.material,'') LIKE ? OR COALESCE(oi.size,'') LIKE ? OR
            COALESCE(oi.finishing,'') LIKE ?
        ) THEN 120 ELSE 0 END""")
        score_params.extend([like] * 8)

    score_sql = " + ".join(score_parts) if score_parts else "0"

    sql=f"""
        SELECT
            oi.id AS item_id, oi.order_id, oi.product_name,
            COALESCE(oi.material,'') AS material,
            COALESCE(oi.size,'') AS size,
            COALESCE(oi.finishing,'') AS finishing,
            oi.quantity, COALESCE(oi.unit,'') AS unit,
            oi.unit_price, oi.subtotal, COALESCE(oi.note,'') AS note,
            o.order_number, o.status AS order_status, o.created_at, o.delivery_date,
            c.name AS customer_name,
            COALESCE(p.project_name,'') AS project_name,
            COALESCE(ow.name,'') AS work_unit_name,
            ow.linked_customer_id AS work_unit_customer_id,
            ({score_sql}) AS relevance_score
        FROM order_items oi
        JOIN orders o ON o.id=oi.order_id
        JOIN customers c ON c.id=o.customer_id
        LEFT JOIN projects p ON p.id=o.project_id
        LEFT JOIN order_work_units ow ON ow.id=oi.work_unit_id
        WHERE {' AND '.join(where)}
        ORDER BY relevance_score DESC, o.created_at DESC, o.id DESC, oi.id DESC
        LIMIT 40
    """
    rows=conn.execute(sql, score_params + where_params).fetchall()
    conn.close()
    return jsonify([dict(x) for x in rows])


@app.get("/api/orders/<int:order_id>/history-detail")
def api_order_history_detail(order_id):
    conn=db()
    order=conn.execute(
        """
        SELECT o.*, c.name AS customer_name, c.contact_person, c.tax_id, c.phone,
               COALESCE(p.project_name,'') AS project_name
        FROM orders o
        JOIN customers c ON c.id=o.customer_id
        LEFT JOIN projects p ON p.id=o.project_id
        WHERE o.id=?
        """,
        (order_id,),
    ).fetchone()
    if not order:
        conn.close()
        return jsonify({"ok":False}),404

    items=conn.execute(
        """
        SELECT oi.*, COALESCE(ow.name,'') AS work_unit_name
        FROM order_items oi
        LEFT JOIN order_work_units ow ON ow.id=oi.work_unit_id
        WHERE oi.order_id=?
        ORDER BY COALESCE(ow.sort_order,0),oi.sort_order,oi.id
        """,
        (order_id,),
    ).fetchall()
    subtotal,tax_amount,total=calculate_totals(items,order["tax_mode"],order["tax_rate"])
    paid_total = float(conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM order_payments WHERE order_id=?",
        (order_id,),
    ).fetchone()["total"] or 0)
    order_data = dict(order)
    order_data["contact_person"] = display_customer_contact(
        order_data["customer_name"], order_data["contact_person"]
    )
    order_data["created_at_display"] = display_date(order["created_at"])
    conn.close()
    return jsonify({
        "ok":True,
        "order":order_data,
        "items":[dict(x) for x in items],
        "subtotal":subtotal,
        "tax_amount":tax_amount,
        "total":total,
        "paid_total":paid_total,
        "balance":max(float(total)-paid_total,0),
    })


# ---------------- Export / backup basics ----------------

@app.get("/admin/backups")
def admin_backups():
    return render_template(
        "admin/backups.html",
        title="備份管理",
        backups=list_backups(),
        daily_retention=DAILY_RETENTION,
        monthly_retention=MONTHLY_RETENTION,
    )


@app.post("/admin/backups/create")
def admin_backups_create():
    try:
        path = create_backup()
        flash(f"備份完成：{path.name}")
    except Exception as exc:
        flash(f"備份失敗：{exc}")
    return redirect(url_for("admin_backups"))


@app.get("/admin/backups/<path:name>/download")
def admin_backups_download(name):
    try:
        path = resolve_backup(name)
    except (ValueError, FileNotFoundError):
        abort(404)
    return send_from_directory(path.parent, path.name, as_attachment=True)


@app.post("/admin/backups/<path:name>/restore")
def admin_backups_restore(name):
    if request.form.get("confirmation", "").strip() != "完整還原":
        flash("還原已取消：請輸入「完整還原」確認。")
        return redirect(url_for("admin_backups"))
    try:
        safety = restore_backup(name)
        flash(f"完整還原成功；還原前保命備份：{safety.name}")
    except Exception as exc:
        flash(f"還原失敗，未完成資料庫替換：{exc}")
    return redirect(url_for("admin_backups"))

@app.route("/admin/export/json")
def admin_export_json():
    conn = db()
    data = {}
    for table in [
        "customers", "projects", "spec_presets", "finishing_options",
        "quotes", "quote_work_units", "quote_items",
        "orders", "order_work_units", "order_items", "order_payments",
        "daily_sequences",
    ]:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        data[table] = rows_dict(rows)
    conn.close()
    return jsonify(data)


@app.route("/healthz")
def healthz():
    return {
        "ok": True,
        "app": "PrintShop",
        "version": APP_VERSION,
        "data_root": str(DATA_ROOT),
    }


if __name__ == "__main__":
    init_database()
    ensure_automatic_backups(force_check=True)
    port = int(os.environ.get("PRINTSHOP_PORT", "5000"))
    debug = os.environ.get("PRINTSHOP_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
