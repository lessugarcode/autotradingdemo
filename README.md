# Auto Trading Demo V3

Two Binance trading bots — paper futures dan market tracker.
**Versi 3** membawa peningkatan stabilitas besar: NaN-safe indicator handling, state persistence (saldo & posisi terbuka selamat dari restart), retry otomatis saat network error, alert deduplication, dan integrasi Fear & Greed ke scoring sinyal.

## Bot 1: Paper Futures (`lessugar_futures_paperv2.py`)

Multi-pair paper trading bot di Binance Futures. Menggunakan strategi multi-confluence scoring (EMA, RSI, MACD, Bollinger, Stochastic, Fibonacci, Fear & Greed, Support/Resistance, candle pattern). **Tidak perlu API key.**

### Fitur V3
- **State Persistence** — Saldo, statistik, dan posisi terbuka disimpan ke disk. Restart tidak mereset apa pun.
- **NaN-Safe Indicators** — Semua indikator (EMA200, MACD, BB, ATR, StochRSI) di-wrap dengan `safe_float()` sehingga tidak crash saat data belum cukup.
- **Trailing Stop Fix** — Inisialisasi trail price sekarang benar untuk posisi LONG dan SHORT.
- **Position Sizing Guard** — Proteksi pembagian-nol, batas exposure total, dan minimum posisi $5.
- **ATR Sanity Check** — Skip entry jika ATR ≈ 0 (market mati / data stale).
- **Retry + Backoff** — Fetch candle otomatis retry 3x (1s → 2s → 4s) saat network error.
- **Minimum Candle Check** — Tolak data jika kurang dari 60 candle (indikator belum valid).

### Cara pakai

```
RUN_FUTURES_BOT.bat
# atau langsung:
python lessugar_futures_paperv2.py
```

### Konfigurasi

Edit variabel `CFG` di `lessugar_futures_paperv2.py`:
- `initial_balance` — modal awal (default 1000 USDT)
- `leverage` — leverage (default 10x)
- `risk_per_trade` — risiko per trade (default 1%)
- `max_position_pct` — batas per posisi (default 30% modal)
- `max_open_positions` — maks posisi terbuka bersamaan (default 4)
- `timeframe` — timeframe candle (default 5m)
- `min_score_entry` — minimum skor entry (default 4)
- `sl_atr_mult` — jarak SL dalam kelipatan ATR (default 1.5)
- `tp_rr` — risk-reward ratio TP (default 2.5)
- `trailing_enabled` — aktifkan trailing stop (default True)

Pair aktif: BTCUSDT, ETHUSDT, SOLUSDT, XAUUSDT.

### File yang dihasilkan
- `lessugar_logs/trades_v4.json` — riwayat semua trade (entry + exit)
- `lessugar_logs/state_v4.json` — snapshot state bot (saldo, posisi, statistik)

---

## Bot 2: Market Tracker (`sugar_tracker_v2.py`)

Multi-asset tracker dengan indikator teknikal + sinyal whale CoinGecko + Fear & Greed. Menggunakan `ccxt` (Binance spot). Opsional: pasang API key CoinGecko di `.env` untuk akses data whale.

### Fitur V3
- **Fear & Greed Scoring** — F&G sekarang masuk ke skor sinyal: Extreme Fear (≤25) → +1 buy, Extreme Greed (≥75) → +1 sell (contrarian).
- **Alert Deduplication** — Sinyal BUY/SELL yang sama untuk pair yang sama hanya di-log sekali per 15 menit (tidak spam).
- **CoinGecko Cache + Rate Limit** — Response di-cache 2 menit; jika 429 (rate limited), otomatis back off 30 detik dan sajikan data cache.
- **NaN-Safe Indicators** — Sama seperti futures bot, semua indikator aman dari NaN.
- **UTF-8 Fix** — Otomatis `reconfigure(encoding="utf-8")` di Windows agar box-drawing character tidak rusak.

### Cara pakai

```
LIVE_PRICE_TRACKER.bat
# atau langsung:
python sugar_tracker_v2.py
```

### .env (opsional)

Buat file `.env` di folder ini:
```
COINGECKO_API_KEY=your_api_key_here
```

Tanpa `.env` tracker tetap jalan, hanya data whale CoinGecko tidak bisa diambil.

### Konfigurasi

Edit variabel `CONFIG` di `sugar_tracker_v2.py`:
- `timeframe` — timeframe analisis (default 15m)
- `refresh_seconds` — interval refresh (default 30s)
- `assets` — daftar aset yang dipantau

---

## Dependencies

```
pip install pandas pandas_ta colorama requests ccxt python-dotenv
```

## Catatan

- Kedua script berjalan terus menerus, matikan dengan Ctrl+C.
- Terminal harus support UTF-8. Jika karakter kotak-kotak rusak di Windows: `chcp 65001`.
- Log disimpan ke `lessugar_logs/trades_v4.json` (futures) dan `sugar_alerts.log` (tracker).
- State bot disimpan di `lessugar_logs/state_v4.json` — hapus file ini untuk reset saldo & posisi ke default.
