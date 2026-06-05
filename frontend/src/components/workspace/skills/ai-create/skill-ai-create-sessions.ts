export interface SkillAiCreateSessionRecord {
  threadId: string;
  title: string;
  skillName: string | null;
  published: boolean;
  updatedAt: number;
}

const STORAGE_KEY = "skill-ai-create-session-history";

function readSessions(): SkillAiCreateSessionRecord[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as SkillAiCreateSessionRecord[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeSessions(sessions: SkillAiCreateSessionRecord[]) {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
}

export function upsertSkillAiCreateSession(
  record: Omit<SkillAiCreateSessionRecord, "updatedAt"> & {
    updatedAt?: number;
  },
) {
  const sessions = readSessions();
  const next: SkillAiCreateSessionRecord = {
    ...record,
    updatedAt: record.updatedAt ?? Date.now(),
  };
  const index = sessions.findIndex((item) => item.threadId === record.threadId);
  if (index >= 0) {
    sessions[index] = { ...sessions[index], ...next };
  } else {
    sessions.unshift(next);
  }
  writeSessions(
    sessions.sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 50),
  );
}

export function getSkillAiCreateSession(
  threadId: string,
): SkillAiCreateSessionRecord | null {
  return readSessions().find((item) => item.threadId === threadId) ?? null;
}

export function listSkillAiCreateSessions() {
  return readSessions();
}

export function markSkillAiCreateSessionPublished(threadId: string) {
  const sessions = readSessions();
  const index = sessions.findIndex((item) => item.threadId === threadId);
  if (index < 0) return;
  const current = sessions[index];
  if (!current) return;
  sessions[index] = { ...current, published: true, updatedAt: Date.now() };
  writeSessions(sessions);
}

export function listUnpublishedSkillAiCreateSessions(excludeThreadId?: string) {
  return readSessions().filter(
    (session) => !session.published && session.threadId !== excludeThreadId,
  );
}

export function deleteSkillAiCreateSession(threadId: string) {
  writeSessions(
    readSessions().filter((session) => session.threadId !== threadId),
  );
}
