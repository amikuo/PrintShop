from datetime import datetime, timedelta
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import backup, database


class BackupTests(unittest.TestCase):
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

    def tearDown(self):
        database.DB_DIR = self.old_db_dir
        database.DB_PATH = self.old_db_path
        database.BACKUP_DIR = self.old_backup_dir
        self.temp.cleanup()

    def _customer_names(self):
        conn = database.connect()
        try:
            return [row[0] for row in conn.execute("SELECT name FROM customers ORDER BY id")]
        finally:
            conn.close()

    def test_backup_and_full_restore(self):
        conn = database.connect()
        conn.execute("INSERT INTO customers(name) VALUES ('備份前客戶')")
        conn.commit()
        conn.close()
        saved = backup.create_backup()

        conn = database.connect()
        conn.execute("DELETE FROM customers")
        conn.execute("INSERT INTO customers(name) VALUES ('還原前現況')")
        conn.commit()
        conn.close()

        safety = backup.restore_backup(saved.name)
        self.assertEqual(self._customer_names(), ["備份前客戶"])
        self.assertTrue(safety.exists())
        safety_conn = sqlite3.connect(safety)
        self.assertEqual(safety_conn.execute("SELECT name FROM customers").fetchone()[0], "還原前現況")
        safety_conn.close()

    def test_schema_version_and_safe_name(self):
        conn = database.connect()
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        conn.close()
        self.assertEqual(version, database.SCHEMA_VERSION)
        with self.assertRaises(ValueError):
            backup.resolve_backup("../printshop_20260817_120000.db")

    def test_v2_database_is_migrated_in_place(self):
        conn = database.connect()
        conn.execute("INSERT INTO customers(name) VALUES ('V2.9.3 原有客戶')")
        conn.execute("DROP TABLE schema_migrations")
        conn.commit()
        conn.close()

        database.init_database()
        self.assertEqual(self._customer_names(), ["V2.9.3 原有客戶"])
        conn = database.connect()
        self.assertEqual(
            conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
            database.SCHEMA_VERSION,
        )
        conn.close()

    def test_automatic_backup_retention(self):
        database.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        current = datetime(2026, 8, 17, 9, 0, 0)
        for offset in range(35):
            day = (current - timedelta(days=offset + 1)).strftime("%Y%m%d")
            (database.BACKUP_DIR / f"printshop_daily_{day}_090000.db").touch()
        for offset in range(15):
            month_index = current.year * 12 + current.month - 1 - offset
            year, zero_based_month = divmod(month_index, 12)
            month = zero_based_month + 1
            (database.BACKUP_DIR / f"printshop_monthly_{year:04d}{month:02d}01_090000.db").touch()

        manual = backup.create_backup()
        safety = backup.create_backup(safety=True)
        backup.ensure_automatic_backups(now=current, force_check=True)

        self.assertEqual(len(list(database.BACKUP_DIR.glob("printshop_daily_*.db"))), 30)
        self.assertEqual(len(list(database.BACKUP_DIR.glob("printshop_monthly_*.db"))), 12)
        self.assertTrue(manual.exists())
        self.assertTrue(safety.exists())

    def test_backup_page_create_download_and_confirmation(self):
        from app.main import app

        app.config.update(TESTING=True)
        client = app.test_client()
        self.assertEqual(client.get("/admin/backups").status_code, 200)
        self.assertEqual(client.post("/admin/backups/create").status_code, 302)
        saved = backup.list_backups()[0]["name"]
        response = client.get(f"/admin/backups/{saved}/download")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        response.close()
        response = client.post(
            f"/admin/backups/{saved}/restore",
            data={"confirmation": "不是確認文字"},
        )
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
