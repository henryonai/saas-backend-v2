import { Link } from '@tanstack/react-router';
import { DownloadIcon, FolderIcon, MessagesSquareIcon, Trash2Icon } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FileKindIcon } from '@/features/assistant/components/files';
import { downloadFile, type FileFilters, useDeleteFile, useFiles } from '@/features/assistant/hooks';
import type { AssistantFile, AssistantFileKind } from '@/shared/api/types';
import { ConfirmDialog } from '@/shared/components/confirm-dialog';
import { PageHeader } from '@/shared/components/page-header';
import { ErrorState, LoadingRows } from '@/shared/components/query-state';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Card } from '@/shared/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/shared/components/ui/empty';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { Spinner } from '@/shared/components/ui/spinner';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table';
import { toastError } from '@/shared/lib/errors';
import { formatBytes, formatRelative } from '@/shared/lib/format';

const SOURCES = ['all', 'assistant', 'upload'] as const;
const KINDS = ['all', 'image', 'text', 'data', 'document', 'other'] as const;

/**
 * The user's files (/assistant/files): what the assistant made for them, and
 * what they attached, newest first, by kind; each to download, to delete, and
 * a link to the conversation it came from.
 */
export function FilesPage() {
  const { t } = useTranslation();
  const [source, setSource] = useState<(typeof SOURCES)[number]>('all');
  const [kind, setKind] = useState<(typeof KINDS)[number]>('all');
  const filters: FileFilters = { source: source === 'all' ? undefined : source, kind: kind === 'all' ? undefined : (kind as AssistantFileKind) };
  const files = useFiles(filters);
  const items = files.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <PageHeader title={t('files.title')} description={t('files.description')} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-lg border p-1" role="group" aria-label={t('files.sourceLabel')}>
          {SOURCES.map((value) => (
            <Button key={value} size="sm" variant={source === value ? 'secondary' : 'ghost'} aria-pressed={source === value} onClick={() => setSource(value)}>
              {t(`files.sources.${value}`)}
            </Button>
          ))}
        </div>
        <Select value={kind} onValueChange={(value) => setKind(value as (typeof KINDS)[number])}>
          <SelectTrigger className="w-40" aria-label={t('files.kindLabel')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KINDS.map((value) => (
              <SelectItem key={value} value={value}>
                {t(`files.kinds.${value}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {files.isPending ? (
        <LoadingRows rows={5} />
      ) : files.isError ? (
        <ErrorState error={files.error} onRetry={() => files.refetch()} />
      ) : items.length === 0 && !files.hasNextPage ? (
        <Empty className="border border-dashed">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FolderIcon />
            </EmptyMedia>
            <EmptyTitle>{t('files.empty.title')}</EmptyTitle>
            <EmptyDescription>{t('files.empty.description')}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="space-y-4">
          <Card className="py-0">
            <FilesTable files={items} />
          </Card>
          {files.hasNextPage && (
            <div className="flex justify-center">
              <Button variant="outline" onClick={() => files.fetchNextPage()} disabled={files.isFetchingNextPage}>
                {files.isFetchingNextPage && <Spinner />}
                {t('common.loadMore')}
              </Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}

function FilesTable({ files }: { files: AssistantFile[] }) {
  const { t } = useTranslation();
  const remove = useDeleteFile();
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t('files.columns.name')}</TableHead>
          <TableHead className="hidden sm:table-cell">{t('files.columns.from')}</TableHead>
          <TableHead className="text-right">{t('files.columns.size')}</TableHead>
          <TableHead className="hidden md:table-cell">{t('files.columns.updated')}</TableHead>
          <TableHead>
            <span className="sr-only">{t('files.columns.actions')}</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {files.map((file) => (
          <TableRow key={file.file_id}>
            <TableCell className="max-w-72">
              <div className="flex items-center gap-2">
                <FileKindIcon kind={file.kind} />
                <span className="truncate font-medium">{file.name}</span>
                {file.versions.length > 1 && <Badge variant="secondary">{t('files.versions', { count: file.versions.length })}</Badge>}
              </div>
            </TableCell>
            <TableCell className="hidden text-muted-foreground sm:table-cell">
              <div className="flex items-center gap-2">
                <span>{file.source === 'assistant' ? (file.agent ?? t('files.sources.assistant')) : t('files.you')}</span>
                {file.thread_id && (
                  <Link to="/assistant/$threadId" params={{ threadId: file.thread_id }} className="text-foreground hover:underline" title={t('files.openChat')}>
                    <MessagesSquareIcon className="size-4" />
                    <span className="sr-only">{t('files.openChat')}</span>
                  </Link>
                )}
              </div>
            </TableCell>
            <TableCell className="text-right tabular-nums">{formatBytes(file.size)}</TableCell>
            <TableCell className="hidden text-muted-foreground md:table-cell">{formatRelative(file.updated_at)}</TableCell>
            <TableCell>
              <div className="flex justify-end gap-1">
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => downloadFile(file.file_id).catch((error: unknown) => toastError(error, t))}
                  aria-label={t('assistant.files.download', { name: file.name })}
                  title={t('assistant.files.downloadShort')}
                >
                  <DownloadIcon />
                </Button>
                <ConfirmDialog
                  trigger={
                    <Button variant="ghost" size="icon-sm" aria-label={t('files.delete', { name: file.name })} title={t('files.deleteShort')}>
                      <Trash2Icon />
                    </Button>
                  }
                  title={t('files.deleteTitle')}
                  description={t('files.deleteDescription', { name: file.name })}
                  confirmLabel={t('files.deleteShort')}
                  destructive
                  onConfirm={() =>
                    remove.mutateAsync(file.file_id).catch((error: unknown) => {
                      toastError(error, t);
                      throw error;
                    })
                  }
                />
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
