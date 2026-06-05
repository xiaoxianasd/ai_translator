@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
title AI 同声传译助手

echo ============================================================
echo   AI 同声传译助手
echo ============================================================
echo.

:: ========== 查找 Python ==========
set PYTHON=
set VENV=%CD%\.venv

:: 1) 优先用项目自带的虚拟环境
if exist "%VENV%\Scripts\python.exe" (
    set PYTHON=%VENV%\Scripts\python.exe
    echo [OK] 使用项目虚拟环境
    goto :check
)

:: 2) 优先用 Anaconda / Miniconda
for %%d in (
    "%USERPROFILE%\Anaconda3"
    "%USERPROFILE%\anaconda3"
    "%USERPROFILE%\Miniconda3"
    "%USERPROFILE%\miniconda3"
    "F:\Anaconda3"
    "C:\ProgramData\Anaconda3"
    "C:\Anaconda3"
) do (
    if exist "%%~d\python.exe" (
        set SYSTEM_PYTHON=%%~d\python.exe
        echo [OK] 找到 Anaconda: !SYSTEM_PYTHON!
        goto :setup_venv
    )
)

:: 3) 再找系统安装的 Python
where python >nul 2>&1
if %ERRORLEVEL%==0 (
    for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)"') do set SYSTEM_PYTHON=%%i
    echo [OK] 找到 Python: !SYSTEM_PYTHON!
    goto :setup_venv
)

:: 4) 找 Microsoft Store 安装的 Python
where python3 >nul 2>&1
if %ERRORLEVEL%==0 (
    set SYSTEM_PYTHON=python3
    echo [OK] 找到 Python3
    goto :setup_venv
)

:: 5) 都没找到
echo [ERROR] 未找到 Python，请先安装 Python 3.10+:
echo   https://www.python.org/downloads/
echo   安装时请勾选 "Add Python to PATH"
pause
exit /b 1

:: ========== 创建虚拟环境并安装依赖 ==========
:setup_venv
echo.
echo 正在创建虚拟环境...
"!SYSTEM_PYTHON!" -m venv "%VENV%" --clear
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] 虚拟环境创建失败
    pause
    exit /b 1
)
set PYTHON=%VENV%\Scripts\python.exe

echo.
echo 正在安装依赖包（首次约需 3-10 分钟，请耐心等待）...
echo.
"%PYTHON%" -m pip install -q --upgrade pip
"%PYTHON%" -m pip install -q -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [WARN] 部分依赖安装失败，尝试继续...
    echo 如果无法运行，请手动执行: pip install -r requirements.txt
)

echo.
echo 依赖安装完成！
echo.

:: ========== 检查并下载模型 ==========
:check
echo 正在检查模型文件...
echo.

:: ========== 启动 ==========
"%PYTHON%" main.py

pause
