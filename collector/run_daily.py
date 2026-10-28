"""국내(연기금 일별) 수집 → SQLite → JSON.

사용 예
  python -m collector.run_daily                       # 오늘(KST) 수집
  python -m collector.run_daily --backfill 120        # 최근 120영업일 백필
  python -m collector.run_daily --redo-last           # 직전 영업일 확정치로 재수집(다음날 08시)
  python -m collector.run_daily --date 20261027 --force
  python -m collector.run_daily --source fake --backfill 22   # 오프라인 모의 테스트
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from . import dart
from .config import DEFAULT_BACKFILL, RAW_KRX, STOCK_PAGE_LOOKBACK, TABLE_TOP_N
from .db import connect, ingest_all, upsert_day
from .export import Exporter
from .krx import FakeSource, build_sources, collect_day
from .util import iso, log, now_kst, to_date, write_json_gz, ymd


def raw_path(d: str):
    return RAW_KRX / f"{ymd(d)}.json.gz"


def plan_dates(args, primary, fallback) -> list[str]:
    end = ymd(args.date) if args.date else ymd(now_kst().date())
    if args.redo_last:
        # 기준일 직전 평일을 다시 받는다 → 잠정치를 확정치로 덮고, 전날 18시 실행이 실패했어도 메운다.
        prev = to_date(end) - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return [ymd(prev)]
    if args.backfill:
        start = ymd(to_date(end) - timedelta(days=int(args.backfill * 1.6) + 10))
        for src in (primary, fallback):
            if src is None:
                continue
            try:
                return src.trading_days(start, end)[-args.backfill:]
            except Exception as e:  # noqa: BLE001
                log.warning("%s 영업일 목록 실패: %s", src.name, e)
        raise SystemExit("영업일 목록을 가져오지 못했습니다")
    return [end]


def table_codes(con) -> list[str]:
    """최근 영업일 표(순매수·순매도 상위)에 오른 종목 코드."""
    q = f"""
    WITH recent AS (SELECT date FROM days ORDER BY date DESC LIMIT {STOCK_PAGE_LOOKBACK}),
    ranked AS (
      SELECT code,
        ROW_NUMBER() OVER (PARTITION BY date ORDER BY net DESC) AS rb,
        ROW_NUMBER() OVER (PARTITION BY date ORDER BY net ASC)  AS rs
      FROM pension WHERE date IN (SELECT date FROM recent))
    SELECT DISTINCT code FROM ranked WHERE rb<={TABLE_TOP_N} OR rs<={TABLE_TOP_N}"""
    return [r[0] for r in con.execute(q)]


def fake_revenue(con, codes: list[str], year: int) -> None:
    with con:
        for c in codes:
            base = c[:5] + "0" if c[-1] != "0" else c
            amt = (int(base) % 97 + 3) * 10**11 * (3 if int(base) % 5 == 0 else 1)
            con.execute("INSERT OR REPLACE INTO revenue VALUES(?,?,?,?,?)", (base, year - 1, amt, "매출액", "CFS"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="auto", choices=["auto", "fake"])
    ap.add_argument("--date", help="기준일 YYYYMMDD (기본: 오늘 KST)")
    ap.add_argument("--backfill", type=int, nargs="?", const=DEFAULT_BACKFILL, default=0)
    ap.add_argument("--redo-last", action="store_true", help="기준일 직전 평일을 다시 받아 덮어쓰기(확정치)")
    ap.add_argument("--force", action="store_true", help="이미 받은 날짜도 다시 수집")
    ap.add_argument("--no-dart", action="store_true")
    ap.add_argument("--export-recent", type=int, default=10, help="다시 만들 최근 일별 JSON 수")
    args = ap.parse_args(argv)

    primary, fallback = build_sources(args.source)
    dates = plan_dates(args, primary, fallback)
    log.info("대상 영업일 %d개: %s ~ %s", len(dates), iso(dates[0]) if dates else "-",
             iso(dates[-1]) if dates else "-")

    con = connect()
    ingest_all(con)
    failed: list[str] = []
    for d in dates:
        if raw_path(d).exists() and not (args.force or args.redo_last):
            continue
        try:
            raw = collect_day(d, primary, fallback)
        except Exception as e:  # noqa: BLE001
            log.error("%s 수집 실패: %s — 화면은 직전 영업일 데이터를 유지", iso(d), e)
            failed.append(d)
            continue
        if raw is None:
            continue
        write_json_gz(raw_path(d), raw)
        upsert_day(con, raw)

    codes = table_codes(con)
    latest = con.execute("SELECT MAX(date) FROM days").fetchone()[0]
    if latest and not args.no_dart:
        year = int(latest[:4])
        if isinstance(primary, FakeSource):
            fake_revenue(con, codes, year)
        else:
            try:
                dart.update_revenue(con, codes, year)
                dart.update_nps_filings(days_back=90 if args.backfill else 7)
            except Exception as e:  # noqa: BLE001
                log.error("DART 처리 실패(계속 진행): %s", e)

    Exporter(con).run(recent_only=None if args.backfill else args.export_recent)
    if failed:
        log.error("실패한 날짜: %s", ", ".join(iso(d) for d in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
