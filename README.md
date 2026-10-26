# Gold Confirmation Dashboard (Python, 24x7 on GitHub)

Runs your TradingView logic (HTF 4H trend + DXY + US10Y + price action + momentum, session/news filter, ATR SL/TP) every 15 min via GitHub Actions and shows it on a GitHub Pages dashboard.

## Setup (5 min)
1. GitHub pe naya repo banao (public, free Pages ke liye) aur ye saari files upload karo (folder structure same rakho, `.github/workflows/run.yml` zaroor).
2. Settings → Actions → General → Workflow permissions → **Read and write permissions**.
3. Settings → Pages → Source: *Deploy from a branch* → `main` / `/docs`.
4. Actions tab → **gold-engine** → *Run workflow* (pehli baar manual).
5. Dashboard: `https://<username>.github.io/<repo>/`

## Telegram alerts (optional)
@BotFather se bot banao → Settings → Secrets and variables → Actions me `TELEGRAM_TOKEN` aur `TELEGRAM_CHAT_ID` add karo.

## Local use
    pip install -r requirements.txt
    python gold_engine.py        # docs/data.json update
    python -m http.server -d docs 8000

## Customize
`config.json` me sab inputs hain (min_score, SL/RR, session UTC hours, news windows).
News ke liye: `"news_block": true` ya `"news_windows_utc": [["2026-11-06T13:15","2026-11-06T14:00"]]`.

## Notes
- Data Yahoo Finance se (free, ~15 min delayed). GC=F futures hai, spot XAUUSD se thoda alag price hota hai.
- GitHub cron kabhi 5-10 min late ho sakta hai; tick-level scalping ke liye ye tool suitable nahi.
- Signals sirf closed candle pe aate hain (repaint nahi).
