"use client";

import { useEffect, useRef } from "react";

type ShortcutAction = () => void;

interface Shortcut {
  key: string;
  meta: boolean;
  shift?: boolean;
  action: ShortcutAction;
}

/**
 * Register global keyboard shortcuts on window.
 * Shortcuts are suppressed when focus is inside an input, textarea, or
 * contentEditable element - except for Cmd+K which always fires.
 *
 * The latest ``shortcuts`` array is stored in a ref so that callers do not
 * need to memoize the array on every render; the listener is registered once
 * and reads the ref at invocation time.
 */
export function useGlobalShortcuts(shortcuts: Shortcut[]) {
  const shortcutsRef = useRef(shortcuts);
  shortcutsRef.current = shortcuts;

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const meta = event.metaKey || event.ctrlKey;
      const currentShortcuts = shortcutsRef.current;

      for (const shortcut of currentShortcuts) {
        if (
          event.key.toLowerCase() === shortcut.key.toLowerCase() &&
          meta === shortcut.meta &&
          (shortcut.shift ?? false) === event.shiftKey
        ) {
          // Allow Cmd+K even in inputs (standard command palette behavior)
          if (shortcut.key !== "k") {
            const target = event.target as HTMLElement;
            const tag = target.tagName;
            if (
              tag === "INPUT" ||
              tag === "TEXTAREA" ||
              target.isContentEditable
            ) {
              continue;
            }
          }

          event.preventDefault();
          shortcut.action();
          return;
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);
}
