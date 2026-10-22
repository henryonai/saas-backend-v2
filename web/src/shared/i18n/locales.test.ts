import { describe, expect, it } from 'vitest';
import en from './locales/en.json';
import vi from './locales/vi.json';

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ''): Map<string, string> {
  const out = new Map<string, string>();
  for (const [key, value] of Object.entries(tree)) {
    if (typeof value === 'string') out.set(prefix + key, value);
    else for (const [k, v] of flatten(value, `${prefix}${key}.`)) out.set(k, v);
  }
  return out;
}

const variables = (text: string) => [...text.matchAll(/{{(\w+)}}/g)].map((match) => match[1]).sort();

describe('translations', () => {
  const english = flatten(en);
  const vietnamese = flatten(vi);

  it('have the same keys in English and Vietnamese', () => {
    expect([...vietnamese.keys()].sort()).toEqual([...english.keys()].sort());
  });

  it('use the same {{variables}} in both languages', () => {
    for (const [key, text] of english) {
      expect(variables(vietnamese.get(key) ?? ''), key).toEqual(variables(text));
    }
  });
});
