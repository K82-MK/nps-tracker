"""국민연금(National Pension Service) Form 13F-HR 수집·비교.

흐름: EDGAR submissions(CIK) → 13F-HR 목록 → 분기별 information table XML 파싱
     → CUSIP 합산 → 직전 분기 비교(신규매수/비중확대/비중축소/전량매도/유지)
     → OpenFIGI(CUSIP→티커) → SEC SIC 코드로 근사 섹터 → USD/KRW 환산 → JSON

SEC 정책: User-Agent에 이름+이메일 필수(SEC_USER_AGENT), 초당 10회 이하.
원천 XML 파싱 결과는 data/raw/13f/<accession>.json 으로 보관해 다시 받지 않는다.
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from xml.etree import ElementTree as ET

import requests

from .config import (DATA, NPS_CIK, OPENFIGI_KEY, RAW, RAW_13F, SEC_MIN_INTERVAL,
                     SEC_USER_AGENT, THIRTEENF_NOTE, THIRTEENF_QUARTERS, DISCLAIMER)
from .util import RateLimiter, log, now_kst, read_json, retry, write_json

FIGI_CACHE = RAW / "figi_cache.json"
SECTOR_CACHE = RAW / "sector_cache.json"
FX_CACHE = RAW / "fx_cache.json"


# ============================================================== SEC
class Sec:
    def __init__(self, user_agent: str = SEC_USER_AGENT):
        if not user_agent or "@" not in user_agent:
            raise SystemExit("SEC_USER_AGENT 환경변수에 '이름 이메일' 형식으로 넣어주세요 (SEC 필수)")
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.rl = RateLimiter(SEC_MIN_INTERVAL)

    @retry(tries=5, base=2.5, exceptions=(requests.RequestException,))
    def get(self, url: str) -> requests.Response:
        self.rl.wait()
        r = self.s.get(url, timeout=60)
        if r.status_code == 429:
            time.sleep(10)
        r.raise_for_status()
        return r

    def json(self, url: str):
        return self.get(url).json()

    def text(self, url: str) -> str:
        return self.get(url).text


def list_filings(sec: Sec, cik: str = NPS_CIK) -> list[dict]:
    """분기(reportDate)별 원본 13F-HR 1건(가장 최근 제출분)."""
    js = sec.json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    rc = js["filings"]["recent"]
    by_q: dict[str, dict] = {}
    for form, acc, fd, rd in zip(rc["form"], rc["accessionNumber"], rc["filingDate"], rc["reportDate"]):
        if form != "13F-HR" or not rd:
            continue
        cur = by_q.get(rd)
        if cur is None or fd > cur["filing_date"]:
            by_q[rd] = {"accession": acc, "filing_date": fd, "report_date": rd}
    amendments = sum(1 for f in rc["form"] if f == "13F-HR/A")
    if amendments:
        log.info("13F-HR/A(정정) %d건은 원본 대비 비교에서 제외", amendments)
    return sorted(by_q.values(), key=lambda x: x["report_date"])


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_infotable(xml_text: str, filing_date: str) -> list[dict]:
    """information table XML → 행 목록. 2023-01-03 이전 제출분은 value가 천달러 단위."""
    mult = 1000 if filing_date < "2023-01-03" else 1
    root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    rows = []
    for it in root.iter():
        if _local(it.tag) != "infoTable":
            continue
        f = {}
        for ch in it.iter():
            name = _local(ch.tag)
            if ch.text and ch.text.strip():
                f[name] = ch.text.strip()
        rows.append({
            "cusip": f.get("cusip", "").upper(), "name": f.get("nameOfIssuer", ""),
            "class": f.get("titleOfClass", ""), "value": int(float(f.get("value", 0))) * mult,
            "shares": int(float(f.get("sshPrnamt", 0))), "type": f.get("sshPrnamtType", "SH"),
            "putcall": f.get("putCall", ""),
        })
    return rows


def fetch_filing(sec: Sec, f: dict, cik: str = NPS_CIK) -> dict:
    path = RAW_13F / f"{f['accession']}.json"
    cached = read_json(path)
    if cached:
        return cached
    acc = f["accession"].replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}"
    items = sec.json(f"{base}/index.json")["directory"]["item"]
    xmls = [i["name"] for i in items if i["name"].lower().endswith(".xml")
            and "primary_doc" not in i["name"].lower()]
    if not xmls:
        raise RuntimeError(f"information table XML 없음: {f['accession']}")
    rows = []
    for name in xmls:
        rows += parse_infotable(sec.text(f"{base}/{name}"), f["filing_date"])
    out = {**f, "rows": rows, "url": f"{base}/"}
    write_json(path, out)
    log.info("13F %s (%s) %d행 저장", f["report_date"], f["accession"], len(rows))
    return out


def aggregate(rows: list[dict]) -> dict[str, dict]:
    """CUSIP 단위 합산(주식만, 옵션 제외)."""
    agg: dict[str, dict] = {}
    for r in rows:
        if r["putcall"] or r["type"] != "SH" or not r["cusip"]:
            continue
        a = agg.setdefault(r["cusip"], {"cusip": r["cusip"], "name": r["name"],
                                         "class": r["class"], "value": 0, "shares": 0})
        a["value"] += r["value"]
        a["shares"] += r["shares"]
    return agg


# ============================================================== OpenFIGI
class Figi:
    URL = "https://api.openfigi.com/v3/mapping"

    def __init__(self, key: str = OPENFIGI_KEY):
        self.cache = read_json(FIGI_CACHE, {})
        self.s = requests.Session()
        if key:
            self.s.headers["X-OPENFIGI-APIKEY"] = key
        self.batch = 100 if key else 10
        self.rl = RateLimiter(0.25 if key else 2.6)  # 키 있음 25회/6초, 없음 25회/분

    @retry(tries=5, base=3, exceptions=(requests.RequestException,))
    def _post(self, jobs: list[dict]) -> list:
        self.rl.wait()
        r = self.s.post(self.URL, json=jobs, timeout=60)
        if r.status_code == 429:
            time.sleep(30)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _pick(data: list[dict]) -> dict:
        def score(x):
            return ((x.get("exchCode") == "US") * 4 + (x.get("marketSector") == "Equity") * 2
                    + (x.get("securityType") in ("Common Stock", "ADR", "REIT")) * 1)
        best = max(data, key=score)
        return {"ticker": best.get("ticker"), "figi_name": best.get("name"),
                "sec_type": best.get("securityType")}

    def map(self, cusips: list[str]) -> dict[str, dict]:
        todo = [c for c in cusips if c not in self.cache]
        for i in range(0, len(todo), self.batch):
            chunk = todo[i:i + self.batch]
            res = self._post([{"idType": "ID_CUSIP", "idValue": c} for c in chunk])
            for c, r in zip(chunk, res):
                self.cache[c] = self._pick(r["data"]) if r.get("data") else {"ticker": None}
            write_json(FIGI_CACHE, self.cache)
        if todo:
            log.info("OpenFIGI 신규 매핑 %d건", len(todo))
        return {c: self.cache.get(c, {"ticker": None}) for c in cusips}


# ============================================================== 섹터(SIC 기반 근사)
def sic_to_sector(sic: int | None) -> str:
    if not sic:
        return "기타/미분류"
    s = int(sic)
    R = lambda a, b: a <= s <= b  # noqa: E731
    if s in (1311, 1381, 1382, 1389, 2911, 2990, 5171, 5172) or R(4922, 4925) or R(1220, 1241):
        return "에너지"
    if R(2830, 2836) or R(3841, 3851) or R(8000, 8099) or s in (5122, 5047, 6324):
        return "헬스케어"
    if s in (6798,) or R(6500, 6553):
        return "부동산"
    if R(6000, 6799):
        return "금융"
    if R(3570, 3579) or R(3600, 3699) or R(7370, 7379) or R(3820, 3829) or s == 3577:
        return "IT"
    if R(4800, 4899) or R(2710, 2799) or R(7810, 7841) or s == 7900:
        return "커뮤니케이션"
    if R(4900, 4949) or R(4960, 4991):
        return "유틸리티"
    if R(2000, 2199) or R(2840, 2844) or s in (5411, 5912, 5331, 5399) or R(5140, 5149) or R(5180, 5182):
        return "필수소비재"
    if R(3710, 3716) or R(2300, 2399) or s in (3140, 3021) or R(7000, 7099) or R(5800, 5899) \
            or R(3630, 3639) or R(5000, 5999) or R(3940, 3949) or R(7990, 7999) or R(8200, 8299):
        return "경기소비재"
    if R(1000, 1499) or R(2400, 2699) or R(2800, 2899) or R(3200, 3399) or R(2950, 2952):
        return "소재"
    if R(1500, 1799) or R(3400, 3569) or R(3580, 3599) or R(3720, 3812) or R(4000, 4799) \
            or R(4950, 4959) or R(7380, 7389) or R(8700, 8799) or R(3900, 3999):
        return "산업재"
    return "기타/미분류"


class Sectors:
    def __init__(self, sec: Sec):
        self.sec = sec
        self.cache = read_json(SECTOR_CACHE, {"tickers": {}, "ticker_cik": {}, "updated": ""})

    def _ticker_cik(self) -> dict[str, int]:
        if not self.cache["ticker_cik"] or self.cache["updated"] < str(date.today() - timedelta(days=60)):
            js = self.sec.json("https://www.sec.gov/files/company_tickers.json")
            self.cache["ticker_cik"] = {v["ticker"].upper(): v["cik_str"] for v in js.values()}
            self.cache["updated"] = str(date.today())
        return self.cache["ticker_cik"]

    def get(self, tickers: list[str]) -> dict[str, dict]:
        tmap, n = None, 0
        for t in tickers:
            if not t or t in self.cache["tickers"]:
                continue
            tmap = tmap or self._ticker_cik()
            cik = tmap.get(t.upper()) or tmap.get(t.upper().replace("/", "-").replace(".", "-"))
            info = {"sic": None, "sic_desc": None}
            if cik:
                try:
                    js = self.sec.json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
                    info = {"sic": int(js.get("sic") or 0) or None, "sic_desc": js.get("sicDescription")}
                except Exception as e:  # noqa: BLE001
                    log.warning("SIC 조회 실패 %s: %s", t, e)
            self.cache["tickers"][t] = info
            n += 1
            if n % 50 == 0:
                write_json(SECTOR_CACHE, self.cache)
        write_json(SECTOR_CACHE, self.cache)
        return {t: {**self.cache["tickers"].get(t, {}), "sector": sic_to_sector(
            self.cache["tickers"].get(t, {}).get("sic"))} for t in tickers if t}


# ============================================================== 환율
@retry(tries=3, exceptions=(requests.RequestException, KeyError, ValueError))
def _fx_fetch(d: str) -> tuple[float, str]:
    for url in (f"https://api.frankfurter.dev/v1/{d}?base=USD&symbols=KRW",
                f"https://api.frankfurter.app/{d}?from=USD&to=KRW"):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            js = r.json()
            return float(js["rates"]["KRW"]), js.get("date", d)
        except requests.RequestException:
            continue
    raise requests.RequestException("환율 API 모두 실패")


def usd_krw(d: str) -> tuple[float | None, str | None]:
    cache = read_json(FX_CACHE, {})
    if d in cache:
        return tuple(cache[d])
    try:
        rate, used = _fx_fetch(d)
    except Exception as e:  # noqa: BLE001
        log.warning("환율 조회 실패(%s): %s — 원화 환산 생략", d, e)
        return None, None
    cache[d] = [rate, used]
    write_json(FX_CACHE, cache)
    return rate, used


# ============================================================== 비교·내보내기
def quarter_label(report_date: str) -> str:
    y, m = int(report_date[:4]), int(report_date[5:7])
    return f"{y}Q{(m - 1) // 3 + 1}"


def classify(cur: int, prev: int) -> str:
    if prev == 0 and cur > 0:
        return "신규매수"
    if cur == 0 and prev > 0:
        return "전량매도"
    if cur > prev:
        return "비중확대"
    if cur < prev:
        return "비중축소"
    return "유지"


def build_quarter(cur_f: dict, prev_f: dict | None, figi: dict, sectors: dict,
                  fx: tuple[float | None, str | None]) -> dict:
    cur, prev = aggregate(cur_f["rows"]), aggregate(prev_f["rows"]) if prev_f else {}
    total = sum(h["value"] for h in cur.values()) or 1
    prev_total = sum(h["value"] for h in prev.values()) or 1

    def enrich(h: dict, p: dict | None, side: str) -> dict:
        fm = figi.get(h["cusip"], {})
        t = fm.get("ticker")
        cs, ps = (h["shares"], p["shares"] if p else 0) if side == "cur" else (0, h["shares"])
        status = classify(cs, ps) if prev_f else "유지"
        return {
            "cusip": h["cusip"], "ticker": t, "name": h["name"], "class": h["class"],
            "shares": cs, "value": h["value"] if side == "cur" else 0,
            "weight": round(h["value"] / total * 100, 3) if side == "cur" else 0,
            "prev_shares": ps, "prev_value": (p["value"] if p else 0) if side == "cur" else h["value"],
            "prev_weight": round(((p["value"] if p else 0) if side == "cur" else h["value"]) / prev_total * 100, 3),
            "chg_shares": cs - ps,
            "chg_pct": round((cs - ps) / ps * 100, 2) if ps else None,
            "status": status,
            "sector": sectors.get(t, {}).get("sector", "기타/미분류") if t else "기타/미분류",
        }

    holdings = sorted((enrich(h, prev.get(c), "cur") for c, h in cur.items()), key=lambda x: -x["value"])
    exited = sorted((enrich(h, None, "prev") for c, h in prev.items() if c not in cur),
                    key=lambda x: -x["prev_value"]) if prev_f else []
    counts = defaultdict(int)
    for h in holdings:
        counts[h["status"]] += 1
    sec_sum = defaultdict(int)
    for h in holdings:
        sec_sum[h["sector"]] += h["value"]
    rate, fx_date = fx
    return {
        "quarter": quarter_label(cur_f["report_date"]),
        "report_date": cur_f["report_date"], "filing_date": cur_f["filing_date"],
        "accession": cur_f["accession"], "url": cur_f.get("url"),
        "prev_quarter": quarter_label(prev_f["report_date"]) if prev_f else None,
        "summary": {
            "total_usd": total, "usd_krw": rate, "fx_date": fx_date,
            "total_krw": round(total * rate) if rate else None,
            "count": len(holdings), "prev_count": len(prev),
            "new": counts["신규매수"], "exited": len(exited),
            "increased": counts["비중확대"], "decreased": counts["비중축소"], "kept": counts["유지"],
        },
        "sectors": sorted(({"sector": s, "value": v, "weight": round(v / total * 100, 2)}
                           for s, v in sec_sum.items()), key=lambda x: -x["value"]),
        "holdings": holdings, "exited": exited,
        "meta": {"source": f"SEC EDGAR Form 13F-HR (CIK {NPS_CIK}), OpenFIGI, SEC SIC 코드(섹터 근사), "
                           "환율 ECB(frankfurter)", "as_of": cur_f["report_date"],
                 "note": THIRTEENF_NOTE, "disclaimer": DISCLAIMER,
                 "generated": now_kst().isoformat(timespec="minutes")},
    }


def run(sec=None, figi=None, sectors_obj=None, fx_fn=usd_krw, out=DATA) -> int:
    sec = sec or Sec()
    filings = list_filings(sec)[-(THIRTEENF_QUARTERS + 1):]
    if len(filings) < 2:
        log.error("13F 분기가 2개 미만")
        return 1
    loaded = [fetch_filing(sec, f) for f in filings]
    all_cusips = sorted({r["cusip"] for f in loaded for r in aggregate(f["rows"]).values()} |
                        {c for f in loaded for c in aggregate(f["rows"])})
    figi = figi or Figi()
    fmap = figi.map(all_cusips)
    sectors_obj = sectors_obj or Sectors(sec)
    smap = sectors_obj.get(sorted({v["ticker"] for v in fmap.values() if v.get("ticker")}))
    quarters = []
    for i in range(1, len(loaded)):
        q = build_quarter(loaded[i], loaded[i - 1], fmap, smap, fx_fn(loaded[i]["report_date"]))
        write_json(out / "13f" / f"{q['quarter']}.json", q)
        quarters.append({"quarter": q["quarter"], "report_date": q["report_date"],
                         "filing_date": q["filing_date"], "count": q["summary"]["count"],
                         "total_usd": q["summary"]["total_usd"]})
    write_json(out / "13f" / "index.json", {
        "updated": now_kst().isoformat(timespec="minutes"), "cik": NPS_CIK,
        "quarters": sorted(quarters, key=lambda x: x["report_date"], reverse=True),
        "note": THIRTEENF_NOTE})
    log.info("13F 내보내기 %d개 분기 (최신 %s)", len(quarters), quarters[-1]["quarter"])
    return 0


if __name__ == "__main__":
    sys.exit(run())
