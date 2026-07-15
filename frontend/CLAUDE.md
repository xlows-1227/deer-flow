# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DeerFlow Frontend is a Next.js 16 web interface for an AI agent system. It communicates with a LangGraph-based backend to provide thread-based AI conversations with streaming responses, artifacts, and a skills/tools system.

**Stack**: Next.js 16, React 19, TypeScript 5.8, Tailwind CSS 4, pnpm 10.26.2

## Commands

| Command          | Purpose                                           |
| ---------------- | ------------------------------------------------- |
| `pnpm dev`       | Dev server with Webpack (http://localhost:3000, lower memory) |
| `pnpm dev:turbo` | Dev server with Turbopack (faster HMR, high memory on macOS) |
| `pnpm build`     | Production build                                  |
| `pnpm check`     | Lint + type check (run before committing)         |
| `pnpm lint`      | ESLint only                                       |
| `pnpm lint:fix`  | ESLint with auto-fix                              |
| `pnpm test`      | Run unit tests with Vitest                        |
| `pnpm test:e2e`  | Run E2E tests with Playwright (Chromium)          |
| `pnpm typecheck` | TypeScript type check (`tsc --noEmit`)            |
| `pnpm start`     | Start production server                           |

Unit tests live under `tests/unit/` and mirror the `src/` layout (e.g., `tests/unit/core/api/stream-mode.test.ts` tests `src/core/api/stream-mode.ts`). Powered by Vitest; import source modules via the `@/` path alias.

E2E tests live under `tests/e2e/` and use Playwright with Chromium. They mock all backend APIs via `page.route()` network interception and test real page interactions (navigation, chat input, streaming responses). Config: `playwright.config.ts`.

## Architecture

```
Frontend (Next.js) ──▶ LangGraph SDK ──▶ LangGraph Backend (lead_agent)
                                              ├── Sub-Agents
                                              └── Tools & Skills
```

The frontend is a stateful chat application. Users create **threads** (conversations), send messages, and receive streamed AI responses. The backend orchestrates agents that can produce **artifacts** (files/code) and **todos**.

### Source Layout (`src/`)

- **`app/`** — Next.js App Router. Routes: `/` (landing), `/workspace/chats/[thread_id]` (chat).
- **`components/`** — React components split into:
  - `ui/` — Shadcn UI primitives (auto-generated, ESLint-ignored)
  - `ai-elements/` — Vercel AI SDK elements (auto-generated, ESLint-ignored)
  - `workspace/` — Chat page components (messages, artifacts, settings)
  - `landing/` — Landing page sections
- **`core/`** — Business logic, the heart of the app:
  - `threads/` — Thread creation, streaming, state management (hooks + types)
  - `api/` — LangGraph client singleton
  - `artifacts/` — Artifact loading and caching
  - `i18n/` — Internationalization (en-US, zh-CN)
  - `settings/` — User preferences in localStorage
  - `memory/` — Persistent user memory system
  - `skills/` — Skills installation and management
  - `messages/` — Message processing and transformation
  - `mcp/` — Model Context Protocol integration
  - `models/` — TypeScript types and data models
- **`hooks/`** — Shared React hooks
- **`lib/`** — Utilities (`cn()` from clsx + tailwind-merge)
- **`server/`** — Server-side code (better-auth, not yet active)
- **`styles/`** — Global CSS with Tailwind v4 `@import` syntax and CSS variables for theming

### Data Flow

1. User input → thread hooks (`core/threads/hooks.ts`) → LangGraph SDK streaming
2. Stream events update thread state (messages, artifacts, todos)
3. TanStack Query manages server state; localStorage stores user settings
4. Components subscribe to thread state and render updates

### Key Patterns

- **Server Components by default**, `"use client"` only for interactive components
- **Thread hooks** (`useThreadStream`, `useSubmitThread`, `useThreads`) are the primary API interface
- **LangGraph client** is a singleton obtained via `getAPIClient()` in `core/api/`
- **对话生成文件发现**：先搜索近期对话及标题，再读取每个对话的最新状态取得 `artifacts`；对话搜索响应本身不包含 checkpoint 中的产物列表
- **共享 HTML 预览**：使用包含 `allow-scripts allow-forms`、但不包含 `allow-same-origin` 的 iframe，在保留交互能力的同时隔离应用 Cookie、本地存储和父页面 DOM
- **HTML 永久外链**：只有“对话生成”中的 `.html` 和 `.htm` 文件显示发布操作；文件页通过 `['files', 'publications']` 查询一次映射发布状态。公开路由 `/published/[token]` 不依赖登录态，先以纯文本加载 HTML，再通过 `srcDoc` 注入同样的不透明源沙箱；页面必须设置 `noindex`，并为失效 token 显示独立错误状态
- **Environment validation** uses `@t3-oss/env-nextjs` with Zod schemas (`src/env.js`). Skip with `SKIP_ENV_VALIDATION=1`

## Code Style

- **Imports**: Enforced ordering (builtin → external → internal → parent → sibling), alphabetized, newlines between groups. Use inline type imports: `import { type Foo }`.
- **Unused variables**: Prefix with `_`.
- **Class names**: Use `cn()` from `@/lib/utils` for conditional Tailwind classes.
- **Path alias**: `@/*` maps to `src/*`.
- **Components**: `ui/` and `ai-elements/` are generated from registries (Shadcn, MagicUI, React Bits, Vercel AI SDK) — don't manually edit these.

## Environment

Backend API URLs are optional; an nginx proxy is used by default:

```
NEXT_PUBLIC_BACKEND_BASE_URL=http://localhost:8001
NEXT_PUBLIC_LANGGRAPH_BASE_URL=http://localhost:2024
```

Requires Node.js 22+ and pnpm 10.26.2+.
