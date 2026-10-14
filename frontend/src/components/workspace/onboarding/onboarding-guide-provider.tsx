"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { usePathname } from "next/navigation";

import { loadCustomModels } from "@/core/models/config-api";
import { useI18n } from "@/core/i18n/hooks";

import { ModelOnboardingGuide } from "./model-onboarding-guide";

type Step = 1 | 2 | 3 | 4;

type OnboardingState = {
  /** Whether the guide is currently active */
  step: Step | null;
  /** Advance to next step (called when user clicks the highlighted target) */
  advanceStep: () => void;
  /** Exit the guide completely */
  exitGuide: () => void;
  /** Clear the onboarding flag (called after Gaia model is saved) */
  clearOnboarding: () => void;
  /** Set the onboarding flag (called when last Gaia model is deleted) */
  markOnboardingNeeded: () => void;
};

const OnboardingGuideContext = createContext<OnboardingState>({
  step: null,
  advanceStep: () => {},
  exitGuide: () => {},
  clearOnboarding: () => {},
  markOnboardingNeeded: () => {},
});

export function useOnboardingGuide() {
  return useContext(OnboardingGuideContext);
}

/** localStorage key — persists until user saves a Gaia model */
const STORAGE_KEY = "yumc_onboarding_needed";

/**
 * Called from the login page after successful login.
 * Checks if the user has any Gaia-protocol model; if not,
 * sets a persistent localStorage flag so every workspace page
 * can check and prompt the user until a Gaia model is saved.
 */
export async function checkAndPromptOnboarding(): Promise<boolean> {
  try {
    const models = await loadCustomModels();
    const hasGaia = models.some((m) => m.provider === "gaia");
    if (hasGaia) {
      localStorage.removeItem(STORAGE_KEY);
      return false;
    }
    localStorage.setItem(STORAGE_KEY, "1");
    return true;
  } catch {
    return false;
  }
}

export function OnboardingGuideProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const { t } = useI18n();
  const pathname = usePathname();
  const [step, setStep] = useState<Step | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);

  // Check localStorage on every route change (NOT on step changes)
  // If flag is set and guide is not active, show confirmation dialog
  useEffect(() => {
    if (step) return; // Guide already active, don't interrupt
    const flag = localStorage.getItem(STORAGE_KEY);
    if (flag) {
      setShowConfirm(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  const startGuide = useCallback(() => {
    setStep(1);
  }, []);

  const exitGuide = useCallback(() => {
    setStep(null);
  }, []);

  const advanceStep = useCallback(() => {
    setStep((prev) => {
      if (prev === null) return null;
      if (prev >= 4) return null; // Done after step 4
      return (prev + 1) as Step;
    });
  }, []);

  const clearOnboarding = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setStep(null);
  }, []);

  const markOnboardingNeeded = useCallback(() => {
    localStorage.setItem(STORAGE_KEY, "1");
  }, []);

  // Listen for target clicks via custom events
  useEffect(() => {
    if (!step) return;
    const handler = () => advanceStep();
    window.addEventListener("onboarding:advance", handler);
    return () => window.removeEventListener("onboarding:advance", handler);
  }, [step, advanceStep]);

  return (
    <OnboardingGuideContext.Provider
      value={{ step, advanceStep, exitGuide, clearOnboarding, markOnboardingNeeded }}
    >
      {children}
      {showConfirm && (
        <ConfirmDialog
          title={t.onboarding?.confirmTitle ?? "配置 YUMC 模型"}
          message={
            t.onboarding?.confirmMessage ??
            "将引导配置 YUMC 模型，确认前往吗？"
          }
          confirmText={t.onboarding?.confirm ?? "确认"}
          cancelText={t.common?.cancel ?? "取消"}
          onConfirm={() => {
            setShowConfirm(false);
            startGuide();
          }}
          onCancel={() => setShowConfirm(false)}
        />
      )}
      {step && <ModelOnboardingGuide step={step} onExit={exitGuide} />}
    </OnboardingGuideContext.Provider>
  );
}

/** Simple confirmation dialog */
function ConfirmDialog({
  title,
  message,
  confirmText,
  cancelText,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmText: string;
  cancelText: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[10010] flex items-center justify-center bg-black/50">
      <div className="mx-4 w-full max-w-sm rounded-xl bg-white p-6 shadow-2xl">
        <h3 className="mb-2 text-lg font-semibold text-gray-900">{title}</h3>
        <p className="mb-6 text-sm text-gray-600">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            type="button"
            className="rounded-lg border border-gray-300 px-4 py-1.5 text-sm text-gray-700 transition hover:bg-gray-50"
            onClick={onCancel}
          >
            {cancelText}
          </button>
          <button
            type="button"
            className="rounded-lg bg-gray-900 px-4 py-1.5 text-sm text-white transition hover:bg-gray-800"
            onClick={onConfirm}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
