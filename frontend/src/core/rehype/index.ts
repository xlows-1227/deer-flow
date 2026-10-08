import type { Element, Root, ElementContent } from "hast";
import { useMemo } from "react";
import { visit } from "unist-util-visit";
import type { BuildVisitor } from "unist-util-visit";

const CJK_TEXT_RE =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;

/**
 * 超过此长度的文本节点跳过分词动画。LLM 退化时可能生成数万字的
 * 重复文本（如连续的 "[工具_call]"），若逐词包成 <span> 会创建
 * 海量 DOM 节点，导致主线程阻塞、页面无法点击或滚动。
 */
const WORD_SPAN_MAX_TEXT_LENGTH = 8000;

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
            // 超长文本不做分词，避免 DOM 节点爆炸导致页面卡死。
            if (child.value.length > WORD_SPAN_MAX_TEXT_LENGTH) {
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

const KNOWN_HTML_TAGS = new Set([
  "html", "head", "body", "div", "span", "section", "article", "header",
  "footer", "nav", "main", "aside", "address", "h1", "h2", "h3", "h4", "h5",
  "h6", "p", "br", "hr", "blockquote", "pre", "code", "em", "strong", "i",
  "b", "u", "s", "del", "ins", "mark", "small", "sub", "sup", "abbr", "cite",
  "q", "kbd", "samp", "var", "ul", "ol", "li", "dl", "dt", "dd", "table",
  "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "col", "colgroup",
  "a", "img", "figure", "figcaption", "picture", "source", "video", "audio",
  "iframe", "embed", "object", "canvas", "map", "area", "form", "input",
  "button", "textarea", "select", "option", "optgroup", "label", "fieldset",
  "legend", "datalist", "output", "progress", "meter", "details", "summary",
  "dialog", "time", "data", "bdi", "bdo", "ruby", "rt", "rp", "wbr", "script",
  "style", "noscript", "template", "slot", "link", "meta", "title", "base",
  "param", "track", "svg",
]);

const KNOWN_SVG_TAGS = new Set([
  "svg", "g", "path", "circle", "rect", "line", "polyline", "polygon",
  "ellipse", "defs", "use", "symbol", "clippath", "mask", "pattern", "marker",
  "lineargradient", "radialgradient", "stop", "image", "text", "tspan",
  "textpath", "foreignobject", "desc", "switch", "animate", "animatemotion",
  "animatetransform", "set", "filter", "fegaussianblur", "feoffset",
  "femerge", "femergenode", "fecolormatrix", "fecomposite", "feflood",
  "feblend", "femorphology", "fetile", "fedisplacementmap", "fespecularlighting",
  "fediffuselighting", "fepointlight", "fedistantlight", "fespotlight",
  "fefuncca", "fefuncr", "fefuncg", "fefuncb", "feconvolvematrix",
  "feimagel", "feturbulence",
]);

function isKnownTag(tagName: string, inSvg: boolean): boolean {
  const lower = tagName.toLowerCase();
  if (inSvg) {
    return KNOWN_SVG_TAGS.has(lower) || KNOWN_HTML_TAGS.has(lower);
  }
  return KNOWN_HTML_TAGS.has(lower);
}

function nodeToRawString(node: ElementContent): string {
  if (node.type === "text") {
    return (node as { value?: string }).value ?? "";
  }
  if (node.type === "raw") {
    return (node as { value?: string }).value ?? "";
  }
  if (node.type === "comment") {
    return "";
  }
  if (node.type === "element") {
    const el = node;
    const attrs = Object.entries(el.properties ?? {})
      .map(([k, v]) =>
        v === true || v == null ? ` ${k}` : ` ${k}="${String(v)}"`,
      )
      .join("");
    const inner = (el.children ?? [])
      .map((c) => nodeToRawString(c))
      .join("");
    return `<${el.tagName}${attrs}>${inner}</${el.tagName}>`;
  }
  return "";
}

function escapeUnknownNode(
  node: ElementContent,
  inSvg: boolean,
): ElementContent {
  if (node.type !== "element") {
    return node;
  }
  const el = node;
  const isSvg = el.tagName.toLowerCase() === "svg";
  const newInSvg = inSvg || isSvg;
  if (el.children) {
    el.children = el.children.map((c) => escapeUnknownNode(c, newInSvg));
  }
  if (!isKnownTag(el.tagName, inSvg)) {
    return { type: "text", value: nodeToRawString(el) };
  }
  return el;
}

/**
 * Rehype plugin that escapes unrecognized HTML/SVG tags (e.g.
 * LLM-hallucinated tags like `<path>`, `<query>`, `<ls>`, `<glob>`,
 * `<pattern>`, `<description>`) by converting them back to text nodes so
 * they render as visible text instead of unknown DOM elements. Known HTML
 * tags are always preserved; known SVG tags are only preserved when inside
 * an `<svg>` ancestor, so a stray `<path>` outside an `<svg>` is escaped
 * while a real SVG `<path>` renders normally. Must run AFTER rehype-raw.
 */
export function rehypeEscapeUnknownTags() {
  return (tree: Root) => {
    // Root.children is RootContent (Doctype/Raw/MDX nodes beyond
    // ElementContent); escapeUnknownNode only transforms elements and returns
    // everything else unchanged, so only elements are passed in.
    tree.children = tree.children.map((c) =>
      c.type === "element" ? escapeUnknownNode(c, false) : c,
    );
  };
}
