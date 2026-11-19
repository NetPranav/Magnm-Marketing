import { parsePitchParam } from '../../router';
import { PitchEditor } from './PitchEditor';
import { PitchList } from './PitchList';

export interface PitchSectionProps {
  param: string | null;
  onNavigate: (nextParam?: string | null) => void;
}

/** Section 7 Pitch: switches between the #/pitch list and #/pitch/<id> editor. */
export function PitchSection({ param, onNavigate }: PitchSectionProps) {
  const { proposalId } = parsePitchParam(param);
  if (proposalId) {
    return (
      <PitchEditor
        proposalId={proposalId}
        onBackToList={() => onNavigate(null)}
        onPresent={(id) => onNavigate(`${id}/present`)}
      />
    );
  }
  return (
    <PitchList
      onOpenProposal={(id) => onNavigate(id)}
      onPresentProposal={(id) => onNavigate(`${id}/present`)}
    />
  );
}
