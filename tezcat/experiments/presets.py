"""Built-in experiment presets."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tezcat.core.config import (
    AgentGroupConfig,
    AgentType,
    ExperimentConfig,
    MarketConfig,
    MetricsPolicy,
    RegimePolicy,
    ShockConfig,
    ShockTrigger,
    ShockType,
)


def _agents(*specs) -> List[AgentGroupConfig]:
    return [AgentGroupConfig(**s) for s in specs]


def _stable_baseline() -> ExperimentConfig:
    return ExperimentConfig(
        market=MarketConfig(),
        total_steps=1500,
        agents=_agents(
            dict(agent_type=AgentType.NOISE_TRADER, count=20, cash=10_000, inventory=100,
                 trading_frequency=0.55, risk_tolerance=0.5),
            dict(agent_type=AgentType.RETAIL_TRADER, count=10, cash=8_000, inventory=80,
                 trading_frequency=0.4, risk_tolerance=0.45, params={"herding": 0.4}),
            dict(agent_type=AgentType.MOMENTUM_TRADER, count=5, cash=15_000, inventory=120,
                 trading_frequency=0.45, risk_tolerance=0.5, params={"threshold": 0.002}),
            dict(agent_type=AgentType.MEAN_REVERSION_TRADER, count=6, cash=15_000, inventory=120,
                 trading_frequency=0.5, risk_tolerance=0.55, params={"band": 0.008}),
            dict(agent_type=AgentType.MARKET_MAKER, count=4, cash=50_000, inventory=500,
                 trading_frequency=1.0, risk_tolerance=0.7,
                 params={"half_spread": 2.0, "quote_size": 14}),
        ),
        shocks=[
            ShockConfig(shock_id="mild_sentiment", shock_type=ShockType.SENTIMENT_SHOCK,
                        trigger=ShockTrigger(kind="scheduled", step=700), side="sell",
                        magnitude=0.3, duration=60,
                        description="Mild negative sentiment ripple"),
        ],
    )


def _flash_crash() -> ExperimentConfig:
    return ExperimentConfig(
        market=MarketConfig(),
        total_steps=2000,
        agents=_agents(
            dict(agent_type=AgentType.NOISE_TRADER, count=16, cash=10_000, inventory=100,
                 trading_frequency=0.5, risk_tolerance=0.5),
            dict(agent_type=AgentType.RETAIL_TRADER, count=14, cash=8_000, inventory=90,
                 trading_frequency=0.5, risk_tolerance=0.55, params={"herding": 0.85}),
            dict(agent_type=AgentType.MOMENTUM_TRADER, count=7, cash=15_000, inventory=120,
                 trading_frequency=0.55, risk_tolerance=0.6, params={"threshold": 0.0012}),
            dict(agent_type=AgentType.MEAN_REVERSION_TRADER, count=5, cash=30_000, inventory=140,
                 trading_frequency=0.5, risk_tolerance=0.55,
                 params={"band": 0.012, "fundamental_weight": 0.7}),
            dict(agent_type=AgentType.MARKET_MAKER, count=2, cash=40_000, inventory=350,
                 trading_frequency=1.0, risk_tolerance=0.6,
                 params={"half_spread": 2.5, "quote_size": 10}),
        ),
        shocks=[
            ShockConfig(shock_id="whale_dump", shock_type=ShockType.WHALE_ORDER,
                        trigger=ShockTrigger(kind="scheduled", step=800), side="sell",
                        magnitude=3200, duration=45,
                        description="Sustained whale sell program sweeps the bid side"),
            ShockConfig(shock_id="mm_pullout", shock_type=ShockType.MM_WITHDRAWAL,
                        trigger=ShockTrigger(kind="scheduled", step=810),
                        magnitude=1.0, duration=120,
                        description="Market makers withdraw quotes after the dump"),
            ShockConfig(shock_id="panic_sentiment", shock_type=ShockType.SENTIMENT_SHOCK,
                        trigger=ShockTrigger(kind="scheduled", step=815), side="sell",
                        magnitude=0.9, duration=150,
                        description="Fear spreads through retail flow"),
        ],
    )


def _bubble_formation() -> ExperimentConfig:
    return ExperimentConfig(
        market=MarketConfig(),
        total_steps=2200,
        agents=_agents(
            dict(agent_type=AgentType.NOISE_TRADER, count=14, cash=12_000, inventory=100,
                 trading_frequency=0.5, risk_tolerance=0.5),
            dict(agent_type=AgentType.RETAIL_TRADER, count=16, cash=12_000, inventory=60,
                 trading_frequency=0.55, risk_tolerance=0.65, params={"herding": 0.9}),
            dict(agent_type=AgentType.MOMENTUM_TRADER, count=12, cash=20_000, inventory=80,
                 trading_frequency=0.6, risk_tolerance=0.7, params={"threshold": 0.001}),
            dict(agent_type=AgentType.MEAN_REVERSION_TRADER, count=2, cash=15_000, inventory=150,
                 trading_frequency=0.35, risk_tolerance=0.4, params={"band": 0.02}),
            dict(agent_type=AgentType.MARKET_MAKER, count=3, cash=60_000, inventory=500,
                 trading_frequency=1.0, risk_tolerance=0.7,
                 params={"half_spread": 2.0, "quote_size": 12}),
        ),
        shocks=[
            ShockConfig(shock_id="hype_wave", shock_type=ShockType.SENTIMENT_SHOCK,
                        trigger=ShockTrigger(kind="scheduled", step=300), side="buy",
                        magnitude=0.85, duration=500,
                        description="Optimistic sentiment wave starts the bubble"),
            ShockConfig(shock_id="hype_wave_2", shock_type=ShockType.SENTIMENT_SHOCK,
                        trigger=ShockTrigger(kind="scheduled", step=850), side="buy",
                        magnitude=0.7, duration=300,
                        description="Second hype wave sustains the trend"),
            ShockConfig(shock_id="reality_check", shock_type=ShockType.SENTIMENT_SHOCK,
                        trigger=ShockTrigger(kind="scheduled", step=1500), side="sell",
                        magnitude=0.9, duration=350,
                        description="Sentiment reverses; the bubble deflates"),
        ],
    )


PRESETS: Dict[str, Dict[str, Any]] = {
    "stable_baseline": {
        "preset_id": "stable_baseline",
        "name": "Stable Baseline",
        "description": "Normally functioning market: balanced agent ecology, deep books, "
                       "tight spreads, one mild sentiment ripple.",
        "tags": ["baseline", "calm", "control"],
        "build": _stable_baseline,
        "recommended_metrics": ["last_price", "spread", "rolling_volatility"],
    },
    "flash_crash": {
        "preset_id": "flash_crash",
        "name": "Flash Crash",
        "description": "Thin liquidity meets a whale sell order followed by market-maker "
                       "withdrawal and panic sentiment: liquidity collapse and sharp drawdown.",
        "tags": ["crash", "liquidity", "shock"],
        "build": _flash_crash,
        "recommended_metrics": ["last_price", "spread", "depth", "max_drawdown"],
    },
    "bubble_formation": {
        "preset_id": "bubble_formation",
        "name": "Bubble Formation",
        "description": "Momentum-heavy ecology with herding retail flow and hype sentiment "
                       "waves: trend overextension followed by deflation.",
        "tags": ["bubble", "momentum", "herding"],
        "build": _bubble_formation,
        "recommended_metrics": ["last_price", "total_return", "rolling_volatility"],
    },
}


def list_presets() -> List[Dict[str, Any]]:
    out = []
    for p in PRESETS.values():
        cfg: ExperimentConfig = p["build"]()
        out.append({
            "preset_id": p["preset_id"],
            "name": p["name"],
            "description": p["description"],
            "tags": p["tags"],
            "total_steps": cfg.total_steps,
            "agent_summary": {g.agent_type.value: g.count for g in cfg.agents},
            "shock_summary": [
                f"{s.shock_type.value} @ step {s.trigger.step}" for s in cfg.shocks
            ],
        })
    return out


def get_preset(preset_id: str) -> Optional[Dict[str, Any]]:
    p = PRESETS.get(preset_id)
    if p is None:
        return None
    cfg = p["build"]()
    return {
        "preset_id": p["preset_id"],
        "name": p["name"],
        "description": p["description"],
        "tags": p["tags"],
        "recommended_metrics": p["recommended_metrics"],
        "config_template": cfg.model_dump(mode="json"),
    }


def build_config(preset_id: str, overrides: Optional[Dict[str, Any]] = None) -> Optional[ExperimentConfig]:
    p = PRESETS.get(preset_id)
    if p is None:
        return None
    cfg: ExperimentConfig = p["build"]()
    if overrides:
        payload = cfg.model_dump(mode="json")
        payload.update(overrides)
        cfg = ExperimentConfig.model_validate(payload)
    return cfg
