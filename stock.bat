@echo off
rem A-share analyzer launcher: bundled Python, force UTF-8 output
setlocal
set "ROOT=%~dp0"
"%ROOT%python\python.exe" -X utf8 "%ROOT%stock.py" %*
endlocal & exit /b %ERRORLEVEL%
