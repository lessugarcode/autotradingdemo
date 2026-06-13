"""
╔══════════════════════════════════════════════════════════════╗
║     LESSUGAR FUTURES PRO V4 — Multi-Pair Paper Trading      ║
║     Pairs: BTC · ETH · SOL · XAU  |  Timeframe: 5m         ║
║     Strategy: Multi-Confluence Scoring + Dynamic Risk       ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, sys, time, json, re, math, requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime, date, timedelta
from colorama import init, Fore, Style

init(autoreset=True)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# ══════════════════════════════════════════════════════════════
#  KONFIGURASI GLOBAL
# ══════════════════════════════════════════════════════════════
CFG = {
    "timeframe":          "5m",
    "candle_limit":       350,

    # Modal & Risk (shared satu akun untuk semua pair)
    "initial_balance":    1000.0,
    "leverage":           10,
    "risk_per_trade":     0.02,       # 2% modal per trade (agresif)
    "max_position_pct":   0.3,        # Max 30% modal per posisi (multi-pair)
    "max_open_positions": 4,          # Maksimal posisi terbuka bersamaan
    "daily_loss_limit":   0.08,       # Stop jika loss harian > 8% (lebih longgar)
    "max_consec_losses":  4,          # 4 loss berturut-turut baru cooldown
    "cooldown_minutes":   15,         # Cooldown lebih pendek

    # SL / TP / Trailing
    "sl_atr_mult":        1.2,        # SL lebih ketat (entry lebih cepat)
    "tp_rr":              2.0,        # RR 1:2 (TP lebih mudah tercapai)
    "trailing_enabled":   True,
    "trailing_atr_mult":  1.0,

    # Indikator
    "ema_fast":    9,
    "ema_mid":     21,
    "ema_slow":    55,
    "ema_trend":   200,
    "rsi_period":  14,
    "rsi_ob":      65,                # Lebih sensitif (was 68)
    "rsi_os":      35,                # Lebih sensitif (was 32)
    "macd_fast":   12,
    "macd_slow":   26,
    "macd_signal": 9,
    "bb_period":   20,
    "atr_period":  14,
    "vol_ma_period": 20,
    "stoch_k":     14,
    "stoch_ob":    75,                # Lebih sensitif (was 80)
    "stoch_os":    25,                # Lebih sensitif (was 20)

    # Scoring
    "min_score_entry": 3,             # Lebih agresif (was 4)

    # System
    "log_dir":       "lessugar_logs",
    "refresh_sec":   15,              # Scan lebih cepat (was 20)
}

# ── Definisi Pair ─────────────────────────────────────────────
# XAU (Gold) di Binance Futures = XAUUSDT
PAIRS = {
    "BTCUSDT":  {"name": "Bitcoin",  "emoji": "₿",  "price_fmt": ",.2f", "decimals": 2},
    "ETHUSDT":  {"name": "Ethereum", "emoji": "Ξ",  "price_fmt": ",.2f", "decimals": 2},
    "SOLUSDT":  {"name": "Solana",   "emoji": "◎",  "price_fmt": ",.3f", "decimals": 3},
    "XAUUSDT":  {"name": "Gold/XAU", "emoji": "Au", "price_fmt": ",.2f", "decimals": 2},
}


# ══════════════════════════════════════════════════════════════
#  UTILITAS TAMPILAN
# ══════════════════════════════════════════════════════════════
W = 65

def _strip(s):
    return re.sub(r'\x1b\[[0-9;]*m', '', s)

def box_top(c=Fore.CYAN):   print(c + Style.BRIGHT + "╔" + "═"*(W-2) + "╗")
def box_bot(c=Fore.CYAN):   print(c + Style.BRIGHT + "╚" + "═"*(W-2) + "╝")
def box_div(c=Fore.CYAN):   print(c + "╠" + "═"*(W-2) + "╣")
def box_thin(c=Fore.CYAN):  print(c + "╟" + "─"*(W-2) + "╢")

def box_row(text, c=Fore.WHITE):
    raw = _strip(text)
    pad = max(W - 2 - len(raw), 0)
    print(Fore.CYAN + "║ " + c + text + " "*pad + Fore.CYAN + "║")

def pbar(value, max_val=10, width=14, fill="█", empty="░"):
    filled = int(min(max(value/max_val, 0), 1.0) * width)
    return fill*filled + empty*(width-filled)

def score_color(s, threshold):
    if s >= threshold:     return Fore.GREEN
    if s >= threshold*0.7: return Fore.YELLOW
    return Fore.WHITE

def safe_float(val, fallback: float = 0.0) -> float:
    """Return fallback if value is NaN or None."""
    if val is None:
        return fallback
    try:
        f = float(val)
        return fallback if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return fallback


# ══════════════════════════════════════════════════════════════
#  STATE POSISI PER PAIR
# ══════════════════════════════════════════════════════════════
class PairPosition:
    def __init__(self, symbol: str):
        self.symbol          = symbol
        self.in_position     = None   # None | 'LONG' | 'SHORT'
        self.entry_price     = 0.0
        self.sl_price        = 0.0
        self.tp_price        = 0.0
        self.trail_price     = 0.0
        self.position_usdt   = 0.0
        self.open_time       = None
        self.entry_score     = 0.0

    def reset(self):
        self.in_position   = None
        self.entry_price   = 0.0
        self.sl_price      = 0.0
        self.tp_price      = 0.0
        self.trail_price   = 0.0
        self.position_usdt = 0.0
        self.open_time     = None
        self.entry_score   = 0.0

    def unrealized_pnl(self, price: float) -> tuple[float, float]:
        """Returns (pnl_usdt, pnl_pct_leveraged)"""
        if not self.in_position:
            return 0.0, 0.0
        if self.in_position == "LONG":
            pct = (price - self.entry_price) / self.entry_price
        else:
            pct = (self.entry_price - price) / self.entry_price
        lev_pct  = pct * CFG["leverage"]
        pnl_usdt = self.position_usdt * lev_pct
        return round(pnl_usdt, 4), round(lev_pct * 100, 2)

    def duration_str(self) -> str:
        if not self.open_time:
            return "--"
        delta = datetime.now() - self.open_time
        m = int(delta.total_seconds() / 60)
        return f"{m}m" if m < 60 else f"{m//60}h{m%60}m"


# ══════════════════════════════════════════════════════════════
#  BOT UTAMA
# ══════════════════════════════════════════════════════════════
class LessugarFuturesProV4:

    def __init__(self):
        os.makedirs(CFG["log_dir"], exist_ok=True)

        # Akun (shared semua pair)
        self.balance        = CFG["initial_balance"]
        self.balance_today  = CFG["initial_balance"]
        self.peak_balance   = CFG["initial_balance"]

        # State per pair
        self.positions: dict[str, PairPosition] = {
            sym: PairPosition(sym) for sym in PAIRS
        }

        # Risk management global
        self.consec_losses   = 0
        self.cooldown_until  = None
        self.daily_loss_date = date.today()
        self.daily_loss_usd  = 0.0

        # Statistik global
        self.total_trades  = 0
        self.win_trades    = 0
        self.total_pnl     = 0.0
        self.max_drawdown  = 0.0

        # Statistik per pair
        self.pair_stats: dict[str, dict] = {
            sym: {"trades": 0, "wins": 0, "pnl": 0.0} for sym in PAIRS
        }

        # Cache data pasar (di-update tiap siklus)
        self.market_data: dict[str, dict]  = {}
        self.pair_scores: dict[str, dict]  = {}

        # Riwayat trade
        self.trades_file = os.path.join(CFG["log_dir"], "trades_v4.json")
        self.trades: list = self._load_trades()

        # State persistence (balance + open positions survive restart)
        self.state_file = os.path.join(CFG["log_dir"], "state_v4.json")
        self._load_state()

        # F&G (update lebih jarang)
        self.fng = {"value": 50, "label": "Neutral"}
        self.fng_last_update = datetime.min

    # ── Persist ──────────────────────────────────────────────
    def _load_trades(self):
        try:
            with open(self.trades_file) as f:
                return json.load(f)
        except Exception:
            return []

    def _save_trade(self, entry: dict):
        self.trades.append(entry)
        with open(self.trades_file, "w") as f:
            json.dump(self.trades, f, indent=2)
        self._save_state()

    # ── State Persistence ─────────────────────────────────────
    def _save_state(self):
        """Persist balance, stats, and open positions so restarts are safe."""
        state = {
            "balance":        self.balance,
            "balance_today":  self.balance_today,
            "peak_balance":   self.peak_balance,
            "consec_losses":  self.consec_losses,
            "daily_loss_usd": self.daily_loss_usd,
            "daily_loss_date": self.daily_loss_date.isoformat(),
            "total_trades":   self.total_trades,
            "win_trades":     self.win_trades,
            "total_pnl":      self.total_pnl,
            "max_drawdown":   self.max_drawdown,
            "pair_stats":     self.pair_stats,
            "positions": {},
        }
        for sym, pos in self.positions.items():
            if pos.in_position:
                state["positions"][sym] = {
                    "side":         pos.in_position,
                    "entry_price":  pos.entry_price,
                    "sl_price":     pos.sl_price,
                    "tp_price":     pos.tp_price,
                    "trail_price":  pos.trail_price,
                    "position_usdt": pos.position_usdt,
                    "open_time":    pos.open_time.strftime("%Y-%m-%d %H:%M:%S") if pos.open_time else None,
                    "entry_score":  pos.entry_score,
                }
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass  # Non-fatal

    def _load_state(self):
        """Restore balance, stats, and open positions from disk."""
        try:
            with open(self.state_file) as f:
                state = json.load(f)
        except Exception:
            return  # No saved state — start fresh

        self.balance        = state.get("balance",        self.balance)
        self.balance_today  = state.get("balance_today",  self.balance_today)
        self.peak_balance   = state.get("peak_balance",   self.peak_balance)
        self.consec_losses  = state.get("consec_losses",  self.consec_losses)
        self.daily_loss_usd = state.get("daily_loss_usd", self.daily_loss_usd)
        self.total_trades   = state.get("total_trades",   self.total_trades)
        self.win_trades     = state.get("win_trades",     self.win_trades)
        self.total_pnl      = state.get("total_pnl",      self.total_pnl)
        self.max_drawdown   = state.get("max_drawdown",   self.max_drawdown)

        saved_date = state.get("daily_loss_date", "")
        if saved_date:
            try:
                self.daily_loss_date = date.fromisoformat(saved_date)
            except Exception:
                pass

        saved_stats = state.get("pair_stats", {})
        for sym, st in saved_stats.items():
            if sym in self.pair_stats:
                self.pair_stats[sym].update(st)

        saved_positions = state.get("positions", {})
        for sym, pdata in saved_positions.items():
            if sym in self.positions:
                pos = self.positions[sym]
                pos.in_position   = pdata.get("side")
                pos.entry_price   = pdata.get("entry_price", 0.0)
                pos.sl_price      = pdata.get("sl_price", 0.0)
                pos.tp_price      = pdata.get("tp_price", 0.0)
                pos.trail_price   = pdata.get("trail_price", 0.0)
                pos.position_usdt = pdata.get("position_usdt", 0.0)
                pos.entry_score   = pdata.get("entry_score", 0.0)
                ot = pdata.get("open_time")
                if ot:
                    try:
                        pos.open_time = datetime.strptime(ot, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pos.open_time = None

    # ── Fetch Data ───────────────────────────────────────────
    def fetch_ohlcv(self, symbol: str, max_retries: int = 3) -> pd.DataFrame:
        url = (
            f"https://fapi.binance.com/fapi/v1/klines"
            f"?symbol={symbol}&interval={CFG['timeframe']}"
            f"&limit={CFG['candle_limit']}"
        )
        last_err = None
        for attempt in range(max_retries):
            try:
                r   = requests.get(url, timeout=12)
                raw = r.json()
                if isinstance(raw, dict) and raw.get("code"):
                    raise ValueError(f"Binance error {symbol}: {raw.get('msg')}")
                df = pd.DataFrame(raw, columns=[
                    "ts","open","high","low","close","volume",
                    "close_time","qav","trades","tbv","tqv","ignore"
                ])
                for col in ["open","high","low","close","volume"]:
                    df[col] = df[col].astype(float)
                if len(df) < 60:
                    raise ValueError(f"Insufficient candles for {symbol}: {len(df)} (need 60+)")
                return df
            except (requests.exceptions.RequestException, ValueError) as e:
                last_err = e
                wait = 2 ** attempt  # 1s, 2s, 4s backoff
                time.sleep(wait)
        raise last_err  # type: ignore

    def fetch_fng(self):
        try:
            r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
            d = r.json()["data"][0]
            self.fng = {"value": int(d["value"]), "label": d["value_classification"]}
        except Exception:
            pass  # Gunakan nilai terakhir

    # ── Indikator ────────────────────────────────────────────
    def compute_indicators(self, df: pd.DataFrame) -> dict:
        c = CFG

        df["EMA9"]   = ta.ema(df["close"], length=c["ema_fast"])
        df["EMA21"]  = ta.ema(df["close"], length=c["ema_mid"])
        df["EMA55"]  = ta.ema(df["close"], length=c["ema_slow"])
        df["EMA200"] = ta.ema(df["close"], length=c["ema_trend"])
        df["RSI"]    = ta.rsi(df["close"], length=c["rsi_period"])
        df["VOL_MA"] = ta.sma(df["volume"], length=c["vol_ma_period"])

        # MACD
        macd_df   = ta.macd(df["close"], fast=c["macd_fast"], slow=c["macd_slow"], signal=c["macd_signal"])
        macd_col  = next((col for col in macd_df.columns if col.startswith("MACD_")),  None)
        macds_col = next((col for col in macd_df.columns if col.startswith("MACDs_")), None)
        macdh_col = next((col for col in macd_df.columns if col.startswith("MACDh_")), None)
        df["MACD"]      = macd_df[macd_col]  if macd_col  else float("nan")
        df["MACD_SIG"]  = macd_df[macds_col] if macds_col else float("nan")
        df["MACD_HIST"] = macd_df[macdh_col] if macdh_col else float("nan")

        # Bollinger Bands
        bb_df   = ta.bbands(df["close"], length=c["bb_period"])
        bbu_col = next((col for col in bb_df.columns if col.startswith("BBU_")), None)
        bbm_col = next((col for col in bb_df.columns if col.startswith("BBM_")), None)
        bbl_col = next((col for col in bb_df.columns if col.startswith("BBL_")), None)
        df["BBU"] = bb_df[bbu_col] if bbu_col else float("nan")
        df["BBM"] = bb_df[bbm_col] if bbm_col else float("nan")
        df["BBL"] = bb_df[bbl_col] if bbl_col else float("nan")

        # ATR
        df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=c["atr_period"])

        # Stochastic RSI
        stoch = ta.stochrsi(df["close"], length=c["stoch_k"])
        if stoch is not None and not stoch.empty:
            sk_col = next((col for col in stoch.columns if "STOCHRSIk" in col), None)
            sd_col = next((col for col in stoch.columns if "STOCHRSId" in col), None)
            df["STOCH_K"] = stoch[sk_col] if sk_col else float("nan")
            df["STOCH_D"] = stoch[sd_col] if sd_col else float("nan")
        else:
            df["STOCH_K"] = df["STOCH_D"] = float("nan")

        # Support / Resistance
        pivot      = df.iloc[-50:]
        support    = pivot["low"].min()
        resistance = pivot["high"].max()

        # Fibonacci
        rec = df.iloc[-100:]
        hi  = rec["high"].max()
        lo  = rec["low"].min()
        rng = hi - lo
        fibo = {
            "382": hi - 0.382 * rng,
            "500": hi - 0.500 * rng,
            "618": hi - 0.618 * rng,
        }

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # Safe indicator reads (NaN → fallback 0.0)
        price     = safe_float(last["close"])
        opn       = safe_float(last["open"])
        high      = safe_float(last["high"])
        low       = safe_float(last["low"])
        vol       = safe_float(last["volume"])
        vol_ma    = safe_float(last["VOL_MA"])
        rsi       = safe_float(last["RSI"], 50.0)
        ema9      = safe_float(last["EMA9"],  price)
        ema21     = safe_float(last["EMA21"], price)
        ema55     = safe_float(last["EMA55"], price)
        ema200    = safe_float(last["EMA200"], price)
        macd      = safe_float(last["MACD"])
        macd_sig  = safe_float(last["MACD_SIG"])
        macd_hist = safe_float(last["MACD_HIST"])
        bbu       = safe_float(last["BBU"], price * 1.05)
        bbm       = safe_float(last["BBM"], price)
        bbl       = safe_float(last["BBL"], price * 0.95)
        atr       = safe_float(last["ATR"], 0.0001)
        stoch_k   = safe_float(last["STOCH_K"], 50.0)
        stoch_d   = safe_float(last["STOCH_D"], 50.0)

        prev_macd     = safe_float(prev["MACD"])
        prev_macd_sig = safe_float(prev["MACD_SIG"])
        prev_close    = safe_float(prev["close"])
        prev_open     = safe_float(prev["open"])

        # Pola candle
        body  = abs(opn - price) if price != opn else 0.0001
        wick  = max(high - low, 0.0001)
        doji  = body < wick * 0.1

        hammer = (price > opn) and (opn - low) > body * 2

        shooting = (price < opn) and (high - opn) > body * 2

        bull_engulf = (prev_close < prev_open) and \
                      (price > opn) and \
                      (price > prev_open) and \
                      (opn < prev_close)

        bear_engulf = (prev_close > prev_open) and \
                      (price < opn) and \
                      (price < prev_open) and \
                      (opn > prev_close)

        macd_cross_up = prev_macd < prev_macd_sig and macd >= macd_sig
        macd_cross_dn = prev_macd > prev_macd_sig and macd <= macd_sig

        return {
            "price":        price,
            "open":         opn,
            "high":         high,
            "low":          low,
            "volume":       vol,
            "vol_ma":       vol_ma,
            "rsi":          rsi,
            "ema9":         ema9,
            "ema21":        ema21,
            "ema55":        ema55,
            "ema200":       ema200,
            "macd":         macd,
            "macd_sig":     macd_sig,
            "macd_hist":    macd_hist,
            "macd_cross_up": macd_cross_up,
            "macd_cross_dn": macd_cross_dn,
            "bbu":          bbu,
            "bbm":          bbm,
            "bbl":          bbl,
            "atr":          atr,
            "stoch_k":      stoch_k,
            "stoch_d":      stoch_d,
            "support":      float(support),
            "resistance":   float(resistance),
            "fibo":         fibo,
            "vol_spike":    vol > vol_ma * 1.5 if vol_ma > 0 else False,
            "candle_bull":  price > opn,
            "doji":         doji,
            "hammer":       hammer,
            "shooting_star": shooting,
            "bull_engulf":  bull_engulf,
            "bear_engulf":  bear_engulf,
        }

    # ── Scoring ──────────────────────────────────────────────
    def score_entry(self, d: dict, fng_val: int) -> dict:
        c = CFG
        ls = ss = 0.0
        lr = []   # long reasons
        sr = []   # short reasons
        price = d["price"]

        # 1. EMA Stack (max 2)
        ema_bull = d["ema9"] > d["ema21"] > d["ema55"]
        ema_bear = d["ema9"] < d["ema21"] < d["ema55"]
        if ema_bull and price > d["ema200"]:
            ls += 2; lr.append("✔ EMA Stack Bullish + Harga > EMA200")
        elif price > d["ema200"]:
            ls += 1; lr.append("✔ Harga > EMA200")
        if ema_bear and price < d["ema200"]:
            ss += 2; sr.append("✔ EMA Stack Bearish + Harga < EMA200")
        elif price < d["ema200"]:
            ss += 1; sr.append("✔ Harga < EMA200")

        # 2. RSI (max 2)
        if d["rsi"] < c["rsi_os"]:
            ls += 2; lr.append(f"✔ RSI Oversold ({d['rsi']:.1f})")
        elif d["rsi"] < 50:
            ls += 0.5
        if d["rsi"] > c["rsi_ob"]:
            ss += 2; sr.append(f"✔ RSI Overbought ({d['rsi']:.1f})")
        elif d["rsi"] > 50:
            ss += 0.5

        # 3. MACD (max 2)
        if d["macd_cross_up"]:
            ls += 2; lr.append("✔ MACD Golden Cross ↑")
        elif d["macd"] > d["macd_sig"] and d["macd_hist"] > 0:
            ls += 1; lr.append("✔ MACD Bullish")
        if d["macd_cross_dn"]:
            ss += 2; sr.append("✔ MACD Death Cross ↓")
        elif d["macd"] < d["macd_sig"] and d["macd_hist"] < 0:
            ss += 1; sr.append("✔ MACD Bearish")

        # 4. Stochastic RSI (max 1.5)
        if d["stoch_k"] < c["stoch_os"] and d["stoch_k"] > d["stoch_d"]:
            ls += 1.5; lr.append(f"✔ StochRSI Oversold Cross ({d['stoch_k']:.0f})")
        elif d["stoch_k"] < c["stoch_os"]:
            ls += 0.5
        if d["stoch_k"] > c["stoch_ob"] and d["stoch_k"] < d["stoch_d"]:
            ss += 1.5; sr.append(f"✔ StochRSI Overbought Cross ({d['stoch_k']:.0f})")
        elif d["stoch_k"] > c["stoch_ob"]:
            ss += 0.5

        # 5. Bollinger Bands (max 1.5)
        if price <= d["bbl"]:
            ls += 1.5; lr.append("✔ Harga di bawah BB Lower")
        elif price <= d["bbm"]:
            ls += 0.5
        if price >= d["bbu"]:
            ss += 1.5; sr.append("✔ Harga di atas BB Upper")
        elif price >= d["bbm"]:
            ss += 0.5

        # 6. Volume (max 1)
        if d["vol_spike"]:
            if d["candle_bull"]:
                ls += 1; lr.append("✔ Volume Spike + Candle Bullish")
            else:
                ss += 1; sr.append("✔ Volume Spike + Candle Bearish")

        # 7. Candlestick Pattern (max 1.5)
        if d["hammer"] or d["bull_engulf"]:
            ls += 1.5
            lr.append(f"✔ Pola {'Hammer' if d['hammer'] else 'Bull Engulfing'}")
        if d["shooting_star"] or d["bear_engulf"]:
            ss += 1.5
            sr.append(f"✔ Pola {'Shooting Star' if d['shooting_star'] else 'Bear Engulfing'}")

        # 8. Fibonacci (max 1)
        near_fibo = any(
            abs(price - v) < d["atr"] * 0.5
            for v in d["fibo"].values()
        )
        if near_fibo:
            ls += 1; lr.append("✔ Zona Fibonacci")

        # 9. Fear & Greed contrarian (max 1)
        if fng_val <= 25:
            ls += 1; lr.append(f"✔ F&G Extreme Fear ({fng_val})")
        if fng_val >= 75:
            ss += 1; sr.append(f"✔ F&G Extreme Greed ({fng_val})")

        # 10. Support / Resistance (max 1)
        if abs(price - d["support"]) < d["atr"] * 0.8:
            ls += 1; lr.append("✔ Dekat Support")
        if abs(price - d["resistance"]) < d["atr"] * 0.8:
            ss += 1; sr.append("✔ Dekat Resistance")

        return {
            "long_score":   round(min(ls, 10), 1),
            "short_score":  round(min(ss, 10), 1),
            "long_reasons": lr,
            "short_reasons": sr,
        }

    # ── Risk Management ──────────────────────────────────────
    def open_position_count(self) -> int:
        return sum(1 for p in self.positions.values() if p.in_position)

    def is_trading_allowed(self) -> tuple[bool, str]:
        if date.today() != self.daily_loss_date:
            self.daily_loss_date = date.today()
            self.daily_loss_usd  = 0.0
            self.balance_today   = self.balance

        if self.cooldown_until and datetime.now() < self.cooldown_until:
            rem = int((self.cooldown_until - datetime.now()).total_seconds() / 60)
            return False, f"⏳ Cooldown {rem}m"

        if self.daily_loss_usd / max(self.balance_today, 1) >= CFG["daily_loss_limit"]:
            return False, "🛑 Daily Loss Limit"

        if self.open_position_count() >= CFG["max_open_positions"]:
            return False, f"📊 Max {CFG['max_open_positions']} posisi terbuka"

        if self.balance <= 1.0:
            return False, "💀 Modal habis"

        return True, "OK"

    # ── Trailing Stop ────────────────────────────────────────
    def update_trailing(self, pos: PairPosition, price: float, atr: float):
        if not CFG["trailing_enabled"] or not pos.in_position:
            return
        dist = atr * CFG["trailing_atr_mult"]
        if pos.in_position == "LONG":
            new_trail = price - dist
            if new_trail > pos.trail_price:
                pos.trail_price = new_trail
                if pos.trail_price > pos.sl_price:
                    pos.sl_price = round(pos.trail_price, 2)
        else:
            new_trail = price + dist
            if new_trail < pos.trail_price or pos.trail_price == 0:
                pos.trail_price = new_trail
                if pos.trail_price < pos.sl_price:
                    pos.sl_price = round(pos.trail_price, 2)

    # ── Total Exposure Check ──────────────────────────────────
    def total_open_exposure(self) -> float:
        """Sum of position_usdt for all open positions."""
        return sum(p.position_usdt for p in self.positions.values() if p.in_position)

    # ── Entry ────────────────────────────────────────────────
    def enter_position(self, symbol: str, side: str, price: float,
                       atr: float, reasons: list, score: float):
        # ATR sanity check — skip if volatility is near-zero (stale data)
        if atr <= 0 or price <= 0:
            return

        pos     = self.positions[symbol]
        sl_dist = atr * CFG["sl_atr_mult"]
        tp_dist = sl_dist * CFG["tp_rr"]

        # Protect against zero SL distance
        if sl_dist / price < 0.0005:  # SL < 0.05% = too tight, skip
            return

        if side == "LONG":
            sl = price - sl_dist
            tp = price + tp_dist
        else:
            sl = price + sl_dist
            tp = price - tp_dist

        # Position sizing with total exposure cap
        risk_usdt   = self.balance * CFG["risk_per_trade"]
        sl_pct      = sl_dist / price
        pos_usdt    = risk_usdt / sl_pct
        max_total   = self.balance * CFG["max_position_pct"] * CFG["max_open_positions"]
        remaining   = max(max_total - self.total_open_exposure(), 0)
        pos_usdt    = min(pos_usdt, self.balance * CFG["max_position_pct"], remaining)

        if pos_usdt < 5.0:  # Minimum viable position
            return

        pos.in_position   = side
        pos.entry_price   = price
        pos.sl_price      = round(sl, 4)
        pos.tp_price      = round(tp, 4)
        # Fix trailing init: start beyond SL so first update always works
        if side == "LONG":
            pos.trail_price = round(sl - atr * 0.1, 4)   # slightly below SL
        else:
            pos.trail_price = round(sl + atr * 0.1, 4)   # slightly above SL
        pos.position_usdt = round(pos_usdt, 2)
        pos.open_time     = datetime.now()
        pos.entry_score   = score

        self._save_trade({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": "ENTRY", "side": side, "symbol": symbol,
            "price": price, "sl": pos.sl_price, "tp": pos.tp_price,
            "pos_usdt": pos_usdt, "score": score, "leverage": CFG["leverage"],
            "reasons": reasons, "balance": round(self.balance, 4),
        })

    # ── Exit ─────────────────────────────────────────────────
    def exit_position(self, symbol: str, price: float, reason: str):
        pos  = self.positions[symbol]
        if not pos.in_position:
            return
        side = pos.in_position

        pnl_pct  = ((price - pos.entry_price) / pos.entry_price
                    if side == "LONG"
                    else (pos.entry_price - price) / pos.entry_price)
        lev_pct  = pnl_pct * CFG["leverage"]
        pnl_usdt = pos.position_usdt * lev_pct

        self.balance   += pnl_usdt
        self.total_pnl += pnl_usdt
        self.total_trades += 1
        self.pair_stats[symbol]["trades"] += 1
        self.pair_stats[symbol]["pnl"]    += pnl_usdt

        if pnl_usdt > 0:
            self.win_trades += 1
            self.pair_stats[symbol]["wins"] += 1
            self.consec_losses = 0
        else:
            self.consec_losses += 1
            self.daily_loss_usd += abs(pnl_usdt)
            if self.consec_losses >= CFG["max_consec_losses"]:
                self.cooldown_until = datetime.now() + timedelta(minutes=CFG["cooldown_minutes"])

        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = (self.peak_balance - self.balance) / self.peak_balance
        if dd > self.max_drawdown:
            self.max_drawdown = dd

        self._save_trade({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": "EXIT", "side": side, "symbol": symbol,
            "price": price, "entry_price": pos.entry_price,
            "pnl_usdt": round(pnl_usdt, 4),
            "pnl_pct": round(lev_pct * 100, 2),
            "reason": reason,
            "duration": pos.duration_str(),
            "balance": round(self.balance, 4),
        })

        pos.reset()

    # ══════════════════════════════════════════════════════════
    #  DASHBOARD
    # ══════════════════════════════════════════════════════════
    def print_dashboard(self, allowed: bool, allowed_msg: str):
        try:
            os.system("cls" if os.name == "nt" else "clear")
        except Exception:
            pass  # non-fatal: some terminals don't support cls
        c   = CFG
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        wr  = self.win_trades / max(self.total_trades, 1) * 100
        pnl_c = Fore.GREEN if self.total_pnl >= 0 else Fore.RED
        bal_c = Fore.GREEN if self.balance >= CFG["initial_balance"] else Fore.RED

        # ── Header
        box_top(Fore.CYAN)
        box_row(f"  🚀 LESSUGAR FUTURES PRO V4  ·  MULTI-PAIR  ·  {CFG['timeframe']}", Fore.CYAN + Style.BRIGHT)
        box_row(f"  {now}  ·  Lev: {CFG['leverage']}x  ·  RR: 1:{CFG['tp_rr']}  ·  Risk/trade: {CFG['risk_per_trade']*100:.0f}%", Fore.WHITE)
        box_div()

        # ── Akun
        box_row(f"  💰 AKUN", Fore.YELLOW + Style.BRIGHT)
        box_row(f"  Saldo    : {bal_c + Style.BRIGHT}${self.balance:.4f}  "
                f"{Fore.WHITE}(Awal: ${CFG['initial_balance']:.2f})")
        box_row(f"  Total PnL: {pnl_c}{self.total_pnl:+.4f} USDT  "
                f"{Fore.WHITE}WR: {Fore.CYAN}{wr:.0f}%  "
                f"{Fore.WHITE}({self.win_trades}W / {self.total_trades - self.win_trades}L / {self.total_trades}T)")
        box_row(f"  Max DD   : {Fore.RED}{self.max_drawdown*100:.2f}%  "
                f"{Fore.WHITE}Loss Streak: {Fore.RED}{self.consec_losses}x  "
                f"{Fore.WHITE}Posisi Buka: {Fore.CYAN}{self.open_position_count()}/{CFG['max_open_positions']}")

        # F&G
        fg   = self.fng
        fgc  = Fore.RED if fg["value"] < 25 else (Fore.GREEN if fg["value"] > 75 else Fore.YELLOW)
        fgb  = pbar(fg["value"], 100, width=18)
        box_row(f"  F&G      : {fgc}{fg['value']}/100 — {fg['label']}  [{fgc}{fgb}{Fore.WHITE}]")

        if not allowed:
            box_row(f"  STATUS   : {Fore.RED + Style.BRIGHT}{allowed_msg}")
        else:
            box_row(f"  STATUS   : {Fore.GREEN}✅ Trading Aktif")
        box_div()

        # ── Per Pair
        for symbol, info in PAIRS.items():
            d  = self.market_data.get(symbol)
            sc = self.pair_scores.get(symbol)
            ps = self.pair_stats[symbol]
            pos = self.positions[symbol]

            if d is None:
                box_row(f"  {info['emoji']} {info['name']} ({symbol})  {Fore.RED}⚠ Gagal fetch data")
                box_thin()
                continue

            price   = d["price"]
            price_chg = (price - d["open"]) / d["open"] * 100
            pc_c    = Fore.GREEN if price_chg >= 0 else Fore.RED
            trend_c = Fore.GREEN if price > d["ema200"] else Fore.RED
            trend_l = "▲" if price > d["ema200"] else "▼"
            rsi_c   = Fore.RED if d["rsi"] > c["rsi_ob"] else (Fore.GREEN if d["rsi"] < c["rsi_os"] else Fore.YELLOW)

            # Nama pair + harga
            fmt = info["price_fmt"]
            box_row(
                f"  {info['emoji']} {Fore.WHITE + Style.BRIGHT}{info['name']:<10}  "
                f"{Fore.YELLOW + Style.BRIGHT}${price:{fmt}}  "
                f"{pc_c}({'+' if price_chg >= 0 else ''}{price_chg:.2f}%)  "
                f"{trend_c}{trend_l}",
            )

            # Indikator ringkas
            macd_c = Fore.GREEN if d["macd"] > d["macd_sig"] else Fore.RED
            macd_x = (Fore.GREEN+"▲X" if d["macd_cross_up"] else (Fore.RED+"▼X" if d["macd_cross_dn"] else "  "))
            box_row(
                f"  RSI:{rsi_c}{d['rsi']:.0f}{Fore.WHITE}  "
                f"MACD:{macd_c}{d['macd']:.1f}/{d['macd_sig']:.1f}{macd_x}{Fore.WHITE}  "
                f"ATR:{Fore.CYAN}{d['atr']:.2f}{Fore.WHITE}  "
                f"StochK:{Fore.CYAN}{d['stoch_k']:.0f}"
            )

            # Scoring bar
            if sc:
                ls, ss = sc["long_score"], sc["short_score"]
                lc = score_color(ls, c["min_score_entry"])
                sc_c = score_color(ss, c["min_score_entry"])
                lb = pbar(ls, 10, width=10)
                sb = pbar(ss, 10, width=10)
                box_row(
                    f"  L:{lc}{lb}{ls:.1f}{Fore.WHITE}  "
                    f"S:{sc_c}{sb}{ss:.1f}{Fore.WHITE}  "
                    f"Trades:{Fore.CYAN}{ps['trades']}{Fore.WHITE}  "
                    f"PnL:{(Fore.GREEN if ps['pnl']>=0 else Fore.RED)}{ps['pnl']:+.3f}"
                )

            # Posisi aktif
            if pos.in_position:
                unr_usdt, unr_pct = pos.unrealized_pnl(price)
                unr_c  = Fore.GREEN if unr_usdt >= 0 else Fore.RED
                side_c = Fore.GREEN if pos.in_position == "LONG" else Fore.RED
                box_row(
                    f"  {side_c + Style.BRIGHT}▶ {pos.in_position}{Fore.WHITE}  "
                    f"Entry:${pos.entry_price:{fmt}}  "
                    f"SL:{Fore.RED}${pos.sl_price:{fmt}}  "
                    f"TP:{Fore.GREEN}${pos.tp_price:{fmt}}"
                )
                box_row(
                    f"    Trail:{Fore.MAGENTA}${pos.trail_price:{fmt}}{Fore.WHITE}  "
                    f"UNR:{unr_c}{unr_usdt:+.4f}({unr_pct:+.1f}%)  "
                    f"{Fore.WHITE}Dur:{pos.duration_str()}"
                )

            box_thin()

        # ── Recent trades global (exit only)
        exits = [t for t in self.trades if t.get("type") == "EXIT"][-5:]
        if exits:
            box_row(f"  📋 5 TRADE TERAKHIR", Fore.YELLOW + Style.BRIGHT)
            for t in exits:
                tc  = Fore.GREEN if t.get("pnl_usdt", 0) >= 0 else Fore.RED
                em  = "✅" if t.get("pnl_usdt", 0) >= 0 else "❌"
                sym = t.get("symbol", "?")[:7]
                box_row(
                    f"  {em} {sym:<8}{t['side']:<5} "
                    f"{t['time'][-8:]}  "
                    f"{tc}{t.get('pnl_usdt', 0):+.4f} USDT  "
                    f"{Fore.WHITE}{t.get('reason','')[:16]}"
                )
            box_div()

        box_bot(Fore.CYAN)
        print(Fore.BLUE + Style.BRIGHT +
              f"  ⏱  Refresh tiap {CFG['refresh_sec']}s  |  Pairs: {', '.join(PAIRS)}  |  Ctrl+C keluar")

    # ══════════════════════════════════════════════════════════
    #  MAIN LOOP
    # ══════════════════════════════════════════════════════════
    def run(self):
        print(Fore.CYAN + Style.BRIGHT + "\n  🚀 Memulai LessugarFutures Pro V4 (Multi-Pair)...\n")
        print(Fore.WHITE + f"  Pairs aktif: {', '.join(PAIRS.keys())}")
        print(Fore.WHITE + f"  Modal awal : ${CFG['initial_balance']:.2f} USDT\n")
        time.sleep(2)

        # Update F&G sekali dulu
        self.fetch_fng()
        self.fng_last_update = datetime.now()

        while True:
            try:
                # Update F&G setiap 5 menit
                if (datetime.now() - self.fng_last_update).total_seconds() > 300:
                    self.fetch_fng()
                    self.fng_last_update = datetime.now()

                # ── Scan semua pair
                allowed, allowed_msg = True, "OK"  # default; re-checked per pair
                for symbol in PAIRS:
                    try:
                        # Re-check trading permission each pair (positions may have changed)
                        allowed, allowed_msg = self.is_trading_allowed()

                        df = self.fetch_ohlcv(symbol)
                        d  = self.compute_indicators(df)
                        sc = self.score_entry(d, self.fng["value"])

                        self.market_data[symbol]  = d
                        self.pair_scores[symbol]  = sc

                        pos   = self.positions[symbol]
                        price = d["price"]
                        atr   = d["atr"]

                        # Update trailing
                        if pos.in_position:
                            self.update_trailing(pos, price, atr)

                        # ── Cek EXIT (prioritas utama)
                        if pos.in_position:
                            exit_reason = None
                            if pos.in_position == "LONG":
                                if price <= pos.sl_price:
                                    exit_reason = "Stop Loss / Trailing"
                                elif price >= pos.tp_price:
                                    exit_reason = "Take Profit"
                                elif sc["short_score"] >= 5 and sc["long_score"] < 3:
                                    exit_reason = "Reversal Signal"
                            else:  # SHORT
                                if price >= pos.sl_price:
                                    exit_reason = "Stop Loss / Trailing"
                                elif price <= pos.tp_price:
                                    exit_reason = "Take Profit"
                                elif sc["long_score"] >= 5 and sc["short_score"] < 3:
                                    exit_reason = "Reversal Signal"

                            if exit_reason:
                                self.exit_position(symbol, price, exit_reason)

                        # ── Cek ENTRY
                        if not pos.in_position and allowed:
                            ls = sc["long_score"]
                            ss = sc["short_score"]
                            min_s = CFG["min_score_entry"]

                            if ls >= min_s and ls > ss:
                                self.enter_position(
                                    symbol, "LONG", price, atr,
                                    sc["long_reasons"], ls
                                )
                            elif ss >= min_s and ss > ls:
                                self.enter_position(
                                    symbol, "SHORT", price, atr,
                                    sc["short_reasons"], ss
                                )

                    except ValueError as e:
                        # Pair tidak tersedia (misal XAU di beberapa region)
                        self.market_data[symbol] = None
                        print(Fore.RED + f"  ⚠ {symbol}: {e}")
                    except Exception as e:
                        self.market_data[symbol] = None
                        print(Fore.RED + f"  ⚠ Error {symbol}: {e}")

                # ── Print dashboard setelah semua pair di-scan
                try:
                    self.print_dashboard(allowed, allowed_msg)
                except Exception as e:
                    print(Fore.RED + f"  ⚠ Dashboard render error: {e}")
                sys.stdout.flush()
                time.sleep(CFG["refresh_sec"])

            except KeyboardInterrupt:
                print(Fore.CYAN + "\n\n  Terima kasih! Paper trading selesai. 🍬")
                wr = self.win_trades / max(self.total_trades, 1) * 100
                print(Fore.WHITE +
                      f"  PnL: {self.total_pnl:+.4f} USDT  |  "
                      f"Trade: {self.total_trades}  |  WR: {wr:.0f}%")
                print(Fore.WHITE + "\n  Per-Pair Summary:")
                for sym, st in self.pair_stats.items():
                    pair_wr = st["wins"] / max(st["trades"], 1) * 100
                    print(f"    {sym:<10} T:{st['trades']}  "
                          f"WR:{pair_wr:.0f}%  "
                          f"PnL:{st['pnl']:+.4f}")
                sys.exit(0)

            except requests.exceptions.RequestException as e:
                print(Fore.RED + f"\n  ⚠ Network error: {e}")
                time.sleep(15)
            except Exception as e:
                print(Fore.RED + f"\n  ⚠ Error global: {e}")
                import traceback; traceback.print_exc()
                time.sleep(5)


# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    bot = LessugarFuturesProV4()
    bot.run()
