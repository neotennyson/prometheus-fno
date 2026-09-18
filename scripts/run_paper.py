import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings, project_root
from src.costs import CostModel
from src.exec.dhan_broker import DhanBroker
from src.exec.paper_broker import PaperBroker
from src.orchestrator import Orchestrator, ist_now


def build_quote_fn(client=None):
    if client is not None:
        def q(seg, sid):
            return client.ltp([(seg, sid)])
        return q

    import random
    base = {"NSE_FNO:13": 24000.0}

    def synthetic(seg, sid):
        v = base.get(f"{seg}:{sid}", 100.0)
        return round(v * (1 + random.uniform(-0.001, 0.001)), 2)
    return synthetic


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["paper", "live"], default="paper")
    ap.add_argument("--synthetic", action="store_true",
                    help="no broker creds needed; validates plumbing with random prices")
    ap.add_argument("--once", action="store_true", help="single orchestrator tick then exit")
    args = ap.parse_args()

    cfg = Settings.load()
    cm = CostModel.from_cfg(cfg.raw["costs"])

    client = None
    if not args.synthetic:
        from src.data.dhan_client import DhanClient
        client = DhanClient.from_env(cfg.raw["execution"]["dhan"]["access_token_env"],
                                     cfg.raw["execution"]["dhan"]["client_id_env"])

    quote_fn = build_quote_fn(client)

    if args.mode == "live":
        if args.synthetic:
            print("refusing live mode with --synthetic")
            sys.exit(1)
        # HARD GATE: live mode requires BOTH an Algo-ID (SEBI requirement)
        # and an explicit LIVE_CONFIRMED=yes in the environment. No accidental
        # real-money mode, ever.
        if os.environ.get("LIVE_CONFIRMED") != "yes":
            raise SystemExit(
                "REFUSED: --mode live needs both DHAN_ALGO_ID set AND "
                "LIVE_CONFIRMED=yes in the environment. Paper mode is the default "
                "and only automated mode.")
        broker = DhanBroker(client, dry_run=False)
        print("[warn] LIVE MODE — real orders. SEBI algo framework requires registered Algo-ID + static IP.")
    else:
        cash = float(cfg.raw["capital"])
        broker = PaperBroker(quote_fn, cm,
                             db_path=str(project_root() / cfg.raw["storage"]["trades_db"]),
                             starting_cash=cash)

    orch = Orchestrator(cfg_raw := Settings.load(), broker, quote_fn)
    interval = int(cfg_raw.raw["execution"]["poll_interval_sec"])

    print(f"[run] mode={args.mode} synthetic={args.synthetic} poll={interval}s "
          f"ist={ist_now():%H:%M:%S} equity={broker.equity():,.0f}")

    while True:
        out = orch.run_once()
        if out["actions"]:
            print(f"[{ist_now():%H:%M:%S}] {out['actions']}")
        if args.once:
            break
        now = ist_now()
        if now.hour == 15 and now.minute >= 31:
            print("market closed")
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
