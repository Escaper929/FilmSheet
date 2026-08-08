#!/bin/bash
# FilmSheet API 启动脚本
# 用法: ./run.sh [端口]

PORT=${1:-8000}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🎞️  FilmSheet API 启动中..."
echo "📡  地址: http://localhost:$PORT"
echo "📱  iOS 访问: http://$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '192.168.x.x'):$PORT"
echo "🔴  按 Ctrl+C 停止"
echo ""

cd "$SCRIPT_DIR"
uvicorn main:app --host 0.0.0.0 --port "$PORT" --reload
