# MMS — Mastermind ZX external reference

**Source:** Mastermind ZX (mastermindzx.pl) — "MMS – BTC/XAU/NQ Mean-Reversion Strategy",
author Marcin Zubrzycki (@MastermindZX). Private course content.

**Purpose:** internal reference for the `mean_reversion_bb_stoch` strategy
(`algo_bot/strategies/mean_reversion_bb_stoch.py`). The Mastermind methodology is the
economic prior behind the strategy's parameter ranges (`config/mr_b1..b3.yaml`) and the
anchor for the alignment table in
`docs/reference/modules/strategy-mean-reversion-bb-stoch.md`.

## Copyright and redistribution

- Content in this folder is derived from a **private, paid course**. Copyright
  © Marcin Zubrzycki / Mastermind. All rights reserved by the author.
- **Not for redistribution.** This folder exists solely as an internal research
  reference inside a private repository. Do not publish, share, or quote it outside
  this repo.
- Rules and parameters are summarised **in our own words** (understanding check +
  copyright safety). Screenshots in `raw/` and `img/` are verbatim captures kept as a
  permanent versioned reference — the source website may change or go offline.

## Layout

| Path | Content |
|---|---|
| `01-position-building.md` … `09-*.md` | Per-tab extraction (HIGH tabs: structured rules + params; MEDIUM tabs: screenshots + captions) |
| `raw/mastermind/` | Full-tab screenshot captures (source material, uncropped) — markdown files embed these directly |
| `img/` | Reserved for cropped highlights (unused as of 2026-07-11) |

Tab priority per MR-Session 1 Audit kickoff: HIGH = 01 position building,
02 position management/filters, 03 stop loss/sequentiality, 04 interval marking;
MEDIUM = 05 market mechanics patterns, 06 algotrading semi-automatic,
09 backtests 2025-2026; SKIP = links, mindset/psychology.

**Extracted:** 2026-07-11 (MR-Session 1 Audit). Live site was reachable at extraction
time; text cross-checked against the screenshots in `raw/`.
