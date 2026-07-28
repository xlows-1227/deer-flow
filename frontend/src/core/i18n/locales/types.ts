import type { LucideIcon } from "lucide-react";

export interface Translations {
  // Locale meta
  locale: {
    localName: string;
  };

  // Common
  common: {
    home: string;
    settings: string;
    delete: string;
    edit: string;
    rename: string;
    share: string;
    openInNewWindow: string;
    close: string;
    more: string;
    search: string;
    loadMore: string;
    download: string;
    downloadSuccess: string;
    downloadFailed: string;
    thinking: string;
    artifacts: string;
    public: string;
    custom: string;
    notAvailableInDemoMode: string;
    loading: string;
    version: string;
    lastUpdated: string;
    code: string;
    preview: string;
    cancel: string;
    save: string;
    install: string;
    create: string;
    import: string;
    export: string;
    exportAsMarkdown: string;
    exportAsJSON: string;
    exportSuccess: string;
    view: string;
  };

  home: {
    docs: string;
    blog: string;
  };

  // Welcome
  welcome: {
    greeting: string;
    description: string;
    createYourOwnSkill: string;
    createYourOwnSkillDescription: string;
  };

  // Clipboard
  clipboard: {
    copyToClipboard: string;
    copiedToClipboard: string;
    failedToCopyToClipboard: string;
    linkCopied: string;
  };

  // Input Box
  inputBox: {
    placeholder: string;
    createSkillPrompt: string;
    addAttachments: string;
    mode: string;
    flashMode: string;
    flashModeDescription: string;
    reasoningMode: string;
    reasoningModeDescription: string;
    proMode: string;
    proModeDescription: string;
    ultraMode: string;
    ultraModeDescription: string;
    reasoningEffort: string;
    reasoningEffortMinimal: string;
    reasoningEffortMinimalDescription: string;
    reasoningEffortLow: string;
    reasoningEffortLowDescription: string;
    reasoningEffortMedium: string;
    reasoningEffortMediumDescription: string;
    reasoningEffortHigh: string;
    reasoningEffortHighDescription: string;
    skill: string;
    noSkill: string;
    noSkillDescription: string;
    connector: string;
    noConnector: string;
    noConnectorDescription: string;
    slashSkillPickerTitle: string;
    slashSkillPickerHint: string;
    slashSkillPickerEmpty: string;
    slashSkillPickerKeep: string;
    slashCommandModel: string;
    slashCommandModelDescription: string;
    slashCommandClear: string;
    slashCommandClearDescription: string;
    slashCommandHelp: string;
    slashCommandHelpDescription: string;
    slashCommandHelpTitle: string;
    slashCommandHelpIntro: string;
    slashCommandHelpSkillRow: string;
    slashCommandHelpModeRow: string;
    slashCommandHelpModelRow: string;
    slashCommandHelpClearRow: string;
    mentionFilePickerTitle: string;
    mentionFilePickerHint: string;
    mentionFilePickerEmpty: string;
    mentionFilePickerLoading: string;
    mentionFilePickerError: string;
    mentionFilePickerNoFiles: string;
    referencedFileChipRemove: string;
    referencedFileFromLibrary: string;
    referencedFileOpenInLibrary: string;
    searchModels: string;
    surpriseMe: string;
    surpriseMePrompt: string;
    followupLoading: string;
    followupConfirmTitle: string;
    followupConfirmDescription: string;
    followupConfirmAppend: string;
    followupConfirmReplace: string;
    suggestions: {
      suggestion: string;
      prompt: string;
      icon: LucideIcon;
    }[];
    suggestionsCreate: (
      | {
          suggestion: string;
          prompt: string;
          icon: LucideIcon;
        }
      | {
          type: "separator";
        }
    )[];
  };

  // Sidebar
  sidebar: {
    recentChats: string;
    viewAllChats: string;
    newChat: string;
    chats: string;
    demoChats: string;
    agents: string;
    skills: string;
    files: string;
    memory: string;
    scheduledTasks: string;
    collapseSidebar: string;
    expandSidebar: string;
    taskRecords: string;
    taskRecordsEmpty: string;
    taskRecordsLoadFailed: string;
    taskRecordsOpen: string;
    taskRunStatus: {
      running: string;
      success: string;
      error: string;
      cancelled: string;
    };
  };

  // Agents
  agents: {
    title: string;
    description: string;
    newAgent: string;
    emptyTitle: string;
    emptyDescription: string;
    chat: string;
    delete: string;
    deleteConfirm: string;
    deleteSuccess: string;
    newChat: string;
    createPageTitle: string;
    createPageSubtitle: string;
    nameStepTitle: string;
    nameStepHint: string;
    nameStepPlaceholder: string;
    nameStepContinue: string;
    nameStepInvalidError: string;
    nameStepAlreadyExistsError: string;
    nameStepNetworkError: string;
    nameStepCheckError: string;
    nameStepApiDisabledError: string;
    nameStepBootstrapMessage: string;
    save: string;
    saving: string;
    saveRequested: string;
    saveHint: string;
    saveCommandMessage: string;
    agentCreatedPendingRefresh: string;
    more: string;
    agentCreated: string;
    startChatting: string;
    backToGallery: string;
  };

  // Published Agents
  publishedAgents: {
    gallery: {
      title: string;
      eyebrow: string;
      description: string;
      newAgent: string;
      createTitle: string;
      createDescription: string;
      slug: string;
      slugHint: string;
      displayName: string;
      descriptionLabel: string;
      descriptionPlaceholder: string;
      createDraft: string;
      creating: string;
      createSuccess: string;
      emptyTitle: string;
      emptyDescription: string;
      loadError: string;
      retry: string;
      studio: string;
      actions: string;
      suspend: string;
      resume: string;
      archive: string;
      confirmSuspendTitle: string;
      confirmSuspendDescription: string;
      confirmArchiveTitle: string;
      confirmArchiveDescription: string;
      statusUpdated: string;
      release: (releaseNo: number) => string;
      noRelease: string;
      publishedAt: string;
      apiKeyCount: (count: number) => string;
      feishuCount: (count: number) => string;
      runsCount: (count: number) => string;
      tokensCount: (count: number) => string;
      usage7d: string;
      integrations: string;
      health: string;
    };
    status: {
      draft: string;
      published: string;
      suspended: string;
      archived: string;
    };
    health: {
      healthy: string;
      unhealthy: string;
      unknown: string;
      notConfigured: string;
    };
    studio: {
      eyebrow: string;
      back: string;
      draftVersion: (revision: number) => string;
      saveDraft: string;
      saving: string;
      saved: string;
      conflictTitle: string;
      reloadDraft: string;
      overviewTab: string;
      instructionsTab: string;
      skillsTab: string;
      connectorsTab: string;
      sandboxTab: string;
      publishTab: string;
      integrationsTab: string;
      operationsTab: string;
      overviewTitle: string;
      overviewDescription: string;
      stableIdentity: string;
      stableIdentityDescription: string;
      slug: string;
      displayName: string;
      description: string;
      avatar: string;
      notConfigured: string;
      model: string;
      inheritModel: string;
      instructionsTitle: string;
      instructionsDescription: string;
      agentMarkdownTitle: string;
      agentMarkdownDescription: string;
      agentMarkdownSuggestions: string[];
      agentMarkdownPlaceholder: string;
      agentMarkdownTemplate: string;
      instructionSuggestionsLabel: string;
      soulPresetTitle: string;
      soulPresetDescription: string;
      soulPresetBadge: string;
      soulPresetLegacyTitle: string;
      soulPresetLegacyDescription: string;
      soulPresetLegacyPreviewLabel: string;
      soulPresets: {
        professional: {
          name: string;
          summary: string;
          content: string;
        };
        warm: {
          name: string;
          summary: string;
          content: string;
        };
        concise: {
          name: string;
          summary: string;
          content: string;
        };
        coach: {
          name: string;
          summary: string;
          content: string;
        };
      };
      skillsTitle: string;
      skillsDescription: string;
      publicSkills: string;
      privateSkills: string;
      emptySkills: string;
      skillSearchLabel: string;
      skillSearchPlaceholder: string;
      skillSearchSummary: (
        visible: number,
        total: number,
        selected: number,
      ) => string;
      noMatchingSkills: string;
      clearSkillSearch: string;
      connectorRequirements: string;
      noConnectorRequired: string;
      missingGrant: string;
      granted: string;
      connectorsTitle: string;
      connectorsDescription: string;
      emptyConnectors: string;
      disabledConnector: string;
      grantCapability: string;
      revokeCapability: string;
      sandboxTitle: string;
      sandboxDescription: string;
      notLive: string;
      sandboxSafety: string;
      sandboxMessageLabel: string;
      sandboxMessagePlaceholder: string;
      runSandbox: string;
      runningSandbox: string;
      sandboxStarted: (revision: number) => string;
      openSandbox: string;
    };
    publish: {
      title: string;
      description: string;
      loadError: string;
      savedDraftOnly: string;
      initialSummary: string;
      unsavedTitle: string;
      unsavedDescription: string;
      neverPublished: string;
      changeSummary: string;
      publish: string;
      publishing: string;
      successTitle: (releaseNo: number) => string;
      successDescription: string;
      validationTitle: string;
      validationDescription: string;
      violation: (code: string, fallback: string) => string;
      unchanged: string;
      added: string;
      removed: string;
      model: string;
      defaultModel: string;
      toolGroups: string;
      skills: string;
      connectorGrants: string;
      historyTitle: string;
      historyDescription: string;
      historyEmpty: string;
      createdBy: string;
      current: string;
      compareTitle: string;
      compareDescription: string;
      compareFrom: string;
      compareTo: string;
      fromTo: (from: number, to: number) => string;
      rollback: string;
      rollbackTitle: (releaseNo: number) => string;
      rollbackDescription: string;
      stableIntegrationNotice: string;
      cancel: string;
      confirmRollback: string;
      rollingBack: string;
      rollbackSuccess: (releaseNo: number) => string;
    };
    integrations: {
      title: string;
      description: string;
      apiKeysTitle: string;
      apiKeysDescription: string;
      createApiKey: string;
      publishFirstTitle: string;
      publishFirstDescription: string;
      loading: string;
      noKeys: string;
      lastUsed: string;
      copyKeyFor: (name: string) => string;
      copyKeyUnavailable: string;
      deleteKey: (name: string) => string;
      copy: string;
      delete: string;
      apiExamplesTitle: string;
      apiExamplesDescription: string;
      sync: string;
      sse: string;
      async: string;
      copyExample: string;
      createKeyTitle: string;
      createKeyDescription: string;
      keyName: string;
      createKey: string;
      secretTitle: string;
      secretDescription: string;
      secretOnce: string;
      secretWarning: string;
      copyKey: string;
      keyCopied: string;
      storedKey: string;
      deleteTitle: string;
      deleteDescription: string;
      confirmDelete: string;
      keyDeleted: string;
      keyStatus: (status: string) => string;
      feishuTitle: string;
      feishuDescription: string;
      addBinding: string;
      channelLoadError: string;
      noBindings: string;
      channelHealth: (health: string) => string;
      channelStatus: (status: string) => string;
      noHealthDetail: string;
      testConnection: string;
      stop: string;
      start: string;
      restart: string;
      rotateCredentials: string;
      createBindingTitle: string;
      createBindingDescription: string;
      appId: string;
      appSecret: string;
      verificationToken: string;
      encryptKey: string;
      keepAppId: string;
      createBinding: string;
      bindingCreated: string;
      rotateCredentialsTitle: string;
      rotateCredentialsDescription: string;
      confirmCredentialRotation: string;
      credentialsRotated: string;
      channelActionSuccess: (action: string) => string;
    };
    ops: {
      title: string;
      description: string;
      usageTitle: string;
      usageDescription: string;
      dateRange: string;
      lastDays: (days: number) => string;
      source: string;
      allSources: string;
      apiKey: string;
      allKeys: string;
      totalRuns: string;
      totalTokens: string;
      errorRate: string;
      estimatedCost: string;
      currentReleaseErrorRate: string;
      quotaRejections: string;
      saturation: string;
      feishuP95Latency: string;
      connectorIssues: string;
      denied: string;
      bindingHealth: string;
      unhealthy: string;
      dailyRuns: string;
      dailyTokens: string;
      dailyErrorRate: string;
      auditTitle: string;
      auditDescription: string;
      loadingAudit: string;
      noRejections: string;
      auditCategory: (category: string) => string;
      quotaTitle: string;
      quotaDescription: string;
      saveQuota: string;
      savingQuota: string;
      inheritanceTitle: string;
      inheritanceDescription: string;
      saveOtherDraftTitle: string;
      saveOtherDraftDescription: string;
      quotaLoadError: string;
      quotaOverrideLabel: (field: string) => string;
      inherited: string;
      overridden: string;
      platformDefault: string;
      effectiveAfterPublish: string;
      exceedsMaximum: (maximum: number) => string;
      positiveInteger: string;
      draftOnlyNotice: string;
      quotaSaved: string;
      quotaConflict: string;
    };
  };

  // Breadcrumb
  breadcrumb: {
    workspace: string;
    chats: string;
  };

  // Workspace
  workspace: {
    officialWebsite: string;
    githubTooltip: string;
    settingsAndMore: string;
    visitGithub: string;
    reportIssue: string;
    contactUs: string;
    about: string;
    logout: string;
  };

  // Conversation
  conversation: {
    noMessages: string;
    startConversation: string;
    memoryRollup: string;
    memoryRollupSuccess: string;
    memoryRollupEmpty: string;
    memoryRollupFailed: string;
  };

  // Chats
  chats: {
    searchChats: string;
  };

  // Page titles (document title)
  pages: {
    appName: string;
    chats: string;
    newChat: string;
    untitled: string;
  };

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => string;
    lessSteps: string;
    executeCommand: string;
    presentFiles: string;
    needYourHelp: string;
    useTool: (toolName: string) => string;
    searchForRelatedInfo: string;
    searchForRelatedImages: string;
    searchFor: (query: string) => string;
    searchForRelatedImagesFor: (query: string) => string;
    searchOnWebFor: (query: string) => string;
    viewWebPage: string;
    listFolder: string;
    readFile: string;
    writeFile: string;
    clickToViewContent: string;
    writeTodos: string;
    skillInstallTooltip: string;
  };

  // Uploads
  uploads: {
    uploading: string;
    uploadingFiles: string;
  };

  // Subtasks
  subtasks: {
    subtask: string;
    executing: (count: number) => string;
    in_progress: string;
    completed: string;
    failed: string;
  };

  // Token Usage
  tokenUsage: {
    title: string;
    label: string;
    input: string;
    output: string;
    total: string;
    view: string;
    unavailable: string;
    unavailableShort: string;
    note: string;
    presets: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    presetDescriptions: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    finalAnswer: string;
    stepTotal: string;
    sharedAttribution: string;
    subagent: (description: string) => string;
    startTodo: (content: string) => string;
    completeTodo: (content: string) => string;
    updateTodo: (content: string) => string;
    removeTodo: (content: string) => string;
  };

  // Shortcuts
  shortcuts: {
    searchActions: string;
    noResults: string;
    actions: string;
    keyboardShortcuts: string;
    keyboardShortcutsDescription: string;
    openCommandPalette: string;
    toggleSidebar: string;
  };

  // Settings
  settings: {
    title: string;
    description: string;
    sections: {
      account: string;
      appearance: string;
      memory: string;
      connectors: string;
      models: string;
      tools: string;
      skills: string;
      notification: string;
      about: string;
    };
    memory: {
      title: string;
      description: string;
      empty: string;
      rawJson: string;
      exportButton: string;
      exportSuccess: string;
      importButton: string;
      importConfirmTitle: string;
      importConfirmDescription: string;
      importFileLabel: string;
      importInvalidFile: string;
      importSuccess: string;
      manualFactSource: string;
      addFact: string;
      addFactTitle: string;
      editFactTitle: string;
      addFactSuccess: string;
      editFactSuccess: string;
      clearAll: string;
      clearAllConfirmTitle: string;
      clearAllConfirmDescription: string;
      clearAllSuccess: string;
      factDeleteConfirmTitle: string;
      factDeleteConfirmDescription: string;
      factDeleteSuccess: string;
      factContentLabel: string;
      factCategoryLabel: string;
      factConfidenceLabel: string;
      factContentPlaceholder: string;
      factCategoryPlaceholder: string;
      factConfidenceHint: string;
      factSave: string;
      factValidationContent: string;
      factValidationConfidence: string;
      noFacts: string;
      memoryFullyEmpty: string;
      factPreviewLabel: string;
      rollupDaily: string;
      rollupDailySuccess: string;
      rollupDailyEmpty: string;
      dailyDeleteConfirmTitle: string;
      dailyDeleteConfirmDescription: string;
      dailyDeleteSuccess: string;
      dailyDeletePreviewLabel: string;
      markdown: {
        facts: string;
        table: {
          category: string;
          confidence: string;
          confidenceLevel: {
            veryHigh: string;
            high: string;
            normal: string;
            unknown: string;
          };
          source: string;
          createdAt: string;
        };
        export: {
          longTermProfile: string;
          dailySummary: string;
          preferences: string;
          communicationStyle: string;
          skillUsagePatterns: string;
          interests: string;
          topOfMind: string;
          corrections: string;
          updatedAt: string;
        };
      };
    };
    models: {
      title: string;
      description: string;
      hint: string;
      empty: string;
      addModel: string;
      editModel: string;
      formDescription: string;
      name: string;
      namePlaceholder: string;
      displayName: string;
      displayNamePlaceholder: string;
      provider: string;
      modelId: string;
      modelIdPlaceholder: string;
      baseUrl: string;
      apiKey: string;
      apiKeyPlaceholder: string;
      apiKeyKeepExisting: string;
      apiKeySet: string;
      apiKeyMissing: string;
      enabled: string;
      enabledDescription: string;
      supportsThinking: string;
      supportsThinkingDescription: string;
      supportsReasoningEffort: string;
      supportsReasoningEffortDescription: string;
      reasoningEffortOverride: string;
      reasoningEffortOverrideDescription: string;
      reasoningEffortAuto: string;
      disabled: string;
      loadFailed: string;
      saveFailed: string;
      createSuccess: string;
      updateSuccess: string;
      deleteSuccess: string;
      deleteFailed: string;
      deleteConfirm: string;
      validationRequired: string;
    };
    appearance: {
      themeTitle: string;
      themeDescription: string;
      system: string;
      light: string;
      dark: string;
      systemDescription: string;
      lightDescription: string;
      darkDescription: string;
      languageTitle: string;
      languageDescription: string;
    };
    tools: {
      title: string;
      description: string;
      tabs: {
        imageGeneration: string;
        mcp: string;
      };
      mcp: {
        systemBadge: string;
        userBadge: string;
        readOnlyHint: string;
      };
      imageGeneration: {
        title: string;
        description: string;
        loading: string;
        loadFailed: string;
        noConfig: string;
        enableTool: string;
        enableToolDescription: string;
        defaultProvider: string;
        outputDir: string;
        selectModel: string;
        enabled: string;
        disabled: string;
        keyConfigured: string;
        defaultModel: string;
        apiKey: string;
        keepExistingKey: string;
        enterApiKey: string;
        baseUrl: string;
        timeoutSeconds: string;
        saveConfig: string;
        saving: string;
        saveSuccess: string;
        retry: string;
        adapter: string;
      };
    };
    connectors: {
      title: string;
      description: string;
      total: string;
      active: string;
      availableTypes: string;
      refresh: string;
      add: string;
      loading: string;
      emptyTitle: string;
      emptyDescription: string;
      statusActive: string;
      statusDisabled: string;
      test: string;
      testConnection: string;
      edit: string;
      enable: string;
      disable: string;
      delete: string;
      policy: string;
      lastTested: string;
      lastUsed: string;
      never: string;
      maxRows: (count: number) => string;
      createTitle: string;
      editTitle: string;
      createDescription: string;
      editDescription: string;
      name: string;
      displayName: string;
      type: string;
      host: string;
      port: string;
      database: string;
      ssl: string;
      secretBoundary: string;
      authMode: string;
      authModeEnv: string;
      authModeInline: string;
      credentialRef: string;
      username: string;
      password: string;
      passwordPlaceholder: string;
      credentialUpdateHint: string;
      credentialUpdateHintInline: string;
      maxRowsLabel: string;
      allowedSchemas: string;
      allowedSchemasPlaceholder: string;
      cancel: string;
      create: string;
      save: string;
      created: string;
      updated: string;
      enabled: string;
      disabled: string;
      deleted: string;
      testSuccess: string;
      testSuccessWithLatency: (latencyMs: number) => string;
      testFailed: string;
      deleteConfirm: (name: string) => string;
      validationName: string;
      validationHost: string;
      validationDatabase: string;
      validationCredentialRef: string;
      validationUsername: string;
      validationPassword: string;
    };
    skills: {
      title: string;
      description: string;
      createSkill: string;
      emptyTitle: string;
      emptyDescription: string;
      emptyButton: string;
    };
    notification: {
      title: string;
      description: string;
      requestPermission: string;
      deniedHint: string;
      testButton: string;
      testTitle: string;
      testBody: string;
      notSupported: string;
      secureContextRequired: string;
      disableNotification: string;
    };
    account: {
      profileTitle: string;
      email: string;
      role: string;
      changePasswordTitle: string;
      changePasswordDescription: string;
      currentPassword: string;
      newPassword: string;
      confirmNewPassword: string;
      passwordMismatch: string;
      passwordTooShort: string;
      passwordChangedSuccess: string;
      networkError: string;
      updating: string;
      updatePassword: string;
      signOut: string;
    };
    acknowledge: {
      emptyTitle: string;
      emptyDescription: string;
    };
  };
}
