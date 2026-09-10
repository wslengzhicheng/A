#!/usr/bin/env python3
"""测试简单的JSON序列化"""
import json
import math

def clean_for_json(obj):
    """递归清理对象，确保可以JSON序列化"""
    if isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_for_json(item) for item in obj]
    elif isinstance(obj, (int, float)):
        if math.isinf(obj) or math.isnan(obj):
            return None
        return obj
    else:
        return obj

def test_clean():
    # 创建包含Infinity的测试数据
    test_data = {
        "normal_value": 123.45,
        "infinity_value": float('inf'),
        "negative_infinity": float('-inf'),
        "nan_value": float('nan'),
        "nested": {
            "price": 100,
            "ratio": float('inf'),
            "list": [1, 2, float('nan'), 4]
        }
    }

    print("原始数据:")
    print(json.dumps(test_data, ensure_ascii=False, indent=2))

    # 清理数据
    cleaned = clean_for_json(test_data)

    print("\n清理后数据:")
    print(json.dumps(cleaned, ensure_ascii=False, indent=2))

    # 确认可以序列化
    try:
        json_str = json.dumps(cleaned, ensure_ascii=False)
        print("\n[成功] JSON序列化成功！")
        return True
    except Exception as e:
        print(f"\n[失败] JSON序列化失败: {e}")
        return False

if __name__ == "__main__":
    test_clean()