from __future__ import annotations

import pandas as pd

from src.state_bundle import StateBundle


def test_state_bundle_round_trip():
    state = StateBundle.empty()
    state.applied_settings = pd.DataFrame([{
        "platform": "rakuten", "entity_type": "ITEM", "product_id": "P1", "keyword": "",
        "applied_cpc": 35, "applied_status": "ACTIVE", "effective_at": "2026-07-15T00:00:00+00:00",
        "source": "MODIFY", "run_id": "r1",
    }])
    raw = state.to_zip_bytes()
    restored = StateBundle.from_zip_bytes(raw)
    assert restored.applied_settings.iloc[0]["product_id"] == "P1"
    assert restored.applied_settings.iloc[0]["applied_cpc"] == "35"
