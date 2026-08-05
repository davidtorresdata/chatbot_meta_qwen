"""Inspect the conversation registry (SQLite backend).

Examples
--------
    python scripts/registry_cli.py                # last 10 records
    python scripts/registry_cli.py --limit 50     # last 50 records
    python scripts/registry_cli.py --path data/conversation_log.sqlite3
    python scripts/registry_cli.py --export report.csv
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def list_records(db_path: str, limit: int) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(conversations)")]
        if not cols:
            print(f"No 'conversations' table in {db_path} (nothing logged yet).")
            return []
        query = f"SELECT * FROM conversations ORDER BY rowid DESC LIMIT ?"
        return conn.execute(query, (limit,)).fetchall()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="View the conversation registry (SQLite).")
    parser.add_argument(
        "--path",
        default=str(PROJECT_ROOT / "data" / "conversation_log.sqlite3"),
        help="Path to the SQLite database file",
    )
    parser.add_argument("--limit", type=int, default=10, help="Max rows to show (default 10)")
    parser.add_argument(
        "--export",
        metavar="CSV",
        help="Also write all records to a CSV file",
    )
    args = parser.parse_args()

    if not Path(args.path).is_file():
        print(f"Database not found: {args.path}")
        print("Records appear here once CONVERSATION_LOG_ENABLED=1 and a message is handled.")
        return 1

    records = list_records(args.path, args.limit)
    if not records:
        return 0

    headers = records[0].keys()
    rows = [tuple(r) for r in records]

    if args.export:
        with open(args.export, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            writer.writerows(rows)
        print(f"Exported {len(rows)} records to {args.export}")

    width = max(len(h) for h in headers)
    print(" | ".join(h.ljust(width) for h in headers))
    print("-+-".join("-" * width for _ in headers))
    for row in rows:
        print(" | ".join((str(c) if c is not None else "").ljust(width) for c in row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
