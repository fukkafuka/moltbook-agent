#!/bin/bash


export GROQ_API_KEY="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"
export GEMINI_API_KEY="AIzaSyD3MJmPXz_TQwlBZe--PQ1-WxPX77mDjQo"

# Ollamaが起動中か確認
if ! curl -s http://localhost:11434 > /dev/null 2>&1; then
    echo "$(date): Ollama not running, skipping." >> /Users/fk/ai-agent/logs/agent_claude.log
    exit 0
fi


echo "$(date): Running agent..." >> /Users/fk/ai-agent/logs/agent_claude.log
python3 /Users/fk/ai-agent/moltbook/agent_claude.py >> /Users/fk/ai-agent/logs/agent_claude.log 2>&1
