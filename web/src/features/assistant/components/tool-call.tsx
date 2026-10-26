import type { TFunction } from 'i18next';
import {
  BotIcon,
  BrainIcon,
  CheckIcon,
  ChevronRightIcon,
  GaugeIcon,
  type LucideIcon,
  PackageIcon,
  TerminalIcon,
  WrenchIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Spinner } from '@/shared/components/ui/spinner';
import { formatResult, type ToolCallView } from '../transcript';

/** The tools agents can call (modules/assistant/src/assistant/tools/), as the user sees them. */
const TOOLS = {
  list_orders: { label: 'assistant.tool.listOrders', icon: PackageIcon },
  get_order: { label: 'assistant.tool.getOrder', icon: PackageIcon },
  assistant_usage: { label: 'assistant.tool.usage', icon: GaugeIcon },
  bash: { label: 'assistant.tool.bash', icon: TerminalIcon },
  forget: { label: 'assistant.tool.forget', icon: BrainIcon },
} as const;

function describe(call: ToolCallView, t: TFunction): { label: string; icon: LucideIcon } {
  if (call.name === 'task') {
    // A subagent at work: its answer comes back as this call's result.
    const agent = call.args?.agent;
    return { label: typeof agent === 'string' ? t('assistant.tool.task', { agent }) : t('assistant.tool.taskPending'), icon: BotIcon };
  }
  if (call.name === 'remember') {
    // Memory updated, as ChatGPT says: the Memory dialog has it now.
    return { label: call.args?.replaces ? t('assistant.tool.rememberAgain') : t('assistant.tool.remember'), icon: BrainIcon };
  }
  const tool = TOOLS[call.name as keyof typeof TOOLS];
  return tool ? { label: t(tool.label), icon: tool.icon } : { label: call.name, icon: WrenchIcon };
}

/** What the call was given, in the form that reads best for its tool. */
function input(call: ToolCallView): string | null {
  if (!call.args) return null;
  if (call.name === 'bash' && typeof call.args.command === 'string') return `$ ${call.args.command}`;
  if (call.name === 'task' && typeof call.args.instructions === 'string') return call.args.instructions;
  if (call.name === 'remember' && typeof call.args.fact === 'string') return call.args.fact;
  return Object.keys(call.args).length > 0 ? JSON.stringify(call.args, null, 2) : null;
}

/** A tool call: its name and status, and on opening, its input and result. `active`: the run is still going. */
export function ToolCall({ call, active }: { call: ToolCallView; active: boolean }) {
  const { t } = useTranslation();
  const { label, icon: Icon } = describe(call, t);
  const given = input(call);
  const running = call.result === null && active;

  return (
    <details className="group rounded-lg border bg-muted/40 text-sm">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 select-none [&::-webkit-details-marker]:hidden">
        <Icon className="size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 truncate font-medium">{label}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
          {running ? <Spinner className="size-3.5" /> : call.result !== null && <CheckIcon className="size-3.5" />}
          {running ? t('assistant.tool.running') : call.result !== null && t('assistant.tool.done')}
        </span>
        <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
      </summary>
      <div className="space-y-3 border-t px-3 py-3">
        {given && <Output title={t('assistant.tool.input')} text={given} />}
        {call.result !== null && <Output title={t('assistant.tool.result')} text={formatResult(call.result)} />}
      </div>
    </details>
  );
}

function Output({ title, text }: { title: string; text: string }) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium text-muted-foreground">{title}</div>
      <pre className="max-h-80 overflow-auto rounded-md bg-background p-2 font-mono text-xs whitespace-pre-wrap break-words">{text}</pre>
    </div>
  );
}
