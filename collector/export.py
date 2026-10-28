"""SQLite → 화면용 JSON (스키마: docs/SCHEMA.md)."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from .config import (DATA, DISCLAIMER, MARKET_SERIES_DAYS, PENSION_NOTE, RAW,
                     SOURCE_NOTE_KR, STOCK_HISTORY_DAYS, STOCK_PAGE_LOOKBACK, TABLE_TOP_N)
from .db import business_days
from .util import eok, log, now_kst, read_json, write_json

WEEK = "월화수목금토일"
FLOW_ROWS = ["개인", "외국인", "기관합계", "금융투자", "투신", "연기금", "기타법인"]


def _weekday(d: str) -> str:
    return WEEK[date.fromisoformat(d).weekday()]


class Exporter:
    def __init__(self, con: sqlite3.Connection, out: Path = DATA):
        self.con, self.out = con, out
        self.days = business_days(con)
        self.pos = {d: i for i, d in enumerate(self.days)}
        # code → {date: net}
        self.net: dict[str, dict[str, int]] = defaultdict(dict)
        self.meta: dict[str, tuple[str, str]] = {}
        for d, code, name, mkt, net in con.execute(
                "SELECT date, code, name, market, net FROM pension ORDER BY date"):
            self.net[code][d] = net
            self.meta[code] = (name, mkt)
        self.revenue = self._load_revenue()
        self.filings = read_json(RAW / "dart_nps_filings.json", {"items": []})["items"]
        self.generated = now_kst().isoformat(timespec="minutes")

    # ------------------------------------------------------------ helpers
    def _load_revenue(self) -> dict[str, tuple[int | None, int]]:
        best: dict[str, tuple[int | None, int]] = {}
        for code, year, amt in self.con.execute("SELECT code, year, amount FROM revenue ORDER BY year"):
            if amt is not None or code not in best:
                best[code] = (amt, year)
        return best

    def streak(self, code: str, d: str) -> tuple[int, bool]:
        """d 기준 같은 방향 연속 영업일 수, 그리고 DB 시작일에 닿아 잘렸는지."""
        series = self.net.get(code, {})
        v0 = series.get(d, 0)
        if v0 == 0:
            return 0, False
        sign = 1 if v0 > 0 else -1
        i, n = self.pos[d], 0
        while i >= 0:
            v = series.get(self.days[i], 0)
            if v == 0 or (v > 0) != (sign > 0):
                return n, False
            n += 1
            i -= 1
        return n, True

    def _rev(self, code: str) -> tuple[int | None, int | None]:
        base = code[:5] + "0" if code[-1] != "0" else code
        return self.revenue.get(base, (None, None))

    def _prices(self, d: str) -> dict[str, tuple[int, int]]:
        return {c: (p, m) for c, p, m in self.con.execute(
            "SELECT code, close, mcap FROM prices WHERE date=?", (d,))}

    def _footer(self, d: str) -> dict:
        return {"source": SOURCE_NOTE_KR, "as_of": d, "generated": self.generated,
                "pension_note": PENSION_NOTE, "disclaimer": DISCLAIMER}

    # ------------------------------------------------------------ daily
    def daily(self, d: str) -> dict:
        i = self.pos[d]
        rows = self.con.execute(
            "SELECT code, name, market, buy, sell, net FROM pension WHERE date=?", (d,)).fetchall()
        px = self._prices(d)
        ix_now = self.con.execute("SELECT kospi, kosdaq FROM days WHERE date=?", (d,)).fetchone()
        ix_prev = (self.con.execute("SELECT kospi, kosdaq FROM days WHERE date=?",
                                    (self.days[i - 1],)).fetchone() if i > 0 else None)

        def idx(k: int) -> dict:
            c = ix_now[k]
            p = ix_prev[k] if ix_prev else None
            return {"close": c, "chg": round(c - p, 2) if p else None,
                    "chg_pct": round((c / p - 1) * 100, 2) if p else None}

        def row(r, rank) -> dict:
            code, name, mkt, buy, sell, net = r
            k, capped = self.streak(code, d)
            rv, ry = self._rev(code)
            p, mc = px.get(code, (None, None))
            return {"rank": rank, "code": code, "name": name, "market": mkt,
                    "net": eok(net), "buy": eok(buy), "sell": eok(sell),
                    "close": p, "streak": k, "streak_capped": capped,
                    "revenue": rv, "revenue_year": ry, "mcap": mc}

        pos = sorted((r for r in rows if r[5] > 0), key=lambda r: -r[5])
        neg = sorted((r for r in rows if r[5] < 0), key=lambda r: r[5])

        flows = {}
        for mkt in ("KOSPI", "KOSDAQ"):
            f = {inv: net for inv, net in self.con.execute(
                "SELECT investor, net FROM flows WHERE date=? AND market=?", (d, mkt))}
            flows[mkt] = [{"investor": inv, "net": eok(f.get(inv), 0)} for inv in FLOW_ROWS if inv in f]

        return {
            "date": d, "weekday": _weekday(d),
            "prev": self.days[i - 1] if i > 0 else None,
            "next": self.days[i + 1] if i + 1 < len(self.days) else None,
            "index": {"KOSPI": idx(0), "KOSDAQ": idx(1)},
            "flows": flows,
            "totals": {"buy": eok(sum(r[5] for r in pos)), "sell": eok(-sum(r[5] for r in neg)),
                       "buy_count": len(pos), "sell_count": len(neg)},
            "buy": [row(r, n + 1) for n, r in enumerate(pos[:TABLE_TOP_N])],
            "sell": [row(r, n + 1) for n, r in enumerate(neg[:TABLE_TOP_N])],
            "dart": [x for x in self.filings if x["date"] == d],
            "meta": self._footer(d),
        }

    # ------------------------------------------------------------ stock
    def stock(self, code: str) -> dict:
        last = self.days[-STOCK_HISTORY_DAYS:]
        px = defaultdict(dict)
        for d, c, m in self.con.execute(
                "SELECT date, close, mcap FROM prices WHERE code=? AND date>=? ORDER BY date",
                (code, last[0])):
            px[d] = (c, m)
        bs = {d: (b, s) for d, b, s in self.con.execute(
            "SELECT date, buy, sell FROM pension WHERE code=? AND date>=?", (code, last[0]))}
        name, mkt = self.meta.get(code, (code, ""))
        hist = []
        for d in last:
            c, m = px.get(d, (None, None))
            b, s = bs.get(d, (0, 0))
            hist.append({"date": d, "net": eok(self.net[code].get(d, 0)),
                         "buy": eok(b), "sell": eok(s), "close": c, "mcap": m})
        k, capped = self.streak(code, self.days[-1])
        rv, ry = self._rev(code)
        return {"code": code, "name": name, "market": mkt, "history": hist,
                "streak": k if self.net[code].get(self.days[-1], 0) >= 0 else -k,
                "streak_capped": capped,
                "sum_net": eok(sum(self.net[code].get(d, 0) for d in last)),
                "revenue": rv, "revenue_year": ry,
                "meta": self._footer(self.days[-1])}

    # ------------------------------------------------------------ all
    def run(self, recent_only: int | None = None) -> None:
        if not self.days:
            log.warning("DB에 영업일 데이터가 없어 내보내기를 건너뜀")
            write_json(self.out / "index.json", {"updated": self.generated, "dates": [],
                                                  "latest": None, "first": None})
            return
        lookback = self.days[-STOCK_PAGE_LOOKBACK:]
        recent = self.days if recent_only is None else self.days[-recent_only:]
        targets = sorted(set(recent) | set(lookback))
        table_codes: set[str] = set()
        for d in targets:
            js = self.daily(d)
            write_json(self.out / "daily" / f"{d}.json", js)
            if d in lookback:
                table_codes.update(r["code"] for r in js["buy"] + js["sell"])
        for code in sorted(table_codes):
            write_json(self.out / "stocks" / f"{code}.json", self.stock(code))

        s = self.days[-MARKET_SERIES_DAYS:]
        rows = {d: (k, q) for d, k, q in self.con.execute("SELECT date, kospi, kosdaq FROM days")}
        pf = defaultdict(dict)
        for d, m, net in self.con.execute("SELECT date, market, net FROM flows WHERE investor='연기금'"):
            pf[m][d] = net
        write_json(self.out / "market.json", {
            "dates": s,
            "kospi": [rows[d][0] for d in s], "kosdaq": [rows[d][1] for d in s],
            "pf_kospi": [eok(pf["KOSPI"].get(d), 0) for d in s],
            "pf_kosdaq": [eok(pf["KOSDAQ"].get(d), 0) for d in s],
            "unit": "억원", "generated": self.generated})
        write_json(self.out / "index.json", {
            "updated": self.generated, "first": self.days[0], "latest": self.days[-1],
            "dates": self.days, "stocks": sorted(table_codes),
            "names": {c: self.meta[c][0] for c in sorted(table_codes) if c in self.meta}})
        dart = defaultdict(list)
        for x in self.filings:
            dart[x["date"]].append(x)
        write_json(self.out / "dart" / "nps_5pct.json",
                   {"updated": self.generated,
                    "by_date": [{"date": d, "items": dart[d]} for d in sorted(dart, reverse=True)]})
        log.info("JSON 내보내기: 일별 %d개, 종목 %d개, 최신 %s", len(targets), len(table_codes), self.days[-1])
