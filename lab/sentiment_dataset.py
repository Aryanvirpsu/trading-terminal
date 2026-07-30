"""Fixed, hand-labelled financial-headline dataset for sentiment benchmarking.

Labels use the FINANCIAL-SENTIMENT convention (the same one FinBERT was trained
on): `positive` = good for the stock / bullish, `negative` = bad / bearish,
`neutral` = no clear directional signal (procedural, factual, or genuinely mixed).

Each item: {id, text, label, category, target?, aliases?}. `target` marks the
ticker the sentiment is FOR (used by the entity-relevance tests) — the same
multi-company headline appears once per target with its own label.

This set is deliberately balanced across categories the brief calls out:
earnings, guidance, analyst up/downgrades, lawsuits/regulatory, mixed-company,
ambiguous — plus clear positives/negatives/neutrals.
"""
from __future__ import annotations

from typing import Dict, List

DATASET: List[Dict] = [
    # ── Clear POSITIVE — earnings beats / good news ─────────────────────────
    {"id": 1, "text": "Apple beats Q3 earnings expectations, revenue up 12% year-over-year", "label": "positive", "category": "earnings", "target": "AAPL", "aliases": ["apple"]},
    {"id": 2, "text": "Microsoft cloud revenue jumps 30%, tops analyst estimates", "label": "positive", "category": "earnings", "target": "MSFT", "aliases": ["microsoft"]},
    {"id": 3, "text": "Netflix adds 8 million subscribers, smashing forecasts", "label": "positive", "category": "earnings", "target": "NFLX", "aliases": ["netflix"]},
    {"id": 4, "text": "Ford reports record quarterly profit on strong truck sales", "label": "positive", "category": "earnings", "target": "F", "aliases": ["ford"]},
    {"id": 5, "text": "Coinbase surges after beating revenue estimates", "label": "positive", "category": "earnings", "target": "COIN", "aliases": ["coinbase"]},
    # ── POSITIVE — guidance raises ──────────────────────────────────────────
    {"id": 6, "text": "Nvidia raises full-year guidance on surging AI chip demand", "label": "positive", "category": "guidance", "target": "NVDA", "aliases": ["nvidia"]},
    {"id": 7, "text": "Delta Air Lines raises profit outlook on robust travel demand", "label": "positive", "category": "guidance", "target": "DAL", "aliases": ["delta"]},
    {"id": 8, "text": "Broadcom lifts dividend 11%, signals confidence in outlook", "label": "positive", "category": "guidance", "target": "AVGO", "aliases": ["broadcom"]},
    # ── POSITIVE — analyst upgrades ─────────────────────────────────────────
    {"id": 9, "text": "Morgan Stanley upgrades Tesla to overweight, lifts price target to $320", "label": "positive", "category": "analyst", "target": "TSLA", "aliases": ["tesla"]},
    {"id": 10, "text": "JPMorgan initiates coverage of Palantir with a buy rating", "label": "positive", "category": "analyst", "target": "PLTR", "aliases": ["palantir"]},
    {"id": 11, "text": "Bank of America reiterates buy on Amazon ahead of earnings", "label": "positive", "category": "analyst", "target": "AMZN", "aliases": ["amazon"]},
    # ── POSITIVE — regulatory / legal wins ──────────────────────────────────
    {"id": 12, "text": "Pfizer wins FDA approval for new cancer drug", "label": "positive", "category": "regulatory", "target": "PFE", "aliases": ["pfizer"]},
    {"id": 13, "text": "Judge dismisses class-action lawsuit against the automaker", "label": "positive", "category": "legal", "target": "GM", "aliases": ["general motors", "gm"]},
    {"id": 14, "text": "Eli Lilly's weight-loss drug shows strong late-stage trial results", "label": "positive", "category": "regulatory", "target": "LLY", "aliases": ["eli lilly", "lilly"]},
    {"id": 15, "text": "Amazon announces $10 billion buyback, shares rally", "label": "positive", "category": "good_news", "target": "AMZN", "aliases": ["amazon"]},

    # ── Clear NEGATIVE — earnings misses ────────────────────────────────────
    {"id": 16, "text": "Intel misses earnings and cuts its full-year revenue guidance", "label": "negative", "category": "earnings", "target": "INTC", "aliases": ["intel"]},
    {"id": 17, "text": "Snap shares crater after posting a wider-than-expected loss", "label": "negative", "category": "earnings", "target": "SNAP", "aliases": ["snap", "snapchat"]},
    {"id": 18, "text": "Meta plunges after warning of weaker ad revenue", "label": "negative", "category": "guidance", "target": "META", "aliases": ["meta", "facebook"]},
    {"id": 19, "text": "Disney slashes profit forecast amid mounting streaming losses", "label": "negative", "category": "guidance", "target": "DIS", "aliases": ["disney"]},
    {"id": 20, "text": "Chipmaker warns of a demand slowdown, stock slides", "label": "negative", "category": "guidance", "target": "MU", "aliases": ["micron"]},
    # ── NEGATIVE — analyst downgrades ───────────────────────────────────────
    {"id": 21, "text": "Goldman Sachs downgrades Boeing to sell on 737 production concerns", "label": "negative", "category": "analyst", "target": "BA", "aliases": ["boeing"]},
    {"id": 22, "text": "Wells Fargo cuts Nike price target, citing weak consumer demand", "label": "negative", "category": "analyst", "target": "NKE", "aliases": ["nike"]},
    # ── NEGATIVE — lawsuits / regulatory ────────────────────────────────────
    {"id": 23, "text": "SEC opens an investigation into the company's accounting practices", "label": "negative", "category": "regulatory", "target": "XYZ", "aliases": []},
    {"id": 24, "text": "Tesla recalls 1.2 million vehicles over a software defect", "label": "negative", "category": "regulatory", "target": "TSLA", "aliases": ["tesla"]},
    {"id": 25, "text": "Pharma company hit with a $2 billion lawsuit over drug side effects", "label": "negative", "category": "legal", "target": "JNJ", "aliases": ["johnson"]},
    {"id": 26, "text": "Regulators fine the bank $500 million for compliance failures", "label": "negative", "category": "regulatory", "target": "WFC", "aliases": ["wells fargo"]},
    {"id": 27, "text": "FTC sues to block the $20 billion acquisition", "label": "negative", "category": "regulatory", "target": "XYZ", "aliases": []},
    # ── NEGATIVE — other bad news ───────────────────────────────────────────
    {"id": 28, "text": "Company announces layoffs of 15% of its workforce", "label": "negative", "category": "bad_news", "target": "XYZ", "aliases": []},
    {"id": 29, "text": "Bank stock tumbles as loan losses mount", "label": "negative", "category": "bad_news", "target": "BAC", "aliases": ["bank of america"]},
    {"id": 30, "text": "Shares slump after the CEO abruptly resigns", "label": "negative", "category": "bad_news", "target": "XYZ", "aliases": []},

    # ── Clear NEUTRAL — procedural / factual ────────────────────────────────
    {"id": 31, "text": "Company to report third-quarter earnings on October 26", "label": "neutral", "category": "procedural", "target": "XYZ", "aliases": []},
    {"id": 32, "text": "CEO to present at an industry conference next week", "label": "neutral", "category": "procedural", "target": "XYZ", "aliases": []},
    {"id": 33, "text": "Board announces the date of the annual shareholder meeting", "label": "neutral", "category": "procedural", "target": "XYZ", "aliases": []},
    {"id": 34, "text": "Company files its routine quarterly 10-Q with the SEC", "label": "neutral", "category": "procedural", "target": "XYZ", "aliases": []},
    {"id": 35, "text": "Stock to begin trading ex-dividend on Friday", "label": "neutral", "category": "procedural", "target": "XYZ", "aliases": []},
    {"id": 36, "text": "Firm names a new chief financial officer", "label": "neutral", "category": "procedural", "target": "XYZ", "aliases": []},
    {"id": 37, "text": "Analyst maintains a hold rating and keeps the price target unchanged", "label": "neutral", "category": "analyst", "target": "XYZ", "aliases": []},
    {"id": 38, "text": "Company completes its previously announced acquisition", "label": "neutral", "category": "procedural", "target": "XYZ", "aliases": []},
    {"id": 39, "text": "Shares were little changed in a quiet trading session", "label": "neutral", "category": "market", "target": "XYZ", "aliases": []},
    {"id": 40, "text": "Index reshuffle to add two new members next month", "label": "neutral", "category": "market", "target": "XYZ", "aliases": []},
    {"id": 41, "text": "EU antitrust regulators approve the merger with conditions", "label": "neutral", "category": "regulatory", "target": "XYZ", "aliases": []},

    # ── MIXED-COMPANY — sentiment depends on the TARGET ticker ──────────────
    {"id": 42, "text": "Nvidia soars while Intel struggles in the shifting chip market", "label": "positive", "category": "mixed_company", "target": "NVDA", "aliases": ["nvidia"]},
    {"id": 43, "text": "Nvidia soars while Intel struggles in the shifting chip market", "label": "negative", "category": "mixed_company", "target": "INTC", "aliases": ["intel"]},
    {"id": 44, "text": "Ford gains as General Motors warns on production cuts", "label": "positive", "category": "mixed_company", "target": "F", "aliases": ["ford"]},
    {"id": 45, "text": "Ford gains as General Motors warns on production cuts", "label": "negative", "category": "mixed_company", "target": "GM", "aliases": ["general motors", "gm"]},
    {"id": 46, "text": "A tech rally lifts Apple and Microsoft, but Meta lags behind", "label": "positive", "category": "mixed_company", "target": "AAPL", "aliases": ["apple"]},
    {"id": 47, "text": "A tech rally lifts Apple and Microsoft, but Meta lags behind", "label": "negative", "category": "mixed_company", "target": "META", "aliases": ["meta", "facebook"]},

    # ── AMBIGUOUS — genuinely uncertain / conflicting ───────────────────────
    {"id": 48, "text": "Company shares turn volatile after mixed quarterly results", "label": "neutral", "category": "ambiguous", "target": "XYZ", "aliases": []},
    {"id": 49, "text": "Analysts are split on the outlook after the earnings call", "label": "neutral", "category": "ambiguous", "target": "XYZ", "aliases": []},
    {"id": 50, "text": "Stock swings between gains and losses on Fed uncertainty", "label": "neutral", "category": "ambiguous", "target": "XYZ", "aliases": []},
    {"id": 51, "text": "Investors weigh strong sales against rising input costs", "label": "neutral", "category": "ambiguous", "target": "XYZ", "aliases": []},
    {"id": 52, "text": "Salesforce tops earnings but issues cautious guidance for next quarter", "label": "negative", "category": "ambiguous", "target": "CRM", "aliases": ["salesforce"]},

    # ── A few more clear cases for class balance ────────────────────────────
    {"id": 53, "text": "Uber posts first-ever profitable quarter, shares jump", "label": "positive", "category": "earnings", "target": "UBER", "aliases": ["uber"]},
    {"id": 54, "text": "Rivian burns through cash, guides production lower", "label": "negative", "category": "guidance", "target": "RIVN", "aliases": ["rivian"]},
    {"id": 55, "text": "Costco same-store sales rise, beating Wall Street", "label": "positive", "category": "earnings", "target": "COST", "aliases": ["costco"]},
    {"id": 56, "text": "PayPal falls as active accounts decline again", "label": "negative", "category": "earnings", "target": "PYPL", "aliases": ["paypal"]},
    {"id": 57, "text": "Company to hold an investor day in the fourth quarter", "label": "neutral", "category": "procedural", "target": "XYZ", "aliases": []},
    {"id": 58, "text": "Exxon boosts share buyback after a bumper profit", "label": "positive", "category": "good_news", "target": "XOM", "aliases": ["exxon"]},
    {"id": 59, "text": "Regional bank cuts its dividend to preserve capital", "label": "negative", "category": "bad_news", "target": "XYZ", "aliases": []},
    {"id": 60, "text": "Trading volume was in line with the 30-day average", "label": "neutral", "category": "market", "target": "XYZ", "aliases": []},
]

LABELS = ("positive", "neutral", "negative")


def by_category() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for d in DATASET:
        out[d["category"]] = out.get(d["category"], 0) + 1
    return out


def label_counts() -> Dict[str, int]:
    out = {k: 0 for k in LABELS}
    for d in DATASET:
        out[d["label"]] += 1
    return out


if __name__ == "__main__":
    print("items:", len(DATASET))
    print("labels:", label_counts())
    print("categories:", by_category())
