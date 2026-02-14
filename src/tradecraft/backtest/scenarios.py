from __future__ import annotations

from dataclasses import dataclass

from tradecraft.backtest.engine import BacktestConfig


@dataclass(frozen=True)
class ScenarioPreset:
    key: str
    label: str
    description: str
    drift_bps: float
    volatility_bps: float
    fee_multiplier: float = 1.0
    slippage_multiplier: float = 1.0


_PRESETS: dict[str, ScenarioPreset] = {
    "baseline": ScenarioPreset(
        key="baseline",
        label="기본",
        description="중립 드리프트 + 보통 변동성",
        drift_bps=0.2,
        volatility_bps=18.0,
    ),
    "bull_trend": ScenarioPreset(
        key="bull_trend",
        label="상승 추세",
        description="우상향 드리프트 + 낮은 변동성",
        drift_bps=1.8,
        volatility_bps=12.0,
    ),
    "bear_trend": ScenarioPreset(
        key="bear_trend",
        label="하락 추세",
        description="하향 드리프트 + 보통 변동성",
        drift_bps=-1.8,
        volatility_bps=18.0,
    ),
    "high_vol": ScenarioPreset(
        key="high_vol",
        label="고변동",
        description="중립 드리프트 + 고변동성",
        drift_bps=0.1,
        volatility_bps=45.0,
        slippage_multiplier=1.4,
    ),
    "fee_stress": ScenarioPreset(
        key="fee_stress",
        label="수수료 스트레스",
        description="수수료/슬리피지 가중치 2배",
        drift_bps=0.2,
        volatility_bps=18.0,
        fee_multiplier=2.0,
        slippage_multiplier=2.0,
    ),
}


def list_scenarios() -> list[dict[str, object]]:
    return [
        {
            "key": item.key,
            "label": item.label,
            "description": item.description,
            "drift_bps": item.drift_bps,
            "volatility_bps": item.volatility_bps,
            "fee_multiplier": item.fee_multiplier,
            "slippage_multiplier": item.slippage_multiplier,
        }
        for item in _PRESETS.values()
    ]


def apply_scenario(config: BacktestConfig, key: str) -> BacktestConfig:
    preset = _PRESETS.get((key or "").strip().lower())
    if not preset:
        return config

    config.drift_bps = preset.drift_bps
    config.volatility_bps = preset.volatility_bps
    config.fee_rate = max(config.fee_rate * preset.fee_multiplier, 0.0)
    config.slippage_bps = max(config.slippage_bps * preset.slippage_multiplier, 0.0)
    return config
