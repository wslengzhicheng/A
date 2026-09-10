@echo off
rem A-share analyzer Web UI launcher
setlocal
set "ROOT=%~dp0"
echo 正在启动 A股分析 Web 界面...
start http://localhost:8888
"%ROOT%python\python.exe" -X utf8 "%ROOT%web.py" %*
endlocal & exit /b %ERRORLEVEL%
