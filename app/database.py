
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("PRINTSHOP_DATA_DIR", BASE_DIR)).expanduser().resolve()
DB_DIR = DATA_ROOT / "database"
DB_PATH = DB_DIR / "printshop.db"
BACKUP_DIR = DATA_ROOT / "backups"
SCHEMA_VERSION = 2

CUSTOMER_CATEGORIES = ["一般", "學校", "政府", "公司", "宗親會"]
CUSTOMER_TYPES = {"person": "個人", "organization": "公司或單位"}
QUOTE_STATUSES = ["報價中", "無下訂報價", "已轉訂單"]
ORDER_STATUSES = ["設計中", "印製中", "待取貨", "完結", "廢單"]
PROJECT_STATUSES = ["進行中", "暫停", "已完成", "已取消"]

DEFAULT_FINISHING_OPTIONS = [
    "燙金", "倒圓角", "上亮膜", "上霧膜", "流水號"
]


def connect() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def rows_dict(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def normalize_customer_text(value: Any) -> str:
    """Normalize customer names for duplicate and alias comparisons."""
    return "".join(str(value or "").split()).casefold()


def display_customer_contact(customer_name: Any, contact_person: Any) -> str:
    """Avoid showing a personal customer's name twice."""
    contact = str(contact_person or "").strip()
    if normalize_customer_text(customer_name) == normalize_customer_text(contact):
        return ""
    return contact


def clean_customer_contact(customer_type: str, customer_name: Any, contact_person: Any) -> str:
    contact = display_customer_contact(customer_name, contact_person)
    return "" if customer_type == "person" else contact


TAIPEI_TZ = ZoneInfo("Asia/Taipei")

def today_key() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%y%m%d")


def current_local_date_input() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def normalize_order_created_at(local_value: str | None) -> tuple[str, str]:
    """Convert a Taiwan date value to SQLite UTC and its YYMMDD serial key."""
    now_local = datetime.now(TAIPEI_TZ)
    if local_value:
        try:
            selected_date = date.fromisoformat(local_value.strip()[:10])
        except (TypeError, ValueError) as exc:
            raise ValueError("建立日期格式不正確。") from exc
        if selected_date > now_local.date():
            raise ValueError("建立日期不可晚於今天。")
        if selected_date == now_local.date():
            local_dt = now_local
        else:
            # Historical entries only need a date. Noon avoids timezone date rollover.
            local_dt = datetime(
                selected_date.year,
                selected_date.month,
                selected_date.day,
                12,
                0,
                tzinfo=TAIPEI_TZ,
            )
    else:
        local_dt = now_local
    utc_value = local_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return utc_value, local_dt.strftime("%y%m%d")


def display_date(ts: str | None) -> str:
    """SQLite CURRENT_TIMESTAMP is UTC; display it as Asia/Taipei local time."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(ts)[:16]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS daily_sequences (
            seq_date TEXT PRIMARY KEY,
            next_no INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            customer_type TEXT NOT NULL DEFAULT 'organization',
            contact_person TEXT DEFAULT '',
            tax_id TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            category TEXT NOT NULL DEFAULT '一般',
            note TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);

        CREATE TABLE IF NOT EXISTS customer_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(customer_id, normalized_alias),
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_customer_aliases_customer ON customer_aliases(customer_id);
        CREATE INDEX IF NOT EXISTS idx_customer_aliases_alias ON customer_aliases(alias);

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            project_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '進行中',
            note TEXT DEFAULT '',
            delivery_date TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        CREATE INDEX IF NOT EXISTS idx_projects_customer ON projects(customer_id);

        CREATE TABLE IF NOT EXISTS spec_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            material TEXT DEFAULT '',
            size TEXT DEFAULT '',
            unit TEXT DEFAULT '',
            note TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_spec_presets_product ON spec_presets(product_name);

        CREATE TABLE IF NOT EXISTS finishing_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_number TEXT NOT NULL UNIQUE,
            quote_seq INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            project_id INTEGER DEFAULT NULL,
            mode TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT '報價中',
            note TEXT DEFAULT '',
            delivery_date TEXT DEFAULT NULL,
            tax_mode TEXT NOT NULL DEFAULT 'none',
            tax_rate REAL NOT NULL DEFAULT 5,
            converted_order_id INTEGER DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE INDEX IF NOT EXISTS idx_quotes_customer ON quotes(customer_id);
        CREATE INDEX IF NOT EXISTS idx_quotes_project ON quotes(project_id);
        CREATE INDEX IF NOT EXISTS idx_quotes_seq ON quotes(quote_seq);

        CREATE TABLE IF NOT EXISTS quote_work_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_id INTEGER NOT NULL,
            linked_customer_id INTEGER DEFAULT NULL,
            name TEXT NOT NULL,
            note TEXT DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE,
            FOREIGN KEY (linked_customer_id) REFERENCES customers(id)
        );

        CREATE INDEX IF NOT EXISTS idx_quote_work_units_quote ON quote_work_units(quote_id);

        CREATE TABLE IF NOT EXISTS quote_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_id INTEGER NOT NULL,
            work_unit_id INTEGER DEFAULT NULL,
            product_name TEXT NOT NULL,
            material TEXT DEFAULT '',
            size TEXT DEFAULT '',
            finishing TEXT DEFAULT '',
            quantity REAL NOT NULL DEFAULT 0,
            unit TEXT DEFAULT '',
            unit_price REAL NOT NULL DEFAULT 0,
            subtotal REAL NOT NULL DEFAULT 0,
            note TEXT DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE,
            FOREIGN KEY (work_unit_id) REFERENCES quote_work_units(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_quote_items_quote ON quote_items(quote_id);
        CREATE INDEX IF NOT EXISTS idx_quote_items_product ON quote_items(product_name);

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT NOT NULL UNIQUE,
            order_seq INTEGER NOT NULL,
            quote_id INTEGER DEFAULT NULL UNIQUE,
            customer_id INTEGER NOT NULL,
            project_id INTEGER DEFAULT NULL,
            mode TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT '設計中',
            note TEXT DEFAULT '',
            delivery_date TEXT DEFAULT NULL,
            tax_mode TEXT NOT NULL DEFAULT 'none',
            tax_rate REAL NOT NULL DEFAULT 5,
            void_reason TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (quote_id) REFERENCES quotes(id),
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
        CREATE INDEX IF NOT EXISTS idx_orders_project ON orders(project_id);
        CREATE INDEX IF NOT EXISTS idx_orders_seq ON orders(order_seq);

        CREATE TABLE IF NOT EXISTS order_work_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            source_quote_work_unit_id INTEGER DEFAULT NULL,
            linked_customer_id INTEGER DEFAULT NULL,
            name TEXT NOT NULL,
            note TEXT DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY (linked_customer_id) REFERENCES customers(id)
        );

        CREATE INDEX IF NOT EXISTS idx_order_work_units_order ON order_work_units(order_id);

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            work_unit_id INTEGER DEFAULT NULL,
            source_quote_item_id INTEGER DEFAULT NULL,
            product_name TEXT NOT NULL,
            material TEXT DEFAULT '',
            size TEXT DEFAULT '',
            finishing TEXT DEFAULT '',
            quantity REAL NOT NULL DEFAULT 0,
            unit TEXT DEFAULT '',
            unit_price REAL NOT NULL DEFAULT 0,
            subtotal REAL NOT NULL DEFAULT 0,
            note TEXT DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY (work_unit_id) REFERENCES order_work_units(id) ON DELETE SET NULL,
            FOREIGN KEY (source_quote_item_id) REFERENCES quote_items(id)
        );

        CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
        CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items(product_name);

        CREATE TABLE IF NOT EXISTS order_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            note TEXT DEFAULT '',
            paid_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_order_payments_order ON order_payments(order_id);


        -- 基礎規格：與常用規格分離
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sizes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS product_materials (
            product_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            PRIMARY KEY (product_id, material_id),
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS product_sizes (
            product_id INTEGER NOT NULL,
            size_id INTEGER NOT NULL,
            PRIMARY KEY (product_id, size_id),
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY (size_id) REFERENCES sizes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS product_units (
            product_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL,
            PRIMARY KEY (product_id, unit_id),
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS product_finishings (
            product_id INTEGER NOT NULL,
            finishing_id INTEGER NOT NULL,
            PRIMARY KEY (product_id, finishing_id),
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY (finishing_id) REFERENCES finishing_options(id) ON DELETE CASCADE
        );

        -- 常用規格：完整規格組合，只用於快速帶入
        CREATE TABLE IF NOT EXISTS quick_specs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            material_id INTEGER DEFAULT NULL,
            size_id INTEGER DEFAULT NULL,
            unit_id INTEGER DEFAULT NULL,
            default_quantity REAL DEFAULT NULL,
            default_unit_price REAL DEFAULT NULL,
            note TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (material_id) REFERENCES materials(id),
            FOREIGN KEY (size_id) REFERENCES sizes(id),
            FOREIGN KEY (unit_id) REFERENCES units(id)
        );

        CREATE TABLE IF NOT EXISTS quick_spec_finishings (
            quick_spec_id INTEGER NOT NULL,
            finishing_id INTEGER NOT NULL,
            PRIMARY KEY (quick_spec_id, finishing_id),
            FOREIGN KEY (quick_spec_id) REFERENCES quick_specs(id) ON DELETE CASCADE,
            FOREIGN KEY (finishing_id) REFERENCES finishing_options(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_quick_specs_product ON quick_specs(product_id);

        """
    )
    _ensure_finishing_seed(conn)


def _ensure_finishing_seed(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT COUNT(*) AS c FROM finishing_options")
    if cur.fetchone()["c"]:
        return
    for idx, name in enumerate(DEFAULT_FINISHING_OPTIONS):
        conn.execute(
            "INSERT INTO finishing_options (name, sort_order, is_active) VALUES (?, ?, 1)",
            (name, idx),
        )


def init_database() -> None:
    conn = connect()
    has_migrations = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if has_migrations:
        recorded_version = int(conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()["version"])
        if recorded_version > SCHEMA_VERSION:
            conn.close()
            raise RuntimeError(
                f"資料庫 schema {recorded_version} 高於本程式支援的 {SCHEMA_VERSION}，請使用新版程式。"
            )
    ensure_schema(conn)

    # V2.3 hierarchical specification model
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS spec_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS spec_product_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, name),
            FOREIGN KEY (product_id) REFERENCES spec_products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS spec_product_sizes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, name),
            FOREIGN KEY (product_id) REFERENCES spec_products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS spec_product_quantities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            value_text TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, value_text),
            FOREIGN KEY (product_id) REFERENCES spec_products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS spec_product_unit (
            product_id INTEGER PRIMARY KEY,
            unit_name TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES spec_products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS spec_finishings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS spec_quick_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            material_text TEXT DEFAULT '',
            size_text TEXT DEFAULT '',
            quantity_text TEXT DEFAULT '',
            unit_text TEXT DEFAULT '',
            unit_price REAL DEFAULT NULL,
            note TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES spec_products(id)
        );

        CREATE TABLE IF NOT EXISTS spec_quick_template_finishings (
            template_id INTEGER NOT NULL,
            finishing_name TEXT NOT NULL,
            PRIMARY KEY (template_id, finishing_name),
            FOREIGN KEY (template_id) REFERENCES spec_quick_templates(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_spec_product_materials_product ON spec_product_materials(product_id);
        CREATE INDEX IF NOT EXISTS idx_spec_product_sizes_product ON spec_product_sizes(product_id);
        CREATE INDEX IF NOT EXISTS idx_spec_product_quantities_product ON spec_product_quantities(product_id);
        CREATE INDEX IF NOT EXISTS idx_spec_quick_templates_product ON spec_quick_templates(product_id);
    """)

    for _name in ("燙金", "倒圓角", "上亮膜", "上霧膜", "流水號"):
        conn.execute(
            "INSERT OR IGNORE INTO spec_finishings(name,is_active) VALUES(?,1)",
            (_name,)
        )
    def _ensure_column(table: str, column: str, definition: str) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    _ensure_column("customers", "customer_type", "TEXT NOT NULL DEFAULT 'organization'")
    _ensure_column("quotes", "delivery_date", "TEXT DEFAULT NULL")
    _ensure_column("orders", "delivery_date", "TEXT DEFAULT NULL")
    _ensure_column("quote_work_units", "linked_customer_id", "INTEGER DEFAULT NULL")
    _ensure_column("order_work_units", "linked_customer_id", "INTEGER DEFAULT NULL")
    _ensure_column("quotes", "tax_mode", "TEXT NOT NULL DEFAULT 'none'")
    _ensure_column("quotes", "tax_rate", "REAL NOT NULL DEFAULT 5")
    _ensure_column("orders", "delivery_date", "TEXT DEFAULT NULL")
    _ensure_column("orders", "tax_mode", "TEXT NOT NULL DEFAULT 'none'")
    _ensure_column("orders", "tax_rate", "REAL NOT NULL DEFAULT 5")
    _ensure_column("orders", "void_reason", "TEXT DEFAULT ''")
    conn.execute("UPDATE orders SET status='廢單' WHERE status='已取消'")

    # V3.0 migration baseline. Existing V2.x databases are upgraded in place;
    # future versions append a numbered migration and never rebuild the DB.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version INTEGER PRIMARY KEY,
               applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    current_version = int(conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
    ).fetchone()["version"])
    if current_version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"資料庫 schema {current_version} 高於本程式支援的 {SCHEMA_VERSION}，請使用新版程式。"
        )
    if current_version < 1:
        conn.execute("INSERT INTO schema_migrations(version) VALUES (1)")
        current_version = 1
    if current_version < 2:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS customer_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                alias TEXT NOT NULL,
                normalized_alias TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(customer_id, normalized_alias),
                FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_customer_aliases_customer ON customer_aliases(customer_id);
            CREATE INDEX IF NOT EXISTS idx_customer_aliases_alias ON customer_aliases(alias);
            """
        )
        duplicate_rows = conn.execute(
            "SELECT id,name,contact_person FROM customers WHERE TRIM(contact_person)<>''"
        ).fetchall()
        for row in duplicate_rows:
            if normalize_customer_text(row["name"]) == normalize_customer_text(row["contact_person"]):
                conn.execute(
                    "UPDATE customers SET customer_type='person',contact_person='' WHERE id=?",
                    (row["id"],),
                )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (2)")

    conn.commit()
    conn.close()


def _get_or_create_daily_sequence(conn: sqlite3.Connection, seq_date: str) -> int:
    row = conn.execute(
        "SELECT next_no FROM daily_sequences WHERE seq_date = ?",
        (seq_date,),
    ).fetchone()
    used = conn.execute(
        """
        SELECT COALESCE(MAX(seq), 0) AS max_seq
        FROM (
            SELECT order_seq AS seq FROM orders WHERE substr(order_number, 1, 6) = ?
            UNION ALL
            SELECT quote_seq AS seq FROM quotes WHERE substr(quote_number, 2, 6) = ?
        )
        """,
        (seq_date, seq_date),
    ).fetchone()
    next_no = max(int(row["next_no"]) if row else 1, int(used["max_seq"] or 0) + 1)
    conn.execute(
        """INSERT INTO daily_sequences (seq_date, next_no) VALUES (?, ?)
           ON CONFLICT(seq_date) DO UPDATE SET next_no=excluded.next_no""",
        (seq_date, next_no + 1),
    )
    return next_no


def allocate_serial(conn: sqlite3.Connection, seq_date: str | None = None) -> tuple[str, int]:
    seq_date = seq_date or today_key()
    conn.execute("BEGIN IMMEDIATE")
    seq = _get_or_create_daily_sequence(conn, seq_date)
    return seq_date, seq


def quote_number_from(seq_date: str, seq: int) -> str:
    return f"Q{seq_date}{seq:02d}"


def order_number_from(seq_date: str, seq: int) -> str:
    return f"{seq_date}{seq:02d}"


def strip_quote_prefix(quote_number: str) -> str:
    return quote_number[1:] if quote_number.startswith("Q") else quote_number


def refresh_expired_quotes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE quotes
           SET status = '無下訂報價',
               updated_at = CURRENT_TIMESTAMP
         WHERE status IN ('報價中', '已確認')
           AND converted_order_id IS NULL
           AND date(created_at) <= date('now', '-30 day')
        """
    )


def update_customer_master(conn: sqlite3.Connection, customer_id: int, payload: dict[str, Any]) -> None:
    existing = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not existing:
        raise ValueError("客戶不存在。")

    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("客戶名稱不能空白。")
    customer_type = str(payload.get("customer_type") or "organization").strip()
    if customer_type not in CUSTOMER_TYPES:
        customer_type = "organization"
    contact = clean_customer_contact(customer_type, name, payload.get("contact_person"))

    old_name = str(existing["name"] or "").strip()
    old_normalized = normalize_customer_text(old_name)
    new_normalized = normalize_customer_text(name)
    if old_normalized and old_normalized != new_normalized:
        conn.execute(
            """
            INSERT OR IGNORE INTO customer_aliases(customer_id,alias,normalized_alias)
            VALUES (?,?,?)
            """,
            (customer_id, old_name, old_normalized),
        )
    if new_normalized:
        conn.execute(
            "DELETE FROM customer_aliases WHERE customer_id=? AND normalized_alias=?",
            (customer_id, new_normalized),
        )

    conn.execute(
        """
        UPDATE customers
           SET name=?, customer_type=?, contact_person=?, tax_id=?, phone=?, email=?,
               category=?, note=?, updated_at=CURRENT_TIMESTAMP
         WHERE id=?
        """,
        (
            name,
            customer_type,
            contact,
            str(payload.get("tax_id") or "").strip(),
            str(payload.get("phone") or "").strip(),
            str(payload.get("email") or "").strip(),
            str(payload.get("category") or "一般").strip() or "一般",
            str(payload.get("note") or "").strip(),
            customer_id,
        ),
    )


def ensure_customer(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    customer_id = payload.get("customer_id")
    customer_name = (payload.get("customer_name") or "").strip()
    contact = (payload.get("customer_contact") or payload.get("contact_person") or "").strip()
    tax_id = (payload.get("customer_tax_id") or payload.get("tax_id") or "").strip()
    phone = (payload.get("customer_phone") or payload.get("phone") or "").strip()

    if customer_id:
        cid = int(customer_id)
        # Transaction forms only select a customer. Master-data changes must
        # go through Customer Management so an edit cannot rename it silently.
        if not conn.execute("SELECT 1 FROM customers WHERE id=?", (cid,)).fetchone():
            raise ValueError("選取的客戶不存在。")
        return cid

    if not customer_name:
        raise ValueError("客戶名稱不能空白")

    existing = conn.execute(
        "SELECT id FROM customers WHERE name=? LIMIT 1",
        (customer_name,),
    ).fetchone()
    if existing:
        return int(existing["id"])

    customer_type = (
        "person"
        if contact and normalize_customer_text(customer_name) == normalize_customer_text(contact)
        else "organization"
    )
    contact = clean_customer_contact(customer_type, customer_name, contact)

    cur = conn.execute(
        """
        INSERT INTO customers (name, customer_type, contact_person, tax_id, phone, category)
        VALUES (?, ?, ?, ?, ?, '一般')
        """,
        (customer_name, customer_type, contact, tax_id, phone),
    )
    return int(cur.lastrowid)


def ensure_project(conn: sqlite3.Connection, payload: dict[str, Any], customer_id: int) -> int | None:
    """
    Existing project linkage is ONLY by project_id.
    A typed project_name without a selected project_id always creates a new project.
    Same-name projects are allowed and must never be auto-merged by text.
    """
    project_id = payload.get("project_id")
    project_name = (payload.get("project_name") or "").strip()
    if project_id:
        existing = conn.execute("SELECT id FROM projects WHERE id=?", (int(project_id),)).fetchone()
        if not existing:
            raise ValueError("選取的專案不存在")
        return int(project_id)
    if not project_name:
        return None
    cur = conn.execute(
        """
        INSERT INTO projects (customer_id, project_name, status, note)
        VALUES (?, ?, ?, ?)
        """,
        (customer_id, project_name, payload.get("project_status", "進行中"), payload.get("project_note", "")),
    )
    return int(cur.lastrowid)


def save_quote_structure(conn: sqlite3.Connection, quote_id: int, payload: dict[str, Any]) -> None:
    mode = payload.get("mode", "normal")
    if mode == "project":
        work_units = payload.get("work_units") or []
        for wu_sort, wu in enumerate(work_units):
            wu_name = (wu.get("name") or "").strip()
            if not wu_name:
                continue
            cur = conn.execute(
                """
                INSERT INTO quote_work_units (quote_id, linked_customer_id, name, note, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (quote_id, wu.get("linked_customer_id"), wu_name, wu.get("note", ""), wu_sort),
            )
            wu_id = int(cur.lastrowid)
            save_items(
                conn,
                table="quote_items",
                parent_id_field="quote_id",
                parent_id=quote_id,
                items=wu.get("items") or [],
                work_unit_id=wu_id,
            )
    else:
        save_items(
            conn,
            table="quote_items",
            parent_id_field="quote_id",
            parent_id=quote_id,
            items=payload.get("items") or [],
            work_unit_id=None,
        )


def save_order_structure(conn: sqlite3.Connection, order_id: int, payload: dict[str, Any]) -> None:
    mode = payload.get("mode", "normal")
    if mode == "project":
        work_units = payload.get("work_units") or []
        for wu_sort, wu in enumerate(work_units):
            wu_name = (wu.get("name") or "").strip()
            if not wu_name:
                continue
            cur = conn.execute(
                """
                INSERT INTO order_work_units (order_id, source_quote_work_unit_id, linked_customer_id, name, note, sort_order)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (order_id, wu.get("source_quote_work_unit_id"), wu.get("linked_customer_id"), wu_name, wu.get("note", ""), wu_sort),
            )
            wu_id = int(cur.lastrowid)
            save_items(
                conn,
                table="order_items",
                parent_id_field="order_id",
                parent_id=order_id,
                items=wu.get("items") or [],
                work_unit_id=wu_id,
            )
    else:
        save_items(
            conn,
            table="order_items",
            parent_id_field="order_id",
            parent_id=order_id,
            items=payload.get("items") or [],
            work_unit_id=None,
        )


def save_items(
    conn: sqlite3.Connection,
    table: str,
    parent_id_field: str,
    parent_id: int,
    items: list[dict[str, Any]],
    work_unit_id: int | None,
) -> None:
    for sort_order, item in enumerate(items):
        product_name = (item.get("product_name") or "").strip()
        if not product_name:
            continue
        qty = float(item.get("quantity") or 0)
        unit_price = float(item.get("unit_price") or 0)
        subtotal = qty * unit_price
        cols = [
            parent_id_field,
            "work_unit_id",
            "source_quote_item_id" if table == "order_items" else None,
            "product_name",
            "material",
            "size",
            "finishing",
            "quantity",
            "unit",
            "unit_price",
            "subtotal",
            "note",
            "sort_order",
        ]
        cols = [c for c in cols if c]
        vals = [
            parent_id,
            work_unit_id,
            item.get("source_quote_item_id") if table == "order_items" else None,
            product_name,
            item.get("material", ""),
            item.get("size", ""),
            item.get("finishing", ""),
            qty,
            item.get("unit", ""),
            unit_price,
            subtotal,
            item.get("note", ""),
            sort_order,
        ]
        vals = [v for v in vals if v is not None or True]
        if table == "quote_items":
            conn.execute(
                """
                INSERT INTO quote_items
                (quote_id, work_unit_id, product_name, material, size, finishing,
                 quantity, unit, unit_price, subtotal, note, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parent_id,
                    work_unit_id,
                    product_name,
                    item.get("material", ""),
                    item.get("size", ""),
                    item.get("finishing", ""),
                    qty,
                    item.get("unit", ""),
                    unit_price,
                    subtotal,
                    item.get("note", ""),
                    sort_order,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO order_items
                (order_id, work_unit_id, source_quote_item_id, product_name, material, size,
                 finishing, quantity, unit, unit_price, subtotal, note, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parent_id,
                    work_unit_id,
                    item.get("source_quote_item_id"),
                    product_name,
                    item.get("material", ""),
                    item.get("size", ""),
                    item.get("finishing", ""),
                    qty,
                    item.get("unit", ""),
                    unit_price,
                    subtotal,
                    item.get("note", ""),
                    sort_order,
                ),
            )


def payload_to_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # normal mode helper not used heavily by backend but kept for completeness
    return payload.get("items") or []


def quote_to_payload(conn: sqlite3.Connection, quote_id: int) -> dict[str, Any]:
    quote = conn.execute(
        """
        SELECT q.*, c.name AS customer_name, c.contact_person, c.tax_id, c.phone
        FROM quotes q
        JOIN customers c ON c.id = q.customer_id
        WHERE q.id = ?
        """,
        (quote_id,),
    ).fetchone()
    if not quote:
        raise ValueError("Quote not found")
    payload: dict[str, Any] = {
        "mode": quote["mode"],
        "customer_id": quote["customer_id"],
        "customer_name": quote["customer_name"],
        "customer_contact": "",
        "customer_tax_id": "",
        "customer_phone": "",
        "project_id": quote["project_id"],
        "project_name": "",
        "note": quote["note"] or "",
        "delivery_date": quote["delivery_date"] or "",
        "tax_mode": quote["tax_mode"] if "tax_mode" in quote.keys() else "none",
        "tax_rate": quote["tax_rate"] if "tax_rate" in quote.keys() else 5,
        "status": quote["status"] or "報價中",
        "project_status": "進行中",
    }
    payload["customer_contact"] = quote["contact_person"] or ""
    payload["customer_tax_id"] = quote["tax_id"] or ""
    payload["customer_phone"] = quote["phone"] or ""
    if quote["project_id"]:
        project = conn.execute("SELECT project_name FROM projects WHERE id = ?", (quote["project_id"],)).fetchone()
        payload["project_name"] = project["project_name"] if project else ""
    if quote["mode"] == "project":
        work_units = conn.execute(
            "SELECT * FROM quote_work_units WHERE quote_id = ? ORDER BY sort_order, id",
            (quote_id,),
        ).fetchall()
        wu_payload = []
        for wu in work_units:
            items = conn.execute(
                "SELECT * FROM quote_items WHERE quote_id = ? AND work_unit_id = ? ORDER BY sort_order, id",
                (quote_id, wu["id"]),
            ).fetchall()
            wu_payload.append(
                {
                    "id": wu["id"],
                    "name": wu["name"],
                    "linked_customer_id": wu["linked_customer_id"] if "linked_customer_id" in wu.keys() else None,
                    "note": wu["note"] or "",
                    "items": [dict(i) for i in items],
                }
            )
        payload["work_units"] = wu_payload
    else:
        items = conn.execute(
            "SELECT * FROM quote_items WHERE quote_id = ? ORDER BY sort_order, id",
            (quote_id,),
        ).fetchall()
        payload["items"] = [dict(i) for i in items]
    return payload


def order_to_payload(conn: sqlite3.Connection, order_id: int) -> dict[str, Any]:
    order = conn.execute(
        """
        SELECT o.*, c.name AS customer_name, c.contact_person, c.tax_id, c.phone
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        WHERE o.id = ?
        """,
        (order_id,),
    ).fetchone()
    if not order:
        raise ValueError("Order not found")
    payload: dict[str, Any] = {
        "mode": order["mode"],
        "customer_id": order["customer_id"],
        "customer_name": order["customer_name"],
        "customer_contact": order["contact_person"] or "",
        "customer_tax_id": order["tax_id"] or "",
        "customer_phone": order["phone"] or "",
        "project_id": order["project_id"],
        "project_name": "",
        "note": order["note"] or "",
        "delivery_date": order["delivery_date"] or "",
        "tax_mode": order["tax_mode"] if "tax_mode" in order.keys() else "none",
        "tax_rate": order["tax_rate"] if "tax_rate" in order.keys() else 5,
        "status": order["status"] or "設計中",
        "project_status": "進行中",
    }
    if order["project_id"]:
        project = conn.execute("SELECT project_name FROM projects WHERE id = ?", (order["project_id"],)).fetchone()
        payload["project_name"] = project["project_name"] if project else ""
    if order["mode"] == "project":
        work_units = conn.execute(
            "SELECT * FROM order_work_units WHERE order_id = ? ORDER BY sort_order, id",
            (order_id,),
        ).fetchall()
        wu_payload = []
        for wu in work_units:
            items = conn.execute(
                "SELECT * FROM order_items WHERE order_id = ? AND work_unit_id = ? ORDER BY sort_order, id",
                (order_id, wu["id"]),
            ).fetchall()
            wu_payload.append(
                {
                    "id": wu["id"],
                    "name": wu["name"],
                    "linked_customer_id": wu["linked_customer_id"] if "linked_customer_id" in wu.keys() else None,
                    "note": wu["note"] or "",
                    "items": [dict(i) for i in items],
                }
            )
        payload["work_units"] = wu_payload
    else:
        items = conn.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY sort_order, id",
            (order_id,),
        ).fetchall()
        payload["items"] = [dict(i) for i in items]
    return payload


def create_quote_from_payload(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    seq_date, seq = allocate_serial(conn)
    customer_id = ensure_customer(conn, payload)
    project_id = None
    if (payload.get("mode") == "project") or payload.get("project_id") or (payload.get("project_name") or "").strip():
        project_id = ensure_project(conn, payload, customer_id)
    quote_number = quote_number_from(seq_date, seq)
    mode = payload.get("mode", "normal")
    cur = conn.execute(
        """
        INSERT INTO quotes
        (quote_number, quote_seq, customer_id, project_id, mode, status, note, delivery_date, tax_mode, tax_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            quote_number,
            seq,
            customer_id,
            project_id,
            mode,
            payload.get("status", "報價中"),
            payload.get("note", ""),
            (payload.get("delivery_date") or None),
            payload.get("tax_mode", "none"),
            5.0,
        ),
    )
    quote_id = int(cur.lastrowid)
    save_quote_structure(conn, quote_id, payload)
    return quote_id



def update_quote_from_payload(conn: sqlite3.Connection, quote_id: int, payload: dict[str, Any]) -> None:
    quote = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    if not quote:
        raise ValueError("Quote not found")
    if quote["converted_order_id"]:
        raise ValueError("此報價已轉成訂單，請至訂單修改。")

    customer_id = ensure_customer(conn, payload)

    mode = payload.get("mode", "normal")
    project_id = None
    if mode == "project":
        existing_project_id = payload.get("project_id") or quote["project_id"]
        if existing_project_id:
            project_id = int(existing_project_id)
        else:
            project_id = ensure_project(conn, payload, customer_id)

    conn.execute(
        """
        UPDATE quotes
        SET customer_id=?,
            project_id=?,
            mode=?,
            note=?,
            delivery_date=?,
            tax_mode=?,
            tax_rate=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (
            customer_id,
            project_id,
            mode,
            payload.get("note", ""),
            payload.get("delivery_date") or None,
            payload.get("tax_mode", "none"),
            5.0,
            quote_id,
        ),
    )

    # Rebuild quote detail structure only; quote number/status/created_at stay unchanged.
    conn.execute("DELETE FROM quote_items WHERE quote_id=?", (quote_id,))
    conn.execute("DELETE FROM quote_work_units WHERE quote_id=?", (quote_id,))
    save_quote_structure(conn, quote_id, payload)

def create_order_from_payload(conn: sqlite3.Connection, payload: dict[str, Any], source_quote_id: int | None = None) -> int:
    created_at, selected_date = normalize_order_created_at(
        payload.get("created_date") or payload.get("created_at_local")
    )
    seq_date, seq = allocate_serial(conn, selected_date)
    customer_id = ensure_customer(conn, payload)
    project_id = None
    if (payload.get("mode") == "project") or payload.get("project_id") or (payload.get("project_name") or "").strip():
        project_id = ensure_project(conn, payload, customer_id)
    order_number = order_number_from(seq_date, seq)
    mode = payload.get("mode", "normal")
    cur = conn.execute(
        """
        INSERT INTO orders
        (order_number, order_seq, quote_id, customer_id, project_id, mode, status, note, delivery_date, tax_mode, tax_rate, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_number,
            seq,
            source_quote_id,
            customer_id,
            project_id,
            mode,
            payload.get("status", "設計中"),
            payload.get("note", ""),
            (payload.get("delivery_date") or None),
            payload.get("tax_mode", "none"),
            5.0,
            created_at,
        ),
    )
    order_id = int(cur.lastrowid)
    save_order_structure(conn, order_id, payload)
    if source_quote_id:
        conn.execute(
            "UPDATE quotes SET converted_order_id = ?, status = '已轉訂單', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (order_id, source_quote_id),
        )
    return order_id



def update_order_from_payload(conn: sqlite3.Connection, order_id: int, payload: dict[str, Any]) -> None:
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        raise ValueError("Order not found")
    if order["status"] == "廢單":
        raise ValueError("廢單不可直接修改內容。")

    customer_id = ensure_customer(conn, payload)
    mode = payload.get("mode", "normal")
    project_id = None
    if mode == "project":
        existing_project_id = payload.get("project_id") or order["project_id"]
        if existing_project_id:
            project_id = int(existing_project_id)
        else:
            project_id = ensure_project(conn, payload, customer_id)

    status = payload.get("status", order["status"])
    if status not in ORDER_STATUSES:
        status = order["status"]

    conn.execute(
        """
        UPDATE orders
        SET customer_id=?, project_id=?, mode=?, status=?, note=?, delivery_date=?,
            tax_mode=?, tax_rate=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (
            customer_id, project_id, mode, status, payload.get("note", ""),
            payload.get("delivery_date") or None,
            payload.get("tax_mode", "none"), 5.0,
            order_id,
        ),
    )
    conn.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
    conn.execute("DELETE FROM order_work_units WHERE order_id=?", (order_id,))
    save_order_structure(conn, order_id, payload)


def convert_quote_to_order(conn: sqlite3.Connection, quote_id: int) -> int:
    payload = quote_to_payload(conn, quote_id)
    # 轉訂單時，編號沿用報價號碼，僅去掉 Q
    quote_row = conn.execute(
        "SELECT * FROM quotes WHERE id = ?",
        (quote_id,),
    ).fetchone()
    if not quote_row:
        raise ValueError("Quote not found")
    existing = conn.execute(
        "SELECT id FROM orders WHERE quote_id = ? LIMIT 1",
        (quote_id,),
    ).fetchone()
    if existing:
        return int(existing["id"])
    # Create an order with same seq; do not allocate a new daily sequence.
    order_number = strip_quote_prefix(quote_row["quote_number"])
    cur = conn.execute(
        """
        INSERT INTO orders
        (order_number, order_seq, quote_id, customer_id, project_id, mode, status, note, delivery_date, tax_mode, tax_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_number,
            quote_row["quote_seq"],
            quote_id,
            quote_row["customer_id"],
            quote_row["project_id"],
            quote_row["mode"],
            "設計中",
            quote_row["note"] or "",
            quote_row["delivery_date"] if "delivery_date" in quote_row.keys() else None,
            quote_row["tax_mode"] if "tax_mode" in quote_row.keys() else "none",
            5.0,
        ),
    )
    order_id = int(cur.lastrowid)
    # copy structure without renumbering
    if quote_row["mode"] == "project":
        work_units = conn.execute(
            "SELECT * FROM quote_work_units WHERE quote_id = ? ORDER BY sort_order, id",
            (quote_id,),
        ).fetchall()
        wu_map: dict[int, int] = {}
        for wu in work_units:
            cwu = conn.execute(
                """
                INSERT INTO order_work_units
                (order_id, source_quote_work_unit_id, linked_customer_id, name, note, sort_order)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (order_id, wu["id"], wu["linked_customer_id"] if "linked_customer_id" in wu.keys() else None, wu["name"], wu["note"] or "", wu["sort_order"]),
            )
            wu_map[int(wu["id"])] = int(cwu.lastrowid)
        items = conn.execute(
            "SELECT * FROM quote_items WHERE quote_id = ? ORDER BY sort_order, id",
            (quote_id,),
        ).fetchall()
        for item in items:
            conn.execute(
                """
                INSERT INTO order_items
                (order_id, work_unit_id, source_quote_item_id, product_name, material, size,
                 finishing, quantity, unit, unit_price, subtotal, note, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    wu_map.get(item["work_unit_id"]) if item["work_unit_id"] else None,
                    item["id"],
                    item["product_name"],
                    item["material"] or "",
                    item["size"] or "",
                    item["finishing"] or "",
                    item["quantity"] or 0,
                    item["unit"] or "",
                    item["unit_price"] or 0,
                    item["subtotal"] or 0,
                    item["note"] or "",
                    item["sort_order"],
                ),
            )
    else:
        items = conn.execute(
            "SELECT * FROM quote_items WHERE quote_id = ? ORDER BY sort_order, id",
            (quote_id,),
        ).fetchall()
        for item in items:
            conn.execute(
                """
                INSERT INTO order_items
                (order_id, work_unit_id, source_quote_item_id, product_name, material, size,
                 finishing, quantity, unit, unit_price, subtotal, note, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    None,
                    item["id"],
                    item["product_name"],
                    item["material"] or "",
                    item["size"] or "",
                    item["finishing"] or "",
                    item["quantity"] or 0,
                    item["unit"] or "",
                    item["unit_price"] or 0,
                    item["subtotal"] or 0,
                    item["note"] or "",
                    item["sort_order"],
                ),
            )
    conn.execute(
        "UPDATE quotes SET converted_order_id = ?, status = '已轉訂單', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (order_id, quote_id),
    )
    return order_id


def search_customers(conn: sqlite3.Connection, q: str, limit: int = 10) -> list[dict[str, Any]]:
    like = f"%{q}%"
    rows = conn.execute(
        """
        SELECT id, name, customer_type, contact_person, tax_id, phone, email, category
        FROM customers c
        WHERE is_active = 1
          AND (name LIKE ? OR contact_person LIKE ? OR phone LIKE ? OR tax_id LIKE ? OR email LIKE ?
               OR EXISTS (SELECT 1 FROM customer_aliases ca WHERE ca.customer_id=c.id AND ca.alias LIKE ?))
        ORDER BY CASE WHEN name LIKE ? THEN 0 ELSE 1 END, name
        LIMIT ?
        """,
        (like, like, like, like, like, like, f"{q}%", limit),
    ).fetchall()
    result = rows_dict(rows)
    for row in result:
        row["contact_person"] = display_customer_contact(row["name"], row["contact_person"])
    return result


def search_projects(conn: sqlite3.Connection, q: str, customer_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
    like = f"%{q}%"
    sql = """
        SELECT p.id, p.project_name, p.status, p.customer_id, c.name AS customer_name
        FROM projects p
        JOIN customers c ON c.id = p.customer_id
        WHERE p.project_name LIKE ?
    """
    params: list[Any] = [like]
    if customer_id:
        sql += " AND p.customer_id = ?"
        params.append(customer_id)
    sql += " ORDER BY p.id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return rows_dict(rows)


def search_spec_presets(conn: sqlite3.Connection, q: str, limit: int = 10) -> list[dict[str, Any]]:
    like = f"%{q}%"
    rows = conn.execute(
        """
        SELECT *
        FROM spec_presets
        WHERE is_active = 1
          AND (product_name LIKE ? OR material LIKE ? OR size LIKE ? OR unit LIKE ? OR note LIKE ?)
        ORDER BY CASE WHEN product_name LIKE ? THEN 0 ELSE 1 END, product_name, id DESC
        LIMIT ?
        """,
        (like, like, like, like, like, f"{q}%", limit),
    ).fetchall()
    return rows_dict(rows)


def search_order_items(conn: sqlite3.Connection, q: str, limit: int = 20) -> list[dict[str, Any]]:
    like = f"%{q}%"
    rows = conn.execute(
        """
        SELECT
            oi.id,
            oi.product_name,
            oi.material,
            oi.size,
            oi.finishing,
            oi.quantity,
            oi.unit,
            oi.unit_price,
            oi.subtotal,
            oi.note,
            o.order_number,
            o.created_at,
            c.name AS customer_name,
            p.project_name,
            ow.name AS work_unit_name
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        JOIN customers c ON c.id = o.customer_id
        LEFT JOIN projects p ON p.id = o.project_id
        LEFT JOIN order_work_units ow ON ow.id = oi.work_unit_id
        WHERE oi.product_name LIKE ?
           OR oi.material LIKE ?
           OR oi.size LIKE ?
           OR oi.finishing LIKE ?
           OR c.name LIKE ?
           OR COALESCE(p.project_name, '') LIKE ?
           OR COALESCE(ow.name, '') LIKE ?
        ORDER BY o.created_at DESC, o.id DESC, oi.id DESC
        LIMIT ?
        """,
        (like, like, like, like, like, like, like, limit),
    ).fetchall()
    return rows_dict(rows)


def list_finishing_options(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM finishing_options WHERE is_active = 1 ORDER BY sort_order, name"
    ).fetchall()
    return rows_dict(rows)


def count_tables(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "customers", "projects", "spec_presets", "finishing_options",
        "quotes", "quote_work_units", "quote_items",
        "orders", "order_work_units", "order_items",
    ]
    result = {}
    for t in tables:
        result[t] = int(conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"])
    return result
