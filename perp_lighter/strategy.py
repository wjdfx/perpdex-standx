import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional

# =========================
# 策略配置参数
# =========================

# 市况判断阈值
TREND_GAP_RANGE_THRESHOLD = 0.0012  # EMA快慢线差距阈值，低于此值视为震荡市况。数值越大，越容易判断为震荡，减少趋势交易；数值越小，越容易进入趋势模式
TREND_GAP_STRONG_THRESHOLD = 0.005  # EMA快慢线差距阈值，高于此值视为强趋势。数值越大，强趋势判断更严格，减少强趋势下的逆势禁止；数值越小，更容易触发强趋势保护

# 趋势衰竭检测参数
VOLUME_RATIO_EXHAUSTION = 1.5  # 近期成交量相对历史均值的放大倍数阈值。数值越大，衰竭信号触发更难，趋势延续性更强；数值越小，更容易检测到衰竭，增加反转机会
CONSECUTIVE_DIRECTION_EXHAUSTION = 6  # 连续同向K线数量阈值。数值越大，需要更长的连续运动才判断衰竭，减少反转信号；数值越小，更敏感地检测趋势过度

# RSI阈值
RSI_OVERSOLD_THRESHOLD = 40  # RSI超卖阈值，低于此值产生做多信号。数值越大，超卖判断更严格，减少做多信号；数值越小，更容易触发做多
RSI_OVERBOUGHT_THRESHOLD = 60  # RSI超买阈值，高于此值产生做空信号。数值越大，超买判断更严格，减少做空信号；数值越小，更容易触发做空
RSI_EXTREME_OVERSOLD = 25  # RSI极度超卖阈值，用于趋势衰竭时的增强判断。数值越大，极度超卖判断更严格；数值越小，更容易触发强力反转信号
RSI_EXTREME_OVERBOUGHT = 75  # RSI极度超买阈值，用于趋势衰竭时的增强判断。数值越大，极度超买判断更严格；数值越小，更容易触发强力反转信号

# 动态滞后阈值
TREND_THRESHOLD_RANGE = 0.1  # 震荡市况下信号强度翻向门槛。数值越大，震荡中更难改变方向，增加稳定性但减少灵活性；数值越小，更容易在震荡中切换方向
TREND_THRESHOLD_STRONG = 0.2  # 强趋势下信号强度翻向门槛。数值越大，强趋势中更难改变方向，减少反转执着但增加 whipsaw；数值越小，更容易在强趋势中反转
TREND_THRESHOLD_MEDIUM = 0.2  # 中等趋势下信号强度翻向门槛。数值越大，增加滞后稳定性；数值越小，增加响应速度

# 置信度评估阈值
CONTINUATION_STRONG_THRESHOLD = 0.5  # 趋势延续得分阈值，高于此值认为延续性强。数值越大，延续判断更严格，增加反转权重；数值越小，更容易认为趋势弱
REVERSAL_STRONG_THRESHOLD = 0.5  # 反转信号得分阈值，高于此值认为反转信号强。数值越大，反转判断更严格；数值越小，更容易触发反转
CONFIDENCE_DIFF_FUZZY = 0.15  # 延续与反转得分差异阈值，差异小于此值视为模糊。数值越大，模糊场景更多，增加小仓交易；数值越小，减少模糊判断

# 止盈止损基础倍数
BASE_TAKE_MULT = 0.4  # 基础止盈倍数（相对于ATR）。数值越大，止盈距离更远，减少频繁止盈但增加持仓风险；数值越小，更快止盈，增加交易频率
BASE_STOP_MULT = 0.55  # 基础止损倍数（相对于ATR）。数值越大，止损距离更远，减少止损触发但增加亏损幅度；数值越小，更紧止损，减少亏损但增加误触发

# 信号强度调整因子
STRENGTH_HIGH_MULTIPLIER = 1.15  # 高强度信号的止盈倍数乘数。数值越大，高强度信号的止盈更宽松；数值越小，减少高强度信号的优势
STRENGTH_LOW_MULTIPLIER = 0.95  # 低强度信号的止盈倍数乘数。数值越大，低强度信号的止盈相对宽松；数值越小，进一步收紧低强度信号的止盈
STRENGTH_HIGH_THRESHOLD = 0.8  # 高强度信号阈值。数值越大，高强度判断更严格；数值越小，更容易获得高强度待遇
STRENGTH_LOW_THRESHOLD = 0.3  # 低强度信号阈值。数值越大，低强度判断更严格；数值越小，更容易触发低强度调整

# 趋势强度过滤因子
STRONG_TREND_SIGNAL_MULTIPLIER = 1.5  # 强趋势下信号强度的放大倍数。数值越大，强趋势信号增强更多，增加顺势交易；数值越小，减少强趋势优势
RANGE_SIGNAL_MULTIPLIER = 0.7  # 震荡市况下信号强度的衰减倍数。数值越大，震荡中信号衰减较少；数值越小，震荡中信号更弱，更倾向于反转

# 顺逆势不对称因子
WITH_TREND_TAKE_MULTIPLIER = 1.05  # 顺势交易的止盈倍数乘数。数值越大，顺势止盈更宽松；数值越小，减少顺势优势
WITH_TREND_STOP_MULTIPLIER = 0.95  # 顺势交易的止损倍数乘数。数值越大，顺势止损更宽松；数值越小，顺势止损更紧
COUNTER_TREND_TAKE_MULTIPLIER = 0.95  # 逆势交易的止盈倍数乘数。数值越大，逆势止盈相对宽松；数值越小，进一步惩罚逆势
COUNTER_TREND_STOP_MULTIPLIER = 1.05  # 逆势交易的止损倍数乘数。数值越大，逆势止损更紧；数值越小，减少逆势惩罚

# 趋势放大因子
STRONG_TREND_TAKE_MULTIPLIER = 1.05  # 强趋势下的止盈倍数乘数。数值越大，强趋势止盈更宽；数值越小，减少强趋势止盈优势
RANGE_TAKE_MULTIPLIER = 0.95  # 震荡市况下的止盈倍数乘数。数值越大，震荡止盈相对宽松；数值越小，进一步收紧震荡止盈
RANGE_STOP_MULTIPLIER = 1.05  # 震荡市况下的止损倍数乘数。数值越大，震荡止损更紧；数值越小，减少震荡止损惩罚

# 追涨杀跌惩罚参数
STRETCHED_DISTANCE_THRESHOLD = 0.002  # 价格偏离均线的距离阈值（相对价格）。数值越大，惩罚触发更难，减少追涨杀跌抑制；数值越小，更容易触发惩罚
STRETCHED_PENALTY_MULTIPLIER = 0.8  # 追涨杀跌惩罚的信号强度乘数。数值越大，惩罚较轻；数值越小，惩罚更重，减少极端顺势

# RSI增强因子（趋势衰竭时）
RSI_EXHAUSTION_MULTIPLIER = 2.0  # 趋势衰竭时RSI信号的增强倍数。数值越大，衰竭时的RSI信号增强更多；数值越小，减少增强效果

# VWAP和EMA权重调整（趋势衰竭时）
VWAP_EXHAUSTION_MULTIPLIER = 0.5  # 趋势衰竭时VWAP信号的权重乘数。数值越大，衰竭时VWAP权重降低较少；数值越小，进一步降低VWAP权重
EMA_EXHAUSTION_MULTIPLIER = 0.3  # 趋势衰竭时EMA信号的权重乘数。数值越大，衰竭时EMA权重降低较少；数值越小，进一步降低EMA权重

# 反转信号权重
REVERSAL_WEIGHT_DIVERGENCE = 0.4  # RSI背离信号在反转评分中的权重。数值越大，背离对反转判断影响更大；数值越小，减少背离影响
REVERSAL_WEIGHT_VOLUME = 0.2  # 成交量耗尽信号在反转评分中的权重。数值越大，成交量对反转判断影响更大；数值越小，减少成交量影响
REVERSAL_WEIGHT_STAGNATION = 0.2  # 价格停滞信号在反转评分中的权重。数值越大，停滞对反转判断影响更大；数值越小，减少停滞影响
REVERSAL_WEIGHT_LEVEL_TEST = 0.3  # 关键价位测试信号在反转评分中的权重。数值越大，价位测试对反转判断影响更大；数值越小，减少价位影响

# 趋势延续权重
CONTINUATION_WEIGHT_MOMENTUM = 0.3  # 动量一致性在延续评分中的权重。数值越大，动量对延续判断影响更大；数值越小，减少动量影响
CONTINUATION_WEIGHT_VOLUME = 0.25  # 成交量配合在延续评分中的权重。数值越大，成交量对延续判断影响更大；数值越小，减少成交量影响
CONTINUATION_WEIGHT_PULLBACK = 0.25  # 回调健康度在延续评分中的权重。数值越大，回调对延续判断影响更大；数值越小，减少回调影响
CONTINUATION_WEIGHT_MA = 0.2  # 均线支撑在延续评分中的权重。数值越大，均线对延续判断影响更大；数值越小，减少均线影响

# 置信度计算因子
CONFIDENCE_CONTINUATION_MULTIPLIER = 1.15  # 趋势延续性强时的置信度乘数。数值越大，延续强时的置信度提升更多；数值越小，减少置信度提升
CONFIDENCE_REVERSAL_MULTIPLIER = 0.85  # 反转信号强时的置信度乘数。数值越大，反转强时的置信度降低较少；数值越小，进一步降低反转置信度
CONFIDENCE_FUZZY_MULTIPLIER = 0.7  # 模糊场景下的置信度乘数。数值越大，模糊时的置信度降低较少；数值越小，进一步降低模糊置信度

# 其他参数
HEALTHY_PULLBACK_RATIO = 0.03  # 健康的回调比例阈值。数值越大，允许更大回调仍视为健康；数值越小，更严格的回调健康判断
RESISTANCE_TEST_TOLERANCE = 0.005  # 阻力位测试的容差比例。数值越大，测试更容易触发；数值越小，更严格的价位测试
VOLUME_EXHAUSTION_RATIO = 2.0  # 成交量耗尽的放大倍数阈值。数值越大，耗尽判断更严格；数值越小，更容易检测耗尽
PRICE_STAGNATION_RATIO = 0.5  # 价格停滞的波动率降低比例。数值越大，停滞判断更严格；数值越小，更容易检测停滞

# ==== 双窗与波动Gate常量（常量配置，可按品种/分辨率调整） ====
# 决策短窗（仅用于择时/即时判断）
SHORT_DECISION_WINDOW = 20  # 1m建议10–30，5m建议10–20
# 基线长窗（用于分位/均值/方差/ATR基线等）
LONG_BASELINE_WINDOW = 300  # 1m建议300–600，5m建议≥100

# 波动暂停的分位化阈值
VOL_JUMP_QUANTILE = 0.98   # 单根收益绝对值的分位阈（如0.98=98分位）
ATR_RATIO_THRESHOLD = 3.0  # 当前ATR / 长窗ATR均值 的倍数阈
RECENT_CANDLES_FOR_GATE = 4
MIN_BASELINE_SAMPLES = 100  # 低于该样本数时回退到固定阈值

# 固定阈值回退（当长窗样本不足时使用）
FALLBACK_PRICE_JUMP_THRESHOLD = 0.015  # 1.5%
FALLBACK_ATR_RATIO_THRESHOLD = 2.5

# 趋势/震荡判定稳健化常量（1m降噪）
TREND_GAP_SMOOTH_SPAN = 10  # 对 trend_gap 指标做指数平滑的跨度
REGIME_PERSIST_RANGE = 2    # 判定“震荡”需满足的连续/累计满足次数
REGIME_PERSIST_STRONG = 2   # 判定“强趋势”需满足的连续/累计满足次数
VWAP_SLOPE_FLAT_THRESHOLD = 0.0002   # VWAP每bar归一化斜率阈值（2bp以内视为“平”）
VWAP_SLOPE_TREND_THRESHOLD = 0.0005  # 趋势确认所需VWAP斜率（≥5bp/每bar）
VOL_ADAPT_K = 0.6  # 阈值对波动的自适应强度（0-1）

# 进入/确认的滞回阈值（无全局状态下，采用更严格的“进入阈值”提高置信度）
HYSTERESIS_RANGE_FACTOR = 0.9   # 进入“震荡”采用更小的gap阈值（阈值×0.90）
HYSTERESIS_STRONG_FACTOR = 1.10  # 进入“强趋势”采用更大的gap阈值（阈值×1.10）

# 布林带宽度辅助判定（以价格归一化）
BB_WIDTH_RANGE_THRESHOLD = 0.003  # 低于此宽度（~30bp）偏向“震荡”
BB_WIDTH_TREND_MIN = 0.004        # 高于此宽度（~40bp）更可能“趋势”

# 高阶时间框（HTF）过滤常量（用更长跨度EMA近似5m/15m趋势过滤）
# 反馈：当前过滤偏强，先默认关闭，保留常量便于灰度开启
ENABLE_HTF_FILTER = False
HTF_EMA_FAST_SPAN_MULT = 5      # HTF快速EMA跨度系数：相当于把12扩到 12*5
HTF_EMA_SLOW_SPAN_MULT = 5      # HTF慢速EMA跨度系数：相当于把26扩到 26*5
HTF_GAP_RANGE_THRESHOLD = 0.002 # HTF下判定震荡的gap阈值（约20bp）
HTF_GAP_STRONG_THRESHOLD = 0.012# 提高为~120bp，避免过早拦截

# 中性市况对打分的衰减（降低1m误判带来的错误进场）
REGIME_NEUTRAL_SCORE_MULTIPLIER = 0.85

# 快速动量“推力”覆盖（不丢失1m短期走势/量能）
FAST_THRUST_WINDOW = 3            # 近N根内的快速位移窗口（1m建议2–4）
FAST_THRUST_RET = 0.0025          # 位移阈值（0.25%），过此视为有效推力
FAST_THRUST_VOL_EMA_SPAN = 30     # 成交量EMA用于基线
FAST_THRUST_VOL_MULT = 1.4        # 当前量 / 量EMA 的放大倍数阈值

# 推力后的“回钩”容忍（不丢失短期回踩后的继续启动）
THRUST_HOOK_WINDOW = 5            # 检测推力后的近N根作为回钩窗口
THRUST_HOOK_MAX_RET = 0.0015      # 允许的最大百分比回撤（0.15%）
THRUST_HOOK_MAX_ATR_MULT = 0.35   # 或以ATR的最大回撤倍数（≤0.35×ATR）

def compute_atr(df, period=21):
    """计算ATR"""
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr

def compute_vwap(df, period=20):
    """计算滚动VWAP，适合5分钟K线"""
    pv = (df['close'] * df['volume']).rolling(window=period).sum()
    v = df['volume'].rolling(window=period).sum()
    vwap = pv / v
    return vwap

def compute_rsi(df, period=21):
    """计算RSI"""
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# =========================
# 趋势分析增强函数
# =========================

def analyze_trend_continuation(df, direction):
    """
    分析趋势延续性，返回趋势继续的概率
    
    参数：
    - df: 价格数据
    - direction: 当前趋势方向 ("LONG" 或 "SHORT")
    
    返回：
    - continuation_score: 趋势延续性得分 (0-1)
    - analysis_details: 详细分析结果
    """
    if len(df) < 20:
        return 0.5, {"reason": "数据不足"}
    
    details = {}
    
    # 1. 动量持续性检测
    closes = df['close'].values
    volumes = df['volume'].values
    
    # 计算价格动量一致性
    recent_changes = np.diff(closes[-10:])
    if direction == "LONG":
        momentum_consistency = np.sum(recent_changes > 0) / len(recent_changes)
    else:
        momentum_consistency = np.sum(recent_changes < 0) / len(recent_changes)
    
    details["momentum_consistency"] = momentum_consistency
    
    # 2. 成交量配合度分析
    recent_volumes = volumes[-10:]
    volume_trend = np.polyfit(range(len(recent_volumes)), recent_volumes, 1)[0]
    
    # 上涨趋势中成交量应该放大，下跌趋势中成交量可能萎缩
    if direction == "LONG":
        volume_alignment = min(1.0, max(0.0, volume_trend / np.mean(recent_volumes) * 10 + 0.5))
    else:
        # 下跌趋势中成交量萎缩是健康的
        volume_alignment = min(1.0, max(0.0, 0.5 - volume_trend / np.mean(recent_volumes) * 10))
    
    details["volume_alignment"] = volume_alignment
    
    # 3. 回调深度分析
    if direction == "LONG":
        highs = df['high'].values
        recent_high = np.max(highs[-10:])
        current_price = closes[-1]
        pullback_ratio = (recent_high - current_price) / recent_high
        # 健康的回调通常不超过3%
        healthy_pullback = min(1.0, max(0.0, 1.0 - pullback_ratio / HEALTHY_PULLBACK_RATIO))
    else:
        lows = df['low'].values
        recent_low = np.min(lows[-10:])
        current_price = closes[-1]
        pullback_ratio = (current_price - recent_low) / recent_low
        healthy_pullback = min(1.0, max(0.0, 1.0 - pullback_ratio / HEALTHY_PULLBACK_RATIO))
    
    details["pullback_health"] = healthy_pullback
    
    # 4. 价格与关键均线关系
    ema_12 = df['close'].ewm(span=12).mean().iloc[-1]
    ema_26 = df['close'].ewm(span=26).mean().iloc[-1]
    current_price = closes[-1]
    
    if direction == "LONG":
        ma_support = min(1.0, max(0.0, (current_price - ema_12) / (ema_12 - ema_26) if ema_12 > ema_26 else 0.5))
    else:
        ma_support = min(1.0, max(0.0, (ema_12 - current_price) / (ema_12 - ema_26) if ema_12 < ema_26 else 0.5))
    
    details["ma_support"] = ma_support
    
    # 综合评分
    continuation_score = (
        momentum_consistency * CONTINUATION_WEIGHT_MOMENTUM +
        volume_alignment * CONTINUATION_WEIGHT_VOLUME +
        healthy_pullback * CONTINUATION_WEIGHT_PULLBACK +
        ma_support * CONTINUATION_WEIGHT_MA
    )
    
    return continuation_score, details

def detect_reversal_signals(df, direction):
    """
    检测反转信号，返回反转概率
    
    参数：
    - df: 价格数据
    - direction: 当前趋势方向 ("LONG" 或 "SHORT")
    
    返回：
    - reversal_score: 反转概率得分 (0-1)
    - analysis_details: 详细分析结果
    """
    if len(df) < 30:
        return 0.0, {"reason": "数据不足"}
    
    details = {}
    
    # 1. RSI背离检测
    df['RSI'] = compute_rsi(df)
    recent_prices = df['close'].values[-10:]
    recent_rsi = df['RSI'].values[-10:]
    
    # 检查价格新高但RSI不创新高（看跌背离）
    if direction == "LONG":
        price_high_idx = np.argmax(recent_prices)
        rsi_high_idx = np.argmax(recent_rsi)
        bearish_divergence = price_high_idx > rsi_high_idx and recent_prices[-1] > recent_prices[0]
        details["bearish_divergence"] = bearish_divergence
    else:
        # 检查价格新低但RSI不创新低（看涨背离）
        price_low_idx = np.argmin(recent_prices)
        rsi_low_idx = np.argmin(recent_rsi)
        bullish_divergence = price_low_idx > rsi_low_idx and recent_prices[-1] < recent_prices[0]
        details["bullish_divergence"] = bullish_divergence
    
    # 2. 成交量exhaustion检测
    recent_volumes = df['volume'].values[-10:]
    avg_volume = np.mean(recent_volumes)
    max_volume = np.max(recent_volumes)
    volume_exhaustion = max_volume > avg_volume * VOLUME_EXHAUSTION_RATIO  # 成交量激增
    details["volume_exhaustion"] = volume_exhaustion
    
    # 3. 价格停滞检测
    recent_highs = df['high'].values[-5:]
    recent_lows = df['low'].values[-5:]
    price_range = np.max(recent_highs) - np.min(recent_lows)
    avg_range = np.mean(df['high'].values[-20:] - df['low'].values[-20:])
    price_stagnation = price_range < avg_range * PRICE_STAGNATION_RATIO  # 波动率显著降低
    details["price_stagnation"] = price_stagnation
    
    # 4. 关键价位测试
    current_price = df['close'].iloc[-1]
    
    # 计算支撑阻力位
    recent_highs = df['high'].values[-20:]
    recent_lows = df['low'].values[-20:]
    
    if direction == "LONG":
        resistance_level = np.percentile(recent_highs, 90)
        resistance_test = abs(current_price - resistance_level) / resistance_level < RESISTANCE_TEST_TOLERANCE  # 0.5%以内
        details["resistance_test"] = resistance_test
    else:
        support_level = np.percentile(recent_lows, 10)
        support_test = abs(current_price - support_level) / support_level < RESISTANCE_TEST_TOLERANCE  # 0.5%以内
        details["support_test"] = support_test
    
    # 综合评分
    reversal_factors = []
    
    if direction == "LONG":
        if details.get("bearish_divergence", False):
            reversal_factors.append(REVERSAL_WEIGHT_DIVERGENCE)
        if volume_exhaustion:
            reversal_factors.append(REVERSAL_WEIGHT_VOLUME)
        if price_stagnation:
            reversal_factors.append(REVERSAL_WEIGHT_STAGNATION)
        if details.get("resistance_test", False):
            reversal_factors.append(REVERSAL_WEIGHT_LEVEL_TEST)
    else:
        if details.get("bullish_divergence", False):
            reversal_factors.append(REVERSAL_WEIGHT_DIVERGENCE)
        if volume_exhaustion:
            reversal_factors.append(REVERSAL_WEIGHT_VOLUME)
        if price_stagnation:
            reversal_factors.append(REVERSAL_WEIGHT_STAGNATION)
        if details.get("support_test", False):
            reversal_factors.append(REVERSAL_WEIGHT_LEVEL_TEST)
    
    reversal_score = min(1.0, sum(reversal_factors))
    
    return reversal_score, details

def assess_confidence(df, direction, strength):
    """
    评估交易信号的置信度
    
    参数：
    - df: 价格数据
    - direction: 信号方向
    - strength: 信号强度
    
    返回：
    - confidence_score: 置信度得分 (0-1)
    - recommendation: 建议操作 ("TRADE", "WAIT", "REDUCE")
    - analysis: 详细分析结果
    """
    # 趋势延续性分析
    continuation_score, continuation_details = analyze_trend_continuation(df, direction)
    
    # 反转信号检测
    reversal_score, reversal_details = detect_reversal_signals(df, direction)
    
    # 基础置信度（基于信号强度）
    base_confidence = min(1.0, strength / 2.0)  # 假设strength最大为2.0
    
    # 综合置信度计算（放宽门槛，提升成交率，并与交易层逻辑对齐）
    if continuation_score >= CONTINUATION_STRONG_THRESHOLD and reversal_score <= REVERSAL_STRONG_THRESHOLD:
        # 趋势延续性较强，反转信号较弱
        confidence_score = base_confidence * CONFIDENCE_CONTINUATION_MULTIPLIER
        recommendation = "TRADE"
    elif reversal_score >= REVERSAL_STRONG_THRESHOLD and continuation_score <= CONTINUATION_STRONG_THRESHOLD:
        # 反转信号较强，趋势延续性较弱 -> 小仓试单
        confidence_score = base_confidence * CONFIDENCE_REVERSAL_MULTIPLIER
        recommendation = "REDUCE"
    elif abs(continuation_score - reversal_score) < CONFIDENCE_DIFF_FUZZY:
        # 模糊场景：改为小仓试单，而非直接等待
        confidence_score = base_confidence * CONFIDENCE_FUZZY_MULTIPLIER
        recommendation = "REDUCE"
    else:
        # 其他中间情况：以base为主，并降低进入门槛
        confidence_score = base_confidence
        recommendation = "TRADE"
    
    # 确保置信度在0-1范围内
    confidence_score = min(1.0, max(0.0, confidence_score))
    
    analysis = {
        "continuation_score": continuation_score,
        "continuation_details": continuation_details,
        "reversal_score": reversal_score,
        "reversal_details": reversal_details,
        "base_confidence": base_confidence,
        "final_confidence": confidence_score
    }
    
    return confidence_score, recommendation, analysis

# =========================
# 数据类定义
# =========================

@dataclass
class TradingSignal:
    """交易信号数据类"""
    timestamp: int
    direction: str
    entry_price: float
    take_profit: float
    stop_loss: float
    rsi: float
    ema_fast: float
    ema_slow: float
    vwap: float
    score: float
    strength: float
    trend_gap: float
    take_mult: float
    stop_mult: float
    confidence_score: float = 0.0
    recommendation: str = "TRADE"

# =========================
# 核心策略逻辑
# =========================

def generate_signals(df, prev_direction=None, entry_price=None):
    """
    综合策略（趋势加速 + 动态强度 + 实际成交价止盈止损）
    如果提供 entry_price，则以该价格为基础计算止盈止损。
    """

    # === 指标计算 ===
    df['ATR'] = compute_atr(df)
    df['VWAP'] = compute_vwap(df)
    df['RSI'] = compute_rsi(df)
    df['EMA_FAST'] = df['close'].ewm(span=12).mean()
    df['EMA_SLOW'] = df['close'].ewm(span=26).mean()

    if len(df) < 50:
        return None

    # 双窗：短窗用于即时决策，长窗用于基线/归一化
    long_df = df.tail(LONG_BASELINE_WINDOW)
    short_df = df.tail(SHORT_DECISION_WINDOW)

    last = df.iloc[-1]
    avg_atr = long_df['ATR'].mean() if len(long_df) > 0 else df['ATR'].mean()

    price = last['close']
    atr = last['ATR']
    vwap = last['VWAP']
    rsi = last['RSI']
    ema_fast = last['EMA_FAST']
    ema_slow = last['EMA_SLOW']

    # === 信号打分（按市况自适应：趋势=动量，震荡=均值回归）===
    score = 0

    # 市况判断：双窗+平滑+持久化+宽度与滞回，降低1m误判
    # 1) 动态阈值：随波动自适应（以长窗ATR均值为基线）
    long_atr_mean_ctx = long_df['ATR'].mean() if len(long_df) > 0 else df['ATR'].mean()
    atr_ratio_ctx = (atr / long_atr_mean_ctx) if (long_atr_mean_ctx is not None and long_atr_mean_ctx > 0) else 1.0
    dyn_range_th = TREND_GAP_RANGE_THRESHOLD * max(0.5, 1 + VOL_ADAPT_K * (atr_ratio_ctx - 1))
    dyn_strong_th = TREND_GAP_STRONG_THRESHOLD * max(0.5, 1 + VOL_ADAPT_K * (atr_ratio_ctx - 1))
    # 进入阈值采用滞回系数（更严格的进入条件，提高判定置信度）
    dyn_range_enter = dyn_range_th * HYSTERESIS_RANGE_FACTOR
    dyn_strong_enter = dyn_strong_th * HYSTERESIS_STRONG_FACTOR

    # 2) 对 trend_gap 指标做指数平滑
    gap_series = (df['EMA_FAST'] - df['EMA_SLOW']).abs() / df['close']
    smoothed_gap = gap_series.ewm(span=TREND_GAP_SMOOTH_SPAN, min_periods=1).mean().iloc[-1]

    # 3) 持久化判定：近N根满足阈值的次数需达到门槛
    recent_gap = gap_series.tail(max(REGIME_PERSIST_STRONG, REGIME_PERSIST_RANGE))
    range_persist = (recent_gap < dyn_range_enter).sum() >= REGIME_PERSIST_RANGE
    strong_persist = (recent_gap > dyn_strong_enter).sum() >= REGIME_PERSIST_STRONG

    # 4) VWAP斜率与布林带宽度：平坦/窄带 → 震荡；明显坡度/宽带 → 趋势
    k = max(1, min(10, len(short_df) - 1)) if len(short_df) > 1 else 1
    vwap_slope = (short_df['VWAP'].iloc[-1] - short_df['VWAP'].iloc[-1 - k]) / (k * price) if len(short_df) > k else 0.0
    is_flat = abs(vwap_slope) < VWAP_SLOPE_FLAT_THRESHOLD
    has_trend_slope = abs(vwap_slope) >= VWAP_SLOPE_TREND_THRESHOLD

    bb_win = max(5, min(20, len(short_df)))
    bb_std = short_df['close'].rolling(window=bb_win).std().iloc[-1] if len(short_df) >= bb_win else 0.0
    bb_width = (2.0 * bb_std) / price if price > 0 else 0.0
    narrow_band = (bb_width < BB_WIDTH_RANGE_THRESHOLD)
    wide_band = (bb_width > BB_WIDTH_TREND_MIN)

    # 5) 综合判定（需要多信号共振），降低单一指标误判概率
    # 放宽震荡进入条件：平坦 或 窄带 任一满足即可，避免过度过滤
    is_range_regime = (smoothed_gap < dyn_range_enter) and range_persist and (is_flat or narrow_band)
    is_strong_trend = (smoothed_gap > dyn_strong_enter) and strong_persist and has_trend_slope and wide_band

    # 保持与后续返回结构兼容
    tmp_trend_gap = smoothed_gap

    # 快速动量推力覆盖：捕捉短期趋势发动，不被平滑/滞回过度抑制
    if len(df) > FAST_THRUST_WINDOW:
        _k = FAST_THRUST_WINDOW
        _ret = (df['close'].iloc[-1] - df['close'].iloc[-1 - _k]) / max(1e-12, df['close'].iloc[-1 - _k])
        _vol_now = df['volume'].iloc[-1]
        _vol_ema = df['volume'].ewm(span=FAST_THRUST_VOL_EMA_SPAN, min_periods=1).mean().iloc[-1]
        _vol_ok = (_vol_now > FAST_THRUST_VOL_MULT * max(1e-12, _vol_ema))
        if _vol_ok:
            if _ret >= FAST_THRUST_RET and ema_fast > ema_slow:
                is_strong_trend = True
                is_range_regime = False
            elif _ret <= -FAST_THRUST_RET and ema_fast < ema_slow:
                is_strong_trend = True
                is_range_regime = False

    # 推力+回钩（Hook）确认：强势启动后的浅回撤不超过阈值 → 仍视作趋势，避免错过一波
    if len(df) > max(FAST_THRUST_WINDOW, THRUST_HOOK_WINDOW) + 1:
        _k = FAST_THRUST_WINDOW
        _ret_base = df['close'].iloc[-1 - _k]
        _ret = (df['close'].iloc[-1] - _ret_base) / max(1e-12, _ret_base)
        _up = (_ret >= FAST_THRUST_RET) and (ema_fast > ema_slow)
        _dn = (_ret <= -FAST_THRUST_RET) and (ema_fast < ema_slow)
        _recent = df.tail(THRUST_HOOK_WINDOW + 1)
        if _up:
            _peak = _recent['close'].max()
            _pull = (_peak - _recent['close'].iloc[-1]) / max(1e-12, _peak)
            _pull_atr = (_peak - _recent['close'].iloc[-1]) / max(1e-12, atr)
            if _pull <= THRUST_HOOK_MAX_RET or _pull_atr <= THRUST_HOOK_MAX_ATR_MULT:
                is_strong_trend = True
                is_range_regime = False
        elif _dn:
            _trough = _recent['close'].min()
            _pull = (_recent['close'].iloc[-1] - _trough) / max(1e-12, _trough)
            _pull_atr = (_recent['close'].iloc[-1] - _trough) / max(1e-12, atr)
            if _pull <= THRUST_HOOK_MAX_RET or _pull_atr <= THRUST_HOOK_MAX_ATR_MULT:
                is_strong_trend = True
                is_range_regime = False

    # 高阶时间框过滤：用更长跨度EMA近似HTF趋势确认，降低1m误判（默认关闭，可灰度开启）
    if ENABLE_HTF_FILTER:
        ema_fast_htf = df['close'].ewm(span=int(12 * HTF_EMA_FAST_SPAN_MULT)).mean().iloc[-1]
        ema_slow_htf = df['close'].ewm(span=int(26 * HTF_EMA_SLOW_SPAN_MULT)).mean().iloc[-1]
        coarse_gap = abs(ema_fast_htf - ema_slow_htf) / price if price > 0 else 0.0
        dyn_range_htf = HTF_GAP_RANGE_THRESHOLD * max(0.5, 1 + VOL_ADAPT_K * (atr_ratio_ctx - 1))
        dyn_strong_htf = HTF_GAP_STRONG_THRESHOLD * max(0.5, 1 + VOL_ADAPT_K * (atr_ratio_ctx - 1))
        htf_is_range = coarse_gap < dyn_range_htf
        htf_is_strong = coarse_gap > dyn_strong_htf
        # 若HTF明确给出趋势/震荡信号，则优先采用HTF的判定，以降低1m噪声误判
        if htf_is_strong:
            is_strong_trend = True
            is_range_regime = False
        elif htf_is_range:
            is_range_regime = True
            is_strong_trend = False

    # === 量价关系分析：检测趋势衰竭信号 ===
    # 计算近期成交量变化
    recent_volumes = short_df['volume'].tail(5)
    avg_recent_volume = recent_volumes.mean()
    avg_historical_volume = long_df['volume'].mean() if len(long_df) > 0 else df['volume'].mean()
    volume_ratio = avg_recent_volume / avg_historical_volume if (avg_historical_volume is not None and avg_historical_volume > 0) else 1
    
    # 检测成交量持续放大（趋势可能衰竭）
    is_volume_sustained = volume_ratio > VOLUME_RATIO_EXHAUSTION  # 成交量放大50%以上
    
    # 检测价格连续同向（趋势可能过度）
    recent_closes = df['close'].tail(5).values
    if len(recent_closes) >= 2:
        price_changes = np.diff(recent_closes)
        consecutive_same_direction = 0
        current_direction = 1 if price_changes[-1] > 0 else -1
        for change in reversed(price_changes):
            if (change > 0 and current_direction > 0) or (change < 0 and current_direction < 0):
                consecutive_same_direction += 1
            else:
                break
    else:
        consecutive_same_direction = 0
    
    # 趋势衰竭判断：高成交量+连续同向
    is_trend_exhausted = is_volume_sustained and consecutive_same_direction >= CONSECUTIVE_DIRECTION_EXHAUSTION

    # 1) RSI 贡献：趋势→顺势；震荡→反向；趋势衰竭时增强RSI超买超卖信号
    if rsi < RSI_OVERSOLD_THRESHOLD:
        rsi_contrib = - (50 - rsi) / 10
    elif rsi > RSI_OVERBOUGHT_THRESHOLD:
        rsi_contrib = (rsi - 50) / 10
    else:
        rsi_contrib = 0

    # 趋势衰竭时增强RSI信号权重
    if is_trend_exhausted:
        if rsi > RSI_EXTREME_OVERBOUGHT:  # 超买严重，增强做空信号
            rsi_contrib *= RSI_EXHAUSTION_MULTIPLIER
        elif rsi < RSI_EXTREME_OVERSOLD:  # 超卖严重，增强做多信号
            rsi_contrib *= RSI_EXHAUSTION_MULTIPLIER
    
    if is_range_regime:
        rsi_contrib = -rsi_contrib
    score += rsi_contrib

    # 2) VWAP 贡献：趋势→价在VWAP上方偏多，震荡→价在VWAP上方偏空
    vwap_contrib = 0.5 if price > vwap else -0.5
    
    # 趋势衰竭时，VWAP信号权重降低（价格可能回归）
    if is_trend_exhausted:
        vwap_contrib *= VWAP_EXHAUSTION_MULTIPLIER
    
    if is_range_regime:
        vwap_contrib = -vwap_contrib
    score += vwap_contrib

    # 3) EMA趋势辅助（趋势衰竭时降低权重）
    ema_contrib = 0.15 if ema_fast > ema_slow else -0.15
    
    # 趋势衰竭时，EMA趋势信号权重降低
    if is_trend_exhausted:
        ema_contrib *= EMA_EXHAUSTION_MULTIPLIER
    
    score += ema_contrib

    # 4) 追涨杀跌惩罚：强趋势中，远离均值时削弱顺势分数，减少"追高/杀低"
    dist_ema = abs(price - ema_fast) / price
    dist_vwap = abs(price - vwap) / price
    stretched = min(dist_ema, dist_vwap) > STRETCHED_DISTANCE_THRESHOLD  # 15bp
    if is_strong_trend:
        if ema_fast > ema_slow and price > ema_fast and score > 0 and stretched:
            score *= STRETCHED_PENALTY_MULTIPLIER
        elif ema_fast < ema_slow and price < ema_fast and score < 0 and stretched:
            score *= STRETCHED_PENALTY_MULTIPLIER

    # === 趋势强度过滤 ===
    # 使用统一的趋势判断标准
    if is_strong_trend:
        score *= STRONG_TREND_SIGNAL_MULTIPLIER  # 强趋势 → 大幅增强顺势信号
    elif is_range_regime:
        score *= RANGE_SIGNAL_MULTIPLIER  # 震荡 → 减弱信号
    else:
        score *= REGIME_NEUTRAL_SCORE_MULTIPLIER  # 中性市况 → 进一步衰减，降低误判影响
    
    # 保留trend_gap变量用于返回结果
    trend_gap = tmp_trend_gap

    # === 确定方向（修正符号，使正分=做多） ===
    direction = "LONG" if score > 0 else "SHORT"
    strength = abs(score)

    # === 动态滞后：趋势强时提高翻向门槛；震荡时降低门槛，允许更灵活地切换 ===
    is_up_trend = ema_fast > ema_slow
    if is_range_regime:
        trend_threshold = TREND_THRESHOLD_RANGE
    elif is_strong_trend:
        trend_threshold = TREND_THRESHOLD_STRONG
    else:
        trend_threshold = TREND_THRESHOLD_MEDIUM
    if prev_direction and direction != prev_direction and strength < trend_threshold:
        direction = prev_direction

    # === 强趋势逆势禁止：趋势衰竭时允许逆势，否则禁止 ===
    is_with_trend = (direction == "LONG" and is_up_trend) or (direction == "SHORT" and not is_up_trend)
    
    if is_strong_trend and not is_with_trend:
        if is_trend_exhausted:
            # 趋势衰竭时，允许逆势但要求高置信度
            allow_counter = (
                (direction == "LONG" and rsi > RSI_EXTREME_OVERBOUGHT and strength > 1.2) or
                (direction == "SHORT" and rsi < RSI_EXTREME_OVERSOLD and strength > 1.2)
            )
            if not allow_counter:
                direction = "LONG" if is_up_trend else "SHORT"
                strength = max(strength, 0.8)
        else:
            # 正常强趋势，完全禁止逆势
            direction = "LONG" if is_up_trend else "SHORT"
            strength = max(strength, 0.8)

    # === 置信度评估（用于quant.py中的判断） ===
    confidence_score, recommendation, confidence_analysis = assess_confidence(df, direction, strength)

    # === 动态止盈止损倍数 ===
    atr_ratio = atr / avg_atr if avg_atr != 0 else 1
    base_take_mult = BASE_TAKE_MULT
    base_stop_mult = BASE_STOP_MULT

    if strength > STRENGTH_HIGH_THRESHOLD:
        take_mult = base_take_mult * STRENGTH_HIGH_MULTIPLIER
        stop_mult = base_stop_mult * (2 - STRENGTH_HIGH_MULTIPLIER)
    elif strength < STRENGTH_LOW_THRESHOLD:
        take_mult = base_take_mult * STRENGTH_LOW_MULTIPLIER
        stop_mult = base_stop_mult * (2 - STRENGTH_LOW_MULTIPLIER)
    else:
        take_mult = base_take_mult
        stop_mult = base_stop_mult

    # 趋势放大 / 收紧止盈止损
    if is_strong_trend:
        take_mult *= STRONG_TREND_TAKE_MULTIPLIER
    elif is_range_regime:
        take_mult *= RANGE_TAKE_MULTIPLIER
        stop_mult *= RANGE_STOP_MULTIPLIER

    # 顺/逆势不对称：顺势放宽止盈、收紧止损；逆势反之
    is_up_trend_now = ema_fast > ema_slow
    is_with_trend_now = (direction == "LONG" and is_up_trend_now) or (direction == "SHORT" and not is_up_trend_now)
    if is_with_trend_now:
        take_mult *= WITH_TREND_TAKE_MULTIPLIER
        stop_mult *= WITH_TREND_STOP_MULTIPLIER
    else:
        take_mult *= COUNTER_TREND_TAKE_MULTIPLIER
        stop_mult *= COUNTER_TREND_STOP_MULTIPLIER

    # 波动性调节
    take_mult *= atr_ratio
    stop_mult *= atr_ratio

    # === 快速成交目标（约1根K线内） ===
    # 假设当前DataFrame的时间粒度为1分钟；若非1分钟，请按比例调整 target_bars
    target_bars = 1
    bar_tr_series = (short_df['high'] - short_df['low'])
    recent_bar_tr = bar_tr_series.rolling(window=min(10, len(bar_tr_series))).median().iloc[-1]
    if np.isnan(recent_bar_tr) or recent_bar_tr == 0:
        recent_bar_tr = bar_tr_series.iloc[-1]

    # 以ATR为基础得到当前的TP/SL距离（价格单位）
    tp_distance = atr * take_mult
    sl_distance = atr * stop_mult

    # 将TP/SL限制在近期单根波动的上限内，提升1根K线内被触达的概率
    tp_cap = recent_bar_tr * 0.75 * target_bars
    sl_cap = recent_bar_tr * 1.15 * target_bars

    if not np.isnan(tp_cap) and tp_cap > 0:
        tp_distance = min(tp_distance, tp_cap)
    if not np.isnan(sl_cap) and sl_cap > 0:
        sl_distance = min(sl_distance, sl_cap)

    # 回映射为等效ATR倍数，便于后续统一计算与结果输出
    if atr and atr > 0:
        take_mult = tp_distance / atr
        stop_mult = sl_distance / atr

    # === 使用实际成交价（如果提供） ===
    entry_price = entry_price if entry_price is not None else price

    # === 计算止盈止损 ===
    if direction == "LONG":
        take_profit = entry_price + atr * take_mult
        stop_loss = entry_price - atr * stop_mult
    else:
        take_profit = entry_price - atr * take_mult
        stop_loss = entry_price + atr * stop_mult

    # === 返回结果 ===
    return TradingSignal(
        timestamp=df.index[-1],
        direction=direction,
        entry_price=round(entry_price, 2),
        take_profit=round(take_profit, 2),
        stop_loss=round(stop_loss, 2),
        rsi=round(rsi, 2),
        ema_fast=round(ema_fast, 2),
        ema_slow=round(ema_slow, 2),
        vwap=round(vwap, 2),
        score=round(score, 2),
        strength=round(strength, 2),
        trend_gap=round(trend_gap * 100, 3),
        take_mult=round(take_mult, 2),
        stop_mult=round(stop_mult, 2),
        confidence_score=round(confidence_score, 3),
        recommendation=recommendation
    )

# 检查波动率
def check_volatility(df, atr_threshold=3.0, price_jump_threshold=0.01, recent_candles=3):
    """
    改造：使用双窗分位数与ATR基线的波动Gate。
    常量配置见文件顶部（VOL_JUMP_QUANTILE、ATR_RATIO_THRESHOLD、RECENT_CANDLES_FOR_GATE 等）。
    注意：函数参数仅用于兼容旧调用，默认使用常量配置。
    返回 (bool, dict): (是否暂停开仓, 详细诊断信息)
    """
    if len(df) < 20:
        return False, {"reason": "数据不足"}

    df = df.copy()
    # 基础指标
    df['ATR'] = compute_atr(df)
    df['returns'] = df['close'].pct_change()

    # 双窗划分
    long_df = df.tail(LONG_BASELINE_WINDOW)
    short_df = df.tail(max(RECENT_CANDLES_FOR_GATE, recent_candles))

    # 样本不足时的回退逻辑（使用固定阈值）
    if len(long_df) < MIN_BASELINE_SAMPLES:
        current_atr = df['ATR'].iloc[-1]
        avg_atr = df['ATR'].iloc[-20:].mean()
        atr_ratio = current_atr / avg_atr if avg_atr and avg_atr > 0 else 1

        recent_vol = short_df['returns'].abs().max()
        recent_std = short_df['returns'].std()
        long_std = df['returns'].std()

        extreme_atr = atr_ratio > max(FALLBACK_ATR_RATIO_THRESHOLD, atr_threshold)
        sudden_jump = recent_vol > max(FALLBACK_PRICE_JUMP_THRESHOLD, price_jump_threshold)
        unstable = (recent_std > long_std * 2.0) if (long_std is not None and long_std > 0) else False

        if extreme_atr or sudden_jump or unstable:
            reason = []
            if extreme_atr:
                reason.append(f"ATR过高({atr_ratio:.2f}x)")
            if sudden_jump:
                reason.append(f"单根跳变>{max(FALLBACK_PRICE_JUMP_THRESHOLD, price_jump_threshold)*100:.1f}%")
            if unstable:
                reason.append("短期波动方差过大")
            return True, {"mode": "fallback", "atr_ratio": atr_ratio, "recent_vol": recent_vol, "recent_std": recent_std, "reason": ", ".join(reason)}

        return False, {"mode": "fallback", "atr_ratio": atr_ratio, "recent_vol": recent_vol, "recent_std": recent_std, "reason": "正常"}

    # 分位数阈值（长窗）
    long_abs_returns = long_df['returns'].abs().dropna()
    if len(long_abs_returns) == 0:
        jump_q = FALLBACK_PRICE_JUMP_THRESHOLD
    else:
        jump_q = np.quantile(long_abs_returns, VOL_JUMP_QUANTILE)

    # 指标与统计
    recent_vol = short_df['returns'].abs().max()
    recent_std = short_df['returns'].std()
    long_std = df['returns'].std()

    current_atr = df['ATR'].iloc[-1]
    long_atr_mean = long_df['ATR'].mean()
    atr_ratio = current_atr / long_atr_mean if (long_atr_mean is not None and long_atr_mean > 0) else 1

    sudden_jump = recent_vol > jump_q
    extreme_atr = atr_ratio > ATR_RATIO_THRESHOLD
    unstable = (recent_std > long_std * 2.0) if (long_std is not None and long_std > 0) else False

    if extreme_atr or sudden_jump or unstable:
        reason = []
        if extreme_atr:
            reason.append(f"ATR过高({atr_ratio:.2f}x)")
        if sudden_jump:
            reason.append(f"单根跳变>{VOL_JUMP_QUANTILE*100:.1f}%分位")
        if unstable:
            reason.append("短期波动方差过大")
        return True, {
            "mode": "quantile",
            "atr_ratio": atr_ratio,
            "recent_vol": recent_vol,
            "recent_std": recent_std,
            "jump_q": jump_q,
            "reason": ", ".join(reason),
        }

    return False, {
        "mode": "quantile",
        "atr_ratio": atr_ratio,
        "recent_vol": recent_vol,
        "recent_std": recent_std,
        "jump_q": jump_q,
        "reason": "正常",
    }
