import pandas as pd
import numpy as np


def compute_atr(df: pd.DataFrame, period=14):
    """计算ATR"""
    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr


def compute_vwap(df: pd.DataFrame, period=20):
    """计算滚动VWAP，适合5分钟K线"""
    pv = (df["close"] * df["volume"]).rolling(window=period).sum()
    v = df["volume"].rolling(window=period).sum()
    vwap = pv / v
    return vwap


def compute_ema(df: pd.DataFrame, period=20, column="close"):
    """计算EMA（指数移动平均）"""
    if column not in df:
        raise KeyError(f"列 {column} 不存在于DataFrame中")
    ema = df[column].ewm(span=period, adjust=False).mean()
    return ema


def compute_rsi(df: pd.DataFrame, period=14):
    """计算RSI"""
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_adx(df: pd.DataFrame, period=14):
    """计算ADX (Average Directional Index, 平均趋向指标)，用于衡量趋势的强度。周期14。"""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.DataFrame({"tr1": tr1, "tr2": tr2, "tr3": tr3}).max(axis=1)

    # Directional Movement
    up_move = high.diff()
    down_move = low.shift() - low

    plus_dm = pd.Series(0.0, index=df.index)
    mask_plus = (up_move > down_move) & (up_move > 0)
    plus_dm.loc[mask_plus] = up_move[mask_plus]

    minus_dm = pd.Series(0.0, index=df.index)
    mask_minus = (down_move > up_move) & (down_move > 0)
    minus_dm.loc[mask_minus] = down_move[mask_minus]

    # Wilder's smoothing using ewm (alpha = 1/period)
    alpha = 1 / period
    atr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_smooth)
    minus_di = 100 * (minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_smooth)

    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx, plus_di, minus_di


def compute_linear_regression_slope(df: pd.DataFrame, period=14):
    """计算Linear Regression Slope (线性回归斜率)，用于计算价格下跌的角度。周期14。"""

    def calc_slope(prices):
        if len(prices) < 2:
            return 0.0
        x = np.arange(len(prices))
        cov_xy = np.cov(x, prices)[0, 1]
        var_x = np.var(x)
        if var_x == 0:
            return 0.0
        return cov_xy / var_x

    slope = df["close"].rolling(window=period).apply(calc_slope, raw=True)
    return slope


def compute_roc(df: pd.DataFrame, period=14):
    """ROC (Rate of Change, 变动率)：(Close - Close_n) / Close_n * 100。简单版可设period=1: (Close - Open)/Open * 100"""
    roc = ((df["close"] - df["close"].shift(period)) / df["close"].shift(period)) * 100
    return roc


def compute_bollinger_pb(df: pd.DataFrame, period=20, std_dev=2.0):
    """Bollinger Bands %B (布林带位置)：(Close - Lower) / (Upper - Lower)，周期20，标准差2.0"""
    middle = df["close"].rolling(window=period).mean()
    bb_std = df["close"].rolling(window=period).std()
    upper = middle + (bb_std * std_dev)
    lower = middle - (bb_std * std_dev)
    pb = (df["close"] - lower) / (upper - lower)
    return pb


def compute_volume_ratio(df, period=20):
    """Volume Ratio (量比)：当前Volume / 过去20根K线Volume均值"""
    vol_avg = df["volume"].rolling(window=period).mean().shift(1)
    vr = df["volume"] / vol_avg
    return vr


def compute_atr_multiplier(df, atr_period=14, period=20):
    """ATR Multiplier：当前ATR / 过去ATR均值（atr_period=14, ma_period=20）"""
    atr = compute_atr(df, atr_period)
    atr_avg = atr.rolling(window=period).mean().shift(1)
    multiplier = atr / atr_avg
    return multiplier
