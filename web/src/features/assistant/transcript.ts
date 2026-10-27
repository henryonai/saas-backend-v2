import type { Message } from '@ag-ui/client';
import type { AssistantFileKind } from '@/shared/api/types';

/** A file on a user's message: an image or document part, which names it by its id (a file source). */
export interface AttachmentView {
  fileId: string;
  name: string;
  contentType: string;
  size: number;
  kind: AssistantFileKind;
}

type UserContent = Extract<Message, { role: 'user' }>['content'];

export interface ToolCallView {
  id: string;
  name: string;
  /** the arguments once they parse: they stream in as JSON text */
  args: Record<string, unknown> | null;
  /** what the tool returned; null while it runs */
  result: string | null;
}

export type Part = { type: 'text'; id: string; text: string } | { type: 'tool'; call: ToolCallView };

export type Turn =
  | { role: 'user'; id: string; text: string; attachments: AttachmentView[] }
  /** `answerId`: its last assistant message, which a rating of the answer goes on */
  | { role: 'assistant'; id: string; answerId: string; parts: Part[] };

/**
 * The messages as the page shows them. One answer is one turn, however many
 * model calls it took: its text and tool calls in order (the server ends a
 * text before a tool call starts), each tool call with its result, which AG-UI
 * sends as a separate tool message.
 */
export function toTurns(messages: Message[]): Turn[] {
  const results = new Map<string, string>();
  for (const message of messages) {
    if (message.role === 'tool') results.set(message.toolCallId, textOf(message.content));
  }

  const turns: Turn[] = [];
  for (const message of messages) {
    if (message.role === 'user') {
      turns.push({ role: 'user', id: message.id, text: textOf(message.content), attachments: attachmentsOf(message.content) });
    } else if (message.role === 'assistant') {
      const last = turns.at(-1);
      const turn: Extract<Turn, { role: 'assistant' }> =
        last?.role === 'assistant' ? last : { role: 'assistant', id: message.id, answerId: message.id, parts: [] };
      if (turn !== last) turns.push(turn);
      turn.answerId = message.id;
      if (message.content) turn.parts.push({ type: 'text', id: message.id, text: message.content });
      for (const call of message.toolCalls ?? []) {
        turn.parts.push({
          type: 'tool',
          call: { id: call.id, name: call.function.name, args: parseArgs(call.function.arguments), result: results.get(call.id) ?? null },
        });
      }
    }
  }
  return turns;
}

export function textOf(content: string | { type: string; text?: string }[] | undefined): string {
  if (typeof content === 'string') return content;
  return (content ?? []).map((part) => (part.type === 'text' ? (part.text ?? '') : '')).join('');
}

/**
 * A user message's content, as the server saves it (service.user_content):
 * its text, or, with files, a text part and an image or document part each.
 */
export function userContent(text: string, attachments: AttachmentView[]): UserContent {
  if (attachments.length === 0) return text;
  return [
    ...(text ? [{ type: 'text' as const, text }] : []),
    ...attachments.map((file) => ({
      type: file.kind === 'image' ? ('image' as const) : ('document' as const),
      source: { type: 'file' as const, value: file.fileId, mimeType: file.contentType },
      metadata: { name: file.name, size: file.size, kind: file.kind },
    })),
  ] as UserContent;
}

/** The files on a user message (userContent's parts). */
export function attachmentsOf(content: unknown): AttachmentView[] {
  if (!Array.isArray(content)) return [];
  const files: AttachmentView[] = [];
  for (const part of content as { type?: string; source?: { type?: string; value?: string; mimeType?: string }; metadata?: Record<string, unknown> }[]) {
    if ((part.type !== 'image' && part.type !== 'document') || part.source?.type !== 'file' || !part.source.value) continue;
    const metadata = part.metadata ?? {};
    files.push({
      fileId: part.source.value,
      name: typeof metadata.name === 'string' ? metadata.name : 'file',
      contentType: part.source.mimeType ?? 'application/octet-stream',
      size: typeof metadata.size === 'number' ? metadata.size : 0,
      kind: typeof metadata.kind === 'string' ? (metadata.kind as AssistantFileKind) : part.type === 'image' ? 'image' : 'other',
    });
  }
  return files;
}

/** What a file is, from its type, as the server says (files.kind_of), for a file the page has only a type for. */
export function kindOf(contentType: string): AssistantFileKind {
  if (contentType.startsWith('image/')) return contentType === 'image/svg+xml' ? 'text' : 'image';
  if (['text/csv', 'application/json', 'text/tab-separated-values'].includes(contentType) || /spreadsheet|excel/.test(contentType)) return 'data';
  if (contentType === 'application/pdf' || /word|presentation|rtf/.test(contentType)) return 'document';
  return contentType.startsWith('text/') ? 'text' : 'other';
}

/** A file saved by write_file or save_file: its tool result, as the server writes it (tools/files.py). */
export interface SavedFile {
  file_id: string;
  name: string;
  content_type: string;
  size: number;
  version: number;
}

export function savedFileOf(call: ToolCallView): SavedFile | null {
  if ((call.name !== 'write_file' && call.name !== 'save_file') || call.result === null) return null;
  try {
    const saved: unknown = JSON.parse(call.result);
    return saved && typeof saved === 'object' && typeof (saved as SavedFile).file_id === 'string' ? (saved as SavedFile) : null;
  } catch {
    return null; // an error, said in words
  }
}

function parseArgs(json: string): Record<string, unknown> | null {
  try {
    const args: unknown = JSON.parse(json);
    return args && typeof args === 'object' && !Array.isArray(args) ? (args as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** A tool's result for display: JSON indented, anything else as it came. */
export function formatResult(result: string): string {
  try {
    return JSON.stringify(JSON.parse(result), null, 2);
  } catch {
    return result;
  }
}
