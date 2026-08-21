from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sqlite3
from threading import Lock

from . import database

DAILY_RETENTION = 30
MONTHLY_RETENTION = 12

_BACKUP_NAME = re.compile(
    r"^printshop_(?:(?P<kind>safety|daily|monthly)_)?\d{8}_\d{6}(?:_\d+)?\.db$"
)
_operation_lock = Lock()
_last_automatic_date: str | None = None


def _unique_path(prefix: str) -> Path:
    database.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = database.BACKUP_DIR / f"{prefix}_{stamp}.db"
    suffix = 1
    while candidate.exists():
        candidate = database.BACKUP_DIR / f"{prefix}_{stamp}_{suffix}.db"
        suffix += 1
    return candidate


def _create_backup(*, kind: str = "manual") -> Path:
    database.init_database()
    prefixes = {
        "manual": "printshop",
        "safety": "printshop_safety",
        "daily": "printshop_daily",
        "monthly": "printshop_monthly",
    }
    if kind not in prefixes:
        raise ValueError("無效的備份類型。")
    destination = _unique_path(prefixes[kind])
    source = sqlite3.connect(database.DB_PATH)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("備份完整性檢查失敗。")
    except Exception:
        target.close()
        destination.unlink(missing_ok=True)
        raise
    finally:
        if source:
            source.close()
        try:
            target.close()
        except Exception:
            pass
    return destination


def create_backup(*, safety: bool = False) -> Path:
    """Use SQLite's online backup API to create a consistent snapshot."""
    with _operation_lock:
        return _create_backup(kind="safety" if safety else "manual")


def _prune_automatic_backups() -> dict[str, int]:
    removed = {"daily": 0, "monthly": 0}
    policies = {"daily": DAILY_RETENTION, "monthly": MONTHLY_RETENTION}
    for kind, keep in policies.items():
        paths = sorted(
            database.BACKUP_DIR.glob(f"printshop_{kind}_*.db"),
            key=lambda path: path.name,
            reverse=True,
        )
        for path in paths[keep:]:
            path.unlink()
            removed[kind] += 1
    return removed


def ensure_automatic_backups(*, now: datetime | None = None, force_check: bool = False) -> list[Path]:
    """Create at most one daily and one monthly snapshot, then apply retention."""
    global _last_automatic_date
    current = now or datetime.now()
    day_key = current.strftime("%Y%m%d")
    month_key = current.strftime("%Y%m")
    if not force_check and _last_automatic_date == day_key:
        return []

    created: list[Path] = []
    with _operation_lock:
        database.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        if not any(database.BACKUP_DIR.glob(f"printshop_daily_{day_key}_*.db")):
            created.append(_create_backup(kind="daily"))
        if not any(database.BACKUP_DIR.glob(f"printshop_monthly_{month_key}??_*.db")):
            created.append(_create_backup(kind="monthly"))
        _prune_automatic_backups()
        _last_automatic_date = day_key
    return created


def list_backups() -> list[dict[str, object]]:
    database.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for path in database.BACKUP_DIR.glob("printshop_*.db"):
        match = _BACKUP_NAME.fullmatch(path.name)
        if not match:
            continue
        kind = match.group("kind") or "manual"
        labels = {
            "manual": "手動備份",
            "safety": "還原前保命備份",
            "daily": "每日自動備份",
            "monthly": "每月封存備份",
        }
        stat = path.stat()
        result.append({
            "name": path.name,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime),
            "is_safety": path.name.startswith("printshop_safety_"),
            "kind": kind,
            "type_label": labels[kind],
        })
    return sorted(result, key=lambda item: item["modified"], reverse=True)


def resolve_backup(name: str) -> Path:
    if not _BACKUP_NAME.fullmatch(name):
        raise ValueError("無效的備份檔名。")
    path = (database.BACKUP_DIR / name).resolve()
    if path.parent != database.BACKUP_DIR.resolve() or not path.is_file():
        raise FileNotFoundError(name)
    return path


def restore_backup(name: str) -> Path:
    """Fully replace the live DB after a safety backup; rows are never merged."""
    with _operation_lock:
        selected = resolve_backup(name)
        safety = _create_backup(kind="safety")
        restore_file = database.DB_PATH.with_suffix(".restore.tmp")
        restore_file.unlink(missing_ok=True)
        source = sqlite3.connect(selected)
        try:
            if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("選取的備份檔已損壞。")
            has_versions = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if has_versions:
                version = int(source.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0])
                if version > database.SCHEMA_VERSION:
                    raise RuntimeError(
                        f"備份 schema {version} 高於本程式支援的 {database.SCHEMA_VERSION}。"
                    )
            target = sqlite3.connect(restore_file)
            try:
                source.backup(target)
            finally:
                target.close()
        except Exception:
            restore_file.unlink(missing_ok=True)
            raise
        finally:
            source.close()
        restore_file.replace(database.DB_PATH)
        database.init_database()
        return safety
