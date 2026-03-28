"""
주간 자동 재학습 스케줄러
- 매주 일요일 00:00 UTC 실행
- 슬라이딩 윈도우: 최근 90일 데이터
- 새 모델 F1 > 기존 모델 F1 이면 DB에 저장 + 메모리 핫스왑
- 아니면 기존 모델 유지 (성능 하락 방지)
"""
import asyncio
import logging
import pickle
import io
from datetime import datetime, timezone, timedelta

import numpy as np
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score, accuracy_score
from sklearn.utils.class_weight import compute_sample_weight

log = logging.getLogger("weekly-retrain")

WINDOW_DAYS = 90   # 슬라이딩 윈도우 크기


async def _get_current_f1(symbol: str) -> float:
    """DB에 저장된 최신 모델의 F1 점수 조회"""
    try:
        from storage import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT f1_macro FROM models WHERE symbol=$1 ORDER BY trained_at DESC LIMIT 1",
                symbol,
            )
        return float(row["f1_macro"]) if row and row["f1_macro"] else 0.0
    except Exception:
        return 0.0


async def _save_model(symbol: str, model, accuracy: float, f1: float) -> None:
    data = pickle.dumps(model)
    from storage import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO models (symbol, trained_at, window_days, accuracy, f1_macro, model_data)
            VALUES ($1, NOW(), $2, $3, $4, $5)
            ON CONFLICT (symbol, trained_at) DO NOTHING
            """,
            symbol, WINDOW_DAYS, accuracy, f1, data,
        )


async def retrain_symbol(symbol: str) -> dict:
    from agents.strategy_agent.feature_builder import build_training_data
    from agents.strategy_agent.xgb_model import reload_model

    log.info(f"[{symbol}] 재학습 시작 (최근 {WINDOW_DAYS}일)")

    X, y = await build_training_data(symbol, days=WINDOW_DAYS)
    if len(X) < 500:
        log.warning(f"[{symbol}] 데이터 부족 ({len(X)}개) — 재학습 스킵")
        return {"symbol": symbol, "status": "skipped", "reason": "insufficient data"}

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.15, shuffle=False
    )

    model = xgb.XGBClassifier(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="mlogloss",
        early_stopping_rounds=30,
        random_state=42,
        n_jobs=-1,
    )
    sample_weights = compute_sample_weight("balanced", y_train)
    model.fit(X_train, y_train, sample_weight=sample_weights,
              eval_set=[(X_val, y_val)], verbose=False)

    y_pred   = model.predict(X_val)
    accuracy = float(accuracy_score(y_val, y_pred))
    f1       = float(f1_score(y_val, y_pred, average="macro"))

    # 기존 모델과 비교
    prev_f1 = await _get_current_f1(symbol)

    if f1 >= prev_f1 - 0.01:  # 1% 이내 하락은 허용 (노이즈 방지)
        await _save_model(symbol, model, accuracy, f1)
        reload_model(symbol)  # 메모리 캐시 무효화 → 다음 predict 때 DB에서 새 모델 로드
        status = "updated"
        log.info(f"[{symbol}] 모델 업데이트 ✓  F1: {prev_f1:.3f} → {f1:.3f}  acc: {accuracy:.3f}")
    else:
        status = "kept"
        log.info(f"[{symbol}] 기존 모델 유지  F1: {f1:.3f} < prev {prev_f1:.3f}")

    return {
        "symbol":   symbol,
        "status":   status,
        "f1":       round(f1, 4),
        "prev_f1":  round(prev_f1, 4),
        "accuracy": round(accuracy, 4),
        "samples":  len(X),
    }


async def run_weekly_retrain() -> None:
    """매주 일요일 00:00 UTC에 실행"""
    from config import settings

    while True:
        # 다음 일요일 00:00 UTC까지 대기
        now  = datetime.now(timezone.utc)
        days_until_sunday = (6 - now.weekday()) % 7 or 7  # 0=월 ... 6=일
        next_run = (now + timedelta(days=days_until_sunday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait_secs = (next_run - now).total_seconds()
        log.info(f"다음 재학습: {next_run.strftime('%Y-%m-%d %H:%M UTC')}  ({wait_secs/3600:.1f}시간 후)")
        await asyncio.sleep(wait_secs)

        log.info("=== 주간 자동 재학습 시작 ===")
        results = []
        for symbol in settings.symbols:
            try:
                result = await retrain_symbol(symbol)
                results.append(result)
            except Exception as e:
                log.error(f"[{symbol}] 재학습 오류: {e}")

        updated = [r for r in results if r.get("status") == "updated"]
        log.info(f"=== 재학습 완료: {len(updated)}/{len(results)}개 갱신 ===")
