import { Card, Field, PillButton, Select, TextArea, TextInput } from '@magnm/design/react';
import type { PitchCatalog, PitchRuleViolation } from '../../api/pitch';
import {
  MAX_ARCH_ROLES,
  MAX_ROLE_POINTS,
  getDefaultFinalPlans,
  swapListItems,
  validatePitchText,
  validateSlug,
  type PitchDraftState,
} from './pitchUtils';

export interface PitchDetailsTabProps {
  draft: PitchDraftState;
  catalog?: PitchCatalog | null;
  reservedPresets: string[];
  violations: PitchRuleViolation[];
  onChange: (updater: (prev: PitchDraftState) => PitchDraftState) => void;
}

function getFieldViolation(violations: PitchRuleViolation[], field: string): string | null {
  const match = violations.find((v) => v.field === field);
  return match ? match.message : null;
}

function getPitchFieldError(
  violations: PitchRuleViolation[],
  field: string,
  liveValue?: string,
): string | null {
  const serverErr = getFieldViolation(violations, field);
  if (serverErr) return serverErr;
  return liveValue !== undefined ? validatePitchText(liveValue) : null;
}

function renderFieldError(err: string | null) {
  return err ? <span className="studio-field-error" role="alert">{err}</span> : undefined;
}

/** Editor tab 1: Details, contact info, Pitch preset copy, and architecture roles. */
export function PitchDetailsTab({
  draft,
  catalog = null,
  reservedPresets,
  violations,
  onChange,
}: PitchDetailsTabProps) {
  const liveSlugErr = validateSlug(draft.slug, reservedPresets);
  const slugErr = getFieldViolation(violations, 'slug') || liveSlugErr;
  const clientNameErr = getPitchFieldError(violations, 'client_name', draft.client_name);
  const businessNameErr = getPitchFieldError(violations, 'business_name', draft.business_name);
  const contactNameErr = getPitchFieldError(violations, 'contact.name', draft.contact.name);
  const contactEmailErr = getPitchFieldError(violations, 'contact.email', draft.contact.email);
  const contactPhoneErr = getPitchFieldError(violations, 'contact.phone', draft.contact.phone);
  const contactNotesErr = getPitchFieldError(violations, 'contact.notes', draft.contact.notes);
  const badgeErr = getPitchFieldError(violations, 'preset.badge', draft.preset.badge);
  const titleErr = getPitchFieldError(violations, 'preset.title', draft.preset.title);
  const leadErr = getPitchFieldError(violations, 'preset.lead', draft.preset.lead);
  const whatWeDoErr = getPitchFieldError(violations, 'preset.whatWeDo', draft.preset.whatWeDo);
  const portfolioErr = getPitchFieldError(violations, 'preset.portfolio', draft.preset.portfolio);
  const thisPlanErr = getPitchFieldError(violations, 'preset.thisPlan', draft.preset.thisPlan);
  const archHeadingErr = getPitchFieldError(
    violations,
    'preset.architectureBlock.heading',
    draft.preset.architectureBlock.heading,
  );
  const archSubErr = getPitchFieldError(
    violations,
    'preset.architectureBlock.sub',
    draft.preset.architectureBlock.sub,
  );
  const roles = draft.preset.architectureBlock.roles;

  const updatePreset = (patch: Partial<PitchDraftState['preset']>) => {
    onChange((prev) => ({
      ...prev,
      preset: { ...prev.preset, ...patch },
    }));
  };

  const updateArch = (patch: Partial<PitchDraftState['preset']['architectureBlock']>) => {
    onChange((prev) => ({
      ...prev,
      preset: {
        ...prev.preset,
        architectureBlock: { ...prev.preset.architectureBlock, ...patch },
      },
    }));
  };

  return (
    <div className="studio-stack">
      <Card>
        <div className="studio-stack-sm">
          <div className="mg-label">Proposal identity</div>
          <div className="studio-grid-2">
            <Field
              label="Slug"
              htmlFor="pitch-field-slug"
              hint={slugErr ? <span className="studio-field-error" role="alert">{slugErr}</span> : 'URL slug for client route'}
            >
              <TextInput
                id="pitch-field-slug"
                aria-invalid={Boolean(slugErr)}
                value={draft.slug}
                onChange={(e) => {
                  const nextSlug = e.target.value.toLowerCase();
                  onChange((prev) => ({
                    ...prev,
                    slug: nextSlug,
                    preset: { ...prev.preset, slug: nextSlug },
                  }));
                }}
              />
            </Field>

            <Field
              label="Kind"
              htmlFor="pitch-field-kind"
              hint={getFieldViolation(violations, 'kind') ? <span className="studio-field-error" role="alert">{getFieldViolation(violations, 'kind')}</span> : undefined}
            >
              <Select
                id="pitch-field-kind"
                value={draft.kind}
                onChange={(e) => {
                  const nextKind = e.target.value as PitchDraftState['kind'];
                  onChange((prev) => {
                    if (nextKind === 'final' && prev.kind !== 'final') {
                      const defaults = getDefaultFinalPlans(catalog);
                      return {
                        ...prev,
                        kind: nextKind,
                        payment_plan:
                          prev.payment_plan.length >= 2 ? prev.payment_plan : defaults.payment_plan,
                        payment_note: prev.payment_note || defaults.payment_note,
                        timeline: prev.timeline.length >= 1 ? prev.timeline : defaults.timeline,
                      };
                    }
                    return { ...prev, kind: nextKind };
                  });
                }}
              >
                <option value="proposal">Proposal</option>
                <option value="quote_form">Quote form</option>
                <option value="final">Final quotation</option>
              </Select>
            </Field>

            <Field
              label="Status"
              htmlFor="pitch-field-status"
              hint={getFieldViolation(violations, 'status') ? <span className="studio-field-error" role="alert">{getFieldViolation(violations, 'status')}</span> : undefined}
            >
              <Select
                id="pitch-field-status"
                value={draft.status}
                onChange={(e) =>
                  onChange((prev) => ({
                    ...prev,
                    status: e.target.value as PitchDraftState['status'],
                  }))
                }
              >
                <option value="draft">Draft</option>
                <option value="ready">Ready</option>
                <option value="sent">Sent</option>
                <option value="archived">Archived</option>
              </Select>
            </Field>

            <Field
              label="Client name"
              htmlFor="pitch-field-client-name"
              hint={renderFieldError(clientNameErr)}
            >
              <TextInput
                id="pitch-field-client-name"
                aria-invalid={Boolean(clientNameErr)}
                value={draft.client_name}
                onChange={(e) =>
                  onChange((prev) => ({
                    ...prev,
                    client_name: e.target.value,
                    preset: { ...prev.preset, clientName: e.target.value },
                  }))
                }
              />
            </Field>

            <Field
              label="Business name"
              htmlFor="pitch-field-business-name"
              hint={renderFieldError(businessNameErr)}
            >
              <TextInput
                id="pitch-field-business-name"
                aria-invalid={Boolean(businessNameErr)}
                value={draft.business_name}
                onChange={(e) =>
                  onChange((prev) => ({
                    ...prev,
                    business_name: e.target.value,
                    preset: { ...prev.preset, businessName: e.target.value },
                  }))
                }
              />
            </Field>
          </div>
        </div>
      </Card>

      <Card>
        <div className="studio-stack-sm">
          <div className="mg-label">Client contact</div>
          <div className="studio-grid-2">
            <Field
              label="Contact name"
              htmlFor="pitch-contact-name"
              hint={renderFieldError(contactNameErr)}
            >
              <TextInput
                id="pitch-contact-name"
                aria-invalid={Boolean(contactNameErr)}
                value={draft.contact.name}
                onChange={(e) =>
                  onChange((prev) => ({
                    ...prev,
                    contact: { ...prev.contact, name: e.target.value },
                  }))
                }
              />
            </Field>
            <Field
              label="Contact email"
              htmlFor="pitch-contact-email"
              hint={renderFieldError(contactEmailErr)}
            >
              <TextInput
                id="pitch-contact-email"
                type="email"
                aria-invalid={Boolean(contactEmailErr)}
                value={draft.contact.email}
                onChange={(e) =>
                  onChange((prev) => ({
                    ...prev,
                    contact: { ...prev.contact, email: e.target.value },
                  }))
                }
              />
            </Field>
            <Field
              label="Contact phone"
              htmlFor="pitch-contact-phone"
              hint={renderFieldError(contactPhoneErr)}
            >
              <TextInput
                id="pitch-contact-phone"
                aria-invalid={Boolean(contactPhoneErr)}
                value={draft.contact.phone}
                onChange={(e) =>
                  onChange((prev) => ({
                    ...prev,
                    contact: { ...prev.contact, phone: e.target.value },
                  }))
                }
              />
            </Field>
          </div>
          <Field
            label="Contact notes"
            htmlFor="pitch-contact-notes"
            hint={renderFieldError(contactNotesErr)}
          >
            <TextArea
              id="pitch-contact-notes"
              rows={2}
              aria-invalid={Boolean(contactNotesErr)}
              value={draft.contact.notes}
              onChange={(e) =>
                onChange((prev) => ({
                  ...prev,
                  contact: { ...prev.contact, notes: e.target.value },
                }))
              }
            />
          </Field>
        </div>
      </Card>

      <Card>
        <div className="studio-stack-sm">
          <div className="mg-label">Pitch copy</div>
          <div className="studio-grid-2">
            <Field
              label="Badge"
              htmlFor="pitch-preset-badge"
              hint={renderFieldError(badgeErr)}
            >
              <TextInput
                id="pitch-preset-badge"
                aria-invalid={Boolean(badgeErr)}
                value={draft.preset.badge}
                onChange={(e) => updatePreset({ badge: e.target.value })}
              />
            </Field>
            <Field
              label="Headline (title)"
              htmlFor="pitch-preset-title"
              hint={renderFieldError(titleErr)}
            >
              <TextInput
                id="pitch-preset-title"
                aria-invalid={Boolean(titleErr)}
                value={draft.preset.title}
                onChange={(e) => updatePreset({ title: e.target.value })}
              />
            </Field>
          </div>

          <Field
            label="Lead"
            htmlFor="pitch-preset-lead"
            hint={renderFieldError(leadErr)}
          >
            <TextArea
              id="pitch-preset-lead"
              rows={2}
              aria-invalid={Boolean(leadErr)}
              value={draft.preset.lead}
              onChange={(e) => updatePreset({ lead: e.target.value })}
            />
          </Field>

          <Field
            label="What we do"
            htmlFor="pitch-preset-whatwedo"
            hint={renderFieldError(whatWeDoErr)}
          >
            <TextInput
              id="pitch-preset-whatwedo"
              aria-invalid={Boolean(whatWeDoErr)}
              value={draft.preset.whatWeDo}
              onChange={(e) => updatePreset({ whatWeDo: e.target.value })}
            />
          </Field>

          <div className="studio-grid-2">
            <Field
              label="Portfolio"
              htmlFor="pitch-preset-portfolio"
              hint={renderFieldError(portfolioErr)}
            >
              <TextInput
                id="pitch-preset-portfolio"
                aria-invalid={Boolean(portfolioErr)}
                value={draft.preset.portfolio}
                onChange={(e) => updatePreset({ portfolio: e.target.value })}
              />
            </Field>
            <Field
              label="This plan"
              htmlFor="pitch-preset-thisplan"
              hint={renderFieldError(thisPlanErr)}
            >
              <TextInput
                id="pitch-preset-thisplan"
                aria-invalid={Boolean(thisPlanErr)}
                value={draft.preset.thisPlan}
                onChange={(e) => updatePreset({ thisPlan: e.target.value })}
              />
            </Field>
          </div>
        </div>
      </Card>

      <Card>
        <div className="studio-stack-sm">
          <div className="studio-section-bar">
            <span className="mg-label">Architecture block (up to {MAX_ARCH_ROLES} roles)</span>
            <PillButton
              variant="outline"
              size="sm"
              disabled={roles.length >= MAX_ARCH_ROLES}
              onClick={() =>
                updateArch({
                  roles: [...roles, { label: '', points: [''] }],
                })
              }
            >
              Add role
            </PillButton>
          </div>

          <Field
            label="Architecture heading"
            htmlFor="pitch-arch-heading"
            hint={renderFieldError(archHeadingErr)}
          >
            <TextInput
              id="pitch-arch-heading"
              aria-invalid={Boolean(archHeadingErr)}
              value={draft.preset.architectureBlock.heading}
              onChange={(e) => updateArch({ heading: e.target.value })}
            />
          </Field>

          <Field
            label="Architecture subheading"
            htmlFor="pitch-arch-sub"
            hint={renderFieldError(archSubErr)}
          >
            <TextInput
              id="pitch-arch-sub"
              aria-invalid={Boolean(archSubErr)}
              value={draft.preset.architectureBlock.sub}
              onChange={(e) => updateArch({ sub: e.target.value })}
            />
          </Field>

          {roles.length === 0 ? (
            <div className="mg-empty">No architecture roles yet. Add up to {MAX_ARCH_ROLES} roles.</div>
          ) : (
            <div className="studio-stack-sm">
              {roles.map((role, rIdx) => {
                const roleLabelErr = getPitchFieldError(
                  violations,
                  `preset.architectureBlock.roles[${rIdx}].label`,
                  role.label,
                );
                return (
                  <Card key={`role-${rIdx}`} outline>
                    <div className="studio-stack-sm">
                      <div className="studio-section-bar">
                        <span className="studio-mono">Role {rIdx + 1}</span>
                        <div className="studio-btn-group">
                          <PillButton
                            variant="quiet"
                            size="sm"
                            aria-label={`Move role ${rIdx + 1} up`}
                            disabled={rIdx === 0}
                            onClick={() => updateArch({ roles: swapListItems(roles, rIdx, rIdx - 1) })}
                          >
                            Up
                          </PillButton>
                          <PillButton
                            variant="quiet"
                            size="sm"
                            aria-label={`Move role ${rIdx + 1} down`}
                            disabled={rIdx === roles.length - 1}
                            onClick={() => updateArch({ roles: swapListItems(roles, rIdx, rIdx + 1) })}
                          >
                            Down
                          </PillButton>
                          <PillButton
                            variant="quiet"
                            size="sm"
                            aria-label={`Remove role ${rIdx + 1}`}
                            onClick={() =>
                              updateArch({
                                roles: roles.filter((_, idx) => idx !== rIdx),
                              })
                            }
                          >
                            Remove
                          </PillButton>
                        </div>
                      </div>

                      <Field
                        label="Role label"
                        htmlFor={`pitch-role-label-${rIdx}`}
                        hint={renderFieldError(roleLabelErr)}
                      >
                        <TextInput
                          id={`pitch-role-label-${rIdx}`}
                          aria-invalid={Boolean(roleLabelErr)}
                          value={role.label}
                          onChange={(e) => {
                            const nextRoles = roles.map((item, idx) =>
                              idx === rIdx ? { ...item, label: e.target.value } : item,
                            );
                            updateArch({ roles: nextRoles });
                          }}
                        />
                      </Field>

                      <div className="studio-stack-xs">
                        <div className="studio-section-bar">
                          <span className="mg-label">Points ({role.points.length}/{MAX_ROLE_POINTS})</span>
                          <PillButton
                            variant="outline"
                            size="sm"
                            disabled={role.points.length >= MAX_ROLE_POINTS}
                            onClick={() => {
                              const nextRoles = roles.map((item, idx) =>
                                idx === rIdx ? { ...item, points: [...item.points, ''] } : item,
                              );
                              updateArch({ roles: nextRoles });
                            }}
                          >
                            Add point
                          </PillButton>
                        </div>

                        {role.points.map((pt, pIdx) => {
                          const pointErr = getPitchFieldError(
                            violations,
                            `preset.architectureBlock.roles[${rIdx}].points[${pIdx}]`,
                            pt,
                          );
                          return (
                            <div key={`role-${rIdx}-pt-${pIdx}`} className="studio-stack-xs">
                              <div className="studio-pitch-point-row">
                                <TextInput
                                  aria-label={`Role ${rIdx + 1} point ${pIdx + 1}`}
                                  aria-invalid={Boolean(pointErr)}
                                  value={pt}
                                  onChange={(e) => {
                                    const nextRoles = roles.map((item, idx) => {
                                      if (idx !== rIdx) return item;
                                      const nextPts = item.points.map((p, j) =>
                                        j === pIdx ? e.target.value : p,
                                      );
                                      return { ...item, points: nextPts };
                                    });
                                    updateArch({ roles: nextRoles });
                                  }}
                                />
                                <div className="studio-row-actions">
                                  <PillButton
                                    variant="quiet"
                                    size="sm"
                                    aria-label={`Move role ${rIdx + 1} point ${pIdx + 1} up`}
                                    disabled={pIdx === 0}
                                    onClick={() => {
                                      const nextRoles = roles.map((item, idx) =>
                                        idx === rIdx
                                          ? { ...item, points: swapListItems(item.points, pIdx, pIdx - 1) }
                                          : item,
                                      );
                                      updateArch({ roles: nextRoles });
                                    }}
                                  >
                                    Up
                                  </PillButton>
                                  <PillButton
                                    variant="quiet"
                                    size="sm"
                                    aria-label={`Move role ${rIdx + 1} point ${pIdx + 1} down`}
                                    disabled={pIdx === role.points.length - 1}
                                    onClick={() => {
                                      const nextRoles = roles.map((item, idx) =>
                                        idx === rIdx
                                          ? { ...item, points: swapListItems(item.points, pIdx, pIdx + 1) }
                                          : item,
                                      );
                                      updateArch({ roles: nextRoles });
                                    }}
                                  >
                                    Down
                                  </PillButton>
                                  <PillButton
                                    variant="quiet"
                                    size="sm"
                                    aria-label={`Remove role ${rIdx + 1} point ${pIdx + 1}`}
                                    onClick={() => {
                                      const nextRoles = roles.map((item, idx) =>
                                        idx === rIdx
                                          ? { ...item, points: item.points.filter((_, j) => j !== pIdx) }
                                          : item,
                                      );
                                      updateArch({ roles: nextRoles });
                                    }}
                                  >
                                    Remove
                                  </PillButton>
                                </div>
                              </div>
                              {pointErr ? (
                                <span className="studio-field-error" role="alert">
                                  {pointErr}
                                </span>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </div>
      </Card>

      <Card>
        <Field
          label="Internal notes"
          htmlFor="pitch-field-notes"
          hint={getFieldViolation(violations, 'notes') ? <span className="studio-field-error" role="alert">{getFieldViolation(violations, 'notes')}</span> : undefined}
        >
          <TextArea
            id="pitch-field-notes"
            rows={3}
            value={draft.notes}
            onChange={(e) => onChange((prev) => ({ ...prev, notes: e.target.value }))}
          />
        </Field>
      </Card>
    </div>
  );
}
