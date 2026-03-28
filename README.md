# Alpha Agents

자율 암호화폐 트레이딩 AI 시스템. 멀티 에이전트 구조로 BTC/ETH 스윙 트레이딩을 자동화한다.

## 구조

```
analysis → strategy → risk → execute
```

| 에이전트 | 역할 |
|----------|------|
| Analysis | 기술적 지표 계산 (RSI, MACD, BB, EMA, ADX 등 18개 피처, 3 타임프레임) |
| Strategy | XGBoost 모델로 BUY/SELL/HOLD 예측 |
| Risk | confidence 체크, MDD 서킷브레이커, 포지션 비중 결정 |
| Execution | Paper trading 실행 및 DB 기록 |

## 기술 스택

- **오케스트레이션**: LangGraph StateGraph
- **모델**: XGBoost (클래스 균형 보정)
- **지표**: TA-Lib (15m / 1h / 4h 멀티 타임프레임)
- **DB**: PostgreSQL (asyncpg)
- **API**: Binance (OHLCV 수집)
- **웹**: FastAPI + TradingView Lightweight Charts
- **배포**: Railway

## 트레이딩 전략

- **심볼**: BTCUSDT, ETHUSDT
- **타임프레임**: 15분봉 기준 예측
- **예측 호라이즌**: 8시간 (32 × 15m)
- **레이블**: +1% 초과 → BUY, -1% 미만 → SELL, 나머지 → HOLD
- **리스크**: MDD 15% 서킷브레이커, 최대 포지션 25%

## 실행

### 로컬

```bash
# 의존성 설치
pip install -r requirements.txt

# 인프라 실행 (PostgreSQL, Redis)
docker-compose up -d

# 환경변수 설정
cp .env.example .env
# .env에 Binance API 키 등 입력

# OHLCV 백필 (90일)
PYTHONPATH=. python3 scripts/backfill_ohlcv.py --days 90

# XGBoost 학습
PYTHONPATH=. python3 agents/strategy_agent/trainer.py

# 실행
PYTHONPATH=. python3 main.py
```

### 백테스트

```bash
PYTHONPATH=. python3 scripts/run_backtest.py
```

### 대시보드 (로컬)

```bash
PYTHONPATH=. python3 web/app.py
# http://localhost:8000
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
TRADING_SYMBOLS=BTCUSDT,ETHUSDT
MAX_POSITION_RATIO=0.25
MDD_CIRCUIT_BREAKER=0.15
```

5. 배포 후 OHLCV 백필 (Railway Public URL 사용):

```bash
DATABASE_URL=<Railway Public URL> PYTHONPATH=. python3 scripts/backfill_ohlcv.py
```

## 대시보드

- 현재가 (BTC/ETH)
- 캔들스틱 차트 (15분봉)
- 잔고 / 수익률 / 승률
- 실현·미실현 손익
- 최근 거래 내역

## 디렉토리 구조

```
alpha-agents/
├── agents/
│   ├── analysis_agent/   # 기술적 지표
│   ├── strategy_agent/   # XGBoost 학습 및 예측
│   ├── risk_agent/       # 리스크 관리
│   └── execution_agent/  # Paper trading 실행
├── graph/                # LangGraph 오케스트레이션
├── storage/              # PostgreSQL / Redis
├── backtest/             # 백테스트 엔진
├── web/                  # FastAPI 대시보드
├── scripts/              # 백필, 백테스트, 상태 조회
├── models/               # 학습된 XGBoost 모델
└── main.py               # 메인 실행 루프
```
