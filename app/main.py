
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for

from .database import (
    ORDER_STATUSES,
    PROJECT_STATUSES,
    QUOTE_STATUSES,
    connect,
    count_tables,
    create_order_from_payload,
    create_quote_from_payload,
    convert_quote_to_order,
    display_date,
    init_database,
    list_finishing_options,
    order_to_payload,
    quote_to_payload,
    refresh_expired_quotes,
    row_dict,
    rows_dict,
    search_customers,
    search_order_items,
    search_projects,
    search_spec_presets,
    today_key,
)

app = Flask(__name__)
app.secret_key = "printshop-v2.2-secret"


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

    payload = {
        "customer_id": customer_id or None,
        "customer_name": customer_name,
        "customer_contact": request.form.get("customer_contact", "").strip(),
        "customer_tax_id": request.form.get("customer_tax_id", "").strip(),
        "customer_phone": request.form.get("customer_phone", "").strip(),
        "mode": mode,
        "project_name": request.form.get("project_name", "").strip(),
        "note": request.form.get("note", "").strip(),
        "status": "報價中",
        "items": items if mode != "project" else [],
        "work_units": work_units if mode == "project" else [],
    }
    return payload


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


@app.context_processor
def inject_globals():
    return {
        "customer_categories": ["一般", "學校", "政府", "公司", "宗親會"],
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
                q.created_at AS created_at
            FROM quotes q
            JOIN customers c ON c.id = q.customer_id
            LEFT JOIN projects p ON p.id = q.project_id
            WHERE NOT EXISTS (
                SELECT 1 FROM orders o WHERE o.quote_id = q.id
            )

            UNION ALL

            SELECT
                'order' AS record_type,
                o.id AS record_id,
                o.order_number AS document_number,
                c.name AS customer_name,
                p.project_name AS project_name,
                o.status AS status,
                o.created_at AS created_at
            FROM orders o
            JOIN customers c ON c.id = o.customer_id
            LEFT JOIN projects p ON p.id = o.project_id
        )
        ORDER BY created_at DESC, record_id DESC
        LIMIT 12
        """
    ).fetchall()

    conn.close()
    return render_template(
        "index.html",
        stats=stats,
        recent_entries=recent_entries,
    )


# ---------------- Customers ----------------

@app.route("/customers")
def customers_list():
    q = request.args.get("q", "").strip()
    conn = db()
    if q:
        rows = conn.execute(
            """
            SELECT *
            FROM customers
            WHERE is_active = 1
              AND (
                name LIKE ? OR contact_person LIKE ? OR tax_id LIKE ? OR phone LIKE ? OR email LIKE ?
              )
            ORDER BY name
            """,
            (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM customers WHERE is_active = 1 ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("customers/list.html", customers=rows, q=q)


@app.route("/customers/new", methods=["GET", "POST"])
def customers_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("客戶名稱不能空白。")
            return redirect(url_for("customers_new"))
        conn = db()
        cur = conn.execute(
            """
            INSERT INTO customers (name, contact_person, tax_id, phone, email, category, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                request.form.get("contact_person", "").strip(),
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
        conn.execute(
            """
            UPDATE customers
               SET name = ?,
                   contact_person = ?,
                   tax_id = ?,
                   phone = ?,
                   email = ?,
                   category = ?,
                   note = ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (
                request.form.get("name", "").strip(),
                request.form.get("contact_person", "").strip(),
                request.form.get("tax_id", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("category", "一般"),
                request.form.get("note", "").strip(),
                customer_id,
            ),
        )
        conn.commit()
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
    name = request.form.get("name","").strip()
    product_id = request.form.get("product_id","").strip()
    if not name or not product_id:
        flash("常用規格名稱與品項為必填。")
        return redirect(url_for("specs_list"))
    conn = db()
    cur = conn.execute("""
        INSERT INTO spec_quick_templates
        (name,product_id,material_text,size_text,quantity_text,unit_text,unit_price,note,is_active)
        VALUES(?,?,?,?,?,?,?,?,1)
    """, (
        name,int(product_id),
        request.form.get("material_text","").strip(),
        request.form.get("size_text","").strip(),
        request.form.get("quantity_text","").strip(),
        request.form.get("unit_text","").strip(),
        request.form.get("unit_price") or None,
        request.form.get("note","").strip()
    ))
    tid = int(cur.lastrowid)
    for f in request.form.getlist("finishings"):
        if f.strip():
            conn.execute("INSERT OR IGNORE INTO spec_quick_template_finishings(template_id,finishing_name) VALUES(?,?)", (tid,f.strip()))
    conn.commit(); conn.close()
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


# ---------------- Projects ----------------

@app.route("/projects")
def projects_list():
    q = request.args.get("q", "").strip()
    conn = db()
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
        SELECT o.*, o.order_number AS number
        FROM orders o
        WHERE o.project_id = ?
        ORDER BY o.id DESC
        """,
        (project_id,),
    ).fetchall()
    conn.close()
    return render_template("projects/detail.html", project=project, quotes=quotes, orders=orders, customers=[])





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
    total = sum(float(r["subtotal"] or 0) for r in items)
    conn.close()
    return render_template("quotes/detail.html", quote=quote, items=items, work_units=work_units, total=total)


@app.route("/quotes/<int:quote_id>/status", methods=["POST"])
def quotes_status(quote_id):
    status = request.form.get("status", "").strip()
    if status not in QUOTE_STATUSES:
        flash("不支援的報價狀態。")
        return redirect(url_for("quotes_detail", quote_id=quote_id))
    conn = db()
    conn.execute(
        "UPDATE quotes SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, quote_id),
    )
    conn.commit()
    conn.close()
    flash("報價狀態已更新。")
    return redirect(url_for("quotes_detail", quote_id=quote_id))


@app.route("/quotes/<int:quote_id>/convert", methods=["POST"])
def quotes_convert(quote_id):
    conn = db()
    quote = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    if not quote:
        conn.close()
        abort(404)
    try:
        order_id = convert_quote_to_order(conn, quote_id)
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
    sql += " ORDER BY o.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template("orders/list.html", orders=rows, q=q, status=status)


@app.route("/orders/new", methods=["GET", "POST"])
def orders_new():
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
            return redirect(url_for("orders_new"))
        try:
            order_id = create_order_from_payload(conn, payload, source_quote_id=None)
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f"建立訂單失敗：{e}")
            return redirect(url_for("orders_new"))
        conn.close()
        flash("訂單已建立。")
        return redirect(url_for("orders_detail", order_id=order_id))

    conn.close()
    return render_template(
        "orders/form.html",
        customers=customers,
        projects=projects,
        finishing_options=finishing_options,
        smart_search_endpoint=url_for("api_orders_smart_search"),
        draft=None,
    )


@app.route("/orders/<int:order_id>")
def orders_detail(order_id):
    conn = db()
    order = conn.execute(
        """
        SELECT o.*, c.name AS customer_name, c.contact_person, c.tax_id, c.phone, c.email, p.project_name, q.quote_number
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
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY sort_order, id",
        (order_id,),
    ).fetchall()
    work_units = conn.execute(
        "SELECT * FROM order_work_units WHERE order_id = ? ORDER BY sort_order, id",
        (order_id,),
    ).fetchall()
    total = sum(float(r["subtotal"] or 0) for r in items)
    conn.close()
    return render_template("orders/detail.html", order=order, items=items, work_units=work_units, total=total)


@app.route("/orders/<int:order_id>/status", methods=["POST"])
def orders_status(order_id):
    status = request.form.get("status", "").strip()
    if status not in ORDER_STATUSES:
        flash("不支援的訂單狀態。")
        return redirect(url_for("orders_detail", order_id=order_id))
    conn = db()
    conn.execute(
        "UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, order_id),
    )
    conn.commit()
    conn.close()
    flash("訂單狀態已更新。")
    return redirect(url_for("orders_detail", order_id=order_id))


@app.route("/api/orders/smart-search")
def api_orders_smart_search():
    q = request.args.get("q", "").strip()
    conn = db()
    rows = search_order_items(conn, q, limit=20) if q else []
    conn.close()
    return jsonify(rows)


# ---------------- Export / backup basics ----------------

@app.route("/admin/export/json")
def admin_export_json():
    conn = db()
    data = {}
    for table in [
        "customers", "projects", "spec_presets", "finishing_options",
        "quotes", "quote_work_units", "quote_items",
        "orders", "order_work_units", "order_items",
        "daily_sequences",
    ]:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        data[table] = rows_dict(rows)
    conn.close()
    return jsonify(data)


@app.route("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    init_database()
    app.run(host="0.0.0.0", port=5000, debug=True)
