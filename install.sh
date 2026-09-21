#!/usr/bin/env bash
set -e

if ! command -v python3 &> /dev/null; then
    echo "Python 3 no encontrado. Instálalo antes de continuar."
    exit 1
fi

python3 install.py
