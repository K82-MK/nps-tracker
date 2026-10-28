# JSON 스키마 (v1)

모든 금액 단위는 표기된 대로입니다. 국내 `net/buy/sell`은 **억원(소수 2자리)**, `close`는 원, `mcap`·`revenue`는 원, 13F `value`는 **미국 달러**입니다.
Claude 분석용 원격 경로: `https://raw.githubusercontent.com/K82-MK/nps-tracker/main/data/...`

## 국내

### `data/index.json`
```json
{ "updated": "2026-10-27T18:12+09:00", "first": "2026-05-04", "latest": "2026-10-27",
  "dates": ["2026-05-04", "...", "2026-10-27"],
  "stocks": ["000660", "..."], "names": {"000660": "SK하이닉스"} }
```
`dates` = 데이터가 있는 영업일 전부(휴장일·수집 실패일은 없음 → 화면은 직전 영업일로 이동).

### `data/market.json` (메인 복합 차트, 최근 250영업일, 열 방향 배열)
```json
{ "dates": ["2026-10-26","2026-10-27"], "kospi": [5469.47, 5638.34], "kosdaq": [727.21, 743.83],
  "pf_kospi": [436, -1265], "pf_kosdaq": [12, 431], "unit": "억원" }
```
`pf_*` = 시장 전체 연기금 순매수(억원, 정수).

### `data/daily/YYYY-MM-DD.json`
```json
{
  "date": "2026-10-27", "weekday": "화", "prev": "2026-10-26", "next": null,
  "index": { "KOSPI": {"close": 5638.34, "chg": 168.87, "chg_pct": 3.09}, "KOSDAQ": {...} },
  "flows": { "KOSPI": [{"investor": "개인", "net": -14046}, {"investor": "연기금", "net": -1265}], "KOSDAQ": [...] },
  "totals": { "buy": 3159.12, "sell": 2723.40, "buy_count": 412, "sell_count": 388 },
  "buy":  [ ROW, ... 상위 30 ],
  "sell": [ ROW, ... 상위 30 ],
  "dart": [ { "date": "...", "corp": "...", "title": "...", "url": "https://dart.fss.or.kr/..." } ],
  "meta": { "source": "...", "as_of": "2026-10-27", "generated": "...", "pension_note": "...", "disclaimer": "..." }
}
```
ROW:
| 필드 | 뜻 |
|---|---|
| `rank` | 순위(금액 절댓값 내림차순) |
| `code`, `name`, `market` | 종목코드, 종목명, `KOSPI`/`KOSDAQ` |
| `net` | 연기금 순매수(억원). 순매도 표에서는 음수 |
| `buy`, `sell` | 연기금 매수·매도 거래대금(억원) |
| `close`, `mcap` | 종가(원), 시가총액(원) |
| `streak` | 기준일부터 같은 방향(순매수/순매도) 연속 영업일 수. 거래 없는 날(0)은 끊김 |
| `streak_capped` | DB 시작일까지 이어져 실제보다 짧게 잡혔을 수 있음(화면 `N일+`) |
| `revenue`, `revenue_year` | DART 사업보고서 매출(원)과 사업연도. 우선주는 보통주 값 |

`totals.buy` = 그날 순매수>0 **모든 종목**의 합, `totals.sell` = 순매수<0 모든 종목 합의 절댓값.

### `data/stocks/{code}.json` (최근 60영업일 표에 오른 종목만)
```json
{ "code": "000660", "name": "SK하이닉스", "market": "KOSPI", "streak": 4, "streak_capped": false,
  "sum_net": 5120.3, "revenue": 97100000000000, "revenue_year": 2025,
  "history": [ {"date": "2026-08-01", "net": 512.45, "buy": 900.1, "sell": 387.6, "close": 1275000, "mcap": 9.2e14} ] }
```
`streak` 양수=연속 순매수, 음수=연속 순매도.

### `data/dart/nps_5pct.json`
```json
{ "by_date": [ { "date": "2026-10-02", "items": [ {"corp": "...", "code": "...", "title": "주식등의대량보유상황보고서(일반)", "url": "..."} ] } ] }
```

## 해외 (13F)

### `data/13f/index.json`
```json
{ "cik": "1608046", "quarters": [ {"quarter": "2026Q2", "report_date": "2026-06-30", "filing_date": "2026-08-12", "count": 512, "total_usd": 1.0e11} ] }
```

### `data/13f/YYYYQn.json`
```json
{
  "quarter": "2026Q2", "report_date": "2026-06-30", "filing_date": "2026-08-12", "prev_quarter": "2026Q1",
  "summary": { "total_usd": 0, "usd_krw": 1380.5, "fx_date": "2026-06-30", "total_krw": 0,
               "count": 0, "prev_count": 0, "new": 0, "exited": 0, "increased": 0, "decreased": 0, "kept": 0 },
  "sectors":  [ {"sector": "IT", "value": 0, "weight": 31.2} ],
  "holdings": [ H, ... 평가액 내림차순 전체 ],
  "exited":   [ H, ... 전량매도 종목(직전 분기 값 기준) ],
  "meta": { "source": "...", "note": "13F는 분기 말 기준 미국 상장 종목만 포함하며 최대 45일 지연 공개됨" }
}
```
H: `cusip, ticker, name, class, shares, value, weight(%), prev_shares, prev_value, prev_weight, chg_shares, chg_pct, status(신규매수|비중확대|비중축소|전량매도|유지), sector`

섹터는 SEC SIC 코드를 GICS 유사 11개 분류로 묶은 **근사치**입니다.

## 원천 보관 (저장소에 커밋)
- `data/raw/krx/YYYYMMDD.json.gz` — 하루치 원천(원 단위): 지수, 투자자별 매매, 연기금 종목별 매매, 전 종목 종가·시총
- `data/raw/13f/<accession>.json` — 13F 파싱 결과
- `data/raw/*_cache.json` — DART 고유번호, OpenFIGI, SIC, 환율 캐시
