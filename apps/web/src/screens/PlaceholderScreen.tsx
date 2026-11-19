import { SheetHeader } from '@magnm/design/react';

export interface PlaceholderScreenProps {
  page: number;
  total: number;
  title: string;
  description: string;
}

/** Minimal placeholder screen for upcoming Studio sections. */
export function PlaceholderScreen({ page, total, title, description }: PlaceholderScreenProps) {
  return (
    <SheetHeader
      page={page}
      total={total}
      subtitle="Magnm Studio"
      title={title}
      lead={description}
    />
  );
}
