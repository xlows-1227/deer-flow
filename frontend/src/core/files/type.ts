// ----------------------------------------------------------------------------
// File-library types
// ----------------------------------------------------------------------------
//
// Shape mirrors the backend `FileItem` model in
// `backend/app/gateway/routers/files.py`. Keep them in sync — the backend is
// the source of truth and this file is the TS projection.

export type FileSource = "uploaded" | "generated";

export type FileItemKind = "file" | "folder";

export interface FileItem {
  id: string;
  name: string;
  /** POSIX-style path relative to the user document library root. */
  path: string;
  kind: FileItemKind;
  source: FileSource | null;
  size: number;
  mime_type: string | null;
  extension: string;
  /** ISO 8601 timestamp string. */
  modified_at: string;
  preview_url: string | null;
  download_url: string | null;
  /**
   * Set when this record is a thread upload (a file the user attached to a
   * chat turn via the paperclip / drag-drop), NOT a library file. The
   * `useAllUserFiles` hook stitches library files and per-thread uploads
   * into a single list for the file-management page; this field is the
   * switch the page uses to render the source label and route
   * open/delete calls to the right endpoint.
   *
   * `undefined` / absent for library files.
   */
  source_thread_id?: string;
  /**
   * Optional human-readable title for the source thread (only set on
   * thread uploads). The hook looks it up from `useThreads`; falls back
   * to the thread id when no title is available.
   */
  source_thread_title?: string;
}

export interface FileListResponse {
  folder_path: string;
  items: FileItem[];
  total: number;
}

/**
 * Lightweight reference to a file the user has attached to a message via the
 * `@`-mention picker. This is *not* the full library record — just enough
 * metadata to render a chip in the input and to ship the reference to the
 * backend on submit.
 */
export interface ReferencedFile {
  id: string;
  name: string;
  path: string;
  mime_type: string | null;
  extension: string;
  size: number;
}

// ----------------------------------------------------------------------------
// Aliases used by the standalone `/workspace/files` management page.
// ----------------------------------------------------------------------------
//
// `UserFileItem` and `UserFileTypeFilter` are the names the files-page already
// imports. We re-export them here so the page and the @-mention picker share
// the same underlying model without the page having to be renamed.

/** @deprecated Prefer `FileItem` for new code. */
export type UserFileItem = FileItem;

export type UserFileTypeFilter =
  | "all"
  | "folder"
  | "document"
  | "image"
  | "audio"
  | "other";
