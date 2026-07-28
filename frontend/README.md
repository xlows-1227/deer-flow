# DeerFlow Frontend

Like the original DeerFlow 1.0, we would love to give the community a minimalistic and easy-to-use web interface with a more modern and flexible architecture.

## Tech Stack

- **Framework**: [Next.js 16](https://nextjs.org/) with [App Router](https://nextjs.org/docs/app)
- **UI**: [React 19](https://react.dev/), [Tailwind CSS 4](https://tailwindcss.com/), [Shadcn UI](https://ui.shadcn.com/), [MagicUI](https://magicui.design/) and [React Bits](https://reactbits.dev/)
- **AI Integration**: [LangGraph SDK](https://www.npmjs.com/package/@langchain/langgraph-sdk) and [Vercel AI Elements](https://vercel.com/ai-sdk/ai-elements)

## Quick Start

### Prerequisites

- Node.js 22+
- pnpm 10.26.2+

### Installation

```bash
# Install dependencies
pnpm install

# Copy environment variables
cp .env.example .env
# Edit .env with your configuration
```

### Development

```bash
# Start development server
pnpm dev

# The app will be available at http://localhost:3000
```

### Build & Test

```bash
# Type check
pnpm typecheck

# Check formatting
pnpm format

# Apply formatting
pnpm format:write

# Lint
pnpm lint

# Run unit tests
pnpm test

# One-time setup: install Playwright Chromium browser
pnpm exec playwright install chromium

# Run E2E tests (builds and starts production server automatically)
pnpm test:e2e

# Build for production
pnpm build

# Start production server
pnpm start
```

## Site Map

```
├── /                    # Landing page
├── /chats               # Chat list
├── /chats/new           # New chat page
├── /chats/[thread_id]   # A specific chat page
├── /workspace/agents    # Published-Agent owner control console
└── /workspace/agents/[agent_id] # Agent Studio draft editor
```

## Configuration

### Environment Variables

Key environment variables (see `.env.example` for full list):

```bash
# Backend API URL (optional, uses local Next.js/nginx proxy by default)
NEXT_PUBLIC_BACKEND_BASE_URL="http://localhost:8001"
# LangGraph-compatible API URL (optional, uses local Next.js/nginx proxy by default)
NEXT_PUBLIC_LANGGRAPH_BASE_URL="http://localhost:8001/api"
```

## Project Structure

```
tests/
├── e2e/                    # E2E tests (Playwright, Chromium, mocked backend)
└── unit/                   # Unit tests (mirrors src/ layout)
src/
├── app/                    # Next.js App Router pages
│   ├── api/                # API routes
│   ├── workspace/          # Main workspace pages
│   └── mock/               # Mock/demo pages
├── components/             # React components
│   ├── ui/                 # Reusable UI components
│   ├── workspace/          # Workspace-specific components
│   ├── landing/            # Landing page components
│   └── ai-elements/        # AI-related UI elements
├── core/                   # Core business logic
│   ├── api/                # API client & data fetching
│   ├── artifacts/          # Artifact management
│   ├── config/              # App configuration
│   ├── i18n/               # Internationalization
│   ├── mcp/                # MCP integration
│   ├── messages/           # Message handling
│   ├── models/             # Data models & types
│   ├── published-agents/   # Publishing control-plane API, types, and Query hooks
│   ├── settings/           # User settings
│   ├── skills/             # Skills system
│   ├── threads/            # Thread management
│   ├── todos/              # Todo system
│   └── utils/              # Utility functions
├── hooks/                  # Custom React hooks
├── lib/                    # Shared libraries & utilities
├── server/                 # Server-side code
│   └── better-auth/        # Authentication setup and session helpers
└── styles/                 # Global styles
```

The Published-Agent owner console combines immutable Release, API Key,
Feishu binding, health, and seven-day usage summaries without exposing a
public marketplace. Agent Studio edits `AGENT.md` and `SOUL.md` as a
revisioned backend draft with separate responsibilities: **Work rules**
`AGENT.md` is user-editable and empty drafts receive a localized template for
role, responsibilities, workflow, boundaries, and output requirements;
`SOUL.md` is system-generated from a managed personality preset and has no
free-text editor. Existing custom `SOUL.md` content remains read-only until the
owner explicitly replaces it with a preset. The live Release remains unchanged
until publish. Studio
filters public/private Skills through owner-authorized options, displays
localized Skill names and descriptions when available, and searches across
both localized and technical catalog metadata. It connects Skill requirements
to capability-level Connector grants,
and launches explicitly non-live draft test chats. Its owner-only publishing
area previews saved-draft changes, localizes publish violations, compares
immutable Releases, and rolls back without changing API paths, Keys, bindings,
or conversation identity. Post-publish panels provide one-time Key reveal,
stable sync/SSE/async examples, Feishu lifecycle health, filtered daily
usage/error charts, bounded quota inheritance, and metadata-only rejection
events. Custom-Agent conversations retain their Agent slug and display name in
Thread metadata; the recent-chat sidebar and full chat history show a compact
Agent icon/name badge beside the generated conversation title. Older sandbox
Threads that only contain `draft_sandbox_agent_id` resolve the same identity
from the owner Agent list. The custom-Agent chat header keeps usage and
artifact controls but intentionally omits a duplicate "New chat" action beside
the token indicator. The final Playwright acceptance gate verifies all
supported instruction
combinations, draft/live separation, and adding Agent Keys or Feishu bindings
after the first publish. Generated public API examples use the stable
`{"message":"..."}` request body; they never expose a Release identifier.

## Scripts

| Command             | Description                                          |
| ------------------- | ---------------------------------------------------- |
| `pnpm dev`          | Start development server with Webpack (lower memory) |
| `pnpm dev:turbo`    | Start development server with Turbopack (faster HMR) |
| `pnpm build`        | Build for production                                 |
| `pnpm start`        | Start production server                              |
| `pnpm test`         | Run unit tests with Vitest                           |
| `pnpm test:e2e`     | Run E2E tests with Playwright                        |
| `pnpm format`       | Check formatting with Prettier                       |
| `pnpm format:write` | Apply formatting with Prettier                       |
| `pnpm lint`         | Run ESLint                                           |
| `pnpm lint:fix`     | Fix ESLint issues                                    |
| `pnpm typecheck`    | Run TypeScript type checking                         |
| `pnpm check`        | Run both lint and typecheck                          |

## Development Notes

- Uses pnpm workspaces (see `packageManager` in package.json)
- Webpack is the default dev bundler; use `pnpm dev:turbo` when you need faster HMR
- Environment validation can be skipped with `SKIP_ENV_VALIDATION=1` (useful for Docker)
- Backend API URLs are optional; nginx proxy is used by default in development

## License

MIT License. See [LICENSE](../LICENSE) for details.
