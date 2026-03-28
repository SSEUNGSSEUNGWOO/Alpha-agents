# Alpha Agents

자율 암호화폐 트레이딩 AI 시스템. 멀티 에이전트 구조로 Top 5 코인 스윙 트레이딩을 자동화한다.

## 구조

```
analysis → strategy → risk → execute
```

| 에이전트 | 역할 |
|----------|------|
| Analysis | 기술적 지표 계산 (RSI, MACD, BB, EMA, ADX 등, 3 타임프레임) |
| Strategy | XGBoost 모델로 BUY/SELL/HOLD 예측 |
| Risk | confidence 체크, MDD 서킷브레이커, 포지션 비중 결정 |
| Execution | Paper trading 실행 및 DB 기록 |

## 기술 스택

- **오케스트레이션**: LangGraph StateGraph
- **모델**: XGBoost (클래스 균형 + 시간 감쇠 가중치)
- **지표**: TA-Lib (15m / 1h / 4h 멀티 타임프레임)
- **DB**: PostgreSQL (asyncpg)
- **API**: Binance (OHLCV 수집)
- **웹**: FastAPI + TradingView Lightweight Charts
- **배포**: Railway

## 트레이딩 전략

- **심볼**: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT (Top 5)
- **자본 관리**: 단일 포트폴리오 풀 — 심볼별 독립 자본이 아닌 공유 잔고에서 동적 배분
- **타임프레임**: 15분봉 기준 예측
- **예측 호라이즌**: 8시간 (32 × 15m)
- **레이블**: +1% 초과 → BUY, -1% 미만 → SELL, 나머지 → HOLD
- **리스크**: MDD 15% 서킷브레이커, 단일 포지션 최대 총 자본의 25%

## 피처

BTC 전용 18개, 그 외 심볼 22개 (BTC 시장 피처 4개 추가).
학습(`feature_builder.py`)과 실시간 예측(`technical.py`) 양쪽에서 동일한 값을 사용한다.

| 그룹 | 피처 | 심볼 |
|------|------|------|
| 4h 지표 | RSI, MACD hist, BB position/width, EMA cross | 전체 |
| 1h 지표 | RSI, MACD hist, BB position, EMA cross | 전체 |
| 15m 지표 | BB width, ATR, Volatility, ADX, Volume ratio, Stoch K | 전체 |
| BTC 시장 | BTC 15m/1h 수익률, BTC/ETH 비율, 24h 상관관계 | BTC 제외 |
| 시장 심리 | Fear & Greed Index, Google Trends 검색량/변화율 | 전체 |

## 모델 학습 및 평가

### 데이터 분할 (시계열 순서 유지 — look-ahead bias 방지)

```
전체 데이터 (시간순 정렬)
├── Train      70%  → 모델 학습
├── Validation 15%  → Early stopping 모니터링 (학습 중단 기준)
└── Test       15%  → 최종 성능 평가 (학습에 전혀 미사용 — unseen)
```

시계열 데이터 특성상 `shuffle=False` 유지. 미래 데이터가 과거 학습에 유입되는 것을 방지한다.

### 샘플 가중치

두 가지 가중치를 곱해서 적용한다:

- **클래스 균형 가중치**: BUY/SELL/HOLD 불균형 보정 (`compute_sample_weight("balanced")`)
- **시간 감쇠 가중치**: 최근 데이터일수록 높은 가중치 (반감기 180일, 지수 감쇠)

### 현재 Test 성능 (unseen 데이터 기준)

| 심볼 | Test Accuracy | Test F1 (macro) | Test 기간 |
|------|:---:|:---:|------|
| BTCUSDT | 0.482 | 0.294 | 2026-03-14 ~ 2026-03-27 |
| ETHUSDT | 0.384 | 0.390 | 2026-03-14 ~ 2026-03-27 |
| SOLUSDT | 0.354 | 0.328 | 2026-03-14 ~ 2026-03-27 |
| BNBUSDT | 0.445 | 0.340 | 2026-03-14 ~ 2026-03-27 |
| XRPUSDT | 0.369 | 0.372 | 2026-03-14 ~ 2026-03-27 |

> 랜덤 예측 baseline: Accuracy ≈ 0.33, F1 ≈ 0.33 (3-class uniform)
> 현재 모델은 baseline 대비 일부 심볼에서 유의미한 개선을 보인다.
> 데이터가 90일치밖에 없어 test 기간이 짧다 — 데이터 축적될수록 평가 신뢰도 향상 예정.

## 자동 재학습

- **주기**: 매주 일요일 00:00 UTC
- **방식**: 전체 누적 데이터 + 시간 감쇠 가중치 (180일 반감기)
- **비교**: 새 모델 F1 ≥ 기존 모델 F1 - 1%일 때만 교체
- **저장**: PostgreSQL BYTEA — Railway 재배포 후에도 모델 유지
- **핫스왑**: 재시작 없이 메모리 내 모델 즉시 교체

## 데이터 수집 (백그라운드)

| 수집기 | 주기 | 내용 |
|--------|------|------|
| OHLCV | 실시간 | Binance 15m/1h/4h 캔들 |
| Fear & Greed | 24시간 | alternative.me (무료, 키 불필요) |
| Google Trends | 7일 | 심볼별 검색량 (pytrends) |
| CryptoPanic | 1시간 | 뉴스 감성 (CRYPTOPANIC_API_KEY 필요) |

## 웹 대시보드

- 포트폴리오 총 잔고 / 수익률 / 가용 현금
- 실현·미실현 손익
- Fear & Greed 게이지
- 심볼 카드 (클릭하면 캔들차트 + 포지션 + 거래 내역 펼쳐짐)
- 60초 자동 갱신

## 실행

### 로컬

```bash
# 의존성 설치
pip install -r requirements.txt

# 인프라 실행 (PostgreSQL)
docker-compose up -d

# 환경변수 설정
cp .env.example .env
# .env에 Binance API 키 등 입력

# OHLCV 백필 (90일)
PYTHONPATH=. python3 scripts/backfill_ohlcv.py --days 90

# Google Trends 백필 (5년치)
PYTHONPATH=. python3 scripts/backfill_trends.py

# XGBoost 학습
PYTHONPATH=. python3 agents/strategy_agent/trainer.py

# 모델 DB 업로드
PYTHONPATH=. python3 scripts/upload_models_to_db.py

# 실행 (트레이딩 봇 + 웹 서버 동시 실행)
PYTHONPATH=. python3 start.py
```

### 백테스트

```bash
PYTHONPATH=. python3 scripts/run_backtest.py
```

## Railway 배포

1. Railway에서 Empty Project 생성
2. PostgreSQL 서비스 추가
3. GitHub Repo 연결
4. Variables 설정:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
BINANCE_TESTNET=true
TRADING_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT
TOTAL_CAPITAL=5000
MAX_POSITION_RATIO=0.25
MDD_CIRCUIT_BREAKER=0.15
# 선택
CRYPTOPANIC_API_KEY=...
```

5. 배포 후 초기 데이터 세팅 (로컬에서 Railway DB URL로):

```bash
# OHLCV 백필
DATABASE_URL=<Railway DB URL> PYTHONPATH=. python3 scripts/backfill_ohlcv.py --days 90

# Google Trends 백필
DATABASE_URL=<Railway DB URL> PYTHONPATH=. python3 scripts/backfill_trends.py

# 모델 DB 업로드
DATABASE_URL=<Railway DB URL> PYTHONPATH=. python3 scripts/upload_models_to_db.py
```

## 디렉토리 구조

```
alpha-agents/
├── agents/
│   ├── analysis_agent/    # 기술적 지표
│   ├── strategy_agent/    # XGBoost 학습·예측·주간 재학습
│   ├── risk_agent/        # 리스크 관리
│   ├── execution_agent/   # Paper trading 실행
│   └── data_agent/        # OHLCV·감성·Trends 수집기
├── graph/                 # LangGraph 오케스트레이션
├── storage/               # PostgreSQL 연결·테이블 초기화
├── backtest/              # 백테스트 엔진
├── web/                   # FastAPI 대시보드
├── scripts/               # 백필·백테스트·모델 업로드
├── models/                # 학습된 XGBoost 모델 (파일 fallback용)
└── main.py                # 메인 실행 루프
```
