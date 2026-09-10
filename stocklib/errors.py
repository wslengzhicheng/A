"""统一错误模型（见 PLAN.md §4.3）。退出码：网络2 / 未找到3 / 数据4。"""


class StockError(Exception):
    exit_code = 1


class NetworkError(StockError):
    """全部数据源均失败。"""
    exit_code = 2

    def __init__(self, message, attempts=None):
        super().__init__(message)
        self.attempts = attempts or []  # [(源名, 错误摘要), ...] 用于网络诊断输出


class NotFoundError(StockError):
    """查询无匹配 / 无效代码 / 不支持的市场。"""
    exit_code = 3


class DataError(StockError):
    """源有返回但解析失败或数据不足。"""
    exit_code = 4
