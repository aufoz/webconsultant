#!/bin/bash
set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║        WebConsultant — запуск            ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Install deps
echo "▶ Установка зависимостей..."
pip install -r requirements.txt -q --break-system-packages

echo ""
echo "▶ Запуск сервера на http://localhost:8000"
echo ""
echo "  Режим AI:"
echo "  • С Ollama (llama3.2): полноценный AI-ответ"
echo "  • Без Ollama: keyword-поиск по базе"
echo ""
echo "  Для установки Ollama (опционально):"
echo "  curl https://ollama.ai/install.sh | sh"
echo "  ollama pull llama3.2"
echo ""
echo "─────────────────────────────────────────────"

cd "$(dirname "$0")"
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
