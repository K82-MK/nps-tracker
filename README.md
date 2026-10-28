# 국민연금 매수매도 추적기

연기금 일별 순매수·순매도(KRX)와 국민연금 미국 13F 분기 보유 변화를 보여주는 정적 웹사이트입니다.
서버 없이 **GitHub Actions(수집) + GitHub Pages(화면)** 로 운영합니다.

- 사이트: https://k82-mk.github.io/nps-tracker/
- 데이터(Claude 분석용): `https://raw.githubusercontent.com/K82-MK/nps-tracker/main/data/...`
- JSON 구조: [docs/SCHEMA.md](docs/SCHEMA.md)

> 정보 제공용이며 투자 권유가 아닙니다. ‘연기금’은 국민연금 외 기관을 포함합니다.

---

## 처음 한 번 설정 (약 20분)

### 1. 파일 올리기
GitHub 저장소 `nps-tracker` → **Add file → Upload files** → 압축을 푼 폴더 **안의 내용 전체**를 끌어다 놓고 **Commit changes**.
- `.github` 폴더(점으로 시작)가 꼭 같이 올라가야 자동 실행이 됩니다. macOS는 Finder에서 `Cmd+Shift+.` 로 숨김 폴더를 보이게 한 뒤 끌어다 놓으세요.

### 2. 인증키 등록 — Settings → Secrets and variables → Actions → New repository secret

| 이름 | 필수 | 어디서 받나 |
|---|---|---|
| `KRX_ID`, `KRX_PW` | 필수 | [KRX 정보데이터시스템](https://data.krx.co.kr) 회원가입 아이디·비밀번호 (pykrx 1.2+ 로그인 필수) |
| `DART_API_KEY` | 권장 | [DART Open API](https://opendart.fss.or.kr) → 인증키 신청 (작년매출, 5% 공시) |
| `SEC_USER_AGENT` | 13F 필수 | 직접 작성: `이름 이메일` 예) `Minkyu Kim you@example.com` (SEC 규정) |
| `KRX_API_KEY` | 선택 | [KRX Open API](https://openapi.krx.co.kr) 인증키 + ‘유가증권/코스닥 일별매매정보’, ‘KOSPI/KOSDAQ 시리즈 일별시세’ 이용신청. pykrx 시세·지수 실패 시 대체 |
| `OPENFIGI_API_KEY` | 선택 | [OpenFIGI](https://www.openfigi.com/api) 무료 키. 없으면 티커 변환이 느릴 뿐 동작함 |

### 3. Actions 쓰기 권한
Settings → Actions → General → **Workflow permissions → Read and write permissions** → Save

### 4. 첫 데이터 만들기
Actions 탭 →
1. **국내 연기금 수집** → Run workflow → mode `backfill`, 일수 `120` (KRX 호출 간격 때문에 30~60분)
2. **해외 13F 수집** → Run workflow (5~10분)

### 5. 사이트 공개
Settings → Pages → Source **Deploy from a branch** → Branch `main` / `/(root)` → Save.
1~2분 뒤 https://k82-mk.github.io/nps-tracker/ 에서 열립니다.

---

## 자동 실행 일정 (KST)

| 언제 | 무엇 |
|---|---|
| 평일 18:10 | 당일 연기금·지수·시세 수집 → JSON 갱신 |
| 평일 다음날 08:40 | 직전 영업일을 다시 받아 확정치로 덮어씀(전날 실패분도 메움) |
| 매주 월 07:20 | 국민연금 13F 새 분기 확인 |

GitHub 예약 실행은 몇 분~수십 분 늦게 시작될 수 있습니다.

## 예외 처리
- **휴장일**: 지수 데이터가 없으면 그날은 건너뜀. 화면에서 휴장일을 고르면 직전 영업일을 표시하고 안내 문구를 띄움
- **API 실패**: 모든 호출은 지수 백오프로 최대 4~5회 재시도. 시세·지수는 KRX Open API로 대체 시도
- **연기금 데이터 실패**: Open API에는 투자자별 종목 매매가 없어 대체 불가 → 그날은 저장하지 않고(화면은 직전 영업일 유지) 워크플로를 실패(빨간 X)로 표시, 다음날 08:40에 자동 재시도

## 폴더 구조
```
collector/   수집기 (krx, dart, sec13f, db, export, run_daily)
data/        화면용 JSON + raw/ 원천 보관(커밋됨)
db/          SQLite (Actions 캐시로 유지, 없으면 raw/에서 자동 재구성)
assets/      화면 (app.js, app.css, vendor/Chart.js·zoom·hammer)
docs/        SCHEMA.md, PROJECT_PROMPT.md
tests/       오프라인 모의 데이터 테스트
```

## 내 PC에서 실행
```bash
pip install -r requirements.txt pytest
python -m pytest -q tests                                  # 모의 데이터 테스트
KRX_ID=... KRX_PW=... DART_API_KEY=... python -m collector.run_daily --backfill 22
python -m http.server 8000                                 # http://localhost:8000
```

## 데이터 출처
KRX 정보데이터시스템(투자자별 매매동향·시세·지수), KRX Open API, DART Open API, SEC EDGAR(Form 13F-HR, CIK 1608046), OpenFIGI, ECB 환율(frankfurter).
섹터는 SEC SIC 코드를 묶은 근사 분류입니다.
