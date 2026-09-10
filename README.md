# A股个股分析程序

命令行工具：按**名称或代码**查询沪深A股，输出多维指标分析、0~100 综合评分、中文总结与后市走势预测。零安装——项目自带 Python 运行时。

## 快速开始

```bat
cd F:\Allen\A
stock.bat 贵州茅台          :: 按名称
stock.bat 600519            :: 按代码（也支持 sh600519 / 000001.SZ）
stock.bat 600519 --days 120 :: 自定义分析窗口（30~320个交易日，默认250）
stock.bat 600519 --json     :: 输出机器可读 JSON
stock.bat 600519 --fresh    :: 跳过当日缓存强制取新
stock.bat --test            :: 离线自检（指标断言+预测不变量+fixtures回放）
stock.bat                   :: 帮助
```

名称有歧义时（如"平安"）会列出候选并自动选择第一只，用代码可精确指定。

## Docker 运行

可以。这个项目本质上是一个标准 Python Web 服务，适合直接打包进 Docker 运行，不需要把仓库自带的 Windows Python 一起带进镜像。

构建镜像：

```bash
docker build -t a-stock-analyzer .
```

启动 Web 服务：

```bash
docker run --rm -p 8888:8888 -v ${PWD}/cache:/app/cache a-stock-analyzer
```

浏览器打开 `http://localhost:8888`。

说明：

- 镜像入口默认执行 `python stock.py --web --port 8888`
- 建议挂载 `cache/`，这样容器重启后仍能复用缓存
- 容器运行时仍然需要能访问腾讯、东方财富、AkShare 对应的公网数据源
- 如果是在 Windows PowerShell 中运行，可把挂载参数改成 `-v ${PWD}\cache:/app/cache`

### 部署到极空间 NAS（Docker）

如果你不是在本机 Docker 跑，而是要放到极空间 NAS，推荐下面两种方式。

#### 方式 A：先打包镜像，再导入极空间

1. 在一台已安装并启动 Docker 的电脑构建 Linux 镜像（按 NAS 架构选平台）：

```bash
# 极空间大多数 x86 机型
docker buildx build --platform linux/amd64 -t a-stock-analyzer:latest --load .

# 如果你的极空间是 ARM 架构，则改成 linux/arm64
# docker buildx build --platform linux/arm64 -t a-stock-analyzer:latest --load .
```

2. 导出镜像：

```bash
docker save -o a-stock-analyzer_latest.tar a-stock-analyzer:latest
```

3. 把 `a-stock-analyzer_latest.tar` 上传到 NAS，在极空间 Docker 中导入镜像。

4. 创建容器时配置：

- 镜像：`a-stock-analyzer:latest`
- 端口映射：`8888:8888`
- 卷映射：NAS 持久目录 -> `/app/cache`（例如 `/volume1/docker/a-stock-analyzer/cache:/app/cache`）
- 环境变量：`TZ=Asia/Shanghai`
- 重启策略：`unless-stopped`

5. 启动后访问：`http://NAS_IP:8888`

#### 方式 B：在极空间用 compose 启动

仓库中已提供 `docker-compose.nas.yml`，内容已经包含端口、时区和缓存卷。

```bash
# 在 NAS 上（或极空间 compose 界面）
export CACHE_DIR=/volume1/docker/a-stock-analyzer/cache
docker compose -f docker-compose.nas.yml up -d
```

如果看到下面错误：

`open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified`

含义是你当前这台电脑的 Docker Desktop 没启动。对 NAS 部署来说，可以忽略本机 Docker，直接在极空间执行上面的 compose 启动；或者先启动 Docker Desktop 再在本机构建导出镜像。

说明：

- `CACHE_DIR` 用你的 NAS 实际目录替换
- 如果极空间的 compose 页面不支持 `build`，就先用方式 A 导入镜像再 `up`
- 若访问失败，优先检查 NAS 防火墙/端口放行和容器网络模式

**范围**：仅沪深A股（60/68/00/30 开头）；北交所/三板暂不支持。

## 报告内容

- **行情快照**：现价、涨跌、量额、换手、PE(TTM)、PB、市值、52周区间、年化波动率、财务摘要（ROE/营收与净利同比/毛利率）
- **指标信号**：MA(5/10/20/60)、MACD(12/26/9)、KDJ(9/3/3)、RSI(6/12/24)、BOLL(20,2σ)、量价配合、年度位置——每项给出 -2~+2 信号分与中文解读
- **结构分析（缠论·日线单级别）**：K线包含处理→分型→笔（老笔标准）→笔中枢→MACD背驰（趋势/盘整）→三类买卖点（含"类一买"），输出笔方向、中枢区间 [ZD,ZG]、背驰状态与最近买卖点；买卖点/背驰仅近10个交易日内确认才计分，更早的仅陈述。实现口径与放宽声明见 PLAN.md §9
- **综合评分**（口径 **v1.3**，与更早版本不可横向比较）：信号类内加权归一后按类别加权；趋势与缠论冲突时结构类权重减半；**行业 PE 分位**优先于绝对阈值；RSI6/12 联合 + **独立 RSI24**。当前默认权重可被 `stocklib/calibration_overrides.json` 覆盖（由 `python -m stocklib.calibrate --live N --apply` 生成；已按约 20 股×713 点短线 IC 微调：趋势↑ / 摆动↓）。映射到 0~100，五档结论：≥75 强势看多｜60~74 偏多｜40~59 中性震荡｜25~39 偏空｜<25 弱势看空
- **走势预测**：近20/60日回归趋势（斜率年化+R²）、支撑/阻力位（含缠论中枢边界）、未来5~10个交易日展望与置信度（由信号共振度与拟合度决定）

## 数据来源与机制

| 用途 | 主源 | 备用链 |
|---|---|---|
| 实时快照 | 腾讯行情 | 东方财富 → 新浪 |
| 历史日K（前复权） | 腾讯行情 | 东方财富 → akshare |
| 名称搜索 | 东方财富 suggest | 腾讯 smartbox |
| 财务摘要 | 东方财富 F10 | 失败则降级为估值快照并提示 |

- 全部为公开、免注册的行情接口；单次分析仅 2~4 个请求，请求间隔 ≥300ms；
- **当日缓存**（`cache/`）：K线全天有效；实时快照盘中 10 分钟过期，收盘后当日有效；`--fresh` 绕过；
- 报告头部标注数据截止日期与（盘中/收盘）状态。

## 接口失效自查

数据源为非官方公开接口，可能变动。若查询失败：

1. 运行 `stock.bat 600519 --fresh`，看 stderr 中各源的失败原因（程序会逐源列出）；
2. 手动验证连通性：浏览器打开 `https://qt.gtimg.cn/q=sh600519` 应返回一行行情文本；
3. 全部 URL 与字段映射集中在 `stocklib/sources/*.py` 顶部，字段漂移时单点修改；
4. 修改后运行 `stock.bat --test` 确认离线断言仍全绿。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 网络失败（全部数据源不可用，附诊断） |
| 3 | 未找到 / 无效代码 / 不支持的市场 |
| 4 | 数据错误（返回但不可解析/不足） |
| 5 | 参数错误 |

## 开发

- 架构：`stocklib/sources/`（各源解析器）→ `datasource.py`（回退链/缓存/限流）→ `indicators.py`/`chanlun.py`/`analyzer.py`/`predictor.py`（纯函数分析层）→ `report.py`；
- 评分权重与阈值集中在 `stocklib/analyzer.py` 顶部常量，校准依据见 `PLAN.md` §7/§8；缠论实现口径（含全部理论放宽声明）见 `PLAN.md` §9；
- `tools/chan_backcheck.py`：缠论买卖点事后事件检验（确认日起算避免前视偏差），一次性验证工具，不进入主流程；
- 依赖锁定于 `requirements.lock`；升级依赖后必须重跑 `stock.bat --test`；
- 技术方案与 GitHub 开源项目调研（qlib/vnpy/akshare/tushare/myhhub-stock 等 10K+ 星项目的有效性判断）见 `PLAN.md`。

## 免责声明

本工具基于历史行情与公开数据的技术指标计算，输出为规则化打分与概率倾向，**不构成任何投资建议**。股市有风险，入市需谨慎。数据仅供个人研究使用。
