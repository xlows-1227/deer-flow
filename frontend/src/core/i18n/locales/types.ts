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
