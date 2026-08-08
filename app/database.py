import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_DIR = BASE_DIR / "database"
DATABASE_DIR.mkdir(exist_ok=True)

DATABASE_PATH = DATABASE_DIR / "printshop.db"


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_database():
    connection = get_connection()

    connection.executescript("""
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT NOT NULL,
        contact_person TEXT DEFAULT '',
        tax_id TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        category TEXT NOT NULL DEFAULT '一般',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS product_specs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        material TEXT NOT NULL DEFAULT '',
        size TEXT NOT NULL DEFAULT '',
        finishing TEXT NOT NULL DEFAULT '',
        is_favorite INTEGER NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id)
    );

    CREATE TABLE IF NOT EXISTS spec_quantity_tiers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        spec_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        unit TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (spec_id) REFERENCES product_specs(id)
    );

    CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_number TEXT NOT NULL UNIQUE,
        customer_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT '報價中',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    );

    CREATE TABLE IF NOT EXISTS quote_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        material TEXT NOT NULL DEFAULT '',
        size TEXT NOT NULL DEFAULT '',
        finishing TEXT NOT NULL DEFAULT '',
        quantity REAL NOT NULL DEFAULT 0,
        unit TEXT NOT NULL DEFAULT '',
        unit_price REAL NOT NULL DEFAULT 0,
        subtotal REAL NOT NULL DEFAULT 0,
        FOREIGN KEY (quote_id) REFERENCES quotes(id)
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT NOT NULL UNIQUE,
        quote_id INTEGER,
        customer_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT '設計中',
        is_void INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (quote_id) REFERENCES quotes(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    );

    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        material TEXT NOT NULL DEFAULT '',
        size TEXT NOT NULL DEFAULT '',
        finishing TEXT NOT NULL DEFAULT '',
        quantity REAL NOT NULL DEFAULT 0,
        unit TEXT NOT NULL DEFAULT '',
        unit_price REAL NOT NULL DEFAULT 0,
        subtotal REAL NOT NULL DEFAULT 0,
        FOREIGN KEY (order_id) REFERENCES orders(id)
    );

    CREATE INDEX IF NOT EXISTS idx_customers_company_name
        ON customers(company_name);

    CREATE INDEX IF NOT EXISTS idx_products_name
        ON products(name);

    CREATE INDEX IF NOT EXISTS idx_product_specs_product_id
        ON product_specs(product_id);

    CREATE INDEX IF NOT EXISTS idx_quote_items_spec
        ON quote_items(product_name, material, size, finishing, quantity);

    CREATE INDEX IF NOT EXISTS idx_order_items_spec
        ON order_items(product_name, material, size, finishing, quantity);

    CREATE INDEX IF NOT EXISTS idx_orders_customer_id
        ON orders(customer_id);

    CREATE INDEX IF NOT EXISTS idx_quotes_customer_id
        ON quotes(customer_id);
    """)

    connection.commit()
    connection.close()


if __name__ == "__main__":
    init_database()
    print(f"SQLite database initialized: {DATABASE_PATH}")
