"""경로·상수·환경변수 설정.

모든 비밀값은 환경변수(GitHub Actions Secrets)로만 받는다.
  KRX_ID, KRX_PW        : KRX 정보데이터시스템(data.krx.co.kr) 계정 — pykrx 1.2+ 로그인 필수
  KRX_API_KEY           : KRX Open API(openapi.krx.co.kr) 인증키 — 시세·지수 대체 경로(선택)
  DART_API_KEY          : DART Open API 인증키 — 작년매출·대량보유 공시
  SEC_USER_AGENT        : SEC 요청 헤더. "이름 이메일" 형식 (예: "Hong Gildong you@example.com")
  OPENFIGI_API_KEY      : OpenFIGI 키(선택, 없으면 느리게 동작)
"""
from __future__ import annotations

import os
from datetime import timezone, timedelta
from pathlib import Path

ROOT = Path(os.getenv("NPS_ROOT", Path(__file__).resolve().parent.parent))
DATA = Path(os.getenv("NPS_DATA_DIR", ROOT / "data"))
RAW = DATA / "raw"
RAW_KRX = RAW / "krx"
RAW_13F = RAW / "13f"
DB_PATH = Path(os.getenv("NPS_DB_PATH", ROOT / "db" / "nps.sqlite"))

KST = timezone(timedelta(hours=9))

# 국내
MARKETS = ("KOSPI", "KOSDAQ")
INDEX_TICKERS = {"KOSPI": "1001", "KOSDAQ": "2001"}
INVESTOR = "연기금"
INVESTOR_ROWS = ["금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금",
                 "기관합계", "기타법인", "개인", "외국인", "기타외국인"]
TABLE_TOP_N = 30            # 순매수/순매도 표 각 상위 N
STOCK_HISTORY_DAYS = 60     # 상세 페이지 영업일 수
STOCK_PAGE_LOOKBACK = 60    # 최근 N영업일 표에 한 번이라도 오른 종목만 상세 JSON 생성
MARKET_SERIES_DAYS = 250    # 메인 차트에 싣는 영업일 수
DEFAULT_BACKFILL = 120      # 최초 백필 영업일 수(연속일수 계산용)
KRX_SLEEP = float(os.getenv("KRX_SLEEP", "1.0"))   # KRX 호출 간격(초)

# DART
DART_KEY = os.getenv("DART_API_KEY", "")
DART_FILER_KEYWORD = "국민연금"
DART_FILINGS_KEEP_DAYS = 180

# SEC / 13F
NPS_CIK = os.getenv("NPS_CIK", "1608046")   # National Pension Service (확인: EDGAR 13F-HR 제출자)
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "")
SEC_MIN_INTERVAL = 0.12                     # 초당 10회 이하 보장 (≈8.3회/초)
THIRTEENF_QUARTERS = 8                      # 드롭다운에 보여줄 최근 분기 수
OPENFIGI_KEY = os.getenv("OPENFIGI_API_KEY", "")

KRX_API_KEY = os.getenv("KRX_API_KEY", "")

SOURCE_NOTE_KR = "KRX 투자자별 매매동향(연기금), KRX 시세·지수, DART Open API"
PENSION_NOTE = ("'연기금'은 KRX 투자자 분류로 국민연금 외에 공무원·사학·군인연금, "
                "각종 공제회 등이 함께 포함됩니다.")
DISCLAIMER = "본 사이트는 정보 제공용이며 투자 권유가 아닙니다."
THIRTEENF_NOTE = "13F는 분기 말 기준 미국 상장 종목만 포함하며 최대 45일 지연 공개됨"
