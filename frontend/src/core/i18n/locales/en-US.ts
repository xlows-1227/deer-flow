import {
  CompassIcon,
  GraduationCapIcon,
  ImageIcon,
  MicroscopeIcon,
  PenLineIcon,
  ShapesIcon,
  SparklesIcon,
  VideoIcon,
} from "lucide-react";

import type { Translations } from "./types";

export const enUS: Translations = {
  // Locale meta
  locale: {
    localName: "English",
  },

  // Common
  common: {
    home: "Home",
    settings: "Settings",
    delete: "Delete",
    edit: "Edit",
    rename: "Rename",
    share: "Share",
    openInNewWindow: "Open in new window",
    close: "Close",
    more: "More",
    search: "Search",
    loadMore: "Load more",
    download: "Download",
    downloadSuccess: "Download successful",
    downloadFailed: "Download failed",
    thinking: "Thinking",
    artifacts: "Artifacts",
    public: "Public",
    custom: "Custom",
    notAvailableInDemoMode: "Not available in demo mode",
    loading: "Loading...",
    version: "Version",
    lastUpdated: "Last updated",
    code: "Code",
    preview: "Preview",
    cancel: "Cancel",
    save: "Save",
    install: "Install",
    create: "Create",
    import: "Import",
    export: "Export",
    exportAsMarkdown: "Export as Markdown",
    exportAsJSON: "Export as JSON",
    exportSuccess: "Conversation exported",
    view: "View",
  },

  // Home
  home: {
    docs: "Docs",
    blog: "Blog",
  },

  // Welcome
  welcome: {
    greeting: "Hello, again!",
    description:
      "Welcome to Friday. It can help you search the web, analyze data, and generate slides and images. It can do almost anything.",

    createYourOwnSkill: "Create Your Own Skill",
    createYourOwnSkillDescription:
      "Create your own skill to release the power of Friday. With customized skills,\nFriday can help you search on the web, analyze data, and generate\n artifacts like slides, web pages and do almost anything.",
  },

  // Clipboard
  clipboard: {
    copyToClipboard: "Copy to clipboard",
    copiedToClipboard: "Copied to clipboard",
    failedToCopyToClipboard: "Failed to copy to clipboard",
    linkCopied: "Link copied to clipboard",
  },

  // Input Box
  inputBox: {
    placeholder: "How can I assist you today?",
    createSkillPrompt:
      "We're going to build a new skill step by step with `skill-creator`. To start, what do you want this skill to do?",
    addAttachments: "Add attachments",
    mode: "Mode",
    flashMode: "Flash",
    flashModeDescription: "Fast and efficient, but may not be accurate",
    reasoningMode: "Reasoning",
    reasoningModeDescription:
      "Reasoning before action, balance between time and accuracy",
    proMode: "Pro",
    proModeDescription:
      "Reasoning, planning and executing, get more accurate results, may take more time",
    ultraMode: "Ultra",
    ultraModeDescription:
      "Pro mode with subagents to divide work; best for complex multi-step tasks",
    reasoningEffort: "Reasoning Effort",
    reasoningEffortMinimal: "Minimal",
    reasoningEffortMinimalDescription: "Retrieval + Direct Output",
    reasoningEffortLow: "Low",
    reasoningEffortLowDescription: "Simple Logic Check + Shallow Deduction",
    reasoningEffortMedium: "Medium",
    reasoningEffortMediumDescription:
      "Multi-layer Logic Analysis + Basic Verification",
    reasoningEffortHigh: "High",
    reasoningEffortHighDescription:
      "Full-dimensional Logic Deduction + Multi-path Verification + Backward Check",
    skill: "Skill",
    noSkill: "Auto",
    noSkillDescription:
      "Let the model choose the appropriate skill automatically",
    connector: "Connector",
    noConnector: "No connector",
    noConnectorDescription: "Do not pin this chat to a database connector",
    slashSkillPickerTitle: "Run a command",
    slashSkillPickerHint: "Type to filter · ↑↓ to navigate · Enter to select",
    slashSkillPickerEmpty: "No matching commands",
    slashSkillPickerKeep: "Keep current",
    slashCommandModel: "Model",
    slashCommandModelDescription: "Switch the chat model",
    slashCommandClear: "Clear input",
    slashCommandClearDescription: "Empty the current message",
    slashCommandHelp: "Help",
    slashCommandHelpDescription: "Show all slash commands",
    slashCommandHelpTitle: "Slash commands",
    slashCommandHelpIntro:
      "Type / in the input to summon a command. Below is what ships by default; your team can register more via the slash-commands API.",
    slashCommandHelpSkillRow: "/<skill> — pick a skill (e.g. /researcher)",
    slashCommandHelpModeRow: "/flash · /thinking · /pro · /ultra — switch mode",
    slashCommandHelpModelRow: "/model — open the model picker",
    slashCommandHelpClearRow: "/clear — empty the current input",
    mentionFilePickerTitle: "Reference a file",
    mentionFilePickerHint: "Type to filter · ↑↓ to navigate · Enter to select",
    mentionFilePickerEmpty: "No matching files",
    mentionFilePickerLoading: "Loading files…",
    mentionFilePickerError: "Failed to load files",
    mentionFilePickerNoFiles: "No files in your library yet",
    referencedFileChipRemove: "Remove referenced file",
    referencedFileFromLibrary: "From library",
    referencedFileOpenInLibrary: "Open in library",
    searchModels: "Search models...",
    surpriseMe: "Surprise",
    surpriseMePrompt: "Surprise me",
    followupLoading: "Generating follow-up questions...",
    followupConfirmTitle: "Send suggestion?",
    followupConfirmDescription:
      "You already have text in the input. Choose how to send it.",
    followupConfirmAppend: "Append & send",
    followupConfirmReplace: "Replace & send",
    suggestions: [
      {
        suggestion: "Write",
        prompt: "Write a blog post about the latest trends on [topic]",
        icon: PenLineIcon,
      },
      {
        suggestion: "Research",
        prompt:
          "Conduct a deep dive research on [topic], and summarize the findings.",
        icon: MicroscopeIcon,
      },
      {
        suggestion: "Collect",
        prompt: "Collect data from [source] and create a report.",
        icon: ShapesIcon,
      },
      {
        suggestion: "Learn",
        prompt: "Learn about [topic] and create a tutorial.",
        icon: GraduationCapIcon,
      },
    ],
    suggestionsCreate: [
      {
        suggestion: "Webpage",
        prompt: "Create a webpage about [topic]",
        icon: CompassIcon,
      },
      {
        suggestion: "Image",
        prompt: "Create an image about [topic]",
        icon: ImageIcon,
      },
      {
        suggestion: "Video",
        prompt: "Create a video about [topic]",
        icon: VideoIcon,
      },
      {
        type: "separator",
      },
      {
        suggestion: "Skill",
        prompt:
          "We're going to build a new skill step by step with `skill-creator`. To start, what do you want this skill to do?",
        icon: SparklesIcon,
      },
    ],
  },

  // Sidebar
  sidebar: {
    newChat: "New chat",
    chats: "Chats",
    recentChats: "Recent chats",
    viewAllChats: "View all",
    demoChats: "Demo chats",
    agents: "Agents",
    skills: "Skills",
    files: "Files",
    memory: "Memory",
    scheduledTasks: "Scheduled tasks",
    collapseSidebar: "Collapse sidebar",
    expandSidebar: "Expand sidebar",
    taskRecords: "Task records",
    taskRecordsEmpty: "No task runs yet",
    taskRecordsLoadFailed: "Could not load task records.",
    taskRecordsOpen: "Open task conversation",
    taskRunStatus: {
      running: "Running",
      success: "Success",
      error: "Failed",
      cancelled: "Cancelled",
    },
  },

  // Agents
  agents: {
    title: "Agents",
    description:
      "Create and manage custom agents with specialized prompts and capabilities.",
    newAgent: "New Agent",
    emptyTitle: "No custom agents yet",
    emptyDescription:
      "Create your first custom agent with a specialized system prompt.",
    chat: "Chat",
    delete: "Delete",
    deleteConfirm:
      "Are you sure you want to delete this agent? This action cannot be undone.",
    deleteSuccess: "Agent deleted",
    newChat: "New chat",
    createPageTitle: "Design your Agent",
    createPageSubtitle:
      "Describe the agent you want — I'll help you create it through conversation.",
    nameStepTitle: "Name your new Agent",
    nameStepHint:
      "Letters, digits, and hyphens only — stored lowercase (e.g. code-reviewer)",
    nameStepPlaceholder: "e.g. code-reviewer",
    nameStepContinue: "Continue",
    nameStepInvalidError:
      "Invalid name — use only letters, digits, and hyphens",
    nameStepAlreadyExistsError: "An agent with this name already exists",
    nameStepNetworkError:
      "Network request failed — check your network or backend connection",
    nameStepCheckError: "Could not verify name availability — please try again",
    nameStepApiDisabledError:
      "Custom agent management is not enabled on this server. Please contact your administrator.",
    nameStepBootstrapMessage:
      "The new custom agent name is {name}. Help me design its purpose, behavior, and SOUL.md before saving it.",
    save: "Save agent",
    saving: "Saving agent...",
    saveRequested:
      "Save requested. DeerFlow is generating and saving an initial version now.",
    saveHint:
      "You can save this agent at any time from the top-right menu, even if this is only a first draft.",
    saveCommandMessage:
      "Please save this custom agent now based on everything we have discussed so far. Treat this as my explicit confirmation to save. If some details are still missing, make reasonable assumptions, generate a concise first SOUL.md in English, and call setup_agent immediately without asking me for more confirmation.",
    agentCreatedPendingRefresh:
      "The agent was created, but DeerFlow could not load it yet. Please refresh this page in a moment.",
    more: "More actions",
    agentCreated: "Agent created!",
    startChatting: "Start chatting",
    backToGallery: "Back to Gallery",
  },

  // Published Agents
  publishedAgents: {
    gallery: {
      title: "Agent control",
      eyebrow: "PUBLISHING PLANE",
      description:
        "Draft, publish and operate stable Agent identities from one owner-only console.",
      newAgent: "New Agent",
      createTitle: "Create a stable Agent",
      createDescription:
        "This creates a private draft identity. Nothing goes live until you publish.",
      slug: "Agent slug",
      slugHint:
        "Letters, numbers and hyphens. This stable identifier cannot be reused by another Agent you own.",
      displayName: "Display name",
      descriptionLabel: "Description",
      descriptionPlaceholder: "What is this Agent responsible for?",
      createDraft: "Create draft",
      creating: "Creating…",
      createSuccess: "Draft Agent created",
      emptyTitle: "No Agent identities yet",
      emptyDescription:
        "Create a private draft, shape its capabilities in Studio, then publish when it is ready.",
      loadError: "Could not load the Agent control plane.",
      retry: "Try again",
      studio: "Open Studio",
      actions: "Agent actions",
      suspend: "Suspend",
      resume: "Resume",
      archive: "Archive",
      confirmSuspendTitle: "Suspend this Agent?",
      confirmSuspendDescription:
        "New external runs will stop. Drafts, Releases, API keys, bindings and history are preserved.",
      confirmArchiveTitle: "Archive this Agent?",
      confirmArchiveDescription:
        "The Agent leaves active operation but all data remains available to its owner.",
      statusUpdated: "Agent status updated",
      release: (releaseNo) => `Release ${releaseNo}`,
      noRelease: "Not published",
      publishedAt: "Published",
      apiKeyCount: (count) => `${count} API key${count === 1 ? "" : "s"}`,
      feishuCount: (count) =>
        `${count} Feishu binding${count === 1 ? "" : "s"}`,
      runsCount: (count) => `${count} run${count === 1 ? "" : "s"}`,
      tokensCount: (count) => `${count.toLocaleString()} tokens`,
      usage7d: "7-day usage",
      integrations: "Active integrations",
      health: "Health",
    },
    status: {
      draft: "Draft",
      published: "Published",
      suspended: "Suspended",
      archived: "Archived",
    },
    health: {
      healthy: "Healthy",
      unhealthy: "Needs attention",
      unknown: "Unknown",
      notConfigured: "No channel",
    },
    studio: {
      eyebrow: "AGENT STUDIO",
      back: "Back to control",
      draftVersion: (revision) => `Draft r${revision}`,
      saveDraft: "Save draft",
      saving: "Saving…",
      saved: "Draft saved",
      conflictTitle: "Draft changed elsewhere",
      reloadDraft: "Reload draft",
      overviewTab: "Overview",
      instructionsTab: "Instructions",
      skillsTab: "Skills",
      connectorsTab: "Connectors",
      sandboxTab: "Sandbox",
      publishTab: "Publish",
      integrationsTab: "Integrations",
      operationsTab: "Operations",
      overviewTitle: "Draft configuration",
      overviewDescription:
        "Review the stable identity and choose the runtime model. Tool availability follows the platform policy.",
      stableIdentity: "Stable identity",
      stableIdentityDescription:
        "Identity fields survive republishing and rollback. The slug is used by draft test chats.",
      slug: "Slug",
      displayName: "Display name",
      description: "Description",
      avatar: "Avatar",
      notConfigured: "Not configured",
      model: "Model",
      inheritModel: "Use platform default",
      instructionsTitle: "Agent instructions",
      instructionsDescription:
        "Define work rules in AGENT.md, then choose a managed personality for SOUL.md. Saving the draft does not change the live Release.",
      agentMarkdownTitle: "Work rules",
      agentMarkdownDescription:
        "Customize the starting template to define responsibilities, workflow, boundaries, and output requirements.",
      agentMarkdownSuggestions: [
        "Role & goal",
        "Responsibilities",
        "Workflow",
        "Boundaries",
        "Output requirements",
      ],
      agentMarkdownPlaceholder:
        "# Responsibilities\nDescribe what this Agent owns and the outcomes it should produce.",
      agentMarkdownTemplate:
        "# Role & goal\nYou are [Agent name], responsible for [core goal]. Your success is measured by [expected outcome].\n\n# Responsibilities\n- Own: [primary responsibility]\n- Support: [secondary responsibility]\n- Do not take ownership of: [out-of-scope work]\n\n# Workflow\n1. Confirm the user's goal and the minimum context needed to proceed.\n2. Break the request into clear steps and use only authorized Skills and Connectors.\n3. Check the result against the user's goal before responding.\n4. State important assumptions, uncertainty, and any unfinished work.\n\n# Boundaries\n- Do not claim capabilities, data access, or permissions that are unavailable.\n- Do not invent facts, sources, execution results, or completion status.\n- Ask for clarification before acting when a missing decision would materially change the result.\n- Stop and explain the limitation when a request is unsafe, unauthorized, or outside scope.\n\n# Output requirements\n- Lead with the conclusion or completed result.\n- Use a clear structure appropriate to the task.\n- Distinguish facts, assumptions, and recommendations when relevant.\n- Include sources, risks, and suggested next steps when useful.",
      instructionSuggestionsLabel: "Good topics",
      soulPresetTitle: "Personality & voice",
      soulPresetDescription:
        "Choose one managed personality. SOUL.md is generated by the system and cannot be edited directly.",
      soulPresetBadge: "Preset only",
      soulPresetLegacyTitle: "Legacy custom SOUL.md is active",
      soulPresetLegacyDescription:
        "The existing content remains unchanged and read-only. Select a preset to replace it; the live Release is unaffected until you publish.",
      soulPresetLegacyPreviewLabel: "Current read-only SOUL.md",
      soulPresets: {
        professional: {
          name: "Professional & rigorous",
          summary: "Evidence-led, dependable, and precise",
          content:
            "# Personality\nYou are professional, rigorous, and dependable.\n\n# Communication style\n- Use precise language and lead with verified facts.\n- Separate facts, assumptions, and recommendations.\n- State uncertainty and limitations plainly.\n- Remain calm, respectful, and solution-oriented.",
        },
        warm: {
          name: "Warm & patient",
          summary: "Approachable, considerate, and explanatory",
          content:
            "# Personality\nYou are warm, patient, and considerate.\n\n# Communication style\n- Use approachable language without becoming overly casual.\n- Explain unfamiliar concepts step by step.\n- Acknowledge the user's concerns and avoid judgment.\n- Encourage progress while remaining honest about limitations.",
        },
        concise: {
          name: "Concise & direct",
          summary: "Fast, focused, and low on ceremony",
          content:
            "# Personality\nYou are concise, direct, and pragmatic.\n\n# Communication style\n- Lead with the answer or completed result.\n- Remove filler, repetition, and unnecessary background.\n- Use short sections or bullets only when they improve scanning.\n- Surface important risks or blockers without softening them.",
        },
        coach: {
          name: "Guiding coach",
          summary: "Structured, curious, and growth-oriented",
          content:
            "# Personality\nYou are a thoughtful, structured coach.\n\n# Communication style\n- Help the user clarify goals, constraints, and success criteria.\n- Ask focused questions only when the answer materially changes the path.\n- Explain reasoning and offer actionable next steps.\n- Support the user's judgment instead of making decisions on their behalf.",
        },
      },
      skillsTitle: "Skill selection",
      skillsDescription:
        "Only public Skills and private Skills owned by you are selectable.",
      publicSkills: "Public",
      privateSkills: "Private",
      emptySkills: "No selectable Skills in this group.",
      skillSearchLabel: "Search Skills",
      skillSearchPlaceholder: "Search Skill names or descriptions...",
      skillSearchSummary: (visible, total, selected) =>
        `${visible} of ${total} Skills · ${selected} selected`,
      noMatchingSkills: "No matching Skills.",
      clearSkillSearch: "Clear Skill search",
      connectorRequirements: "Connector requirements",
      noConnectorRequired: "No Connector capability required",
      missingGrant: "Missing Connector grant",
      granted: "Granted",
      connectorsTitle: "Connector grants",
      connectorsDescription:
        "Grant the minimum capability needed. Secrets stay in the Connector store and are never copied into a Release.",
      emptyConnectors:
        "No owner Connector instances are available. Add one in Settings first.",
      disabledConnector: "Inactive Connector",
      grantCapability: "Grant capability",
      revokeCapability: "Revoke capability",
      sandboxTitle: "Draft sandbox",
      sandboxDescription:
        "Launch an isolated internal conversation using the current draft configuration.",
      notLive: "Not live",
      sandboxSafety:
        "Sandbox chats read the mutable draft, do not replace the published Release, and do not create Published usage charges.",
      sandboxMessageLabel: "Sandbox message",
      sandboxMessagePlaceholder: "Ask the saved draft to perform a test task…",
      runSandbox: "Run saved draft",
      runningSandbox: "Starting draft run…",
      sandboxStarted: (revision: number) =>
        `Draft revision ${revision} · Not billable`,
      openSandbox: "Open sandbox conversation",
    },
    publish: {
      title: "Publish and releases",
      description:
        "Validate the saved draft, inspect the exact change set, and atomically promote an immutable Release.",
      loadError: "Could not load Release history.",
      savedDraftOnly:
        "This comparison uses the latest saved draft. Publishing never reads unsaved editor state.",
      initialSummary:
        "This will create the first immutable Release for the stable Agent identity.",
      unsavedTitle: "Save the draft before publishing",
      unsavedDescription:
        "The editor contains unsaved changes. Save them so the preview and published snapshot stay identical.",
      neverPublished: "No live Release",
      changeSummary: "Saved draft change set",
      publish: "Publish saved draft",
      publishing: "Publishing…",
      successTitle: (releaseNo) => `Release ${releaseNo} is live`,
      successDescription:
        "The stable Agent identity now points to the new immutable snapshot.",
      validationTitle: "Draft validation failed",
      validationDescription:
        "Resolve every item below, save the draft, then publish again.",
      violation: (code, fallback) =>
        ({
          EMPTY_INSTRUCTIONS:
            "Add content to at least one of AGENT.md or SOUL.md.",
          INSTRUCTION_TOO_LARGE:
            "Reduce the indicated instruction file below the platform size limit.",
          MODEL_NOT_AVAILABLE:
            "Choose a model currently available to this owner.",
          SKILL_NOT_FOUND:
            "Remove or replace the Skill that is no longer selectable.",
          CONNECTOR_NOT_GRANTED:
            "Grant the Connector capability required by the selected Skill.",
          CONNECTOR_NOT_OWNED:
            "Remove the Connector instance that is unavailable to this owner.",
          CONNECTOR_CAPABILITY_UNSUPPORTED:
            "The selected Connector does not support this capability.",
          TOOL_GROUP_UNKNOWN:
            "Remove the tool group that is not on the platform allowlist.",
          QUOTA_EXCEEDS_PLATFORM:
            "Lower the quota override to the platform maximum.",
          DRAFT_REVISION_CONFLICT:
            "The draft changed during publishing. Reload and try again.",
        })[code] ?? fallback,
      unchanged: "Unchanged",
      added: "Added",
      removed: "Removed",
      model: "Model",
      defaultModel: "Platform default",
      toolGroups: "Tool groups",
      skills: "Skills",
      connectorGrants: "Connector grants",
      historyTitle: "Release history",
      historyDescription:
        "History is owner-only. Releases are immutable; rollback only changes the current pointer.",
      historyEmpty: "No Releases have been published.",
      createdBy: "created by",
      current: "Current",
      compareTitle: "Compare historical Releases",
      compareDescription: "Choose two different Releases to inspect changes.",
      compareFrom: "Compare from",
      compareTo: "Compare to",
      fromTo: (from, to) => `Release ${from} → Release ${to}`,
      rollback: "Roll back",
      rollbackTitle: (releaseNo) => `Roll back to Release ${releaseNo}?`,
      rollbackDescription:
        "The selected immutable snapshot becomes current immediately. No Release is deleted.",
      stableIntegrationNotice:
        "Stable contract: the Agent ID, API path, API keys, Feishu bindings and conversation identity remain unchanged.",
      cancel: "Cancel",
      confirmRollback: "Confirm rollback",
      rollingBack: "Rolling back…",
      rollbackSuccess: (releaseNo) =>
        `Current pointer moved to Release ${releaseNo}`,
    },
    integrations: {
      title: "Post-publish integrations",
      description:
        "Issue Agent-scoped credentials and operate independent Feishu bot bindings without republishing.",
      apiKeysTitle: "Agent API keys",
      apiKeysDescription:
        "Create multiple named credentials. A new plaintext key remains copyable for this page session only; delete keys you no longer need.",
      createApiKey: "Create API key",
      publishFirstTitle: "Publish this Agent first",
      publishFirstDescription:
        "Keys and channel bindings attach to a live stable identity, so they are disabled until the first Release.",
      loading: "Loading integration state…",
      noKeys:
        "No API keys. Create one when this Agent is ready for API traffic.",
      lastUsed: "Last used",
      copyKeyFor: (name) => `Copy ${name}`,
      copyKeyUnavailable:
        "The full key is only available in the page session where it was created.",
      deleteKey: (name) => `Delete ${name}`,
      copy: "Copy",
      delete: "Delete",
      apiExamplesTitle: "Stable API examples",
      apiExamplesDescription:
        "These paths use the stable Agent ID and never expose an internal Release. Create a conversation first and set CONVERSATION_ID.",
      sync: "Synchronous",
      sse: "SSE stream",
      async: "Asynchronous",
      copyExample: "Copy API example",
      createKeyTitle: "Create a named API key",
      createKeyDescription: "Enter a recognizable name for this credential.",
      keyName: "Key name",
      createKey: "Create key",
      secretTitle: "Store the new API key now",
      secretDescription:
        "Only its prefix and final four characters will remain visible.",
      secretOnce: "This secret is displayed once",
      secretWarning:
        "Copy it into your secret manager before closing. It cannot be retrieved again.",
      copyKey: "Copy API key",
      keyCopied: "Copied",
      storedKey: "I stored this key",
      deleteTitle: "Delete this API key?",
      deleteDescription:
        "This permanently removes the key and immediately rejects future requests that use it. This action cannot be undone.",
      confirmDelete: "Delete API key",
      keyDeleted: "API key deleted",
      keyStatus: (status) =>
        ({
          active: "Active",
          overlap: "Rotation overlap",
          revoked: "Revoked",
          expired: "Expired",
        })[status] ?? status,
      feishuTitle: "Feishu bot bindings",
      feishuDescription:
        "Each binding is an isolated app credential and WebSocket lifecycle. Health does not change Agent publish status.",
      addBinding: "Add Feishu binding",
      channelLoadError: "Could not load channel bindings.",
      noBindings: "No Feishu binding. API-only operation is fully supported.",
      channelHealth: (health) =>
        ({
          healthy: "Healthy",
          unhealthy: "Needs attention",
          unknown: "Unknown",
          starting: "Starting",
          stopped: "Stopped",
        })[health] ?? health,
      channelStatus: (status) =>
        ({
          inactive: "Inactive",
          active: "Active",
          deleting: "Deleting",
        })[status] ?? status,
      noHealthDetail: "No health detail reported.",
      testConnection: "Test connection",
      stop: "Stop",
      start: "Start",
      restart: "Restart",
      rotateCredentials: "Rotate credentials",
      createBindingTitle: "Add a Feishu application",
      createBindingDescription:
        "Credentials are encrypted into SecretStore and never returned by this control plane.",
      appId: "App ID",
      appSecret: "App Secret",
      verificationToken: "Verification token",
      encryptKey: "Encrypt key (optional)",
      keepAppId: "Keep the current App ID",
      createBinding: "Create binding",
      bindingCreated: "Feishu binding created",
      rotateCredentialsTitle: "Rotate Feishu credentials",
      rotateCredentialsDescription:
        "Active bindings restart with readiness checks. If readiness fails, the previous credentials are restored.",
      confirmCredentialRotation: "Rotate credentials",
      credentialsRotated: "Feishu credentials rotated",
      channelActionSuccess: (action) =>
        ({
          test: "Connection test completed",
          start: "Feishu binding started",
          stop: "Feishu binding stopped",
          restart: "Feishu binding restarted",
        })[action] ?? "Channel action completed",
    },
    ops: {
      title: "Usage and operations",
      description:
        "Inspect owner-scoped usage, set bounded quotas for the next Release, and review metadata-only rejection events.",
      usageTitle: "Published usage",
      usageDescription:
        "Daily terminal Run accounting. Filters are applied inside the owner-scoped usage query.",
      dateRange: "Date range",
      lastDays: (days) => `Last ${days} days`,
      source: "Traffic source",
      allSources: "All sources",
      apiKey: "API key",
      allKeys: "All API keys",
      totalRuns: "Runs",
      totalTokens: "Tokens",
      errorRate: "Error rate",
      estimatedCost: "Estimated cost",
      currentReleaseErrorRate: "Current Release errors",
      quotaRejections: "Quota rejections",
      saturation: "saturation",
      feishuP95Latency: "Feishu event p95",
      connectorIssues: "Connector failures",
      denied: "denied",
      bindingHealth: "Active bindings",
      unhealthy: "unhealthy",
      dailyRuns: "Daily runs",
      dailyTokens: "Daily tokens",
      dailyErrorRate: "Daily error rate",
      auditTitle: "Recent rejection events",
      auditDescription:
        "Metadata only: no prompts, messages, model output, credentials, client IPs or user-agent values are exposed.",
      loadingAudit: "Loading recent rejection metadata…",
      noRejections: "No owner-visible rejection events in the recent window.",
      auditCategory: (category) =>
        ({
          quota: "Quota rejection",
          authentication: "Authentication failure",
          capability: "Capability rejection",
          request: "Request rejection",
        })[category] ?? category,
      quotaTitle: "Owner quota draft",
      quotaDescription:
        "Overrides can only tighten platform limits and are snapshotted into the next immutable Release.",
      saveQuota: "Save quota draft",
      savingQuota: "Saving quota…",
      inheritanceTitle: "Inheritance is always bounded",
      inheritanceDescription:
        "Blank means inherit the platform default — never unlimited.",
      saveOtherDraftTitle: "Save other draft edits first",
      saveOtherDraftDescription:
        "Quota saving is paused to avoid creating a revision conflict with unsaved Studio changes.",
      quotaLoadError: "Could not load the platform quota policy.",
      quotaOverrideLabel: (field) =>
        `${
          {
            max_concurrent_runs: "Concurrent runs",
            daily_runs: "Daily runs",
            daily_tokens: "Daily tokens",
            max_run_seconds: "Run duration",
            max_tokens_per_run: "Tokens per run",
            max_input_bytes: "Input bytes",
            inbound_rps: "Inbound requests / second",
          }[field] ?? field
        } override`,
      inherited: "Inherited",
      overridden: "Override",
      platformDefault: "Platform default / maximum",
      effectiveAfterPublish: "Effective after publish",
      exceedsMaximum: (maximum) =>
        `Must not exceed ${maximum.toLocaleString()}.`,
      positiveInteger: "Enter a positive whole number.",
      draftOnlyNotice:
        "Saving here updates the mutable draft only. The current live Release keeps its existing quota until you publish again.",
      quotaSaved: "Quota draft saved",
      quotaConflict:
        "The draft changed elsewhere. Reload before saving quota overrides.",
    },
  },

  // Breadcrumb
  breadcrumb: {
    workspace: "Workspace",
    chats: "Chats",
  },

  // Workspace
  workspace: {
    officialWebsite: "DeerFlow's official website",
    githubTooltip: "DeerFlow on Github",
    settingsAndMore: "Settings and more",
    visitGithub: "DeerFlow on GitHub",
    reportIssue: "Report a issue",
    contactUs: "Contact us",
    about: "About DeerFlow",
    logout: "Log out",
  },

  // Conversation
  conversation: {
    noMessages: "No messages yet",
    startConversation: "Start a conversation to see messages here",
    memoryRollup: "Summarize to memory",
    memoryRollupSuccess: "Conversation summarized to memory",
    memoryRollupEmpty: "This conversation has nothing to summarize",
    memoryRollupFailed: "Failed to summarize conversation memory",
  },

  // Chats
  chats: {
    searchChats: "Search chats",
  },

  // Page titles (document title)
  pages: {
    appName: "Friday",
    chats: "Chats",
    newChat: "New chat",
    untitled: "Untitled",
  },

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => `${count} more step${count === 1 ? "" : "s"}`,
    lessSteps: "Less steps",
    executeCommand: "Execute command",
    presentFiles: "Present files",
    needYourHelp: "Need your help",
    useTool: (toolName: string) => `Use "${toolName}" tool`,
    searchFor: (query: string) => `Search for "${query}"`,
    searchForRelatedInfo: "Search for related information",
    searchForRelatedImages: "Search for related images",
    searchForRelatedImagesFor: (query: string) =>
      `Search for related images for "${query}"`,
    searchOnWebFor: (query: string) => `Search on the web for "${query}"`,
    viewWebPage: "View web page",
    listFolder: "List folder",
    readFile: "Read file",
    writeFile: "Write file",
    clickToViewContent: "Click to view file content",
    writeTodos: "Update to-do list",
    skillInstallTooltip: "Install skill and make it available to DeerFlow",
  },

  // Subtasks
  uploads: {
    uploading: "Uploading...",
    uploadingFiles: "Uploading files, please wait...",
  },

  subtasks: {
    subtask: "Subtask",
    executing: (count: number) =>
      `Executing ${count === 1 ? "" : count + " "}subtask${count === 1 ? "" : "s in parallel"}`,
    in_progress: "Running subtask",
    completed: "Subtask completed",
    failed: "Subtask failed",
  },

  // Token Usage
  tokenUsage: {
    title: "Token Usage",
    label: "Tokens",
    input: "Input",
    output: "Output",
    total: "Total",
    view: "Display",
    unavailable:
      "No token usage yet. Usage appears only after a successful model response when the provider returns usage_metadata.",
    unavailableShort: "No usage returned",
    note: "Header totals use persisted thread usage, plus visible in-flight usage while a run is still streaming. Per-turn and debug usage come from currently visible messages only. Totals may differ from provider billing pages.",
    presets: {
      off: "Off",
      summary: "Summary",
      perTurn: "Per turn",
      debug: "Debug",
    },
    presetDescriptions: {
      off: "Hide token usage in the header and conversation.",
      summary: "Show only the current conversation total in the header.",
      perTurn:
        "Show the header total and one token summary per assistant turn.",
      debug: "Show the header total and step-level token debugging details.",
    },
    finalAnswer: "Final answer",
    stepTotal: "Step total",
    sharedAttribution: "Shared across multiple actions in this step",
    subagent: (description: string) => `Subagent: ${description}`,
    startTodo: (content: string) => `Start To-do: ${content}`,
    completeTodo: (content: string) => `Complete To-do: ${content}`,
    updateTodo: (content: string) => `Update To-do: ${content}`,
    removeTodo: (content: string) => `Remove To-do: ${content}`,
  },

  // Shortcuts
  shortcuts: {
    searchActions: "Search actions...",
    noResults: "No results found.",
    actions: "Actions",
    keyboardShortcuts: "Keyboard Shortcuts",
    keyboardShortcutsDescription:
      "Navigate DeerFlow faster with keyboard shortcuts.",
    openCommandPalette: "Open Command Palette",
    toggleSidebar: "Toggle Sidebar",
  },

  // Settings
  settings: {
    title: "Settings",
    description: "Adjust how DeerFlow looks and behaves for you.",
    sections: {
      account: "Account",
      appearance: "Appearance",
      memory: "Memory",
      connectors: "Connectors",
      models: "Models",
      tools: "Tools",
      skills: "Skills",
      notification: "Notification",
      about: "About",
    },
    memory: {
      title: "Memory",
      description:
        "DeerFlow automatically learns from your conversations in the background. These memories help DeerFlow understand you better and deliver a more personalized experience.",
      empty: "No memory data to display.",
      rawJson: "Raw JSON",
      exportButton: "Export memory",
      exportSuccess: "Memory exported",
      importButton: "Import memory",
      importConfirmTitle: "Import memory?",
      importConfirmDescription:
        "This will overwrite your current memory with the selected JSON backup.",
      importFileLabel: "Selected file",
      importInvalidFile:
        "Failed to read the selected memory file. Please choose a valid JSON export.",
      importSuccess: "Memory imported",
      manualFactSource: "Manual",
      addFact: "Add manual memory",
      addFactTitle: "Add manual memory",
      editFactTitle: "Edit manual memory",
      addFactSuccess: "Manual memory created",
      editFactSuccess: "Manual memory updated",
      clearAll: "Clear all memory",
      clearAllConfirmTitle: "Clear all memory?",
      clearAllConfirmDescription:
        "This will remove the long-term profile, daily summaries, and manual memories. This action cannot be undone.",
      clearAllSuccess: "All memory cleared",
      factDeleteConfirmTitle: "Delete this manual memory?",
      factDeleteConfirmDescription:
        "This manual memory will be removed immediately. This action cannot be undone.",
      factDeleteSuccess: "Manual memory deleted",
      factContentLabel: "Content",
      factCategoryLabel: "Category",
      factConfidenceLabel: "Confidence",
      factContentPlaceholder: "Describe what you want DeerFlow to remember",
      factCategoryPlaceholder: "context",
      factConfidenceHint: "Use a number between 0 and 1.",
      factSave: "Save manual memory",
      factValidationContent: "Manual memory content cannot be empty.",
      factValidationConfidence: "Confidence must be a number between 0 and 1.",
      noFacts: "No manually added memories yet.",
      memoryFullyEmpty: "No memory saved yet.",
      factPreviewLabel: "Manual memory to delete",
      rollupDaily: "Roll up now",
      rollupDailySuccess: "Daily summary updated",
      rollupDailyEmpty: "No session content is ready to summarize",
      dailyDeleteConfirmTitle: "Delete this daily summary?",
      dailyDeleteConfirmDescription:
        "This soft-deletes the daily summary and immediately excludes it from display, injection, and profile consolidation.",
      dailyDeleteSuccess: "Daily summary deleted",
      dailyDeletePreviewLabel: "Date to delete",
      markdown: {
        facts: "Manual memories",
        table: {
          category: "Category",
          confidence: "Confidence",
          confidenceLevel: {
            veryHigh: "Very high",
            high: "High",
            normal: "Normal",
            unknown: "Unknown",
          },
          source: "Source",
          createdAt: "CreatedAt",
        },
        export: {
          longTermProfile: "Long-term Profile",
          dailySummary: "Daily Summary",
          preferences: "Preferences",
          communicationStyle: "Communication Style",
          skillUsagePatterns: "Skill Usage Patterns",
          interests: "Interests",
          topOfMind: "Recent Focus",
          corrections: "Corrections",
          updatedAt: "Updated at",
        },
      },
    },
    models: {
      title: "Custom Models",
      description:
        "Configure your own LLM providers with a base URL and API key. Custom models appear in the chat model selector.",
      hint: "Models are private to your account and stored with encrypted API keys.",
      empty: "No custom models yet. Add one to use it in chat.",
      addModel: "Add model",
      editModel: "Edit model",
      formDescription:
        "Choose OpenAI or Anthropic protocol. The model name must be unique within your account.",
      name: "Name",
      namePlaceholder: "yumcode-pro",
      displayName: "Display name",
      displayNamePlaceholder: "My GPT-4o",
      provider: "Provider protocol",
      modelId: "Model ID",
      modelIdPlaceholder: "gpt-4o",
      baseUrl: "Base URL",
      apiKey: "API key",
      apiKeyPlaceholder: "sk-...",
      apiKeyKeepExisting: "Leave unchanged to keep the existing key",
      apiKeySet: "Configured (••••{lastFour})",
      apiKeyMissing: "Not configured",
      enabled: "Enabled",
      enabledDescription:
        "Disabled models stay saved but won't appear in chat.",
      supportsThinking: "Thinking modes",
      supportsThinkingDescription:
        "Allow thinking, pro, and ultra modes. Disable to restrict this model to flash mode only.",
      supportsReasoningEffort: "Reasoning effort",
      supportsReasoningEffortDescription:
        "Pass the reasoning effort parameter (minimal / low / medium / high) to this model.",
      reasoningEffortOverride: "Reasoning effort override",
      reasoningEffortOverrideDescription:
        "Auto follows each mode's default (Thinking/Pro: low, Ultra: high). Set a value to override it for all chats.",
      reasoningEffortAuto: "Auto",
      disabled: "Disabled",
      loadFailed: "Failed to load custom models",
      saveFailed: "Failed to save custom model",
      createSuccess: "Custom model created",
      updateSuccess: "Custom model updated",
      deleteSuccess: "Custom model deleted",
      deleteFailed: "Failed to delete custom model",
      deleteConfirm: 'Delete model "{name}"?',
      validationRequired: "Name and model ID are required",
    },
    appearance: {
      themeTitle: "Theme",
      themeDescription:
        "Choose how the interface follows your device or stays fixed.",
      system: "System",
      light: "Light",
      dark: "Dark",
      systemDescription: "Match the operating system preference automatically.",
      lightDescription: "Bright palette with higher contrast for daytime.",
      darkDescription: "Dim palette that reduces glare for focus.",
      languageTitle: "Language",
      languageDescription: "Switch between languages.",
    },
    tools: {
      title: "Tools",
      description: "Manage image generation and MCP tool configuration.",
      tabs: {
        imageGeneration: "Image Generation",
        mcp: "MCP",
      },
      mcp: {
        systemBadge: "System",
        userBadge: "Mine",
        readOnlyHint:
          "System MCP servers can only be enabled or disabled for your account. Configuration is managed by administrators.",
      },
      imageGeneration: {
        title: "Image Generation",
        description:
          "Configure the default provider, model, and API Key. Agents will call the generate_image tool with these defaults.",
        loading: "Loading image generation configuration...",
        loadFailed: "Failed to load image generation configuration",
        noConfig: "No image generation configuration",
        enableTool: "Enable Image Generation",
        enableToolDescription:
          "When enabled, activated providers will be exposed to Agents for image generation.",
        defaultProvider: "Default Provider",
        outputDir: "Output Directory",
        selectModel: "Select model",
        enabled: "Enabled",
        disabled: "Disabled",
        keyConfigured: "Key configured",
        defaultModel: "Default Model",
        apiKey: "API Key",
        keepExistingKey: "Leave empty to keep existing Key",
        enterApiKey: "Enter API Key",
        baseUrl: "Base URL",
        timeoutSeconds: "Timeout (seconds)",
        saveConfig: "Save Configuration",
        saving: "Saving...",
        saveSuccess: "Image generation configuration saved",
        retry: "Retry",
        adapter: "Adapter",
      },
    },
    connectors: {
      title: "Connectors",
      description:
        "Manage external data connectors such as MySQL and StarRocks for safe, authorized agent queries.",
      total: "Total connectors",
      active: "Active",
      availableTypes: "Available database types",
      refresh: "Refresh",
      add: "Add connector",
      loading: "Loading connectors...",
      emptyTitle: "No connectors yet",
      emptyDescription:
        "Add a MySQL or StarRocks connector so agents can query external data through authorized tools.",
      statusActive: "Active",
      statusDisabled: "Disabled",
      test: "Test",
      testConnection: "Test connection",
      edit: "Edit",
      enable: "Enable",
      disable: "Disable",
      delete: "Delete",
      policy: "Query policy",
      lastTested: "Last tested",
      lastUsed: "Last used",
      never: "Never",
      maxRows: (count: number) => `Up to ${count} rows`,
      createTitle: "Add connector",
      editTitle: "Edit connector",
      createDescription:
        "Choose how to provide credentials. Reference a secret URL via environment variable, or store an encrypted username/password directly with the connector.",
      editDescription:
        "Update connection parameters and the default read-only policy. Leave the credential fields empty to keep the current value.",
      name: "Name",
      displayName: "Display name",
      type: "Type",
      host: "Host",
      port: "Port",
      database: "Database",
      ssl: "Use SSL",
      secretBoundary: "Credential boundary",
      authMode: "Credential mode",
      authModeEnv: "Environment variable",
      authModeInline: "Username & password",
      credentialRef: "Environment variable",
      username: "Username",
      password: "Password",
      passwordPlaceholder: "Leave empty to keep existing password",
      credentialUpdateHint:
        "Leave empty to keep the existing credential reference.",
      credentialUpdateHintInline:
        "Leave password empty to keep the existing one.",
      maxRowsLabel: "Max rows",
      allowedSchemas: "Allowed schemas",
      allowedSchemasPlaceholder:
        "One schema per line; leave empty for no schema allowlist",
      cancel: "Cancel",
      create: "Create",
      save: "Save",
      created: "Connector created",
      updated: "Connector updated",
      enabled: "Connector enabled",
      disabled: "Connector disabled",
      deleted: "Connector deleted",
      testSuccess: "Connection test passed",
      testSuccessWithLatency: (latencyMs: number) =>
        `Connection test passed in ${latencyMs}ms`,
      testFailed: "Connection test failed",
      deleteConfirm: (name: string) =>
        `Delete connector "${name}"? This action cannot be undone.`,
      validationName: "Enter a connector name",
      validationHost: "Enter a host",
      validationDatabase: "Enter a database",
      validationCredentialRef: "Enter an environment variable name",
      validationUsername: "Enter a username",
      validationPassword: "Enter a password",
    },
    skills: {
      title: "Agent Skills",
      description:
        "Manage the configuration and enabled status of the agent skills.",
      createSkill: "Create skill",
      emptyTitle: "No agent skill yet",
      emptyDescription:
        "Put your agent skill folders under the `/skills/custom` folder under the root folder of DeerFlow.",
      emptyButton: "Create Your First Skill",
    },
    notification: {
      title: "Notification",
      description:
        "DeerFlow only sends a completion notification when the window is not active. This is especially useful for long-running tasks so you can switch to other work and get notified when done.",
      requestPermission: "Request notification permission",
      deniedHint:
        "Notification permission was denied. You can enable it in your browser's site settings to receive completion alerts.",
      testButton: "Send test notification",
      testTitle: "Friday",
      testBody: "This is a test notification.",
      notSupported: "Your browser does not support notifications.",
      secureContextRequired:
        "This address cannot use browser notifications. Use HTTPS, or access the Docker service through localhost / 127.0.0.1.",
      disableNotification: "Disable notification",
    },
    account: {
      profileTitle: "Profile",
      email: "Email",
      role: "Role",
      changePasswordTitle: "Change Password",
      changePasswordDescription: "Update your account password.",
      currentPassword: "Current password",
      newPassword: "New password",
      confirmNewPassword: "Confirm new password",
      passwordMismatch: "New passwords do not match",
      passwordTooShort: "Password must be at least 8 characters",
      passwordChangedSuccess: "Password changed successfully",
      networkError: "Network error. Please try again.",
      updating: "Updating...",
      updatePassword: "Update Password",
      signOut: "Sign Out",
    },
    acknowledge: {
      emptyTitle: "Acknowledgements",
      emptyDescription: "Credits and acknowledgements will show here.",
    },
  },
};
