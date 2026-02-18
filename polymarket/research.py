"""Multi-source Research — Tavily + Polymarket Analytics + price history

Gathers intelligence from multiple sources before the superforecaster
runs its analysis. Better queries = better forecasts.
"""
import httpx
import logging
from config import cfg

logger = logging.getLogger("polyagent.pm.research")

_tavily_client = None


def _get_tavily():
    global _tavily_client
    if _tavily_client is None and cfg.tavily_key:
        from tavily import TavilyClient
        _tavily_client = TavilyClient(api_key=cfg.tavily_key)
    return _tavily_client


def _build_search_query(market: dict) -> str:
    """Build a more targeted search query from the market question.

    Instead of searching for the raw question (which may be too specific),
    extract the core topic and add context for better results.
    """
    question = market["question"]
    category = market.get("category", "")

    # For questions like "Will X happen by Y date?" extract the core event
    # The raw question works well for most Polymarket questions
    # But we add category context for better results
    if category:
        return f"{question} {category} latest news 2026"
    return f"{question} latest news analysis 2026"


async def research_market(market: dict) -> dict:
    """Gather intelligence from multiple sources for a prediction market."""
    question = market["question"]
    context = {"question": question, "sources": [], "summary": ""}

    # ── Source 1: Tavily Web Search (targeted query) ──
    try:
        tavily = _get_tavily()
        if tavily:
            search_query = _build_search_query(market)
            result = tavily.search(
                query=search_query,
                search_depth="advanced",
                max_results=cfg.research_max_sources,
                include_answer=True,
            )
            context["sources"].append({
                "type": "web_search",
                "answer": result.get("answer", ""),
                "results": [
                    {"title": r["title"], "url": r["url"], "snippet": r.get("content", "")[:300]}
                    for r in result.get("results", [])[:5]
                ],
            })

            # If the first search was about a person/event, do a follow-up
            # search for contrarian perspectives
            if result.get("answer") and len(result.get("answer", "")) > 50:
                try:
                    contrarian = tavily.search(
                        query=f"{question} unlikely reasons against criticism",
                        search_depth="basic",
                        max_results=3,
                        include_answer=True,
                    )
                    if contrarian.get("answer"):
                        context["sources"].append({
                            "type": "contrarian_search",
                            "answer": contrarian.get("answer", ""),
                            "results": [
                                {"title": r["title"], "url": r["url"], "snippet": r.get("content", "")[:200]}
                                for r in contrarian.get("results", [])[:3]
                            ],
                        })
                except Exception:
                    pass

    except Exception as e:
        logger.warning(f"Tavily search failed: {e}")

    # ── Source 2: Polymarket Market Detail — description, resolution, traders ──
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://gamma-api.polymarket.com/markets/{market['id']}",
            )
            if resp.status_code == 200:
                data = resp.json()
                context["sources"].append({
                    "type": "market_data",
                    "volume_24h": data.get("volume24hr", 0),
                    "competitive_volume": data.get("competitiveVolume", 0),
                    "num_traders": data.get("uniqueTraders", 0),
                    "description": data.get("description", "")[:500],
                    "resolution_source": data.get("resolutionSource", ""),
                })
    except Exception as e:
        logger.warning(f"Market data fetch failed: {e}")

    # ── Source 3: Price history (momentum and volatility) ──
    try:
        tokens = market.get("tokens", [])
        if tokens:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://clob.polymarket.com/prices-history",
                    params={
                        "market": market["id"],
                        "interval": "1d",
                        "fidelity": 24,
                    },
                )
                if resp.status_code == 200:
                    history = resp.json()
                    if history:
                        prices = [float(p.get("p", 0)) for p in history.get("history", []) if p.get("p")]
                        if len(prices) >= 2:
                            momentum = prices[-1] - prices[0]
                            volatility = max(prices) - min(prices)

                            # Detect recent trend direction and strength
                            recent_prices = prices[-6:] if len(prices) >= 6 else prices
                            recent_change = recent_prices[-1] - recent_prices[0]

                            context["sources"].append({
                                "type": "price_history",
                                "current": prices[-1],
                                "start": prices[0],
                                "momentum": momentum,
                                "volatility": volatility,
                                "recent_change": recent_change,
                                "data_points": len(prices),
                                "trend": "up" if momentum > 0.02 else "down" if momentum < -0.02 else "flat",
                            })
    except Exception as e:
        logger.warning(f"Price history fetch failed: {e}")

    # ── Compile summary ──
    context["summary"] = _compile_summary(context["sources"])
    return context


def _compile_summary(sources: list) -> str:
    """Compile all research sources into a structured summary."""
    parts = []

    for src in sources:
        if src["type"] == "web_search":
            answer = src.get("answer", "")
            if answer:
                parts.append(f"Web research: {answer}")
            for r in src.get("results", [])[:3]:
                parts.append(f"  - {r['title']}: {r['snippet'][:150]}")

        elif src["type"] == "contrarian_search":
            answer = src.get("answer", "")
            if answer:
                parts.append(f"Contrarian view: {answer}")

        elif src["type"] == "market_data":
            parts.append(
                f"Market: {src.get('num_traders', '?')} traders, "
                f"24h vol: ${src.get('volume_24h', 0):,.0f}"
            )
            desc = src.get("description", "")
            if desc:
                parts.append(f"  Description: {desc[:250]}")
            res = src.get("resolution_source", "")
            if res:
                parts.append(f"  Resolution source: {res}")

        elif src["type"] == "price_history":
            direction = "UP" if src["momentum"] > 0 else "DOWN"
            parts.append(
                f"Price trend: {src['start']:.2f} -> {src['current']:.2f} "
                f"({direction} {abs(src['momentum']):.2f}), "
                f"volatility: {src['volatility']:.2f}, "
                f"recent trend: {src['trend']}"
            )

    return "\n".join(parts) if parts else "No research data available."
