import { safeExternal } from "../main/policy";

// Match Telegram's source placement while keeping existing marker numbering.
export function cite(
  result: string,
  folded: string[] = [],
  links: string[] = [],
) {
  if (!links.length || [result, ...folded].some((text) => /\[\d+\]/.test(text)))
    return { result, folded, links };
  const unique = [...new Set(links)];
  const numbers = links.map((link) => unique.indexOf(link) + 1);
  const blocks = [result, ...folded].map((text) => text.split("\n"));
  const rows = (group: string[][]) =>
    group.flatMap((block) =>
      block.flatMap((line, i) => (/^\d+\. /.test(line) ? [{ block, i }] : [])),
    );
  const matching = [rows(blocks.slice(0, 1)), rows(blocks.slice(1))].find(
    (group) => group.length === links.length,
  );
  if (matching)
    matching.forEach(({ block, i }, index) => {
      block[i] += ` [${numbers[index]}]`;
    });
  else {
    let last = blocks[0].length - 1;
    while (last > 0 && !blocks[0][last].trim()) last--;
    blocks[0][last] += unique.map((_, i) => ` [${i + 1}]`).join("");
  }
  return {
    result: blocks[0].join("\n"),
    folded: blocks.slice(1).map((block) => block.join("\n")),
    links: unique,
  };
}

type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
};

// Transform parsed text only: never rewrite code, HTML or existing links.
export function remarkCitations({ links }: { links: string[] }) {
  return (tree: MarkdownNode) => {
    function visit(node: MarkdownNode) {
      if (
        ["link", "linkReference", "code", "inlineCode", "html"].includes(
          node.type,
        )
      )
        return;
      node.children = node.children?.flatMap((child) => {
        if (child.type !== "text") {
          visit(child);
          return [child];
        }
        const text = child.value || "";
        const parts: MarkdownNode[] = [];
        let start = 0;
        for (const match of text.matchAll(/\[(\d+)\]/g)) {
          const url = links[Number(match[1]) - 1];
          if (!url || !safeExternal(url)) continue;
          parts.push({ type: "text", value: text.slice(start, match.index) });
          parts.push({
            type: "link",
            url,
            children: [{ type: "text", value: match[0] }],
          });
          start = match.index + match[0].length;
        }
        parts.push({ type: "text", value: text.slice(start) });
        return parts;
      });
    }
    visit(tree);
  };
}
