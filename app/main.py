from flask import Flask, render_template
from database import init_database

app = Flask(__name__)

# 啟動 Flask 時確保 SQLite 資料庫已初始化
init_database()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/db-test")
def db_test():
    return {
        "status": "ok",
        "database": "SQLite",
        "message": "資料庫連線正常"
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
