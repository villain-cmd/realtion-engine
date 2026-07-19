from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Policy:
    schema_version: int = 2
    objective: str = "CONTRIBUTION_PROFIT"
    target_roas_pct: float = 450.0
    default_margin_rate: float = 0.35
    allow_assumed_margin_for_simulation: bool = True
    allow_assumed_margin_for_upload: bool = False
    other_promo_cost_rate_default: float = 0.0
    profit_safety_buffer_rate: float = 0.10

    min_cpc_default: float = 24.0
    max_cpc_default: float = 100.0
    max_raise_pct: float = 0.50
    max_down_pct: float = 0.50
    max_absolute_change_yen: float = 30.0
    deadband_pct: float = 0.10
    deadband_yen: float = 3.0
    round_mode: str = "CEIL"

    short_window_days: int = 7
    long_window_days: int = 28
    min_clicks_for_judgement: int = 10
    no_order_stop_clicks: int = 50
    new_product_observation_days: int = 14
    change_observation_days: int = 7
    bayes_prior_clicks: float = 20.0

    data_missing_block_rate: float = 0.01
    row_count_drop_block_rate: float = 0.20
    anomaly_min_clicks: int = 20
    anomaly_cvr_drop_rate: float = 0.50
    anomaly_roas_drop_rate: float = 0.50
    anomaly_cpc_rise_rate: float = 0.50
    anomaly_required_signals: int = 2

    auto_ready_scale_down: bool = True
    auto_ready_stop: bool = True
    scale_up_requires_manual: bool = True
    auto_rollback: bool = False

    platform: str = "rakuten"
    attribution_hours_rakuten: int = 720
    attribution_hours_yahoo: int = 24

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "Policy":
        if not raw:
            return cls()
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in raw.items() if k in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
