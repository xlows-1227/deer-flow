"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { useI18n } from "@/core/i18n/hooks";

/** Target element IDs for each onboarding step */
export const ONBOARDING_TARGETS = {
  settingsButton: "onboarding-settings-button",
  modelsTab: "onboarding-models-tab",
  addModelButton: "onboarding-add-model-button",
  addModelForm: "onboarding-add-model-form",
} as const;

type Step = 1 | 2 | 3 | 4;

const TOTAL_STEPS = 4;

const STEP_HINTS: Record<Step, string> = {
  1: "点击「设置和更多」打开设置",
  2: "点击「模型」进入模型配置",
  3: "点击「添加模型」开始配置",
  4: "填写模型信息后点击保存",
};

type Rect = { top: number; left: number; width: number; height: number };

function getTargetRect(id: string): Rect | null {
  const el = document.getElementById(id);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

const pad = 6;

function getStepTargetId(step: Step): string {
  switch (step) {
    case 1:
      return ONBOARDING_TARGETS.settingsButton;
    case 2:
      return ONBOARDING_TARGETS.modelsTab;
    case 3:
      return ONBOARDING_TARGETS.addModelButton;
    case 4:
      return ONBOARDING_TARGETS.addModelForm;
  }
}

/** Blocks clicks on everything except the spotlight area.
 *  The strips carry the dark overlay themselves — no SVG mask needed,
 *  which avoids url(#id) reference issues inside React portals. */
function ClickBlocker({ rect }: { rect: Rect }) {
  // Four divs surrounding the target, leaving a hole
  const topH = Math.max(0, rect.top - pad);
  const leftW = Math.max(0, rect.left - pad);
  const rightEdge = rect.left + rect.width + pad;
  const bottomEdge = rect.top + rect.height + pad;
  const bottomH = Math.max(0, window.innerHeight - bottomEdge);
  const rightW = Math.max(0, window.innerWidth - rightEdge);

  const bg = "rgba(0,0,0,0.72)";

  return (
    <div className="fixed inset-0 z-[9998]" style={{ pointerEvents: "none" }}>
      {/* Top strip */}
      <div
        className="absolute left-0 right-0 top-0"
        style={{ height: topH, pointerEvents: "auto", background: bg }}
      />
      {/* Bottom strip */}
      <div
        className="absolute bottom-0 left-0 right-0"
        style={{ height: bottomH, pointerEvents: "auto", background: bg }}
      />
      {/* Left strip (between top and bottom) */}
      <div
        className="absolute"
        style={{
          top: topH,
          left: 0,
          width: leftW,
          height: rect.height + pad * 2,
          pointerEvents: "auto",
          background: bg,
        }}
      />
      {/* Right strip */}
      <div
        className="absolute"
        style={{
          top: topH,
          right: 0,
          width: rightW,
          height: rect.height + pad * 2,
          pointerEvents: "auto",
          background: bg,
        }}
      />
    </div>
  );
}

export function ModelOnboardingGuide({
  step,
  onExit,
}: {
  step: Step | null;
  onExit: () => void;
}) {
  const { t } = useI18n();
  const [rect, setRect] = useState<Rect | null>(null);
  const [imgZoomed, setImgZoomed] = useState(false);
  const [mounted, setMounted] = useState(false);

  // Portal target only exists on the client
  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!step) {
      setRect(null);
      return;
    }
    const targetId = getStepTargetId(step);
    const measure = () => {
      let r = getTargetRect(targetId);
      // For the "add model form" step we need the target to exist.  In the
      // initial frames the Dialog hasn't opened yet (step 3 just advanced),
      // so the element might be missing.  Wait until it appears.
      if (step === 4 && !r) {
        // form element not mounted yet — keep trying every 100 ms
        setRect(null);
        return;
      }
      setRect(r);
    };
    const timer = setTimeout(measure, 100);
    const interval = setInterval(measure, 500);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [step]);

  // Event-level click guard. DOM overlays alone are unreliable here: the
  // settings dialog (Radix) portals to body and manages pointer-events, so
  // depending on stacking/portal order clicks can slip past the visual
  // mask. Capture-phase listeners on document block any interaction whose
  // target is neither inside the spotlight element nor the guide's own UI.
  useEffect(() => {
    if (!step) return;
    const targetId = getStepTargetId(step);

    const isAllowed = (target: EventTarget | null): boolean => {
      if (!(target instanceof Element)) return false;
      // The guide's own controls (exit button, step-4 card, lightbox)
      if (target.closest("[data-onboarding-ui]")) return true;
      // The spotlight target and anything inside it
      const el = document.getElementById(targetId);
      return el ? el.contains(target) : false;
    };

    const blockPointer = (e: Event) => {
      if (!isAllowed(e.target)) {
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    };
    // Prevent dismissing dialogs via Escape mid-guide (leaves the guide
    // pointing at an unmounted element)
    const blockEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    };

    document.addEventListener("pointerdown", blockPointer, true);
    document.addEventListener("pointerup", blockPointer, true);
    document.addEventListener("click", blockPointer, true);
    document.addEventListener("keydown", blockEscape, true);
    return () => {
      document.removeEventListener("pointerdown", blockPointer, true);
      document.removeEventListener("pointerup", blockPointer, true);
      document.removeEventListener("click", blockPointer, true);
      document.removeEventListener("keydown", blockEscape, true);
    };
  }, [step]);

  if (!step || !mounted) return null;

  // Render via portal directly on document.body so the overlay's z-index
  // competes at top level — otherwise ancestor stacking contexts (e.g.
  // transform on layout containers) let Radix Dialog (z-50, portaled to
  // body) render ABOVE the click blocker and clicks pass through.
  return createPortal(
    <>
      {/* Click blocker strips double as the dark visual overlay (no SVG
           mask — url(#id) refs are unreliable inside React portals).
           Falls back to a full-screen block while the target is missing. */}
      {rect ? (
        <ClickBlocker rect={rect} />
      ) : (
        <div
          className="fixed inset-0 z-[9998]"
          style={{
            pointerEvents: "auto",
            background: "rgba(0,0,0,0.72)",
          }}
        />
      )}

      {/* Spotlight border (red dashed) */}
      {rect && (
        <div
          className="pointer-events-none fixed z-[9999] rounded-lg border-2 border-dashed border-red-500"
          style={{
            top: rect.top - pad,
            left: rect.left - pad,
            width: rect.width + pad * 2,
            height: rect.height + pad * 2,
          }}
        />
      )}

      {/* Arrow (red, curved from upper-left to target) */}
      {rect && (
        <svg
          className="pointer-events-none fixed z-[9999]"
          style={{
            top: Math.max(8, rect.top - 90),
            left: Math.max(8, rect.left - 60),
            width: 80,
            height: 100,
          }}
          viewBox="0 0 80 100"
        >
          <defs>
            <marker
              id="arrowhead"
              markerWidth="10"
              markerHeight="8"
              refX="8"
              refY="4"
              orient="auto"
            >
              <polygon points="0 0, 10 4, 0 8" fill="#ef4444" />
            </marker>
          </defs>
          <path
            d="M 8 8 Q 40 8, 30 40 T 52 88"
            stroke="#ef4444"
            strokeWidth="3"
            fill="none"
            markerEnd="url(#arrowhead)"
          />
        </svg>
      )}

      {/* Step indicator + exit (top center, always clickable) */}
      <div
        data-onboarding-ui=""
        className="pointer-events-auto fixed top-4 left-1/2 z-[10001] flex -translate-x-1/2 items-center gap-3 rounded-full bg-black/85 px-4 py-2 text-sm text-white shadow-lg"
      >
        <span className="font-medium text-orange-400">
          {step}/{TOTAL_STEPS}
        </span>
        <span className="text-white/40">|</span>
        <button
          type="button"
          className="cursor-pointer text-orange-400 transition hover:text-orange-300"
          onClick={(e) => {
            e.stopPropagation();
            onExit();
          }}
        >
          {t.onboarding?.exitGuide ?? "退出引导"}
        </button>
      </div>

      {/* Hint text (below target) */}
      {rect && (
        <div
          className="pointer-events-none fixed z-[9999] max-w-xs rounded-lg bg-red-500 px-3 py-1.5 text-xs text-white shadow-lg"
          style={{
            top: rect.top + rect.height + pad + 12,
            left: Math.min(
              rect.left,
              typeof window !== "undefined" ? window.innerWidth - 240 : rect.left,
            ),
          }}
        >
          {STEP_HINTS[step]}
        </div>
      )}

      {/* Step 4: Extra guidance for API Key — show on the RIGHT side of dialog,
           fall back to LEFT side if not enough space */}
      {step === 4 && rect && (
        <div
          data-onboarding-ui=""
          className="pointer-events-auto fixed z-[9999] max-w-sm rounded-lg bg-white/95 px-4 py-3 text-xs text-gray-700 shadow-xl backdrop-blur-sm dark:bg-gray-800/95 dark:text-gray-200"
          style={{
            top: rect.top + rect.height / 2,
            left:
              typeof window !== "undefined" &&
              rect.left + rect.width + pad + 16 + 320 > window.innerWidth
                ? Math.max(8, rect.left - 320 - pad - 16)
                : rect.left + rect.width + pad + 16,
            transform: "translateY(-50%)",
          }}
        >
          <div className="flex flex-col gap-2">
            <div className="font-medium text-gray-900 dark:text-gray-100">
              {t.onboarding?.step4GetKey ?? "获取 API Key"}
            </div>
            <a
              href="https://gaia.hwwt2.com/nextg/user/settings/tokens"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 underline hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300"
            >
              {t.onboarding?.step4GetKeyLink ?? "前往 GAIA 平台获取个人令牌"}
            </a>
            <div className="mt-1 rounded-md bg-gray-100 p-2 dark:bg-gray-700">
              <div className="mb-1 font-medium text-gray-600 dark:text-gray-300">
                {t.onboarding?.step4Example ?? "API Key 示例"}
              </div>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/images/onboarding/gaia-token-example.png"
                alt="GAIA token example"
                className="w-full cursor-zoom-in rounded border border-gray-200 transition hover:opacity-90 dark:border-gray-600"
                onClick={(e) => {
                  e.stopPropagation();
                  setImgZoomed(true);
                }}
              />
              <div className="mt-1 text-[10px] text-gray-400 dark:text-gray-500">
                点击图片可放大查看
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Zoomed example image — fullscreen lightbox, click anywhere to close */}
      {imgZoomed && (
        <div
          data-onboarding-ui=""
          className="pointer-events-auto fixed inset-0 z-[10002] flex cursor-zoom-out items-center justify-center bg-black/85 p-8"
          onClick={() => setImgZoomed(false)}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/images/onboarding/gaia-token-example.png"
            alt="GAIA token example (zoomed)"
            className="max-h-full max-w-full rounded-lg border border-white/20 shadow-2xl"
          />
        </div>
      )}
    </>,
    document.body,
  );
}
