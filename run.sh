#!/bin/bash
# agent_claude 実行ラッパー
# APIキーは ~/.config/ai-keys/.env で一元管理する（このファイルには書かない）

LOG=/Users/fk/Logs/agent_claude.log
ENV_FILE="$HOME/.config/ai-keys/.env"

# APIキーを読み込む（.env の各行を export する）
if [ -f "$ENV_FILE" ]; then
    set -a
    . "$ENV_FILE"
    set +a
else
    echo "$(date): ENV file not found: $ENV_FILE" >> "$LOG"
    exit 1
fi

# Ollamaが起動中か確認
if ! curl -s http://localhost:11434 > /dev/null 2>&1; then
    echo "$(date): Ollama not running, skipping." >> "$LOG"
    exit 0
fi

echo "$(date): Running agent..." >> "$LOG"
python3 /Users/fk/ai-agent/moltbook/agent_claude.py >> "$LOG" 2>&1
