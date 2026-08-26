import sys

from src.atlas.sweep import run_sweep

if __name__ == "__main__":
    df = run_sweep()
    pd = __import__("pandas")
    pd.set_option("display.width", 220)
    cols = ["strategy", "params", "score", "full_sharpe", "is_sharpe", "oos_sharpe",
            "oos_cagr_pct", "oos_max_dd_pct", "oos_pf", "trade_days_full",
            "quarters_hit"]
    print(df[cols].head(20).to_string(index=False, max_colwidth=48))
    sys.exit(0)
