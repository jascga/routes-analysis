@echo off
REM ============================================================
REM routesanalysis Windows 打包脚本
REM 在 Windows 上、已安装 Python 3.9+ 后，双击或在 cmd 中运行
REM 输出: dist\routesanalysis.exe
REM ============================================================

setlocal enabledelayedexpansion
chcp 65001 > nul

echo.
echo === routesanalysis Windows 打包 ===
echo.

REM 1) 检查 Python
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 python，请先安装 Python 3.9+ 并加入 PATH
    echo        下载地址: https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [信息] Python 版本: %PYVER%

REM 2) 创建/使用虚拟环境
if not exist ".venv" (
    echo [信息] 创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

REM 3) 安装依赖
echo [信息] 安装项目依赖 ...
python -m pip install --upgrade pip --quiet
python -m pip install -e ".[dev]" --quiet
python -m pip install pyinstaller --quiet
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

REM 4) 清理旧产物
if exist "build"   rmdir /s /q "build"
if exist "dist"    rmdir /s /q "dist"

REM 5) 打包
echo.
echo [信息] 开始打包 PyInstaller --onefile ...
echo        （首次打包会较慢，可能需要 1-3 分钟）
echo.

pyinstaller ^
    --clean ^
    --noconfirm ^
    scripts\routesanalysis.spec

if errorlevel 1 (
    echo.
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo ============================================================
echo [完成] 可执行文件: dist\routesanalysis.exe
echo.
echo 使用方法:
echo   routesanalysis.exe multi-group device.txt -o report.xlsx
echo   routesanalysis.exe --help
echo ============================================================
echo.

pause
endlocal
