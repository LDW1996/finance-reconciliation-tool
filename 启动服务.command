#!/bin/zsh
# 双击启动本地服务（普通终端环境，不经 WorkBuddy 沙箱）
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python backend/launcher.py
