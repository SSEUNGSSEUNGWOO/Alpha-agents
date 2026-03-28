import pickle
import io
from pathlib import Path
import numpy as np
import pandas as pd
from agents.strategy_agent.feature_builder import FEATURE_COLS, BTC_FEATURE_COLS

MODEL_DIR = Path("models")
LABEL_MAP  = {0: "SELL", 1: "HOLD", 2: "BUY"}

_models: dict = {}


async def _load_from_db(symbol: str):
    """DB에서 가장 최신 모델 로드 (없으면 None)"""
    try:
        from storage import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT model_data FROM models WHERE symbol=$1 ORDER BY trained_at DESC LIMIT 1",
                symbol,
            )
        if row:
            return pickle.loads(bytes(row["model_data"]))
    except Exception:
        pass
    return None


def _load_from_file(symbol: str):
    """파일에서 모델 로드 (fallback)"""
    path = MODEL_DIR / f"xgb_{symbol.lower()}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"모델 없음: {path}. 먼저 trainer.py 실행 필요.")
    with open(path, "rb") as f:
        return pickle.load(f)


def load_model(symbol: str):
    """캐시 → 파일 순으로 동기 로드 (startup 시 사용)"""
    if symbol not in _models:
        _models[symbol] = _load_from_file(symbol)
    return _models[symbol]


async def load_model_async(symbol: str):
    """캐시 → DB → 파일 순으로 로드"""
    if symbol not in _models:
        model = await _load_from_db(symbol)
        if model is None:
            model = _load_from_file(symbol)
        _models[symbol] = model
    return _models[symbol]


def reload_model(symbol: str) -> None:
    """재학습 후 메모리 캐시 갱신용"""
    if symbol in _models:
        del _models[symbol]


def predict(symbol: str, signals: dict) -> dict:
    model = load_model(symbol)

    feat_cols = BTC_FEATURE_COLS if symbol == "BTCUSDT" else FEATURE_COLS
    features  = pd.DataFrame([{col: signals.get(col, 0.0) for col in feat_cols}])
    proba     = model.predict_proba(features)[0]
    pred_idx  = int(np.argmax(proba))

    return {
        "action":     LABEL_MAP[pred_idx],
        "confidence": float(proba[pred_idx]),
        "proba":      {LABEL_MAP[i]: float(p) for i, p in enumerate(proba)},
    }
