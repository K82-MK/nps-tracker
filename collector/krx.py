"""KRX 데이터 소스.

우선순위
  1) pykrx (KRX 정보데이터시스템, KRX_ID/KRX_PW 로그인 필요)
  2) KRX Open API (KRX_API_KEY) — 지수·종가·시가총액만 대체 가능.
     ※ Open API에는 '투자자별(연기금) 종목 순매수' 항목이 없으므로,
       연기금 데이터가 실패하면 그날은 저장하지 않고 화면은 직전 영업일을 보여준다.
     ※ Open API는 전일(T-1)까지만 제공 → 주로 다음날 08시 재확인 실행에서 쓰인다.
  3) FakeSource — 오프라인 테스트용 결정적 모의 데이터

수집 결과(원 단위)는 하루 1개 파일 data/raw/krx/YYYYMMDD.json.gz 로 남긴다(원천 보관).
"""
from __future__ import annotations

import hashlib
import math
import os
import time
from datetime import timedelta
from typing import Protocol

import requests

from .config import (INDEX_TICKERS, INVESTOR, INVESTOR_ROWS, KRX_API_KEY,
                     KRX_SLEEP, MARKETS)
from .util import RetryError, iso, log, retry, to_date, ymd

# --------------------------------------------------------------------------
# 공통 인터페이스
# --------------------------------------------------------------------------


class Source(Protocol):
    name: str

    def trading_days(self, start: str, end: str) -> list[str]: ...
    def index_close(self, d: str) -> dict[str, float]: ...
    def investor_flows(self, d: str, market: str) -> dict[str, dict]: ...
    def pension_by_stock(self, d: str, market: str) -> list[list]: ...
    def prices(self, d: str, market: str) -> dict[str, list]: ...


class HolidayError(Exception):
    """해당 일자는 휴장일(또는 아직 데이터 없음)."""


# --------------------------------------------------------------------------
# 1) pykrx
# --------------------------------------------------------------------------


class PykrxSource:
    name = "pykrx"

    def __init__(self) -> None:
        if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
            log.warning("KRX_ID/KRX_PW 미설정 — pykrx 1.2+ 는 로그인 없이 동작하지 않을 수 있음")
        from pykrx import stock  # import 시점에 환경변수로 로그인
        self.stock = stock

    def _pause(self) -> None:
        time.sleep(KRX_SLEEP)

    @retry(tries=4)
    def trading_days(self, start: str, end: str) -> list[str]:
        df = self.stock.get_index_ohlcv_by_date(ymd(start), ymd(end),
                                                INDEX_TICKERS["KOSPI"], name_display=False)
        self._pause()
        return [ymd(ix.date()) for ix in df.index]

    @retry(tries=4, giveup=(HolidayError,))
    def index_close(self, d: str) -> dict[str, float]:
        out = {}
        for m, tk in INDEX_TICKERS.items():
            df = self.stock.get_index_ohlcv_by_date(ymd(d), ymd(d), tk, name_display=False)
            self._pause()
            if df.empty or ymd(df.index[-1].date()) != ymd(d):
                raise HolidayError(d)
            out[m] = float(df["종가"].iloc[-1])
        return out

    @retry(tries=4, empty_is_error=True)
    def investor_flows(self, d: str, market: str) -> dict[str, dict]:
        df = self.stock.get_market_trading_value_by_investor(ymd(d), ymd(d), market)
        self._pause()
        out = {}
        for inv in INVESTOR_ROWS:
            if inv in df.index:
                r = df.loc[inv]
                out[inv] = {"buy": int(r["매수"]), "sell": int(r["매도"]), "net": int(r["순매수"])}
        if INVESTOR not in out:
            raise ValueError("연기금 행 없음")
        inst = ["금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금"]
        if "기관합계" not in out and all(k in out for k in inst):   # 상세 표에는 합계 행이 없을 수 있음
            out["기관합계"] = {f: sum(out[k][f] for k in inst) for f in ("buy", "sell", "net")}
        return out

    @retry(tries=4)
    def pension_by_stock(self, d: str, market: str) -> list[list]:
        df = self.stock.get_market_net_purchases_of_equities(ymd(d), ymd(d), market, INVESTOR)
        self._pause()
        rows = []
        for code, r in df.iterrows():
            buy, sell, net = int(r["매수거래대금"]), int(r["매도거래대금"]), int(r["순매수거래대금"])
            if buy == 0 and sell == 0:
                continue
            rows.append([str(code), str(r["종목명"]), buy, sell, net])
        return rows

    @retry(tries=4, empty_is_error=True)
    def prices(self, d: str, market: str) -> dict[str, list]:
        df = self.stock.get_market_cap(ymd(d), market=market)
        self._pause()
        return {str(code): [int(r["종가"]), int(r["시가총액"])] for code, r in df.iterrows()}


# --------------------------------------------------------------------------
# 2) KRX Open API (지수·시세 대체)
# --------------------------------------------------------------------------


class KrxOpenApiSource:
    name = "krx_openapi"
    BASE = "https://data-dbg.krx.co.kr/svc/apis"
    INDEX_EP = {"KOSPI": ("idx/kospi_dd_trd", "코스피"), "KOSDAQ": ("idx/kosdaq_dd_trd", "코스닥")}
    STOCK_EP = {"KOSPI": "sto/stk_bydd_trd", "KOSDAQ": "sto/ksq_bydd_trd"}

    def __init__(self, key: str = KRX_API_KEY) -> None:
        if not key:
            raise RuntimeError("KRX_API_KEY 없음")
        self.key = key
        self.s = requests.Session()
        self.s.headers["AUTH_KEY"] = key

    @retry(tries=4, exceptions=(requests.RequestException, ValueError))
    def _get(self, ep: str, d: str) -> list[dict]:
        r = self.s.get(f"{self.BASE}/{ep}", params={"basDd": ymd(d), "AUTH_KEY": self.key}, timeout=30)
        r.raise_for_status()
        js = r.json()
        return js.get("OutBlock_1") or []

    @staticmethod
    def _num(v) -> float:
        if v in (None, "", "-"):
            return math.nan
        return float(str(v).replace(",", ""))

    def trading_days(self, start: str, end: str) -> list[str]:
        days, cur, last = [], to_date(start), to_date(end)
        while cur <= last:
            if cur.weekday() < 5 and self._get(self.INDEX_EP["KOSPI"][0], ymd(cur)):
                days.append(ymd(cur))
            cur += timedelta(days=1)
        return days

    def index_close(self, d: str) -> dict[str, float]:
        out = {}
        for m, (ep, nm) in self.INDEX_EP.items():
            rows = self._get(ep, d)
            hit = [r for r in rows if r.get("IDX_NM") == nm]
            if not hit:
                raise HolidayError(d)
            out[m] = self._num(hit[0].get("CLSPRC_IDX"))
        return out

    def investor_flows(self, d: str, market: str) -> dict[str, dict]:
        raise NotImplementedError("KRX Open API는 투자자별 매매동향을 제공하지 않음")

    def pension_by_stock(self, d: str, market: str) -> list[list]:
        raise NotImplementedError("KRX Open API는 투자자별 종목 순매수를 제공하지 않음")

    def prices(self, d: str, market: str) -> dict[str, list]:
        out = {}
        for r in self._get(self.STOCK_EP[market], d):
            code = str(r.get("ISU_CD") or r.get("ISU_SRT_CD") or "")[-6:]
            close, cap = self._num(r.get("TDD_CLSPRC")), self._num(r.get("MKTCAP"))
            if code and not math.isnan(close):
                out[code] = [int(close), int(0 if math.isnan(cap) else cap)]
        if not out:
            raise ValueError("Open API 시세 없음")
        return out


# --------------------------------------------------------------------------
# 3) 오프라인 테스트용 모의 데이터
# --------------------------------------------------------------------------

_FAKE_UNIVERSE = [
    ("005930", "삼성전자", "KOSPI", 213000, 1270e12), ("000660", "SK하이닉스", "KOSPI", 1275000, 928e12),
    ("373220", "LG에너지솔루션", "KOSPI", 375000, 87e12), ("207940", "삼성바이오로직스", "KOSPI", 1452000, 103e12),
    ("018260", "삼성에스디에스", "KOSPI", 213000, 16e12), ("064400", "LG씨엔에스", "KOSPI", 73500, 7e12),
    ("032830", "삼성생명", "KOSPI", 246500, 49e12), ("402340", "SK스퀘어", "KOSPI", 769000, 101e12),
    ("034730", "SK", "KOSPI", 465500, 33e12), ("028260", "삼성물산", "KOSPI", 292000, 50e12),
    ("096770", "SK이노베이션", "KOSPI", 137000, 20e12), ("068270", "셀트리온", "KOSPI", 190000, 41e12),
    ("017670", "SK텔레콤", "KOSPI", 72100, 15e12), ("298040", "효성중공업", "KOSPI", 1853000, 17e12),
    ("079550", "LIG디펜스앤에어로스페이스", "KOSPI", 429000, 9.4e12), ("005935", "삼성전자우", "KOSPI", 164200, 132e12),
    ("034020", "두산에너빌리티", "KOSPI", 57600, 37e12), ("329180", "HD현대중공업", "KOSPI", 289500, 30e12),
    ("000150", "두산", "KOSPI", 901000, 12e12), ("010950", "S-Oil", "KOSPI", 127700, 14e12),
    ("105560", "KB금융", "KOSPI", 147200, 52e12), ("033780", "KT&G", "KOSPI", 188900, 19e12),
    ("005380", "현대차", "KOSPI", 288000, 58e12), ("086790", "하나금융지주", "KOSPI", 112400, 30e12),
    ("108490", "로보티즈", "KOSDAQ", 246500, 3.2e12), ("066970", "엘앤에프", "KOSDAQ", 83200, 3e12),
    ("247540", "에코프로비엠", "KOSDAQ", 180000, 17e12), ("086520", "에코프로", "KOSDAQ", 74400, 10e12),
    ("196170", "알테오젠", "KOSDAQ", 420000, 22e12), ("036930", "주성엔지니어링", "KOSDAQ", 52000, 2.5e12),
]


class FakeSource:
    """해시 기반 결정적 모의 데이터. 실제 시장과 무관."""
    name = "fake"

    def __init__(self, holidays: set[str] | None = None) -> None:
        self.holidays = holidays or {"20261009"}  # 한글날 예시

    @staticmethod
    def _h(*parts) -> float:
        b = hashlib.md5("|".join(map(str, parts)).encode()).digest()
        return int.from_bytes(b[:4], "big") / 2**32  # 0~1

    def trading_days(self, start: str, end: str) -> list[str]:
        out, cur, last = [], to_date(start), to_date(end)
        while cur <= last:
            if cur.weekday() < 5 and ymd(cur) not in self.holidays:
                out.append(ymd(cur))
            cur += timedelta(days=1)
        return out

    def _drift(self, d: str, key: str) -> float:
        n = (to_date(d) - to_date("20260101")).days
        return math.sin(n / 9 + len(key)) * 0.08 + (self._h(d, key) - 0.5) * 0.04

    def index_close(self, d: str) -> dict[str, float]:
        if to_date(d).weekday() >= 5 or ymd(d) in self.holidays:
            raise HolidayError(d)
        return {"KOSPI": round(5800 * (1 + self._drift(d, "KOSPI")), 2),
                "KOSDAQ": round(780 * (1 + self._drift(d, "KOSDAQ")), 2)}

    def investor_flows(self, d: str, market: str) -> dict[str, dict]:
        out = {}
        for inv in INVESTOR_ROWS:
            buy = int(self._h(d, market, inv, "b") * 2e12) + int(1e11)
            net = int((self._h(d, market, inv, "n") - 0.5) * 4e11)
            out[inv] = {"buy": buy, "sell": buy - net, "net": net}
        return out

    def pension_by_stock(self, d: str, market: str) -> list[list]:
        rows = []
        n = (to_date(d) - to_date("20260101")).days
        for code, name, m, _, cap in _FAKE_UNIVERSE:
            if m != market:
                continue
            bias = math.sin(n / 7 + int(code) % 13)  # 연속 매수/매도 구간이 생기도록
            v = (bias * 0.7 + (self._h(d, code) - 0.5) * 0.6) * cap / 2000
            buy = int(abs(v) * (1.5 + self._h(d, code, "x"))) + int(1e8)
            rows.append([code, name, buy, buy - int(v), int(v)])
        return rows

    def prices(self, d: str, market: str) -> dict[str, list]:
        out = {}
        for code, _, m, px, cap in _FAKE_UNIVERSE:
            if m == market:
                f = 1 + self._drift(d, code)
                out[code] = [int(px * f // 100 * 100), int(cap * f)]
        return out


# --------------------------------------------------------------------------
# 하루치 수집 (소스 대체 포함)
# --------------------------------------------------------------------------


def build_sources(kind: str = "auto") -> tuple[Source, Source | None]:
    """(주 소스, 지수·시세 대체 소스)."""
    if kind == "fake":
        return FakeSource(), None
    primary = PykrxSource()
    fallback = None
    if KRX_API_KEY:
        try:
            fallback = KrxOpenApiSource()
        except Exception as e:  # noqa: BLE001
            log.warning("KRX Open API 준비 실패: %s", e)
    return primary, fallback


def _try(primary, fallback, method: str, *args):
    try:
        return getattr(primary, method)(*args), primary.name
    except HolidayError:
        raise
    except (RetryError, Exception) as e:  # noqa: BLE001
        if fallback is None:
            raise
        log.warning("%s.%s 실패 → %s 로 대체: %s", primary.name, method, fallback.name, e)
        return getattr(fallback, method)(*args), fallback.name


def collect_day(d: str, primary: Source, fallback: Source | None = None) -> dict | None:
    """하루치 원천 데이터. 휴장일이면 None. 연기금 데이터 실패 시 예외."""
    d = ymd(d)
    try:
        index, idx_src = _try(primary, fallback, "index_close", d)
    except HolidayError:
        log.info("%s 휴장일(또는 데이터 없음) — 건너뜀", iso(d))
        return None
    raw = {"date": iso(d), "source": {"index": idx_src}, "index": index,
           "flows": {}, "pension": [], "prices": {}}
    for m in MARKETS:
        raw["flows"][m] = primary.investor_flows(d, m)          # 대체 불가
        for code, name, buy, sell, net in primary.pension_by_stock(d, m):
            raw["pension"].append([code, name, m, buy, sell, net])
        raw["prices"][m], raw["source"][f"prices_{m}"] = _try(primary, fallback, "prices", d, m)
    raw["source"]["pension"] = primary.name
    log.info("%s 수집 완료: 연기금 거래 종목 %d개", iso(d), len(raw["pension"]))
    return raw
