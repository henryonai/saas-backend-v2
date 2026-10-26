import type { Message } from '@ag-ui/client';
import { describe, expect, it } from 'vitest';
import { attachmentsOf, formatResult, kindOf, savedFileOf, toTurns, userContent } from './transcript';

const call = (id: string, name: string, args: string) => ({ id, type: 'function' as const, function: { name, arguments: args } });

describe('toTurns', () => {
  it('makes one turn of an answer that took several model calls, results on their calls', () => {
    const messages: Message[] = [
      { id: 'u1', role: 'user', content: 'What did we order?' },
      { id: 'a1', role: 'assistant', content: 'Let me check.', toolCalls: [call('c1', 'list_orders', '{"limit": 5}')] },
      { id: 't1', role: 'tool', toolCallId: 'c1', content: '{"items": []}' },
      { id: 'a2', role: 'assistant', content: 'You have no orders yet.' },
    ];
    expect(toTurns(messages)).toEqual([
      { role: 'user', id: 'u1', text: 'What did we order?', attachments: [] },
      {
        role: 'assistant',
        id: 'a1',
        answerId: 'a2', // the answer's last message: a rating goes there
        parts: [
          { type: 'text', id: 'a1', text: 'Let me check.' },
          { type: 'tool', call: { id: 'c1', name: 'list_orders', args: { limit: 5 }, result: '{"items": []}' } },
          { type: 'text', id: 'a2', text: 'You have no orders yet.' },
        ],
      },
    ]);
  });

  it('shows a tool call before its arguments are complete or its result is in', () => {
    const [, answer] = toTurns([
      { id: 'u1', role: 'user', content: 'Run it' },
      { id: 'a1', role: 'assistant', toolCalls: [call('c1', 'bash', '{"command": "ls')] },
    ]);
    expect(answer).toEqual({ role: 'assistant', id: 'a1', answerId: 'a1', parts: [{ type: 'tool', call: { id: 'c1', name: 'bash', args: null, result: null } }] });
  });

  it('starts a new turn after each user message, and reads text parts', () => {
    const turns = toTurns([
      { id: 'u1', role: 'user', content: [{ type: 'text', text: 'Hi ' }, { type: 'text', text: 'there' }] },
      { id: 'a1', role: 'assistant', content: 'Hello' },
      { id: 'u2', role: 'user', content: 'Again' },
      { id: 'a2', role: 'assistant', content: 'Hello again' },
    ]);
    expect(turns.map((turn) => [turn.role, turn.id])).toEqual([['user', 'u1'], ['assistant', 'a1'], ['user', 'u2'], ['assistant', 'a2']]);
    expect(turns[0]).toMatchObject({ text: 'Hi there' });
  });

  it('leaves out an assistant message that has nothing to show yet', () => {
    expect(toTurns([{ id: 'a1', role: 'assistant', content: '' }])).toEqual([{ role: 'assistant', id: 'a1', answerId: 'a1', parts: [] }]);
  });
});

describe('files on messages', () => {
  const receipt = { fileId: 'f1', name: 'receipt.png', contentType: 'image/png', size: 2048, kind: 'image' as const };
  const notes = { fileId: 'f2', name: 'notes.txt', contentType: 'text/plain', size: 19, kind: 'text' as const };

  it("writes a message's files as the server does, and reads them back", () => {
    expect(userContent('Hi', [])).toBe('Hi');
    const content = userContent('What are these?', [receipt, notes]);
    expect(content).toEqual([
      { type: 'text', text: 'What are these?' },
      { type: 'image', source: { type: 'file', value: 'f1', mimeType: 'image/png' }, metadata: { name: 'receipt.png', size: 2048, kind: 'image' } },
      { type: 'document', source: { type: 'file', value: 'f2', mimeType: 'text/plain' }, metadata: { name: 'notes.txt', size: 19, kind: 'text' } },
    ]);
    expect(attachmentsOf(content)).toEqual([receipt, notes]);
    const [turn] = toTurns([{ id: 'u1', role: 'user', content }]);
    expect(turn).toEqual({ role: 'user', id: 'u1', text: 'What are these?', attachments: [receipt, notes] });
  });

  it('knows a file an agent saved from its tool result', () => {
    const saved = { file_id: 'f3', name: 'report.md', content_type: 'text/markdown', size: 12, version: 1 };
    expect(savedFileOf({ id: 'c1', name: 'write_file', args: null, result: JSON.stringify(saved) })).toEqual(saved);
    expect(savedFileOf({ id: 'c1', name: 'write_file', args: null, result: 'Error: too long' })).toBeNull();
    expect(savedFileOf({ id: 'c1', name: 'bash', args: null, result: JSON.stringify(saved) })).toBeNull();
    expect([kindOf('image/png'), kindOf('text/csv'), kindOf('application/pdf'), kindOf('text/markdown'), kindOf('application/zip')])
      .toEqual(['image', 'data', 'document', 'text', 'other']);
  });
});

describe('formatResult', () => {
  it('indents JSON and leaves other text alone', () => {
    expect(formatResult('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(formatResult('exit code 0')).toBe('exit code 0');
  });
});
