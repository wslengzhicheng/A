# A股分析工具 - IIS 部署配置指南

## 快速开始（推荐方法）

### 步骤1：启用 IIS 功能（管理员 PowerShell）

```powershell
# 以管理员身份运行此脚本
.\setup_iis.ps1
```

或手动执行：
```powershell
# 启用 IIS WebServer
Enable-WindowsOptionalFeature -Online -FeatureName IIS-WebServer -All

# 启用 ARR
Enable-WindowsOptionalFeature -Online -FeatureName IIS-ApplicationRequestRouting

# 启用 WebSockets
Enable-WindowsOptionalFeature -Online -FeatureName IIS-WebSockets
```

### 步骤2：配置 ARR 代理

1. 打开 **IIS 管理器**（Win+R 输入 `inetmgr`）
2. 选择服务器节点
3. 双击 **Application Request Routing**
4. 点击右侧 **Server Proxy Settings**
5. 配置：
   ```
   ✅ Enable proxy
   ✅ Enable SSL Offloading
   ✅ Reverse rewrite host in response headers
   ```
6. 点击 **Apply**

### 步骤3：创建网站

1. 右键 **Sites** → **Add Website**
2. 配置：
   - **网站名称**: `A股分析`
   - **物理路径**: `C:\Users\Allen.Leng\Desktop\A\A`
   - **类型**: `HTTP`
   - **IP地址**: `127.0.0.1`
   - **端口**: `80`
3. 点击 **OK**

### 步骤4：配置反向代理规则

1. 选择刚创建的网站
2. 双击 **URL Rewrite**
3. 点击 **Inbound Rules** → **Add Rule(s)**
4. 选择 **Reverse Proxy**
5. 输入：
   - **Inbound**: `http://127.0.0.1:8888`
   - ✅ Enable SSL Offloading
6. 点击 **OK**

### 步骤5：启动服务

```bash
# 双击运行或在管理员 PowerShell 中执行
iis_startup.bat
```

### 步骤6：配置自动启动（可选）

1. 打开 **任务计划程序**（`taskschd.msc`）
2. 创建任务：
   - 名称: `A股分析Web服务`
   - 触发器: 计算机启动时
   - 操作: 运行 `C:\Users\Allen.Leng\Desktop\A\A\iis_startup.bat`
   - 条件: 仅在以下网络连接可用时启动

### 步骤7：验证配置

```powershell
# 运行验证脚本
.\verify_iis.ps1
```

浏览器访问：
- 主页: `http://localhost/`
- 批量对比: `http://localhost/compare.html`
- 回测验证: `http://localhost/backtest.html`
- 市场概览: `http://localhost/market_overview.html`

---

## 故障排除

### 问题1：无法访问 localhost

检查 Python 服务是否运行：
```bash
# 查看进程
tasklist | findstr python
```

如果未运行，手动启动：
```bash
iis_startup.bat
```

### 问题2：ARR 未启用

运行：
```powershell
# 启用 ARR
Enable-WindowsOptionalFeature -Online -FeatureName IIS-ApplicationRequestRouting

# 重启 IIS
iisreset /restart
```

### 问题3：端口 80 被占用

检查端口占用：
```powershell
netstat -ano | findstr :80
```

更改网站端口（在 IIS 管理器中右键网站 → 编辑绑定）

### 问题4：Python 服务自动关闭

检查日志并调整启动脚本：
```batch
@echo off
chcp 65001 >nul
cd /d "%~dp0"

:loop
echo [%date% %time%] 服务运行中...
python\python.exe -X utf8 web.py
echo [%date% %time%] 服务异常退出，10秒后重启...
timeout /t 10 /nobreak >nul
goto loop
```

---

## 替代端口配置

如果 80 端口被占用，使用其他端口：

### 选项A：更改 IIS 网站端口
1. 右键网站 → 编辑绑定
2. 将端口改为 `8080`
3. 访问 `http://localhost:8080`

### 选项B：使用防火墙端口
```powershell
# 开放防火墙端口
New-NetFirewallRule -DisplayName "A股分析工具" -Direction Inbound -LocalPort 8888 -Protocol TCP -Action Allow
```

直接访问 Python 服务器：
- `http://localhost:8888`
- `http://your-ip:8888`

---

## 性能优化

### 配置 FastCGI（高级）

如果反向代理性能不足，使用 FastCGI：

1. 安装依赖：
```powershell
python\python.exe -m pip install wfastcgi pywin32
```

2. 启用 CGI：
```powershell
Enable-WindowsOptionalFeature -Online -FeatureName IIS-CGI
iisreset /restart
```

3. 配置处理程序映射：
- 请求路径: `*`
- 模块: `FastCgiModule`
- 可执行文件: `C:\Users\Allen.Leng\Desktop\A\A\python\python.exe`
- 名称: `Python FastCGI`

### SSL/HTTPS 配置

1. 在 IIS 中绑定 HTTPS
2. 确保已勾选 **Enable SSL Offloading** in ARR 设置
3. 访问 `https://localhost`

---

## 文件清单

创建的配置文件：
- `iis_startup.bat` - 服务启动脚本
- `setup_iis.ps1` - 一键启用 IIS 功能
- `verify_iis.ps1` - 配置验证脚本
- `IIS_SETUP.md` - 本配置文档

---

## 卸载配置

如需移除 IIS 配置：

```powershell
# 删除网站
Remove-Website -Name "A股分析"

# 禁用 ARR
Disable-WindowsOptionalFeature -Online -FeatureName IIS-ApplicationRequestRouting

# 重启 IIS
iisreset /restart
```

---

## 技术支持

- Python 服务端口: 8888
- IIS 默认端口: 80
- 日志位置: 标准输出（控制台窗口）
- 配置文件: `stocklib/`, `static/`

---

## 安全建议

1. 仅在内网使用，避免暴露到公网
2. 如需公网访问，配置防火墙规则
3. 定期检查 Python 服务运行状态
4. 备份配置文件和数据