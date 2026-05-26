export type UserFileSource = "uploaded" | "generated";
export type UserFileKind = "file" | "folder";
export type UserFileTypeFilter =
  | "all"
  | "folder"
  | "document"
  | "image"
  | "audio"
  | "other";

export interface UserFileItem {
  id: string;
  name: string;
  path: string;
  kind: UserFileKind;
  source: UserFileSource | null;
  size: number;
  mime_type: string | null;
  extension: string;
  modified_at: string;
  preview_url: string | null;
  download_url: string | null;
}

export interface UserFileListResponse {
  folder_path: string;
  items: UserFileItem[];
  total: number;
}

