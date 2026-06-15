import { useEffect, useRef } from "react";

/**
 * Manages a single highlight-timeout lifecycle.
 *
 * Calling ``highlight(setPaths, paths)`` sets the highlighted paths and
 * schedules them to be cleared after ``delay`` ms.  If the component
 * unmounts or ``highlight`` is called again before the timeout fires, the
 * pending timeout is cancelled so state setters never run on an unmounted
 * component.
 */
export function useHighlightTimeout() {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const highlight = (
    setHighlightedPaths: (paths: Set<string>) => void,
    paths: Set<string>,
    delay = 4000,
  ) => {
    setHighlightedPaths(paths);
    if (timerRef.current) {
      clearTimeout(timerRef.current);
    }
    timerRef.current = setTimeout(() => {
      setHighlightedPaths(new Set());
      timerRef.current = null;
    }, delay);
  };

  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, []);

  return highlight;
}
