#!/usr/bin/env bash
# 运行子图文件助手 agent（交互式）
cd "$(dirname "$0")"
mkdir -p /tmp/opencode/pi_work
[ -f /tmp/opencode/pi_work/welcome.txt ] || echo "工作目录初始化" > /tmp/opencode/pi_work/welcome.txt
export DEEPSEEK_API_KEY_FILE="${DEEPSEEK_API_KEY_FILE:-$HOME/sys_prog/deepseek_api.txt}"
exec /home/terenliu/vibe_coding/langraph_skills/.conda/bin/python -m langgraph_skills run pi_agent.md
