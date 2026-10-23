import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '@/shared/lib/utils';

// Links leave the app in a new tab. react-markdown already drops javascript: and other unsafe URLs.
const COMPONENTS: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
};

/** An answer: the model writes Markdown (lists, tables, code). Raw HTML in it shows as text, never runs. */
export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div
      className={cn(
        'prose prose-sm prose-neutral max-w-none break-words dark:prose-invert',
        'prose-pre:bg-muted prose-pre:text-foreground prose-code:before:content-none prose-code:after:content-none',
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
