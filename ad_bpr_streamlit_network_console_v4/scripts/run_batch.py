#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.io_utils import read_csv_flexible
from src.output_generator import make_download_payloads
from src.pipeline import build_pipeline, finalize_operator_decisions, update_state_bundle
from src.policy import Policy
from src.state_bundle import StateBundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Profit Network Console v4 バッチ実行")
    parser.add_argument("--reports", nargs="+", required=True)
    parser.add_argument("--setting")
    parser.add_argument("--product-master")
    parser.add_argument("--state")
    parser.add_argument("--policy")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    state = StateBundle.from_zip_bytes(Path(args.state).read_bytes()) if args.state else StateBundle.empty()
    policy = state.policy
    if args.policy:
        policy = Policy.from_dict(json.loads(Path(args.policy).read_text(encoding="utf-8")))

    reports = [read_csv_flexible(p) for p in args.reports]
    setting_df = read_csv_flexible(args.setting).dataframe if args.setting else None
    master_df = read_csv_flexible(args.product_master).dataframe if args.product_master else None

    context = build_pipeline(reports, setting_df, master_df, state, policy)
    finalized = finalize_operator_decisions(context.decisions)
    if context.quality.status == "BLOCK":
        finalized["final_approved"] = False
        finalized["final_changed"] = False
        finalized["upload_eligible"] = False

    for _, (name, data, _) in make_download_payloads(setting_df, finalized, context.run_id).items():
        (out_dir / name).write_bytes(data)

    updated = update_state_bundle(state, context, finalized, policy)
    (out_dir / f"state_bundle_{context.run_id}.zip").write_bytes(updated.to_zip_bytes())
    profile = {
        "run_id": context.run_id,
        "quality_status": context.quality.status,
        "quality_score": context.quality.score,
        "input_hash": context.input_hash,
        "duplicate_input": context.duplicate_input,
        "rows": len(finalized),
        "approved": int(finalized["final_approved"].sum()),
        "changed": int(finalized["final_changed"].sum()),
        "upload_output": int(finalized["upload_eligible"].sum()),
        "unmatched": int((finalized["final_changed"] & ~finalized["upload_match"]).sum()),
        "status_counts": finalized["decision_status"].value_counts().to_dict(),
    }
    (out_dir / f"run_profile_{context.run_id}.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
