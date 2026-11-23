import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { PitchTab } from './components/PitchTab';
import type { ActionInput } from './actions';
import type { PhoneAction, PitchCloudInfo, PitchProposalItem, QuoteRequestItem } from './types';

const VERSION = '2026-11-23T10:00:00.123456+00:00';
const CATALOG = {
  items: {
    w_front: { g: 'Web & SaaS', n: 'Responsive Web Application', p: 6000 },
    w_anim: { g: 'Web & SaaS', n: 'Advanced UI', p: 1800 },
    b_api: { g: 'Backend', n: 'REST API', p: 5000 },
  },
  groups: ['Web & SaaS', 'Mobile app', 'Backend'],
  presets: [{ slug: 'urban-company', name: 'Urban Company-style' }],
};

function cloud(over: Partial<PitchCloudInfo> = {}): PitchCloudInfo {
  return {
    version: VERSION,
    editable: true,
    status: 'draft',
    client_name: 'Acme',
    business_name: 'Acme Co',
    contact: { name: 'Acme Owner', phone: '+91 1' },
    notes: 'Internal',
    preset: { title: 'Plan', lead: 'Lead text', whatWeDo: 'We do', portfolio: 'site', thisPlan: 'This plan', initialSelected: ['w_front'], sections: { p2: ['w_front', 'w_anim'], p3: [], p4: ['b_api'], p5: [] } },
    hidden_sheets: [],
    hidden_about: [],
    price_overrides: {},
    share_id: 'AAAAAAAAAAAAAAAAAAAAAA',
    link_status: null,
    link_url: null,
    truncated: false,
    catalog: CATALOG,
    ...over,
  };
}

const ACME: PitchProposalItem = { id: 'prop_acme', name: 'Acme Co · Acme', kind: 'proposal', updated_at: VERSION, payload: { mode: 'present' }, cloud: cloud() };
const DEFAULT: PitchProposalItem = { id: 'prop_default', name: 'Default quotation', kind: 'default', updated_at: VERSION, payload: { mode: 'form' }, cloud: cloud({ editable: false, share_id: 'DDDDDDDDDDDDDDDDDDDDDD' }) };
const FINAL: PitchProposalItem = { id: 'prop_final', name: 'Final one', kind: 'final', updated_at: VERSION, payload: {}, cloud: cloud({ client_name: 'Final', share_id: 'FFFFFFFFFFFFFFFFFFFFFF' }) };
const CONF: PitchProposalItem = { id: 'prop_conf', confidential: true };
const LAPTOP_ONLY: PitchProposalItem = { id: 'prop_lap', name: 'Only on laptop', kind: 'proposal', payload: { mode: 'present' } };

function action(partial: Partial<PhoneAction>): PhoneAction {
  return { id: 'a1', kind: 'pitch_link', target_id: 'prop_acme', payload: { action: 'publish' }, label: null, created_at: '', owner_uid: 'u', sent: true, state: 'waiting', result: null, ...partial };
}

interface Props {
  proposals?: PitchProposalItem[];
  actions?: PhoneAction[];
  requests?: QuoteRequestItem[];
  signedIn?: boolean;
  queue?: (input: ActionInput, ok: string) => Promise<boolean>;
  copy?: (text: string) => Promise<void> | void;
}

function renderTab(p: Props = {}) {
  const queue = p.queue ?? vi.fn(async () => true);
  const copy = p.copy ?? vi.fn();
  render(
    <PitchTab
      proposals={p.proposals ?? [DEFAULT, ACME, FINAL, CONF, LAPTOP_ONLY]}
      presentingProposal={null}
      laptopReachable={false}
      pricesAsOfLabel={null}
      errorMessage={null}
      onSelectProposal={vi.fn()}
      onExitPresent={vi.fn()}
      onSharePdf={vi.fn(async () => {})}
      signedIn={p.signedIn ?? true}
      phoneActions={p.actions ?? []}
      requests={p.requests ?? []}
      onQueueAction={queue as never}
      onCopyText={copy as never}
    />,
  );
  return { queue: queue as ReturnType<typeof vi.fn>, copy: copy as ReturnType<typeof vi.fn> };
}

const open = (name = 'Acme Co · Acme') => fireEvent.click(screen.getByRole('button', { name: `Manage ${name}` }));

afterEach(cleanup);

describe('the Pitch list without the laptop', () => {
  it('lists proposals, offers Manage only where the cloud has the proposal, and keeps confidential ones closed', () => {
    renderTab();
    expect(screen.getByRole('button', { name: 'Manage Acme Co · Acme' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Manage Final one' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Manage Only on laptop' })).toBeNull();
    expect(screen.getByText('Confidential proposal')).toBeTruthy();
    expect(screen.queryAllByRole('button', { name: /Manage .*onfidential/ })).toEqual([]);
    expect(screen.getByRole('button', { name: 'New proposal' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Quote requests/ })).toBeTruthy();
  });

  it('opening a proposal still presents it', () => {
    const onSelect = vi.fn();
    render(
      <PitchTab proposals={[ACME]} presentingProposal={null} laptopReachable={false} pricesAsOfLabel={null} errorMessage={null}
        onSelectProposal={onSelect} onExitPresent={vi.fn()} onSharePdf={vi.fn(async () => {})} />,
    );
    fireEvent.click(screen.getByText('Acme Co · Acme'));
    expect(onSelect).toHaveBeenCalledWith(ACME);
    expect(screen.queryByRole('button', { name: /New proposal/ })).toBeNull();
  });
});

describe('editing a proposal', () => {
  it('saves only what changed as one edit based on the version the phone saw', async () => {
    const { queue } = renderTab();
    open();
    const save = screen.getByRole('button', { name: 'Save changes' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Client name'), { target: { value: 'Acme Ltd' } });
    fireEvent.change(screen.getByLabelText('Contact email'), { target: { value: 'o@acme.example' } });
    fireEvent.change(screen.getByLabelText('Lead'), { target: { value: 'A new lead' } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(queue).toHaveBeenCalledTimes(1));
    const [input] = queue.mock.calls[0] as [ActionInput, string];
    expect(input).toMatchObject({ kind: 'pitch_update', target_id: 'prop_acme' });
    expect(input.payload).toEqual({
      base_version: VERSION,
      fields: { client_name: 'Acme Ltd', contact: { email: 'o@acme.example' }, preset: { lead: 'A new lead' } },
    });
  });

  it('chooses which services each sheet shows and which are in the plan', async () => {
    const { queue } = renderTab();
    open();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Shown on its sheet: Advanced UI' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'In the plan: Advanced UI' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show sheet: Backend' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show section: Portfolio' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    const fields = (queue.mock.calls[0][0] as ActionInput).payload.fields!;
    expect(fields.preset?.sections?.p2).toEqual(['w_front']);
    expect(fields.preset?.initialSelected).toEqual(['w_front', 'w_anim']);
    expect(fields.hidden_sheets).toEqual(['p4']);
    expect(fields.hidden_about).toEqual(['portfolio']);
  });

  it('sets the price of a service for this proposal, whole numbers only, blank to go back to the base price', async () => {
    const { queue } = renderTab();
    open();
    const price = screen.getByLabelText('Price for Responsive Web Application') as HTMLInputElement;
    expect(price.placeholder).toBe('6000');
    for (const bad of ['12.5', '-3', 'abc', '10000001', '1e3']) {
      fireEvent.change(price, { target: { value: bad } });
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      await screen.findByText(/whole number/i);
    }
    expect(queue).not.toHaveBeenCalled();
    fireEvent.change(price, { target: { value: '6500' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    expect((queue.mock.calls[0][0] as ActionInput).payload.fields).toEqual({ price_overrides: { w_front: 6500 } });
  });

  it('refuses angle brackets and shows why, without queueing anything', async () => {
    const { queue } = renderTab();
    open();
    fireEvent.change(screen.getByLabelText('Notes (private)'), { target: { value: 'a < b' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findByText(/cannot contain < or >/i);
    fireEvent.change(screen.getByLabelText('Notes (private)'), { target: { value: 'Internal' } });
    fireEvent.change(screen.getByLabelText('Client name'), { target: { value: '<b>x</b>' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findAllByText(/cannot contain < or >/i);
    expect(queue).not.toHaveBeenCalled();
  });

  it('waits its turn when an earlier edit is still waiting, and when signed out', () => {
    renderTab({ actions: [action({ kind: 'pitch_update', payload: { base_version: VERSION, fields: { notes: 'x' } } })] });
    open();
    expect(screen.getByText(/earlier edit .* waiting for the laptop/i)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Client name'), { target: { value: 'Other' } });
    expect((screen.getByRole('button', { name: 'Save changes' }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    renderTab({ signedIn: false });
    open();
    expect(screen.getByText(/sign in/i)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Client name'), { target: { value: 'Other' } });
    expect((screen.getByRole('button', { name: 'Save changes' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows what the laptop said about the last change, including that both copies were kept', () => {
    renderTab({
      actions: [
        action({ kind: 'pitch_update', state: 'applied', result: 'Kept both: your edit is a new copy marked "(phone)", because the proposal changed on the laptop first.', payload: { base_version: VERSION, fields: { notes: 'x' } } }),
      ],
    });
    open();
    expect(screen.getByText(/Last change: .*Kept both/)).toBeTruthy();
  });

  it('tells the person what happens if the laptop changed the proposal first', () => {
    renderTab();
    open();
    expect(screen.getByText(/kept as a copy marked "\(phone\)"/i)).toBeTruthy();
  });

  it('the default quotation and a cut-down copy cannot be edited here', () => {
    renderTab();
    open('Default quotation');
    expect(screen.getByText(/cannot be edited/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
    cleanup();
    renderTab({ proposals: [{ ...ACME, cloud: cloud({ truncated: true, editable: false }) }] });
    open();
    expect(screen.getByText(/too large to edit/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
  });
});

describe('final quotation and client links', () => {
  it('turns a proposal into a final quotation, but not a final, the default or a confidential one', async () => {
    const { queue } = renderTab();
    open();
    fireEvent.click(screen.getByRole('button', { name: 'Make final quotation' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    expect(queue.mock.calls[0][0]).toMatchObject({ kind: 'pitch_finalize', target_id: 'prop_acme', payload: {} });
    cleanup();
    renderTab();
    open('Final one');
    expect(screen.queryByRole('button', { name: 'Make final quotation' })).toBeNull();
    cleanup();
    renderTab();
    open('Default quotation');
    expect(screen.queryByRole('button', { name: 'Make final quotation' })).toBeNull();
  });

  it('publishes and withdraws a link by queueing it, and shows Waiting for the laptop', async () => {
    const { queue } = renderTab();
    open();
    expect(screen.getByText('No client link yet')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Copy link' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Withdraw link' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Publish link' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    expect(queue.mock.calls[0][0]).toMatchObject({ kind: 'pitch_link', target_id: 'prop_acme', payload: { action: 'publish' } });
    cleanup();
    renderTab({ actions: [action({})] });
    open();
    expect(screen.getByText('Waiting for the laptop')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Publish link' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('copies a live link and withdraws it', async () => {
    const live: PitchProposalItem = { ...ACME, cloud: cloud({ link_status: 'live', link_url: 'https://magnm-pitch.vercel.app/p/AAAAAAAAAAAAAAAAAAAAAA' }) };
    const { queue, copy } = renderTab({ proposals: [live] });
    open();
    expect(screen.getByText('Link is live')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Copy link' }));
    expect(copy).toHaveBeenCalledWith('https://magnm-pitch.vercel.app/p/AAAAAAAAAAAAAAAAAAAAAA');
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw link' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    expect(queue.mock.calls[0][0]).toMatchObject({ kind: 'pitch_link', payload: { action: 'withdraw' } });
  });
});

describe('a new proposal', () => {
  it('is made from details and optionally a built-in preset', async () => {
    const { queue } = renderTab();
    fireEvent.click(screen.getByRole('button', { name: 'New proposal' }));
    fireEvent.change(screen.getByLabelText('Client name'), { target: { value: 'Meera Shah' } });
    fireEvent.change(screen.getByLabelText('Business name'), { target: { value: 'Shah Clinics' } });
    fireEvent.change(screen.getByLabelText('Contact phone'), { target: { value: '+91 90000 00000' } });
    fireEvent.change(screen.getByLabelText('Start from'), { target: { value: 'urban-company' } });
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'quote_form' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create proposal' }));
    await waitFor(() => expect(queue).toHaveBeenCalled());
    const [input] = queue.mock.calls[0] as [ActionInput, string];
    expect(input).toMatchObject({ kind: 'pitch_create', target_id: 'new' });
    expect(input.payload).toEqual({
      kind: 'quote_form',
      from_preset: 'urban-company',
      client_name: 'Meera Shah',
      business_name: 'Shah Clinics',
      contact: { phone: '+91 90000 00000' },
    });
  });

  it('refuses angle brackets and does not queue', async () => {
    const { queue } = renderTab();
    fireEvent.click(screen.getByRole('button', { name: 'New proposal' }));
    fireEvent.change(screen.getByLabelText('Client name'), { target: { value: 'x<y' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create proposal' }));
    await screen.findByText(/cannot contain < or >/i);
    expect(queue).not.toHaveBeenCalled();
  });
});

describe('quote requests', () => {
  const req: QuoteRequestItem = { id: 'q1', share_id: 'AAAAAAAAAAAAAAAAAAAAAA', created_at: '2026-11-23T09:00:00Z', seen_at: null, name: 'Client One', business: 'Biz Ltd', phone: '+91 2', notes: 'Call soon', selected: ['w_front'], total_shown: 7800 };

  it('lists the requests with who asked, what they chose and the total they were shown', () => {
    renderTab({ requests: [req] });
    fireEvent.click(screen.getByRole('button', { name: /Quote requests \(1\)/ }));
    const card = screen.getByTestId('quote-request');
    expect(within(card).getByText(/Client One/)).toBeTruthy();
    expect(within(card).getByText(/Biz Ltd/)).toBeTruthy();
    expect(within(card).getByText(/Responsive Web Application/)).toBeTruthy();
    expect(within(card).getByText(/7,800/)).toBeTruthy();
    expect(within(card).getByText(/Call soon/)).toBeTruthy();
    expect(within(card).getByText(/Acme Co · Acme/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByRole('button', { name: 'New proposal' })).toBeTruthy();
  });

  it('shows plain text only, never markup', () => {
    renderTab({ requests: [{ ...req, name: '<img src=x onerror=alert(1)>', notes: '<script>x</script>' }] });
    fireEvent.click(screen.getByRole('button', { name: /Quote requests/ }));
    expect(document.querySelector('img[src="x"]')).toBeNull();
    expect(document.querySelector('script')).toBeNull();
    expect(screen.getByText(/<img src=x/)).toBeTruthy();
  });
});
