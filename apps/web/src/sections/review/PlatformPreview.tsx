import { useMemo, useState } from 'react';
import { Card, Field, PillButton, Select } from '@magnm/design/react';
import { contentFileUrl } from '../../api/review';
import type { ContentItem, PlatformCheckResult } from '../../types';
import { SafeMarkdown } from '../../ui/SafeMarkdown';
import {
  KNOWN_PLATFORMS,
  countWords,
  extractHashtags,
  normalizePlatform,
  splitXThread,
} from './reviewUtils';

export interface PlatformPreviewProps {
  item: ContentItem;
  body: string;
  platformCheck: PlatformCheckResult;
}

const PLATFORM_OPTIONS = ['linkedin', 'x', 'instagram', 'youtube', 'blog'] as const;

const ASPECT_BY_PLATFORM: Record<string, { label: string; cssRatio: string }> = {
  linkedin: { label: '1.91:1 / 4:5 crop', cssRatio: '1.91 / 1' },
  x: { label: '16:9 crop', cssRatio: '16 / 9' },
  instagram: { label: '4:5 crop', cssRatio: '4 / 5' },
  youtube: { label: '16:9 thumbnail', cssRatio: '16 / 9' },
  blog: { label: '16:9 cover', cssRatio: '16 / 9' },
};

/** Monochrome neutral platform preview using engine/platforms.py limits and crop norms. */
export function PlatformPreview({ item, body, platformCheck }: PlatformPreviewProps) {
  const initialPlatform = normalizePlatform(platformCheck?.platform || item.platform);
  const [selectedPlatform, setSelectedPlatform] = useState(
    PLATFORM_OPTIONS.includes(initialPlatform as (typeof PLATFORM_OPTIONS)[number])
      ? initialPlatform
      : 'linkedin',
  );
  const [expanded, setExpanded] = useState(false);

  const limits = useMemo(() => {
    if (selectedPlatform === normalizePlatform(platformCheck?.platform) && platformCheck?.limits) {
      return { ...KNOWN_PLATFORMS[selectedPlatform], ...platformCheck.limits };
    }
    return KNOWN_PLATFORMS[selectedPlatform] || KNOWN_PLATFORMS.linkedin;
  }, [selectedPlatform, platformCheck]);

  const images = useMemo(
    () =>
      (item.files || []).filter(
        (f) => f && (f.kind === 'image' || f.kind === 'card') && Boolean(f.path),
      ),
    [item.files],
  );

  const hashtags = useMemo(() => extractHashtags(body), [body]);
  const words = useMemo(() => countWords(body), [body]);
  const charCount = body.length;
  const xParts = useMemo(() => splitXThread(body), [body]);
  const cropSpec = ASPECT_BY_PLATFORM[selectedPlatform] || ASPECT_BY_PLATFORM.linkedin;

  const hookCutoff = limits.hook_chars || limits.description_hook_chars || null;
  const isTruncated = hookCutoff !== null && charCount > hookCutoff;
  const visibleBody = !expanded && isTruncated ? body.slice(0, hookCutoff) : body;

  const overCharLimit = limits.max_chars !== undefined && charCount > limits.max_chars;
  const overTagLimit = limits.max_hashtags !== undefined && hashtags.length > limits.max_hashtags;
  const underMinWords = limits.min_words !== undefined && words < limits.min_words;
  const overMaxWords = limits.max_words !== undefined && words > limits.max_words;
  const overTitleLimit =
    limits.title_max_chars !== undefined && (item.title || '').length > limits.title_max_chars;

  const blogHeadings = useMemo(
    () =>
      body
        .split('\n')
        .filter((line) => /^\s*##(?!#)\s*\S/.test(line))
        .map((line) => line.replace(/^\s*##\s*/, '').trim()),
    [body],
  );
  const hasTldr = /\btl\s*;?\s*dr\b/i.test(body);

  return (
    <div className="studio-stack-sm" data-testid="platform-preview">
      <div className="studio-section-bar">
        <Field label="Preview frame" htmlFor="preview-platform-select">
          <Select
            id="preview-platform-select"
            value={selectedPlatform}
            onChange={(e) => {
              setSelectedPlatform(e.target.value);
              setExpanded(false);
            }}
          >
            {PLATFORM_OPTIONS.map((plat) => (
              <option key={plat} value={plat}>
                {plat.toUpperCase()}
              </option>
            ))}
          </Select>
        </Field>

        <div className="studio-chip-row">
          {limits.max_chars !== undefined ? (
            <span className="mg-chip">
              {charCount} / {limits.max_chars} chars{overCharLimit ? ' (over limit)' : ''}
            </span>
          ) : null}
          {limits.min_words !== undefined || limits.max_words !== undefined ? (
            <span className="mg-chip">
              {words} words (target {limits.min_words ?? 0}-{limits.max_words ?? 0}
              {underMinWords ? ', short' : overMaxWords ? ', long' : ''})
            </span>
          ) : null}
          {limits.max_hashtags !== undefined ? (
            <span className="mg-chip">
              {hashtags.length} / {limits.max_hashtags} hashtags{overTagLimit ? ' (over)' : ''}
            </span>
          ) : null}
          {limits.title_max_chars !== undefined ? (
            <span className="mg-chip">
              Title {(item.title || '').length} / {limits.title_max_chars} chars
              {overTitleLimit ? ' (over)' : ''}
            </span>
          ) : null}
        </div>
      </div>

      {selectedPlatform === 'x' ? (
        <div className="studio-stack-sm">
          {xParts.length === 0 ? (
            <div className="mg-empty">Empty draft.</div>
          ) : (
            xParts.map((part, idx) => {
              const partOver = part.length > (limits.max_chars ?? 280);
              return (
                <Card key={`x-part-${idx}`} outline className="studio-preview-frame">
                  <div className="studio-preview-head">
                    <span className="mg-label">
                      X POST {idx + 1}/{xParts.length}
                    </span>
                    <span className="studio-mono">
                      {part.length} / {limits.max_chars ?? 280} chars
                      {partOver ? ' (OVER LIMIT)' : ''}
                    </span>
                  </div>
                  <p className="studio-preview-text">{part}</p>
                  {idx === 0 && images.length > 0 ? (
                    <div className="studio-preview-media-grid">
                      {images.map((img) => (
                        <div
                          key={img.path}
                          className="studio-preview-crop"
                          style={{ aspectRatio: cropSpec.cssRatio }}
                        >
                          <img
                            src={contentFileUrl(item.id, img.path)}
                            alt={img.alt_text || img.path}
                          />
                          <span className="studio-mono studio-preview-crop-tag">{cropSpec.label}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </Card>
              );
            })
          )}
        </div>
      ) : selectedPlatform === 'blog' ? (
        <Card outline className="studio-preview-frame">
          <div className="studio-preview-head">
            <span className="mg-label">BLOG PREVIEW</span>
            <span className="studio-mono">
              {hasTldr ? 'TL;DR present' : 'Missing TL;DR'} · {blogHeadings.length} headings
            </span>
          </div>
          <h3 className="studio-preview-title">{item.title || 'Untitled article'}</h3>
          {images.length > 0 ? (
            <div className="studio-preview-media-grid">
              {images.map((img) => (
                <div
                  key={img.path}
                  className="studio-preview-crop"
                  style={{ aspectRatio: cropSpec.cssRatio }}
                >
                  <img
                    src={contentFileUrl(item.id, img.path)}
                    alt={img.alt_text || img.path}
                  />
                  <span className="studio-mono studio-preview-crop-tag">{cropSpec.label}</span>
                </div>
              ))}
            </div>
          ) : null}
          <SafeMarkdown text={body} />
        </Card>
      ) : (
        <Card outline className="studio-preview-frame">
          <div className="studio-preview-head">
            <span className="mg-label">{selectedPlatform.toUpperCase()} PREVIEW</span>
            {hookCutoff ? (
              <span className="studio-mono">
                See more cutoff: {hookCutoff} chars
              </span>
            ) : null}
          </div>

          {selectedPlatform === 'youtube' ? (
            <div className="studio-stack-xs">
              <div className="studio-preview-title">{item.title || 'Untitled video'}</div>
              {images.length > 0 ? (
                <div
                  className="studio-preview-crop"
                  style={{ aspectRatio: cropSpec.cssRatio }}
                >
                  <img
                    src={contentFileUrl(item.id, images[0].path)}
                    alt={images[0].alt_text || images[0].path}
                  />
                  <span className="studio-mono studio-preview-crop-tag">{cropSpec.label}</span>
                </div>
              ) : null}
            </div>
          ) : null}

          {selectedPlatform === 'instagram' && images.length > 0 ? (
            <div className="studio-preview-media-grid">
              {images.map((img) => (
                <div
                  key={img.path}
                  className="studio-preview-crop"
                  style={{ aspectRatio: cropSpec.cssRatio }}
                >
                  <img
                    src={contentFileUrl(item.id, img.path)}
                    alt={img.alt_text || img.path}
                  />
                  <span className="studio-mono studio-preview-crop-tag">{cropSpec.label}</span>
                </div>
              ))}
            </div>
          ) : null}

          <div className="studio-preview-body">
            <p className="studio-preview-text">
              {visibleBody}
              {!expanded && isTruncated ? '...' : ''}
            </p>
            {isTruncated ? (
              <div className="studio-preview-cutoff">
                <span className="studio-mono">Cutoff at {hookCutoff} chars</span>
                <PillButton
                  variant="quiet"
                  size="sm"
                  onClick={() => setExpanded((prev) => !prev)}
                >
                  {expanded ? 'Show less' : 'See more'}
                </PillButton>
              </div>
            ) : null}
          </div>

          {selectedPlatform === 'linkedin' && images.length > 0 ? (
            <div className="studio-preview-media-grid">
              {images.map((img) => (
                <div
                  key={img.path}
                  className="studio-preview-crop"
                  style={{ aspectRatio: cropSpec.cssRatio }}
                >
                  <img
                    src={contentFileUrl(item.id, img.path)}
                    alt={img.alt_text || img.path}
                  />
                  <span className="studio-mono studio-preview-crop-tag">{cropSpec.label}</span>
                </div>
              ))}
            </div>
          ) : null}
        </Card>
      )}
    </div>
  );
}
