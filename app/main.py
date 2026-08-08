from flask import Flask, render_template, request, redirect, url_for
from database import init_database, get_connection

app = Flask(__name__)

# 啟動 Flask 時確保 SQLite 資料庫已初始化
init_database()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/customers")
def customers():
    connection = get_connection()
    rows = connection.execute("""
        SELECT id, company_name, contact_person, tax_id, phone, category
        FROM customers
        WHERE is_active = 1
        ORDER BY company_name COLLATE NOCASE
    """).fetchall()
    connection.close()

    return render_template("customers/list.html", customers=rows)

@app.route("/customers/new", methods=["GET", "POST"])
def new_customer():
    if request.method == "POST":
        company_name = request.form.get("company_name", "").strip()
        contact_person = request.form.get("contact_person", "").strip()
        tax_id = request.form.get("tax_id", "").strip()
        phone = request.form.get("phone", "").strip()
        category = request.form.get("category", "一般").strip()

        if not company_name:
            return "客戶名稱為必填欄位", 400

        connection = get_connection()

        connection.execute(
            """
            INSERT INTO customers
            (company_name, contact_person, tax_id, phone, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                company_name,
                contact_person,
                tax_id,
                phone,
                category
            )
        )

        connection.commit()
        connection.close()

        return redirect(url_for("customers"))

    return render_template("customers/form.html")

@app.route("/db-test")
def db_test():
    return {
        "status": "ok",
        "database": "SQLite",
        "message": "資料庫連線正常"
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
