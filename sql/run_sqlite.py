"""T1-3 runner: load the sampled CSV into SQLite (stdlib only) and run sql/queries.sql.

Usage (from the repo root):
    python sql/run_sqlite.py

- Reads outputs/taobao_clean_sample.csv (1,009,774 rows, 10,053 users).
- Builds an in-memory SQLite table `sample` with the CSV columns.
- Splits sql/queries.sql on statement boundaries (full-line `--` comments
  stripped first; no semicolons appear inside string literals) and prints
  each query's rows as a tab-separated table.
- No third-party dependency, no network, no fabricated numbers.
"""
import csv
import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SAMPLE_FILE = BASE / "outputs" / "taobao_clean_sample.csv"
QUERIES_FILE = BASE / "sql" / "queries.sql"

COLUMNS = ["user_id", "item_id", "category_id", "behavior_type",
           "timestamp", "datetime", "date", "hour"]


def load_sample(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE sample ("
        "user_id INTEGER, item_id INTEGER, category_id INTEGER, "
        "behavior_type TEXT, timestamp INTEGER, datetime TEXT, "
        "date TEXT, hour INTEGER)"
    )
    with open(SAMPLE_FILE, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == COLUMNS, f"unexpected columns: {reader.fieldnames}"
        rows = [
            (int(r["user_id"]), int(r["item_id"]), int(r["category_id"]),
             r["behavior_type"], int(r["timestamp"]), r["datetime"],
             r["date"], int(r["hour"]))
            for r in reader
        ]
    conn.executemany(
        "INSERT INTO sample VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.execute("CREATE INDEX idx_sample_user ON sample(user_id)")
    conn.execute("CREATE INDEX idx_sample_behavior ON sample(behavior_type)")
    conn.execute("CREATE INDEX idx_sample_date ON sample(date)")
    conn.commit()
    return len(rows)


def split_queries(text: str) -> list[tuple[str, str]]:
    """Return [(title, sql)] using the `-- Qn：...` comment lines as titles."""
    lines = [ln for ln in text.split("\n")
             if not ln.strip().startswith("--") or ln.strip().startswith("-- Q")]
    cleaned = "\n".join(lines)
    parts = [p.strip() for p in cleaned.split(";") if p.strip()]
    out = []
    for p in parts:
        title = "query"
        first = p.split("\n", 1)[0].strip()
        if first.startswith("-- Q"):
            title = first.lstrip("- ").strip()
            p = p.split("\n", 1)[1].strip()
        out.append((title, p))
    return out


def main() -> None:
    t0 = time.time()
    conn = sqlite3.connect(":memory:")
    n = load_sample(conn)
    print(f"载入样本：{n:,} 行，用时 {time.time() - t0:.1f}s")
    queries = split_queries(QUERIES_FILE.read_text(encoding="utf-8"))
    print(f"共 {len(queries)} 条查询\n")
    for title, sql in queries:
        print(f"--- {title} ---")
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        print("\t".join(cols))
        for row in cur.fetchall():
            print("\t".join("" if v is None else str(v) for v in row))
        print()
    print(f"总耗时：{time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
