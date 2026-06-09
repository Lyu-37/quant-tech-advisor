"""News fetcher + sentiment classifier + theme miner.

Three stages:
  1. fetch  — yfinance.Ticker(t).news  (free, no API key)
  2. tag    — per-headline: sentiment (lexicon) + category (keyword rules)
  3. theme  — daily cross-ticker theme extraction (Ollama if available)

LLM use is bounded: one call for daily theme synthesis (not per-headline).
Per-headline sentiment stays lexicon-based for speed (150+ headlines/run).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
import re
import urllib.request
import urllib.error
import json

import yfinance as yf


# Finance-loaded lexicon — biased to headline language, not general English
POS_WORDS = {
    "beat", "beats", "surge", "surges", "rally", "rallies", "soar", "soars",
    "jump", "jumps", "upgrade", "upgraded", "raises", "boost", "boosted",
    "outperform", "outperforms", "strong", "robust", "record", "high",
    "breakthrough", "growth", "expansion", "approval", "approved",
    "wins", "win", "partnership", "deal", "contract", "bullish", "buy",
    "ai", "demand", "guidance raised", "raised guidance", "tops",
    "exceeds", "exceed", "above estimates", "beat estimates",
}
NEG_WORDS = {
    "miss", "misses", "missed", "plunge", "plunges", "fall", "falls",
    "drop", "drops", "decline", "declines", "tumble", "tumbles",
    "downgrade", "downgraded", "cut", "cuts", "slashed", "slumps",
    "underperform", "weak", "weakness", "concern", "concerns", "warning",
    "lawsuit", "investigation", "probe", "fraud", "scandal", "bearish",
    "sell", "short", "risk", "risks", "fears", "below estimates",
    "guidance cut", "cut guidance", "layoff", "layoffs", "loss", "losses",
}


# News category buckets. Keyword-driven, intentionally simple — works fast
# on hundreds of headlines without an LLM call per item.
CATEGORY_KEYWORDS = {
    "earnings":   ["earnings", "q1", "q2", "q3", "q4", "quarterly", "beat ",
                   "miss ", "missed", "guidance", "revenue", "eps ",
                   "results", "outlook"],
    "analyst":    ["upgrade", "downgrade", "price target", "rating",
                   "initiated", "reiterates", "buy rating", "sell rating",
                   "outperform", "underperform", "overweight"],
    "regulatory": ["lawsuit", "investigation", "probe", "fine", "fraud",
                   "sec ", "doj", "ftc", "antitrust", "tariff",
                   "sanction", "ban", "export control", "regulator"],
    "product":    ["launch", "launches", "unveils", "announces", "releases",
                   "new chip", "new gpu", "ai model", "partnership", "deal",
                   "contract", "wins ", "rolls out"],
    "corporate":  ["buyback", "dividend", "split", "spin-off", "merger",
                   "acquisition", "acquires", "takeover", "m&a",
                   "ceo ", "cfo ", "resignation", "appointed"],
    "macro":      ["fed ", "federal reserve", "rate", "inflation", "cpi",
                   "ppi", "recession", "yield", "treasury", "fomc",
                   "jobs report", "unemployment", "powell"],
}


@dataclass
class Headline:
    ticker: str
    title: str
    publisher: str
    url: str
    published: datetime
    sentiment: str = "neutral"   # positive / negative / neutral
    score: float = 0.0           # -1 .. +1
    category: str = "general"    # see CATEGORY_KEYWORDS keys


@dataclass
class TickerNewsSummary:
    ticker: str
    headlines: list[Headline] = field(default_factory=list)
    pos: int = 0
    neg: int = 0
    neu: int = 0
    avg_score: float = 0.0
    label: str = "neutral"


def _classify_lexicon(title: str) -> tuple[str, float]:
    """Simple keyword count. Returns (label, score in [-1, 1])."""
    t = title.lower()
    # Use word boundaries for short words, substring for phrases
    pos_hits = sum(1 for w in POS_WORDS if re.search(rf"\b{re.escape(w)}\b", t))
    neg_hits = sum(1 for w in NEG_WORDS if re.search(rf"\b{re.escape(w)}\b", t))
    total = pos_hits + neg_hits
    if total == 0:
        return "neutral", 0.0
    score = (pos_hits - neg_hits) / total
    if score >= 0.34:
        return "positive", score
    if score <= -0.34:
        return "negative", score
    return "neutral", score


def _classify_category(title: str) -> str:
    """Keyword-driven category lookup. Returns 'general' if no rule matches."""
    t = title.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return cat
    return "general"


def classify_headline(title: str) -> tuple[str, float, str]:
    """Returns (sentiment_label, score, category)."""
    sent, score = _classify_lexicon(title)
    cat = _classify_category(title)
    return sent, score, cat


# ---------- Ollama integration for daily theme extraction (one call) ----------

# Ollama is OPT-IN by default. localhost:11434 is the user's 宋予安 (yuan)
# runtime — we must not pollute it. Set ENABLE_OLLAMA=1 only if pointing to a
# separate Ollama instance that doesn't conflict with yuan.
ENABLE_OLLAMA = os.environ.get("ENABLE_OLLAMA", "0") == "1"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")


def _ollama_chat(prompt: str, timeout: int = 120) -> str | None:
    if not ENABLE_OLLAMA:
        return None
    """One-shot Ollama chat. Returns response text or None on any failure."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 1500},
    }
    try:
        req = urllib.request.Request(
            OLLAMA_URL.rstrip("/") + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        msg = body.get("message", {})
        if not isinstance(msg, dict):
            return None
        # Some models (gemma4) return both 'content' and 'thinking'. We want content.
        return (msg.get("content") or "").strip() or None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        print(f"  ! Ollama call failed: {e}")
        return None


# Rule-based theme detection. Each theme is a cluster of keywords; if N+
# headlines across multiple tickers match, the theme fires.
THEME_RULES = [
    {
        "name": "AI 资本开支加速",
        "keywords": ["capex", "data center", "datacenter", "ai spending",
                     "ai infrastructure", "trillion ai", "ai boom"],
        "min_hits": 3,
    },
    {
        "name": "内存/HBM 紧缺涨价",
        "keywords": ["dram", "hbm", "memory shortage", "memory chip",
                     "memory demand", "memory supply"],
        "min_hits": 2,
    },
    {
        "name": "中美关税/出口管制",
        "keywords": ["tariff", "china export", "export control", "sanction",
                     "ban chip", "trade war"],
        "min_hits": 2,
    },
    {
        "name": "财报季活跃",
        "keywords": ["earnings beat", "earnings miss", "raises guidance",
                     "cuts guidance", "q1 results", "q2 results", "q3 results",
                     "q4 results", "quarterly results"],
        "min_hits": 4,
    },
    {
        "name": "Fed 利率/宏观",
        "keywords": ["fed ", "fomc", "rate cut", "rate hike", "powell",
                     "inflation", "cpi data"],
        "min_hits": 2,
    },
    {
        "name": "AI 模型/产品发布",
        "keywords": ["new ai model", "unveils ai", "ai chip launch",
                     "blackwell", "rubin", "instinct mi"],
        "min_hits": 2,
    },
    {
        "name": "数据中心电力短缺",
        "keywords": ["power demand", "grid", "nuclear", "electricity",
                     "energy crunch", "datacenter power"],
        "min_hits": 2,
    },
    {
        "name": "自动驾驶/Robotaxi",
        "keywords": ["robotaxi", "fsd", "autonomous driving",
                     "self-driving"],
        "min_hits": 2,
    },
    {
        "name": "量子计算突破",
        "keywords": ["quantum", "qubit", "quantum computing"],
        "min_hits": 2,
    },
    {
        "name": "AI 算力短缺",
        "keywords": ["gpu shortage", "compute shortage", "ai capacity",
                     "supply constraint"],
        "min_hits": 2,
    },
    {
        "name": "光通信/数据中心互联",
        "keywords": ["optical", "transceiver", "photonics",
                     "datacenter networking", "800g", "1.6t"],
        "min_hits": 2,
    },
    {
        "name": "OpenAI/超大模型",
        "keywords": ["openai", "anthropic", "gpt-", "claude",
                     "gemini", "frontier model"],
        "min_hits": 2,
    },
]


def extract_themes(news_by_ticker: dict, max_headlines: int = 100) -> list[dict]:
    """Detect cross-ticker themes via keyword rules.

    Returns list of {"theme", "tickers", "direction", "n_headlines"}.
    Deterministic and fast — no LLM call.
    """
    all_headlines = []
    for ticker, summary in news_by_ticker.items():
        for h in summary.headlines[:8]:
            all_headlines.append((ticker, h.title.lower(), h.sentiment))
    all_headlines = all_headlines[:max_headlines]

    detected = []
    for rule in THEME_RULES:
        matches = []
        for ticker, title, sent in all_headlines:
            if any(kw in title for kw in rule["keywords"]):
                matches.append((ticker, sent))
        if len(matches) < rule["min_hits"]:
            continue
        tickers_uniq = list(dict.fromkeys(t for t, _ in matches))
        if len(tickers_uniq) < 2:   # need at least 2 distinct tickers
            continue
        sents = [s for _, s in matches]
        pos, neg = sents.count("positive"), sents.count("negative")
        direction = ("bullish" if pos > neg + 1 else
                     "bearish" if neg > pos + 1 else "mixed")
        detected.append({
            "theme": rule["name"],
            "tickers": tickers_uniq[:6],
            "direction": direction,
            "n_headlines": len(matches),
        })

    detected.sort(key=lambda x: -x["n_headlines"])
    return detected


def fetch_ticker_news(ticker: str, max_age_days: int = 7,
                      max_items: int = 10) -> TickerNewsSummary:
    """Pull recent news for a single ticker."""
    summary = TickerNewsSummary(ticker=ticker)
    try:
        items = yf.Ticker(ticker).news or []
    except Exception as e:
        print(f"  ! news fetch failed for {ticker}: {e}")
        return summary

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    for item in items[:max_items * 2]:   # over-fetch to filter by age
        # yfinance returns nested 'content' on newer versions, flat on older
        content = item.get("content", item)
        title = content.get("title") or item.get("title", "")
        if not title:
            continue
        # publish time can be 'pubDate' (ISO str) or 'providerPublishTime' (epoch)
        pub = content.get("pubDate") or item.get("providerPublishTime")
        try:
            if isinstance(pub, (int, float)):
                pub_dt = datetime.fromtimestamp(pub, tz=timezone.utc)
            elif isinstance(pub, str):
                pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            else:
                pub_dt = datetime.now(timezone.utc)
        except (ValueError, TypeError):
            pub_dt = datetime.now(timezone.utc)
        if pub_dt < cutoff:
            continue

        publisher = (content.get("provider", {}).get("displayName")
                     if isinstance(content.get("provider"), dict)
                     else item.get("publisher", "?"))
        url = (content.get("canonicalUrl", {}).get("url")
               if isinstance(content.get("canonicalUrl"), dict)
               else item.get("link", ""))

        label, score, cat = classify_headline(title)
        summary.headlines.append(Headline(
            ticker=ticker, title=title, publisher=publisher or "?",
            url=url or "", published=pub_dt,
            sentiment=label, score=score, category=cat,
        ))
        if len(summary.headlines) >= max_items:
            break

    # Aggregate
    for h in summary.headlines:
        if h.sentiment == "positive":
            summary.pos += 1
        elif h.sentiment == "negative":
            summary.neg += 1
        else:
            summary.neu += 1
    n = len(summary.headlines)
    if n > 0:
        summary.avg_score = sum(h.score for h in summary.headlines) / n
        if summary.avg_score >= 0.20:
            summary.label = "偏积极"
        elif summary.avg_score <= -0.20:
            summary.label = "偏负面"
        else:
            summary.label = "中性"
    return summary


def fetch_news_batch(tickers: list[str]) -> dict[str, TickerNewsSummary]:
    """Fetch news for each ticker in `tickers`. Failures don't stop the batch."""
    return {t: fetch_ticker_news(t) for t in tickers}


def render_news_section(news: dict[str, TickerNewsSummary],
                        focus_tickers: list[str]) -> str:
    """Compact news + sentiment section for the report."""
    lines = ["## 新闻情绪扫描 (近 7 天)", ""]
    # First, aggregate table
    lines.append("| 标的 | 条数 | 正面 | 负面 | 中性 | 情绪 |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for t in focus_tickers:
        s = news.get(t)
        if not s or not s.headlines:
            lines.append(f"| {t} | 0 | — | — | — | 无新闻 |")
            continue
        lines.append(
            f"| {t} | {len(s.headlines)} | {s.pos} | {s.neg} | {s.neu} | "
            f"{s.label} ({s.avg_score:+.2f}) |"
        )
    lines.append("")

    # Then, headline detail for any focus ticker that has news
    for t in focus_tickers:
        s = news.get(t)
        if not s or not s.headlines:
            continue
        lines.append(f"### {t} — {s.label}  ({s.pos}↑ / {s.neg}↓ / {s.neu}·)")
        for h in s.headlines[:5]:
            tag = ("[+]" if h.sentiment == "positive"
                   else "[-]" if h.sentiment == "negative" else "[·]")
            cat_tag = f"[{h.category}]" if h.category != "general" else ""
            pub_short = h.published.strftime("%m-%d")
            title_short = h.title if len(h.title) <= 100 else h.title[:97] + "..."
            link = f"[{title_short}]({h.url})" if h.url else title_short
            lines.append(f"- {tag}{cat_tag} `{pub_short}` {link} _({h.publisher})_")
        lines.append("")
    return "\n".join(lines)


def render_themes_block(themes: list[dict]) -> str:
    """Markdown for the LLM-extracted daily themes."""
    if not themes:
        return ""
    lines = ["## 当日跨股票主题 (关键词聚类)", ""]
    direction_map = {"bullish": "[+]", "bearish": "[-]", "mixed": "[~]"}
    for t in themes[:6]:
        d = direction_map.get(t.get("direction", "mixed"), "[~]")
        tickers = ", ".join(t.get("tickers", []) or [])
        theme_text = t.get("theme", "")
        lines.append(f"- {d} **{theme_text}**" + (f"  ({tickers})" if tickers else ""))
    lines.append("")
    return "\n".join(lines)


def category_breakdown(news: dict) -> dict[str, int]:
    """Cross-ticker count of how many headlines are in each category today."""
    counts = {}
    for s in news.values():
        for h in s.headlines:
            counts[h.category] = counts.get(h.category, 0) + 1
    return counts
