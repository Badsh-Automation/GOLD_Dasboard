"""Gold Confirmation Engine - Python port of the TradingView 'Gold Confirmation Dashboard'.
Run:  python gold_engine.py        -> writes docs/data.json (+ optional Telegram alert)
Config: config.json.  News block: set "news_block": true or add windows to "news_windows_utc",
e.g. [["2026-11-06T13:15","2026-11-06T14:00"]]   (UTC)
Telegram (optional): env TELEGRAM_TOKEN and TELEGRAM_CHAT_ID.
"""
import json, os, urllib.parse, urllib.request
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data.json")
C = json.load(open(os.path.join(HERE, "config.json")))
STEP = {"5m": 5, "15m": 15, "30m": 30, "60m": 60}[C["interval"]]


def rma(s, n): return s.ewm(alpha=1 / n, adjust=False).mean()
def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(c, n):
    d = c.diff()
    up, dn = rma(d.clip(lower=0), n), rma((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def true_range(df):
    pc = df.close.shift(1)
    return pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)


def dmi(df, n):
    up, dn = df.high.diff(), -df.low.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = rma(true_range(df), n)
    dip, dim = 100 * rma(plus, n) / tr, 100 * rma(minus, n) / tr
    dx = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dip, dim, rma(dx.fillna(0), n)


def fetch(sym, interval, period):
    df = yf.Ticker(sym).history(period=period, interval=interval, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"No data for {sym}")
    df.index = df.index.tz_convert("UTC")
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close"]].dropna()


def sign(a, b):  # 1 if a<b (bullish for gold-inverse assets), -1 if a>b
    return np.where(a < b, 1, np.where(a > b, -1, 0))


def build():
    g = fetch(C["gold"], C["interval"], "30d")
    # drop the still-forming candle
    now = pd.Timestamp.now(tz="UTC")
    if g.index[-1] + pd.Timedelta(minutes=STEP) > now:
        g = g.iloc[:-1]
    idx = g.index

    # 1. HTF trend (4h built from 1h; uses last CLOSED 4h bar -> no lookahead)
    h = fetch(C["gold"], "60m", "120d").close.resample("4h").last().dropna()
    hf, hs = ema(h, C["htf_fast"]), ema(h, C["htf_slow"])
    hs_sig = pd.Series(np.where((h > hf) & (hf > hs), 1, np.where((h < hf) & (hf < hs), -1, 0)), index=h.index).shift(1)
    htf = hs_sig.reindex(idx, method="ffill").fillna(0).astype(int)

    # 2/3. Intermarket
    def inter(sym):
        s = fetch(sym, C["interval"], "30d").close
        e = ema(s, C["inter_len"])
        sig = pd.Series(sign(s.values, e.values), index=s.index)
        return s.reindex(idx, method="ffill"), sig.reindex(idx, method="ffill").fillna(0).astype(int)
    dxy, dxy_sig = inter(C["dxy"])
    try:
        yld, yld_sig = inter(C["us10y"]) if C["use_yield"] else (pd.Series(np.nan, index=idx), pd.Series(0, index=idx))
    except Exception:
        yld, yld_sig = pd.Series(np.nan, index=idx), pd.Series(0, index=idx)
    if not C["use_yield"]:
        yld_sig = pd.Series(0, index=idx)

    # 4. Price action (engulf / pin bar within last 3 candles)
    o, hi, lo, cl = g.open, g.high, g.low, g.close
    body = (cl - o).abs()
    upw, low_w = hi - np.maximum(cl, o), np.minimum(cl, o) - lo
    bull_e = (cl > o) & (cl.shift(1) < o.shift(1)) & (cl >= o.shift(1)) & (o <= cl.shift(1))
    bear_e = (cl < o) & (cl.shift(1) > o.shift(1)) & (cl <= o.shift(1)) & (o >= cl.shift(1))
    bull_p = (low_w > 2 * body) & (low_w > 2 * upw)
    bear_p = (upw > 2 * body) & (upw > 2 * low_w)
    bx, sx = bull_e | bull_p, bear_e | bear_p
    bull_pa = bx | bx.shift(1, fill_value=False) | bx.shift(2, fill_value=False)
    bear_pa = sx | sx.shift(1, fill_value=False) | sx.shift(2, fill_value=False)
    pa = pd.Series(np.where(bull_pa & ~bear_pa, 1, np.where(bear_pa & ~bull_pa, -1, 0)), index=idx)

    # 5. Momentum
    e1, r = ema(cl, C["ema_len"]), rsi(cl, C["rsi_len"])
    dip, dim, adx = dmi(g, C["adx_len"])
    mom = pd.Series(np.where((cl > e1) & (r > 50) & (adx > C["adx_min"]) & (dip > dim), 1,
                             np.where((cl < e1) & (r < 50) & (adx > C["adx_min"]) & (dim > dip), -1, 0)), index=idx)

    # Session / news
    in_sess = pd.Series(True, index=idx)
    if C["use_session"]:
        in_sess = pd.Series((idx.hour >= C["session_start_utc"]) & (idx.hour < C["session_end_utc"]), index=idx)
    news_ok = pd.Series(not C["news_block"], index=idx)
    for a, b in C["news_windows_utc"]:
        news_ok &= ~((idx >= pd.Timestamp(a, tz="UTC")) & (idx <= pd.Timestamp(b, tz="UTC")))
    ok = in_sess & news_ok

    # Score
    max_pts = 5 if C["use_yield"] else 4
    need = min(C["min_score"], max_pts)
    sigs = [htf, dxy_sig, yld_sig, pa, mom]
    buy_s = sum((s == 1).astype(int) for s in sigs)
    sell_s = sum((s == -1).astype(int) for s in sigs)
    buy_ok = (buy_s >= need) & (htf != -1) & ok
    sell_ok = (sell_s >= need) & (htf != 1) & ok
    buy_new, sell_new = buy_ok & ~buy_ok.shift(1, fill_value=False), sell_ok & ~sell_ok.shift(1, fill_value=False)
    atr = rma(true_range(g), C["atr_len"])

    # signal history
    history = []
    for t in idx[buy_new | sell_new][-40:]:
        side = "BUY" if buy_new[t] else "SELL"
        k = 1 if side == "BUY" else -1
        a = float(atr[t]); p = float(cl[t])
        history.append(dict(t=t.isoformat(), side=side, entry=round(p, 2),
                            sl=round(p - k * a * C["sl_mult"], 2), tp=round(p + k * a * C["sl_mult"] * C["rr"], 2)))

    L = idx[-1]
    final = "BUY" if buy_ok[L] else "SELL" if sell_ok[L] else "WAIT"
    candles = [[t.isoformat(), *[round(float(x), 2) for x in (o[t], hi[t], lo[t], cl[t], e1[t])]] for t in idx[-120:]]
    f = lambda x: None if pd.isna(x) else round(float(x), 3)
    return dict(
        updated=datetime.now(timezone.utc).isoformat(), symbol=C["gold"], interval=C["interval"],
        bar_time=L.isoformat(), price=f(cl[L]), final=final, need=need, max_pts=max_pts,
        buy_score=int(buy_s[L]), sell_score=int(sell_s[L]),
        factors=dict(
            htf=dict(sig=int(htf[L]), detail=f"4H EMA{C['htf_fast']}/{C['htf_slow']}"),
            dxy=dict(sig=int(dxy_sig[L]), detail=str(f(dxy[L]))),
            us10y=dict(sig=int(yld_sig[L]), detail="OFF" if not C["use_yield"] else str(f(yld[L]))),
            price_action=dict(sig=int(pa[L]), detail="Engulf / Pin"),
            momentum=dict(sig=int(mom[L]), detail=f"RSI {r[L]:.0f} ADX {adx[L]:.0f}")),
        session_ok=bool(in_sess[L]), news_ok=bool(news_ok[L]),
        trade=(history[-1] if history and history[-1]["t"] == L.isoformat() else None),
        atr=f(atr[L]), history=history, candles=candles,
        last_alert=None)


def telegram(msg):
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=20)


if __name__ == "__main__":
    out = build()
    prev = {}
    if os.path.exists(OUT):
        try: prev = json.load(open(OUT))
        except Exception: pass
    out["last_alert"] = prev.get("last_alert")
    if out["history"]:
        s = out["history"][-1]
        if s["t"] != out["last_alert"]:
            if out["last_alert"] is None or s["t"] > out["last_alert"]:
                # only alert if signal is fresh (within last 2 candles)
                age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(s["t"])).total_seconds() / 60
                if age <= STEP * 3 and out["last_alert"] is not None:
                    telegram(f"GOLD {s['side']}\nEntry {s['entry']}\nSL {s['sl']}\nTP {s['tp']}\n({s['t']} UTC)")
                out["last_alert"] = s["t"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(out["bar_time"], out["final"], out["price"], f"buy {out['buy_score']} sell {out['sell_score']}")
