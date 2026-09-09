@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python
echo 需要 Python 3.10 或更新版本。安装后重新运行本文件。
pause
exit /b 1
:use_py
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto version_error
py -3 -B service.py start %*
if errorlevel 1 pause
exit /b
:use_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto version_error
python -B service.py start %*
if errorlevel 1 pause
exit /b
:version_error
echo 检测到的 Python 版本低于 3.10，或 Python 无法启动。
pause
exit /b 1
