# Public HTML Publication Design

**Status:** Approved  
**Date:** 2026-07-15  
**Branch:** `codex/publish-generated-html`

## Context

DeerFlow already distinguishes three file flows: owned library files,
conversation uploads/generated artifacts, and files shared with a registered
recipient. Registered-user sharing is authenticated and recipient-scoped. The
new requirement is different: an owner must be able to publish a generated HTML
artifact at a stable URL that anyone can open without a DeerFlow account.

Published HTML must remain interactive. It must not execute as a same-origin
DeerFlow document because generated scripts could otherwise read application
cookies, storage, or parent-page DOM state.

## Goals

- Publish only owner-controlled `.html` or `.htm` artifacts from the locked
  Conversation Generated collection.
- Return one stable, permanent URL per owner/thread/path until publication is
  explicitly cancelled.
- Allow JavaScript and forms inside the published document.
- Keep public content in an opaque-origin iframe.
- Make repeat publication idempotent and expose copy/cancel actions after page
  reloads.
- Fail closed when the source file is removed or replaced.

## Non-goals

- Publishing library uploads, conversation uploads, or received shares.
- Hosting multi-file website bundles or rewriting relative asset URLs.
- Custom domains, analytics, passwords, expiry dates, or search indexing.
- Letting the published document call authenticated DeerFlow APIs.

## Chosen Architecture

Use a dedicated `file_publications` persistence model instead of overloading
registered-recipient file shares or thread shares. A publication stores its
owner, source thread, normalized generated-file path, current filesystem object
identity, opaque public token, and creation time. A uniqueness constraint on
owner/thread/path makes publication idempotent.

The owner API resolves the source through the existing conversation ownership
and virtual-path checks, then additionally requires an HTML extension. If the
same path now refers to a replacement filesystem object, an explicit publish
request refreshes the stored identity while retaining the stable token. Until
then, public access fails closed.

The external URL is `/published/{token}`. This public Next.js page fetches public
metadata and HTML text, then passes the text to an iframe with:

```html
sandbox="allow-scripts allow-forms"
referrerpolicy="no-referrer"
```

`allow-same-origin` is deliberately absent. The backend content endpoint serves
HTML bytes as `text/plain; charset=utf-8` with `X-Content-Type-Options: nosniff`;
the parent viewer injects them through `srcDoc`. Therefore public HTML can run
its own interactions but cannot inherit the DeerFlow origin.

## Persistence Model

`file_publications` contains:

- `id`: UUID primary key used by authenticated owner operations.
- `public_token`: unique, high-entropy URL token.
- `owner_user_id`: cascading foreign key to `users.id`.
- `thread_id`: required conversation identifier.
- `source_path`: normalized `/mnt/user-data/outputs/...` path.
- `source_identity`: device/inode identity of the explicitly published object.
- `created_at`: timezone-aware creation timestamp.

Unique constraints cover `public_token` and
`(owner_user_id, thread_id, source_path)`. Cancellation deletes the row; public
lookups then return 404. Source deletion or identity mismatch also returns 404
without revealing which condition occurred.

## API Contract

Authenticated owner endpoints:

- `POST /api/file-publications` with `thread_id` and `path`: validate ownership
  and HTML type, create or return the stable publication.
- `GET /api/file-publications`: list the current owner's publication records so
  the generated-files view can map them without N+1 requests.
- `DELETE /api/file-publications/{publication_id}`: owner-scoped cancellation.

Unauthenticated public endpoints:

- `GET /api/public-files/{token}`: return safe metadata (`name`, `content_url`).
- `GET /api/public-files/{token}/content`: return HTML source as non-sniffable
  plain text.

The creation/list response includes `id`, `name`, `thread_id`, `path`,
`public_token`, `public_url`, and `created_at`. The frontend constructs an
absolute URL from `window.location.origin` when copying it.

## Frontend Behavior

The Files page loads publications only while viewing Conversation Generated.
Generated HTML without a publication shows **Publish link**. Published HTML
shows **Copy public link** and **Cancel publication**. Publication and
cancellation invalidate the publication list query and report success/failure
through the existing toast patterns. Non-HTML files, uploads, library files,
and received shares never expose these actions.

The public viewer displays loading, not-found/unpublished, and success states.
It does not render account navigation or any authenticated workspace shell and
sets noindex/nofollow metadata.

## Error and Security Behavior

- Unauthenticated owner mutations return 401; cross-owner thread access is
  indistinguishable from a missing conversation.
- Invalid paths, non-output paths, and non-HTML extensions return 400.
- Missing/replaced source files return 404 for both owner reuse and public read.
- Database unavailability returns 503 only on authenticated owner operations;
  public failures remain generic.
- Tokens are never logged.
- The public endpoint never returns owner identity, thread title, internal path,
  or authenticated download URLs.

## Verification

Backend tests cover migration creation, owner isolation, path/type validation,
idempotent creation, replacement refresh, unauthenticated public access,
deletion/replacement failure, and cancellation. Frontend tests cover API
serialization, HTML-only action visibility, publish/copy/cancel state, public
viewer errors, and sandbox tokens without `allow-same-origin`.

Before delivery, run the migration tests, focused publication router tests,
complete frontend unit suite, TypeScript typecheck, ESLint, backend formatting
and lint checks, and a browser visual/interaction check of the public viewer.
