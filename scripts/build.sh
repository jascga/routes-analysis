#!/usr/bin/env bash
# Linux/macOS 开发用打包脚本（仅供测试，不能在 Windows 上运行）
# Windows 用户请使用 scripts/build.bat

set -e
echo "=== routesanalysis 打包（仅开发测试用）==="
echo "[信息] Python: $(python3 --version)"

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]" --quiet
pip install pyinstaller --quiet

rm -rf build dist

pyinstaller \
    --clean \
    --noconfirm \
    scripts/routesanalysis.spec

echo ""
echo "=== 完成 ==="
echo "可执行文件: dist/routesanalysis"
echo "（注：Linux/macOS 打的二进制不能在 Windows 上运行）"
