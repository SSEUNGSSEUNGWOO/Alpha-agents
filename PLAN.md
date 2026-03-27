# Alpha Agents — 자율투자 AI 시스템 상세 계획서

> "스스로 학습하는 투자 조직"
> 멀티 에이전트 + XGBoost 기반 자율 암호화폐 트레이딩 시스템

작성일: 2026-03-27

---

## 목차

1. 프로젝트 개요 및 목표
2. 시스템 아키텍처
3. 각 에이전트 상세 설계
4. 기술 스택
5. 폴더 구조
6. 개발 단계별 로드맵 (Phase 1~4)
7. 리스크 및 고려사항

---

## 1. 프로젝트 개요 및 목표

### 1.1 개요

Alpha Agents는 멀티 에이전트 오케스트레이션과 XGBoost 전략 모델을 결합한 자율투자 AI 시스템이다. 각 에이전트는 독립된 역할을 수행하면서 LangGraph의 상태 그래프(StateGraph)를 통해 유기적으로 협력한다.

시스템은 "스스로 학습하는 투자 조직"을 지향한다. Phase 1은 OHLCV와 온체인 데이터 기반으로 XGBoost 모델이 매매 결정을 학습한다. Phase 2 이후 뉴스 감성 피처(KR-FinBERT)를 A/B 테스트로 검증하여 점진적으로 추가한다.

### 1.2 핵심 목표

| 목표 | 지표 |
|------|------|
| 수익률 극대화 | 연간 샤프 지수(Sharpe Ratio) > 1.5 |
| 리스크 제어 | 최대 낙폭(MDD) < 15% |
| 자율 학습 | 주 1회 이상 XGBoost 모델 자동 재학습 |
| 실시간 반응 | 기술적 분석 → 주문 실행까지 < 500ms |
| 설명 가능성 | 모든 거래 결정에 대한 근거 로깅 |

### 1.3 범위

- 거래소: Binance (Spot / Futures)
- 초기 대상 자산: BTC/USDT, ETH/USDT (이후 확장)
- 데이터 소스 (Phase 1): OHLCV 캔들 데이터, 온체인 데이터(Glassnode)
- 데이터 소스 (Phase 2+): 뉴스 감성 (newszips → KR-FinBERT, A/B 테스트)
- 운용 모드: 백테스트 → 페이퍼 트레이딩 → 라이브 트레이딩

---

## 2. 시스템 아키텍처

### 2.1 전체 데이터 흐름

```
외부 데이터 소스
  ├── 뉴스 (RSS / CryptoPanic API)
  ├── Reddit API (r/CryptoCurrency, r/Bitcoin 등)
  ├── 온체인 데이터 (Glassnode API)
  └── Binance WebSocket (OHLCV, Order Book)
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│                    LangGraph StateGraph                      │
│                                                              │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │  데이터 Agent│───▶│  분석 Agent  │───▶│  전략 Agent   │  │
│  │  (수집+저장) │    │ (FinBERT+TA)│    │  (XGBoost)    │  │
│  └─────────────┘    └──────────────┘    └───────┬───────┘  │
│         │                  │                    │           │
│         ▼                  ▼                    ▼           │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │ PostgreSQL  │    │  Shared State│    │  리스크 Agent  │  │
│  │ (수집 저장) │    │  (Redis)     │    │               │  │
│  └─────────────┘    └──────────────┘    └───────┬───────┘  │
│                                                  │          │
│                                         ┌────────▼──────┐  │
│                                         │  실행 Agent   │  │
│                                         │ (Binance API) │  │
│                                         └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
   PostgreSQL (거래 기록, XGBoost 학습 데이터)
   Grafana Dashboard (모니터링)
```

### 2.2 에이전트 간 메시지 구조

모든 에이전트는 LangGraph의 공유 상태 딕셔너리를 통해 통신한다. 상태는 불변(immutable) 업데이트 방식으로 관리되며, 각 노드는 자신의 출력만 덮어쓴다.

```
AgentState:
  raw_data:          데이터 Agent가 수집한 원본 데이터
  ohlcv:             OHLCV 캔들 데이터 (15m, 1h, 4h)
  onchain:           온체인 지표 딕셔너리
  technical_signals: 기술적 지표 딕셔너리
  # Phase 2+: sentiment_score 추가 예정
  xgb_action:        전략 Agent의 결정 (BUY / SELL / HOLD, 비율)
  risk_approved:     리스크 Agent의 승인 여부 (bool)
  risk_override:     리스크 Agent의 포지션 조정 명령
  execution_result:  실행 Agent의 주문 결과
  timestamp:         상태 생성 시각
```

### 2.3 오케스트레이션 흐름 (LangGraph 노드 순서)

```
START
  → data_collection_node
  → analysis_node (sentiment + TA 병렬 실행)
  → strategy_node (XGBoost 추론)
  → risk_check_node
      ├── [승인] → execution_node → END
      └── [거부/조정] → strategy_node (재계획) or HALT
```

---

## 3. 각 에이전트 상세 설계

### 3.1 데이터 Agent

**역할:** OHLCV와 온체인 데이터를 수집하고 PostgreSQL에 저장한다. Phase 1은 가격/온체인 데이터만 수집. 뉴스 감성은 Phase 2에서 A/B 테스트로 효과 검증 후 추가.

**입력:**
- 크론 트리거 (1분 간격 OHLCV, 1시간 간격 온체인)

**출력:**
- OHLCV 캔들 데이터 (15m, 1h, 4h)
- 온체인 지표 (일봉)

**주요 서브모듈:**

| 서브모듈 | 역할 | 데이터 소스 |
|---------|------|------------|
| OHLCVCollector | 캔들 데이터 수집 | Binance WebSocket |
| OnchainCollector | 온체인 지표 수집 | Glassnode API |

**기술 구현:**
- Binance WebSocket으로 실시간 15m/1h/4h 캔들 수집
- 수집 데이터는 PostgreSQL에 저장
- 데이터 TTL 정책: OHLCV 90일, 온체인 90일

> **Phase 2 실험 예정:** newszips (유튜브 뉴스 요약, Supabase) → KR-FinBERT 감성 점수를 피처로 추가 후 성능 비교 (A/B 테스트)

---

### 3.2 분석 Agent

**역할:** OHLCV 기반 기술적 지표를 계산하고 전략 Agent에 전달한다. Phase 1은 기술적 분석만. 감성 분석은 Phase 2 A/B 테스트로 효과 검증 후 추가.

**입력:**
- 최근 OHLCV 캔들 데이터 (15m, 1h, 4h)

**출력:**
```python
{
  "technical": {
    "rsi_14": 58.3,
    "macd": 120.5,
    "macd_signal": 98.2,
    "bb_upper": 87500,
    "bb_lower": 82000,
    "ema_20": 84200,
    "ema_50": 83100,
    "atr": 1200,
    "obv": 98234000,
    "volume_ratio": 1.32      # 현재 거래량 / 20봉 평균
  }
}
```

**기술 구현:**
- 기술적 분석: `ta-lib` 라이브러리 (RSI, MACD, Bollinger Bands, EMA, ATR, OBV)
- M2에서 CPU 연산으로 충분히 빠름 (< 10ms)

> **Phase 2 실험 예정:** KR-FinBERT 감성 점수 피처 추가 후 XGBoost 성능 비교

---

### 3.3 전략 Agent (XGBoost)

**역할:** 분석 Agent의 시그널과 기술적 지표를 피처로 받아 XGBoost 분류 모델로 매수/매도/보유를 결정한다. 히스토리컬 거래 데이터로 주기적으로 재학습한다.

**입력:**
```python
{
  "technical_signals": {...},   # ta-lib 지표
  "onchain": {...},             # Glassnode 온체인 지표
  "current_position": 0.3,     # 현재 BTC 포지션 비율 (0~1)
  "unrealized_pnl": 0.045,     # 미실현 손익률
  "market_volatility": 0.023   # 24h ATR 기반 변동성
  # Phase 2+: "sentiment_score": 0.65 추가 예정
}
```

**출력:**
```python
{
  "action": "BUY",            # BUY / SELL / HOLD
  "amount_ratio": 0.15,       # 포트폴리오 대비 비율 (0~1)
  "confidence": 0.78,         # 예측 확률 (모델 출력)
  "feature_importance": {...} # 주요 결정 근거 피처
}
```

**XGBoost 설계:**

| 항목 | 내용 |
|------|------|
| 알고리즘 | XGBoost (다중 분류: BUY / SELL / HOLD) |
| 보조 모델 | LightGBM (앙상블 교차 검증용) |
| 피처 (Phase 1) | RSI, MACD, BB, EMA, ATR, OBV, volume_ratio, 온체인 지표 등 15~20개 |
| 피처 (Phase 2+) | Phase 1 피처 + KR-FinBERT sentiment_score (A/B 테스트) |
| 레이블 | 미래 N봉 수익률 기반 자동 레이블링 (상위 30% → BUY, 하위 30% → SELL, 나머지 → HOLD) |
| 학습 주기 | 주 1회 전체 재학습 + 롤링 윈도우 (최근 90일) |
| 오버피팅 방지 | early stopping, 홀드아웃 14일, feature importance 모니터링 |

**레이블 생성 로직:**
```python
# 미래 8시간(32봉 × 15분봉) 수익률로 레이블 자동 생성 — 스윙 트레이딩
# 기준 타임프레임: 15m 캔들
future_return = (price_t+32 - price_t) / price_t
label = "BUY"  if future_return > threshold_up   else \
        "SELL" if future_return < threshold_down  else "HOLD"
```

**오버피팅 방지:**
- 롤링 윈도우 학습 (최근 90일 데이터만 사용)
- 아웃-오브-샘플 검증 (마지막 14일 홀드아웃)
- XGBoost early stopping (validation loss 기준)
- feature importance 급변 시 재학습 트리거

---

### 3.4 리스크 Agent

**역할:** 전략 Agent의 결정이 사전 정의된 리스크 파라미터를 위반하는지 검토하고, 승인/거부/조정 명령을 내린다. 시스템 전체의 안전망 역할을 담당한다.

**입력:**
- 전략 Agent의 `xgb_action`
- 현재 포트폴리오 상태 (총 자산, 포지션, 미실현 손익)
- 시장 변동성 지표

**출력:**
```python
{
  "approved": True/False,
  "adjusted_action": {...},   # 조정된 경우 수정된 주문
  "override_reason": "...",
  "risk_metrics": {
    "current_mdd": 0.08,
    "position_risk": 0.12,
    "volatility_score": "HIGH"
  }
}
```

**리스크 규칙 체계:**

| 규칙 | 파라미터 | 액션 |
|------|---------|------|
| 단일 거래 최대 노출 | 포트폴리오의 25% 이하 | 초과 시 비율 강제 조정 |
| 최대 낙폭 서킷브레이커 | MDD > 15% (목표치와 동일) | 전체 포지션 강제 청산 |
| 손절 기준 | 단일 포지션 -5% | 자동 손절 주문 |
| 변동성 스케일링 | ATR 기반 포지션 축소 | 고변동성 시 포지션 50% 축소 |
| 일일 최대 손실 | 일일 -3% | 당일 거래 중단 |
| 연속 손실 | 3회 연속 손실 | 포지션 25%로 축소 |

**기술 구현:**
- 규칙 기반 검사는 순수 Python (LLM 불필요, 지연 최소화)
- 포트폴리오 상태는 Redis에서 실시간 조회 (< 10ms)
- 모든 리스크 이벤트는 구조화 로그로 기록 → 사후 분석

---

### 3.5 실행 Agent

**역할:** 리스크 Agent가 승인한 주문을 Binance API를 통해 실제로 집행하고, 체결 결과를 상태에 기록한다.

**입력:**
- 승인된 주문 명세 (방향, 비율, 종목)
- 현재 포트폴리오 잔고 (Binance API)

**출력:**
```python
{
  "order_id": "12345678",
  "status": "FILLED",
  "filled_price": 84230.5,
  "filled_qty": 0.0178,
  "fee": 0.00178,
  "timestamp": "2026-03-27T10:23:45Z",
  "slippage": 0.0003
}
```

**기술 구현:**
- `python-binance` AsyncClient (비동기 I/O)
- WebSocket으로 실시간 체결 확인 (REST 폴링 대신)
- 주문 유형: Market → Limit (슬리피지 초과 시 취소) → Market 폴백
- 지수 백오프 재시도: 5회, 1s → 60s
- Rate limit 준수: Binance 제한의 80% 이하 유지 (초당 1,200 weight)
- 체결 후 PostgreSQL에 거래 기록 저장 (XGBoost 재학습 데이터)

---

## 4. 기술 스택

### 4.1 핵심 프레임워크

| 카테고리 | 선택 기술 | 버전 | 선택 이유 |
|---------|---------|------|---------|
| 에이전트 오케스트레이션 | LangGraph | ≥ 0.3 | StateGraph 기반 명시적 흐름 제어, 병렬 노드 실행 지원 |
| 감성 분석 모델 | FinBERT (ProsusAI/finbert) | 로컬 | 금융 특화, API 비용 없음, CPU 실행 가능 |
| 전략 모델 | XGBoost | ≥ 2.0 | 빠른 학습, feature importance 해석 가능, M2 최적화 |
| 보조 모델 | LightGBM | ≥ 4.3 | 앙상블 교차 검증, 메모리 효율 |
| 벡터 DB | Qdrant | ≥ 1.9 | Phase 3+ 도입 — 뉴스 감성 피처 RAG 확장 시 활성화 |
| 임베딩 모델 | BAAI/bge-small-en-v1.5 | 로컬 | Phase 3+ Qdrant RAG 도입 시 사용 |

### 4.2 데이터 & 인프라

| 카테고리 | 선택 기술 | 용도 |
|---------|---------|------|
| 메시지 큐 | Redis Streams | 에이전트 간 비동기 메시지, 포트폴리오 상태 캐시 |
| 관계형 DB | PostgreSQL 16 | 거래 기록, XGBoost 학습 데이터, 백테스트 결과 |
| 시계열 DB | TimescaleDB (PostgreSQL 확장) | OHLCV 고속 조회 — Phase 3 이후 도입 |
| 컨테이너 | Docker + Docker Compose | 로컬 개발 환경 |
| 오케스트레이션 | Kubernetes (Phase 3~4) | 프로덕션 배포, 스케일링 |
| 모니터링 | Grafana + Prometheus | 실시간 트레이딩 대시보드 |
| 로깅 | structlog → Loki | 구조화 로그, 분산 추적 |

### 4.3 데이터 수집

| 데이터 | 도구/API | 비고 |
|-------|---------|------|
| OHLCV | Binance WebSocket | 무료 |
| 온체인 | Glassnode API | 기본 지표 무료, 고급 $29/월 |
| 기술적 지표 | ta-lib (C 바인딩) | pandas 2.x 호환, brew install ta-lib 필요 |
| 뉴스/감성 (Phase 2+) | newszips Supabase → KR-FinBERT | A/B 테스트로 효과 검증 후 추가 |

### 4.4 전체 Python 의존성 (핵심)

```
langgraph >= 0.3.0
langchain >= 0.3.0
langchain-community >= 0.3.0
sentence-transformers >= 3.0.0  # Phase 2+ 감성 피처 실험 시 활성화
# praw >= 7.7.0                 # 미정 — Reddit 수집 필요 시 추가
xgboost >= 2.0.0
lightgbm >= 4.3.0
scikit-learn >= 1.4.0
python-binance >= 1.0.19
redis >= 5.0.0
asyncpg >= 0.29.0
pandas >= 2.2.0
TA-Lib >= 0.4.28              # brew install ta-lib 선행 필요
numpy >= 1.26.0
structlog >= 24.0.0
prometheus-client >= 0.20.0
pydantic >= 2.6.0
httpx >= 0.27.0
pytest >= 8.0.0
pytest-asyncio >= 0.23.0
```

---

## 5. 폴더 구조

```
alpha-agents/
├── PLAN.md                          # 이 파일
├── README.md
├── pyproject.toml                   # uv / poetry 의존성 관리
├── .env.example                     # 환경변수 템플릿
├── docker-compose.yml               # 로컬 인프라 (Redis, PostgreSQL)
│
├── agents/                          # 에이전트 모듈
│   ├── __init__.py
│   ├── base.py                      # BaseAgent 추상 클래스
│   ├── data_agent/
│   │   ├── __init__.py
│   │   ├── agent.py                 # DataAgent LangGraph 노드
│   │   ├── collectors/
│   │   │   ├── ohlcv.py             # Binance WebSocket OHLCV
│   │   │   └── onchain.py           # Glassnode 수집기
│   │   └── storage.py               # PostgreSQL 저장 인터페이스
│   │   # Phase 2+: news.py (newszips 연동) 추가 예정
│   │
│   ├── analysis_agent/
│   │   ├── __init__.py
│   │   ├── agent.py                 # AnalysisAgent LangGraph 노드
│   │   # Phase 2+: sentiment.py (KR-FinBERT) 추가 예정
│   │   └── technical.py             # pandas-ta 기술적 분석
│   │
│   ├── strategy_agent/
│   │   ├── __init__.py
│   │   ├── agent.py                 # StrategyAgent LangGraph 노드
│   │   ├── xgb_model.py             # XGBoost 모델 추론 인터페이스
│   │   ├── feature_builder.py       # 피처 엔지니어링 (지표 → 모델 입력)
│   │   ├── labeler.py               # 미래 수익률 기반 레이블 자동 생성
│   │   └── trainer.py               # XGBoost 학습 및 재학습 스케줄러
│   │
│   ├── risk_agent/
│   │   ├── __init__.py
│   │   ├── agent.py                 # RiskAgent LangGraph 노드
│   │   ├── rules.py                 # 규칙 기반 리스크 체크
│   │   └── circuit_breaker.py       # 서킷 브레이커 로직
│   │
│   └── execution_agent/
│       ├── __init__.py
│       ├── agent.py                 # ExecutionAgent LangGraph 노드
│       ├── binance_client.py        # Binance AsyncClient 래퍼
│       ├── order_manager.py         # 주문 생성/취소/조회
│       └── position_tracker.py      # 실시간 포지션 추적
│
├── graph/
│   ├── __init__.py
│   ├── state.py                     # AgentState TypedDict 정의
│   ├── graph.py                     # LangGraph StateGraph 빌더
│   └── runner.py                    # 그래프 실행 진입점
│
├── storage/
│   ├── __init__.py
│   ├── qdrant_manager.py            # Qdrant 컬렉션 관리
│   ├── postgres_manager.py          # PostgreSQL 연결 풀
│   └── redis_manager.py             # Redis Streams / 캐시
│
├── backtest/
│   ├── __init__.py
│   ├── engine.py                    # 백테스트 실행 엔진
│   ├── data_loader.py               # 히스토리컬 데이터 로더
│   ├── metrics.py                   # 성과 지표 계산 (Sharpe, MDD 등)
│   └── visualizer.py                # 결과 시각화
│
├── monitoring/
│   ├── dashboard.py                 # Grafana 대시보드 설정
│   ├── prometheus_metrics.py        # 커스텀 메트릭 정의
│   └── alerting.py                  # 알림 규칙
│
├── config/
│   ├── __init__.py
│   ├── settings.py                  # Pydantic Settings (환경변수 로드)
│   └── risk_params.py               # 리스크 파라미터 설정
│
├── tests/
│   ├── unit/
│   │   ├── test_data_agent.py
│   │   ├── test_analysis_agent.py
│   │   ├── test_strategy_agent.py
│   │   ├── test_risk_agent.py
│   │   └── test_execution_agent.py
│   ├── integration/
│   │   ├── test_graph_flow.py
│   │   └── test_binance_paper.py
│   └── backtest/
│       └── test_historical_performance.py
│
└── scripts/
    ├── init_db.py                   # DB 초기화 스크립트
    ├── train_xgb.py                 # XGBoost 초기 학습 스크립트
    ├── backfill_ohlcv.py            # Binance 히스토리컬 OHLCV 백필
    └── paper_trade.py               # 페이퍼 트레이딩 실행 스크립트
```

---

## 6. 개발 단계별 로드맵

### Phase 1: 기반 구축 (4주)

**목표:** 인프라 세팅, 데이터 파이프라인, 히스토리컬 데이터 확보

**주요 작업:**
- [ ] Docker Compose 로컬 환경 구성 (PostgreSQL, Redis)
- [ ] `config/settings.py` Pydantic 설정 구조 및 `.env` 스키마 정의
- [ ] `storage/` 모듈 구현 (PostgreSQL 연결 풀, Redis 연결)
- [ ] Binance 히스토리컬 OHLCV 백필 스크립트 구현 (최소 90일치)
- [ ] 데이터 수집기 구현 (OHLCV, 온체인)
- [ ] LangGraph `AgentState` 스키마 정의
- [ ] 단위 테스트 작성 (데이터 Agent)

**완료 기준:**
- OHLCV + 온체인 데이터 PostgreSQL에 자동 저장됨
- 히스토리컬 90일치 백필 완료
- 데이터 수집 파이프라인 안정적으로 24시간 운영

---

### Phase 2: 분석 + 전략 Agent (5주)

**목표:** 분석 Agent 구현, XGBoost 전략 모델 구축, 백테스트 시스템 완성

**주요 작업:**
- [ ] 분석 Agent — ta-lib 기술적 지표 계산 모듈
- [ ] LangGraph `Send()` 병렬 분석 노드 연결
- [ ] 피처 엔지니어링 모듈 구현 (`feature_builder.py`)
- [ ] 미래 수익률 기반 레이블 자동 생성 (`labeler.py`)
- [ ] XGBoost + LightGBM 학습 파이프라인 구현
- [ ] 히스토리컬 데이터로 초기 모델 학습 및 검증
- [ ] 백테스트 엔진 구현 및 성과 지표 측정
- [ ] LangGraph 그래프 빌더 (데이터 → 분석 → 전략 흐름)

**완료 기준:**
- 백테스트 환경에서 Buy & Hold 대비 양의 초과 수익률 달성
- XGBoost 분류 정확도 > 55% (홀드아웃 14일 기준)

> **Phase 2 추가 실험:** newszips KR-FinBERT 감성 피처 추가 → 정확도 개선 여부 A/B 테스트

---

### Phase 3: 리스크 + 실행 Agent + 페이퍼 트레이딩 (4주)

**목표:** 안전장치 구현, 실제 API 연동, 페이퍼 트레이딩 검증

**주요 작업:**
- [ ] 리스크 Agent — 규칙 기반 검사 모듈 구현
- [ ] 리스크 Agent — 서킷 브레이커 및 포지션 강제 청산 로직
- [ ] 실행 Agent — Binance AsyncClient 래퍼 구현
- [ ] 실행 Agent — 주문 유형 전략 (Market → Limit 폴백)
- [ ] WebSocket 체결 확인 및 포지션 트래커 구현
- [ ] 전체 LangGraph 그래프 통합 (5개 에이전트 연결)
- [ ] Grafana 대시보드 구성 (PnL, 포지션, 신호 강도, 리스크 메트릭)
- [ ] 페이퍼 트레이딩 2주 이상 운영
- [ ] XGBoost 자동 재학습 스케줄러 구현
- [ ] minikube 또는 k3d 로컬 K8s 환경 구성 검토

**완료 기준:**
- 페이퍼 트레이딩 2주 운영 중 시스템 에러 없음
- 리스크 서킷 브레이커 시나리오 테스트 통과
- MDD 제어 확인 (시뮬레이션 시 < 15%)

---

### Phase 4: 프로덕션 + 지속 학습 (지속)

**목표:** 소액 라이브 트레이딩 시작, 지속적 개선 체계 구축

**주요 작업:**
- [ ] Kubernetes 배포 설정 (에이전트별 독립 Pod)
- [ ] 소액 라이브 트레이딩 시작 ($500 이하 초기 자본)
- [ ] 모델 드리프트 탐지 및 자동 재학습 파이프라인 완성
- [ ] A/B 테스트 프레임워크 (구 정책 vs 신 정책 비교)
- [ ] 알림 채널 결정 (텔레그램 권장 — 무료, 봇 API 간단) 및 연동
- [ ] 거래 분석 리포트 자동 생성 (일간/주간)
- [ ] 대상 자산 확장 (ETH, SOL, BNB 등)
- [ ] 멀티 타임프레임 전략 실험 (초단타 1m, 스윙 4h 병행)

**완료 기준:**
- 3개월 라이브 Sharpe Ratio > 1.0
- MDD < 15% 유지
- 시스템 가용성 > 99%

---

## 7. 리스크 및 고려사항

### 7.1 기술적 리스크

| 리스크 | 가능성 | 영향 | 대응 방안 |
|-------|-------|------|---------|
| XGBoost 오버피팅 | 중간 | 높음 | 롤링 윈도우 학습, 홀드아웃 검증, early stopping, out-of-sample 테스트 |
| 시장 레짐 변화 (Regime Shift) | 중간 | 높음 | 변동성 지표 기반 정책 전환, 드리프트 탐지 후 자동 재학습 |
| Binance API 장애 | 낮음 | 매우 높음 | 지수 백오프 재시도, 포지션 긴급 청산 폴백, WebSocket 재연결 |
| FinBERT 추론 지연 | 낮음 | 낮음 | VADER 룰베이스 폴백, 캐시된 최근 분석 결과 사용 |
| 데이터 소스 단절 | 중간 | 중간 | 다중 소스 병렬화, 소스 다운 시 가중치 재분배 |
| 슬리피지 과소 평가 | 높음 | 중간 | 백테스트에 현실적 슬리피지 모델 적용 (OHLCV 기반) |

### 7.2 운용 리스크

**자본 관리 원칙:**
- 초기 라이브 자본은 $500 이하로 제한 (Phase 4 초기)
- 시스템이 3개월 연속 목표 Sharpe > 1.0 달성 시 증자 검토
- 전체 자산의 50% 이상을 자동화 시스템에 맡기지 않음

**블랙 스완 이벤트:**
- 서킷 브레이커가 MDD 15% 초과 시 전량 청산 후 수동 검토 절차 수립
- 크립토 시장 전체 급락 (> 30%) 시 자동 트레이딩 일시 정지 규칙

**규제 리스크:**
- 거주 국가의 암호화폐 자동 거래 관련 법령 확인 필수
- 세금 신고용 거래 기록 완전 보관 (PostgreSQL 영구 보존)

### 7.3 개발 리스크

**AI/ML 비용:**
- 감성 분석: FinBERT 로컬 실행 — API 비용 없음
- 임베딩: `BAAI/bge-small-en-v1.5` 로컬 실행 — API 비용 없음
- 필요 시 LLM 도입 기준: FinBERT 정확도가 지속적으로 부족하다고 판단될 때만 검토

**데이터 비용:**
- Reddit API (PRAW): 무료
- Glassnode: 기본 지표 무료, 고급 온체인 지표 Standard 플랜 ($29/월)

**개발 속도:**
- XGBoost 학습은 M2에서 수 분 내 완료 — GPU 불필요
- ta-lib C 라이브러리 설치 (brew install ta-lib) 필요 — 최초 환경 세팅 시 확인

### 7.4 중요 원칙

1. **백테스트 과신 금지:** 백테스트 수익률은 실거래와 반드시 다르다. 페이퍼 트레이딩을 최소 1개월 운영 후 라이브 전환.
2. **설명 가능성 유지:** 모든 매매 결정에 대해 근거 로그를 반드시 저장. 블랙박스 운용 금지.
3. **점진적 배포:** 소액 → 중간 금액 순서로 증자. 성과 검증 없는 급격한 증자 금지.
4. **인간 감독 유지:** 일간 리포트를 사람이 반드시 검토. 이상 감지 시 수동 개입 절차 문서화.
5. **재현성 보장:** 모든 XGBoost 모델 버전과 학습 데이터셋을 git + DVC로 버전 관리.

---

*이 문서는 시스템 개발 진행에 따라 지속적으로 업데이트됩니다.*
*최종 수정: 2026-03-27 (critic review 반영)*
