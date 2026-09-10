#!/usr/bin/env python3
"""简单测试修复效果"""
import math

def safe_div(a, b, default=0.0):
    """安全的除法，避免除零错误"""
    if b == 0 or not math.isfinite(b):
        return default
    result = a / b
    return result if math.isfinite(result) else default

# 测试profit_factor计算
test_cases = [
    (100, 50),      # 正常情况
    (100, 0),       # 除零
    (0, 50),        # 零盈利
    (-100, 50),     # 负盈利
    (float('inf'), 50),  # Infinity输入
    (100, float('inf')), # Infinity分母
]

print("测试profit_factor计算:")
for total_win, total_loss in test_cases:
    result = safe_div(total_win, total_loss, 0.0)

    # 检查是否是Infinity或NaN
    if math.isinf(result) or math.isnan(result):
        result = None

    print(f"{total_win} / {total_loss} = {result}")

# 测试JSON序列化
import json

test_data = {
    "normal": 123.45,
    "inf": float('inf'),
    "profit_factor": float('inf'),
}

# 直接序列化
try:
    json.dumps(test_data)
    print("直接序列化: 成功")
except Exception as e:
    print(f"直接序列化: 失败 - {e}")

# 清理后序列化
def clean_data(data):
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            if k == "profit_factor" and isinstance(v, (int, float)):
                if math.isinf(v) or math.isnan(v):
                    cleaned[k] = None
                    continue
            cleaned[k] = clean_data(v)
        return cleaned
    elif isinstance(data, (int, float)):
        if math.isinf(data) or math.isnan(data):
            return None
        return data
    else:
        return data

cleaned = clean_data(test_data)
try:
    json.dumps(cleaned)
    print("清理后序列化: 成功")
except Exception as e:
    print(f"清理后序列化: 失败 - {e}")