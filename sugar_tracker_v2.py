import ccxt
import os
import sys
import time
import math
import pandas as pd
import pandas_ta as ta
import requests
from datetime import datetime
from colorama import init, Fore, Back, Style
from dotenv import load_dotenv
import json

# Load API Key from .env
load_dotenv()
init(autoreset=True)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


def safe_float(val, fallback: float = 0.0) -> float:
    """Return fallback if value is NaN or None."""
    if val is None:
        return fallback
    try:
        f = float(val)
        return fallback if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return fallback

# ─────────────────────────────────────────────
#  KONFIGURASI — Edit sesuai kebutuhan
# ─────────────────────────────────────────────
CONFIG = {
    "timeframe": "15m",       # Timeframe OHLCV
    "ma_period": 200,          # MA panjang
    "rsi_period": 14,
    "atr_period": 14,
    "bb_period": 20,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "refresh_seconds": 30,    # Interval update
    "log_alerts": True,        # Simpan alert ke file
    "log_file": "sugar_alerts.log",
    "assets": {
        "BTC/USDT":  {"name": "Bitcoin",     "cg_id": "bitcoin"},
        "ETH/USDT":  {"name": "Ethereum",    "cg_id": "ethereum"},
        "PAXG/USDT": {"name": "Gold (PAXG)", "cg_id": "pax-gold"},
        "BNB/USDT":  {"name": "BNB",         "cg_id": "binancecoin"},
        "SOL/USDT":  {"name": "Solana",      "cg_id": "solana"},
    }
}


# ─────────────────────────────────────────────
#  HELPER: Cetak garis pembatas
# ─────────────────────────────────────────────
def divider(char="─", width=60, color=Fore.CYAN):
    print(color + char * width)

def header_line(text, width=60, color=Fore.CYAN):
    pad = (width - len(text) - 2) // 2
    print(color + Style.BRIGHT + "│" + " " * pad + text + " " * (width - pad - len(text) - 2) + "│")


# ─────────────────────────────────────────────
#  KELAS UTAMA
# ─────────────────────────────────────────────
class SugarTrackerV3:
    def __init__(self):
        self.exchange = ccxt.binance({"enableRateLimit": True})
        self.cg_api_key = os.getenv("COINGECKO_API_KEY", "")
        self.cg_base    = "https://api.coingecko.com/api/v3"
        self.cg_headers = {"x-cg-api-key": self.cg_api_key} if self.cg_api_key else {}
        self.assets     = CONFIG["assets"]
        self.alert_history: list[dict] = []   # Riwayat sinyal sesi ini

        # Alert dedup: don't re-log same symbol+signal within 15 minutes
        self._last_alert: dict[str, tuple[str, datetime]] = {}

        # CoinGecko rate-limit cache (free = 10-30 req/min, stay safe)
        self._cg_cache: dict[str, tuple[float, object]] = {}  # key → (timestamp, data)
        self._cg_min_interval = 7  # seconds between CoinGecko calls

    # ── Binance ──────────────────────────────
    def fetch_ohlcv(self, symbol: str) -> pd.DataFrame:
        bars = self.exchange.fetch_ohlcv(
            symbol, timeframe=CONFIG["timeframe"], limit=300
        )
        df = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])
        return df

    # ── CoinGecko helpers ────────────────────
    def _cg_get(self, path: str, params: dict = None, cache_sec: int = 120):
        """Rate-limited CoinGecko GET with in-memory cache."""
        cache_key = f"{path}:{json.dumps(params or {}, sort_keys=True)}"
        now = time.time()
        if cache_key in self._cg_cache:
            ts, data = self._cg_cache[cache_key]
            if now - ts < cache_sec:
                return data  # Serve from cache
        # Enforce minimum interval between any two CG calls
        try:
            r = requests.get(
                f"{self.cg_base}{path}",
                headers=self.cg_headers,
                params=params,
                timeout=10
            )
            if r.status_code == 429:
                # Rate limited — back off and return cached or empty
                time.sleep(30)
                return self._cg_cache.get(cache_key, (0, {}))[1]
            data = r.json()
            self._cg_cache[cache_key] = (now, data)
            return data
        except Exception:
            return self._cg_cache.get(cache_key, (0, {}))[1]  # stale is better than nothing

    def fetch_fear_greed(self) -> dict | None:
        """Fear & Greed Index via alternative.me (gratis, no key)."""
        try:
            r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
            d = r.json()["data"][0]
            return {"value": int(d["value"]), "label": d["value_classification"]}
        except Exception:
            return None

    def fetch_trending(self) -> list[str]:
        data = self._cg_get("/search/trending")
        return [c["item"]["symbol"].upper() for c in data.get("coins", [])[:7]]

    def fetch_whale_data(self, cg_id: str) -> dict | None:
        data = self._cg_get(f"/coins/{cg_id}/tickers", {"order": "volume_desc"})
        tickers = data.get("tickers", [])
        if not tickers:
            return None
        top5_vol = sum(t["converted_volume"].get("usd", 0) for t in tickers[:5])
        whale_alert = any(
            t["converted_volume"].get("usd", 0) > top5_vol * 0.40
            for t in tickers[:5]
        )
        return {
            "top_vol": top5_vol,
            "whale_alert": whale_alert,
            "main_exchange": tickers[0]["market"]["name"],
        }

    def fetch_global_mcap_change(self) -> float | None:
        """Perubahan total market cap 24 jam (persen)."""
        data = self._cg_get("/global")
        return data.get("data", {}).get("market_cap_change_percentage_24h_usd")

    # ── Analisis Teknikal ────────────────────
    def analyze(self, df: pd.DataFrame) -> dict:
        c = CONFIG

        # Moving Averages
        df["MA200"] = ta.sma(df["close"], length=c["ma_period"])
        df["EMA50"]  = ta.ema(df["close"], length=50)
        df["EMA21"]  = ta.ema(df["close"], length=21)

        # RSI
        df["RSI"] = ta.rsi(df["close"], length=c["rsi_period"])

        # MACD — deteksi kolom otomatis
        macd_df   = ta.macd(df["close"], fast=c["macd_fast"], slow=c["macd_slow"], signal=c["macd_signal"])
        macd_col  = next((col for col in macd_df.columns if col.startswith("MACD_")), None)
        macds_col = next((col for col in macd_df.columns if col.startswith("MACDs_")), None)
        macdh_col = next((col for col in macd_df.columns if col.startswith("MACDh_")), None)
        df["MACD"]        = macd_df[macd_col]  if macd_col  else float("nan")
        df["MACD_signal"] = macd_df[macds_col] if macds_col else float("nan")
        df["MACD_hist"]   = macd_df[macdh_col] if macdh_col else float("nan")

        # Bollinger Bands — deteksi kolom otomatis (nama bervariasi antar versi pandas_ta)
        bb = ta.bbands(df["close"], length=c["bb_period"])
        bb_upper_col = next((col for col in bb.columns if col.startswith("BBU_")), None)
        bb_mid_col   = next((col for col in bb.columns if col.startswith("BBM_")), None)
        bb_lower_col = next((col for col in bb.columns if col.startswith("BBL_")), None)
        df["BB_upper"] = bb[bb_upper_col] if bb_upper_col else float("nan")
        df["BB_mid"]   = bb[bb_mid_col]   if bb_mid_col   else float("nan")
        df["BB_lower"] = bb[bb_lower_col] if bb_lower_col else float("nan")

        # ATR (volatilitas)
        df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=c["atr_period"])

        # Volume MA
        df["VolMA20"] = ta.sma(df["volume"], length=20)

        # Fibonacci (100 candle terakhir)
        recent = df.iloc[-100:]
        hi = recent["high"].max()
        lo = recent["low"].min()
        fibo = {
            "236": hi - 0.236 * (hi - lo),
            "382": hi - 0.382 * (hi - lo),
            "500": hi - 0.500 * (hi - lo),
            "618": hi - 0.618 * (hi - lo),
            "786": hi - 0.786 * (hi - lo),
        }

        # Support / Resistance sederhana (pivot 20 candle)
        pivot_window = df.iloc[-20:]
        support    = pivot_window["low"].min()
        resistance = pivot_window["high"].max()

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # Safe indicator reads
        price     = safe_float(last["close"])
        opn       = safe_float(last["open"])
        high      = safe_float(last["high"])
        low       = safe_float(last["low"])
        vol       = safe_float(last["volume"])
        vol_ma    = safe_float(last["VolMA20"])
        rsi       = safe_float(last["RSI"], 50.0)
        ma200     = safe_float(last["MA200"], price)
        ema50     = safe_float(last["EMA50"], price)
        ema21     = safe_float(last["EMA21"], price)
        macd      = safe_float(last["MACD"])
        macd_sig  = safe_float(last["MACD_signal"])
        macd_hist = safe_float(last["MACD_hist"])
        bb_upper  = safe_float(last["BB_upper"], price * 1.05)
        bb_mid    = safe_float(last["BB_mid"], price)
        bb_lower  = safe_float(last["BB_lower"], price * 0.95)
        atr       = safe_float(last["ATR"], 0.0001)

        prev_macd     = safe_float(prev["MACD"])
        prev_macd_sig = safe_float(prev["MACD_signal"])

        return {
            "price":      price,
            "open":       opn,
            "high":       high,
            "low":        low,
            "volume":     vol,
            "vol_ma":     vol_ma,
            "rsi":        rsi,
            "ma200":      ma200,
            "ema50":      ema50,
            "ema21":      ema21,
            "macd":       macd,
            "macd_sig":   macd_sig,
            "macd_hist":  macd_hist,
            "bb_upper":   bb_upper,
            "bb_mid":     bb_mid,
            "bb_lower":   bb_lower,
            "atr":        atr,
            "fibo":       fibo,
            "support":    safe_float(support, low),
            "resistance": safe_float(resistance, high),
            "candle_bullish": price > opn,
            "macd_cross_up":  prev_macd < prev_macd_sig and macd >= macd_sig,
            "macd_cross_dn":  prev_macd > prev_macd_sig and macd <= macd_sig,
            "vol_spike":  vol > vol_ma * 1.5 if vol_ma > 0 else False,
        }

    # ── Sistem Scoring Sinyal (0–10) ─────────
    def compute_signal(self, d: dict, whale: dict | None, fng: dict | None = None) -> dict:
        buy_score  = 0
        sell_score = 0
        reasons    = []

        # === BUY FACTORS ===
        if d["price"] > d["ma200"]:
            buy_score += 1
            reasons.append(("✔ Harga > MA200", "buy"))
        if d["price"] > d["ema50"]:
            buy_score += 1
            reasons.append(("✔ Harga > EMA50", "buy"))
        if d["ema21"] > d["ema50"]:
            buy_score += 1
            reasons.append(("✔ EMA21 > EMA50 (bullish cross)", "buy"))
        if d["rsi"] < 35:
            buy_score += 2
            reasons.append((f"✔ RSI oversold ({d['rsi']:.1f})", "buy"))
        elif d["rsi"] < 50:
            buy_score += 0.5
        if d["macd_cross_up"]:
            buy_score += 2
            reasons.append(("✔ MACD golden cross ↑", "buy"))
        elif d["macd"] > d["macd_sig"]:
            buy_score += 0.5
        if d["price"] <= d["fibo"]["618"]:
            buy_score += 1
            reasons.append(("✔ Harga di zona Fibo 61.8%", "buy"))
        if d["price"] <= d["bb_lower"]:
            buy_score += 1.5
            reasons.append(("✔ Harga sentuh BB bawah", "buy"))
        if d["vol_spike"] and d["candle_bullish"]:
            buy_score += 1
            reasons.append(("✔ Volume spike + candle hijau", "buy"))
        if d["price"] <= d["support"] * 1.005:
            buy_score += 1
            reasons.append(("✔ Harga dekat Support", "buy"))
        if whale and whale["whale_alert"] and d["price"] > d["ma200"]:
            buy_score += 1
            reasons.append((f"✔ Whale aktif di {whale['main_exchange']}", "buy"))
        # Fear & Greed contrarian — extreme fear = buy opportunity
        if fng and fng["value"] <= 25:
            buy_score += 1
            reasons.append((f"✔ F&G Extreme Fear ({fng['value']}) → Contrarian Buy", "buy"))

        # === SELL FACTORS ===
        if d["price"] < d["ma200"]:
            sell_score += 1
            reasons.append(("✘ Harga < MA200", "sell"))
        if d["rsi"] > 70:
            sell_score += 2
            reasons.append((f"✘ RSI overbought ({d['rsi']:.1f})", "sell"))
        if d["macd_cross_dn"]:
            sell_score += 2
            reasons.append(("✘ MACD death cross ↓", "sell"))
        if d["price"] >= d["bb_upper"]:
            sell_score += 1.5
            reasons.append(("✘ Harga sentuh BB atas", "sell"))
        if d["price"] >= d["resistance"] * 0.995:
            sell_score += 1
            reasons.append(("✘ Harga dekat Resistance", "sell"))
        if d["vol_spike"] and not d["candle_bullish"]:
            sell_score += 1
            reasons.append(("✘ Volume spike + candle merah", "sell"))
        # Fear & Greed contrarian — extreme greed = sell signal
        if fng and fng["value"] >= 75:
            sell_score += 1
            reasons.append((f"✘ F&G Extreme Greed ({fng['value']}) → Contrarian Sell", "sell"))

        # Normalize ke max 10
        buy_score  = min(round(buy_score, 1), 10)
        sell_score = min(round(sell_score, 1), 10)

        # Tentukan sinyal
        if buy_score >= 4:
            signal = "BUY"
        elif sell_score >= 4:
            signal = "SELL"
        else:
            signal = "WAIT"

        # Strength label
        def strength(s):
            if s >= 7: return "KUAT"
            if s >= 4: return "SEDANG"
            return "LEMAH"

        return {
            "signal":     signal,
            "buy_score":  buy_score,
            "sell_score": sell_score,
            "strength":   strength(buy_score if signal == "BUY" else sell_score),
            "reasons":    reasons,
        }

    # ── Log Alert (with dedup — same symbol+signal only once per 15 min) ──
    def log_alert(self, symbol: str, signal_info: dict, price: float):
        if not CONFIG["log_alerts"]:
            return
        signal = signal_info["signal"]
        now = datetime.now()
        # Dedup check
        if symbol in self._last_alert:
            prev_signal, prev_time = self._last_alert[symbol]
            if prev_signal == signal and (now - prev_time).total_seconds() < 900:
                return  # Same signal within 15 minutes — skip
        self._last_alert[symbol] = (signal, now)

        entry = {
            "time":   now.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "signal": signal,
            "score":  signal_info["buy_score"] if signal == "BUY" else signal_info["sell_score"],
            "price":  price,
        }
        self.alert_history.append(entry)
        with open(CONFIG["log_file"], "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── Render Score Bar ──────────────────────
    def score_bar(self, score: float, max_val: float = 10, width: int = 20) -> str:
        filled = int(score / max_val * width)
        bar = "█" * filled + "░" * (width - filled)
        return bar

    # ── Warna RSI ────────────────────────────
    def rsi_color(self, rsi: float) -> str:
        if rsi < 30:  return Fore.GREEN
        if rsi < 50:  return Fore.CYAN
        if rsi < 70:  return Fore.YELLOW
        return Fore.RED

    # ── Print Dashboard ───────────────────────
    def print_dashboard(self, symbol: str, info: dict, d: dict, whale: dict | None, sig: dict):
        W = 60

        # Signal color
        sc = {
            "BUY":  Fore.GREEN,
            "SELL": Fore.RED,
            "WAIT": Fore.YELLOW,
        }[sig["signal"]]

        trend_up = d["price"] > d["ma200"]
        trend_c  = Fore.GREEN if trend_up else Fore.RED
        trend_l  = "▲ UPTREND" if trend_up else "▼ DOWNTREND"

        # Header aset
        print(Fore.WHITE + Style.BRIGHT + f"  {info['name']} ({symbol})")

        # Harga & trend
        price_chg_pct = (d["price"] - d["open"]) / d["open"] * 100
        price_chg_c = Fore.GREEN if price_chg_pct >= 0 else Fore.RED
        print(
            f"  Harga : {Fore.YELLOW + Style.BRIGHT}${d['price']:,.4f}  "
            f"{price_chg_c}({'+' if price_chg_pct >= 0 else ''}{price_chg_pct:.2f}%)"
        )
        print(
            f"  Trend : {trend_c + trend_l}   "
            f"{Fore.WHITE}Support: {Fore.CYAN}${d['support']:,.2f}  "
            f"{Fore.WHITE}Resist: {Fore.CYAN}${d['resistance']:,.2f}"
        )

        # Indikator baris 1
        rsi_c = self.rsi_color(d["rsi"])
        macd_c = Fore.GREEN if d["macd"] > d["macd_sig"] else Fore.RED
        print(
            f"  RSI   : {rsi_c}{d['rsi']:.1f}   "
            f"{Fore.WHITE}MACD: {macd_c}{d['macd']:.2f} / {d['macd_sig']:.2f}  "
            f"{'▲' if d['macd_cross_up'] else ('▼' if d['macd_cross_dn'] else '─')}"
        )

        # Bollinger
        bb_pos = (d["price"] - d["bb_lower"]) / max(d["bb_upper"] - d["bb_lower"], 0.0001) * 100
        print(
            f"  BB    : {Fore.CYAN}[{d['bb_lower']:,.2f} ─ {d['bb_mid']:,.2f} ─ {d['bb_upper']:,.2f}]  "
            f"{Fore.WHITE}Pos: {bb_pos:.0f}%"
        )

        # ATR & Volume
        vol_c = Fore.MAGENTA if d["vol_spike"] else Fore.WHITE
        print(
            f"  ATR   : {Fore.WHITE}{d['atr']:.2f}   "
            f"Volume: {vol_c}{d['volume']:,.0f}"
            f"{Fore.MAGENTA + ' ⚡SPIKE' if d['vol_spike'] else ''}"
        )

        # MA
        ema21_c = Fore.GREEN if d["ema21"] > d["ema50"] else Fore.RED
        print(
            f"  EMA21 : {ema21_c}{d['ema21']:,.2f}   "
            f"{Fore.WHITE}EMA50: {Fore.CYAN}{d['ema50']:,.2f}   "
            f"{Fore.WHITE}MA200: {Fore.CYAN}{d['ma200']:,.2f}"
        )

        # Whale
        if whale:
            if whale["whale_alert"]:
                print(f"  Whale : {Fore.MAGENTA + Style.BRIGHT}🐳 DETECTED di {whale['main_exchange']}! "
                      f"Vol top5: ${whale['top_vol']:,.0f}")
            else:
                print(f"  Whale : {Fore.BLUE}Tenang (Vol top5: ${whale['top_vol']:,.0f})")

        # Score bars
        buy_bar  = self.score_bar(sig["buy_score"])
        sell_bar = self.score_bar(sig["sell_score"])
        print(
            f"  BUY   : {Fore.GREEN}{buy_bar} {sig['buy_score']:.1f}/10\n"
            f"  SELL  : {Fore.RED}{sell_bar} {sig['sell_score']:.1f}/10"
        )

        # Sinyal utama
        print(f"  ► SINYAL: {sc + Style.BRIGHT}{sig['signal']} [{sig['strength']}]")

        # Alasan (top 3)
        for reason, rtype in sig["reasons"][:3]:
            c = Fore.GREEN if rtype == "buy" else Fore.RED
            print(f"    {c}{reason}")

    # ── Main Loop ────────────────────────────
    def run(self):
        cycle = 0
        while True:
            try:
                cycle += 1
                os.system("cls" if os.name == "nt" else "clear")

                # Global data
                trending = self.fetch_trending()
                fg       = self.fetch_fear_greed()
                mcap_chg = self.fetch_global_mcap_change()

                W = 60
                # ══ HEADER ══
                print(Fore.CYAN + Style.BRIGHT + "╔" + "═" * (W - 2) + "╗")
                header_line("🍬 LESSUGAR PREMIUM TRACKER V3 🍬", W)
                header_line("WHALE WATCHER │ MULTI-INDICATOR │ SMART SIGNAL", W)
                print(Fore.CYAN + Style.BRIGHT + "╚" + "═" * (W - 2) + "╝")

                # Waktu & siklus
                print(f"  Waktu  : {Fore.WHITE}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  "
                      f"{Fore.BLUE}Siklus #{cycle}")

                # Fear & Greed
                if fg:
                    fgv = fg["value"]
                    fgc = Fore.GREEN if fgv > 60 else (Fore.RED if fgv < 30 else Fore.YELLOW)
                    print(f"  F&G    : {fgc}{fgv}/100 — {fg['label']}")

                # Market cap global
                if mcap_chg is not None:
                    mc_c = Fore.GREEN if mcap_chg >= 0 else Fore.RED
                    print(f"  MCap   : {mc_c}{mcap_chg:+.2f}% (24h global)")

                # Trending
                print(f"  Trend  : {Fore.MAGENTA}{' │ '.join(trending)}")

                divider("─", W)

                # ══ SETIAP ASET ══
                for symbol, info in self.assets.items():
                    try:
                        df    = self.fetch_ohlcv(symbol)
                        data  = self.analyze(df)
                        whale = self.fetch_whale_data(info["cg_id"])
                        sig   = self.compute_signal(data, whale, fng=fg)

                        # Log jika sinyal kuat
                        if sig["signal"] != "WAIT" and sig["strength"] in ("KUAT", "SEDANG"):
                            self.log_alert(symbol, sig, data["price"])

                        self.print_dashboard(symbol, info, data, whale, sig)

                    except ccxt.BadSymbol:
                        print(Fore.RED + f"  ⚠ {symbol} tidak tersedia di Binance.")
                    except Exception as e:
                        print(Fore.RED + f"  ⚠ Error {symbol}: {e}")

                    divider("─", W)

                # ══ RIWAYAT ALERT SESI ══
                if self.alert_history:
                    print(Fore.WHITE + Style.BRIGHT + "  📋 Riwayat Alert Sesi Ini:")
                    for a in self.alert_history[-5:]:
                        c = Fore.GREEN if a["signal"] == "BUY" else Fore.RED
                        print(f"  {c}[{a['time']}] {a['symbol']} {a['signal']} "
                              f"@ ${a['price']:,.4f} (score {a['score']:.1f})")
                    divider("─", W)

                print(Fore.BLUE + Style.BRIGHT +
                      f"  ⏱  Update tiap {CONFIG['refresh_seconds']}s │ Ctrl+C untuk keluar")
                time.sleep(CONFIG["refresh_seconds"])

            except KeyboardInterrupt:
                print(Fore.CYAN + "\n  Terima kasih! Sampai jumpa. 🍬")
                sys.exit(0)
            except Exception as e:
                print(Fore.RED + f"\n  ⚠ Error global: {e}")
                time.sleep(5)


# ─────────────────────────────────────────────
if __name__ == "__main__":
    tracker = SugarTrackerV3()
    tracker.run()
