"use client";

import {
  CheckIcon,
  CircleOffIcon,
  DatabaseIcon,
  ShieldCheckIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type {
  ConnectorInstance,
  ConnectorTypeDefinition,
} from "@/core/connectors/types";
import { useI18n } from "@/core/i18n/hooks";
import type { AgentConnectorGrant } from "@/core/published-agents";
import { cn } from "@/lib/utils";

interface ConnectorGrantsProps {
  connectors: ConnectorInstance[];
  connectorTypes: ConnectorTypeDefinition[];
  grants: AgentConnectorGrant[];
  onChange: (grants: AgentConnectorGrant[]) => void;
}

export function ConnectorGrants({
  connectors,
  connectorTypes,
  grants,
  onChange,
}: ConnectorGrantsProps) {
  const { t } = useI18n();
  const granted = new Set(
    grants.map(
      (grant) => `${grant.connector_instance_id}:${grant.capability}`,
    ),
  );

  function toggle(connectorId: string, capability: string) {
    const key = `${connectorId}:${capability}`;
    if (granted.has(key)) {
      onChange(
        grants.filter(
          (grant) =>
            !(
              grant.connector_instance_id === connectorId &&
              grant.capability === capability
            ),
        ),
      );
      return;
    }
    onChange([
      ...grants,
      { connector_instance_id: connectorId, capability },
    ]);
  }

  if (connectors.length === 0) {
    return (
      <div className="text-muted-foreground flex min-h-52 flex-col items-center justify-center rounded-xl border border-dashed bg-muted/15 px-6 text-center text-sm">
        <DatabaseIcon className="mb-3 size-6" />
        {t.publishedAgents.studio.emptyConnectors}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {connectors.map((connector) => {
        const type = connectorTypes.find(
          (candidate) => candidate.type === connector.type,
        );
        const capabilities = type?.capabilities ?? [];
        const active = connector.status === "active";
        return (
          <section
            key={connector.id}
            className={cn(
              "rounded-xl border bg-card p-4",
              !active && "opacity-65",
            )}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <div className="bg-muted flex size-10 shrink-0 items-center justify-center rounded-xl">
                  <DatabaseIcon className="size-4.5" />
                </div>
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-semibold">
                    {connector.display_name ?? connector.name}
                  </h3>
                  <p className="text-muted-foreground mt-0.5 font-mono text-xs">
                    {type?.display_name ?? connector.type} · {connector.id}
                  </p>
                </div>
              </div>
              <Badge
                variant="outline"
                className={cn(
                  active
                    ? "border-emerald-300 text-emerald-700 dark:border-emerald-900 dark:text-emerald-300"
                    : "text-muted-foreground",
                )}
              >
                {active ? <ShieldCheckIcon /> : <CircleOffIcon />}
                {active ? t.publishedAgents.studio.granted : connector.status}
              </Badge>
            </div>

            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {capabilities.map((capability) => {
                const key = `${connector.id}:${capability}`;
                const isGranted = granted.has(key);
                return (
                  <button
                    key={capability}
                    type="button"
                    role="checkbox"
                    aria-checked={isGranted}
                    aria-label={`${connector.display_name ?? connector.name} ${capability}`}
                    disabled={!active}
                    onClick={() => toggle(connector.id, capability)}
                    className={cn(
                      "flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-left font-mono text-xs transition-colors outline-none hover:border-foreground/30 focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed",
                      isGranted && "border-foreground/35 bg-foreground/[0.04]",
                    )}
                    title={
                      isGranted
                        ? t.publishedAgents.studio.revokeCapability
                        : t.publishedAgents.studio.grantCapability
                    }
                  >
                    <span className="truncate">{capability}</span>
                    <span
                      className={cn(
                        "flex size-4 shrink-0 items-center justify-center rounded border",
                        isGranted &&
                          "border-foreground bg-foreground text-background",
                      )}
                    >
                      {isGranted ? <CheckIcon className="size-3" /> : null}
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
