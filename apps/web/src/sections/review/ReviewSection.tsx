import { ReviewList } from './ReviewList';
import { ReviewWorkspace } from './ReviewWorkspace';

export interface ReviewSectionProps {
  contentId: string | null;
  onNavigate: (contentId?: string | null) => void;
}

/** Section 6 Review: switches between the #/review list and #/review/<id> workspace. */
export function ReviewSection({ contentId, onNavigate }: ReviewSectionProps) {
  if (contentId) {
    return (
      <ReviewWorkspace
        contentId={contentId}
        onBackToList={() => onNavigate(null)}
      />
    );
  }
  return <ReviewList onOpenItem={(id) => onNavigate(id)} />;
}
