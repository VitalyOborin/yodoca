import DOMPurify from 'dompurify';
import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import json from 'highlight.js/lib/languages/json';
import plaintext from 'highlight.js/lib/languages/plaintext';

hljs.registerLanguage('python', python);
hljs.registerLanguage('json', json);
hljs.registerLanguage('text', plaintext);
hljs.registerLanguage('plaintext', plaintext);

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  breaks: true,
  highlight: (source: string, language: string) => {
    if (language && hljs.getLanguage(language)) {
      const highlighted = hljs.highlight(source, { language }).value;
      return `<pre><code class="hljs language-${language}">${highlighted}</code></pre>`;
    }

    const escaped = markdown.utils.escapeHtml(source);
    return `<pre><code class="hljs language-text">${escaped}</code></pre>`;
  },
});

export function renderMarkdown(content: string): string {
  const html = markdown.render(content);
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
  });
}
