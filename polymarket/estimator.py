"""Superforecasting Estimator — Tetlock 5-step methodology + Kelly sizing

Implements Philip Tetlock's superforecasting framework with three independent
estimation methods, explicit factor analysis, and mathematical Kelly criterion.
"""
import json
import logging
import anthropic
from config import cfg

logger = logging.getLogger("polyagent.pm.estimator")

SYSTEM_PROMPT = """You are an elite superforecaster trained in Philip Tetlock's Good Judgment Project methodology. Your forecasts consistently outperform prediction markets.

Follow this EXACT 5-step process:

## STEP 1: Decompose the Question
- Break the question into smaller, verifiable sub-questions.
- Identify what SPECIFICALLY needs to happen for YES to resolve.
- Identify the time horizon and resolution criteria.

## STEP 2: Establish Base Rates
- What is the historical base rate for this TYPE of event?
- Choose the most appropriate reference class.
- If no direct base rate exists, use the closest analogous events.
- Start with the base rate as your anchor.

## STEP 3: Gather and Weigh Evidence
- List ALL relevant evidence, both FOR and AGAINST.
- For each piece of evidence, rate its:
  - Reliability (is the source trustworthy?)
  - Relevance (how directly does it bear on the question?)
  - Recency (is this current information?)
- Weight evidence by reliability × relevance.

## STEP 4: Identify Cognitive Biases
- Check for these common biases in your own reasoning:
  - Anchoring to the current market price
  - Availability bias (overweighting recent/memorable events)
  - Confirmation bias (seeking evidence that confirms your initial view)
  - Scope insensitivity (treating different magnitudes the same)
- Explicitly adjust for any biases detected.

## STEP 5: Synthesize Final Probability
- Start with base rate from Step 2.
- Adjust up or down based on evidence from Step 3.
- Apply bias corrections from Step 4.
- Compare your estimate to the current market price.
- The market prices this at {market_price}. If your estimate differs significantly,
  explain WHY the market is wrong (smart money blind spot, information lag, bias).

CRITICAL RULES:
- Avoid extreme probabilities (below 5% or above 95%) unless evidence is overwhelming.
- If your estimate is within 3% of the market, recommend SKIP — the edge is too thin.
- You MUST respond with ONLY valid JSON. No markdown, no explanation outside JSON.

Respond in this EXACT format:
{
  "decomposition": "What needs to happen for YES: ...",
  "base_rate": 0.XX,
  "base_rate_reasoning": "Reference class: ... Historical rate: ...",
  "evidence_for": ["evidence 1", "evidence 2"],
  "evidence_against": ["evidence 1", "evidence 2"],
  "evidence_estimate": 0.XX,
  "evidence_reasoning": "Weighing for/against evidence...",
  "bias_check": "Biases detected and corrections applied: ...",
  "market_analysis": 0.XX,
  "market_analysis_reasoning": "The market at X% is wrong/right because...",
  "final_probability": 0.XX,
  "confidence": "low|medium|high",
  "edge_over_market": 0.XX,
  "recommended_side": "YES|NO|SKIP",
  "one_line_thesis": "..."
}"""


def _enrich_market_description(market: dict) -> str:
    """Convert structured market data into natural language for better LLM comprehension."""
    parts = [f"Question: {market['question']}"]

    parts.append(
        f"The current YES price is ${market['best_bid']:.3f} bid / "
        f"${market['best_ask']:.3f} ask (midpoint: {market['mid']:.1%})."
    )

    if market.get("volume"):
        vol = market["volume"]
        if vol >= 1_000_000:
            parts.append(f"This is a high-volume market with ${vol/1e6:.1f}M in total volume traded.")
        elif vol >= 100_000:
            parts.append(f"This market has moderate volume at ${vol/1e3:.0f}K traded.")
        else:
            parts.append(f"This is a low-volume market with ${vol:,.0f} traded.")

    if market.get("liquidity"):
        liq = market["liquidity"]
        if liq >= 50_000:
            parts.append(f"Liquidity is strong at ${liq/1e3:.0f}K.")
        elif liq >= 10_000:
            parts.append(f"Liquidity is moderate at ${liq/1e3:.0f}K.")
        else:
            parts.append(f"Liquidity is thin at ${liq:,.0f}.")

    if market.get("spread"):
        spread = market["spread"]
        if spread <= 0.02:
            parts.append("The bid-ask spread is very tight, indicating an efficient market.")
        elif spread <= 0.05:
            parts.append(f"The bid-ask spread is {spread:.1%}.")
        else:
            parts.append(f"The bid-ask spread is wide at {spread:.1%}, suggesting uncertainty or low participation.")

    if market.get("end_date"):
        parts.append(f"This market resolves by {market['end_date']}.")

    if market.get("category"):
        parts.append(f"Category: {market['category']}.")

    return "\n".join(parts)


async def estimate_market(market: dict, research: dict) -> dict | None:
    """Run superforecasting analysis on a market."""
    if not cfg.anthropic_key:
        logger.error("No Anthropic API key configured")
        return None

    market_price = market["mid"]
    prompt = SYSTEM_PROMPT.replace("{market_price}", f"{market_price:.0%}")

    # Build enriched market description
    market_desc = _enrich_market_description(market)

    # Build research section with structure
    research_text = _format_research(research)

    user_msg = f"""## MARKET TO ANALYZE
{market_desc}

## RESEARCH DATA
{research_text}

Using the 5-step superforecasting methodology, analyze this market and provide your probability estimate. Remember to check for biases and explain any disagreement with the market price."""

    try:
        client = anthropic.Anthropic(api_key=cfg.anthropic_key)
        response = client.messages.create(
            model=cfg.research_model,
            max_tokens=1500,
            system=prompt,
            messages=[{"role": "user", "content": user_msg}],
        )

        text = response.content[0].text.strip()
        # Clean potential markdown fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        result = json.loads(text)

        # Validate and extract
        prob = float(result.get("final_probability", 0.5))
        edge = prob - market_price
        side = result.get("recommended_side", "SKIP")
        confidence = result.get("confidence", "low")

        # Kelly criterion sizing
        kelly_bet = _kelly_size(prob, market_price, side)

        return {
            "probability": prob,
            "edge": edge,
            "abs_edge": abs(edge),
            "side": side,
            "confidence": confidence,
            "kelly_bet": kelly_bet,
            "thesis": result.get("one_line_thesis", ""),
            "base_rate": result.get("base_rate"),
            "evidence": result.get("evidence_estimate"),
            "market_analysis": result.get("market_analysis"),
            "base_rate_reasoning": result.get("base_rate_reasoning", ""),
            "evidence_reasoning": result.get("evidence_reasoning", ""),
            "market_reasoning": result.get("market_analysis_reasoning", ""),
            "decomposition": result.get("decomposition", ""),
            "evidence_for": result.get("evidence_for", []),
            "evidence_against": result.get("evidence_against", []),
            "bias_check": result.get("bias_check", ""),
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude response: {e}")
        return None
    except Exception as e:
        logger.error(f"Estimation failed: {e}")
        return None


def _format_research(research: dict) -> str:
    """Format research data into structured text for the LLM."""
    parts = []

    for src in research.get("sources", []):
        if src["type"] == "web_search":
            answer = src.get("answer", "")
            if answer:
                parts.append(f"### Web Search Summary\n{answer}")
            results = src.get("results", [])
            if results:
                parts.append("### Web Sources")
                for r in results[:4]:
                    parts.append(f"- **{r['title']}**: {r['snippet'][:200]}")

        elif src["type"] == "market_data":
            parts.append("### Market Intelligence")
            traders = src.get("num_traders", "?")
            vol24 = src.get("volume_24h", 0)
            parts.append(f"- Unique traders: {traders}")
            parts.append(f"- 24h volume: ${vol24:,.0f}")
            desc = src.get("description", "")
            if desc:
                parts.append(f"- Market description: {desc[:300]}")
            res_src = src.get("resolution_source", "")
            if res_src:
                parts.append(f"- Resolution source: {res_src}")

        elif src["type"] == "price_history":
            direction = "UP" if src["momentum"] > 0 else "DOWN"
            parts.append("### Price History")
            parts.append(
                f"- Trend: {src['start']:.2f} -> {src['current']:.2f} "
                f"({direction} {abs(src['momentum']):.2f})"
            )
            parts.append(f"- Volatility range: {src['volatility']:.2f}")
            parts.append(f"- Data points: {src['data_points']}")

    return "\n".join(parts) if parts else "No research data available."


def _kelly_size(prob: float, market_price: float, side: str) -> float:
    """Calculate Kelly criterion bet size.

    f = (p * b - q) / b
    where b = payout odds = (1-price)/price, q = 1-p
    """
    if side == "SKIP" or side not in ("YES", "NO"):
        return 0.0

    if side == "YES":
        p = prob
        price = market_price
    else:  # NO
        p = 1 - prob
        price = 1 - market_price

    if price <= 0 or price >= 1:
        return 0.0

    b = (1 - price) / price  # payout odds
    q = 1 - p
    f = (p * b - q) / b if b > 0 else 0

    if f <= 0:
        return 0.0

    # Fractional Kelly (conservative)
    bet = f * cfg.pm_kelly_fraction * cfg.poly_bankroll

    # Apply limits
    bet = min(bet, cfg.pm_max_bet)
    bet = max(bet, 0.50)  # Min $0.50 bet

    return round(bet, 2)
