# Auto Trading Demo

Two Binance trading bots — paper futures dan market tracker.

## Bot 1: Paper Futures (`lessugar_futures_paperv2.py`)

Multi-pair paper trading bot di Binance Futures. Menggunakan strategi multi-confluence scoring (EMA, RSI, MACD, Bollinger, Stochastic, Fibonacci, Fear & Greed, Support/Resistance, candle pattern). **Tidak perlu API key.**

### Cara pakai

```
RUN_FUTURES_BOT.bat
# atau langsung:
python lessugar_futures_paperv2.py
```

### Konfigurasi

Edit variabel `CFG` di `lessugar_futures_paperv2.py:22`:
- `initial_balance` — modal awal (default 1000 USDT)
- `leverage` — leverage (default 10x)
- `risk_per_trade` — risiko per trade (default 1%)
- `timeframe` — timeframe candle (default 5m)
- `min_score_entry` — minimum skor entry (default 4)

Pair aktif: BTCUSDT, ETHUSDT, SOLUSDT, XAUUSDT.

## Bot 2: Market Tracker (`sugar_tracker_v2.py`)

Multi-asset tracker dengan indikator teknikal + sinyal whale CoinGecko + Fear & Greed. Menggunakan `ccxt` (Binance spot). Opsional: pasang API key CoinGecko di `.env` untuk akses data whale.

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

Edit variabel `CONFIG` di `sugar_tracker_v2.py:20`:
- `timeframe` — timeframe analisis (default 15m)
- `refresh_seconds` — interval refresh (default 30s)
- `assets` — daftar aset yang dipantau

## Dependencies

```
pip install pandas pandas_ta colorama requests ccxt python-dotenv
```

## Catatan

- Kedua script berjalan terus menerus, matikan dengan Ctrl+C.
- Terminal harus support UTF-8. Jika karakter kotak-kotak rusak di Windows: `chcp 65001`.
- Log disimpan ke `lessugar_logs/trades_v4.json` (futures) dan `sugar_alerts.log` (tracker).
