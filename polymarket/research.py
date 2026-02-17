"""Multi-source Research — Tavily + Polymarket Analytics + top trader signals"""
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


async def research_market(market: dict) -> dict:
    """Gather intelligence from multiple sources for a prediction market."""
    question = market["question"]
    context = {"question": question, "sources": [], "summary": ""}

    # ── Source 1: Tavily Web Search ──
    try:
        tavily = _get_tavily()
        if tavily:
            result = tavily.search(
                query=question,
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
    except Exception as e:
        logger.warning(f"Tavily search failed: {e}")

    # ── Source 2: Polymarket Analytics — Top trader positions ──
    try:
        slug = market.get("slug", "")
        if slug:
            async with httpx.AsyncClient(timeout=15) as client:
                # Check market activity from Gamma API
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

    # ── Source 3: Polymarket price history (recent momentum) ──
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
                            volatility = max(prices) - min(prices) if prices else 0
                            context["sources"].append({
                                "type": "price_history",
                                "current": prices[-1] if prices else 0,
                                "start": prices[0] if prices else 0,
                                "momentum": momentum,
                                "volatility": volatility,
                                "data_points": len(prices),
                            })
    except Exception as e:
        logger.warning(f"Price history fetch failed: {e}")

    # ── Compile summary ──
    parts = []
    for src in context["sources"]:
        if src["type"] == "web_search":
            parts.append(f"Web research: {src.get('answer', 'No answer')}")
            for r in src.get("results", [])[:3]:
                parts.append(f"  - {r['title']}: {r['snippet'][:150]}")
        elif src["type"] == "market_data":
            parts.append(
                f"Market: {src.get('num_traders', '?')} traders, "
                f"24h vol: ${src.get('volume_24h', 0):,.0f}"
            )
            if src.get("description"):
                parts.append(f"  Description: {src['description'][:200]}")
        elif src["type"] == "price_history":
            direction = "↑" if src["momentum"] > 0 else "↓"
            parts.append(
                f"Price trend: {src['start']:.2f} → {src['current']:.2f} "
                f"({direction} {abs(src['momentum']):.2f}), "
                f"volatility: {src['volatility']:.2f}"
            )

    context["summary"] = "\n".join(parts) if parts else "No research data available."
    return context
