import pickle
from pathlib import Path
import numpy as np
import pandas as pd
from agents.strategy_agent.feature_builder import FEATURE_COLS, BTC_FEATURE_COLS

MODEL_DIR = Path("models")
LABEL_MAP = {0: "SELL", 1: "HOLD", 2: "BUY"}

_models: dict = {}


def load_model(symbol: str):
    if symbol not in _models:
        path = MODEL_DIR / f"xgb_{symbol.lower()}.pkl"
        if not path.exists():
            raise FileNotFoundError(f"모델 없음: {path}. 먼저 trainer.py 실행 필요.")
        with open(path, "rb") as f:
            _models[symbol] = pickle.load(f)
    return _models[symbol]


def predict(symbol: str, signals: dict) -> dict:
    model = load_model(symbol)

    feat_cols = BTC_FEATURE_COLS if symbol == "BTCUSDT" else FEATURE_COLS
    features = pd.DataFrame([{col: signals.get(col, 0.0) for col in feat_cols}])
    proba = model.predict_proba(features)[0]
    pred_idx = int(np.argmax(proba))

    return {
        "action":     LABEL_MAP[pred_idx],
        "confidence": float(proba[pred_idx]),
        "proba":      {LABEL_MAP[i]: float(p) for i, p in enumerate(proba)},
    }
