import { DownloadIcon, FileIcon, FileSpreadsheetIcon, FileTextIcon, ImageIcon, type LucideIcon, PackageIcon, XIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { AssistantFileKind } from '@/shared/api/types';
import { Button } from '@/shared/components/ui/button';
import { Skeleton } from '@/shared/components/ui/skeleton';
import { Spinner } from '@/shared/components/ui/spinner';
import { toastError } from '@/shared/lib/errors';
import { formatBytes } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { downloadFile, useFileLinks } from '../hooks';
import { type AttachmentView, kindOf, type SavedFile } from '../transcript';

const ICONS: Record<AssistantFileKind, LucideIcon> = {
  image: ImageIcon,
  text: FileTextIcon,
  data: FileSpreadsheetIcon,
  document: FileIcon,
  other: PackageIcon,
};

export function FileKindIcon({ kind, className }: { kind: AssistantFileKind; className?: string }) {
  const Icon = ICONS[kind] ?? FileIcon;
  return <Icon className={cn('size-4 shrink-0 text-muted-foreground', className)} />;
}

/**
 * A file, small: its kind, name and size. `uploading` or `failed` while it
 * is attached in the composer, with `onRemove`; sent, a click downloads it.
 */
export function FileChip({ file, state, onRemove }: {
  file: Pick<AttachmentView, 'name' | 'size' | 'kind'> & { fileId?: string };
  state?: 'uploading' | 'failed';
  onRemove?: () => void;
}) {
  const { t } = useTranslation();
  const body = (
    <>
      {state === 'uploading' ? <Spinner className="size-4 shrink-0" /> : <FileKindIcon kind={file.kind} />}
      <span className="min-w-0 truncate">{file.name}</span>
      <span className="shrink-0 text-xs text-muted-foreground">{state === 'failed' ? t('assistant.files.failed') : formatBytes(file.size)}</span>
    </>
  );
  const box = cn('flex h-9 max-w-64 items-center gap-2 rounded-lg border bg-background px-2.5 text-sm', state === 'failed' && 'border-destructive/50');
  if (onRemove) {
    return (
      <div className={box}>
        {body}
        <button
          type="button"
          onClick={onRemove}
          className="-mr-1 rounded p-0.5 text-muted-foreground hover:text-foreground"
          aria-label={t('assistant.files.remove', { name: file.name })}
        >
          <XIcon className="size-3.5" />
        </button>
      </div>
    );
  }
  return <DownloadButton fileId={file.fileId} className={box} label={t('assistant.files.download', { name: file.name })}>{body}</DownloadButton>;
}

function DownloadButton({ fileId, version, className, label, children }: {
  fileId?: string;
  version?: number;
  className?: string;
  label: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      className={cn('text-left hover:bg-accent', className)}
      disabled={!fileId}
      onClick={() => fileId && downloadFile(fileId, version).catch((error: unknown) => toastError(error, t))}
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}

/** An image the user attached or an agent made, shown (a click opens it full size). Gone: its chip says so. */
export function ImageFile({ file, className }: { file: Pick<AttachmentView, 'fileId' | 'name' | 'size'>; className?: string }) {
  const { t } = useTranslation();
  const links = useFileLinks(file.fileId);
  if (links.isPending) return <Skeleton className={cn('size-32 rounded-lg', className)} />;
  if (!links.data?.view_url) return <FileChip file={{ ...file, kind: 'image' }} />;
  return (
    <a href={links.data.view_url} target="_blank" rel="noopener noreferrer" className={cn('block overflow-hidden rounded-lg border', className)} title={file.name}>
      <img src={links.data.view_url} alt={t('assistant.files.image', { name: file.name })} className="max-h-64 max-w-full object-contain" />
    </a>
  );
}

/** The files on a user's message: images shown, others as chips. */
export function Attachments({ files, className }: { files: AttachmentView[]; className?: string }) {
  if (files.length === 0) return null;
  return (
    <div className={cn('flex flex-wrap justify-end gap-2', className)}>
      {files.map((file) =>
        file.kind === 'image' ? <ImageFile key={file.fileId} file={file} className="max-w-[60%]" /> : <FileChip key={file.fileId} file={file} />,
      )}
    </div>
  );
}

/** A file an agent saved in the answer (write_file, save_file): to download, an image shown. */
export function SavedFileCard({ saved }: { saved: SavedFile }) {
  const { t } = useTranslation();
  const kind = kindOf(saved.content_type);
  return (
    <div className="space-y-2">
      {kind === 'image' && <ImageFile file={{ fileId: saved.file_id, name: saved.name, size: saved.size }} />}
      <div className="flex items-center gap-3 rounded-lg border px-3 py-2 text-sm">
        <FileKindIcon kind={kind} className="size-5" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{saved.name}</div>
          <div className="text-xs text-muted-foreground">
            {formatBytes(saved.size)}
            {saved.version > 1 && ` · ${t('assistant.files.version', { version: saved.version })}`}
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => downloadFile(saved.file_id, saved.version).catch((error: unknown) => toastError(error, t))}
          aria-label={t('assistant.files.download', { name: saved.name })}
        >
          <DownloadIcon />
          {t('assistant.files.downloadShort')}
        </Button>
      </div>
    </div>
  );
}
