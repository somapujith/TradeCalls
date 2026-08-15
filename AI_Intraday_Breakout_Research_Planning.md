# AI-Powered Intraday Breakout Research & Alerting System

## 1. Project Overview

### Objective

Build a Windows-based, always-available stock research system that:

1. Collects official NSE market/reference information.
2. Collects continuous intraday market data through a suitable market-data API/WebSocket.
3. Maintains historical OHLCV data locally.
4. Detects high-quality intraday breakout setups.
5. Calculates breakout levels, confirmation conditions, stop-loss and multiple targets using deterministic quantitative logic.
6. Uses an SLM running locally through LM Studio for fast candidate screening.
7. Uses a larger local LLM through LM Studio for deeper reasoning and explanation.
8. Sends concise trade-research alerts to Telegram.
9. Tracks every generated setup and its outcome for backtesting and continuous improvement.
10. Continues research work on weekends/market holidays while disabling live-market scanning when the exchange is closed.

> Important: The system is a research/alerting engine, not a guaranteed prediction or autonomous trading system. All breakout logic should be backtested before being used with real capital.

---

# 2. Core Design Principle

The system must separate **facts, calculations, AI reasoning, and delivery**.

```text
DATA SOURCES
    ↓
DATA NORMALIZATION
    ↓
QUANTITATIVE CALCULATIONS
    ↓
BREAKOUT DETECTION
    ↓
BREAKOUT CONFIRMATION
    ↓
SLM SCREENING
    ↓
LLM ANALYSIS
    ↓
ENTRY / SL / TARGETS
    ↓
TELEGRAM ALERT
    ↓
OUTCOME TRACKING
    ↓
BACKTESTING
```

### Responsibility of each layer

| Layer | Responsibility |
|---|---|
| NSE / official sources | Official reports, corporate filings, announcements, EOD/reference information |
| Live market-data API | Continuous intraday price, OHLCV and WebSocket updates |
| Python | Indicators, levels, candles, volume, breakout logic, scoring |
| SLM | Fast candidate screening and anomaly/context classification |
| LLM | Deep reasoning, explanation and research-call generation |
| PostgreSQL | Historical storage and signal/outcome tracking |
| Telegram | Notifications |
| React dashboard | Visualization and monitoring |

The LLM should **not invent market numbers** and should not be the primary calculator for breakout levels.

---

# 3. High-Level Architecture

```text
                         ┌──────────────────────┐
                         │     NSE OFFICIAL     │
                         │                      │
                         │ Corporate filings    │
                         │ Announcements        │
                         │ Market reports       │
                         │ EOD/reference data   │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │     NSE DATA DB      │
                         └──────────┬───────────┘
                                    │
                                    │
                 ┌──────────────────▼──────────────────┐
                 │       LIVE MARKET DATA API         │
                 │          WebSocket Feed            │
                 └──────────────────┬─────────────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │   MARKET COLLECTOR  │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │   CANDLE BUILDER     │
                         │ 1m / 5m / 15m / etc. │
                         └──────────┬───────────┘
                                    ▼
               ┌────────────────────┼────────────────────┐
               ▼                    ▼                    ▼
        ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
        │ Price/Trend │      │ Volume      │      │ Candle      │
        │ Engine      │      │ Engine      │      │ Engine      │
        └──────┬──────┘      └──────┬──────┘      └──────┬──────┘
               └────────────────────┼────────────────────┘
                                    ▼
                         ┌──────────────────────┐
                         │   LEVEL ENGINE       │
                         │ Support / Resistance │
                         │ ORB / Swing / VWAP   │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │  BREAKOUT ENGINE     │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │ CONFIRMATION ENGINE  │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │      NEWS ENGINE     │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │     SLM SCREENING    │
                         │       LM Studio      │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │     LLM ANALYSIS     │
                         │       LM Studio      │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │ TRADE SETUP ENGINE   │
                         │ Entry / SL / Targets │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │   TELEGRAM BOT       │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │  OUTCOME TRACKER     │
                         │  Backtesting DB      │
                         └──────────────────────┘
```

---

# 4. Stock Selection Workflow

The system should not send every listed stock to the AI.

## Stage 1 — Stock Universe

Start with the desired NSE equity universe.

Potential filters:

- Equity segment
- Active listing
- Sufficient historical data
- Minimum price
- Minimum average daily turnover
- Minimum average volume
- Exclude suspended/illiquid instruments
- Exclude instruments with unreliable data

Example:

```text
NSE Universe
    ↓
Liquidity Filter
    ↓
~300–500 liquid stocks
```

---

# 5. Liquidity Filter

For each stock calculate:

```text
20D Average Volume
20D Average Turnover
Median Volume
Average Price
Estimated Liquidity
```

Example rules:

```text
Price > ₹50

20D Average Turnover > ₹10 Cr

Adequate intraday liquidity
```

The exact thresholds should later be configurable and backtested.

---

# 6. Pre-Market Research

Before the market opens, prepare the watchlist.

## Suggested timeline

### 08:30

Fetch:

- Previous trading session data
- NSE announcements
- Corporate actions
- Board meetings
- Relevant company news
- Sector news
- Global market context

### 08:45

Calculate:

- Previous day high
- Previous day low
- Previous close
- Weekly high/low
- Monthly levels
- 52-week high/low
- ATR
- Major support/resistance
- Historical volume levels

### 09:00

Generate:

```text
MORNING WATCHLIST
```

Example:

```text
LODHA
Trigger: ₹1,245
Previous High: ₹1,242
Resistance Cluster: ₹1,242–₹1,245
Catalyst: Positive announcement
Setup Quality: A

TATAMOTORS
Trigger: ₹1,048
Sector Strength: High
Setup Quality: A-

INFY
Trigger: ₹1,620
Catalyst: Neutral
Setup Quality: B+
```

---

# 7. Live Market Data

During market hours, use a proper streaming market-data source rather than repeatedly scraping webpages.

## Preferred prototype

Use a broker/data provider with a WebSocket market feed.

### Initial provider to evaluate

**Upstox**

Use for:

- Live price
- LTP
- OHLC
- Volume
- WebSocket streaming
- Instrument information

Upstox currently advertises access to trading and market-data APIs and provides V3 market-data WebSocket documentation.

## Alternative

**Zerodha Kite Connect**

Useful for:

- Live WebSocket
- Historical candles

The paid Connect tier currently provides live WebSocket and historical candle access.

## Development/research-only sources

Potentially useful for prototyping/backtesting:

- yfinance
- Alpha Vantage
- Twelve Data

Do not make an unofficial/free source the production foundation without checking its coverage, latency and usage terms.

---

# 8. NSE Official Data Layer

NSE should remain an important official data/reference layer.

## NSE information to collect where officially available

### Corporate filings

- Corporate announcements
- Corporate actions
- Board meetings
- Company disclosures

### Market reports

- Gainers
- Losers
- New highs
- New lows
- Price-band hits
- Market activity reports

### EOD / historical information

- Open
- High
- Low
- Close
- Volume
- Turnover
- Security details

### Why NSE matters

If a stock suddenly moves:

```text
LODHA +3.5%
```

the system should check:

```text
Was there an NSE corporate announcement?
Was there a filing?
Was there a board meeting?
Was there a corporate action?
```

This provides much stronger context than generic sentiment alone.

---

# 9. Data Licensing / Usage

NSE official website information should not automatically be treated as an unrestricted free real-time API.

NSE offers separate data products for real-time, snapshot, historical, analytical and other uses.

For an automated non-display system that calculates trading signals, verify the applicable data-provider and exchange terms before moving beyond personal experimentation.

Do not build a production system that continuously scrapes NSE webpages for high-frequency real-time data.

---

# 10. Continuous Data Pipeline

During market hours:

```text
WebSocket
    ↓
Tick Data
    ↓
Normalize
    ↓
Store Raw Tick
    ↓
Build 1-minute Candle
    ↓
Build 5-minute Candle
    ↓
Build 15-minute Candle
    ↓
Update Indicators
    ↓
Run Scanner
```

The system should not repeatedly request the latest price through REST if a WebSocket feed is available.

---

# 11. Database Architecture

Use PostgreSQL initially.

Recommended tables:

```text
stocks
instruments
market_ticks
candles_1m
candles_5m
candles_15m
daily_ohlcv
technical_indicators
support_resistance_levels
volume_profiles
news
corporate_announcements
market_sessions
breakout_candidates
breakout_events
trade_setups
telegram_alerts
trade_outcomes
backtest_results
model_decisions
```

---

# 12. Market Calendar

The scheduler must understand the Indian market calendar.

Example states:

```text
TRADING_DAY
PRE_MARKET
MARKET_OPEN
MARKET_CLOSED
WEEKEND
EXCHANGE_HOLIDAY
```

Do not assume:

```text
Monday–Friday = trading
Saturday/Sunday = closed
```

Use an exchange-aware calendar.

---

# 13. What happens on non-working days?

The live intraday scanner should be OFF.

But the research engine remains active.

## Saturday

Run:

- Database maintenance
- Historical calculations
- Weekly technical scan
- Backtests
- Corporate-news processing
- Watchlist preparation

## Sunday

Run:

- Global market research
- Sector analysis
- Company/news analysis
- Weekly support/resistance calculation
- Monday watchlist preparation

## Market holiday

Same behavior:

```text
LIVE PRICE SCANNER = OFF

RESEARCH ENGINE = ON
NEWS ENGINE = ON
DATABASE = ON
BACKTEST ENGINE = ON
AI ENGINE = ON
```

---

# 14. Friday → Monday Workflow

This is especially important for gap setups.

### Friday

```text
LODHA

Resistance: ₹1,245
Current: ₹1,238

Setup Score: 76

WATCH
```

### Saturday

```text
Positive company announcement detected
```

### Sunday

```text
Sector strength positive
Global markets positive
```

### Monday 08:45

```text
Pre-market indication: ₹1,252
```

System:

```text
GAP-UP WATCH

Previous breakout level: ₹1,245

Do not chase automatically.

Wait for:
- Opening structure
- VWAP
- Volume
- 5m confirmation
```

---

# 15. Technical Indicator Engine

Calculate indicators in Python, not through the LLM.

## Trend

- EMA 9
- EMA 20
- EMA 50
- SMA 100
- SMA 200

## Momentum

- RSI
- MACD
- Stochastic RSI
- ROC
- CCI
- Williams %R

## Volatility

- ATR
- Bollinger Bands
- Historical volatility

## Market structure

- Swing highs/lows
- Higher highs
- Higher lows
- Lower highs
- Lower lows
- Consolidation ranges

## Intraday

- VWAP
- Opening range
- Day high/low
- Previous day high/low
- Previous week high/low
- Relative volume

---

# 16. Candle Engine

For every important candle calculate:

```text
Open
High
Low
Close
Range
Body
Upper Wick
Lower Wick
Body %
Upper Wick %
Lower Wick %
Close Location
Range / ATR
```

Detect patterns such as:

- Doji
- Hammer
- Inverted Hammer
- Marubozu
- Bullish Engulfing
- Bearish Engulfing
- Morning Star
- Evening Star
- Tweezer patterns
- Strong breakout candle
- Rejection candle

Candlestick patterns should never be used alone.

---

# 17. Volume Engine

Use Relative Volume rather than raw volume alone.

Basic concept:

```text
RVOL =
Actual Volume
/
Expected Volume At This Time Of Day
```

Example:

```text
Expected = 500,000
Actual   = 1,800,000

RVOL = 3.6x
```

Detect:

- Volume spike
- Volume expansion
- Volume contraction
- Breakout volume
- Selling climax
- Accumulation behavior
- Volume confirmation
- Volume divergence

---

# 18. Intraday Volume Curve

Expected volume changes during the trading day.

Store expected volume by time bucket:

```text
09:15–09:30
09:30–10:00
10:00–10:30
...
15:00–15:30
```

This makes RVOL much more meaningful.

---

# 19. Level Detection Engine

The engine should find structural levels from multiple sources.

## Levels

```text
Previous Day High
Previous Day Low
Previous Week High
Previous Week Low
Monthly High/Low
52W High/Low
Opening Range High
Opening Range Low
Intraday Swing High
Intraday Swing Low
Consolidation High
Consolidation Low
VWAP
Volume Profile
Gap Levels
Major Support
Major Resistance
```

Cluster nearby levels.

Example:

```text
Previous High       ₹1,238
Swing High          ₹1,242
Volume resistance   ₹1,244
Opening resistance  ₹1,243

        ↓

Resistance Cluster
₹1,242–₹1,245

Breakout Trigger
₹1,245
```

---

# 20. Breakout Engine

This is the core system.

A breakout should NOT be defined as:

```text
LTP > Resistance
```

Instead, require multiple conditions.

## Basic breakout conditions

```text
Price > Resistance
AND
Candle CLOSE > Resistance
AND
Volume confirmation
AND
Acceptable candle quality
```

Then apply:

- VWAP
- Trend
- Market context
- Sector context
- Relative strength
- Retest
- News catalyst

---

# 21. Opening Range Breakout

Support multiple ORB windows:

```text
5-minute ORB
15-minute ORB
30-minute ORB
```

Example:

```text
09:15–09:30

OR High = ₹720
OR Low  = ₹711
```

Potential breakout:

```text
Price > ₹720
Candle closes > ₹720
RVOL confirmed
VWAP aligned
Market aligned
```

---

# 22. VWAP Breakout Engine

Detect:

- VWAP reclaim
- VWAP rejection
- VWAP breakout
- VWAP retest
- VWAP slope
- Price/VWAP distance

Strong setup:

```text
Price > VWAP
VWAP rising
Resistance breakout
Volume confirmation
```

Weak setup:

```text
Price > resistance
but
Price < VWAP
```

---

# 23. Relative Strength Engine

Compare:

```text
Stock return
vs
Sector return
vs
Index return
```

Example:

```text
NIFTY       +0.3%
AUTO        +0.8%
STOCK       +2.7%
```

Strong relative strength.

Another example:

```text
NIFTY       +1.2%
AUTO        +1.5%
STOCK       +0.2%
```

Weak relative performance.

---

# 24. Market Regime Engine

Classify:

```text
STRONG_BULL
BULL
NEUTRAL
WEAK
BEAR
STRONG_BEAR
```

Inputs:

- NIFTY trend
- BANKNIFTY trend
- India VIX
- Market breadth
- Sector breadth
- Index moving averages
- Volatility
- FII/DII data where available

Market regime should modify breakout confidence.

---

# 25. Sector Engine

For every stock:

```text
Stock
    ↓
Sector
    ↓
Sector Index
    ↓
Relative Strength
```

Example:

```text
LODHA +3.2%
REALTY +2.4%
NIFTY +0.7%
```

Strong sector-supported breakout.

---

# 26. News Engine

Run independently from price scanning.

Sources can include:

- NSE corporate announcements
- NSE filings
- Company announcements
- RSS feeds
- News APIs
- Financial news sources
- Sector news

Classify:

```text
Earnings
Guidance
Order win
Order loss
Regulatory
Legal
Debt
Acquisition
Merger
Management
Promoter activity
Insider activity
Government policy
Sector event
Macro event
Dividend
Buyback
Rating upgrade
Rating downgrade
```

---

# 27. News Severity

Use:

```text
0 = Irrelevant
1 = Minor
2 = Moderate
3 = Significant
4 = Severe
5 = Existential
```

Also determine:

```text
Sentiment
Impact
Expected Duration
Confidence
```

Example:

```text
Order win

Sentiment: +0.92
Impact: HIGH
Duration: MEDIUM/LONG
```

---

# 28. Breakout Score

Initial proposed model:

| Component | Weight |
|---|---:|
| Resistance breakout | 15 |
| Relative volume | 20 |
| Candle quality | 10 |
| VWAP | 10 |
| Trend | 10 |
| Retest | 15 |
| Relative strength | 10 |
| Market confirmation | 5 |
| Sector confirmation | 3 |
| News catalyst | 2 |
| **Total** | **100** |

Suggested interpretation:

```text
90–100 = A+ Exceptional
80–89  = A Strong
70–79  = B Valid / Watch
60–69  = C Weak
<60    = Ignore
```

These weights are starting points only. They must be optimized through historical backtesting.

---

# 29. Hard Rejection Rules

A scoring system should not override critical failures.

Examples:

```text
IF candle does not close above resistance
    → NOT CONFIRMED

IF RVOL is too low
    → downgrade/reject

IF severe rejection wick
    → fake breakout risk

IF price loses VWAP
    → downgrade

IF breakout immediately reverses
    → invalidate

IF market regime is extremely bearish
    → reduce confidence

IF critical negative news is detected
    → reject unless specifically configured otherwise
```

---

# 30. Breakout State Machine

Every candidate should move through states.

```text
WATCH
  ↓
APPROACHING
  ↓
BREAKOUT_ATTEMPT
  ↓
CANDLE_CONFIRMATION
  ↓
VOLUME_CONFIRMATION
  ↓
BREAKOUT_CONFIRMED
  ↓
RETEST_PENDING
  ↓
RETEST_CONFIRMED
  ↓
TRADE_ACTIVE
  ↓
TARGET_HIT / INVALIDATED / SESSION_END
```

This prevents duplicate alerts and reduces noise.

---

# 31. Example: LODHA

Research format:

```text
POSITIONAL RESEARCH

LODHA

Looks good above ₹1,245

SL ₹1,225

Targets:
₹1,252
₹1,255
₹1,260
₹1,265
₹1,270
₹1,275
₹1,280
```

The system should derive this from data.

Example internal calculations:

```text
Resistance Cluster = ₹1,242–₹1,245
Breakout Trigger = ₹1,245

Structural Support = ₹1,225
VWAP = ₹1,232
ATR = ₹18

Volume = 3.4x expected
Candle = Strong bullish close
Sector = Bullish
Market = Bullish
News = Positive
```

---

# 32. Entry Engine

The entry level should be calculated from the breakout structure.

Possible trigger:

```text
Breakout Level
+
Confirmation Buffer
```

But avoid blindly adding a fixed rupee amount to every stock.

The buffer can be volatility-aware:

```text
Buffer = function(ATR, tick size, candle range, liquidity)
```

---

# 33. Stop-Loss Engine

Stop-loss should be structural.

Potential inputs:

```text
Recent swing low
Previous resistance turned support
VWAP
ATR
Consolidation low
Opening range low
Retest low
```

Example:

```text
Entry = ₹1,245

Swing Low = ₹1,225
VWAP = ₹1,232

Structural invalidation = ₹1,225

SL = ₹1,225
```

The exact rule should be backtested.

---

# 34. Target Engine

Do not let the LLM randomly generate targets.

Use:

```text
Structural resistance
ATR
Risk multiples
Volume profile
Previous highs
Day high
Weekly levels
Fibonacci extensions where appropriate
```

Example:

```text
Entry = ₹1,245
SL = ₹1,225

Risk = ₹20

R-based targets:
1R = ₹1,265
2R = ₹1,285
3R = ₹1,305
```

Also calculate market-structure targets.

Then choose or rank targets.

---

# 35. Target Ladder

Support multiple target styles:

```text
T1 = nearest structural resistance
T2 = next resistance
T3 = 1R
T4 = 1.5R
T5 = 2R
...
```

If the research style requires fixed point increments:

```text
+7
+10
+15
+20
+25
+30
+35
```

the system can generate them from the actual entry, but should still validate them against structure and volatility.

---

# 36. SLM Role

The SLM should receive structured data.

Example:

```json
{
  "symbol": "LODHA",
  "price": 1248,
  "breakout_level": 1245,
  "rvol": 3.4,
  "vwap": 1232,
  "atr": 18,
  "candle_body_pct": 78,
  "close_position_pct": 92,
  "market_regime": "BULL",
  "sector_regime": "BULL",
  "news_severity": 1,
  "news_sentiment": 0.72,
  "breakout_score": 88
}
```

SLM output:

```text
VALID_CANDIDATE
```

or:

```text
REJECT
```

The SLM should be optimized for speed.

---

# 37. LLM Role

Only send high-quality candidates to the larger model.

The LLM should:

- Explain why the breakout is meaningful
- Identify conflicting signals
- Summarize news
- Explain risk
- Explain invalidation
- Produce concise research language
- Check whether the quantitative result is internally consistent

The LLM should not be the source of raw prices or calculated indicators.

---

# 38. Telegram Alert

Recommended format:

```text
🚀 BREAKOUT ALERT

LODHA

Looks good above ₹1,245

SL: ₹1,225

🎯 Targets:
₹1,252
₹1,255
₹1,260
₹1,265
₹1,270
₹1,275
₹1,280

Confirmation:
✓ Resistance breakout
✓ Strong candle close
✓ RVOL 3.4×
✓ Above VWAP
✓ Sector aligned
✓ Market aligned

Setup Score: 88/100

⚠️ Invalidation:
Below ₹1,225
```

Keep Telegram concise.

The dashboard can contain the full analysis.

---

# 39. Telegram Alert States

Use different alert types.

### WATCH

```text
Approaching breakout level
```

### BREAKOUT ATTEMPT

```text
Price crossed level
Waiting for close
```

### CONFIRMED

```text
Candle + volume confirmed
```

### RETEST

```text
Breakout level successfully retested
```

### TARGET

```text
T1/T2/T3 reached
```

### INVALIDATED

```text
Setup no longer valid
```

---

# 40. Dashboard

Recommended React dashboard:

```text
┌─────────────────────────────────────────────────────────┐
│ AI BREAKOUT INTELLIGENCE                 ● LIVE         │
├─────────────────────────────────────────────────────────┤
│ NIFTY    BANKNIFTY    VIX    MARKET REGIME             │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ BREAKOUT WATCHLIST                                      │
│                                                         │
│ LODHA       88   ₹1248   ↑   CONFIRMED                │
│ TATAMOTORS  84   ₹1047   ↑   APPROACHING              │
│ INFY        76   ₹1620   ↑   WATCH                    │
│                                                         │
├──────────────────────┬──────────────────────────────────┤
│ BREAKOUT DETAILS     │ NEWS INTELLIGENCE                │
│                      │                                  │
│ Resistance           │ Corporate announcement           │
│ Entry                │ Sentiment                        │
│ VWAP                 │ Severity                         │
│ RVOL                 │ Catalyst                         │
│ Candle               │                                  │
├──────────────────────┴──────────────────────────────────┤
│ CHART                                                    │
│                                                         │
│ Price / VWAP / EMA / Resistance / Breakout / Targets   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

# 41. Backtesting

Backtesting is mandatory before trusting the alerts.

Store:

```text
Signal timestamp
Symbol
Entry
Stop loss
Targets
Breakout score
Market regime
Sector regime
RVOL
Candle statistics
News state
```

Then track:

```text
MFE
MAE
T1 hit
T2 hit
T3 hit
SL hit
Time to target
Maximum drawdown
```

---

# 42. Historical Performance Analysis

Example:

```text
Breakout Score > 85

Total signals: 327

T1 hit: 218
T2 hit: 173
SL hit: 91

Average MFE: X
Average MAE: Y
Win rate: X%
Profit factor: X
Average holding time: X
```

Then analyze by:

- Stock
- Sector
- Time of day
- Breakout type
- RVOL
- Market regime
- Day of week
- News catalyst
- ORB window
- Retest/no-retest
- Score bucket

---

# 43. Machine Learning / AI Improvement

Do not initially let the LLM self-modify the scoring model.

First collect data.

After enough historical signals:

```text
Signals
   ↓
Outcomes
   ↓
Feature dataset
   ↓
Statistical analysis
   ↓
Weight optimization
   ↓
Backtest
   ↓
Paper trading
   ↓
Production
```

Potential future model:

```text
Gradient Boosting
XGBoost
LightGBM
Random Forest
Logistic Regression
```

These can estimate breakout outcome probabilities from structured features.

The LLM can remain the explanation/reasoning layer.

---

# 44. Weekend / Holiday Research Mode

When the market is closed:

```text
LIVE WEBSOCKET = OFF
INTRADAY SCANNER = OFF

NSE RESEARCH = ON
NEWS ENGINE = ON
DATABASE = ON
BACKTESTING = ON
AI ANALYSIS = ON
```

Tasks:

```text
Weekly level calculation
Historical breakout analysis
Corporate announcement processing
News summarization
Sector analysis
Watchlist generation
Model evaluation
Database maintenance
```

---

# 45. Suggested Daily Scheduler

```text
00:00
Database maintenance

06:00
News processing

08:00
NSE/company announcement scan

08:30
Pre-market research

08:45
Calculate levels

09:00
Generate watchlist

09:10
Connect live WebSocket

09:15
Market opens

09:15–15:30
Live breakout scanner

Every 1 minute
Update candles and indicators

Every 5 minutes
Run breakout scan

On candidate
Run SLM

On high-quality candidate
Run LLM

On confirmation
Send Telegram

15:30
Stop live scanner

16:00
Finalize outcomes

17:00
Backtest/update statistics
```

Exact market times and exchange-calendar handling should be configurable rather than hardcoded.

---

# 46. Suggested Project Structure

```text
stock-ai/
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── scheduler.py
│   │   │
│   │   ├── data/
│   │   │   ├── nse_client.py
│   │   │   ├── market_data_client.py
│   │   │   ├── news_client.py
│   │   │   └── normalizer.py
│   │   │
│   │   ├── market/
│   │   │   ├── candles.py
│   │   │   ├── indicators.py
│   │   │   ├── volume.py
│   │   │   ├── levels.py
│   │   │   ├── market_regime.py
│   │   │   └── sector_strength.py
│   │   │
│   │   ├── breakout/
│   │   │   ├── breakout_engine.py
│   │   │   ├── orb.py
│   │   │   ├── vwap.py
│   │   │   ├── candle_confirmation.py
│   │   │   ├── retest.py
│   │   │   └── scoring.py
│   │   │
│   │   ├── ai/
│   │   │   ├── slm.py
│   │   │   ├── llm.py
│   │   │   ├── prompts.py
│   │   │   └── schemas.py
│   │   │
│   │   ├── trading/
│   │   │   ├── entry.py
│   │   │   ├── stop_loss.py
│   │   │   ├── targets.py
│   │   │   └── setup.py
│   │   │
│   │   ├── notifications/
│   │   │   └── telegram.py
│   │   │
│   │   └── backtesting/
│   │       ├── engine.py
│   │       ├── metrics.py
│   │       └── reports.py
│   │
│   ├── tests/
│   └── requirements.txt
│
├── frontend/
│   └── React + Vite
│
├── database/
│   ├── migrations/
│   └── schema.sql
│
├── models/
│   └── LM Studio configuration
│
├── docker-compose.yml
└── README.md
```

---

# 47. Recommended Technology Stack

## Backend

```text
Python
FastAPI
APScheduler
asyncio
Pydantic
```

## Data

```text
PostgreSQL
TimescaleDB optional
Redis optional
```

## Quantitative analysis

```text
NumPy
Pandas
TA-Lib or pandas-ta
SciPy
```

## AI

```text
LM Studio
Local SLM
Local LLM
OpenAI-compatible local API
```

## Frontend

```text
React
Vite
Tailwind CSS
Lightweight Charts / Plotly
```

## Notifications

```text
Telegram Bot API
```

## Deployment

```text
Windows laptop
Python services
LM Studio
PostgreSQL
React frontend
```

---

# 48. Recommended V1

Do not build everything at once.

## V1 — Data + Breakout

Build:

```text
NSE research layer
+
Live market-data WebSocket
+
PostgreSQL
+
1m/5m candles
+
VWAP
+
RVOL
+
Resistance detection
+
Candle confirmation
+
Breakout score
+
Telegram
```

No LLM initially.

First prove that the quantitative engine works.

---

# 49. V2 — AI

Add:

```text
SLM
+
News classification
+
LLM explanation
+
Entry/SL/target explanation
```

---

# 50. V3 — Backtesting

Add:

```text
Historical replay
+
Signal outcome tracking
+
MFE/MAE
+
Score optimization
+
Strategy comparison
```

---

# 51. V4 — Dashboard

Add:

```text
Live market dashboard
+
Breakout heatmap
+
Sector heatmap
+
Signal history
+
AI explanation
+
Backtest analytics
```

---

# 52. V5 — Paper Trading

Before real capital:

```text
Live signals
     ↓
Paper entry
     ↓
Paper SL
     ↓
Paper targets
     ↓
Track outcome
```

Run this for a meaningful sample size before considering real execution.

---

# 53. Final Recommended Architecture

```text
                    NSE OFFICIAL
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
 Corporate           Reports          EOD/History
 Filings
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
                    PostgreSQL
                         ▲
                         │
                LIVE MARKET FEED
                         │
                    WebSocket
                         │
                         ▼
                  Candle Builder
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
       Price          Volume          Candle
       Engine         Engine          Engine
          │              │              │
          └──────────────┼──────────────┘
                         ▼
                  Level Detection
                         │
                         ▼
                  Breakout Engine
                         │
                         ▼
                Confirmation Engine
                         │
                         ▼
                   News Engine
                         │
                         ▼
                   Breakout Score
                         │
                         ▼
                        SLM
                         │
                 ┌───────┴───────┐
                 │               │
               REJECT           PASS
                 │               │
               STOP              ▼
                               LLM
                                 │
                                 ▼
                         Trade Setup Engine
                                 │
                         ┌───────┼────────┐
                         ▼       ▼        ▼
                       Entry     SL     Targets
                         │       │        │
                         └───────┼────────┘
                                 ▼
                              Telegram
                                 │
                                 ▼
                           Outcome Tracker
                                 │
                                 ▼
                             Backtesting
                                 │
                                 ▼
                          Model Improvement
```

---

# 54. Core Principle

The final system should follow this rule:

> **APIs provide the facts. NSE provides official reference information. Python calculates the market structure. The breakout engine determines whether a breakout is real. The SLM filters candidates. The LLM explains the setup. Telegram delivers the signal. Backtesting determines whether the strategy actually works.**

This architecture keeps the system fast, explainable, testable and suitable for running locally on a Windows machine.
