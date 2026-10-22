/** A string search param, or undefined (TanStack Router parses numbers and JSON in the query). */
export function optionalString(search: Record<string, unknown>, key: string): string | undefined {
  const value = search[key];
  if (typeof value === 'string' && value !== '') return value;
  if (typeof value === 'number') return String(value);
  return undefined;
}
