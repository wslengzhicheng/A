#!/usr/bin/env python3
"""测试回测修复效果"""
import sys
import os

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_profit_factor():
    """测试profit_factor计算"""
    import math

    def safe_div(a, b, default=0.0):
        """安全的除法，避免除零错误"""
        if b == 0 or not math.isfinite(b):
            return default
        result = a / b
        return result if math.isfinite(result) else default

    # 测试用例
    test_cases = [
        # (total_win, total_loss, expected)
        (100, 50, 2.0),           # 正常情况
        (100, 0, 0.0),            # 除零
        (0, 50, 0.0),             # 零盈利
        (-100, 50, -2.0),         # 负盈利
        (float('inf'), 50, None), # Infinity输入
        (100, float('inf'), None), # Infinity分母
        (float('nan'), 50, None), # NaN输入
    ]

    print("测试profit_factor计算:")
    for total_win, total_loss, expected in test_cases:
        result = safe_div(total_win, total_loss, 0.0)

        # 检查是否是Infinity或NaN
        if math.isinf(result) or math.isnan(result):
            result = None

        status = "✅" if result == expected else "❌"
        print(f"{status} {total_win} / {total_loss} = {result} (期望: {expected})")

def test_json_serialization():
    """测试JSON序列化"""
    import json

    # 创建包含各种值的测试数据
    test_data = {
        "normal": 123.45,
        "inf": float('inf'),
        "neg_inf": float('-inf'),
        "nan": float('nan'),
        "nested": {
            "profit_factor": float('inf'),
            "other": 42,
            "list": [1, 2, float('nan'), 4]
        }
    }

    print("\n测试JSON序列化:")

    # 直接序列化（应该失败）
    try:
        json.dumps(test_data)
        print("❌ 意外的成功")
    except Exception as e:
        print(f"✅ 预期的失败: {str(e)[:50]}...")

    # 使用我们的清理函数
    def clean_json_data(data):
        if isinstance(data, dict):
            cleaned = {}
            for k, v in data.items():
                if k == "profit_factor" and isinstance(v, (int, float)):
                    if math.isinf(v) or math.isnan(v):
                        cleaned[k] = None
                        continue
                cleaned[k] = clean_json_data(v)
            return cleaned
        elif isinstance(data, list):
            return [clean_json_data(item) for item in data]
        elif isinstance(data, (int, float)):
            if math.isinf(data) or math.isnan(data):
                return None
            return data
        else:
            return data

    cleaned = clean_json_data(test_data)
    try:
        json_str = json.dumps(cleaned, ensure_ascii=False)
        print("✅ 清理后序列化成功")
        print(f"清理后的数据: {json_str[:100]}...")
    except Exception as e:
        print(f"❌ 清理后仍然失败: {e}")

if __name__ == "__main__":
    test_profit_factor()
    test_json_serialization()