import json
import math
import time
from pathlib import Path

import pandas as pd
import requests
from yahooquery import Ticker


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
RESULTS_PATH = BASE_DIR / "results.json"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_us_symbols():
    """Return listed US non-ETF symbols from Nasdaq Trader files."""
    headers = {"User-Agent": "Mozilla/5.0"}

    url1 = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
    r1 = requests.get(url1, headers=headers, timeout=30)
    r1.raise_for_status()
    df1 = pd.read_csv(pd.io.common.StringIO(r1.text), sep="|")
    df1 = df1[df1["Symbol"] != "File Creation Time"].copy()
    if "ETF" in df1.columns:
        df1 = df1[df1["ETF"].fillna("N") == "N"]
    if "Test Issue" in df1.columns:
        df1 = df1[df1["Test Issue"].fillna("N") == "N"]
    df1 = df1[["Symbol"]].copy()
    df1["listing_exchange"] = "NASDAQ"

    url2 = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
    r2 = requests.get(url2, headers=headers, timeout=30)
    r2.raise_for_status()
    df2 = pd.read_csv(pd.io.common.StringIO(r2.text), sep="|")
    df2 = df2[df2["ACT Symbol"] != "File Creation Time"].copy()
    if "ETF" in df2.columns:
        df2 = df2[df2["ETF"].fillna("N") == "N"]
    if "Test Issue" in df2.columns:
        df2 = df2[df2["Test Issue"].fillna("N") == "N"]
    df2 = df2.rename(columns={"ACT Symbol": "Symbol", "Exchange": "listing_exchange"})
    df2 = df2[["Symbol", "listing_exchange"]].copy()

    exchange_map = {
        "N": "NYSE",
        "A": "NYSE American",
        "P": "NYSE Arca",
        "Z": "Cboe BZX",
        "V": "IEX",
    }
    df2["listing_exchange"] = df2["listing_exchange"].map(exchange_map).fillna(df2["listing_exchange"])

    df = pd.concat([df1, df2], ignore_index=True)
    df = df.dropna(subset=["Symbol"])
    df["Symbol"] = df["Symbol"].astype(str).str.strip()
    df = df[~df["Symbol"].str.contains(r"[\^\$]", regex=True)]
    df = df[~df["Symbol"].str.contains(r"\.", regex=True)]
    return df.drop_duplicates(subset=["Symbol"]).reset_index(drop=True)


def normalize_number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def fetch_quote_metadata(symbols, config):
    batch_size = int(config.get("batch_size_quotes", 100))
    min_cap = float(config["market_cap_min"])
    metadata = {}

    for start in range(0, len(symbols), batch_size):
        batch = symbols[start : start + batch_size]
        try:
            q = Ticker(batch, asynchronous=True, max_workers=8)
            price_data = q.price
        except Exception as exc:
            print(f"Quote batch failed {start}-{start + len(batch)}: {exc}")
            time.sleep(1)
            continue

        if not isinstance(price_data, dict):
            continue

        for symbol in batch:
            info = price_data.get(symbol, {})
            if not isinstance(info, dict):
                continue
            market_cap = normalize_number(info.get("marketCap"))
            if market_cap is None or market_cap < min_cap:
                continue
            metadata[symbol] = {
                "market_cap": market_cap,
                "company": info.get("shortName") or info.get("longName") or symbol,
                "exchange": info.get("exchangeName") or info.get("fullExchangeName") or "",
            }
        time.sleep(0.1)

    return metadata


def normalize_history(history):
    if history is None or not hasattr(history, "reset_index"):
        return pd.DataFrame()
    df = history.reset_index()
    if df.empty or "symbol" not in df.columns or "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce").dt.tz_convert(None)
    df = df.dropna(subset=["date"])
    return df.sort_values(["symbol", "date"])


def fetch_histories(symbols, config):
    period = config.get("history_period", "15mo")
    batch_size = int(config.get("batch_size_history", 60))
    frames = []

    for start in range(0, len(symbols), batch_size):
        batch = symbols[start : start + batch_size]
        try:
            t = Ticker(batch, asynchronous=True, max_workers=8)
            hist = t.history(period=period, interval="1d")
            df = normalize_history(hist)
            if not df.empty:
                frames.append(df)
        except Exception as exc:
            print(f"History batch failed {start}-{start + len(batch)}: {exc}")
            time.sleep(1)
            continue
        time.sleep(0.1)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def pct_return_from_bars(closes: pd.Series, bars_back: int):
    if len(closes) <= bars_back:
        return None
    current = float(closes.iloc[-1])
    past = float(closes.iloc[-1 - bars_back])
    return None if past <= 0 else (current / past - 1.0) * 100.0


def percentile_rank(series: pd.Series):
    return series.rank(method="average", pct=True) * 100.0


def find_swing_points(highs: pd.Series, lows: pd.Series, window=3, lookback=90):
    """Find confirmed local highs/lows using a symmetric left/right window.

    A swing high must be the highest high within +/- window bars. A swing low uses
    the same rule on lows. The last `window` bars are not used because they do not
    yet have enough future bars to confirm a swing.
    """
    n = len(highs)
    start = max(window, n - int(lookback))
    end = n - window
    local_highs, local_lows = [], []

    for i in range(start, end):
        h_slice = highs.iloc[i - window : i + window + 1]
        l_slice = lows.iloc[i - window : i + window + 1]
        h = float(highs.iloc[i])
        l = float(lows.iloc[i])
        if h >= float(h_slice.max()):
            local_highs.append((i, h))
        if l <= float(l_slice.min()):
            local_lows.append((i, l))

    return local_highs, local_lows


def extract_contractions(local_highs, local_lows, max_count=3):
    """Pair each swing high with the next swing low before the next swing high."""
    contractions = []
    for pos, (hi_idx, hi_price) in enumerate(local_highs):
        next_hi_idx = local_highs[pos + 1][0] if pos + 1 < len(local_highs) else 10**9
        lows_after = [(idx, price) for idx, price in local_lows if hi_idx < idx < next_hi_idx]
        if not lows_after:
            continue
        low_idx, low_price = min(lows_after, key=lambda x: x[1])
        pct = (hi_price - low_price) / hi_price * 100.0
        if 2.0 <= pct <= 40.0:
            contractions.append({
                "high_idx": hi_idx,
                "high": hi_price,
                "low_idx": low_idx,
                "low": low_price,
                "pct": pct,
            })
    return contractions[-max_count:]


def detect_pivot(local_highs, current_close, n_bars, config):
    """Estimate pivot from a recent resistance cluster of confirmed swing highs."""
    lookback = int(config.get("pivot_lookback_days", 60))
    cluster_pct = float(config.get("pivot_cluster_pct", 2.0)) / 100.0
    candidates = [(idx, price) for idx, price in local_highs if idx >= n_bars - lookback]
    if not candidates:
        return None, "No confirmed swing high"

    # Ignore resistance levels implausibly far from current price for entry timing.
    candidates = [(i, p) for i, p in candidates if current_close * 0.85 <= p <= current_close * 1.12]
    if not candidates:
        return None, "No nearby resistance"

    best_cluster = None
    best_score = -1
    for anchor_idx, anchor_price in candidates:
        cluster = [(i, p) for i, p in candidates if abs(p / anchor_price - 1.0) <= cluster_pct]
        latest_idx = max(i for i, _ in cluster)
        score = len(cluster) * 1000 + latest_idx
        if score > best_score:
            best_score = score
            best_cluster = cluster

    if best_cluster and len(best_cluster) >= 2:
        return max(p for _, p in best_cluster), f"Resistance cluster ({len(best_cluster)} swing highs)"

    recent_idx, recent_price = max(candidates, key=lambda x: x[0])
    return recent_price, "Recent confirmed swing high"


def analyse_entry_setup(stock_hist, ma10, ma20, ma50, config):
    hist = stock_hist.dropna(subset=["close", "high", "low", "volume"]).sort_values("date").copy()
    closes = hist["close"].astype(float).reset_index(drop=True)
    highs = hist["high"].astype(float).reset_index(drop=True)
    lows = hist["low"].astype(float).reset_index(drop=True)
    volumes = hist["volume"].astype(float).reset_index(drop=True)

    if len(closes) < 100:
        return {}

    close = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2])
    avg_vol_50 = float(volumes.iloc[-50:].mean()) if len(volumes) >= 50 else float(volumes.mean())
    avg_vol_10 = float(volumes.iloc[-10:].mean())
    prior_vol_30 = float(volumes.iloc[-40:-10].mean()) if len(volumes) >= 40 else avg_vol_50
    volume_ratio = float(volumes.iloc[-1] / avg_vol_50) if avg_vol_50 > 0 else None
    volume_dryup_ratio = float(avg_vol_10 / prior_vol_30) if prior_vol_30 > 0 else None

    window = int(config.get("swing_window", 3))
    lookback = int(config.get("swing_lookback_days", 90))
    local_highs, local_lows = find_swing_points(highs, lows, window, lookback)
    contractions = extract_contractions(local_highs, local_lows, max_count=3)
    pivot, pivot_reason = detect_pivot(local_highs, close, len(closes), config)

    dist_pivot = ((close / pivot) - 1.0) * 100.0 if pivot else None
    dist_10ma = (close / ma10 - 1.0) * 100.0 if ma10 else None
    dist_20ma = (close / ma20 - 1.0) * 100.0 if ma20 else None
    dist_50ma = (close / ma50 - 1.0) * 100.0 if ma50 else None

    # VCP approximation: shrinking pullbacks, rising/steady lows, volume dry-up,
    # and price sitting close to the estimated pivot.
    contraction_pcts = [c["pct"] for c in contractions]
    contraction_lows = [c["low"] for c in contractions]
    shrinking = False
    rising_lows = False
    if len(contraction_pcts) >= 2:
        shrinking = all(
            contraction_pcts[i] <= contraction_pcts[i - 1] * 0.90
            for i in range(1, len(contraction_pcts))
        )
        rising_lows = all(
            contraction_lows[i] >= contraction_lows[i - 1] * 0.98
            for i in range(1, len(contraction_lows))
        )
    last_contraction_tight = bool(contraction_pcts and contraction_pcts[-1] <= 10.0)
    dryup = volume_dryup_ratio is not None and volume_dryup_ratio <= float(config.get("vcp_volume_dryup_ratio_max", 0.8))
    near_vcp_pivot = dist_pivot is not None and -5.0 <= dist_pivot <= 2.0
    vcp_candidate = len(contraction_pcts) >= 2 and shrinking and rising_lows and last_contraction_tight and dryup and near_vcp_pivot

    breakout_volume_min = float(config.get("breakout_volume_ratio_min", 1.3))
    confirmed_breakout = bool(
        pivot
        and prev_close <= pivot < close
        and dist_pivot is not None
        and dist_pivot <= float(config.get("extended_above_pivot_pct", 5.0))
        and volume_ratio is not None
        and volume_ratio >= breakout_volume_min
    )

    # Pullback candidate: price has retreated at least 3% from the recent 10-day high,
    # is sitting near 10MA/20MA, remains above 50MA, and is not closing weakly today.
    recent_10d_high_close = float(closes.iloc[-10:].max())
    pullback_depth = (close / recent_10d_high_close - 1.0) * 100.0
    tol = float(config.get("pullback_ma_tolerance_pct", 2.5))
    near_10 = dist_10ma is not None and abs(dist_10ma) <= tol
    near_20 = dist_20ma is not None and abs(dist_20ma) <= tol
    turning_up = close >= prev_close
    pullback_candidate = bool(pullback_depth <= -3.0 and close > ma50 and (near_10 or near_20) and turning_up)
    pullback_label = "10MA Pullback" if near_10 else "20MA Pullback"

    extended = bool(
        (dist_pivot is not None and dist_pivot > float(config.get("extended_above_pivot_pct", 5.0)))
        or (dist_20ma is not None and dist_20ma > float(config.get("extended_above_20ma_pct", 10.0)))
        or (dist_50ma is not None and dist_50ma > float(config.get("extended_above_50ma_pct", 15.0)))
    )

    near_pivot_pct = float(config.get("near_pivot_pct", 3.0))
    near_pivot = dist_pivot is not None and -near_pivot_pct <= dist_pivot <= 0.5
    post_breakout = dist_pivot is not None and 0 < dist_pivot <= float(config.get("extended_above_pivot_pct", 5.0))

    if extended:
        setup = "Extended"
        status = "WAIT"
    elif confirmed_breakout:
        setup = "Confirmed Breakout"
        status = "READY"
    elif pullback_candidate:
        setup = pullback_label
        status = "READY"
    elif vcp_candidate:
        setup = "VCP Candidate"
        status = "WATCH"
    elif near_pivot:
        setup = "Near Pivot"
        status = "WATCH"
    elif post_breakout:
        setup = "Post-breakout"
        status = "WATCH"
    else:
        setup = "Strong Trend"
        status = "WATCH"

    return {
        "pivot": pivot,
        "pivot_reason": pivot_reason,
        "dist_from_pivot_pct": dist_pivot,
        "dist_from_10ma_pct": dist_10ma,
        "dist_from_20ma_pct": dist_20ma,
        "dist_from_50ma_pct": dist_50ma,
        "volume_ratio_50d": volume_ratio,
        "volume_dryup_ratio": volume_dryup_ratio,
        "contractions": contraction_pcts,
        "vcp_candidate": vcp_candidate,
        "confirmed_breakout": confirmed_breakout,
        "pullback_candidate": pullback_candidate,
        "extended": extended,
        "entry_setup": setup,
        "entry_status": status,
    }


def calculate_stock_metrics(symbol, stock_hist, meta, benchmark_returns, min_rows, config):
    stock_hist = stock_hist.dropna(subset=["close"]).sort_values("date").copy()
    closes = stock_hist["close"].astype(float).reset_index(drop=True)
    if len(closes) < min_rows:
        return None

    recent_close = float(closes.iloc[-1])
    ma10 = float(closes.rolling(10).mean().iloc[-1])
    ma20 = float(closes.rolling(20).mean().iloc[-1])
    ma50 = float(closes.rolling(50).mean().iloc[-1])
    ma150 = float(closes.rolling(150).mean().iloc[-1])
    ma200_series = closes.rolling(200).mean()
    ma200 = float(ma200_series.iloc[-1])
    ma200_21d_ago = float(ma200_series.iloc[-22]) if len(ma200_series) >= 222 else None

    trailing_52w = closes.iloc[-252:]
    high_52w = float(trailing_52w.max())
    low_52w = float(trailing_52w.min())

    ret_5d = pct_return_from_bars(closes, 5)
    ret_20d = pct_return_from_bars(closes, 20)
    ret_60d = pct_return_from_bars(closes, 60)
    ret_63d = pct_return_from_bars(closes, 63)
    ret_126d = pct_return_from_bars(closes, 126)
    ret_189d = pct_return_from_bars(closes, 189)
    ret_252d = pct_return_from_bars(closes, 252)
    needed = [ret_5d, ret_20d, ret_60d, ret_63d, ret_126d, ret_189d, ret_252d]
    if any(v is None for v in needed):
        return None

    # Liquidity: average daily dollar volume = mean(close * shares traded) over 20 days.
    if "volume" not in stock_hist.columns:
        return None
    dollar_volume = stock_hist["close"].astype(float) * stock_hist["volume"].astype(float)
    avg_dollar_volume_20d = float(dollar_volume.iloc[-20:].mean())

    rs_raw = 0.40 * ret_63d + 0.20 * ret_126d + 0.20 * ret_189d + 0.20 * ret_252d
    rs_5d_vs_spy = ret_5d - benchmark_returns["ret_5d"]
    rs_20d_vs_spy = ret_20d - benchmark_returns["ret_20d"]
    rs_60d_vs_spy = ret_60d - benchmark_returns["ret_60d"]
    dist_from_high = (recent_close / high_52w - 1.0) * 100.0
    pct_above_low = (recent_close / low_52w - 1.0) * 100.0

    structural_conditions = {
        "price_above_150_200": recent_close > ma150 and recent_close > ma200,
        "ma150_above_200": ma150 > ma200,
        "ma200_rising": ma200_21d_ago is not None and ma200 > ma200_21d_ago,
        "ma50_above_150_200": ma50 > ma150 and ma50 > ma200,
        "price_above_50": recent_close > ma50,
        "price_30pct_above_52w_low": recent_close >= low_52w * 1.30,
        "price_within_25pct_52w_high": recent_close >= high_52w * 0.75,
    }

    entry = analyse_entry_setup(stock_hist, ma10, ma20, ma50, config)

    return {
        "symbol": symbol,
        "company": meta["company"],
        "exchange": meta["exchange"],
        "market_cap": meta["market_cap"],
        "recent_close": recent_close,
        "ma10": ma10,
        "ma20": ma20,
        "ma50": ma50,
        "ma150": ma150,
        "ma200": ma200,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "dist_from_52w_high_pct": dist_from_high,
        "pct_above_52w_low": pct_above_low,
        "avg_dollar_volume_20d": avg_dollar_volume_20d,
        "five_day_return_pct": ret_5d,
        "twenty_day_return_pct": ret_20d,
        "sixty_day_return_pct": ret_60d,
        "rs_5d_vs_spy_pct": rs_5d_vs_spy,
        "rs_20d_vs_spy_pct": rs_20d_vs_spy,
        "rs_60d_vs_spy_pct": rs_60d_vs_spy,
        "custom_rs_raw": rs_raw,
        "structural_conditions": structural_conditions,
        **entry,
    }


def benchmark_metrics(history_df, benchmark_symbol):
    bh = history_df[history_df["symbol"] == benchmark_symbol].copy()
    if bh.empty:
        raise RuntimeError(f"No benchmark history returned for {benchmark_symbol}")
    closes = bh.dropna(subset=["close"]).sort_values("date")["close"].astype(float).reset_index(drop=True)
    values = {
        "ret_5d": pct_return_from_bars(closes, 5),
        "ret_20d": pct_return_from_bars(closes, 20),
        "ret_60d": pct_return_from_bars(closes, 60),
    }
    if any(v is None for v in values.values()):
        raise RuntimeError(f"Not enough benchmark history for {benchmark_symbol}")
    return values


def round_or_none(value, digits=1):
    n = normalize_number(value)
    return None if n is None else round(n, digits)


def build_results():
    config = load_config()
    benchmark_symbol = str(config.get("benchmark_symbol", "SPY")).upper()

    symbols_df = fetch_us_symbols()
    all_symbols = symbols_df["Symbol"].tolist()
    print(f"Listed non-ETF symbols: {len(all_symbols)}")

    metadata = fetch_quote_metadata(all_symbols, config)
    large_cap_symbols = sorted(metadata.keys())
    print(f"Market-cap eligible: {len(large_cap_symbols)}")

    history_symbols = sorted(set(large_cap_symbols + [benchmark_symbol]))
    history_df = fetch_histories(history_symbols, config)
    if history_df.empty:
        raise RuntimeError("No history data returned; keeping previous results.json")

    benchmark = benchmark_metrics(history_df, benchmark_symbol)
    min_rows = int(config.get("min_history_rows", 260))
    metrics = []

    for symbol in large_cap_symbols:
        stock_hist = history_df[history_df["symbol"] == symbol]
        if stock_hist.empty:
            continue
        try:
            row = calculate_stock_metrics(symbol, stock_hist, metadata[symbol], benchmark, min_rows, config)
            if row:
                metrics.append(row)
        except Exception as exc:
            print(f"Metric error {symbol}: {exc}")

    minimum_ok = int(config.get("minimum_data_eligible_stocks", 100))
    if len(metrics) < minimum_ok:
        raise RuntimeError(
            f"Only {len(metrics)} stocks had usable history (< {minimum_ok}); refusing to overwrite results.json."
        )

    df = pd.DataFrame(metrics)
    df["rs_rating"] = percentile_rank(df["custom_rs_raw"]).round(0)

    template_scores, template_passes = [], []
    for idx, row in df.iterrows():
        conditions = dict(row["structural_conditions"])
        conditions["rs_rating_70_plus"] = float(row["rs_rating"]) >= 70.0
        score = sum(bool(v) for v in conditions.values())
        template_scores.append(score)
        template_passes.append(score == 8)
        df.at[idx, "structural_conditions"] = conditions
    df["trend_template_score"] = template_scores
    df["trend_template_pass"] = template_passes

    min_liquidity = float(config.get("min_avg_dollar_volume_20d", 20_000_000))
    min_rs = float(config.get("min_rs_rating", 80))
    min_rel20 = float(config.get("min_rs_20d_vs_spy_pct", 0))
    min_rel60 = float(config.get("min_rs_60d_vs_spy_pct", 0))
    max_from_high = float(config.get("max_dist_from_52w_high_pct", 15))

    liquidity_mask = df["avg_dollar_volume_20d"] >= min_liquidity
    final_mask = (
        liquidity_mask
        & df["trend_template_pass"]
        & (df["rs_rating"] >= min_rs)
        & (df["rs_20d_vs_spy_pct"] >= min_rel20)
        & (df["rs_60d_vs_spy_pct"] >= min_rel60)
        & (df["dist_from_52w_high_pct"] >= -max_from_high)
    )
    final_df = df[final_mask].copy()

    status_order = {"READY": 0, "WATCH": 1, "WAIT": 2}
    final_df["status_order"] = final_df["entry_status"].map(status_order).fillna(9)
    final_df = final_df.sort_values(
        ["status_order", "rs_rating", "rs_60d_vs_spy_pct", "dist_from_52w_high_pct"],
        ascending=[True, False, False, False],
    )

    rows = []
    for _, row in final_df.iterrows():
        rows.append({
            "symbol": row["symbol"],
            "company": row["company"],
            "exchange": row["exchange"],
            "market_cap": int(row["market_cap"]),
            "recent_close": round_or_none(row["recent_close"], 2),
            "avg_dollar_volume_20d": round_or_none(row["avg_dollar_volume_20d"], 0),
            "trend_template_score": int(row["trend_template_score"]),
            "trend_template_pass": bool(row["trend_template_pass"]),
            "rs_rating": int(row["rs_rating"]),
            "rs_20d_vs_spy_pct": round_or_none(row["rs_20d_vs_spy_pct"], 1),
            "rs_60d_vs_spy_pct": round_or_none(row["rs_60d_vs_spy_pct"], 1),
            "high_52w": round_or_none(row["high_52w"], 2),
            "low_52w": round_or_none(row["low_52w"], 2),
            "dist_from_52w_high_pct": round_or_none(row["dist_from_52w_high_pct"], 1),
            "pct_above_52w_low": round_or_none(row["pct_above_52w_low"], 1),
            "ma10": round_or_none(row["ma10"], 2),
            "ma20": round_or_none(row["ma20"], 2),
            "ma50": round_or_none(row["ma50"], 2),
            "ma150": round_or_none(row["ma150"], 2),
            "ma200": round_or_none(row["ma200"], 2),
            "pivot": round_or_none(row.get("pivot"), 2),
            "pivot_reason": row.get("pivot_reason"),
            "dist_from_pivot_pct": round_or_none(row.get("dist_from_pivot_pct"), 1),
            "dist_from_10ma_pct": round_or_none(row.get("dist_from_10ma_pct"), 1),
            "dist_from_20ma_pct": round_or_none(row.get("dist_from_20ma_pct"), 1),
            "dist_from_50ma_pct": round_or_none(row.get("dist_from_50ma_pct"), 1),
            "volume_ratio_50d": round_or_none(row.get("volume_ratio_50d"), 2),
            "volume_dryup_ratio": round_or_none(row.get("volume_dryup_ratio"), 2),
            "contractions": [round(float(x), 1) for x in (row.get("contractions") or [])],
            "vcp_candidate": bool(row.get("vcp_candidate", False)),
            "confirmed_breakout": bool(row.get("confirmed_breakout", False)),
            "pullback_candidate": bool(row.get("pullback_candidate", False)),
            "extended": bool(row.get("extended", False)),
            "entry_setup": row.get("entry_setup") or "Strong Trend",
            "entry_status": row.get("entry_status") or "WATCH",
            "template_conditions": row["structural_conditions"],
        })

    output = {
        "generated_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "method": "Minervini trend + custom RS + liquidity + entry setup scanner",
        "rules": {
            "market_cap_min": config["market_cap_min"],
            "benchmark_symbol": benchmark_symbol,
            "min_avg_dollar_volume_20d": min_liquidity,
            "trend_template_required_score": 8,
            "template_rs_rating_min": 70,
            "min_rs_rating": min_rs,
            "min_rs_20d_vs_spy_pct": min_rel20,
            "min_rs_60d_vs_spy_pct": min_rel60,
            "max_dist_from_52w_high_pct": max_from_high,
            "near_pivot_pct": config.get("near_pivot_pct", 3.0),
            "extended_above_pivot_pct": config.get("extended_above_pivot_pct", 5.0),
            "breakout_volume_ratio_min": config.get("breakout_volume_ratio_min", 1.3),
            "spy_twenty_day_return_pct": round_or_none(benchmark["ret_20d"], 1),
            "spy_sixty_day_return_pct": round_or_none(benchmark["ret_60d"], 1),
            "rs_definition": "Custom percentile: 40% 3M + 20% 6M + 20% 9M + 20% 12M cumulative returns; not proprietary IBD RS Rating.",
        },
        "scan_stats": {
            "listed_non_etf_symbols": len(all_symbols),
            "market_cap_eligible": len(large_cap_symbols),
            "data_eligible": len(metrics),
            "liquidity_eligible": int(liquidity_mask.sum()),
            "trend_template_8_of_8": int(df["trend_template_pass"].sum()),
            "final_matches": len(rows),
            "entry_ready": sum(r["entry_status"] == "READY" for r in rows),
            "entry_watch": sum(r["entry_status"] == "WATCH" for r in rows),
            "entry_wait": sum(r["entry_status"] == "WAIT" for r in rows),
        },
        "results": rows,
    }

    tmp_path = RESULTS_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    tmp_path.replace(RESULTS_PATH)
    print(f"Done. final matches={len(rows)} | READY={output['scan_stats']['entry_ready']} | WATCH={output['scan_stats']['entry_watch']} | WAIT={output['scan_stats']['entry_wait']}")


if __name__ == "__main__":
    build_results()
