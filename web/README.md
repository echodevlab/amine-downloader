# amine-downloader Web UI

React + Vite + TypeScript + Tailwind v4 + shadcn/ui。使用 [bun](https://bun.sh/) 管理。

## 开发

```bash
bun install
bun run dev      # http://localhost:5173，/api 会代理到 127.0.0.1:8420
```

后端另开一个终端：

```bash
uv run amine-downloader serve --port 8420
```

## 构建

```bash
bun run build    # 产物在 dist/，由 `amine-downloader serve` 直接托管
```

## 添加组件

```bash
bunx --bun shadcn@latest add <component>
```
