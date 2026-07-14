// ----------------------------------------------------------------------------
// File-library types
// ----------------------------------------------------------------------------
//
// Shape mirrors the backend `FileItem` model in
// `backend/app/gateway/routers/files.py`. Keep them in sync — the backend is
// the source of truth and this file is the TS projection.

export type FileSource = "uploaded" | "generated";

export type ConversationFileSource = FileSource;

export type SystemFileFolder = ConversationFileSource | "shared";

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
   * Set when this record belongs to a conversation, either as an uploaded
   * attachment or a generated artifact. The files page uses this field to
   * render the source conversation and route open/download calls correctly.
   *
   * `undefined` / absent for library files.
   */
  source_thread_id?: string;
  /**
   * Optional human-readable title for the source thread. The hook looks it up
   * from `useThreads`; the UI falls back to the thread id when unavailable.
   */
  source_thread_title?: string;
  /** Identifies a file that belongs to a locked conversation collection. */
  conversation_source?: ConversationFileSource;
  /** Set only on a synthetic, read-only system folder at the root. */
  system_folder?: SystemFileFolder;
  /** Present only on a file shared with the current user. */
  shared_file_id?: string;
  /** Registered account email of the user who shared this file. */
  shared_by_email?: string;
  /** ISO 8601 timestamp for when the share was created. */
  shared_at?: string;
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
  /**
   * Present when the referenced file comes from a chat upload instead of the
   * user document library. The backend uses this to resolve the file from that
   * thread's uploads directory.
   */
  source_thread_id?: string;
  source_thread_title?: string;
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
