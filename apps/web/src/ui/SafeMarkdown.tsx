import { Fragment, type ReactNode } from 'react';
import { isSafeHttpUrl } from './format';

export interface SafeMarkdownProps {
  text: string;
  className?: string;
}

const INLINE_TOKEN_RE =
  /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]]+\]\([^)\s]+\)|https?:\/\/[^\s<>"')]+)/g;

function renderInline(line: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let tokenIdx = 0;

  INLINE_TOKEN_RE.lastIndex = 0;
  while ((match = INLINE_TOKEN_RE.exec(line)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(line.slice(lastIndex, match.index));
    }

    const token = match[0];
    const key = `${keyPrefix}-t${tokenIdx++}`;

    if (token.startsWith('`') && token.endsWith('`') && token.length >= 2) {
      nodes.push(
        <code key={key} className="studio-inline-code">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith('**') && token.endsWith('**') && token.length >= 4) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('[')) {
      const closeBracket = token.indexOf('](');
      const label = token.slice(1, closeBracket);
      const href = token.slice(closeBracket + 2, -1);
      if (isSafeHttpUrl(href)) {
        nodes.push(
          <a key={key} href={href} target="_blank" rel="noopener noreferrer">
            {label}
          </a>,
        );
      } else {
        nodes.push(label);
      }
    } else {
      const cleaned = token.replace(/[.,;:!?]+$/, '');
      const trailing = token.slice(cleaned.length);
      if (isSafeHttpUrl(cleaned)) {
        nodes.push(
          <Fragment key={key}>
            <a href={cleaned} target="_blank" rel="noopener noreferrer">
              {cleaned}
            </a>
            {trailing}
          </Fragment>,
        );
      } else {
        nodes.push(token);
      }
    }

    lastIndex = match.index + token.length;
  }

  if (lastIndex < line.length) {
    nodes.push(line.slice(lastIndex));
  }
  return nodes;
}

interface Block {
  kind: 'code' | 'ul' | 'ol' | 'heading' | 'paragraph';
  lines: string[];
}

function parseBlocks(source: string): Block[] {
  const rawLines = source.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  let idx = 0;

  while (idx < rawLines.length) {
    const line = rawLines[idx];

    if (line.trimStart().startsWith('```')) {
      const codeLines: string[] = [];
      idx += 1;
      while (idx < rawLines.length && !rawLines[idx].trimStart().startsWith('```')) {
        codeLines.push(rawLines[idx]);
        idx += 1;
      }
      if (idx < rawLines.length) idx += 1;
      blocks.push({ kind: 'code', lines: codeLines });
      continue;
    }

    if (!line.trim()) {
      idx += 1;
      continue;
    }

    if (/^#{1,4}\s+/.test(line.trim())) {
      blocks.push({ kind: 'heading', lines: [line.trim().replace(/^#{1,4}\s+/, '')] });
      idx += 1;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (idx < rawLines.length && /^\s*[-*]\s+/.test(rawLines[idx])) {
        items.push(rawLines[idx].replace(/^\s*[-*]\s+/, ''));
        idx += 1;
      }
      blocks.push({ kind: 'ul', lines: items });
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (idx < rawLines.length && /^\s*\d+\.\s+/.test(rawLines[idx])) {
        items.push(rawLines[idx].replace(/^\s*\d+\.\s+/, ''));
        idx += 1;
      }
      blocks.push({ kind: 'ol', lines: items });
      continue;
    }

    const paraLines: string[] = [];
    while (
      idx < rawLines.length &&
      rawLines[idx].trim() &&
      !rawLines[idx].trimStart().startsWith('```') &&
      !/^#{1,4}\s+/.test(rawLines[idx].trim()) &&
      !/^\s*[-*]\s+/.test(rawLines[idx]) &&
      !/^\s*\d+\.\s+/.test(rawLines[idx])
    ) {
      paraLines.push(rawLines[idx]);
      idx += 1;
    }
    blocks.push({ kind: 'paragraph', lines: paraLines });
  }

  return blocks;
}

/** Plain markdown-ish renderer using React nodes only (never dangerouslySetInnerHTML). */
export function SafeMarkdown({ text, className }: SafeMarkdownProps) {
  const blocks = parseBlocks(text || '');
  if (blocks.length === 0) return null;

  return (
    <div className={className ? `studio-md ${className}` : 'studio-md'}>
      {blocks.map((block, bIdx) => {
        const key = `b-${bIdx}`;
        if (block.kind === 'code') {
          return (
            <pre key={key} className="studio-code-block">
              <code>{block.lines.join('\n')}</code>
            </pre>
          );
        }
        if (block.kind === 'heading') {
          return (
            <p key={key} className="studio-md-heading">
              {renderInline(block.lines[0], key)}
            </p>
          );
        }
        if (block.kind === 'ul') {
          return (
            <ul key={key} className="studio-md-list">
              {block.lines.map((item, i) => (
                <li key={`${key}-li-${i}`}>{renderInline(item, `${key}-${i}`)}</li>
              ))}
            </ul>
          );
        }
        if (block.kind === 'ol') {
          return (
            <ol key={key} className="studio-md-list">
              {block.lines.map((item, i) => (
                <li key={`${key}-li-${i}`}>{renderInline(item, `${key}-${i}`)}</li>
              ))}
            </ol>
          );
        }
        return (
          <p key={key}>
            {block.lines.map((line, i) => (
              <Fragment key={`${key}-l-${i}`}>
                {i > 0 ? <br /> : null}
                {renderInline(line, `${key}-${i}`)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
