import { createFileRoute } from '@tanstack/react-router';
import { FilesPage } from '@/features/files/files-page';

export const Route = createFileRoute('/_app/files')({
  component: FilesPage,
});
