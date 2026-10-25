# Methodology and assumptions

## Pattern rules

Both scripts calculate real-body size with `abs(close - open)`, lower wick as `min(open, close) - low`, and upper wick as `high - max(open, close)`. The body comparison uses at least one `syminfo.mintick` to handle doji bars. Wick ratios are user inputs; they are practical filters rather than a claim that every market participant defines these patterns identically.

The candle strategy uses a bullish hammer for long entries and a bearish shooting star for short entries. Its SMA filter aligns each pattern with the current close's side of the moving average. It only enters when flat. That is a simple trend-following pullback example, not a validated trading edge.

## Indicator strategy examples

Each strategy is an independent script. Signals use confirmed bars and pyramiding is disabled. The hammer, Bollinger, and Donchian examples only enter while flat; EMA and momentum entries may reverse an existing position on an opposite signal.

| Script | Entry rule | Exit rule | Intended regime |
|---|---|---|---|
| `ema_rsi_trend.pine` | Fast EMA crosses the slow EMA; RSI must confirm direction. | ATR-distance stop and a configurable R-multiple target, both referenced to signal close. | Momentum / trend changes. Configure the fast EMA below the slow EMA. |
| `bollinger_rsi_mean_reversion.pine` | Price crosses back inside the outer band while RSI is stretched; ADX must be below its limit. | Initial ATR stop and take profit at the Bollinger basis. | Lower-trend-strength / range-like conditions. Low ADX does not guarantee a range. |
| `donchian_adx_breakout.pine` | Close breaks the prior-bar Donchian high or low; ADX and DMI direction must agree. | ATR-distance stop and configurable R-multiple target, based on signal close. | Directional breakouts. |
| `momentum_alpha_candidate.pine` | 126-session return and 200-session SMA agree on direction. | Closes or reverses when the momentum regime changes. | Time-series momentum. The TradingView version uses its configured fixed order quantity. |

The entry filters and exits are illustrative heuristics. These indicators use the chart timeframe and are not independent evidence of expected returns. Compare strategies across multiple symbols and periods, account for costs, and avoid choosing parameters solely from the same historical sample used to report performance.

The Python momentum candidate uses the same directional rule, sizes exposure from trailing 20-session realized volatility toward a 10% annual target, and caps notional exposure at 1.0x. The fixed-quantity Pine strategy and volatility-scaled Python implementation are related examples, not identical portfolio simulations.

## Risk controls

The candle strategy places a stop beyond the pattern candle with an ATR-based buffer and a limit target at a configurable multiple of the stop distance measured from the signal close. The EMA and Donchian strategies use ATR multiples for stop and target distances measured from signal close. The mean-reversion strategy places its target at the Bollinger basis and its stop at an ATR distance. These scripts use the chart's tick and price series, but they do not calculate quantity from a desired account-risk percentage. Users need to set order size, commission, slippage, and margin for their own instrument in TradingView's Strategy Properties.

Because the default broker emulator fills market orders on a later available tick, a gap from signal close to entry can change the realized risk multiple. Bar-level OHLC simulation also cannot reproduce all intrabar price paths or live execution behavior.

## Indicator range threshold

The indicator's configurable fraction is measured down from the current candle high. For example, `0.333` places the threshold one third of that candle's range below its high. The real body bottom must be above the threshold. No swing points or multi-bar Fibonacci retracement are calculated.

## Scope

These scripts analyze the chart's underlying OHLC bars. Options and futures require contract-specific decisions and costs that are outside this repository's current implementation. Validate settings on the exact symbol, venue, timeframe, and data feed before interpreting Strategy Tester output.

## Python research workflow

`research/download_data.py` retrieves daily NIFTY 50 OHLC bars for `^NSEI` from Yahoo Finance's public chart endpoint and writes a CSV plus retrieval metadata. `research/data.py` validates dates and OHLC consistency. `research/indicators.py` computes moving averages, RSI, ATR, Bollinger Bands, and DMI/ADX without third-party packages. `research/strategies.py` builds close-of-bar entry/exit events for the four TradingView examples and a separate momentum candidate. `research/backtest.py` executes signals at the next session open, checks the bar's high/low against bracket orders, charges estimated turnover costs, and uses a stop-first assumption if both stop and target fall within the same daily range. `research/metrics.py` calculates return and benchmark-relative statistics, and `research/plots.py` writes standalone SVG charts.

The momentum candidate uses 126-session time-series momentum, a 200-session SMA direction filter, and a 10% annualized volatility target capped at 1.0x notional exposure. It is intentionally a simple, predeclared candidate rather than a fitted model. The reported chronological holdout was inspected during development/debugging; it should be treated as exploratory, not as a blind final evaluation. A new untouched time period or forward paper-trading sample is needed for confirmatory evidence.

The Python runner is a research approximation of the signal rules, not an exact replay of TradingView's broker emulator. It has no futures contract multiplier, daily settlement, expiry/roll, exchange margin, option payoff, dividend, or realistic slippage model. Yahoo's index history is used as a free underlying price-series proxy, and access/usage terms can change. Its results should not be transferred directly to derivatives.
