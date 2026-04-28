#!/usr/bin/env bash
# CLAUDE.md 第 1 條地雷防護：next dev (port 3000) 跑著的時候別跑 npm run build。
# 兩者共用 web/.next/，並行寫會互相覆蓋，dev server 立刻 500。
#
# Hook 行為：當 Bash 工具收到含 `npm run build` 或 `next build` 的指令時，
# 先用 netstat 檢查 port 3000 是否在 LISTENING；若在 → 拒絕並提示先跑 stop.bat。
# 若 dev 沒跑（cold build / CI 場景）→ 放行。
#
# Hook 收到 stdin 是 JSON：{ "tool_name": "Bash", "tool_input": { "command": "...", "description": "..." } }
# 退出碼 0 = 放行；2 = blocked（並印 stderr 給模型看）。

set -euo pipefail

input=$(cat)

# 只處理 Bash tool
tool_name=$(echo "$input" | python -c "import sys, json; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null || echo "")
if [ "$tool_name" != "Bash" ]; then
  exit 0
fi

cmd=$(echo "$input" | python -c "import sys, json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")

# 偵測 build 指令（涵蓋 npm run build / next build / yarn build / pnpm build）
if echo "$cmd" | grep -qE '(npm|yarn|pnpm)[[:space:]]+(run[[:space:]]+)?build|next[[:space:]]+build'; then
  # 檢查 port 3000 是否 LISTENING
  if netstat -ano 2>/dev/null | grep -E ":3000[[:space:]]+.*LISTENING" >/dev/null; then
    cat >&2 <<EOF
[dev-build-guard] 阻止：next dev 正在 port 3000 執行中。
原因：next dev 與 next build 共用 web/.next/，並行寫入會把 dev server 弄爆 (Cannot find module './XXX.js' / 500 errors)。
修法：先跑 stop.bat 收掉 dev server，build 完再 launch.bat 起回去。
詳見 docs/operations.md「前端 dev / build 工序」與 CLAUDE.md 第 1 條地雷。
EOF
    exit 2
  fi
fi

exit 0
