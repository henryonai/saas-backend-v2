import type { Message } from '@ag-ui/client';

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
  | { role: 'user'; id: string; text: string }
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
      turns.push({ role: 'user', id: message.id, text: textOf(message.content) });
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

function textOf(content: string | { type: string; text?: string }[] | undefined): string {
  if (typeof content === 'string') return content;
  return (content ?? []).map((part) => (part.type === 'text' ? (part.text ?? '') : '')).join('');
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
