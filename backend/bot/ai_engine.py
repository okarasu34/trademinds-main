import json
from loguru import logger
from typing import Optional


# ── Anthropic client (optional) ──────────────────────────────────────────────
_anthropic_client = None

def _get_client():
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    try:
        from core.config import settings
        if getattr(settings, "ANTHROPIC_API_KEY", None):
            import anthropic
            _anthropic_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    except Exception:
        pass
    return _anthropic_client


SYSTEM_PROMPT = """You are TradeMinds AI, an expert autonomous trading system.
You analyze financial markets across Forex, Crypto, Commodities, Stocks, and Indices.

Your role:
- Analyze technical indicators, price action, market sentiment, and news events
- Generate precise BUY, SELL, or HOLD signals with confidence scores
- Provide clear reasoning for every decision
- Apply the assigned trading strategy strictly
- Respect risk parameters provided

Always respond in valid JSON only. No markdown, no explanation outside JSON.

JSON format:
{
  "signal": "buy" | "sell" | "hold",
  "confidence": 0.0-1.0,
  "entry_price": float or null,
  "stop_loss": float,
  "take_profit": float,
  "lot_size_pct": 0.0-1.0,
  "reasoning": "detailed explanation",
  "key_factors": ["factor1", "factor2"],
  "risk_level": "low" | "medium" | "high",
  "expected_duration_hours": float
}
"""


def _technical_signal(
    symbol: str,
    indicators: dict,
    current_price: float,
    strategy_name: str,
    strategy_params: dict,
) -> dict:
    """
    Rule-based fallback signal using technical indicators.
    Used when Anthropic API is not available.
    """
    rsi = indicators.get("rsi_14", 50)
    macd_hist = indicators.get("macd_histogram", 0)
    macd_cross = indicators.get("macd_crossover", "bearish")
    adx = indicators.get("adx", 0)
    trend = indicators.get("trend_direction", "neutral")
    htf_trend = indicators.get("htf_trend_4h", "neutral")
    bb_pos = indicators.get("bb_position", 0.5)
    stoch_k = indicators.get("stoch_k", 50)
    ema_9 = indicators.get("ema_9", current_price)
    ema_21 = indicators.get("ema_21", current_price)
    ema_50 = indicators.get("ema_50", current_price)
    atr = indicators.get("atr_14", current_price * 0.01)

    signal = "hold"
    confidence = 0.0
    reasons = []

    strategy_type = strategy_name.lower()

    # ── Trend Following ──────────────────────────────────────────────────────
    if "trend" in strategy_type:
        buy_score = 0
        sell_score = 0

        if ema_9 > ema_21 > ema_50:
            buy_score += 2
            reasons.append("EMA bullish alignment")
        elif ema_9 < ema_21 < ema_50:
            sell_score += 2
            reasons.append("EMA bearish alignment")

        if macd_cross == "bullish" and macd_hist > 0:
            buy_score += 1
            reasons.append("MACD bullish crossover")
        elif macd_cross == "bearish" and macd_hist < 0:
            sell_score += 1
            reasons.append("MACD bearish crossover")

        if adx > 25:
            buy_score += 1 if trend == "bullish" else 0
            sell_score += 1 if trend == "bearish" else 0
            reasons.append(f"Strong trend ADX={adx:.0f}")

        if htf_trend == "bullish":
            buy_score += 1
        elif htf_trend == "bearish":
            sell_score += 1

        if 40 < rsi < 65 and buy_score > sell_score:
            signal = "buy"
            confidence = min(0.65 + buy_score * 0.05, 0.88)
        elif rsi > 35 and rsi < 60 and sell_score > buy_score:
            signal = "sell"
            confidence = min(0.65 + sell_score * 0.05, 0.88)

    # ── Momentum ─────────────────────────────────────────────────────────────
    elif "momentum" in strategy_type:
        vol_ratio = indicators.get("volume_ratio", 1.0)
        vol_ok = vol_ratio >= strategy_params.get("volume_ratio_min", 1.5)

        if rsi > 55 and macd_hist > 0 and macd_cross == "bullish":
            signal = "buy"
            confidence = 0.68 + (0.05 if vol_ok else 0)
            reasons.append(f"RSI={rsi:.0f} momentum bullish")
        elif rsi < 45 and macd_hist < 0 and macd_cross == "bearish":
            signal = "sell"
            confidence = 0.68 + (0.05 if vol_ok else 0)
            reasons.append(f"RSI={rsi:.0f} momentum bearish")

    # ── Mean Reversion ────────────────────────────────────────────────────────
    elif "mean" in strategy_type or "reversion" in strategy_type:
        if bb_pos < 0.15 and rsi < 35 and stoch_k < 25:
            signal = "buy"
            confidence = 0.70
            reasons.append(f"Oversold: BB={bb_pos:.2f} RSI={rsi:.0f}")
        elif bb_pos > 0.85 and rsi > 65 and stoch_k > 75:
            signal = "sell"
            confidence = 0.70
            reasons.append(f"Overbought: BB={bb_pos:.2f} RSI={rsi:.0f}")

    # ── Sentiment / Default ───────────────────────────────────────────────────
    else:
        if (ema_9 > ema_21 and macd_hist > 0 and
                30 < rsi < 65 and htf_trend != "bearish"):
            signal = "buy"
            confidence = 0.66
            reasons.append("Bullish technical confluence")
        elif (ema_9 < ema_21 and macd_hist < 0 and
              rsi > 35 and rsi < 70 and htf_trend != "bullish"):
            signal = "sell"
            confidence = 0.66
            reasons.append("Bearish technical confluence")

    # ── SL / TP calculation ───────────────────────────────────────────────────
    stop_loss = 0.0
    take_profit = 0.0

    if signal == "buy":
        stop_loss = round(current_price - atr * 1.5, 5)
        take_profit = round(current_price + atr * 3.0, 5)
    elif signal == "sell":
        stop_loss = round(current_price + atr * 1.5, 5)
        take_profit = round(current_price - atr * 3.0, 5)

    reasoning = f"[Technical] {', '.join(reasons)}" if reasons else "No clear signal"

    return {
        "signal": signal,
        "confidence": round(confidence, 3),
        "entry_price": current_price if signal != "hold" else None,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "lot_size_pct": 0.5,
        "reasoning": reasoning,
        "key_factors": reasons,
        "risk_level": "medium",
        "expected_duration_hours": 4.0,
    }


async def analyze_market(
    symbol: str,
    market_type: str,
    strategy_name: str,
    strategy_params: dict,
    indicators: dict,
    recent_news: list,
    economic_events: list,
    current_price: float,
    bid: float,
    ask: float,
    spread: float,
    open_positions: int,
    max_positions: int,
    account_balance: float,
    daily_pnl: float,
    max_daily_loss_pct: float,
    max_risk_pct: float,
    ai_system_prompt_override: Optional[str] = None,
) -> dict:
    """
    Core analysis function.
    Uses Anthropic AI if API key is set, otherwise falls back to technical indicators.
    """

    # Try AI first
    client = _get_client()
    if client is not None:
        try:
            from core.config import settings
            user_message = f"""
MARKET ANALYSIS REQUEST
=======================
Symbol: {symbol}
Market Type: {market_type}
Current Price: {current_price}
Bid: {bid} | Ask: {ask} | Spread: {spread:.5f}

STRATEGY
--------
Name: {strategy_name}
Parameters: {json.dumps(strategy_params, indent=2)}

TECHNICAL INDICATORS
--------------------
{json.dumps(indicators, indent=2)}

RECENT NEWS & SENTIMENT
-----------------------
{chr(10).join(f"- {n}" for n in recent_news) if recent_news else "No recent news"}

UPCOMING ECONOMIC EVENTS (next 4 hours)
----------------------------------------
{json.dumps(economic_events, indent=2) if economic_events else "None"}

ACCOUNT & RISK STATUS
---------------------
Account Balance: {account_balance:.2f}
Daily P&L: {daily_pnl:.2f}
Daily Loss Limit: {max_daily_loss_pct}%
Max Risk Per Trade: {max_risk_pct}%
Open Positions: {open_positions}/{max_positions}

TASK
----
Analyze all data and provide a trading signal.
If open positions are at max, signal HOLD.
"""
            system = ai_system_prompt_override or SYSTEM_PROMPT
            response = await client.messages.create(
                model=getattr(settings, "ANTHROPIC_MODEL", "claude-opus-4-6"),
                max_tokens=getattr(settings, "ANTHROPIC_MAX_TOKENS", 1000),
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text.strip()
            result = json.loads(raw)
            logger.info(f"AI signal {symbol}: {result['signal']} conf={result.get('confidence', 0):.2f}")
            return result
        except Exception as e:
            logger.warning(f"AI unavailable for {symbol}, using technical fallback: {e}")

    # Technical fallback
    result = _technical_signal(
        symbol=symbol,
        indicators=indicators,
        current_price=current_price,
        strategy_name=strategy_name,
        strategy_params=strategy_params,
    )
    if result["signal"] != "hold":
        logger.info(f"Technical signal {symbol}: {result['signal']} conf={result['confidence']:.2f}")
    return result


async def analyze_news_sentiment(
    symbol: str,
    currency: str,
    news_headlines: list,
) -> dict:
    client = _get_client()
    if client is None:
        return {"sentiment": "neutral", "score": 0.0, "key_themes": [], "impact_level": "low"}
    try:
        from core.config import settings
        response = await client.messages.create(
            model=getattr(settings, "ANTHROPIC_MODEL", "claude-opus-4-6"),
            max_tokens=512,
            system="You are a financial news sentiment analyzer. Respond in JSON only.",
            messages=[{
                "role": "user",
                "content": f"""
Analyze sentiment for {symbol} ({currency}) based on these headlines:
{chr(10).join(f"- {h}" for h in news_headlines)}

Respond as JSON:
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "score": -1.0 to 1.0,
  "key_themes": ["theme1"],
  "impact_level": "low" | "medium" | "high"
}}
"""
            }]
        )
        return json.loads(response.content[0].text.strip())
    except Exception:
        return {"sentiment": "neutral", "score": 0.0, "key_themes": [], "impact_level": "low"}


async def generate_daily_market_brief(
    market_summary: dict,
    top_opportunities: list,
    economic_calendar: list,
) -> str:
    client = _get_client()
    if client is None:
        return "Market brief unavailable (AI not configured)."
    try:
        from core.config import settings
        response = await client.messages.create(
            model=getattr(settings, "ANTHROPIC_MODEL", "claude-opus-4-6"),
            max_tokens=1000,
            system="You are a professional market analyst. Write concise, insightful daily briefs.",
            messages=[{
                "role": "user",
                "content": f"""
Write a professional daily market brief (max 300 words) covering:
Market Summary: {json.dumps(market_summary)}
Top Opportunities: {json.dumps(top_opportunities)}
Today's Key Events: {json.dumps(economic_calendar)}
"""
            }]
        )
        return response.content[0].text.strip()
    except Exception as e:
        return f"Market brief unavailable: {e}"