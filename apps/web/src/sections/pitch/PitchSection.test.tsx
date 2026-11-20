import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { PitchCatalog, PitchProposal } from '../../api/pitch';
import { PitchPresent } from './PitchPresent';
import { PitchSection } from './PitchSection';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function makeCatalog(): PitchCatalog {
  return {
    groups: [
      'Web & SaaS',
      'Mobile app',
      'Backend',
      'AI & Automation',
      'DevOps & Maintenance',
    ],
    standalone_fee: 4000,
    standalone_label: 'App-only setup (no web application to share foundation)',
    items: {
      w_front: {
        g: 'Web & SaaS',
        n: 'Responsive Web Application / Frontend',
        d: 'Modern responsive web application.',
        r: [],
        p: 6000,
        base_price: 6000,
        price: 6000,
        overridden: false,
      },
      a_base: {
        g: 'Mobile app',
        n: 'Cross-Platform Mobile App (Android)',
        d: 'Mobile app built for Android.',
        r: ['b_db'],
        p: 6500,
        base_price: 6500,
        price: 6500,
        overridden: false,
      },
      b_db: {
        g: 'Backend',
        n: 'Core Database & API Server',
        d: 'Relational database and REST API.',
        r: [],
        p: 2800,
        base_price: 2000,
        price: 2800,
        overridden: true,
      },
      ops_cloud: {
        g: 'DevOps & Maintenance',
        n: 'Production Cloud Infrastructure & CDN',
        d: 'Cloud hosting and CDN.',
        r: [],
        p: 1500,
        base_price: 1500,
        price: 1500,
        overridden: false,
      },
    },
    presets: {
      general: {
        slug: 'general',
        name: 'General Software Cost Planner',
        badge: '',
        clientName: '',
        businessName: '',
        title: 'magnm Software Cost Planner',
        lead: 'General lead',
        whatWeDo: 'Web, Mobile, Backend',
        portfolio: 'magnm-org.vercel.app',
        thisPlan: 'Custom plan',
        architectureBlock: { heading: 'Architecture', sub: 'Sub', roles: [] },
        initialSelected: ['w_front', 'b_db'],
        sections: {
          p2: ['w_front'],
          p3: ['a_base'],
          p4: ['b_db'],
          p5: ['ops_cloud'],
        },
      },
      'urban-company': {
        slug: 'urban-company',
        name: 'Urban Company-style Booking Platform',
        badge: 'Tailored Proposal',
        clientName: 'Service Client',
        businessName: 'Urban Services',
        title: 'magnm Build Planner',
        lead: 'Urban lead',
        whatWeDo: 'Web & Mobile',
        portfolio: 'magnm-org.vercel.app',
        thisPlan: 'Booking platform',
        architectureBlock: {
          heading: 'Platform roles',
          sub: 'Three roles',
          roles: [{ label: 'Customer', points: ['Browse services'] }],
        },
        initialSelected: ['w_front', 'a_base', 'b_db'],
        sections: {
          p2: ['w_front'],
          p3: ['a_base'],
          p4: ['b_db'],
          p5: ['ops_cloud'],
        },
      },
    },
  };
}

function makeProposal(overrides: Partial<PitchProposal> = {}): PitchProposal {
  const base: PitchProposal = {
    id: 'prop_1',
    slug: 'telemed-india',
    kind: 'proposal',
    status: 'draft',
    client_name: 'Dr. Mehta',
    business_name: 'TeleMed India',
    contact: { name: 'Dr. Mehta', email: 'mehta@telemed.in', phone: '+91 9876543210', notes: '' },
    preset: {
      slug: 'telemed-india',
      name: 'TeleMed India',
      badge: 'Proposal for TeleMed India',
      clientName: 'Dr. Mehta',
      businessName: 'TeleMed India',
      title: 'TeleMed Platform',
      lead: 'Patient consultations and bookings.',
      whatWeDo: 'Web, Mobile, Cloud',
      portfolio: 'magnm-org.vercel.app',
      thisPlan: 'Telemedicine platform',
      architectureBlock: {
        heading: 'System architecture',
        sub: 'Two roles',
        roles: [{ label: 'Patient', points: ['Book video slots'] }],
      },
      initialSelected: ['w_front', 'b_db'],
      sections: {
        p2: ['w_front'],
        p3: ['a_base'],
        p4: ['b_db'],
        p5: ['ops_cloud'],
      },
    },
    price_overrides: { w_front: 7200 },
    custom_items: [],
    notes: 'Initial scope',
    created_by: 'writer',
    created_at: '2026-11-19T10:00:00Z',
    updated_at: '2026-11-19T12:00:00Z',
    pricing_summary: {
      lines: [
        {
          item: 'w_front',
          item_id: 'w_front',
          name: 'Responsive Web Application / Frontend',
          group: 'Web & SaaS',
          base: 6000,
          effective: 7200,
          delta: 1200,
          source: 'proposal',
        },
        {
          item: 'b_db',
          item_id: 'b_db',
          name: 'Core Database & API Server',
          group: 'Backend',
          base: 2000,
          effective: 2800,
          delta: 800,
          source: 'admin',
        },
      ],
      custom_items: [],
      subtotal: 10000,
      standalone_fee: 0,
      standalone_label: '',
      total: 10000,
      differs_from_base: [],
    },
    warnings: [],
    pitch_version: { ref: 'c44b7e2', commit: 'c44b7e2347bdd7c60b620000640107ed3c02a568' },
  };
  return { ...base, ...overrides };
}

describe('PitchSection', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders proposal list, filters by status/kind, creates from preset, and sends Writer brief without price fields', async () => {
    const proposals = [
      makeProposal(),
      makeProposal({
        id: 'prop_2',
        slug: 'intake-form',
        kind: 'quote_form',
        status: 'ready',
        client_name: 'Riya',
        business_name: 'Studio Intake',
        created_by: 'user',
        pricing_summary: {
          lines: [],
          custom_items: [],
          subtotal: 0,
          standalone_fee: 0,
          standalone_label: '',
          total: 0,
          differs_from_base: [],
        },
      }),
    ];
    const catalog = makeCatalog();

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/version') {
        return jsonResponse({ ref: 'c44b7e2', commit: 'c44b7e2347bdd7c60b620000640107ed3c02a568' });
      }
      if (url === '/api/pitch/catalog') {
        return jsonResponse(catalog);
      }
      if (url.startsWith('/api/pitch/proposals') && (!init?.method || init.method === 'GET')) {
        const parsed = new URL(url, 'http://localhost');
        const st = parsed.searchParams.get('status') || '';
        const kd = parsed.searchParams.get('kind') || '';
        const filtered = proposals.filter(
          (p) => (!st || p.status === st) && (!kd || p.kind === kd),
        );
        return jsonResponse({ proposals: filtered });
      }
      if (url === '/api/pitch/proposals' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as Record<string, unknown>;
        return jsonResponse(
          makeProposal({
            id: 'prop_new',
            slug: String(body.slug || 'new-slug'),
            created_by: 'user',
          }),
        );
      }
      if (url === '/api/agent_chat' && init?.method === 'POST') {
        return jsonResponse({ success: true, response: 'Draft created.' });
      }
      return jsonResponse({});
    });

    const onNavigate = vi.fn();
    render(<PitchSection param={null} onNavigate={onNavigate} />);

    await waitFor(() => {
      expect(screen.getByText('TeleMed India · Dr. Mehta')).toBeTruthy();
    });

    expect(screen.getByText('Pitch version c44b7e2')).toBeTruthy();
    expect(screen.getByText('Drafted by Writer')).toBeTruthy();
    expect(screen.getByText('₹10,000')).toBeTruthy();
    expect(screen.getByText('Studio Intake · Riya')).toBeTruthy();

    // Filter by kind = quote_form
    fireEvent.change(screen.getByLabelText('Filter by kind'), { target: { value: 'quote_form' } });
    await waitFor(() => {
      expect(screen.queryByText('TeleMed India · Dr. Mehta')).toBeNull();
      expect(screen.getByText('Studio Intake · Riya')).toBeTruthy();
    });

    // Reset filter and filter by status = draft
    fireEvent.change(screen.getByLabelText('Filter by kind'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Filter by status'), { target: { value: 'draft' } });
    await waitFor(() => {
      expect(screen.getByText('TeleMed India · Dr. Mehta')).toBeTruthy();
      expect(screen.queryByText('Studio Intake · Riya')).toBeNull();
    });

    // New proposal from preset
    fireEvent.click(screen.getByRole('button', { name: 'New proposal' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Start from'), { target: { value: 'preset' } });
    fireEvent.change(within(dialog).getByLabelText('Pitch preset'), { target: { value: 'urban-company' } });
    fireEvent.change(within(dialog).getByLabelText(/^Slug/), { target: { value: 'urban-acme' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create proposal' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/pitch/proposals',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"from_preset":"urban-company"'),
        }),
      );
      expect(onNavigate).toHaveBeenCalledWith('prop_new');
    });

    // Draft with Writer dialog has no price fields and posts to /api/agent_chat
    fireEvent.click(screen.getByRole('button', { name: 'Draft with Writer' }));
    const writerDialog = screen.getByRole('dialog');
    expect(writerDialog.querySelectorAll('input')).toHaveLength(0);
    expect(within(writerDialog).queryByRole('spinbutton')).toBeNull();
    expect(within(writerDialog).queryByLabelText(/price/i)).toBeNull();
    fireEvent.change(within(writerDialog).getByLabelText(/^Client brief/), {
      target: { value: 'Build a clinic portal for Apex Health' },
    });
    fireEvent.click(within(writerDialog).getByRole('button', { name: 'Ask Writer to draft' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/agent_chat',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"agent_id":"writer"'),
        }),
      );
    });
  });

  it('saves editor body, renders inline 400 rule violations, dependency notes, custom items, price sources/overrides, reset, engine totals, PDF export, and disabled Phase 4 actions', async () => {
    const catalog = makeCatalog();
    let currentProp = makeProposal({
      price_overrides: { w_front: 7200 },
      pricing_summary: {
        lines: [],
        custom_items: [],
        subtotal: 10000,
        standalone_fee: 0,
        standalone_label: '',
        total: 10000,
        differs_from_base: [],
      },
    });
    let pdfList: Array<{ filename: string; path: string; url: string; size: number; created_at: string }> = [];
    let failNextSave = false;

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1' && (!init?.method || init.method === 'GET')) {
        return jsonResponse(currentProp);
      }
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: currentProp.slug, pdfs: pdfList });
      }
      if (url === '/api/pitch/proposals/prop_1' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as Record<string, unknown>;
        if (failNextSave) {
          failNextSave = false;
          return jsonResponse(
            {
              error: "slug 'general' is reserved",
              errors: [
                {
                  field: 'slug',
                  rule: 'reserved_slug',
                  message: "slug 'general' is reserved; built-in routes cannot be overwritten",
                },
              ],
            },
            400,
          );
        }
        currentProp = makeProposal({
          ...currentProp,
          client_name: String(body.client_name ?? currentProp.client_name),
          price_overrides: (body.price_overrides as Record<string, number>) ?? {},
          custom_items: (body.custom_items as PitchProposal['custom_items']) ?? [],
          preset: (body.preset as PitchProposal['preset']) ?? currentProp.preset,
          pricing_summary: {
            lines: [],
            custom_items: [],
            subtotal: 14500,
            standalone_fee: 4000,
            standalone_label: 'App-only setup (no web application to share foundation)',
            total: 18500,
            differs_from_base: [],
          },
          warnings: [
            {
              product: 'lazylayout',
              message: 'LazyLayout is in development: teasers only.',
            },
          ],
        });
        return jsonResponse(currentProp);
      }
      return jsonResponse({});
    });

    const onBackToList = vi.fn();
    const onPresent = vi.fn();
    render(<PitchSection param="prop_1" onNavigate={(p) => (p ? onPresent(p) : onBackToList())} />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('telemed-india')).toBeTruthy();
    });

    // BottomBar tooltips & disabled state for Phase 4 actions
    const copyLinkBtn = screen.getByRole('button', { name: 'Copy link' }) as HTMLButtonElement;
    const phoneBtn = screen.getByRole('button', { name: 'Send to my phone' }) as HTMLButtonElement;
    expect(copyLinkBtn.disabled).toBe(true);
    expect(copyLinkBtn.title).toBe('Client links need the team connection (Phase 4)');
    expect(phoneBtn.disabled).toBe(true);
    expect(phoneBtn.title).toBe('Available after phone pairing (Phase 4)');

    // 400 rule violation renders inline next to slug field
    failNextSave = true;
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(
        screen.getByText("slug 'general' is reserved; built-in routes cannot be overwritten"),
      ).toBeTruthy();
    });

    // Deliverables tab: dependency auto-add note & custom item add
    fireEvent.click(screen.getByRole('tab', { name: /Deliverables/i }));
    const mobileRow = screen
      .getByText('Cross-Platform Mobile App (Android)')
      .closest('.mg-row') as HTMLElement;
    const mobilePreSelect = within(mobileRow).getByLabelText('Pre-selected') as HTMLInputElement;
    fireEvent.click(mobilePreSelect);

    await waitFor(() => {
      const dbRow = screen.getByText('Core Database & API Server').closest('.mg-row') as HTMLElement;
      expect(dbRow.textContent).toContain('needed for');
      expect(dbRow.textContent).toContain('Cross-Platform Mobile App (Android)');
    });

    // Add a custom item
    fireEvent.change(screen.getByLabelText('Custom item name'), {
      target: { value: 'ABHA Health ID Sync' },
    });
    fireEvent.change(screen.getByLabelText('Description'), {
      target: { value: 'Integrate ABHA patient verification' },
    });
    fireEvent.change(screen.getByLabelText('Price (INR)'), {
      target: { value: '2500' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add custom item' }));

    await waitFor(() => {
      expect(screen.getAllByText('ABHA Health ID Sync').length).toBeGreaterThanOrEqual(1);
    });

    // Prices tab: keeps live preview visible next to table (no Show/Hide preview toggle), compact Base column, Reset button text & nowrap class
    expect(screen.getByTestId('pitch-side-preview').hidden).toBe(false);
    expect(screen.queryByRole('button', { name: 'Show preview' })).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: /Prices/i }));
    expect(screen.getByTestId('pitch-side-preview').hidden).toBe(false);
    expect(screen.queryByRole('button', { name: 'Show preview' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Hide preview' })).toBeNull();

    const priceTable = screen.getByRole('table');
    expect(within(priceTable).getByText('Admin')).toBeTruthy();
    expect(within(priceTable).getByText('Admin ₹2,800')).toBeTruthy();
    expect(within(priceTable).getAllByText('This proposal').length).toBeGreaterThanOrEqual(1);

    const wFrontInput = screen.getByLabelText(
      'Proposal price for Responsive Web Application / Frontend',
    ) as HTMLInputElement;
    expect(wFrontInput.value).toBe('7200');

    // Reset w_front to base -> button text is "Reset" with nowrap class, clears override, source becomes Pitch
    const wFrontResetBtn = screen.getByRole('button', {
      name: 'Reset to base for Responsive Web Application / Frontend',
    });
    expect(wFrontResetBtn.textContent).toBe('Reset');
    expect(wFrontResetBtn.className).toContain('studio-pitch-reset-btn');
    fireEvent.click(wFrontResetBtn);
    expect(wFrontInput.value).toBe('');
    const wFrontRow = wFrontInput.closest('tr') as HTMLElement;
    expect(within(wFrontRow).getByText('Pitch')).toBeTruthy();

    // Set proposal override for ops_cloud only
    const opsInput = screen.getByLabelText(
      'Proposal price for Production Cloud Infrastructure & CDN',
    ) as HTMLInputElement;
    fireEvent.change(opsInput, { target: { value: '1900' } });

    // Save and verify request body sends ONLY ops_cloud override and totals come from engine response
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      const saveCall = fetchMock.mock.calls.find(
        ([u, initArg]) =>
          u === '/api/pitch/proposals/prop_1' &&
          (initArg as RequestInit | undefined)?.method === 'POST',
      );
      expect(saveCall).toBeDefined();
    });

    const postCalls = fetchMock.mock.calls.filter(
      ([u, initArg]) =>
        u === '/api/pitch/proposals/prop_1' &&
        (initArg as RequestInit | undefined)?.method === 'POST',
    );
    const lastBody = JSON.parse(String((postCalls[postCalls.length - 1][1] as RequestInit).body));
    expect(lastBody.price_overrides).toEqual({ ops_cloud: 1900 });
    expect(lastBody.custom_items).toHaveLength(1);
    expect(lastBody.custom_items[0].n).toBe('ABHA Health ID Sync');

    // Totals come from engine pricing_summary (18,500 with standalone fee 4,000) and warning shows
    await waitFor(() => {
      expect(screen.getAllByText('₹18,500').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('₹4,000')).toBeTruthy();
      expect(screen.getByText(/LazyLayout is in development/)).toBeTruthy();
    });

    // Download PDF calls iframe contentWindow.downloadPdf() and lists saved PDFs
    const iframe = screen.getByTitle('Pitch preview for telemed-india') as HTMLIFrameElement;
    const downloadPdfMock = vi.fn(() => {
      pdfList = [
        {
          filename: 'telemed-india-20261119.pdf',
          path: 'outputs/pitch/telemed-india/telemed-india-20261119.pdf',
          url: '/api/pitch/proposals/prop_1/pdf/telemed-india-20261119.pdf',
          size: 2048,
          created_at: '2026-11-19T12:10:00Z',
        },
      ];
    });
    Object.defineProperty(iframe, 'contentWindow', {
      configurable: true,
      value: {
        downloadPdf: downloadPdfMock,
        postMessage: vi.fn(),
        focus: vi.fn(),
      },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Download PDF' }));

    expect(downloadPdfMock).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(
        screen.getByText('Saved to outputs/pitch/telemed-india/telemed-india-20261119.pdf'),
      ).toBeTruthy();
      expect(screen.getByText('telemed-india-20261119.pdf')).toBeTruthy();
    });
  });

  it('exits present mode on Exit button click or Escape', () => {
    const onExit = vi.fn();
    render(<PitchPresent proposalId="prop_1" onExit={onExit} />);

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onExit).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Exit' }));
    expect(onExit).toHaveBeenCalledTimes(2);
  });

  it('shows 4 Preview tab below 1200px and guards unsaved changes when leaving', async () => {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    const catalog = makeCatalog();
    const prop = makeProposal();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1') return jsonResponse(prop);
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: prop.slug, pdfs: [] });
      }
      return jsonResponse({});
    });

    const onNavigate = vi.fn();
    try {
      render(<PitchSection param="prop_1" onNavigate={onNavigate} />);

      await waitFor(() => {
        expect(screen.getByTestId('pitch-tabbed-layout')).toBeTruthy();
      });

      const tabs = screen.getAllByRole('tab');
      expect(tabs).toHaveLength(4);
      expect(tabs[3].textContent).toContain('Preview');

      fireEvent.click(tabs[3]);
      expect(tabs[3].getAttribute('aria-selected')).toBe('true');

      // Edit a field -> leaving triggers Unsaved changes dialog
      fireEvent.click(tabs[0]);
      fireEvent.change(screen.getByLabelText(/^Client name/), { target: { value: 'Changed Client' } });
      fireEvent.click(screen.getByRole('button', { name: 'Back to Pitch' }));

      const guardDialog = screen.getByRole('dialog');
      expect(within(guardDialog).getByText('Unsaved changes')).toBeTruthy();
      expect(onNavigate).not.toHaveBeenCalled();

      fireEvent.click(within(guardDialog).getByRole('button', { name: 'Discard changes' }));
      expect(onNavigate).toHaveBeenCalledWith(null);
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it('shows inline validation message as user types < or > in Details and Deliverables fields', async () => {
    const catalog = makeCatalog();
    const prop = makeProposal();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1') return jsonResponse(prop);
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: prop.slug, pdfs: [] });
      }
      return jsonResponse({});
    });

    render(<PitchSection param="prop_1" onNavigate={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('telemed-india')).toBeTruthy();
    });

    // Type <img...> into Badge and Role 1 point 1 -> inline validation appears immediately
    const badgeInput = screen.getByLabelText(/^Badge/) as HTMLInputElement;
    fireEvent.change(badgeInput, { target: { value: 'Hello <script>' } });
    expect(badgeInput.getAttribute('aria-invalid')).toBe('true');

    const pointInput = screen.getByLabelText('Role 1 point 1') as HTMLInputElement;
    fireEvent.change(pointInput, { target: { value: '<img src=x onerror=alert(1)>' } });
    expect(pointInput.getAttribute('aria-invalid')).toBe('true');

    expect(screen.getAllByText('Text cannot contain < or >').length).toBeGreaterThanOrEqual(2);

    // Switch to Deliverables tab and type < or > into Custom item name and Description
    fireEvent.click(screen.getByRole('tab', { name: /Deliverables/i }));
    const customNameInput = screen.getByLabelText(/^Custom item name/) as HTMLInputElement;
    const customDescInput = screen.getByLabelText(/^Description/) as HTMLTextAreaElement;
    fireEvent.change(customNameInput, { target: { value: 'Item <b>' } });
    fireEvent.change(customDescInput, { target: { value: 'Desc >' } });
    expect(customNameInput.getAttribute('aria-invalid')).toBe('true');
    expect(customDescInput.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getAllByText('Text cannot contain < or >')).toHaveLength(2);
  });

  it('renders pinned default row with allowed actions only, opens default read-only, filters final, creates quote form & final (blank and finalize)', async () => {
    const catalog = makeCatalog();
    catalog.default_plans = {
      payment_plan: [
        { percent: 40, label: 'to start' },
        { percent: 30, label: 'at demo' },
        { percent: 30, label: 'at launch' },
      ],
      payment_note: 'Third-party infrastructure billed directly.',
      timeline: [
        { title: 'Scope & Cost Estimation', when: 'Day 1', detail: 'Configure scope.' },
        { title: 'Architecture & UI/UX Screens', when: 'Week 1', detail: 'Wireframes.' },
        { title: 'Sprint Development & QA Testing', when: 'Weeks 2 to 6', detail: 'Bi-weekly demos.' },
        { title: 'Cloud Deployment & Handover', when: 'Final Week', detail: 'Launch and handover.' },
      ],
    };
    const defaultProp = makeProposal({
      id: 'prop_default',
      slug: 'default-quotation',
      kind: 'default',
      status: 'ready',
      client_name: '',
      business_name: '',
      created_by: 'system',
      pricing_summary: {
        lines: [],
        custom_items: [],
        subtotal: 8800,
        standalone_fee: 0,
        standalone_label: '',
        total: 8800,
        differs_from_base: [],
      },
    });
    const finalProp = makeProposal({
      id: 'prop_final_1',
      slug: 'acme-final',
      kind: 'final',
      status: 'ready',
      client_name: 'Asha',
      business_name: 'Acme Health',
      created_by: 'user',
      pricing_summary: {
        lines: [],
        custom_items: [],
        subtotal: 12000,
        standalone_fee: 0,
        standalone_label: '',
        total: 12000,
        differs_from_base: [],
      },
    });
    const proposals = [makeProposal(), defaultProp, finalProp];

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/version') {
        return jsonResponse({ ref: 'c44b7e2', commit: 'c44b7e2347bdd7c60b620000640107ed3c02a568' });
      }
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/default') return jsonResponse(defaultProp);
      if (url === '/api/pitch/proposals/prop_default') return jsonResponse(defaultProp);
      if (url === '/api/pitch/proposals/prop_default/pdfs') {
        return jsonResponse({ proposal_id: 'prop_default', slug: 'default-quotation', pdfs: [] });
      }
      if (url === '/api/pitch/proposals/prop_default/duplicate' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as Record<string, unknown>;
        return jsonResponse(
          makeProposal({
            id: 'prop_dup_qf',
            slug: String(body.slug || 'dup-qf'),
            kind: (body.kind as PitchProposal['kind']) || 'proposal',
            created_by: 'user',
          }),
        );
      }
      if (url.startsWith('/api/pitch/proposals') && (!init?.method || init.method === 'GET')) {
        const parsed = new URL(url, 'http://localhost');
        const st = parsed.searchParams.get('status') || '';
        const kd = parsed.searchParams.get('kind') || '';
        const filtered = proposals.filter(
          (p) => (!st || p.status === st) && (!kd || p.kind === kd),
        );
        return jsonResponse({ proposals: filtered });
      }
      if (url === '/api/pitch/proposals' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as Record<string, unknown>;
        return jsonResponse(
          makeProposal({
            id: 'prop_created',
            slug: String(body.slug || 'created'),
            kind: (body.kind as PitchProposal['kind']) || 'proposal',
            created_by: 'user',
          }),
        );
      }
      if (url === '/api/pitch/proposals/prop_1/finalize' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as Record<string, unknown>;
        return jsonResponse(
          makeProposal({
            id: 'prop_finalized',
            slug: String(body.slug || 'telemed-india-final'),
            kind: 'final',
            created_by: 'user',
          }),
        );
      }
      return jsonResponse({});
    });

    const onNavigate = vi.fn();
    const { rerender } = render(<PitchSection param={null} onNavigate={onNavigate} />);

    await waitFor(() => {
      expect(screen.getByTestId('pitch-default-row')).toBeTruthy();
    });

    // Pinned default row is first and has ONLY allowed actions
    const rows = document.querySelectorAll('.mg-row');
    expect(rows[0].getAttribute('data-testid')).toBe('pitch-default-row');
    const defRow = screen.getByTestId('pitch-default-row');
    expect(within(defRow).getByText('Default')).toBeTruthy();
    expect(within(defRow).getByText('Pitch version c44b7e2')).toBeTruthy();
    expect(within(defRow).getByText('₹8,800')).toBeTruthy();
    expect(within(defRow).getByRole('button', { name: 'Open' })).toBeTruthy();
    expect(within(defRow).getByRole('button', { name: 'Present' })).toBeTruthy();
    expect(within(defRow).getByRole('button', { name: 'Download PDF' })).toBeTruthy();
    const defCopyBtn = within(defRow).getByRole('button', { name: 'Copy link' }) as HTMLButtonElement;
    expect(defCopyBtn.disabled).toBe(true);
    expect(defCopyBtn.title).toBe('Client links need the team connection (Phase 4)');
    expect(within(defRow).getByRole('button', { name: 'Duplicate' })).toBeTruthy();
    expect(within(defRow).queryByRole('button', { name: 'Archive' })).toBeNull();
    expect(within(defRow).queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(within(defRow).queryByRole('button', { name: 'Make final quotation' })).toBeNull();

    // Present button navigates to present mode
    fireEvent.click(within(defRow).getByRole('button', { name: 'Present' }));
    expect(onNavigate).toHaveBeenCalledWith('prop_default/present');

    // Duplicate default as quote_form
    fireEvent.click(within(defRow).getByRole('button', { name: 'Duplicate' }));
    const dupDialog = screen.getByRole('dialog');
    fireEvent.change(within(dupDialog).getByLabelText('Duplicate as'), {
      target: { value: 'quote_form' },
    });
    fireEvent.change(within(dupDialog).getByLabelText('New slug'), {
      target: { value: 'default-qf-copy' },
    });
    fireEvent.click(within(dupDialog).getByRole('button', { name: 'Duplicate' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/pitch/proposals/prop_default/duplicate',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ slug: 'default-qf-copy', kind: 'quote_form' }),
        }),
      );
    });

    // Regular proposal row gets "Make final quotation"; final row does not
    const telemedRow = screen.getByText('TeleMed India · Dr. Mehta').closest('.mg-row') as HTMLElement;
    expect(within(telemedRow).getByRole('button', { name: 'Make final quotation' })).toBeTruthy();
    const finalRow = screen.getByText('Acme Health · Asha').closest('.mg-row') as HTMLElement;
    expect(within(finalRow).getByText('Final quotation')).toBeTruthy();
    expect(within(finalRow).queryByRole('button', { name: 'Make final quotation' })).toBeNull();

    // Kind filter includes Final quotation
    fireEvent.change(screen.getByLabelText('Filter by kind'), { target: { value: 'final' } });
    await waitFor(() => {
      expect(screen.queryByTestId('pitch-default-row')).toBeNull();
      expect(screen.queryByText('TeleMed India · Dr. Mehta')).toBeNull();
      expect(screen.getByText('Acme Health · Asha')).toBeTruthy();
    });

    // New quote form
    fireEvent.click(screen.getByRole('button', { name: 'New quote form' }));
    const qfDialog = screen.getByRole('dialog');
    fireEvent.change(within(qfDialog).getByLabelText(/^Slug/), {
      target: { value: 'client-intake' },
    });
    fireEvent.click(within(qfDialog).getByRole('button', { name: 'Create quote form' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/pitch/proposals',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"kind":"quote_form"'),
        }),
      );
    });

    // New final quotation from blank (requires selecting at least one agreed service)
    fireEvent.click(screen.getByRole('button', { name: 'New final quotation' }));
    const finalBlankDialog = screen.getByRole('dialog');
    fireEvent.change(within(finalBlankDialog).getByLabelText(/^Slug/), {
      target: { value: 'blank-final-1' },
    });
    fireEvent.click(
      within(finalBlankDialog).getByLabelText('Responsive Web Application / Frontend'),
    );
    fireEvent.click(
      within(finalBlankDialog).getByRole('button', { name: 'Create final quotation' }),
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/pitch/proposals',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"initialSelected":["w_front"]'),
        }),
      );
    });

    // New final quotation from an existing proposal via finalize
    fireEvent.change(screen.getByLabelText('Filter by kind'), { target: { value: '' } });
    await waitFor(() => {
      expect(screen.getByText('TeleMed India · Dr. Mehta')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'New final quotation' }));
    const finalSrcDialog = screen.getByRole('dialog');
    fireEvent.change(within(finalSrcDialog).getByLabelText('Start from'), {
      target: { value: 'finalize' },
    });
    fireEvent.change(within(finalSrcDialog).getByLabelText(/^Slug/), {
      target: { value: 'telemed-agreed' },
    });
    fireEvent.click(
      within(finalSrcDialog).getByRole('button', { name: 'Create final quotation' }),
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/pitch/proposals/prop_1/finalize',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"slug":"telemed-agreed"'),
        }),
      );
    });

    // Default opens read-only (no Save button, no form tabs)
    rerender(<PitchSection param="prop_default" onNavigate={onNavigate} />);
    await waitFor(() => {
      expect(screen.getByTestId('pitch-default-readonly')).toBeTruthy();
    });
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(screen.queryByRole('tab')).toBeNull();
    const roCopyBtn = screen.getByRole('button', { name: 'Copy link' }) as HTMLButtonElement;
    expect(roCopyBtn.disabled).toBe(true);
    expect(roCopyBtn.title).toBe('Client links need the team connection (Phase 4)');
  });

  it('edits final quotation: Services without sheet toggle, Prices with recurring, Plans validation/limits/reset/reorder/periods, bottom bar recurring line', async () => {
    const catalog = makeCatalog();
    catalog.default_plans = {
      payment_plan: [
        { percent: 40, label: 'to start' },
        { percent: 30, label: 'at demo' },
        { percent: 30, label: 'at launch' },
      ],
      payment_note: 'Default payment note.',
      timeline: [
        { title: 'Scope & Cost Estimation', when: 'Day 1', detail: 'Estimate scope.' },
        { title: 'Architecture & UI/UX Screens', when: 'Week 1', detail: 'Wireframes.' },
        { title: 'Sprint Development & QA Testing', when: 'Weeks 2 to 6', detail: 'Sprints.' },
        { title: 'Cloud Deployment & Handover', when: 'Final Week', detail: 'Handover.' },
      ],
    };

    let finalProp = makeProposal({
      id: 'prop_final',
      slug: 'acme-final',
      kind: 'final',
      status: 'ready',
      client_name: 'Asha',
      business_name: 'Acme Health',
      price_overrides: {},
      payment_plan: [
        { percent: 40, label: 'to start' },
        { percent: 30, label: 'at demo' },
        { percent: 30, label: 'at launch' },
      ],
      payment_note: 'Default payment note.',
      timeline: [
        { title: 'Step One', when: 'Day 1', detail: 'First phase' },
        { title: 'Step Two', when: 'Week 2', detail: 'Second phase' },
      ],
      service_plans: [
        {
          id: 'plan_care',
          name: 'Monthly Care',
          description: 'Hosting and updates',
          price: 2500,
          period: 'monthly',
        },
        {
          id: 'plan_setup',
          name: 'Onboarding',
          description: 'Initial setup',
          price: 1500,
          period: 'one_time',
        },
      ],
      pricing_summary: {
        lines: [],
        custom_items: [],
        one_time_plans: [
          {
            id: 'plan_setup',
            name: 'Onboarding',
            description: 'Initial setup',
            price: 1500,
            period: 'one_time',
          },
        ],
        recurring: [
          { id: 'plan_care', name: 'Monthly Care', price: 2500, period: 'monthly' },
        ],
        recurring_totals: { monthly: 2500, quarterly: 0, yearly: 0 },
        payment_plan: [
          { percent: 40, label: 'to start', amount: 4120 },
          { percent: 30, label: 'at demo', amount: 3090 },
          { percent: 30, label: 'at launch', amount: 3090 },
        ],
        subtotal: 10300,
        standalone_fee: 0,
        standalone_label: '',
        total: 10300,
        differs_from_base: [],
      },
    });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_final' && (!init?.method || init.method === 'GET')) {
        return jsonResponse(finalProp);
      }
      if (url === '/api/pitch/proposals/prop_final/pdfs') {
        return jsonResponse({ proposal_id: 'prop_final', slug: 'acme-final', pdfs: [] });
      }
      if (url === '/api/pitch/proposals/prop_final' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as Partial<PitchProposal>;
        finalProp = {
          ...finalProp,
          ...body,
          pricing_summary: {
            ...finalProp.pricing_summary,
            payment_plan: [
              { percent: 50, label: 'upfront', amount: 5150 },
              { percent: 50, label: 'delivery', amount: 5150 },
            ],
          },
        };
        return jsonResponse(finalProp);
      }
      return jsonResponse({});
    });

    render(<PitchSection param="prop_final" onNavigate={vi.fn()} />);

    await waitFor(() => {
      expect(
        screen.getByText('Final quotation: the client sees only these services, ticked and locked.'),
      ).toBeTruthy();
    });

    // Bottom bar shows Total (one-time) + recurring mono line + disabled Phase 4 buttons
    expect(screen.getByText('Total (one-time)')).toBeTruthy();
    expect(screen.getByText('+ ₹2,500 / month')).toBeTruthy();
    const copyLinkBtn = screen.getByRole('button', { name: 'Copy link' }) as HTMLButtonElement;
    const phoneBtn = screen.getByRole('button', { name: 'Send to my phone' }) as HTMLButtonElement;
    expect(copyLinkBtn.disabled).toBe(true);
    expect(copyLinkBtn.title).toBe('Client links need the team connection (Phase 4)');
    expect(phoneBtn.disabled).toBe(true);
    expect(phoneBtn.title).toBe('Available after phone pairing (Phase 4)');

    // Tabs: Details, Services, Prices, Plans
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual([
      expect.stringContaining('Details'),
      expect.stringContaining('Services'),
      expect.stringContaining('Prices'),
      expect.stringContaining('Plans'),
    ]);

    // Services tab: search works and NO "Show on this sheet" checkbox exists
    fireEvent.click(screen.getByRole('tab', { name: /Services/i }));
    expect(screen.queryByLabelText('Show on this sheet')).toBeNull();
    expect(screen.getAllByLabelText('Agreed').length).toBeGreaterThanOrEqual(1);
    fireEvent.change(screen.getByLabelText(/^Search services/), {
      target: { value: 'Android' },
    });
    expect(screen.getByText('Cross-Platform Mobile App (Android)')).toBeTruthy();
    expect(screen.queryByText('Responsive Web Application / Frontend')).toBeNull();

    // Prices tab: shows one-time plans and recurring totals from engine pricing_summary
    fireEvent.click(screen.getByRole('tab', { name: /Prices/i }));
    const summaryEl = screen.getByLabelText('Engine pricing totals');
    expect(within(summaryEl).getByText('Onboarding')).toBeTruthy();
    expect(within(summaryEl).getByText('Monthly Care')).toBeTruthy();
    expect(within(summaryEl).getByText('Recurring total (monthly)')).toBeTruthy();

    // Plans tab
    fireEvent.click(screen.getByRole('tab', { name: /Plans/i }));
    const paymentSection = screen.getByRole('region', { name: 'Payment plan' });
    expect(within(paymentSection).getByText('Installment 1 · ₹4,120')).toBeTruthy();

    // Payment plan sum validation + add/remove row limits (2..4) + Reset
    fireEvent.change(within(paymentSection).getByLabelText('Percent (%)', { selector: '#pay-percent-0' }), {
      target: { value: '20' },
    });
    expect(
      within(paymentSection).getByText('Payment percentages sum to 80%. They must equal 100%.'),
    ).toBeTruthy();

    const addPayRowBtn = within(paymentSection).getByRole('button', { name: 'Add row' }) as HTMLButtonElement;
    fireEvent.click(addPayRowBtn);
    expect(addPayRowBtn.disabled).toBe(true); // 4 rows max reached

    fireEvent.click(within(paymentSection).getByRole('button', { name: 'Remove payment row 4' }));
    fireEvent.click(within(paymentSection).getByRole('button', { name: 'Remove payment row 3' }));
    const removeRow1 = within(paymentSection).getByRole('button', {
      name: 'Remove payment row 1',
    }) as HTMLButtonElement;
    expect(removeRow1.disabled).toBe(true); // 2 rows min reached

    fireEvent.click(
      within(paymentSection).getByRole('button', { name: "Reset to Pitch's default" }),
    );
    expect(within(paymentSection).getByTestId('payment-plan-sum').textContent).toContain('100%');

    // Timeline reorder & Reset to Pitch's default
    const timelineSection = screen.getByRole('region', { name: 'Timeline' });
    fireEvent.click(within(timelineSection).getByRole('button', { name: 'Move step 2 up' }));
    const step0Title = within(timelineSection).getByLabelText('Title', {
      selector: '#timeline-title-0',
    }) as HTMLInputElement;
    expect(step0Title.value).toBe('Step Two');
    fireEvent.click(
      within(timelineSection).getByRole('button', { name: "Reset to Pitch's default" }),
    );
    expect(
      (
        within(timelineSection).getByLabelText('Title', {
          selector: '#timeline-title-0',
        }) as HTMLInputElement
      ).value,
    ).toBe('Scope & Cost Estimation');

    // Service plans: period select options
    const spSection = screen.getByRole('region', { name: 'Service plans' });
    const periodSelect = within(spSection).getByLabelText('Billing period', {
      selector: '#sp-period-0',
    }) as HTMLSelectElement;
    expect(Array.from(periodSelect.options).map((o) => o.text)).toEqual([
      'One-time',
      'Monthly',
      'Quarterly',
      'Yearly',
    ]);
    fireEvent.change(periodSelect, { target: { value: 'yearly' } });
    expect(periodSelect.value).toBe('yearly');

    // Live < validation on new Plans fields
    const payLabel0 = within(paymentSection).getByLabelText('Label', {
      selector: '#pay-label-0',
    }) as HTMLInputElement;
    fireEvent.change(payLabel0, { target: { value: '<b>start</b>' } });
    expect(payLabel0.getAttribute('aria-invalid')).toBe('true');

    const payNote = screen.getByLabelText('Payment note') as HTMLTextAreaElement;
    fireEvent.change(payNote, { target: { value: 'Note <script>' } });
    expect(payNote.getAttribute('aria-invalid')).toBe('true');

    const stepDetail0 = within(timelineSection).getByLabelText('Detail', {
      selector: '#timeline-detail-0',
    }) as HTMLTextAreaElement;
    fireEvent.change(stepDetail0, { target: { value: 'Detail > bad' } });
    expect(stepDetail0.getAttribute('aria-invalid')).toBe('true');

    const spName0 = within(spSection).getByLabelText('Plan name', {
      selector: '#sp-name-0',
    }) as HTMLInputElement;
    fireEvent.change(spName0, { target: { value: 'Care <x>' } });
    expect(spName0.getAttribute('aria-invalid')).toBe('true');

    expect(screen.getAllByText('Text cannot contain < or >')).toHaveLength(4);
  });

  it('removes and adds back sheets, disables last software option, shows included_from_hidden note, and shows dropped-items notice after save', async () => {
    const catalog = makeCatalog();
    let currentProp = makeProposal({
      preset: {
        ...makeProposal().preset,
        initialSelected: ['a_base', 'b_db'],
        sections: {
          p2: ['w_front'],
          p3: ['a_base'],
          p4: ['b_db'],
          p5: ['ops_cloud'],
        },
      },
    });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1' && (!init?.method || init.method === 'GET')) {
        return jsonResponse(currentProp);
      }
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: currentProp.slug, pdfs: [] });
      }
      if (url === '/api/pitch/proposals/prop_1' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as Partial<PitchProposal>;
        currentProp = {
          ...currentProp,
          ...body,
          included_from_hidden: [
            {
              item_id: 'b_db',
              name: 'Core Database & API Server',
              sheet_shown_on: 'p3',
              needed_for: ['Cross-Platform Mobile App (Android)'],
            },
          ],
          changes: [
            {
              item_id: 'w_front',
              name: 'Responsive Web Application / Frontend',
              sheet: 'p2',
              dropped_from: 'sections',
              reason: 'hidden_sheet',
              message: "Dropped 'Responsive Web Application / Frontend' (w_front) because sheet p2 is hidden",
            },
          ],
        };
        return jsonResponse(currentProp);
      }
      return jsonResponse({});
    });

    render(<PitchSection param="prop_1" onNavigate={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('telemed-india')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('tab', { name: /Deliverables/i }));

    // Remove Web & SaaS (p2), Backend (p4), and AI & Ops (p5), leaving only Mobile app (p3)
    fireEvent.click(screen.getByRole('button', { name: 'Remove Web & SaaS from pitch' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Backend from pitch' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove AI & Ops from pitch' }));

    // Last remaining software option (Mobile app) has disabled Remove button + hint
    const lastRemoveBtn = screen.getByRole('button', {
      name: 'Remove Mobile app from pitch',
    }) as HTMLButtonElement;
    expect(lastRemoveBtn.disabled).toBe(true);
    expect(screen.getByText('Keep at least one software option')).toBeTruthy();

    // Since Backend (p4) is hidden and Mobile app (p3) has a_base which requires b_db,
    // p3 shows the included_from_hidden note
    const mobileSection = screen.getByRole('region', { name: 'Mobile app deliverables' });
    expect(within(mobileSection).getByText(/Core Database & API Server/)).toBeTruthy();
    expect(
      within(mobileSection).getByText(
        /Included because Cross-Platform Mobile App \(Android\) needs it/,
      ),
    ).toBeTruthy();

    // Add back AI & Ops -> Mobile app Remove button becomes enabled again
    fireEvent.click(screen.getByRole('button', { name: 'Add back AI & Ops' }));
    expect(lastRemoveBtn.disabled).toBe(false);

    // Save proposal -> shows dropped-items notice from engine changes
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(
        screen.getAllByText(
          /Dropped 'Responsive Web Application \/ Frontend' \(w_front\) because sheet p2 is hidden/,
        ).length,
      ).toBeGreaterThanOrEqual(1);
    });
  });

  it('removes and adds back About sections while keeping field values, and supports Timeline/Payment removal in final Plans tab', async () => {
    const catalog = makeCatalog();
    const proposal = makeProposal();
    const finalProp = makeProposal({
      id: 'prop_final_about',
      slug: 'final-about',
      kind: 'final',
      payment_plan: [
        { percent: 50, label: 'kickoff split' },
        { percent: 50, label: 'launch split' },
      ],
      payment_note: 'Custom final payment note',
      timeline: [
        { title: 'Phase Alpha', when: 'Week 1', detail: 'Alpha detail' },
      ],
    });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1') return jsonResponse(proposal);
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: proposal.slug, pdfs: [] });
      }
      if (url === '/api/pitch/proposals/prop_final_about') return jsonResponse(finalProp);
      if (url === '/api/pitch/proposals/prop_final_about/pdfs') {
        return jsonResponse({ proposal_id: 'prop_final_about', slug: finalProp.slug, pdfs: [] });
      }
      return jsonResponse({});
    });

    const { unmount } = render(<PitchSection param="prop_1" onNavigate={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('TeleMed Platform')).toBeTruthy();
    });

    // Title is always shown and has no Remove button
    expect(screen.getByText('Title is always shown')).toBeTruthy();

    // Edit Intro (Lead) and Portfolio, then remove both sections
    const leadInput = screen.getByLabelText(/^Lead/) as HTMLTextAreaElement;
    fireEvent.change(leadInput, { target: { value: 'Preserved lead copy' } });
    fireEvent.click(screen.getByRole('button', { name: 'Remove Intro from pitch' }));
    expect(screen.queryByLabelText(/^Lead/)).toBeNull();

    const portfolioInput = screen.getByLabelText('Portfolio', {
      selector: 'input',
    }) as HTMLInputElement;
    fireEvent.change(portfolioInput, { target: { value: 'custom-portfolio.example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Remove Portfolio from pitch' }));
    expect(screen.queryByLabelText('Portfolio', { selector: 'input' })).toBeNull();

    // Add back Intro and Portfolio -> edited values are preserved
    fireEvent.click(screen.getByRole('button', { name: 'Add back Intro' }));
    expect((screen.getByLabelText(/^Lead/) as HTMLTextAreaElement).value).toBe(
      'Preserved lead copy',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Add back Portfolio' }));
    expect(
      (
        screen.getByLabelText('Portfolio', {
          selector: 'input',
        }) as HTMLInputElement
      ).value,
    ).toBe('custom-portfolio.example.com');

    // Non-final Details tab also has Remove buttons for What we do, This plan, Architecture block, Timeline, How you pay
    expect(screen.getByRole('button', { name: 'Remove What we do from pitch' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Remove This plan from pitch' })).toBeTruthy();
    expect(
      screen.getByRole('button', { name: 'Remove Architecture block from pitch' }),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Remove Timeline from pitch' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Remove How you pay from pitch' })).toBeTruthy();

    unmount();

    // Mount final quotation and test Timeline & How you pay Remove/Add back in Plans tab
    render(<PitchSection param="prop_final_about" onNavigate={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('final-about')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('tab', { name: /Plans/i }));
    expect(screen.getByDisplayValue('kickoff split')).toBeTruthy();
    expect(screen.getByDisplayValue('Phase Alpha')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Remove How you pay from pitch' }));
    expect(screen.queryByDisplayValue('kickoff split')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add back How you pay' }));
    expect(screen.getByDisplayValue('kickoff split')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Remove Timeline from pitch' }));
    expect(screen.queryByDisplayValue('Phase Alpha')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add back Timeline' }));
    expect(screen.getByDisplayValue('Phase Alpha')).toBeTruthy();
  });

  it('adds, edits, reorders, and deletes custom categories for sheet and new_sheet placements, validates <, and counts own-page category as a software option', async () => {
    const catalog = makeCatalog();
    const prop = makeProposal();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1') return jsonResponse(prop);
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: prop.slug, pdfs: [] });
      }
      return jsonResponse({});
    });

    render(<PitchSection param="prop_1" onNavigate={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('telemed-india')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('tab', { name: /Deliverables/i }));

    // Open Add category form and test < validation on category fields
    fireEvent.click(screen.getByRole('button', { name: 'Add category' }));
    const catNameInput = screen.getByLabelText(/^Category name/) as HTMLInputElement;
    fireEvent.change(catNameInput, { target: { value: 'Bad <Cat>' } });
    expect(catNameInput.getAttribute('aria-invalid')).toBe('true');

    fireEvent.change(screen.getByLabelText(/^Placement/), {
      target: { value: 'new_sheet' },
    });
    const sheetTitleInput = screen.getByLabelText(/^Page title/) as HTMLInputElement;
    const sheetLeadInput = screen.getByLabelText(/^Page intro/) as HTMLInputElement;
    fireEvent.change(sheetTitleInput, { target: { value: 'Title <x>' } });
    fireEvent.change(sheetLeadInput, { target: { value: 'Lead > y' } });
    expect(sheetTitleInput.getAttribute('aria-invalid')).toBe('true');
    expect(sheetLeadInput.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getAllByText('Text cannot contain < or >')).toHaveLength(3);

    // Save category 1 placed on existing sheet p3 (Mobile app)
    fireEvent.change(catNameInput, { target: { value: 'Wearables' } });
    fireEvent.change(screen.getByLabelText(/^Placement/), {
      target: { value: 'p3' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save category' }));

    expect(screen.getAllByText('Wearables').length).toBeGreaterThanOrEqual(1);

    // Add category 2 placed on Its own page (new_sheet)
    fireEvent.click(screen.getByRole('button', { name: 'Add category' }));
    fireEvent.change(screen.getByLabelText(/^Category name/), {
      target: { value: 'Drone software' },
    });
    fireEvent.change(screen.getByLabelText(/^Placement/), {
      target: { value: 'new_sheet' },
    });
    fireEvent.change(screen.getByLabelText(/^Page title/), {
      target: { value: 'Autonomous Drone Stack' },
    });
    fireEvent.change(screen.getByLabelText(/^Page intro/), {
      target: { value: 'Flight control and telemetry' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save category' }));

    expect(screen.getAllByText('Drone software').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Its own page')).toBeTruthy();

    // Add a service to Drone software category
    fireEvent.change(screen.getByLabelText('Service name (Drone software)'), {
      target: { value: 'Flight Controller' },
    });
    fireEvent.change(screen.getByLabelText('Service description (Drone software)'), {
      target: { value: 'Autopilot and mission planner' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add service to Drone software' }));

    expect(screen.getAllByText('Flight Controller').length).toBeGreaterThanOrEqual(1);

    // Because Drone software has its own page AND >= 1 service, it counts as a software option!
    // So all 4 built-in sheets can now be removed if desired.
    fireEvent.click(screen.getByRole('button', { name: 'Remove Web & SaaS from pitch' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Mobile app from pitch' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Backend from pitch' }));
    const removeAiBtn = screen.getByRole('button', {
      name: 'Remove AI & Ops from pitch',
    }) as HTMLButtonElement;
    expect(removeAiBtn.disabled).toBe(false);
    fireEvent.click(removeAiBtn);

    // Add back Mobile app so we can reorder/edit/delete categories cleanly
    fireEvent.click(screen.getByRole('button', { name: 'Add back Mobile app' }));

    // Reorder categories: Move Drone software up
    fireEvent.click(screen.getByRole('button', { name: 'Move category Drone software up' }));
    const moveDroneUpBtn = screen.getByRole('button', {
      name: 'Move category Drone software up',
    }) as HTMLButtonElement;
    expect(moveDroneUpBtn.disabled).toBe(true);

    // Edit category Wearables -> rename to Clinical Wearables
    fireEvent.click(screen.getByRole('button', { name: 'Edit category Wearables' }));
    fireEvent.change(screen.getByLabelText(/^Category name/), {
      target: { value: 'Clinical Wearables' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Update category' }));
    expect(screen.getAllByText('Clinical Wearables').length).toBeGreaterThanOrEqual(1);

    // Delete Drone software category with confirmation -> its items are deleted too
    fireEvent.click(screen.getByRole('button', { name: 'Delete category Drone software' }));
    const delDialog = screen.getByRole('dialog');
    expect(
      within(delDialog).getByText(/All services in this category will be deleted too/),
    ).toBeTruthy();
    fireEvent.click(within(delDialog).getByRole('button', { name: 'Delete category' }));

    expect(screen.queryByRole('button', { name: 'Edit category Drone software' })).toBeNull();
    expect(screen.queryByText('Flight Controller')).toBeNull();
  });

  it('posts scroll-to messages on focus and IntersectionObserver, respects Follow in preview toggle, has sticky tabs with Previous/Next pager, and debounces live price updates', async () => {
    let observerCallback: ((entries: IntersectionObserverEntry[]) => void) | null = null;
    class MockIntersectionObserver {
      constructor(cb: (entries: IntersectionObserverEntry[]) => void) {
        observerCallback = cb;
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);

    const catalog = makeCatalog();
    const prop = makeProposal();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1') return jsonResponse(prop);
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: prop.slug, pdfs: [] });
      }
      if (url === '/api/pitch/preview_payload') {
        return jsonResponse({});
      }
      return jsonResponse({});
    });

    render(<PitchSection param="prop_1" onNavigate={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('telemed-india')).toBeTruthy();
    });

    const iframe = screen.getByTitle('Pitch preview for telemed-india') as HTMLIFrameElement;
    const postMessageMock = vi.fn();
    Object.defineProperty(iframe, 'contentWindow', {
      configurable: true,
      value: {
        postMessage: postMessageMock,
        downloadPdf: vi.fn(),
        focus: vi.fn(),
      },
    });

    // Sticky tab row is present
    const stickyBar = screen.getByTestId('pitch-sticky-tabs');
    expect(stickyBar.className).toContain('studio-pitch-tabs-sticky');

    // Focusing Lead field sends magnm:pitch-scroll-to with target 'about:lead'
    fireEvent.focus(screen.getByLabelText(/^Lead/));
    expect(postMessageMock).toHaveBeenCalledWith(
      { type: 'magnm:pitch-scroll-to', target: 'about:lead' },
      window.location.origin,
    );

    // IntersectionObserver callback posts topmost visible section target
    postMessageMock.mockClear();
    const archSection = document.querySelector(
      '[data-scroll-target="about:architecture"]',
    ) as HTMLElement;
    expect(archSection).toBeTruthy();
    if (observerCallback) {
      (observerCallback as (entries: IntersectionObserverEntry[]) => void)([
        {
          target: archSection,
          isIntersecting: true,
          boundingClientRect: { top: 40 } as DOMRectReadOnly,
        } as unknown as IntersectionObserverEntry,
      ]);
    }
    await waitFor(() => {
      expect(postMessageMock).toHaveBeenCalledWith(
        { type: 'magnm:pitch-scroll-to', target: 'about:architecture' },
        window.location.origin,
      );
    });

    // Turn off "Follow in preview" toggle -> focus and intersection no longer post scroll-to
    postMessageMock.mockClear();
    const followToggle = screen.getByLabelText('Follow in preview') as HTMLInputElement;
    expect(followToggle.checked).toBe(true);
    fireEvent.click(followToggle);
    expect(followToggle.checked).toBe(false);

    fireEvent.focus(screen.getByLabelText('Portfolio', { selector: 'input' }));
    expect(postMessageMock).not.toHaveBeenCalled();

    // Turn "Follow in preview" back on
    fireEvent.click(followToggle);
    expect(followToggle.checked).toBe(true);

    // Bottom pager: Next: Deliverables switches to Deliverables tab; Next: Prices switches to Prices; Previous: Deliverables switches back
    fireEvent.click(screen.getByRole('button', { name: 'Next: Deliverables' }));
    expect(
      screen.getByRole('tab', { name: /Deliverables/i }).getAttribute('aria-selected'),
    ).toBe('true');
    expect(screen.getByRole('button', { name: 'Previous: Details' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Next: Prices' }));
    expect(
      screen.getByRole('tab', { name: /Prices/i }).getAttribute('aria-selected'),
    ).toBe('true');

    // Editing a price on Prices tab posts a debounced (150ms) magnm:pitch-preview-update with the new price and a scroll-to item:<id>
    postMessageMock.mockClear();
    const wFrontPriceInput = screen.getByLabelText(
      'Proposal price for Responsive Web Application / Frontend',
    ) as HTMLInputElement;
    fireEvent.change(wFrontPriceInput, { target: { value: '8100' } });

    await waitFor(
      () => {
        expect(postMessageMock).toHaveBeenCalledWith(
          expect.objectContaining({
            type: 'magnm:pitch-preview-update',
            payload: expect.objectContaining({
              prices: expect.objectContaining({ w_front: 8100 }),
            }),
          }),
          window.location.origin,
        );
        expect(postMessageMock).toHaveBeenCalledWith(
          { type: 'magnm:pitch-scroll-to', target: 'item:w_front' },
          window.location.origin,
        );
      },
      { timeout: 1000 },
    );
  });

  it('sends hidden_sheets in preview_payload body when removing Web & SaaS and renders included_from_hidden from preview_payload response on sheet_shown_on', async () => {
    const catalog = makeCatalog();
    const prop = makeProposal();
    const previewBodies: Array<Record<string, unknown>> = [];

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1') return jsonResponse(prop);
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: prop.slug, pdfs: [] });
      }
      if (url === '/api/pitch/preview_payload' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as Record<string, unknown>;
        previewBodies.push(body);
        return jsonResponse({
          mode: 'preview',
          kind: 'proposal',
          locked: false,
          preset: prop.preset,
          prices: { w_front: 7200, b_db: 2800 },
          custom_items: [],
          hidden_sheets: body.hidden_sheets || [],
          hidden_about: body.hidden_about || [],
          custom_categories: body.custom_categories || [],
          included_from_hidden: [
            {
              item_id: 'w_front',
              name: 'Server Included Dependency',
              sheet_shown_on: 'p4',
              needed_for: ['Core Database & API Server'],
            },
          ],
          pricing_summary: {
            ...prop.pricing_summary,
            total: 9400,
          },
        });
      }
      return jsonResponse({});
    });

    render(<PitchSection param="prop_1" onNavigate={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('telemed-india')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('tab', { name: /Deliverables/i }));

    // Remove Web & SaaS from pitch -> next preview_payload request body has hidden_sheets containing p2
    fireEvent.click(screen.getByRole('button', { name: 'Remove Web & SaaS from pitch' }));

    await waitFor(() => {
      expect(previewBodies.length).toBeGreaterThanOrEqual(1);
      const lastBody = previewBodies[previewBodies.length - 1];
      expect(lastBody.hidden_sheets).toContain('p2');
    });

    // Server preview_payload response's included_from_hidden is shown on sheet_shown_on (p4 = Backend)
    const backendSection = screen.getByRole('region', { name: 'Backend deliverables' });
    await waitFor(() => {
      expect(within(backendSection).getByText('Server Included Dependency')).toBeTruthy();
      expect(
        within(backendSection).getByText(/Included because Core Database & API Server needs it/),
      ).toBeTruthy();
    });

    // Add back Web & SaaS -> next preview_payload request body has hidden_sheets without p2
    fireEvent.click(screen.getByRole('button', { name: 'Add back Web & SaaS' }));
    await waitFor(() => {
      const lastBody = previewBodies[previewBodies.length - 1];
      expect(lastBody.hidden_sheets).not.toContain('p2');
    });
  });

  it('collapses Portfolio to one row with Add back and no Title remove button, and updates hidden_about in final Plans tab', async () => {
    const catalog = makeCatalog();
    const proposal = makeProposal();
    const finalProp = makeProposal({
      id: 'prop_final_plans',
      slug: 'final-plans',
      kind: 'final',
      payment_plan: [
        { percent: 50, label: 'kickoff' },
        { percent: 50, label: 'launch' },
      ],
      payment_note: 'Final note',
      timeline: [{ title: 'Kickoff', when: 'Day 1', detail: 'Start' }],
    });
    const previewBodies: Array<Record<string, unknown>> = [];

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1') return jsonResponse(proposal);
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: proposal.slug, pdfs: [] });
      }
      if (url === '/api/pitch/proposals/prop_final_plans') return jsonResponse(finalProp);
      if (url === '/api/pitch/proposals/prop_final_plans/pdfs') {
        return jsonResponse({ proposal_id: 'prop_final_plans', slug: finalProp.slug, pdfs: [] });
      }
      if (url === '/api/pitch/preview_payload' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as Record<string, unknown>;
        previewBodies.push(body);
        return jsonResponse({
          mode: 'preview',
          kind: body.kind,
          locked: body.kind === 'final',
          preset: body.preset,
          hidden_about: body.hidden_about || [],
        });
      }
      return jsonResponse({});
    });

    const { unmount } = render(<PitchSection param="prop_1" onNavigate={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('TeleMed Platform')).toBeTruthy();
    });

    // Title has no remove button
    expect(screen.queryByRole('button', { name: /Remove.*Title/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Remove.*Headline/i })).toBeNull();

    // Portfolio section collapses to one row with Add back and preserves field value
    const portfolioSection = screen.getByRole('region', { name: 'Portfolio section' });
    const portfolioInput = within(portfolioSection).getByLabelText('Portfolio') as HTMLInputElement;
    fireEvent.change(portfolioInput, { target: { value: 'kept-portfolio.magnm.studio' } });
    fireEvent.click(within(portfolioSection).getByRole('button', { name: 'Remove Portfolio from pitch' }));

    expect(within(portfolioSection).queryByRole('textbox')).toBeNull();
    expect(within(portfolioSection).getByRole('button', { name: 'Add back Portfolio' })).toBeTruthy();

    fireEvent.click(within(portfolioSection).getByRole('button', { name: 'Add back Portfolio' }));
    expect(
      (within(portfolioSection).getByLabelText('Portfolio') as HTMLInputElement).value,
    ).toBe('kept-portfolio.magnm.studio');

    unmount();
    previewBodies.length = 0;

    // Final quotation Plans tab: Timeline and How you pay update hidden_about
    render(<PitchSection param="prop_final_plans" onNavigate={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('final-plans')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('tab', { name: /Plans/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove How you pay from pitch' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Timeline from pitch' }));

    await waitFor(() => {
      expect(previewBodies.length).toBeGreaterThanOrEqual(1);
      const lastBody = previewBodies[previewBodies.length - 1];
      expect(lastBody.hidden_about).toEqual(expect.arrayContaining(['payment', 'timeline']));
    });

    fireEvent.click(screen.getByRole('button', { name: 'Add back How you pay' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add back Timeline' }));

    await waitFor(() => {
      const lastBody = previewBodies[previewBodies.length - 1];
      expect(lastBody.hidden_about).toEqual([]);
    });
  });

  it('requires page title for own-page category and enforces max 6 custom categories', async () => {
    const catalog = makeCatalog();
    const prop = makeProposal();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1') return jsonResponse(prop);
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: prop.slug, pdfs: [] });
      }
      return jsonResponse({});
    });

    render(<PitchSection param="prop_1" onNavigate={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('telemed-india')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('tab', { name: /Deliverables/i }));

    // Try saving own-page category without page title -> shows validation message
    fireEvent.click(screen.getByRole('button', { name: 'Add category' }));
    fireEvent.change(screen.getByLabelText(/^Category name/), {
      target: { value: 'Hardware Systems' },
    });
    fireEvent.change(screen.getByLabelText(/^Placement/), {
      target: { value: 'new_sheet' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save category' }));

    expect(
      screen.getByText('Page title is required when placement is Its own page.'),
    ).toBeTruthy();

    // Provide Page title and save category 1
    fireEvent.change(screen.getByLabelText(/^Page title/), {
      target: { value: 'Embedded Hardware' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save category' }));
    expect(screen.getByText('Custom categories (1/6)')).toBeTruthy();

    // Add 5 more categories to reach max 6
    for (let i = 2; i <= 6; i += 1) {
      fireEvent.click(screen.getByRole('button', { name: 'Add category' }));
      fireEvent.change(screen.getByLabelText(/^Category name/), {
        target: { value: `Custom Category ${i}` },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Save category' }));
    }

    expect(screen.getByText('Custom categories (6/6)')).toBeTruthy();
    const addCatBtn = screen.getByRole('button', { name: 'Add category' }) as HTMLButtonElement;
    expect(addCatBtn.disabled).toBe(true);
  });

  it('posts about:portfolio and item:<id> on focus, persists Follow in preview in localStorage with try/catch, and scrolls form to top on Next/Previous navigation', async () => {
    const catalog = makeCatalog();
    const prop = makeProposal();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1') return jsonResponse(prop);
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: prop.slug, pdfs: [] });
      }
      return jsonResponse({});
    });

    const scrollToSpy = vi.fn();
    const scrollIntoViewSpy = vi.fn();
    vi.stubGlobal('scrollTo', scrollToSpy);
    Element.prototype.scrollIntoView = scrollIntoViewSpy;
    const setItemSpy = vi.fn();
    const getItemSpy = vi.fn(() => 'true');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: getItemSpy,
        setItem: setItemSpy,
        removeItem: vi.fn(),
        clear: vi.fn(),
      },
    });

    render(<PitchSection param="prop_1" onNavigate={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('telemed-india')).toBeTruthy();
    });

    const iframe = screen.getByTitle('Pitch preview for telemed-india') as HTMLIFrameElement;
    const postMessageMock = vi.fn();
    Object.defineProperty(iframe, 'contentWindow', {
      configurable: true,
      value: {
        postMessage: postMessageMock,
        downloadPdf: vi.fn(),
        focus: vi.fn(),
      },
    });

    // Focusing Portfolio field posts about:portfolio
    fireEvent.focus(screen.getByLabelText('Portfolio', { selector: 'input' }));
    expect(postMessageMock).toHaveBeenCalledWith(
      { type: 'magnm:pitch-scroll-to', target: 'about:portfolio' },
      window.location.origin,
    );

    // Next: Deliverables at the end of Details switches tab and scrolls form to top
    fireEvent.click(screen.getByRole('button', { name: 'Next: Deliverables' }));
    expect(
      screen.getByRole('tab', { name: /Deliverables/i }).getAttribute('aria-selected'),
    ).toBe('true');
    expect(scrollIntoViewSpy).toHaveBeenCalled();
    expect(scrollToSpy).toHaveBeenCalled();

    // Navigate to last tab (Prices) and verify Previous: Deliverables works on last tab
    fireEvent.click(screen.getByRole('button', { name: 'Next: Prices' }));
    expect(screen.getByRole('tab', { name: /Prices/i }).getAttribute('aria-selected')).toBe(
      'true',
    );

    // Focusing a price input posts item:<id>
    postMessageMock.mockClear();
    const wFrontInput = screen.getByLabelText(
      'Proposal price for Responsive Web Application / Frontend',
    );
    fireEvent.focus(wFrontInput);
    expect(postMessageMock).toHaveBeenCalledWith(
      { type: 'magnm:pitch-scroll-to', target: 'item:w_front' },
      window.location.origin,
    );

    // Turning off Follow in preview persists to localStorage and handles localStorage errors gracefully
    const followToggle = screen.getByLabelText('Follow in preview') as HTMLInputElement;
    fireEvent.click(followToggle);
    expect(setItemSpy).toHaveBeenCalledWith('magnm-pitch-follow-preview', 'false');

    setItemSpy.mockImplementationOnce(() => {
      throw new Error('QuotaExceededError');
    });
    expect(() => fireEvent.click(followToggle)).not.toThrow();

    // Previous: Deliverables works on the last tab
    scrollIntoViewSpy.mockClear();
    scrollToSpy.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Previous: Deliverables' }));
    expect(
      screen.getByRole('tab', { name: /Deliverables/i }).getAttribute('aria-selected'),
    ).toBe('true');
    expect(scrollIntoViewSpy).toHaveBeenCalled();
    expect(scrollToSpy).toHaveBeenCalled();
  });

  it('debounces price edits with fake timers, updates bottom bar to Total (unsaved) with preview_payload total and recurring line, and restores Total after save', async () => {
    const catalog = makeCatalog();
    let currentProp = makeProposal({
      price_overrides: { w_front: 7200 },
      pricing_summary: {
        lines: [],
        custom_items: [],
        subtotal: 10000,
        standalone_fee: 0,
        standalone_label: '',
        total: 10000,
        differs_from_base: [],
      },
    });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pitch/catalog') return jsonResponse(catalog);
      if (url === '/api/pitch/proposals/prop_1' && (!init?.method || init.method === 'GET')) {
        return jsonResponse(currentProp);
      }
      if (url === '/api/pitch/proposals/prop_1/pdfs') {
        return jsonResponse({ proposal_id: 'prop_1', slug: currentProp.slug, pdfs: [] });
      }
      if (url === '/api/pitch/preview_payload' && init?.method === 'POST') {
        return jsonResponse({
          mode: 'preview',
          kind: currentProp.kind,
          locked: false,
          preset: currentProp.preset,
          prices: { w_front: 8500, b_db: 2800 },
          custom_items: [],
          hidden_sheets: [],
          hidden_about: [],
          custom_categories: [],
          included_from_hidden: [],
          pricing_summary: {
            ...currentProp.pricing_summary,
            subtotal: 11300,
            total: 11300,
            recurring_totals: { monthly: 3200, quarterly: 0, yearly: 0 },
          },
        });
      }
      if (url === '/api/pitch/proposals/prop_1' && init?.method === 'POST') {
        currentProp = {
          ...currentProp,
          price_overrides: { w_front: 8500 },
          pricing_summary: {
            ...currentProp.pricing_summary,
            subtotal: 11300,
            total: 11300,
          },
        };
        return jsonResponse(currentProp);
      }
      return jsonResponse({});
    });

    render(<PitchSection param="prop_1" onNavigate={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('telemed-india')).toBeTruthy();
    });

    const iframe = screen.getByTitle('Pitch preview for telemed-india') as HTMLIFrameElement;
    const postMessageMock = vi.fn();
    Object.defineProperty(iframe, 'contentWindow', {
      configurable: true,
      value: {
        postMessage: postMessageMock,
        downloadPdf: vi.fn(),
        focus: vi.fn(),
      },
    });

    // Switch to Prices tab -> preview is visible
    fireEvent.click(screen.getByRole('tab', { name: /Prices/i }));
    expect(screen.getByTestId('pitch-side-preview').hidden).toBe(false);
    expect(screen.getAllByText('Total').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('₹10,000').length).toBeGreaterThanOrEqual(1);

    // Use fake timers to verify price edit posts within the 150ms debounce window
    vi.useFakeTimers();
    try {
      const wFrontInput = screen.getByLabelText(
        'Proposal price for Responsive Web Application / Frontend',
      ) as HTMLInputElement;
      fireEvent.change(wFrontInput, { target: { value: '8500' } });

      expect(postMessageMock).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(160);
      });

      expect(postMessageMock).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'magnm:pitch-preview-update',
          payload: expect.objectContaining({
            prices: expect.objectContaining({ w_front: 8500 }),
          }),
        }),
        window.location.origin,
      );
      expect(postMessageMock).toHaveBeenCalledWith(
        { type: 'magnm:pitch-scroll-to', target: 'item:w_front' },
        window.location.origin,
      );
    } finally {
      vi.useRealTimers();
    }

    // Bottom bar caption shows "Total (unsaved)" with preview_payload total (₹11,300) and recurring line
    await waitFor(() => {
      expect(screen.getByText('Total (unsaved)')).toBeTruthy();
      expect(screen.getAllByText('₹11,300').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('+ ₹3,200 / month')).toBeTruthy();
    });

    // Save proposal -> bottom bar goes back to "Total" with saved total
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(screen.queryByText('Total (unsaved)')).toBeNull();
      expect(screen.getAllByText('Total').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('₹11,300').length).toBeGreaterThanOrEqual(1);
    });
  });
});
