import { describe, expect, it } from "vitest";

import {
  buildDraftReleaseDiff,
  buildReleaseDiff,
} from "@/components/workspace/published-agents/release-history";
import type { AgentDraft, AgentRelease } from "@/core/published-agents";

const release: AgentRelease = {
  id: "rel_1",
  agent_id: "agent_1",
  release_no: 1,
  agent_markdown: "# Agent v1",
  soul_markdown: "Calm",
  model_name: "model-a",
  tool_groups: ["web"],
  quota_overrides: {},
  manifest_checksum: "sha256:one",
  created_by: "owner-1",
  created_at: "2026-07-24T00:00:00Z",
  skills: [{ skill_revision_id: "skr_search", skill_name: "search" }],
  connector_grants: [
    {
      connector_instance_id: "conn_1",
      capability: "search.query",
    },
  ],
};

describe("published Agent release diff", () => {
  it("describes draft changes against the current immutable release", () => {
    const draft: AgentDraft = {
      agent_id: "agent_1",
      agent_markdown: "# Agent v2",
      soul_markdown: "Calm",
      model_name: "model-b",
      tool_groups: ["web", "filesystem"],
      quota_overrides: {},
      revision: 4,
      skills: [{ skill_name: "research", source: "public" }],
      connector_grants: [
        {
          connector_instance_id: "conn_2",
          capability: "drive.read",
        },
      ],
    };

    expect(buildDraftReleaseDiff(draft, release)).toMatchObject({
      instructionsChanged: true,
      modelChanged: true,
      toolGroups: { added: ["filesystem"], removed: [] },
      skills: { added: ["research"], removed: ["search"] },
      connectorGrants: {
        added: ["conn_2 · drive.read"],
        removed: ["conn_1 · search.query"],
      },
    });
  });

  it("compares two historical releases without relying on list order", () => {
    const next: AgentRelease = {
      ...release,
      id: "rel_2",
      release_no: 2,
      model_name: null,
      tool_groups: [],
      manifest_checksum: "sha256:two",
    };

    expect(buildReleaseDiff(release, next)).toMatchObject({
      modelChanged: true,
      toolGroups: { added: [], removed: ["web"] },
    });
  });
});
