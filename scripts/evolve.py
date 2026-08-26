"""Evolution loop CLI. Usage:
    .venv/bin/python scripts/evolve.py            # test every untested spec
    .venv/bin/python scripts/evolve.py --max 500  # cap combos per generation
"""
import argparse

from src.atlas.evolve import run_generation

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None)
    args = ap.parse_args()
    df = run_generation(max_specs=args.max)
    if len(df):
        import pandas as pd
        pd.set_option("display.width", 220)
        cols = ["family", "params", "filter", "exit", "score", "full_sharpe",
                "is_sharpe", "oos_sharpe", "oos_cagr_pct", "oos_pf",
                "n_trades", "quarters_hit"]
        print(df[cols].head(15).to_string(index=False, max_colwidth=40))
