@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
title AI TongShengChuanYi ZhuShou

echo ============================================================
echo   AI TongShengChuanYi ZhuShou
echo ============================================================
echo.

:: ========== Find Conda / Python ==========
set CONDA=
set CONDA_ROOT=
set SYSTEM_PYTHON=

:: 1) Find conda first
for %%d in (
    "%USERPROFILE%\anaconda3"
    "%USERPROFILE%\Anaconda3"
    "%USERPROFILE%\miniconda3"
    "%USERPROFILE%\Miniconda3"
    "F:\Anaconda3"
    "C:\ProgramData\Anaconda3"
    "C:\Anaconda3"
) do (
    if not defined CONDA (
        if exist "%%~d\Scripts\conda.exe" (
            set "CONDA=%%~d\Scripts\conda.exe"
            set "CONDA_ROOT=%%~d"
            echo [OK] Found Conda: %%~d
        )
    )
)

if defined CONDA (
    :: Create/Use conda env with Python 3.12
    call "!CONDA!" run -n ai_translator python --version >nul 2>&1
    if !ERRORLEVEL! NEQ 0 (
        echo.
        echo Creating Conda env - Python 3.12, please wait...
        echo.
        call "!CONDA!" create -n ai_translator python=3.12 -y -q
        if !ERRORLEVEL! NEQ 0 (
            echo [ERROR] Conda env creation failed
            pause
            exit /b 1
        )
        echo [OK] Conda env created
    ) else (
        echo [OK] Using Conda env: ai_translator
    )
    :: Find python in conda env (search multiple possible paths)
    for %%e in (
        "!CONDA_ROOT!\envs\ai_translator"
        "!USERPROFILE!\.conda\envs\ai_translator"
        "F:\Anaconda_envs\envs\ai_translator"
    ) do (
        if not defined PYTHON (
            if exist "%%~e\python.exe" set "PYTHON=%%~e\python.exe"
        )
    )
    if not defined PYTHON (
        echo [ERROR] Cannot find conda env python. Please report the path to ai_translator env.
        pause
        exit /b 1
    )
    echo [OK] Python: !PYTHON!
    goto :check_deps
)

:: 2) No conda - look for system Python
for %%d in (
    "%USERPROFILE%\anaconda3"
    "%USERPROFILE%\Anaconda3"
    "F:\Anaconda3"
) do (
    if not defined SYSTEM_PYTHON (
        if exist "%%~d\python.exe" set "SYSTEM_PYTHON=%%~d\python.exe"
    )
)
if not defined SYSTEM_PYTHON (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        if not defined SYSTEM_PYTHON set "SYSTEM_PYTHON=%%i"
    )
)
if not defined SYSTEM_PYTHON (
    echo [ERROR] Python not found. Install Python 3.12 or Anaconda.
    pause
    exit /b 1
)

echo [OK] Found Python: !SYSTEM_PYTHON!

:: Create venv
set "VENV=%CD%\.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating venv...
    "!SYSTEM_PYTHON!" -m venv "%VENV%" --clear
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] venv creation failed
        pause
        exit /b 1
    )
)
set "PYTHON=%VENV%\Scripts\python.exe"
echo [OK] Using project venv

:: ========== Dependency detection ==========
:check_deps
echo Checking dependencies status...
"!PYTHON!" -c "import faster_whisper, llama_cpp, PyQt5, google.genai, torchaudio" >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    echo [OK] Dependencies are already installed. Fast startup!
    goto :run
)
echo [INFO] Dependencies missing or incomplete. Starting installation...

:: ========== Install deps ==========
:install
echo.
echo Installing dependencies...

if defined CONDA (
    :: llama-cpp-python needs conda-forge (no pre-built pip wheels for Windows)
    echo [1/2] Installing llama-cpp-python via conda...
    call "!CONDA!" install -c conda-forge llama-cpp-python -n ai_translator -y -q
    if !ERRORLEVEL! NEQ 0 (
        echo [WARN] conda install failed, trying pip fallback...
    )
)

echo Installing packages via pip...
"!PYTHON!" -m pip install -q --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
"!PYTHON!" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo ============================================================
    echo [ERROR] Package install failed.
    echo.
    if not defined CONDA (
        echo llama-cpp-python requires a C++ compiler on Windows.
        echo You have two options:
        echo   1. Install Miniconda - recommended:
        echo      https://docs.anaconda.com/miniconda/install/
        echo      Only ~50MB, handles all compilation automatically.
        echo   2. Install Visual Studio Build Tools:
        echo      https://visualstudio.microsoft.com/downloads/
        echo      ~3GB, adds MSVC compiler to your system.
    ) else (
        echo Network error. Please check your connection and try again.
    )
    echo ============================================================
    pause
    exit /b 1
)
echo [OK] Dependencies ready
echo.

:: HuggingFace mirror
if not defined HF_ENDPOINT set "HF_ENDPOINT=https://hf-mirror.com"

:: ========== Run ==========
:run
echo ============================================================
echo.
"!PYTHON!" main.py

pause
