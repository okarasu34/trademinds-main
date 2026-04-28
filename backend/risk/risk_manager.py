from dataclasses import dataclass
from typing import Optional, Union
from loguru import logger
from db.models import BotConfig, Trade, OrderStatus, TradeMode
from bot.indicators import calculate_lot_size_from_risk


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str
    adjusted_lot_size: Optional[float] = None


class RiskManager:
    """
    Enforces all user-defined risk limits before any trade is executed.
    This is the gatekeeper — no trade passes without its approval.
    """

    def __init__(self, config: BotConfig):
        self.config = config

    def _is_paper(self) -> bool:
        return getattr(self.config, "trade_mode", None) == TradeMode.PAPER

    async def check_new_trade(
        self,
        user_id: str,
        market_type: str,
        symbol: str,
        lot_size: float,
        entry_price: float,
        stop_loss: float,
        account_balance: float,
        open_positions: Union[list, set],
        upcoming_news: list,
    ) -> RiskCheckResult:

        # Normalize open_positions — may be Trade objects or strings
        def _count_open(positions):
            return len(positions)

        def _count_market(positions, mtype):
            count = 0
            for p in positions:
                if isinstance(p, str):
                    pass  # strings are just symbols, can't filter by market type
                elif hasattr(p, "market_type"):
                    try:
                        if p.market_type.value == mtype:
                            count += 1
                    except Exception:
                        pass
            return count

        def _has_symbol(positions, sym):
            for p in positions:
                if isinstance(p, str):
                    if p.upper() == sym.upper():
                        return True
                elif hasattr(p, "symbol"):
                    if p.symbol.upper() == sym.upper():
                        return True
            return False

        # 1. Global position limit
        total_open = _count_open(open_positions)
        if total_open >= self.config.max_positions:
            return RiskCheckResult(
                allowed=False,
                reason=f"Max positions reached ({total_open}/{self.config.max_positions})"
            )

        # 2. Per-market position limit
        market_limits = self.config.market_limits or {}
        market_limit = market_limits.get(market_type, 999)
        market_open = _count_market(open_positions, market_type)
        if market_open >= market_limit:
            return RiskCheckResult(
                allowed=False,
                reason=f"Market limit reached for {market_type} ({market_open}/{market_limit})"
            )

        # 3. Daily loss limit (skip in paper mode with zero balance)
        if account_balance > 0:
            daily_loss_pct = (self.config.daily_loss / account_balance) * 100
            if daily_loss_pct >= self.config.max_daily_loss_pct:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Daily loss limit hit ({daily_loss_pct:.1f}% >= {self.config.max_daily_loss_pct}%)"
                )

        # 4. Risk per trade limit
        price_diff = abs(entry_price - stop_loss)
        if price_diff > 0 and account_balance > 0:
            if market_type == "forex":
                risk_amount = lot_size * price_diff * 100_000
            elif market_type == "crypto":
                risk_amount = lot_size * price_diff
            elif market_type in ("stock", "index"):
                risk_amount = lot_size * price_diff
            elif market_type == "commodity":
                risk_amount = lot_size * price_diff * 100
            else:
                risk_amount = lot_size * price_diff * 100_000

            risk_pct = (risk_amount / account_balance) * 100

            if risk_pct > self.config.max_risk_per_trade_pct:
                adjusted_lot = calculate_lot_size_from_risk(
                    account_balance=account_balance,
                    risk_pct=self.config.max_risk_per_trade_pct,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    market_type=market_type,
                )
                logger.warning(
                    f"Risk {risk_pct:.2f}% > limit {self.config.max_risk_per_trade_pct}%. "
                    f"Adjusting lot from {lot_size} to {adjusted_lot}"
                )
                lot_size = adjusted_lot

        # 5. High-impact news pause
        if self.config.pause_on_high_impact_news and upcoming_news:
            high_impact = [e for e in upcoming_news if e.get("impact") == "high"]
            if high_impact:
                next_event = high_impact[0]
                minutes_until = next_event.get("minutes_until", 999)
                if minutes_until <= self.config.news_pause_minutes:
                    return RiskCheckResult(
                        allowed=False,
                        reason=f"High-impact news in {minutes_until}min: {next_event.get('title', 'Unknown')}"
                    )

        # 6. Duplicate symbol check
        if _has_symbol(open_positions, symbol):
            return RiskCheckResult(
                allowed=False,
                reason=f"Already have open position on {symbol}"
            )

        return RiskCheckResult(allowed=True, reason="All checks passed", adjusted_lot_size=lot_size)

    def check_daily_limit_warning(self, account_balance: float) -> Optional[str]:
        if account_balance <= 0:
            return None
        daily_loss_pct = (self.config.daily_loss / max(account_balance, 1)) * 100
        warning_threshold = self.config.max_daily_loss_pct * 0.8
        if daily_loss_pct >= warning_threshold:
            return (
                f"WARNING: Daily loss at {daily_loss_pct:.1f}% "
                f"(limit: {self.config.max_daily_loss_pct}%)"
            )
        return None

    def should_emergency_stop(self, account_balance: float) -> bool:
        """Returns True if bot should stop all activity immediately."""
        # Paper mode with zero balance — never emergency stop
        if account_balance <= 0:
            if self._is_paper():
                return False
            return True
        daily_loss_pct = (self.config.daily_loss / account_balance) * 100
        return daily_loss_pct >= self.config.max_daily_loss_pct