# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DeerFlow Frontend is a Next.js 16 web interface for an AI agent system. It communicates with a LangGraph-based backend to provide thread-based AI conversations with streaming responses, artifacts, and a skills/tools system.

**Stack**: Next.js 16, React 19, TypeScript 5.8, Tailwind CSS 4, pnpm 10.26.2

## Commands

| Command          | Purpose                                                       |
| ---------------- | ------------------------------------------------------------- |
| `pnpm dev`       | Dev server with Webpack (http://localhost:3000, lower memory) |
| `pnpm dev:turbo` | Dev server with Turbopack (faster HMR, high memory on macOS)  |
| `pnpm build`     | Production build                                              |
| `pnpm check`     | Lint + type check (run before committing)                     |
| `pnpm lint`      | ESLint only                                                   |
| `pnpm lint:fix`  | ESLint with auto-fix                                          |
| `pnpm test`      | Run unit tests with Vitest                                    |
| `pnpm test:e2e`  | Run E2E tests with Playwright (Chromium)                      |
| `pnpm typecheck` | TypeScript type check (`tsc --noEmit`)                        |
| `pnpm start`     | Start production server                                       |

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
  - `published-agents/` — Owner control-plane types, authenticated REST client,
    TanStack Query keys/hooks, and dedicated revision/quota error types
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
- **Conversation-generated files** are discovered by searching recent threads for titles, then loading each latest thread state for `artifacts`; thread search responses do not contain checkpoint artifacts
- **Shared HTML previews** use `allow-scripts allow-forms` without `allow-same-origin`, preserving interaction while isolating app cookies, storage, and the parent DOM
- **Environment validation** uses `@t3-oss/env-nextjs` with Zod schemas (`src/env.js`). Skip with `SKIP_ENV_VALIDATION=1`
- **Published-Agent authoring** uses `/workspace/agents` for the owner Gallery
  and `/workspace/agents/[agent_id]` for Studio. The existing dynamic segment is
  named `[agent_name]` because its nested chat route predates Studio; the
  Studio page treats the same segment as stable `agent_id`, while sandbox chat
  links use the Agent slug. Draft saves always carry `revision`, and a 409 must
  remain an explicit reload decision rather than silently overwriting. Publish
  previews read only the saved draft; unsaved editor state disables publish.
  Tool groups are platform-managed: Studio does not expose or submit them,
  database draft/sandbox runs use the platform policy, and each new Release
  snapshots the current platform group set.
  The instruction editor keeps two documents with distinct responsibilities.
  `AGENT.md` is an editable Work rules document; empty drafts are initialized
  client-side from a localized template covering role, responsibilities,
  workflow, boundaries, and output requirements. `SOUL.md` has no free-text
  editor and is generated from a managed personality preset. Legacy non-empty
  custom `SOUL.md` remains unchanged and read-only until the owner explicitly
  selects a preset. Existing Release snapshots remain unchanged until publish.
  Keep preset detection and initialization pure and idempotent in
  `core/published-agents/instructions.ts`.
  The shared Skill picker prefers `display_name` / `description_zh` in the
  Chinese locale while retaining `skill_name` as the technical identifier; its
  search matches localized names, both descriptions, identifiers, and declared
  Connector capabilities without changing the owner-authorized option set.
  Agent-backed Thread metadata carries `agent_name` and may carry
  `agent_display_name`; chat-history titles use `agentNameOfThread()` for
  routing and render `ThreadAgentBadge` when either identity is present.
  Pre-identity sandbox Threads fall back from `draft_sandbox_agent_id` to the
  conditionally loaded owner Published-Agent list.
  The custom-Agent chat header intentionally has no duplicate `New chat`
  button beside `TokenUsageIndicator`.
  API Key plaintext comes only from the create response. Keep it in
  `ApiKeysPanel` memory so the newly created row remains copyable after its
  reveal dialog closes, then discard it on page unmount/reload; never persist it
  or imply that an existing hashed Key can be recovered. The create dialog asks
  only for a name; Key metadata and quotas are not editable in Studio. The Key
  list exposes session-only copy and permanent delete (including legacy revoked
  rows), but
  no rotate/revoke actions.
  Operations queries use source/Key filters in
  their Query keys, quota blanks mean bounded inheritance, and audit components
  render only the backend's metadata allowlist. Stable Agent API curl examples
  use `{"message":"..."}` (not LangGraph `input.messages`). The final M4 UI
  gate is `tests/e2e/multi-tenant-acceptance.spec.ts` and must retain coverage
  for instruction combinations, draft/live isolation, publish-before-Key, and
  late Feishu binding without republish.

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
