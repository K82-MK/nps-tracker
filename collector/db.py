"""SQLite 누적 저장소.

원천은 data/raw/krx/*.json.gz (저장소에 커밋, 영구 보관)이고,
SQLite(db/nps.sqlite)는 Actions 캐시로 유지되는 작업용 DB다.
캐시가 없어지면 원천 파일에서 자동으로 다시 만든다(ingest_all).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import DB_PATH, RAW_KRX
from .util import log, read_json_gz

SCHEMA = """
CREATE TABLE IF NOT EXISTS days(
  date TEXT PRIMARY KEY, kospi REAL, kosdaq REAL, source TEXT);
CREATE TABLE IF NOT EXISTS flows(
  date TEXT, market TEXT, investor TEXT, buy INTEGER, sell INTEGER, net INTEGER,
  PRIMARY KEY(date, market, investor));
CREATE TABLE IF NOT EXISTS pension(
  date TEXT, code TEXT, name TEXT, market TEXT, buy INTEGER, sell INTEGER, net INTEGER,
  PRIMARY KEY(date, code));
CREATE INDEX IF NOT EXISTS ix_pension_code ON pension(code, date);
CREATE TABLE IF NOT EXISTS prices(
  date TEXT, code TEXT, market TEXT, close INTEGER, mcap INTEGER,
  PRIMARY KEY(date, code));
CREATE INDEX IF NOT EXISTS ix_prices_code ON prices(code, date);
CREATE TABLE IF NOT EXISTS revenue(
  code TEXT, year INTEGER, amount INTEGER, account TEXT, fs TEXT,
  PRIMARY KEY(code, year));
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def upsert_day(con: sqlite3.Connection, raw: dict) -> None:
    d = raw["date"]
    with con:
        for t in ("flows", "pension", "prices"):
            con.execute(f"DELETE FROM {t} WHERE date=?", (d,))
        con.execute("INSERT OR REPLACE INTO days VALUES(?,?,?,?)",
                    (d, raw["index"].get("KOSPI"), raw["index"].get("KOSDAQ"),
                     ",".join(f"{k}={v}" for k, v in raw.get("source", {}).items())))
        con.executemany("INSERT INTO flows VALUES(?,?,?,?,?,?)",
                        [(d, m, inv, v["buy"], v["sell"], v["net"])
                         for m, invs in raw["flows"].items() for inv, v in invs.items()])
        con.executemany("INSERT OR REPLACE INTO pension VALUES(?,?,?,?,?,?,?)",
                        [(d, *r) for r in raw["pension"]])
        con.executemany("INSERT OR REPLACE INTO prices VALUES(?,?,?,?,?)",
                        [(d, code, m, v[0], v[1])
                         for m, px in raw["prices"].items() for code, v in px.items()])


def ingest_all(con: sqlite3.Connection, raw_dir: Path = RAW_KRX, force: bool = False) -> int:
    """원천 파일 중 DB에 없는 날짜(또는 force)만 적재."""
    have = {r[0] for r in con.execute("SELECT date FROM days")}
    n = 0
    for f in sorted(raw_dir.glob("*.json.gz")):
        s = f.name[:8]
        d = f"{s[:4]}-{s[4:6]}-{s[6:]}"
        if d in have and not force:
            continue
        upsert_day(con, read_json_gz(f))
        n += 1
    if n:
        log.info("DB 적재: %d일", n)
    return n


def business_days(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT date FROM days ORDER BY date")]
