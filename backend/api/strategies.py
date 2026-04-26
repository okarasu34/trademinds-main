from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime

from db.database import get_db
from db.models import Strategy, StrategyType, User
from api.auth import get_current_user

router = APIRouter()

BUILTIN_STRATEGIES = [
    {
        "name": "Trend Following Pro",
        "description": "Multi-timeframe EMA crossovers with ADX confirmation.",
        "strategy_type": StrategyType.TREND_FOLLOWING,
        "markets": ["forex", "index", "stock"],
        "parameters": {
            "ema_fast": 9, "ema_slow": 50, "ema_trend": 200,
            "adx_threshold": 25, "rsi_min": 40, "rsi_max": 65,
            "min_confidence": 0.72, "max_risk_per_trade": 0.02,
            "trailing_stop": True,
        },
        "is_builtin": True, "priority": 100,
    },
    {
        "name": "Momentum Burst",
        "description": "RSI divergence + MACD histogram + volume confirmation.",
        "strategy_type": StrategyType.MOMENTUM,
        "markets": ["crypto", "stock"],
        "parameters": {
            "rsi_length": 14, "rsi_buy_threshold": 55, "rsi_sell_threshold": 45,
            "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "volume_ratio_min": 2.0, "min_confidence": 0.78,
        },
        "is_builtin": True, "priority": 90,
    },
    {
        "name": "Mean Reversion Elite",
        "description": "Bollinger Bands %B + Stochastic RSI mean reversion.",
        "strategy_type": StrategyType.MEAN_REVERSION,
        "markets": ["commodity", "forex", "crypto"],
        "parameters": {
            "bb_period": 20, "bb_std": 2.5,
            "bb_position_buy": 0.10, "bb_position_sell": 0.90,
            "stoch_oversold": 20, "stoch_overbought": 80,
            "min_confidence": 0.70, "max_holding_hours": 72,
        },
        "is_builtin": True, "priority": 80,
    },
    {
        "name": "Breakout Scalper",
        "description": "Session-based breakout with ATR stops.",
        "strategy_type": StrategyType.BREAKOUT,
        "markets": ["forex", "crypto"],
        "parameters": {
            "lookback_hours": 4, "breakout_threshold": 0.5,
            "atr_period": 14, "min_confidence": 0.68,
            "holding_max_minutes": 120,
        },
        "is_builtin": True, "priority": 60,
    },
]


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    strategy_type: StrategyType
    markets: List[str] = Field(..., min_length=1)
    symbols: List[str] = Field(default=[])
    parameters: dict = Field(default={})
    ai_system_prompt: Optional[str] = None
    priority: int = Field(default=0, ge=0, le=1000)
    use_ai_overlay: bool = True


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    markets: Optional[List[str]] = None
    symbols: Optional[List[str]] = None
    parameters: Optional[dict] = None
    ai_system_prompt: Optional[str] = None
    priority: Optional[int] = None
    use_ai_overlay: Optional[bool] = None


@router.get("")
async def list_strategies(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    active_only: bool = Query(False),
):
    await _ensure_builtins(user.id, db)
    query = select(Strategy).where(
        Strategy.user_id == user.id,
        Strategy.deleted_at.is_(None),
    )
    if active_only:
        query = query.where(Strategy.is_active == True)
    result = await db.execute(query.order_by(Strategy.priority.desc()))
    return [_serialize(s) for s in result.scalars().all()]


@router.post("", status_code=201)
async def create_strategy(
    body: StrategyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(Strategy).where(Strategy.user_id == user.id, Strategy.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Strategy '{body.name}' exists")
    strategy = Strategy(
        user_id=user.id, name=body.name, description=body.description,
        strategy_type=body.strategy_type, markets=body.markets,
        symbols=body.symbols, parameters=body.parameters,
        ai_system_prompt=body.ai_system_prompt, priority=body.priority,
        use_ai_overlay=body.use_ai_overlay, is_builtin=False, is_active=True,
    )
    db.add(strategy)
    await db.commit()
    await db.refresh(strategy)
    return _serialize(strategy)


@router.get("/{strategy_id}")
async def get_strategy(
    strategy_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return _serialize(await _get_or_404(strategy_id, user.id, db))


@router.put("/{strategy_id}")
async def update_strategy(
    strategy_id: UUID,
    body: StrategyUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    s = await _get_or_404(strategy_id, user.id, db)
    if s.is_builtin:
        allowed = {"is_active", "priority", "symbols", "ai_system_prompt", "use_ai_overlay"}
        attempted = set(body.dict(exclude_none=True).keys())
        if attempted - allowed:
            raise HTTPException(400, f"Built-in cannot modify: {attempted - allowed}")
    for key, value in body.dict(exclude_none=True).items():
        setattr(s, key, value)
    s.updated_at = datetime.utcnow()
    await db.commit()
    return _serialize(s)


@router.delete("/{strategy_id}", status_code=204)
async def delete_strategy(
    strategy_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    s = await _get_or_404(strategy_id, user.id, db)
    if s.is_builtin:
        raise HTTPException(400, "Cannot delete built-in. Disable instead.")
    s.is_active = False
    s.deleted_at = datetime.utcnow()
    await db.commit()


@router.post("/{strategy_id}/toggle")
async def toggle_strategy(
    strategy_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    s = await _get_or_404(strategy_id, user.id, db)
    s.is_active = not s.is_active
    s.updated_at = datetime.utcnow()
    await db.commit()
    return {"id": str(s.id), "is_active": s.is_active}


async def _get_or_404(strategy_id: UUID, user_id, db):
    result = await db.execute(
        select(Strategy).where(
            Strategy.id == str(strategy_id),
            Strategy.user_id == user_id,
            Strategy.deleted_at.is_(None),
        )
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Strategy not found")
    return s


async def _ensure_builtins(user_id, db):
    result = await db.execute(
        select(func.count(Strategy.id)).where(
            Strategy.user_id == user_id, Strategy.is_builtin == True
        )
    )
    if result.scalar() > 0:
        return
    for data in BUILTIN_STRATEGIES:
        db.add(Strategy(user_id=user_id, **data))
    await db.commit()


def _serialize(s: Strategy):
    return {
        "id": str(s.id), "name": s.name, "description": s.description,
        "strategy_type": s.strategy_type.value, "is_active": s.is_active,
        "is_builtin": s.is_builtin, "markets": s.markets, "symbols": s.symbols,
        "parameters": s.parameters, "ai_system_prompt": s.ai_system_prompt,
        "use_ai_overlay": s.use_ai_overlay, "priority": s.priority,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }