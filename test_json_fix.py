#!/usr/bin/env python3
"""测试JSON序列化修复"""
import json
import math
from stocklib import backtest

def test_json_serialization():
    # 测试回测功能，确保能正常JSON序列化
    try:
        result = backtest.run_backtest(
            query="600519",
            strategy_name="chanlun",
            days=120,
            hold_days=10,
            stop_loss_pct=0.05,
            fresh=True
        )

        # 尝试序列化为JSON
        json_str = json.dumps(result, ensure_ascii=False)
        print("✅ JSON序列化成功！")
        print(f"返回数据键: {list(result.keys())}")
        print(f"交易数量: {result.get('trades_count', 0)}")
        return True
    except Exception as e:
        print(f"❌ JSON序列化失败: {e}")
        return False

if __name__ == "__main__":
    test_json_serialization()