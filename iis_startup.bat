@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [IIS] 启动A股分析Web服务...
python\python.exe -X utf8 web.py
pause