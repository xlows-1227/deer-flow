"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { fetch } from "@/core/api/fetcher";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

type LogSource = "backend" | "frontend";

type LogViewResponse = {
  source: string;
  path: string;
  truncated: boolean;
  size: number;
  content: string;
};

function formatSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), units.length - 1);
  return `${(bytes / Math.pow(k, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function LogViewerSettingsPage() {
  const { t } = useI18n();
  const [source, setSource] = useState<LogSource>("backend");
  const [data, setData] = useState<LogViewResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>("");

  const loadLog = useCallback(
    async (selected: LogSource) => {
      setLoading(true);
      setError("");
      try {
        const res = await fetch(`/api/admin/logs?source=${selected}`);
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          const detail =
            (body && (body.detail || body.message)) ||
            t.settings.logViewer.loadError;
          setError(typeof detail === "string" ? detail : t.settings.logViewer.loadError);
          return;
        }
        const payload = (await res.json()) as LogViewResponse;
        setData(payload);
      } catch {
        setError(t.settings.logViewer.loadError);
      } finally {
        setLoading(false);
      }
    },
    [t.settings.logViewer.loadError],
  );

  useEffect(() => {
    void loadLog(source);
  }, [source, loadLog]);

  const content = data?.content ?? "";
  const isEmpty = !content && !loading && !error;

  return (
    <div className="space-y-8">
      <SettingsSection
        title={t.settings.logViewer.title}
        description={t.settings.logViewer.description}
      >
        <div className="mb-3 flex items-center gap-3">
          <ToggleGroup
            type="single"
            value={source}
            onValueChange={(value) => {
              if (value) {
                setSource(value as LogSource);
              }
            }}
            variant="outline"
            size="sm"
          >
            <ToggleGroupItem value="backend">
              {t.settings.logViewer.backend}
            </ToggleGroupItem>
            <ToggleGroupItem value="frontend">
              {t.settings.logViewer.frontend}
            </ToggleGroupItem>
          </ToggleGroup>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={loading}
            onClick={() => void loadLog(source)}
          >
            <RefreshCwIcon className={loading ? "animate-spin" : ""} />
            {t.settings.logViewer.refresh}
          </Button>
        </div>

        {error && <p className="mb-3 text-sm text-red-500">{error}</p>}

        {data && (
          <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground text-xs">
            <span>
              {t.settings.logViewer.pathLabel}: <code>{data.path}</code>
            </span>
            <span>
              {t.settings.logViewer.sizeLabel}: {formatSize(data.size)}
            </span>
            {data.truncated && (
              <span className="text-amber-600">
                {t.settings.logViewer.truncatedHint}
              </span>
            )}
          </div>
        )}

        <div className="max-h-[60vh] overflow-auto rounded-lg border bg-zinc-950">
          {loading && content === "" ? (
            <pre className="p-3 text-muted-foreground text-xs">
              {t.common.loading}...
            </pre>
          ) : isEmpty ? (
            <pre className="p-3 text-muted-foreground text-xs">
              {t.settings.logViewer.empty}
            </pre>
          ) : (
            <pre className="overflow-x-auto p-3 font-mono text-xs leading-relaxed text-zinc-100 whitespace-pre-wrap break-all">
              {content}
            </pre>
          )}
        </div>
      </SettingsSection>
    </div>
  );
}
