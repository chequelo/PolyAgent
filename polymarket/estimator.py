"""Superforecasting Estimator — Multi-method probability estimation + Kelly sizing"""
import json
import logging
import anthropic
from config import cfg

logger = logging.getLogger("polyagent.pm.estimator")

SYSTEM_PROMPT = """You are an elite superforecaster trained in the methods of Philip Tetlock's Good Judgment Project. Your forecasts consistently beat prediction markets by 18-24%.

You will estimate the probability of an event using THREE independent methods, then synthesize:

## METHOD 1: Base Rate Analysis
- What is the historical base rate for this type of event?
- What reference class is most appropriate?
- Adjust for specifics of this case.

## METHOD 2: Evidence Weighing
- What does the current evidence suggest?
- Weight each piece of evidence by reliability and relevance.
- Consider both confirming and disconfirming evidence.

## METHOD 3: Market Analysis + Contrarian Check
- The market currently prices this at {market_price:.0%}.
- Is the market overconfident? Underconfident?
- What biases might affect market participants? (acquiescence bias, recency bias, herd mentality)
- Markets tend to slightly overestimate event probabilities (research shows ~94% accuracy but with systematic overestimation).

## SYNTHESIS
- Average the three methods, weighting by your confidence in each.
- Apply the "outside view" correction.
- Report your FINAL probability.

CRITICAL: You MUST respond with valid JSON only. No markdown, no explanation outside JSON.

Respond EXACTLY in this JSON format:
{
  "base_rate_estimate": 0.XX,
  "base_rate_reasoning": "...",
  "evidence_estimate": 0.XX,
  "evidence_reasoning": "...",
  "market_analysis_estimate": 0.XX,
  "market_analysis_reasoning": "...",
  "final_probability": 0.XX,
  "confidence": "low|medium|high",
  "edge_over_market": 0.XX,
  "recommended_side": "YES|NO|SKIP",
  "one_line_thesis": "..."
}"""


async def estimate_market(market: dict, research: dict) -> dict | None:
    """Run superforecasting analysis on a market."""
    if not cfg.anthropic_key:
        logger.error("No Anthropic API key configured")
        return None

    market_price = market["mid"]
    prompt = SYSTEM_PROMPT.replace("{market_price:.0%}", f"{market_price:.0%}")

    user_msg = f"""## MARKET
Question: {market['question']}
Current YES price: ${market['best_bid']:.3f} - ${market['best_ask']:.3f} (mid: {market_price:.3f})
Volume: ${market['volume']:,.0f}
Liquidity: ${market['liquidity']:,.0f}
Category: {market.get('category', 'Unknown')}
End date: {market.get('end_date', 'Unknown')}

## RESEARCH
{research.get('summary', 'No research available.')}

Analyze this market using the three methods and provide your probability estimate."""

    try:
        client = anthropic.Anthropic(api_key=cfg.anthropic_key)
        response = client.messages.create(
            model=cfg.research_model,
            max_tokens=1000,
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
            "base_rate": result.get("base_rate_estimate"),
            "evidence": result.get("evidence_estimate"),
            "market_analysis": result.get("market_analysis_estimate"),
            "base_rate_reasoning": result.get("base_rate_reasoning", ""),
            "evidence_reasoning": result.get("evidence_reasoning", ""),
            "market_reasoning": result.get("market_analysis_reasoning", ""),
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude response: {e}")
        return None
    except Exception as e:
        logger.error(f"Estimation failed: {e}")
        return None


def _kelly_size(prob: float, market_price: float, side: str) -> float:
    """Calculate Kelly criterion bet size."""
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

    # Kelly: f = (p * b - q) / b where b = (1-price)/price, q = 1-p
    b = (1 - price) / price  # odds
    q = 1 - p
    f = (p * b - q) / b if b > 0 else 0

    if f <= 0:
        return 0.0

    # Fractional Kelly
    bet = f * cfg.pm_kelly_fraction * cfg.poly_bankroll

    # Apply limits
    bet = min(bet, cfg.pm_max_bet)
    bet = max(bet, 0.50)  # Min $0.50 bet

    return round(bet, 2)
