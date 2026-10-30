#!/usr/bin/env bash
set -e

echo "=== AskViolet Linux Binary Builder ==="

# Check dependencies
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is required. Please install python3 and python3-tk."
    exit 1
fi

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pip install TKinterModernThemes pyinstaller

python3 build_exe.py

echo "Linux build finished in dist/AskViolet/"
