let originalWriteText: ((text: string) => Promise<void>) | null = null;
let clipboardPatched = false;

async function writeTextLegacy(text: string): Promise<boolean> {
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.top = "0";
    textarea.setAttribute("readonly", "");
    document.body.appendChild(textarea);
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    const success = document.execCommand("copy");
    document.body.removeChild(textarea);
    return success;
  } catch {
    return false;
  }
}

export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (typeof window === "undefined") {
    return false;
  }

  if (originalWriteText) {
    try {
      await originalWriteText(text);
      return true;
    } catch {
      // Fall through to legacy copy method.
    }
  } else if (!clipboardPatched && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to legacy copy method.
    }
  }

  return writeTextLegacy(text);
}

/**
 * Patches `navigator.clipboard.writeText` so third-party components (e.g.
 * streamdown code blocks) also work on non-secure origins (HTTP deployments).
 */
export function ensureClipboardApi(): void {
  if (typeof window === "undefined" || clipboardPatched) {
    return;
  }
  clipboardPatched = true;

  if (navigator.clipboard?.writeText) {
    originalWriteText = navigator.clipboard.writeText.bind(navigator.clipboard);
  }

  const patchedWriteText = async (text: string) => {
    const success = await copyTextToClipboard(text);
    if (!success) {
      throw new DOMException("Failed to copy", "NotAllowedError");
    }
  };

  if (!navigator.clipboard) {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: patchedWriteText },
      configurable: true,
    });
    return;
  }

  navigator.clipboard.writeText = patchedWriteText;
}
