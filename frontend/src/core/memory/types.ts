export interface MemoryFact {
  id: string;
  content: string;
  category: string;
  confidence: number;
  createdAt: string;
  source: string;
}

export interface MemoryFactInput {
  content: string;
  category: string;
  confidence: number;
}

export interface MemoryFactPatchInput {
  content?: string;
  category?: string;
  confidence?: number;
}

export interface UserMemory {
  version: string;
  lastUpdated: string;
  user: {
    workContext: {
      summary: string;
      updatedAt: string;
    };
    personalContext: {
      summary: string;
      updatedAt: string;
    };
    topOfMind: {
      summary: string;
      updatedAt: string;
    };
  };
  history: {
    recentMonths: {
      summary: string;
      updatedAt: string;
    };
    earlierContext: {
      summary: string;
      updatedAt: string;
    };
    longTermBackground: {
      summary: string;
      updatedAt: string;
    };
  };
  facts: MemoryFact[];
}

export interface MemorySourceRef {
  type: "daily" | "legacy" | "manual";
  id: string;
}

export interface MemoryProfileItem {
  id: string;
  type: string;
  content: string;
  confidence: number;
  sourceRefs: MemorySourceRef[];
  createdAt: string;
  updatedAt: string;
  status: "active" | "inactive";
}

export interface MemorySuppression {
  id: string;
  scope: "profile_item" | "topic" | "daily";
  targetId: string;
  reason: string;
  createdAt: string;
  createdBy: string;
}

export interface MemoryProfile {
  version: string;
  personId: string;
  updatedAt: string;
  overview: string;
  interests: MemoryProfileItem[];
  preferences: MemoryProfileItem[];
  communicationStyle: MemoryProfileItem[];
  skillUsagePatterns: MemoryProfileItem[];
  topOfMind: MemoryProfileItem[];
  corrections: MemoryProfileItem[];
  suppressions: MemorySuppression[];
}

export interface DailyPersonSummary {
  version: string;
  id: string;
  personId: string;
  date: string;
  timezone: string;
  summary: string;
  interests: string[];
  preferences: string[];
  profileSignals: string[];
  recentFocus: string[];
  skillUsagePatterns: string[];
  corrections: string[];
  sourceThreads: string[];
  sourceRuns: string[];
  status: "active" | "deleted";
  deletedAt?: string | null;
  updatedAt: string;
}
