import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";
import { cite, remarkCitations } from "../src/renderer/citations";

function render(result: string, folded: string[] = [], links: string[] = []) {
  const content = cite(result, folded, links);
  return renderToStaticMarkup(
    <Markdown
      remarkPlugins={[remarkGfm, [remarkCitations, { links: content.links }]]}
    >
      {[content.result, ...content.folded].join("\n\n")}
    </Markdown>,
  );
}

describe("DESKTOP-08 inline sources", () => {
  it("links existing markers in the reply and secondary blocks without renumbering", () => {
    const html = render(
      "Наставник [2]",
      ["Команда [1] [2]"],
      ["https://example.com/team?a=1&b=2", "https://example.com/mentor"],
    );
    expect(html).toContain('<a href="https://example.com/mentor">[2]</a>');
    expect(html).toContain(
      '<a href="https://example.com/team?a=1&amp;b=2">[1]</a>',
    );
    expect(html.match(/<a /g)).toHaveLength(3);
    expect(html).not.toContain("Источники");
  });

  it("adds markers to matching numbered rows with one number per URL", () => {
    const html = render(
      "1. Первый\n2. Второй\n3. Повтор",
      [],
      [
        "https://example.com/one",
        "https://example.com/two",
        "https://example.com/one",
      ],
    );
    expect(html).toContain('Первый <a href="https://example.com/one">[1]</a>');
    expect(html).toContain('Второй <a href="https://example.com/two">[2]</a>');
    expect(html).toContain('Повтор <a href="https://example.com/one">[1]</a>');
  });

  it("places new markers in matching secondary rows or at the last reply line", () => {
    expect(
      cite("Ответ", ["1. Подробнее"], ["https://example.com"]).folded,
    ).toEqual(["1. Подробнее [1]"]);
    expect(
      cite("Ответ\n\n", [], ["https://example.com", "https://example.com"])
        .result,
    ).toBe("Ответ [1]\n\n");
  });

  it("does not create unsafe or missing source links or execute HTML", () => {
    const html = render(
      "<script>alert(1)</script>\n\nТекст [1] [2] [3]",
      [],
      ["javascript:alert(1)", "file:///private"],
    );
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("<script>");
    expect(html).toContain("Текст [1] [2] [3]");
  });

  it("preserves Markdown links, inline code and fenced code", () => {
    const html = render(
      "[Документ [1]](https://example.com/original) и `[1]`\n\n```text\n[1]\n```\n\nИсточник [1]",
      [],
      ["https://example.com/source"],
    );
    expect(html).toContain(
      '<a href="https://example.com/original">Документ [1]</a>',
    );
    expect(html).toContain("<code>[1]</code>");
    expect(html).toContain('<code class="language-text">[1]\n</code>');
    expect(html.match(/href="https:\/\/example.com\/source"/g)).toHaveLength(1);
  });

  it("leaves markers unchanged when rendering user text without source links", () => {
    expect(render("Массив [1] и **текст**")).toBe(
      "<p>Массив [1] и <strong>текст</strong></p>",
    );
  });
});
