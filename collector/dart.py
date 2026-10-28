"""DART Open API: 작년매출(주요계정) · 국민연금 대량보유(5%) 공시 목록."""
from __future__ import annotations

import io
import sqlite3
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree as ET

import requests

from .config import DART_FILER_KEYWORD, DART_FILINGS_KEEP_DAYS, DART_KEY, RAW
from .util import RateLimiter, iso, log, now_kst, read_json, retry, write_json, ymd

BASE = "https://opendart.fss.or.kr/api"
CORP_CACHE = RAW / "dart_corp_codes.json"
FILINGS_CACHE = RAW / "dart_nps_filings.json"
REVENUE_ACCOUNTS = ["매출액", "수익(매출액)", "영업수익", "매출", "수익", "영업수익(매출액)"]
_rl = RateLimiter(0.15)


class DartError(RuntimeError):
    pass


@retry(tries=4, exceptions=(requests.RequestException, DartError))
def _get(path: str, **params) -> requests.Response:
    _rl.wait()
    r = requests.get(f"{BASE}/{path}", params={"crtfc_key": DART_KEY, **params}, timeout=60)
    r.raise_for_status()
    return r


def _json(path: str, **params) -> dict:
    js = _get(path, **params).json()
    status = js.get("status")
    if status in ("000", "013"):          # 013 = 조회된 데이터 없음
        return js
    if status in ("020",):                 # 요청 제한 초과 → 재시도 대상 아님
        raise RuntimeError(f"DART 사용한도 초과: {js.get('message')}")
    raise DartError(f"DART {path} status={status} {js.get('message')}")


def enabled() -> bool:
    if not DART_KEY:
        log.warning("DART_API_KEY 미설정 — 작년매출·대량보유 공시 생략")
        return False
    return True


# ---------------------------------------------------------------- 고유번호
def corp_codes(max_age_days: int = 30) -> dict[str, str]:
    """종목코드 → DART 고유번호(corp_code). 30일 캐시."""
    cache = read_json(CORP_CACHE, {})
    if cache.get("map") and cache.get("updated", "") >= iso(date.today() - timedelta(days=max_age_days)):
        return cache["map"]
    zf = zipfile.ZipFile(io.BytesIO(_get("corpCode.xml").content))
    root = ET.fromstring(zf.read(zf.namelist()[0]))
    m = {}
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            m[sc] = el.findtext("corp_code").strip()
    write_json(CORP_CACHE, {"updated": iso(date.today()), "map": m})
    log.info("DART 고유번호 %d건 갱신", len(m))
    return m


def common_code(code: str) -> str:
    """우선주 → 보통주 코드(005935→005930). 매출은 보통주 기준."""
    return code[:5] + "0" if code[-1] != "0" else code


# ---------------------------------------------------------------- 작년매출
def fetch_revenue(corp_code: str, year: int) -> tuple[int, str, str] | None:
    """사업보고서(11011) 주요계정에서 매출. 연결(CFS) 우선."""
    js = _json("fnlttSinglAcnt.json", corp_code=corp_code, bsns_year=str(year), reprt_code="11011")
    rows = js.get("list") or []
    for fs in ("CFS", "OFS"):
        for acc in REVENUE_ACCOUNTS:
            for r in rows:
                if r.get("fs_div") == fs and r.get("account_nm", "").replace(" ", "") == acc \
                        and r.get("sj_div") in ("IS", "CIS"):
                    amt = (r.get("thstrm_amount") or "").replace(",", "")
                    if amt.lstrip("-").isdigit():
                        return int(amt), acc, fs
    return None


def update_revenue(con: sqlite3.Connection, codes: list[str], ref_year: int) -> None:
    """codes 중 캐시에 없는 종목의 직전 사업연도 매출을 채운다(없으면 2년 전)."""
    if not enabled():
        return
    have = {(r[0], r[1]) for r in con.execute("SELECT code, year FROM revenue")}
    cmap = None
    for code in codes:
        base = common_code(code)
        if (base, ref_year - 1) in have or (base, ref_year - 2) in have:
            continue
        cmap = cmap or corp_codes()
        cc = cmap.get(base)
        if not cc:
            continue
        for y in (ref_year - 1, ref_year - 2):
            try:
                got = fetch_revenue(cc, y)
            except Exception as e:  # noqa: BLE001
                log.warning("매출 조회 실패 %s %d: %s", code, y, e)
                got = None
            if got:
                with con:
                    con.execute("INSERT OR REPLACE INTO revenue VALUES(?,?,?,?,?)", (base, y, *got))
                break
        else:
            with con:  # 없음도 기록해 매일 재조회 방지
                con.execute("INSERT OR REPLACE INTO revenue VALUES(?,?,?,?,?)",
                            (base, ref_year - 1, None, None, None))


# ---------------------------------------------------------------- 5% 공시
def update_nps_filings(days_back: int = 7) -> list[dict]:
    """최근 days_back일 '주식등의대량보유상황보고서'(D001) 중 제출인에 '국민연금' 포함 건 누적."""
    cache = read_json(FILINGS_CACHE, {"items": []})
    if not enabled():
        return cache["items"]
    end = now_kst().date()
    start = end - timedelta(days=days_back)
    found, page = [], 1
    while True:
        js = _json("list.json", bgn_de=ymd(start), end_de=ymd(end), pblntf_ty="D",
                   pblntf_detail_ty="D001", page_no=page, page_count=100)
        for r in js.get("list") or []:
            if DART_FILER_KEYWORD in (r.get("flr_nm") or ""):
                found.append({
                    "date": iso(r["rcept_dt"]), "rcept_no": r["rcept_no"],
                    "corp": r.get("corp_name"), "code": r.get("stock_code") or "",
                    "title": (r.get("report_nm") or "").strip(),
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}",
                })
        if page >= int(js.get("total_page") or 1):
            break
        page += 1
    by_no = {x["rcept_no"]: x for x in cache["items"]}
    by_no.update({x["rcept_no"]: x for x in found})
    cutoff = iso(end - timedelta(days=DART_FILINGS_KEEP_DAYS))
    items = sorted((x for x in by_no.values() if x["date"] >= cutoff),
                   key=lambda x: (x["date"], x["rcept_no"]), reverse=True)
    write_json(FILINGS_CACHE, {"updated": now_kst().isoformat(timespec="minutes"), "items": items}, compact=False)
    log.info("국민연금 대량보유 공시: 신규 확인 %d건, 보관 %d건", len(found), len(items))
    return items
