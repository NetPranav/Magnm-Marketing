import {
  Card,
  Field,
  PillButton,
  Select,
  TextArea,
  TextInput,
} from '@magnm/design/react';
import {
  formatRupees,
  type PitchCatalog,
  type PitchPricingSummary,
  type PitchRuleViolation,
  type PitchServicePlanPeriod,
} from '../../api/pitch';
import {
  MAX_PAYMENT_ROWS,
  MAX_SERVICE_PLANS,
  MAX_TIMELINE_STEPS,
  MIN_PAYMENT_ROWS,
  MIN_TIMELINE_STEPS,
  SERVICE_PLAN_PERIOD_OPTIONS,
  createServicePlanId,
  getDefaultFinalPlans,
  swapListItems,
  validatePitchText,
  type PitchDraftState,
} from './pitchUtils';

export interface PitchPlansTabProps {
  draft: PitchDraftState;
  catalog: PitchCatalog | null;
  pricingSummary: PitchPricingSummary;
  dirty: boolean;
  violations: PitchRuleViolation[];
  onChange: (updater: (prev: PitchDraftState) => PitchDraftState) => void;
}

function getFieldViolation(violations: PitchRuleViolation[], field: string): string | null {
  const match = violations.find((v) => v.field === field);
  return match ? match.message : null;
}

function getTextFieldError(
  violations: PitchRuleViolation[],
  field: string,
  liveValue?: string,
): string | null {
  const serverErr = getFieldViolation(violations, field);
  if (serverErr) return serverErr;
  return liveValue !== undefined ? validatePitchText(liveValue) : null;
}

function renderFieldError(err: string | null) {
  return err ? (
    <span className="studio-field-error" role="alert">
      {err}
    </span>
  ) : undefined;
}

/** Editor tab 4 (Final quotation): Payment plan, payment note, timeline steps, and service plans. */
export function PitchPlansTab({
  draft,
  catalog,
  pricingSummary,
  dirty,
  violations,
  onChange,
}: PitchPlansTabProps) {
  const paymentRows = draft.payment_plan;
  const timelineSteps = draft.timeline;
  const servicePlans = draft.service_plans;
  const defaults = getDefaultFinalPlans(catalog);

  const percentSum = paymentRows.reduce(
    (sum, row) => sum + (Number.isFinite(row.percent) ? row.percent : 0),
    0,
  );
  const sumValid = percentSum === 100;
  const paymentPlanServerErr = getFieldViolation(violations, 'payment_plan');
  const paymentNoteErr = getTextFieldError(violations, 'payment_note', draft.payment_note);
  const timelineServerErr = getFieldViolation(violations, 'timeline');
  const servicePlansServerErr = getFieldViolation(violations, 'service_plans');
  const enginePaymentRows = pricingSummary.payment_plan || [];

  const handleResetPaymentPlan = () => {
    onChange((prev) => ({
      ...prev,
      payment_plan: defaults.payment_plan.map((r) => ({ ...r })),
    }));
  };

  const handleAddPaymentRow = () => {
    if (paymentRows.length >= MAX_PAYMENT_ROWS) return;
    const remainder = Math.max(1, Math.min(100, 100 - percentSum));
    onChange((prev) => ({
      ...prev,
      payment_plan: [...prev.payment_plan, { percent: remainder, label: '' }],
    }));
  };

  const handleRemovePaymentRow = (idx: number) => {
    if (paymentRows.length <= MIN_PAYMENT_ROWS) return;
    onChange((prev) => ({
      ...prev,
      payment_plan: prev.payment_plan.filter((_, i) => i !== idx),
    }));
  };

  const handleResetTimeline = () => {
    onChange((prev) => ({
      ...prev,
      timeline: defaults.timeline.map((s) => ({ ...s })),
    }));
  };

  const handleAddTimelineStep = () => {
    if (timelineSteps.length >= MAX_TIMELINE_STEPS) return;
    onChange((prev) => ({
      ...prev,
      timeline: [...prev.timeline, { title: '', when: '', detail: '' }],
    }));
  };

  const handleAddServicePlan = () => {
    if (servicePlans.length >= MAX_SERVICE_PLANS) return;
    const usedIds = new Set<string>([
      ...Object.keys(catalog?.items || {}),
      ...draft.custom_items.map((c) => c.id),
      ...servicePlans.map((sp) => sp.id),
    ]);
    const nextId = createServicePlanId(`plan-${servicePlans.length + 1}`, usedIds);
    onChange((prev) => ({
      ...prev,
      service_plans: [
        ...prev.service_plans,
        {
          id: nextId,
          name: '',
          description: '',
          price: 0,
          period: 'monthly',
        },
      ],
    }));
  };

  return (
    <div className="studio-stack">
      <section aria-label="Payment plan">
        <Card>
          <div className="studio-stack-sm">
            <div className="studio-section-bar">
              <span className="mg-label">
                Payment plan ({paymentRows.length}/{MAX_PAYMENT_ROWS})
              </span>
              <div className="studio-btn-group">
                <PillButton
                  variant="outline"
                  size="sm"
                  onClick={handleResetPaymentPlan}
                >
                  Reset to Pitch&apos;s default
                </PillButton>
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={paymentRows.length >= MAX_PAYMENT_ROWS}
                  onClick={handleAddPaymentRow}
                >
                  Add row
                </PillButton>
              </div>
            </div>

          <div className="studio-section-bar">
            <span className="studio-mono" data-testid="payment-plan-sum">
              Total split: {percentSum}%
            </span>
            {dirty ? (
              <span className="studio-mono">Save to refresh rupee amounts</span>
            ) : null}
          </div>

          {!sumValid ? (
            <div className="mg-notice" role="alert">
              Payment percentages sum to {percentSum}%. They must equal 100%.
            </div>
          ) : null}

          {paymentPlanServerErr ? (
            <div className="mg-notice" role="alert">
              {paymentPlanServerErr}
            </div>
          ) : null}

          <div className="studio-stack-sm">
            {paymentRows.map((row, idx) => {
              const pctErr = getFieldViolation(violations, `payment_plan[${idx}].percent`);
              const labelErr = getTextFieldError(
                violations,
                `payment_plan[${idx}].label`,
                row.label,
              );
              const engineRow = !dirty ? enginePaymentRows[idx] : undefined;
              const amountText =
                engineRow && typeof engineRow.amount === 'number'
                  ? formatRupees(engineRow.amount)
                  : 'Save to compute';

              return (
                <Card key={`pay-row-${idx}`} outline>
                  <div className="studio-stack-sm">
                    <div className="studio-section-bar">
                      <span className="studio-mono">
                        Installment {idx + 1} · {amountText}
                      </span>
                      <PillButton
                        variant="quiet"
                        size="sm"
                        aria-label={`Remove payment row ${idx + 1}`}
                        disabled={paymentRows.length <= MIN_PAYMENT_ROWS}
                        onClick={() => handleRemovePaymentRow(idx)}
                      >
                        Remove
                      </PillButton>
                    </div>
                    <div className="studio-pitch-plan-row">
                      <Field
                        label="Percent (%)"
                        htmlFor={`pay-percent-${idx}`}
                        hint={renderFieldError(pctErr)}
                      >
                        <TextInput
                          id={`pay-percent-${idx}`}
                          type="number"
                          min={1}
                          max={100}
                          step={1}
                          aria-invalid={Boolean(pctErr)}
                          value={String(row.percent)}
                          onChange={(e) => {
                            const val = Math.trunc(Number(e.target.value) || 0);
                            onChange((prev) => ({
                              ...prev,
                              payment_plan: prev.payment_plan.map((item, i) =>
                                i === idx ? { ...item, percent: val } : item,
                              ),
                            }));
                          }}
                        />
                      </Field>
                      <Field
                        label="Label"
                        htmlFor={`pay-label-${idx}`}
                        hint={renderFieldError(labelErr)}
                      >
                        <TextInput
                          id={`pay-label-${idx}`}
                          aria-invalid={Boolean(labelErr)}
                          placeholder="e.g. to start"
                          value={row.label}
                          onChange={(e) =>
                            onChange((prev) => ({
                              ...prev,
                              payment_plan: prev.payment_plan.map((item, i) =>
                                i === idx ? { ...item, label: e.target.value } : item,
                              ),
                            }))
                          }
                        />
                      </Field>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
          </div>
        </Card>
      </section>

      <Card>
        <Field
          label="Payment note"
          htmlFor="pitch-payment-note"
          hint={renderFieldError(paymentNoteErr)}
        >
          <TextArea
            id="pitch-payment-note"
            rows={2}
            aria-invalid={Boolean(paymentNoteErr)}
            value={draft.payment_note}
            onChange={(e) =>
              onChange((prev) => ({ ...prev, payment_note: e.target.value }))
            }
          />
        </Field>
      </Card>

      <section aria-label="Timeline">
        <Card>
          <div className="studio-stack-sm">
            <div className="studio-section-bar">
              <span className="mg-label">
                Timeline ({timelineSteps.length}/{MAX_TIMELINE_STEPS})
              </span>
              <div className="studio-btn-group">
                <PillButton
                  variant="outline"
                  size="sm"
                  onClick={handleResetTimeline}
                >
                  Reset to Pitch&apos;s default
                </PillButton>
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={timelineSteps.length >= MAX_TIMELINE_STEPS}
                  onClick={handleAddTimelineStep}
                >
                  Add step
                </PillButton>
              </div>
            </div>

          {timelineServerErr ? (
            <div className="mg-notice" role="alert">
              {timelineServerErr}
            </div>
          ) : null}

          <div className="studio-stack-sm">
            {timelineSteps.map((step, idx) => {
              const titleErr = getTextFieldError(
                violations,
                `timeline[${idx}].title`,
                step.title,
              );
              const whenErr = getTextFieldError(
                violations,
                `timeline[${idx}].when`,
                step.when,
              );
              const detailErr = getTextFieldError(
                violations,
                `timeline[${idx}].detail`,
                step.detail,
              );

              return (
                <Card key={`timeline-step-${idx}`} outline>
                  <div className="studio-stack-sm">
                    <div className="studio-section-bar">
                      <span className="studio-mono">
                        Step {String(idx + 1).padStart(2, '0')}
                      </span>
                      <div className="studio-btn-group">
                        <PillButton
                          variant="quiet"
                          size="sm"
                          aria-label={`Move step ${idx + 1} up`}
                          disabled={idx === 0}
                          onClick={() =>
                            onChange((prev) => ({
                              ...prev,
                              timeline: swapListItems(prev.timeline, idx, idx - 1),
                            }))
                          }
                        >
                          Up
                        </PillButton>
                        <PillButton
                          variant="quiet"
                          size="sm"
                          aria-label={`Move step ${idx + 1} down`}
                          disabled={idx === timelineSteps.length - 1}
                          onClick={() =>
                            onChange((prev) => ({
                              ...prev,
                              timeline: swapListItems(prev.timeline, idx, idx + 1),
                            }))
                          }
                        >
                          Down
                        </PillButton>
                        <PillButton
                          variant="quiet"
                          size="sm"
                          aria-label={`Remove step ${idx + 1}`}
                          disabled={timelineSteps.length <= MIN_TIMELINE_STEPS}
                          onClick={() =>
                            onChange((prev) => ({
                              ...prev,
                              timeline: prev.timeline.filter((_, i) => i !== idx),
                            }))
                          }
                        >
                          Remove
                        </PillButton>
                      </div>
                    </div>

                    <div className="studio-grid-2">
                      <Field
                        label="Title"
                        htmlFor={`timeline-title-${idx}`}
                        hint={renderFieldError(titleErr)}
                      >
                        <TextInput
                          id={`timeline-title-${idx}`}
                          aria-invalid={Boolean(titleErr)}
                          placeholder="e.g. Architecture & UI/UX Screens"
                          value={step.title}
                          onChange={(e) =>
                            onChange((prev) => ({
                              ...prev,
                              timeline: prev.timeline.map((item, i) =>
                                i === idx ? { ...item, title: e.target.value } : item,
                              ),
                            }))
                          }
                        />
                      </Field>
                      <Field
                        label="When"
                        htmlFor={`timeline-when-${idx}`}
                        hint={renderFieldError(whenErr)}
                      >
                        <TextInput
                          id={`timeline-when-${idx}`}
                          aria-invalid={Boolean(whenErr)}
                          placeholder="e.g. Week 1"
                          value={step.when}
                          onChange={(e) =>
                            onChange((prev) => ({
                              ...prev,
                              timeline: prev.timeline.map((item, i) =>
                                i === idx ? { ...item, when: e.target.value } : item,
                              ),
                            }))
                          }
                        />
                      </Field>
                    </div>

                    <Field
                      label="Detail"
                      htmlFor={`timeline-detail-${idx}`}
                      hint={renderFieldError(detailErr)}
                    >
                      <TextArea
                        id={`timeline-detail-${idx}`}
                        rows={2}
                        aria-invalid={Boolean(detailErr)}
                        placeholder="Describe what happens in this phase"
                        value={step.detail}
                        onChange={(e) =>
                          onChange((prev) => ({
                            ...prev,
                            timeline: prev.timeline.map((item, i) =>
                              i === idx ? { ...item, detail: e.target.value } : item,
                            ),
                          }))
                        }
                      />
                    </Field>
                  </div>
                </Card>
              );
            })}
          </div>
          </div>
        </Card>
      </section>

      <section aria-label="Service plans">
        <Card>
        <div className="studio-stack-sm">
          <div className="studio-section-bar">
            <span className="mg-label">
              Service plans ({servicePlans.length}/{MAX_SERVICE_PLANS})
            </span>
            <PillButton
              variant="outline"
              size="sm"
              disabled={servicePlans.length >= MAX_SERVICE_PLANS}
              onClick={handleAddServicePlan}
            >
              Add service plan
            </PillButton>
          </div>

          {servicePlansServerErr ? (
            <div className="mg-notice" role="alert">
              {servicePlansServerErr}
            </div>
          ) : null}

          {servicePlans.length === 0 ? (
            <div className="studio-hint">
              Add up to {MAX_SERVICE_PLANS} optional one-time or recurring service plans (for example, monthly maintenance or annual hosting).
            </div>
          ) : (
            <div className="studio-stack-sm">
              {servicePlans.map((sp, idx) => {
                const nameErr = getTextFieldError(
                  violations,
                  `service_plans[${idx}].name`,
                  sp.name,
                );
                const descErr = getTextFieldError(
                  violations,
                  `service_plans[${idx}].description`,
                  sp.description,
                );
                const priceErr = getFieldViolation(violations, `service_plans[${idx}].price`);
                const periodErr = getFieldViolation(violations, `service_plans[${idx}].period`);

                return (
                  <Card key={sp.id || `sp-${idx}`} outline>
                    <div className="studio-stack-sm">
                      <div className="studio-section-bar">
                        <span className="studio-mono">
                          Plan {idx + 1} · {sp.id}
                        </span>
                        <PillButton
                          variant="quiet"
                          size="sm"
                          aria-label={`Remove service plan ${idx + 1}`}
                          onClick={() =>
                            onChange((prev) => ({
                              ...prev,
                              service_plans: prev.service_plans.filter((_, i) => i !== idx),
                            }))
                          }
                        >
                          Remove
                        </PillButton>
                      </div>

                      <div className="studio-grid-2">
                        <Field
                          label="Plan name"
                          htmlFor={`sp-name-${idx}`}
                          hint={renderFieldError(nameErr)}
                        >
                          <TextInput
                            id={`sp-name-${idx}`}
                            aria-invalid={Boolean(nameErr)}
                            placeholder="e.g. Monthly Care"
                            value={sp.name}
                            onChange={(e) =>
                              onChange((prev) => ({
                                ...prev,
                                service_plans: prev.service_plans.map((item, i) =>
                                  i === idx ? { ...item, name: e.target.value } : item,
                                ),
                              }))
                            }
                          />
                        </Field>
                        <Field
                          label="Description"
                          htmlFor={`sp-desc-${idx}`}
                          hint={renderFieldError(descErr)}
                        >
                          <TextInput
                            id={`sp-desc-${idx}`}
                            aria-invalid={Boolean(descErr)}
                            placeholder="e.g. Updates, backups, and uptime checks"
                            value={sp.description}
                            onChange={(e) =>
                              onChange((prev) => ({
                                ...prev,
                                service_plans: prev.service_plans.map((item, i) =>
                                  i === idx ? { ...item, description: e.target.value } : item,
                                ),
                              }))
                            }
                          />
                        </Field>
                      </div>

                      <div className="studio-grid-2">
                        <Field
                          label="Price (INR)"
                          htmlFor={`sp-price-${idx}`}
                          hint={renderFieldError(priceErr)}
                        >
                          <TextInput
                            id={`sp-price-${idx}`}
                            type="number"
                            min={0}
                            max={10000000}
                            step={1}
                            aria-invalid={Boolean(priceErr)}
                            value={String(sp.price)}
                            onChange={(e) => {
                              const parsed = Math.max(0, Math.trunc(Number(e.target.value) || 0));
                              onChange((prev) => ({
                                ...prev,
                                service_plans: prev.service_plans.map((item, i) =>
                                  i === idx ? { ...item, price: parsed } : item,
                                ),
                              }));
                            }}
                          />
                        </Field>
                        <Field
                          label="Billing period"
                          htmlFor={`sp-period-${idx}`}
                          hint={renderFieldError(periodErr)}
                        >
                          <Select
                            id={`sp-period-${idx}`}
                            value={sp.period}
                            onChange={(e) =>
                              onChange((prev) => ({
                                ...prev,
                                service_plans: prev.service_plans.map((item, i) =>
                                  i === idx
                                    ? {
                                        ...item,
                                        period: e.target.value as PitchServicePlanPeriod,
                                      }
                                    : item,
                                ),
                              }))
                            }
                          >
                            {SERVICE_PLAN_PERIOD_OPTIONS.map((opt) => (
                              <option key={opt.value} value={opt.value}>
                                {opt.label}
                              </option>
                            ))}
                          </Select>
                        </Field>
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </div>
      </Card>
      </section>
    </div>
  );
}
