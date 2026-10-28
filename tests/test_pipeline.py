"""오프라인 테스트: 모의 데이터로 국내·해외 파이프라인 전체를 돌린다."""
from __future__ import annotations

import json

from collector import config, sec13f
from collector.run_daily import main as run_daily
from collector.util import read_json, read_json_gz

DATA = config.DATA


def _daily(d):
    return json.loads((DATA / "daily" / f"{d}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 국내
def test_backfill_one_month():
    assert run_daily(["--source", "fake", "--date", "20261027", "--backfill", "22"]) == 0
    idx = read_json(DATA / "index.json")
    assert idx["latest"] == "2026-10-27"
    assert len(idx["dates"]) == 22
    assert "2026-10-09" not in idx["dates"]            # 휴장일 제외


def test_holiday_is_skipped_and_previous_day_kept():
    before = read_json(DATA / "index.json")["latest"]
    assert run_daily(["--source", "fake", "--date", "20261009", "--force"]) == 0
    assert not (config.RAW_KRX / "20261009.json.gz").exists()
    assert read_json(DATA / "index.json")["latest"] == before


def test_daily_schema_and_sorting():
    d = _daily("2026-10-27")
    assert d["prev"] == "2026-10-26" and d["next"] is None and d["weekday"] == "화"
    nets = [r["net"] for r in d["buy"]]
    assert nets == sorted(nets, reverse=True) and all(n > 0 for n in nets)
    assert all(r["net"] < 0 for r in d["sell"])
    keys = {"rank", "code", "name", "market", "net", "buy", "sell", "close",
            "streak", "streak_capped", "revenue", "revenue_year", "mcap"}
    assert keys <= set(d["buy"][0])
    assert d["totals"]["buy"] == round(sum(r["net"] for r in d["buy"]), 2) or d["totals"]["buy_count"] > 30


def test_streak_matches_raw():
    days = read_json(DATA / "index.json")["dates"]
    raws = {dd: read_json_gz(config.RAW_KRX / f"{dd.replace('-', '')}.json.gz") for dd in days}
    d = _daily(days[-1])
    for r in d["buy"][:5] + d["sell"][:5]:
        sign = 1 if r["net"] > 0 else -1
        n = 0
        for dd in reversed(days):
            v = next((p[5] for p in raws[dd]["pension"] if p[0] == r["code"]), 0)
            if v * sign <= 0:
                break
            n += 1
        assert r["streak"] == n, r["name"]


def test_stock_detail_and_market_series():
    m = read_json(DATA / "market.json")
    assert len(m["dates"]) == len(m["kospi"]) == len(m["pf_kospi"]) == 22
    code = _daily("2026-10-27")["buy"][0]["code"]
    s = read_json(DATA / "stocks" / f"{code}.json")
    assert len(s["history"]) == 22 and s["history"][-1]["close"]


# ------------------------------------------------------------------ 해외 13F
XML = """<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
{rows}</informationTable>"""
ROW = ("<infoTable><nameOfIssuer>{n}</nameOfIssuer><titleOfClass>COM</titleOfClass>"
       "<cusip>{c}</cusip><value>{v}</value><shrsOrPrnAmt><sshPrnamt>{s}</sshPrnamt>"
       "<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>\n")


class FakeSec:
    Q1 = [("APPLE INC", "037833100", 1000, 10), ("MICROSOFT CORP", "594918104", 900, 9),
          ("EXXON MOBIL", "30231G102", 300, 3), ("INTEL CORP", "458140100", 100, 5)]
    Q2 = [("APPLE INC", "037833100", 1500, 12), ("MICROSOFT CORP", "594918104", 800, 8),
          ("EXXON MOBIL", "30231G102", 300, 3), ("NVIDIA CORP", "67066G104", 700, 7),
          ("APPLE INC", "037833100", 100, 1)]   # 같은 CUSIP 여러 행 → 합산

    def json(self, url):
        if "submissions" in url:
            return {"filings": {"recent": {
                "form": ["13F-HR", "13F-HR/A", "13F-HR"],
                "accessionNumber": ["0001608046-26-000002", "0001608046-26-000003", "0001608046-26-000001"],
                "filingDate": ["2026-08-12", "2026-08-20", "2026-05-13"],
                "reportDate": ["2026-06-30", "2026-06-30", "2026-03-31"]}}}
        return {"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "infotable.xml"}]}}

    def text(self, url):
        rows = self.Q2 if "000002" in url else self.Q1
        return XML.format(rows="".join(ROW.format(n=n, c=c, v=v, s=s) for n, c, v, s in rows))


class FakeFigi:
    T = {"037833100": "AAPL", "594918104": "MSFT", "30231G102": "XOM", "458140100": "INTC", "67066G104": "NVDA"}

    def map(self, cusips):
        return {c: {"ticker": self.T.get(c)} for c in cusips}


class FakeSectors:
    S = {"AAPL": 3571, "MSFT": 7372, "XOM": 2911, "INTC": 3674, "NVDA": 3674}

    def get(self, tickers):
        return {t: {"sector": sec13f.sic_to_sector(self.S.get(t))} for t in tickers}


def test_13f_compare():
    rc = sec13f.run(sec=FakeSec(), figi=FakeFigi(), sectors_obj=FakeSectors(),
                    fx_fn=lambda d: (1380.5, d))
    assert rc == 0
    idx = read_json(DATA / "13f" / "index.json")
    assert idx["quarters"][0]["quarter"] == "2026Q2"
    q = read_json(DATA / "13f" / "2026Q2.json")
    st = {h["ticker"]: h["status"] for h in q["holdings"]}
    assert st == {"AAPL": "비중확대", "MSFT": "비중축소", "XOM": "유지", "NVDA": "신규매수"}
    assert [h["ticker"] for h in q["exited"]] == ["INTC"] and q["exited"][0]["status"] == "전량매도"
    aapl = next(h for h in q["holdings"] if h["ticker"] == "AAPL")
    assert aapl["shares"] == 13 and aapl["chg_shares"] == 3 and aapl["chg_pct"] == 30.0
    s = q["summary"]
    assert s["total_usd"] == 3400 and s["total_krw"] == round(3400 * 1380.5)
    assert s["new"] == 1 and s["exited"] == 1 and s["count"] == 4
    assert round(sum(x["weight"] for x in q["sectors"])) == 100
    assert q["meta"]["note"].startswith("13F는 분기 말 기준")


def test_value_unit_before_2023():
    rows = sec13f.parse_infotable(XML.format(rows=ROW.format(n="A", c="X", v=5, s=1)), "2022-11-14")
    assert rows[0]["value"] == 5000


# ------------------------------------------------------------------ 재수집·실패 처리
def test_redo_last_refetches_previous_weekday():
    f = config.RAW_KRX / "20261027.json.gz"
    before = f.stat().st_mtime_ns
    assert run_daily(["--source", "fake", "--date", "20261028", "--redo-last"]) == 0
    assert f.stat().st_mtime_ns > before


def test_failure_keeps_previous_day(monkeypatch):
    from collector import krx

    def boom(self, d, m):
        raise RuntimeError("KRX 응답 없음(모의)")
    monkeypatch.setattr(krx.FakeSource, "pension_by_stock", boom)
    assert run_daily(["--source", "fake", "--date", "20261028"]) == 1      # 실패 → 종료코드 1
    assert not (config.RAW_KRX / "20261028.json.gz").exists()
    assert read_json(DATA / "index.json")["latest"] == "2026-10-27"         # 화면은 직전 영업일 유지
