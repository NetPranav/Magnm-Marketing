import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

    // Prices tab: hides side preview, shows toggle (remembered in component state), compact Base column, Reset button text
    expect(screen.getByTestId('pitch-side-preview').hidden).toBe(false);
    expect(screen.queryByRole('button', { name: 'Show preview' })).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: /Prices/i }));
    expect(screen.getByTestId('pitch-side-preview').hidden).toBe(true);
    const previewToggle = screen.getByRole('button', { name: 'Show preview' });
    fireEvent.click(previewToggle);
    expect(screen.getByTestId('pitch-side-preview').hidden).toBe(false);
    expect(screen.getByRole('button', { name: 'Hide preview' })).toBeTruthy();

    // Switching tabs remembers preview toggle choice for the session
    fireEvent.click(screen.getByRole('tab', { name: /Details/i }));
    expect(screen.getByTestId('pitch-side-preview').hidden).toBe(false);
    fireEvent.click(screen.getByRole('tab', { name: /Prices/i }));
    expect(screen.getByTestId('pitch-side-preview').hidden).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Hide preview' }));
    expect(screen.getByTestId('pitch-side-preview').hidden).toBe(true);

    const priceTable = screen.getByRole('table');
    expect(within(priceTable).getByText('Admin')).toBeTruthy();
    expect(within(priceTable).getByText('Admin ₹2,800')).toBeTruthy();
    expect(within(priceTable).getAllByText('This proposal').length).toBeGreaterThanOrEqual(1);

    const wFrontInput = screen.getByLabelText(
      'Proposal price for Responsive Web Application / Frontend',
    ) as HTMLInputElement;
    expect(wFrontInput.value).toBe('7200');

    // Reset w_front to base -> button text is "Reset", clears override, source becomes Pitch
    const wFrontResetBtn = screen.getByRole('button', {
      name: 'Reset to base for Responsive Web Application / Frontend',
    });
    expect(wFrontResetBtn.textContent).toBe('Reset');
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
});
