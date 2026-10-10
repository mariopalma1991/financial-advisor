"""News deduplication, ticker entity-linking, and sentiment scoring.

Pipeline (plan §7):
1. Deduplicate raw articles by article_id.
2. Link tickers: scan headlines for known symbols (word-boundary match).
3. Score sentiment: FinBERT for headline text, with a rule-based fallback.
4. Aggregate into daily sentiment windows (1d, 3d, 7d) per ticker.

Tiered processing:
- Screening tier: headline + metadata + headline sentiment only.
- Analysis tier: full body text + embeddings (Phase 2b, Qdrant integration).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.adapters.base import RawNewsArticle
from src.config import all_symbols

# Common English words that happen to be valid ticker symbols — excluded from matching.
_STOP_TICKERS: set[str] = {
    "A", "I", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF", "IN",
    "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US", "WE",
    "ALL", "AND", "ARE", "BUT", "CAN", "CEO", "CFO", "DAY", "DID", "DNA", "END",
    "FOR", "GET", "GOT", "HAS", "HAD", "HER", "HIM", "HIS", "HOW", "ITS", "JOB",
    "KEY", "LAW", "LOW", "MAY", "MEN", "NEW", "NOT", "NOW", "OFF", "OLD", "ONE",
    "OUR", "OUT", "OWN", "PAY", "PUT", "RUN", "SAY", "SEE", "SET", "SHE", "TAX",
    "THE", "TOP", "TWO", "USE", "WAR", "WAY", "WHO", "WHY", "WIN", "YES", "YOU",
    "BIG", "BUY", "CUT", "DIP", "DRY", "DUE", "EAT", "FAN", "FAT", "FED", "FIT",
    "FLY", "FUN", "GAS", "HIT", "HOT", "ICE", "JET", "KID", "LED", "LEG", "LID",
    "LOT", "MAP", "NET", "OIL", "PET", "RAW", "RED", "RIG", "SAD", "SEA", "SKY",
    "SUN", "TAP", "TEN", "TIE", "TIP", "TOY", "VAN", "WET", "WON", "YUM", "ZIP",
    "BALL", "BANK", "BASE", "BEAM", "BEAR", "BEAT", "BELL", "BELT", "BEND",
    "BEST", "BILL", "BIRD", "BITE", "BLOW", "BLUE", "BOAT", "BODY", "BOLD",
    "BOLT", "BOND", "BOOK", "BOOM", "BORN", "BOSS", "BOWL", "BURN", "CALL",
    "CALM", "CAME", "CAMP", "CARD", "CARE", "CART", "CASE", "CASH", "CAST",
    "CAVE", "CELL", "CHIP", "CITY", "CLUB", "COAL", "COAT", "CODE", "COIN",
    "COLD", "COME", "COOK", "COOL", "COPE", "COPY", "CORE", "COST", "CREW",
    "CROP", "CURE", "DALE", "DAME", "DARE", "DARK", "DASH", "DATA", "DATE",
    "DAWN", "DEAD", "DEAL", "DEAR", "DEBT", "DECK", "DEED", "DEER", "DESK",
    "DIAL", "DIET", "DIRT", "DISH", "DISK", "DOCK", "DOME", "DONE", "DOOR",
    "DOSE", "DOWN", "DRAW", "DREW", "DROP", "DRUM", "DUCK", "DUKE", "DUST",
    "DUTY", "EACH", "EARN", "EASE", "EAST", "EASY", "EDGE", "ELSE", "EVEN",
    "EVER", "EVIL", "EXAM", "FACE", "FACT", "FADE", "FAIL", "FAIR", "FAKE",
    "FALL", "FAME", "FARM", "FAST", "FATE", "FEAR", "FEED", "FEEL", "FELL",
    "FELT", "FILE", "FILL", "FILM", "FIND", "FINE", "FIRE", "FIRM", "FISH",
    "FLAG", "FLAT", "FLEW", "FLIP", "FLOW", "FOAM", "FOLD", "FOLK", "FOND",
    "FOOD", "FOOL", "FOOT", "FORD", "FORM", "FORT", "FOUL", "FOUR", "FREE",
    "FROM", "FUEL", "FULL", "FUND", "FURY", "GAIN", "GAME", "GANG", "GATE",
    "GAVE", "GEAR", "GIFT", "GIRL", "GIVE", "GLAD", "GLOW", "GLUE", "GOAL",
    "GOAT", "GOES", "GOLD", "GOLF", "GONE", "GOOD", "GRAB", "GRAY", "GREW",
    "GREY", "GRIN", "GRIP", "GROW", "GULF", "GURU", "HACK", "HAIR", "HALF",
    "HALL", "HAND", "HANG", "HARD", "HARM", "HATE", "HAUL", "HAVE", "HEAD",
    "HEAL", "HEAP", "HEAR", "HEAT", "HEEL", "HELD", "HELL", "HELP", "HERE",
    "HERO", "HIGH", "HIKE", "HILL", "HINT", "HIRE", "HOLD", "HOLE", "HOME",
    "HOPE", "HORN", "HOST", "HOUR", "HUGE", "HULL", "HUNG", "HUNT", "HURT",
    "IDEA", "INTO", "IRON", "ITEM", "JACK", "JAIL", "JAZZ", "JEAN", "JOHN",
    "JOIN", "JOKE", "JUMP", "JURY", "JUST", "KEEN", "KEEP", "KEPT", "KICK",
    "KILL", "KIND", "KING", "KISS", "KITE", "KNEE", "KNEW", "KNIT", "KNOB",
    "KNOT", "KNOW", "LACE", "LACK", "LADY", "LAID", "LAKE", "LAMB", "LAMP",
    "LAND", "LANE", "LARK", "LAST", "LATE", "LAWN", "LAZY", "LEAD", "LEAF",
    "LEAK", "LEAN", "LEAP", "LEFT", "LEND", "LENS", "LESS", "LIAR", "LICK",
    "LIFE", "LIFT", "LIKE", "LIMB", "LIME", "LIMP", "LINE", "LINK", "LION",
    "LIST", "LIVE", "LOAD", "LOAN", "LOCK", "LOFT", "LOGO", "LONE", "LONG",
    "LOOK", "LOOP", "LORD", "LOSE", "LOSS", "LOST", "LOUD", "LOVE", "LUCK",
    "LUMP", "LUNG", "LURE", "LURK", "MADE", "MAIL", "MAIN", "MAKE", "MALE",
    "MALL", "MALT", "MANY", "MARE", "MARK", "MASK", "MASS", "MATE", "MAZE",
    "MEAL", "MEAN", "MEAT", "MEET", "MELD", "MELT", "MEMO", "MEND", "MENU",
    "MERE", "MESH", "MESS", "MILD", "MILE", "MILK", "MILL", "MIND", "MINE",
    "MINT", "MISS", "MIST", "MOAN", "MOAT", "MOCK", "MODE", "MOLD", "MOOD",
    "MOON", "MORE", "MOSS", "MOST", "MOVE", "MUCH", "MUST", "MYTH", "NAIL",
    "NAME", "NAVY", "NEAR", "NEAT", "NECK", "NEED", "NEST", "NEWS", "NEXT",
    "NICE", "NINE", "NODE", "NONE", "NOSE", "NOTE", "NOUN", "NUDE", "NUTS",
    "OATH", "OBEY", "ODDS", "OKAY", "ONCE", "ONLY", "ONTO", "OPEN", "ORAL",
    "OURS", "OVAL", "OVEN", "OVER", "PACE", "PACK", "PAGE", "PAID", "PAIN",
    "PAIR", "PALE", "PALM", "PANE", "PARK", "PART", "PASS", "PAST", "PATH",
    "PAVE", "PEAK", "PEAR", "PEEL", "PEER", "PILE", "PINE", "PINK", "PIPE",
    "PLAN", "PLAY", "PLEA", "PLOT", "PLOY", "PLUG", "PLUM", "PLUS", "POEM",
    "POET", "POLE", "POLL", "POLO", "POND", "POOL", "POOR", "POPE", "PORK",
    "PORT", "POSE", "POST", "POUR", "PRAY", "PREY", "PROP", "PULL", "PUMP",
    "PUNK", "PURE", "PUSH", "QUIT", "QUIZ", "RACE", "RACK", "RAGE", "RAID",
    "RAIL", "RAIN", "RAKE", "RANK", "RARE", "RATE", "RAVE", "READ", "REAL",
    "REAR", "REED", "REEF", "REEL", "RELY", "RENT", "REST", "RICE", "RICH",
    "RIDE", "RIFT", "RING", "RIOT", "RIPE", "RISE", "RISK", "ROAD", "ROAM",
    "ROAR", "ROCK", "RODE", "ROLE", "ROLL", "ROOF", "ROOM", "ROOT", "ROPE",
    "ROSE", "RUIN", "RULE", "RUSH", "RUST", "SACK", "SAFE", "SAGE", "SAID",
    "SAIL", "SAKE", "SALE", "SALT", "SAME", "SAND", "SANG", "SANK", "SAVE",
    "SCAN", "SCAR", "SEAL", "SEAT", "SEED", "SEEK", "SEEM", "SEEN", "SELF",
    "SELL", "SEND", "SENT", "SHED", "SHIP", "SHOE", "SHOP", "SHOT", "SHOW",
    "SHUT", "SICK", "SIDE", "SIGH", "SIGN", "SILK", "SINK", "SITE", "SIZE",
    "SKIN", "SKIP", "SLAM", "SLAP", "SLEW", "SLID", "SLIM", "SLIP", "SLOT",
    "SLOW", "SLUG", "SNAP", "SNOW", "SOAK", "SOAP", "SOAR", "SOCK", "SODA",
    "SOFA", "SOFT", "SOIL", "SOLD", "SOLE", "SOME", "SONG", "SOON", "SORE",
    "SORT", "SOUL", "SOUR", "SPAN", "SPIN", "SPIT", "SPOT", "STAR", "STAY",
    "STEM", "STEP", "STEW", "STIR", "STOP", "STUB", "STUD", "SUCH", "SUIT",
    "SURE", "SURF", "SWAP", "SWIM", "TAIL", "TAKE", "TALE", "TALK", "TALL",
    "TAME", "TANK", "TAPE", "TASK", "TEAM", "TEAR", "TELL", "TEND", "TENT",
    "TERM", "TEST", "TEXT", "THAN", "THAT", "THEM", "THEN", "THEY", "THIN",
    "THIS", "THUS", "TIDE", "TIDY", "TIED", "TIER", "TILE", "TILL", "TILT",
    "TIME", "TINY", "TIRE", "TOAD", "TOIL", "TOLD", "TOLL", "TONE", "TOOK",
    "TOOL", "TOPS", "TORE", "TORN", "TOUR", "TOWN", "TRAP", "TRAY", "TREE",
    "TRIM", "TRIO", "TRIP", "TROY", "TRUE", "TUBE", "TUCK", "TUNA", "TUNE",
    "TURF", "TURN", "TWIN", "TYPE", "UGLY", "UNDO", "UNIT", "UNTO", "UPON",
    "URGE", "USED", "USER", "VAIN", "VALE", "VARY", "VAST", "VEIN", "VENT",
    "VERB", "VERY", "VEST", "VETO", "VICE", "VIEW", "VINE", "VOID", "VOLT",
    "VOTE", "WADE", "WAGE", "WAIT", "WAKE", "WALK", "WALL", "WANT", "WARD",
    "WARM", "WARN", "WARP", "WARY", "WASH", "WAVE", "WAXY", "WAYS", "WEAK",
    "WEAR", "WEED", "WEEK", "WELD", "WELL", "WENT", "WERE", "WEST", "WHAT",
    "WHEN", "WHOM", "WIDE", "WIFE", "WILD", "WILL", "WILT", "WILY", "WIND",
    "WINE", "WING", "WINK", "WIPE", "WIRE", "WISE", "WISH", "WITH", "WOKE",
    "WOLF", "WOOD", "WOOL", "WORD", "WORE", "WORK", "WORM", "WORN", "WOVE",
    "WRAP", "WREN", "YARD", "YARN", "YEAR", "YELL", "YOUR", "ZEAL", "ZERO",
    "ZINC", "ZONE", "ZOOM",
}


def _build_ticker_pattern(symbols: list[str]) -> re.Pattern[str]:
    """Build a regex that matches known ticker symbols as whole words.

    Filters out single-character symbols and common English words to reduce
    false positives. Matches on word boundaries.
    """
    valid = sorted(
        [s for s in symbols if len(s) >= 2 and s.upper() not in _STOP_TICKERS],
        key=len,
        reverse=True,  # longer symbols first to avoid partial matches
    )
    if not valid:
        return re.compile(r"(?!)")  # never matches
    escaped = [re.escape(s) for s in valid]
    pattern = r"\b(" + "|".join(escaped) + r")\b"
    return re.compile(pattern)


def _build_name_map() -> dict[str, str]:
    """Build a mapping of company name (lowercase) -> ticker symbol."""
    from src.config import load_analysis_universe, load_screening_universe

    name_map: dict[str, str] = {}
    for universe in (load_analysis_universe(), load_screening_universe()):
        for t in universe.tickers:
            name = t.name.lower().strip()
            if name and len(name) > 3:
                # Store the full name
                name_map[name] = t.symbol
                # Also store short forms (first word of multi-word names)
                parts = name.split()
                if len(parts) > 1 and len(parts[0]) > 2:
                    name_map[parts[0]] = t.symbol
    return name_map


def link_tickers(
    articles: list[RawNewsArticle],
    symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Scan headlines and bodies for ticker symbols and company names.

    Matches both ticker symbols (e.g. AAPL) and company names (e.g. Apple).
    Each output row has: article_id, published_at, tickers[], headline,
    sentiment_score (None until scored), storage_path (None for screening tier).
    """
    if symbols is None:
        symbols = all_symbols()

    ticker_pattern = _build_ticker_pattern(symbols)
    name_map = _build_name_map()
    # Build a name pattern: match known company names (case-insensitive)
    name_escaped = sorted(
        [re.escape(n) for n in name_map if len(n) > 3],
        key=len,
        reverse=True,
    )
    name_pattern = re.compile(r"\b(" + "|".join(name_escaped) + r")\b", re.IGNORECASE) if name_escaped else None

    rows: list[dict[str, Any]] = []

    for a in articles:
        headline = a.headline or ""
        body = a.body or ""
        text = f"{headline} {body}"

        # Match ticker symbols (case-insensitive)
        ticker_matches = ticker_pattern.findall(text.upper())

        # Match company names and map to tickers
        name_matches: set[str] = set()
        if name_pattern:
            for match in name_pattern.findall(text):
                ticker = name_map.get(match.lower())
                if ticker:
                    name_matches.add(ticker)

        all_matches = sorted(set(ticker_matches) | name_matches)

        if not all_matches:
            continue

        rows.append(
            {
                "article_id": a.article_id,
                "published_at": a.published_at.isoformat(),
                "tickers": all_matches,
                "headline": headline if headline else None,
                "sentiment_score": None,
                "storage_path": None,
                "valid_from": a.published_at.isoformat(),
            }
        )

    return rows


# ---------- Sentiment scoring ----------

# Lazy-loaded FinBERT pipeline — loaded once, cached for the process lifetime.
_finbert_pipeline: Any = None


def _load_finbert() -> Any:
    """Lazy-load the FinBERT sentiment model.

    Uses ProsusAI/finbert, a BERT model fine-tuned on financial text.
    Returns a transformers pipeline, or None if transformers is not installed.
    """
    global _finbert_pipeline
    if _finbert_pipeline is not None:
        return _finbert_pipeline
    try:
        from transformers import pipeline
        _finbert_pipeline = pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            top_k=None,  # return all three labels
        )
        return _finbert_pipeline
    except ImportError:
        return None
    except Exception:
        return None


# Simple keyword-based sentiment as fallback when FinBERT is unavailable.
_POSITIVE_WORDS: set[str] = {
    "beat", "beats", "boost", "bullish", "buy", "climb", "climbs", "gain",
    "gains", "growth", "higher", "jump", "jumps", "outperform", "profit",
    "profits", "raise", "raises", "rally", "record", "rise", "rises", "soar",
    "soars", "strong", "surge", "surges", "upgrade", "upside", "win", "wins",
    "positive", "optimistic", "upbeat", "recovery", "recovered", "expansion",
    "breakthrough", "breakout", "momentum", "outlook raised", "guidance raised",
}

_NEGATIVE_WORDS: set[str] = {
    "bearish", "cut", "cuts", "decline", "declines", "downgrade", "drop",
    "drops", "fall", "falls", "fear", "layoff", "layoffs", "loss", "losses",
    "lower", "miss", "misses", "negative", "plunge", "plunges", "probe",
    "recall", "risk", "sanction", "scandal", "sell", "slump", "slumps",
    "tumble", "tumbles", "weak", "worry", "worries", "crash", "crisis",
    "default", "debt", "delisting", "downturn", "fine", "fined", "fraud",
    "investigation", "lawsuit", "penalty", "recession", "restructuring",
    "short", "subpoena", "tank", "tanks", "volatility", "warning", "warned",
}


def _rule_based_sentiment(text: str) -> float:
    """Simple keyword-count sentiment: (pos - neg) / (pos + neg + 1), range [-1, 1]."""
    words = set(text.lower().split())
    pos = len(words & _POSITIVE_WORDS)
    neg = len(words & _NEGATIVE_WORDS)
    if pos + neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg + 1), 4)


def _finbert_score(text: str) -> float:
    """Score a single text with FinBERT, returning a value in [-1, 1].

    FinBERT returns probabilities for positive, negative, neutral.
    We compute: positive_prob - negative_prob (neutral is ignored).
    """
    pipe = _load_finbert()
    if pipe is None:
        return _rule_based_sentiment(text)

    try:
        result = pipe(text, truncation=True, max_length=512)[0]
        # result is a list of {label: str, score: float} dicts
        scores: dict[str, float] = {r["label"].lower(): r["score"] for r in result}
        pos = scores.get("positive", 0.0)
        neg = scores.get("negative", 0.0)
        return round(pos - neg, 4)
    except Exception:
        return _rule_based_sentiment(text)


def score_sentiment(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score headline sentiment for each article row (mutates in place).

    For screening tier, only the headline is scored. Analysis tier would also
    score the full body text (future enhancement).

    Returns the same list with sentiment_score populated.
    """
    for r in rows:
        headline = r.get("headline") or ""
        if headline:
            r["sentiment_score"] = _finbert_score(headline)
        else:
            r["sentiment_score"] = 0.0
    return rows


# ---------- Daily sentiment aggregation ----------

def aggregate_daily_sentiment(
    articles: list[dict[str, Any]],
    windows: tuple[int, ...] = (1, 3, 7),
) -> list[dict[str, Any]]:
    """Aggregate article-level sentiment into daily windows per ticker.

    For each (ticker, date), computes:
    - sent_1d: mean sentiment of articles published on that date
    - sent_3d: mean sentiment over [date-2, date]
    - sent_7d: mean sentiment over [date-6, date]
    - article_count: number of articles on that date

    Args:
        articles: List of curated article dicts with tickers[], published_at, sentiment_score.
        windows: Tuple of window sizes in days.

    Returns:
        List of dicts ready for curated.upsert_news_sentiment().
    """
    if not articles:
        return []

    # Group articles by (ticker, date)
    by_ticker_date: dict[tuple[str, date], list[float]] = defaultdict(list)
    for a in articles:
        score = a.get("sentiment_score")
        if score is None:
            continue
        pub_date = _parse_date(a["published_at"])
        if pub_date is None:
            continue
        for ticker in a.get("tickers", []):
            by_ticker_date[(ticker, pub_date)].append(score)

    if not by_ticker_date:
        return []

    # Collect all unique (ticker, date) pairs
    all_pairs = sorted(by_ticker_date.keys())
    tickers_set = {t for t, _ in all_pairs}
    min_date = min(d for _, d in all_pairs)
    max_date = max(d for _, d in all_pairs)

    # Build a dense date range per ticker
    rows: list[dict[str, Any]] = []
    for ticker in tickers_set:
        d = min_date
        while d <= max_date:
            scores_today = by_ticker_date.get((ticker, d), [])
            count = len(scores_today)
            sent_1d = round(sum(scores_today) / count, 4) if count else None

            # Rolling windows
            sent_3d = _rolling_mean(by_ticker_date, ticker, d, 3)
            sent_7d = _rolling_mean(by_ticker_date, ticker, d, 7)

            # Only emit rows that have at least one non-null value
            if sent_1d is not None or sent_3d is not None or sent_7d is not None:
                rows.append(
                    {
                        "ticker": ticker,
                        "date": d.isoformat(),
                        "sent_1d": sent_1d,
                        "sent_3d": sent_3d,
                        "sent_7d": sent_7d,
                        "article_count": count,
                    }
                )
            d += timedelta(days=1)

    return rows


def _parse_date(ts_str: str) -> date | None:
    """Parse an ISO-format timestamp string to a date."""
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def _rolling_mean(
    by_ticker_date: dict[tuple[str, date], list[float]],
    ticker: str,
    end_date: date,
    window: int,
) -> float | None:
    """Mean sentiment for a ticker over [end_date - window + 1, end_date]."""
    scores: list[float] = []
    for offset in range(window):
        d = end_date - timedelta(days=offset)
        day_scores = by_ticker_date.get((ticker, d), [])
        scores.extend(day_scores)
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)


# ---------- Full pipeline convenience ----------

def process(
    articles: list[RawNewsArticle],
    symbols: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the full news transform pipeline: link -> score -> aggregate.

    Returns:
        (curated_articles, daily_sentiment_rows) ready for storage.
    """
    linked = link_tickers(articles, symbols)
    scored = score_sentiment(linked)
    daily = aggregate_daily_sentiment(scored)
    return scored, daily