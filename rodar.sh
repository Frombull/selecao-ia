#!/bin/zsh
cd "$(dirname "$0")" && [ -f .env ] && source .env
exec .venv/bin/python selecao_ia.py
