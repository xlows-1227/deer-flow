import type { Element, Root, ElementContent } from "hast";
import { useMemo } from "react";
import { visit } from "unist-util-visit";
import type { BuildVisitor } from "unist-util-visit";

const CJK_TEXT_RE =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;

export function rehypeSplitWordsIntoSpans() {
  return (tree: Root) => {
    visit(tree, "element", ((node: Element) => {
      if (
        ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "strong"].includes(
          node.tagName,
        ) &&
        node.children
      ) {
        const newChildren: Array<ElementContent> = [];
        node.children.forEach((child) => {
          if (child.type === "text") {
            if (CJK_TEXT_RE.test(child.value)) {
              newChildren.push(child);
              return;
            }
            const segmenter = new Intl.Segmenter("zh", { granularity: "word" });
            const segments = segmenter.segment(child.value);
            const words = Array.from(segments)
              .map((segment) => segment.segment)
              .filter(Boolean);
            words.forEach((word: string) => {
              newChildren.push({
                type: "element",
                tagName: "span",
                properties: {
                  className: "animate-fade-in",
                },
                children: [{ type: "text", value: word }],
              });
            });
          } else {
            newChildren.push(child);
          }
        });
        node.children = newChildren;
      }
    }) as BuildVisitor<Root, "element">);
  };
}

const WHITESPACE_ONLY_RE = /^[ \t\r\n]*$/;

/**
 * rehype-raw re-parses the tree with parse5, which "foster-parents" the
 * whitespace-only text nodes that remark-rehype inserts between table
 * elements (<table>/<thead>/<tr>/...) out to BEFORE the table. The resulting
 * run of "\n" nodes renders as a block of blank lines between the preceding
 * heading/paragraph and the table whenever an ancestor uses
 * white-space:pre-wrap, and pollutes copy/paste even when collapsed. Drop
 * whitespace-only text nodes that sit directly under the root or adjacent to
 * a table; they never carry content.
 */
export function rehypeStripBlockWhitespace() {
  const clean = (parent: Root | Element): void => {
    const children = parent.children;
    const kept = children.filter((child, index) => {
      if (child.type !== "text" || !WHITESPACE_ONLY_RE.test(child.value)) {
        return true;
      }
      const prev = children[index - 1];
      const next = children[index + 1];
      const touchesTable =
        (next?.type === "element" && next.tagName === "table") ||
        (prev?.type === "element" && prev.tagName === "table");
      return !(parent.type === "root" || touchesTable);
    });
    if (kept.length !== children.length) {
      parent.children = kept;
    }
    for (const child of parent.children) {
      if (child.type === "element") {
        clean(child);
      }
    }
  };
  return (tree: Root) => {
    clean(tree);
  };
}

export function useRehypeSplitWordsIntoSpans(enabled = true) {
  const rehypePlugins = useMemo(
    () => (enabled ? [rehypeSplitWordsIntoSpans] : []),
    [enabled],
  );
  return rehypePlugins;
}
