# Minervini-style US Stock + Entry Scanner

## Layer 1 — Liquidity / universe
- US listed non-ETF stocks
- Market cap >= $5B
- 20-day average daily dollar volume >= $20M

## Layer 2 — Strength
- Minervini-style Trend Template = 8/8
- Custom RS percentile >= 80
- 20D and 60D relative performance vs SPY >= 0%
- Within 15% of 52-week high

## Layer 3 — Entry timing
The scanner estimates confirmed local swing highs/lows with a +/-3-day window, then uses recent swing-high resistance clusters to estimate a pivot.

It classifies each strong stock as:
- READY: confirmed breakout with >=1.3x 50D average volume, or pullback near 10/20MA turning back up
- WATCH: near pivot, VCP candidate, post-breakout but not extended, or strong trend without trigger
- WAIT: extended >5% above pivot, >10% above 20MA, or >15% above 50MA

VCP is an approximation/candidate detector: shrinking contractions + rising/steady lows + recent volume dry-up + price close to pivot. It should still be visually checked.

## Automatic update
`.github/workflows/update.yml` runs on weekdays and can be run manually from GitHub Actions.
