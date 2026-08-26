from __future__ import annotations

import json
import time as _time
from datetime import date, datetime, time as dtime
from pathlib import Path


def _now_monotonic() -> float:
    return _time.monotonic()

import pandas as pd

from src.config import Settings, project_root
from src.data import ingest_yf
from src.data.calendar import all_holidays, weekly_expiry_on_or_after
from src.events import EventRisk
from src.models.black_scholes import bs_price
from src.risk import RiskManager


def ist_now() -> datetime:
    return datetime.now().astimezone()


def tmin(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class Orchestrator:
    def __init__(self, settings: Settings, broker, quote_fn, vix_fn=None):
        self.cfg = settings.raw
        self.broker = broker
        self.quote_fn = quote_fn
        self.vix_fn = vix_fn or self._vix_from_cache
        self.rm = RiskManager(self.cfg["risk"], None)
        self.state_path = project_root() / "logs" / "trinity_session_state.json"
        self.state = self._load_state()
        self.lot = self.cfg["universe"]["lot_size"]
        self.step = self.cfg["universe"]["strike_step"]
        self.seg = self.cfg["universe"]["exchange"]
        self.spot_sid = self.cfg["universe"]["underlying_security_id"]
        ev_cfg = self.cfg.get("events", {})
        self.events = None
        if ev_cfg.get("enabled"):
            cal = project_root() / ev_cfg.get("calendar_file", "config/events.yaml")
            self.events = EventRisk(cal, ev_cfg)
        self._news_cache: tuple[float, dict | None] | None = None

    def _load_state(self) -> dict:
        p = self.state_path
        if p.exists():
            saved = json.loads(p.read_text())
            if saved.get("date") == str(datetime.now().astimezone().date()):
                return saved
        return {"date": str(datetime.now().astimezone().date()), "positions": {}, "flags": {}, "realized": 0.0}

    def save_state(self):
        self.state["ts"] = datetime.now().isoformat()
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def _vix_from_cache(self) -> float:
        try:
            df = ingest_yf.load_daily("indiavix")
            sub = df[df.index < pd.Timestamp(date.today())]
            return float(sub["close"].iloc[-1])
        except Exception:
            return float("nan")

    def spot(self) -> float:
        return self.quote_fn(self.seg, self.spot_sid)

    def round_strike(self, px: float) -> int:
        return int(round(px / self.step) * self.step)

    def today(self) -> date:
        return ist_now().date()

    def is_expiry_day(self) -> bool:
        today = self.today()
        return weekly_expiry_on_or_after(today, all_holidays()) == today

    def week_key(self) -> tuple:
        return date.today().isocalendar()[:2]

    def kill_switch_tripped(self) -> bool:
        eq = self.broker.equity()
        if not hasattr(self, "_day_start_equity"):
            self._day_start_equity = eq
        dd_pct = -(eq - self._day_start_equity) / max(self._day_start_equity, 1) * 100
        limit = self.cfg["risk"]["daily_loss_kill_pct"]
        if dd_pct >= limit:
            self.broker.log_event("kill_switch", {"dd_pct": dd_pct})
            return True
        return False

    def _news_service_status(self) -> dict | None:
        """Consult the 24/7 news monitor (port 8765). Cached 30s, fails open to
        the local EventRisk poller if the service is down."""
        url = str(self.cfg.get("events", {}).get("news_service_url", "")).strip()
        if not url:
            return None
        now = _now_monotonic()
        if self._news_cache and now - self._news_cache[0] < 30:
            return self._news_cache[1]
        try:
            import httpx
            r = httpx.get(f"{url}/status?minutes=45", timeout=2.0)
            st = r.json() if r.status_code == 200 else None
        except Exception:
            st = None
        self._news_cache = (now, st)
        return st

    def _assess_events(self, now) -> dict:
        st = self._news_service_status()
        if st is not None:
            return {"block_entries": bool(st.get("block_entries")),
                    "force_flat": bool(st.get("force_flat")),
                    "reason": st.get("reason")}
        if self.events:
            return self.events.assess(now)
        return {"block_entries": False, "force_flat": False, "reason": None}

    def run_once(self) -> dict:
        now = ist_now()
        cur = now.hour * 60 + now.minute
        actions = []
        s = self.state

        if self.kill_switch_tripped():
            self.force_flat("kill_switch")
            return {"actions": ["KILL_SWITCH_FLAT"], "state": s}

        if s["flags"].get("flat_for_day"):
            return {"actions": [], "state": s}

        ev = self._assess_events(now)
        if ev["force_flat"]:
            self.force_flat(ev["reason"])
            self.broker.log_event("event_force_flat", {"reason": ev["reason"]})
            return {"actions": [f"EVENT_FLAT({ev['reason']})"], "state": s}
        entries_ok = not ev["block_entries"]
        if not entries_ok and s["flags"].get("event_reason") != ev["reason"]:
            s["flags"]["event_reason"] = ev["reason"]
            self.broker.log_event("event_block", {"reason": ev["reason"]})
            actions.append(f"EVENT_PAUSE({ev['reason']})")
        if entries_ok:
            s["flags"].pop("event_reason", None)

        gap_cfg = self.cfg["strategies"]["gap_reversion"]
        ify_cfg = self.cfg["strategies"]["iron_fly_expiry"]
        on_cfg = self.cfg["strategies"]["overnight_drift"]

        if entries_ok and tmin(gap_cfg.get("entry_time", "09:18")) <= cur < tmin("09:30") \
                and not s["flags"].get("gap_done") and gap_cfg.get("enabled", True):
            act = self.try_gap_entry()
            s["flags"]["gap_done"] = True
            actions.append(act)

        if entries_ok and self.is_expiry_day() and tmin(ify_cfg["entry_time"]) <= cur < tmin(ify_cfg["entry_time"]) + 10 \
                and not s["flags"].get("if_done") and ify_cfg.get("enabled", True):
            act = self.try_iron_fly_entry()
            s["flags"]["if_done"] = True
            actions.append(act)

        if s["positions"].get("iron_fly"):
            act = self.manage_iron_fly(cur)
            if act:
                actions.append(act)

        if s["positions"].get("gap"):
            act = self.manage_gap(cur)
            if act:
                actions.append(act)

        if entries_ok and on_cfg.get("enabled", True):
            if cur >= tmin(on_cfg.get("entry_time", "15:22")) and cur < tmin("15:29") \
                    and not s["flags"].get("on_entered") and self.overnight_gate_passes():
                act = self.try_overnight_entry()
                s["flags"]["on_entered"] = True
                actions.append(act)

        if cur >= tmin(self.cfg["session"]["square_off"]):
            self.force_flat("eod")
            actions.append("EOD_FLAT")

        self.save_state()
        return {"actions": actions, "state": s}

    def overnight_gate_passes(self) -> bool:
        on_cfg = self.cfg["strategies"]["overnight_drift"]
        if self.broker.equity() < float(on_cfg.get("min_equity", 2_000_000)):
            return False
        vix = self.vix_fn()
        gate = float(on_cfg.get("vix_min_gate", 17.0))
        return vix >= gate if vix == vix else False

    def resolve_option_ids(self, atm: int, wing: int) -> dict | None:
        c = getattr(self.broker, "client", None)
        if c is None:
            return None
        try:
            expiries = c.expiry_list(self.spot_sid)
            if not expiries:
                return None
            chain = c.option_chain(self.spot_sid, expiries[0])
        except Exception:
            return None
        oc = chain.get("data", {}).get("oc", {})
        wanted = {
            atm: ["ce", "pe"],
            atm + wing: ["ce"],
            atm - wing: ["pe"],
        }
        roles = {}
        for strike_str, node in oc.items():
            try:
                k = int(float(strike_str))
            except ValueError:
                continue
            if k not in wanted:
                continue
            for side in wanted[k]:
                info = node.get(side) or {}
                px = float(info.get("last_price") or 0)
                sid = str(info.get("security_id") or "")
                if not sid or px <= 0:
                    continue
                role = {(atm, "ce"): "short_ce", (atm, "pe"): "short_pe",
                        (atm + wing, "ce"): "long_ce", (atm - wing, "pe"): "long_pe"}[(k, side)]
                roles[role] = {"sid": sid, "k": k, "ltp": px}
        return roles if len(roles) == 4 else None

    def try_iron_fly_entry(self) -> str:
        spot = self.spot()
        if spot <= 0:
            return "IF_SKIP_NO_SPOT"
        vix = self.vix_fn()
        p = self.cfg["strategies"]["iron_fly_expiry"]
        if vix > self.cfg["risk"].get("vix_max_for_theta", 22):
            self.state["flags"]["if_reason"] = f"vix_high_{vix}"
            return f"IF_SKIP_VIX_{vix:.1f}"
        atm = self.round_strike(spot)
        wing = int(p["wing_width"])
        iv = max(vix * float(p["iv_to_vix_mult"]), 4.0) / 100.0
        t = 325.0 / (252 * 375)
        est_credit = (bs_price(spot, atm, t, iv, kind="CE") +
                      bs_price(spot, atm, t, iv, kind="PE") -
                      bs_price(spot, atm + wing, t, iv, kind="CE") -
                      bs_price(spot, atm - wing, t, iv, kind="PE"))
        if est_credit < float(p["min_credit_pts"]):
            self.state["flags"]["if_reason"] = f"thin_{est_credit:.0f}"
            return f"IF_SKIP_THIN_{est_credit:.0f}pts"

        legs = self.resolve_option_ids(atm, wing)
        if legs is None:
            self.state["flags"]["if_reason"] = "no_chain"
            return "IF_SKIP_NO_CHAIN"

        qty_lots = 1
        entry = {}
        for role, meta in legs.items():
            r = self.broker.market_order(self.seg, meta["sid"], qty_lots * self.lot,
                                         "SELL" if role.startswith("short") else "BUY",
                                         tag=f"IF:{role}")
            entry[role] = {"sid": meta["sid"], "px": r["px"]}
        self.state["positions"]["iron_fly"] = {
            "atm": atm, "wing": wing, "legs": entry,
            "credit": float(sum(l["px"] for n, l in entry.items() if n.startswith("short")) -
                            sum(l["px"] for n, l in entry.items() if n.startswith("long"))),
            "qty_per_leg": qty_lots * self.lot,
            "entered_at": datetime.now().isoformat(),
        }
        self.broker.log_event("iron_fly_entry", self.state["positions"]["iron_fly"])
        return f"IF_ENTRY@{atm}"

    def manage_iron_fly(self, cur: int) -> str | None:
        p = self.cfg["strategies"]["iron_fly_expiry"]
        pos = self.state["positions"]["iron_fly"]
        spot = self.spot()
        val = 0.0
        for role, leg in pos["legs"].items():
            px = self.quote_fn(self.seg, leg["sid"])
            leg["last_px"] = px
            val += px if role.startswith("short") else -px
        credit = pos["credit"]
        if credit <= 0:
            return None
        buffer_pts = float(p.get("buffer_pts", 30))
        breach = spot >= pos["atm"] + buffer_pts or spot <= pos["atm"] - buffer_pts
        if val >= credit * float(p["stop_mult"]) or breach:
            self.close_iron_fly("stop")
            return "IF_STOP"
        if val <= credit * float(p["target_keep_pct"]):
            self.close_iron_fly("target")
            return "IF_TARGET"
        if cur >= tmin(p["exit_time"]):
            self.close_iron_fly("time")
            return "IF_TIME_EXIT"
        return None

    def close_iron_fly(self, reason: str):
        pos = self.state["positions"]["iron_fly"]
        for role, leg in pos["legs"].items():
            self.broker.market_order(self.seg, leg["sid"], pos["qty_per_leg"],
                                     "BUY" if role.startswith("short") else "SELL",
                                     tag=f"IF:exit:{reason}")
        self.broker.log_event("iron_fly_exit", {"reason": reason, **pos})
        del self.state["positions"]["iron_fly"]

    def try_gap_entry(self) -> str:
        try:
            nifty = ingest_yf.load_daily("nifty")
        except Exception:
            return "GAP_SKIP_NO_DATA"
        if nifty.index[-1] >= pd.Timestamp(date.today()):
            hist = nifty[nifty.index < pd.Timestamp(date.today())]
        else:
            hist = nifty
        if len(hist) < 20:
            return "GAP_SKIP_SHORT_HISTORY"
        prev_close = float(hist["close"].iloc[-1])
        open_px = self.spot()
        if open_px <= 0 or prev_close <= 0:
            return "GAP_SKIP_NO_PX"
        atr = float((hist["high"] - hist["low"]).tail(14).mean())
        gap_atr = (open_px / prev_close - 1) / (atr / prev_close)
        thr = float(self.cfg["strategies"]["gap_reversion"]["fade_gap_atr"])
        if abs(gap_atr) < thr:
            self.state["flags"]["gap_reason"] = f"small_gap_{gap_atr:.2f}"
            return f"GAP_NONE({gap_atr:.2f})"
        direction = -1 if gap_atr > 0 else 1
        fut_sid = self.cfg["universe"].get("fut_security_id")
        if not fut_sid:
            return "GAP_SKIP_NO_FUT_ID"
        self.broker.market_order(self.seg, fut_sid, self.lot,
                                 "BUY" if direction > 0 else "SELL", tag="GAP:entry")
        self.state["positions"]["gap"] = {
            "dir": direction, "entry": open_px, "prev_close": prev_close,
            "stop": open_px - direction * atr,
            "target": prev_close, "sid": fut_sid,
        }
        self.broker.log_event("gap_entry", self.state["positions"]["gap"])
        return f"GAP_FADE_{'LONG' if direction>0 else 'SHORT'}"

    def manage_gap(self, cur: int) -> str | None:
        pos = self.state["positions"].get("gap")
        if not pos:
            return None
        px = self.quote_fn(self.seg, pos["sid"])
        hit_stop = px <= pos["stop"] if pos["dir"] > 0 else px >= pos["stop"]
        hit_target = px >= pos["target"] if pos["dir"] > 0 else px <= pos["target"]
        if hit_stop or hit_target or cur >= tmin("15:15"):
            reason = "stop" if hit_stop else ("target" if hit_target else "time")
            self.broker.market_order(self.seg, pos["sid"], self.lot,
                                     "SELL" if pos["dir"] > 0 else "BUY", tag=f"GAP:{reason}")
            self.broker.log_event("gap_exit", {"reason": reason, **pos})
            del self.state["positions"]["gap"]
            return f"GAP_{reason.upper()}"
        return None

    def try_overnight_entry(self) -> str:
        fut_sid = self.cfg["universe"].get("fut_security_id")
        if not fut_sid:
            return "ON_SKIP_NO_FUT_ID"
        r = self.broker.market_order(self.seg, fut_sid, self.lot, "BUY", tag="ON:entry")
        self.state["positions"]["overnight"] = {"sid": fut_sid, "entry": r["px"],
                                                "date": str(date.today())}
        self.broker.log_event("overnight_entry", self.state["positions"]["overnight"])
        return "ON_ENTRY"

    def force_flat(self, why: str):
        for key in list(self.state["positions"].keys()):
            pos_like = self.state["positions"][key]
            if key == "iron_fly":
                self.close_iron_fly(why)
            elif key in ("gap", "overnight"):
                sid = pos_like["sid"]
                dir_mult = 1 if pos_like.get("dir", 1) > 0 else 1
                held_side_long = key == "overnight" or pos_like.get("dir", 1) > 0
                self.broker.market_order(self.seg, sid, self.lot,
                                         "SELL" if held_side_long else "BUY", tag=f"{key}:{why}")
                self.broker.log_event(f"{key}_exit", {"reason": why})
                del self.state["positions"][key]
        self.state["flags"]["flat_for_day"] = True
