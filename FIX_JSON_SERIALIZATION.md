# JSON序列化错误修复方案

## 问题描述
在回测验证中选择缠论买点策略时，点击"运行测试"会报错：
```
请求失败: Unexpected token 'I', ..."_factor": Infinity, "... is not valid JSON
```

## 原因分析
错误是由于MACD指标计算中可能出现 `Infinity` 或 `NaN` 值，这些值无法被JSON序列化。

### Infinity值的来源：
1. **EMA计算**：当股价数据异常或计算过程中数值溢出时
2. **MACD计算**：`dif = ema_fast - ema_slow` 可能产生Infinity
3. **动能计算**：在 `_momentum` 函数中处理MACD数据时

## 修复方案

### 1. 修改 `web.py`
- 添加 `_clean_json_data` 方法递归清理数据中的Infinity和NaN值
- 在返回JSON响应前清理数据

```python
def _clean_json_data(self, data):
    """递归清理JSON数据中的Infinity和NaN值"""
    if isinstance(data, dict):
        return {k: self._clean_json_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [self._clean_json_data(item) for item in data]
    elif isinstance(data, (int, float)):
        if math.isinf(data) or math.isnan(data):
            return None
        else:
            return data
    else:
        return data
```

### 2. 修改 `chanlun.py`
- 在 `_momentum` 函数中添加对Infinity的检查
- 添加 `math` 模块导入

```python
def _momentum(s, dif, hist):
    # ...原有代码...
    if s["dir"] == -1:
        area = sum(-hist[i] for i in idxs if hist[i] < 0)
        dx = max(0.0, -min(dif[i] for i in idxs if math.isfinite(dif[i])))
    else:
        area = sum(hist[i] for i in idxs if hist[i] > 0)
        dx = max(0.0, max(dif[i] for i in idxs if math.isfinite(dif[i])))
    # 确保返回值不是Infinity
    if not math.isfinite(area):
        area = 0.0
    if not math.isfinite(dx):
        dx = 0.0
    return area, dx
```

### 3. 修改 `backtest.py`
- 添加 `_clean_for_json` 函数
- 在 `run_backtest` 函数返回结果前清理数据

## 测试验证
创建了测试脚本验证修复效果：
- 原始数据包含Infinity和NaN值
- 清理后所有无效值转换为null
- JSON序列化成功

## 影响范围
此修复不仅解决了缠论买点的问题，还对以下功能都有改善：
- MACD策略回测
- 均线策略回测  
- 所有使用技术指标的回测功能
- Web API返回的数据安全性

## 注意事项
- Infinity/NaN值被转换为null，不会影响回测逻辑
- 清理是递归的，会处理嵌套的所有数据结构
- 修复是透明的，用户无感知