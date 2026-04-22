import numpy as np

def generate_signal(df):
    """
    Returns: 'buy', 'sell', 'hold'
    """

    close = df['close']

    ema_fast = close.ewm(span=20).mean()
    ema_slow = close.ewm(span=50).mean()

    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))

    atr = (df['high'] - df['low']).rolling(14).mean()

    ema_fast_last = ema_fast.iloc[-1]
    ema_slow_last = ema_slow.iloc[-1]
    rsi_last = rsi.iloc[-1]
    atr_last = atr.iloc[-1]
    price = close.iloc[-1]

    trend_up = ema_fast_last > ema_slow_last
    trend_down = ema_fast_last < ema_slow_last

    # Volatility filter
    if atr_last / price < 0.005:
        return "hold"

    if trend_up and rsi_last < 65:
        return "buy"

    if trend_down and rsi_last > 35:
        return "sell"

    return "hold"
