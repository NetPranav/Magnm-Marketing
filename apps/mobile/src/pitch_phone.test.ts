import { beforeEach, describe, expect, it } from 'vitest';
import { PhoneActionError, enqueueAction, loadActions, patchAction, validateActionInput } from './actions';
import { SESSION_SECRET_KEY } from './auth';
import type { ContextCacheState } from './cache';
import { sendWaitingActions } from './cloudActions';
import fixture from './fixtures/phone_pitch_bodies.json';
import { createPreviewNativeBridge, type NativeHttpRequest, type NativeHttpResponse } from './native';
import { getPublicSupabaseConfig } from './net';
import {
  buildCreateInput,
  buildFinalizeInput,
  buildLinkInput,
  buildUpdateInput,
  changedFields,
  cleanQuoteRequests,
  editValuesOf,
  hasWaitingEdit,
  linkStatusOf,
  mergePitchLists,
  pitchProposalsFromCache,
  proposalsFromMirror,
  requestItemNames,
} from './pitchPhone';
import { fetchQuoteRequests } from './pitchRequests';
import type { MirrorRecord, PhoneAction, PitchProposalItem, ProductRulesConfig } from './types';

const SUPA = getPublicSupabaseConfig();
const UID = 'uid-alpha';
const RULES: ProductRulesConfig = {
  version: 1,
  show_levels: { everything: 'All', nothing: 'Nothing' },
  products: [
    { id: 'cero', name: 'Cero', status: 'released', show: 'everything', confidential: false },
    { id: 'secretproj', name: 'ProjectNightfall', status: 'secret', show: 'nothing', confidential: true },
  ],
};
const VERSION = '2026-11-23T10:00:00.123456+00:00';

type Http = (req: NativeHttpRequest) => Promise<NativeHttpResponse>;

async function bridgeWith(httpHandler: Http, uid = UID) {
  const bridge = createPreviewNativeBridge({ httpHandler });
  await bridge.secureStore.set(
    SESSION_SECRET_KEY,
    JSON.stringify({ access_token: `jwt-${uid}`, refresh_token: 'ref', expires_at: 9_999_999_999, user_id: uid, email: `${uid}@example.com` }),
  );
  return bridge;
}

const CATALOG = {
  items: {
    w_front: { g: 'Web & SaaS', n: 'Responsive Web Application', p: 6000 },
    w_anim: { g: 'Web & SaaS', n: 'Advanced UI', p: 1800 },
    b_api: { g: 'Backend', n: 'REST API', p: 5000 },
  },
  groups: ['Web & SaaS', 'Mobile app', 'Backend'],
  presets: [{ slug: 'urban-company', name: 'Urban Company-style' }],
};

function mirrorRec(id: string, data: Record<string, unknown>, extra: Partial<MirrorRecord> = {}): MirrorRecord {
  return {
    id: `row-${id}`,
    owner_id: UID,
    kind: 'pitch_proposal',
    local_id: id,
    title: typeof data.name === 'string' ? data.name : 'Confidential item',
    data,
    local_updated_at: VERSION,
    cached_at: Date.now(),
    ...extra,
  };
}

function proposalData(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: 'Acme Co · Acme',
    kind: 'proposal',
    status: 'draft',
    version: VERSION,
    editable: true,
    client_name: 'Acme',
    business_name: 'Acme Co',
    contact: { name: 'Acme Owner', phone: '+91 1' },
    notes: 'Internal',
    preset: { title: 'Plan', lead: 'Lead', whatWeDo: 'Do', portfolio: 'site', thisPlan: 'This', initialSelected: ['w_front'], sections: { p2: ['w_front'], p3: [], p4: [], p5: [] } },
    hidden_sheets: [],
    hidden_about: [],
    price_overrides: {},
    share_id: 'AAAAAAAAAAAAAAAAAAAAAA',
    link_status: null,
    link_url: null,
    payload: { mode: 'present', prices: { w_front: 6000 } },
    catalog: CATALOG,
    ...over,
  };
}

function cacheOf(records: MirrorRecord[], laptop: PitchProposalItem[] | null = null): ContextCacheState {
  return {
    ownerUid: UID,
    mirrorRecords: records,
    productRules: null,
    laptop: laptop
      ? ({ deviceId: 'd', status: null, drafts: [], draftDetails: {}, reminders: [], notifications: [], unreadCount: 0, summary: null, pendingResults: [], pitchProposals: laptop, pitchPricesAsOf: 1, cachedAt: 1 } as never)
      : null,
  } as unknown as ContextCacheState;
}

function fixtureBody(name: string, proposal = 'prop_abc', version = VERSION) {
  const item = fixture.actions.find((a) => a.name === name)!;
  return JSON.parse(JSON.stringify(item.body).replace('{{PROPOSAL}}', proposal).replace('{{VERSION}}', version)) as {
    id: string;
    kind: PhoneAction['kind'];
    target_id: string;
    payload: PhoneAction['payload'];
  };
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('queueing pitch actions', () => {
  it('queues each pitch kind as waiting, bound to the account', () => {
    const create = enqueueAction(buildCreateInput({ client_name: 'Meera', kind: 'proposal' }), UID, RULES);
    const update = enqueueAction({ kind: 'pitch_update', target_id: 'prop_1', payload: { base_version: VERSION, fields: { notes: 'x' } } }, UID, RULES);
    const finalize = enqueueAction({ kind: 'pitch_finalize', target_id: 'prop_1', payload: {} }, UID, RULES);
    const link = enqueueAction({ kind: 'pitch_link', target_id: 'prop_1', payload: { action: 'publish' } }, UID, RULES);
    for (const a of [create, update, finalize, link]) {
      expect(a.state).toBe('waiting');
      expect(a.owner_uid).toBe(UID);
    }
    expect(loadActions(UID).map((a) => a.kind)).toEqual(['pitch_create', 'pitch_update', 'pitch_finalize', 'pitch_link']);
  });

  it('refuses angle brackets in every text, as the web app does', () => {
    const texts: Array<Record<string, unknown>> = [
      { client_name: '<script>alert(1)</script>' },
      { business_name: 'a > b' },
      { notes: 'x < y' },
      { contact: { name: 'a<b' } },
      { contact: { notes: '<b>' } },
      { preset: { title: 't>' } },
      { preset: { lead: '<img src=x>' } },
      { preset: { whatWeDo: '<' } },
      { preset: { portfolio: '>' } },
      { preset: { thisPlan: '<p>' } },
    ];
    for (const fields of texts) {
      expect(() => validateActionInput({ kind: 'pitch_update', target_id: 'prop_1', payload: { base_version: VERSION, fields } as never }), JSON.stringify(fields)).toThrow(PhoneActionError);
      expect(() => validateActionInput({ kind: 'pitch_create', target_id: 'new', payload: fields as never }), JSON.stringify(fields)).toThrow(PhoneActionError);
    }
    expect(loadActions(UID)).toHaveLength(0);
  });

  it('refuses fields a phone may not set, and malformed shapes', () => {
    const forbidden = ['slug', 'status', 'kind', 'created_by', 'share_id', 'custom_items', 'payment_plan', 'link_status', 'id', 'updated_at'];
    for (const key of forbidden) {
      expect(() => validateActionInput({ kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { [key]: 'x' } } as never }), key).toThrow(PhoneActionError);
    }
    const bad: Array<Parameters<typeof validateActionInput>[0]> = [
      { kind: 'pitch_update', target_id: 'p', payload: { fields: { notes: 'x' } } },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION } },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: {} } },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: '', fields: { notes: 'x' } } },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: 'v'.repeat(65), fields: { notes: 'x' } } },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: 'v\u0000', fields: { notes: 'x' } } },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { notes: 'x' }, extra: 1 } as never },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { notes: 'x'.repeat(4001) } } },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { notes: 'a\u0001b' } } },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { contact: { whatsapp: '1' } } } as never },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { preset: { slug: 'x' } } } as never },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { preset: { sections: { p9: [] } } } } as never },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { hidden_sheets: ['p9'] } } },
      { kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { hidden_about: ['title'] } } },
      { kind: 'pitch_update', target_id: 'new', payload: { base_version: VERSION, fields: { notes: 'x' } } },
      { kind: 'pitch_create', target_id: 'prop_1', payload: {} },
      { kind: 'pitch_create', target_id: 'new', payload: { kind: 'final' } as never },
      { kind: 'pitch_create', target_id: 'new', payload: { kind: 'default' } as never },
      { kind: 'pitch_create', target_id: 'new', payload: { slug: 'mine' } as never },
      { kind: 'pitch_create', target_id: 'new', payload: { from_preset: '../etc' } },
      { kind: 'pitch_create', target_id: 'new', payload: { duplicate_of: 'p' } as never },
      { kind: 'pitch_finalize', target_id: 'p', payload: { slug: 'x' } as never },
      { kind: 'pitch_finalize', target_id: 'new', payload: {} },
      { kind: 'pitch_link', target_id: 'p', payload: {} },
      { kind: 'pitch_link', target_id: 'p', payload: { action: 'delete' } as never },
      { kind: 'pitch_link', target_id: 'p', payload: { action: 'publish', share_id: 'x' } as never },
    ];
    for (const input of bad) {
      expect(() => validateActionInput(input), JSON.stringify(input).slice(0, 90)).toThrow(PhoneActionError);
      expect(() => enqueueAction(input, UID, RULES)).toThrow(PhoneActionError);
    }
    expect(loadActions(UID)).toHaveLength(0);
  });

  it('only a person-typed whole-number price is accepted', () => {
    const ok = (price: unknown) =>
      validateActionInput({ kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { price_overrides: { w_front: price } } } as never });
    expect(ok(0).payload.fields?.price_overrides).toEqual({ w_front: 0 });
    expect(ok(10_000_000).payload.fields?.price_overrides).toEqual({ w_front: 10_000_000 });
    for (const price of ['6000', 12.5, true, null, -1, 10_000_001, NaN, Infinity, { p: 1 }, [1]]) {
      expect(() => ok(price), String(price)).toThrow(PhoneActionError);
    }
    const many = Object.fromEntries(Array.from({ length: 101 }, (_, i) => [`item_${i}`, 1]));
    expect(() => validateActionInput({ kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { price_overrides: many } } })).toThrow(PhoneActionError);
    expect(() => validateActionInput({ kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: { price_overrides: [] } as never } })).toThrow(PhoneActionError);
  });

  it('never queues text naming a confidential product, anywhere in the details, or any text when the rules are unknown', () => {
    const edits: Array<Record<string, unknown>> = [
      { notes: 'About ProjectNightfall' },
      { contact: { notes: 'wants projectnightfall' } },
      { preset: { lead: 'Built on ProjectNightfall' } },
      { price_overrides: { ProjectNightfall: 5 } },
      { preset: { initialSelected: ['ProjectNightfall'] } },
    ];
    for (const fields of edits) {
      expect(() => enqueueAction({ kind: 'pitch_update', target_id: 'p', payload: { base_version: VERSION, fields: fields as never } }, UID, RULES), JSON.stringify(fields)).toThrow(/confidential/i);
    }
    expect(() => enqueueAction(buildCreateInput({ client_name: 'ProjectNightfall Ltd' }), UID, RULES)).toThrow(/confidential/i);
    expect(() => enqueueAction(buildCreateInput({ client_name: 'Fine' }), UID, null)).toThrow(/rules/i);
    expect(() => enqueueAction({ kind: 'pitch_finalize', target_id: 'p', payload: {}, targetConfidential: true }, UID, RULES)).toThrow(/confidential/i);
    expect(() => enqueueAction({ kind: 'pitch_link', target_id: 'p', payload: { action: 'publish' }, targetConfidential: true }, UID, RULES)).toThrow(/confidential/i);
    expect(window.localStorage.getItem('magnm.mobile.phoneActions.v1') ?? '').not.toContain('Nightfall');
    expect(loadActions(UID)).toHaveLength(0);
  });

  it('lets only one edit of a proposal wait at a time, so a stale second edit never becomes a copy', () => {
    const first = enqueueAction({ kind: 'pitch_update', target_id: 'prop_1', payload: { base_version: VERSION, fields: { notes: 'one' } } }, UID, RULES);
    expect(() =>
      enqueueAction({ kind: 'pitch_update', target_id: 'prop_1', payload: { base_version: VERSION, fields: { notes: 'two' } } }, UID, RULES),
    ).toThrow(/earlier edit/i);
    // Another proposal, a link change and a final quotation are not blocked.
    enqueueAction({ kind: 'pitch_update', target_id: 'prop_2', payload: { base_version: VERSION, fields: { notes: 'two' } } }, UID, RULES);
    enqueueAction({ kind: 'pitch_link', target_id: 'prop_1', payload: { action: 'publish' } }, UID, RULES);
    patchAction(UID, first.id, { state: 'applied' });
    enqueueAction({ kind: 'pitch_update', target_id: 'prop_1', payload: { base_version: 'newer', fields: { notes: 'two' } } }, UID, RULES);
    expect(loadActions(UID)).toHaveLength(4);
  });

  it('drops a tampered stored pitch action instead of trusting it', () => {
    const good = enqueueAction({ kind: 'pitch_link', target_id: 'prop_1', payload: { action: 'publish' } }, UID, RULES);
    const stored = JSON.parse(window.localStorage.getItem('magnm.mobile.phoneActions.v1') || '[]') as PhoneAction[];
    stored.push({ ...good, id: '11111111-1111-4111-8111-111111111111', payload: { action: 'delete' } as never });
    stored.push({ ...good, id: '22222222-2222-4222-8222-222222222222', kind: 'pitch_update', payload: { base_version: VERSION, fields: { slug: 'x' } } as never });
    window.localStorage.setItem('magnm.mobile.phoneActions.v1', JSON.stringify(stored));
    expect(loadActions(UID).map((a) => a.id)).toEqual([good.id]);
  });
});

describe('contract with the laptop', () => {
  it('sends exactly the bodies in the shared fixture, which the laptop tests apply', async () => {
    const seen: Array<Record<string, unknown>> = [];
    const bridge = await bridgeWith(async (req) => {
      seen.push(JSON.parse(req.body || '{}'));
      return { status: 201, headers: {}, body: '' };
    });
    const bodies = fixture.actions.map((a) => fixtureBody(a.name));
    for (const b of bodies) {
      // The same builders the screens use, so the screens and the fixture cannot drift apart.
      const input =
        b.kind === 'pitch_create'
          ? { kind: b.kind, target_id: b.target_id, payload: b.payload }
          : { kind: b.kind, target_id: b.target_id, payload: b.payload };
      enqueueAction(input, UID, RULES, b.id);
    }
    await sendWaitingActions(UID, bridge);
    expect(seen).toEqual(bodies);
  });

  it('the screen builders make the fixture bodies', () => {
    const item: PitchProposalItem = { id: 'prop_abc', name: 'Acme', kind: 'proposal', cloud: proposalsFromMirror(cacheOf([mirrorRec('prop_abc', proposalData())]))[0].cloud };
    const make = (input: ReturnType<typeof buildCreateInput>, id: string) => {
      const a = enqueueAction(input, UID, RULES, id);
      return { id: a.id, kind: a.kind, target_id: a.target_id, payload: a.payload };
    };
    expect(make(buildCreateInput(fixtureBody('create').payload as never), fixtureBody('create').id)).toEqual(fixtureBody('create'));
    expect(make(buildCreateInput(fixtureBody('create_from_preset').payload as never), fixtureBody('create_from_preset').id)).toEqual(fixtureBody('create_from_preset'));
    expect(make(buildUpdateInput(item, fixtureBody('update').payload.fields!), fixtureBody('update').id)).toEqual(fixtureBody('update'));
    expect(make(buildFinalizeInput(item), fixtureBody('finalize').id)).toEqual(fixtureBody('finalize'));
    expect(make(buildLinkInput(item, 'publish'), fixtureBody('publish').id)).toEqual(fixtureBody('publish'));
    expect(make(buildLinkInput(item, 'withdraw'), fixtureBody('withdraw').id)).toEqual(fixtureBody('withdraw'));
  });
});

describe('the proposals the phone shows from the cloud mirror', () => {
  it('lists proposals with everything the editor needs, the default quotation first', () => {
    const items = proposalsFromMirror(
      cacheOf([
        mirrorRec('prop_a', proposalData()),
        mirrorRec('prop_default', proposalData({ name: 'Default quotation', kind: 'default', editable: false }), { local_updated_at: '2026-01-01T00:00:00+00:00' }),
        mirrorRec('prop_b', proposalData({ name: 'Newer', version: '2026-11-24T10:00:00+00:00' }), { local_updated_at: '2026-11-24T10:00:00+00:00' }),
      ]),
    );
    expect(items.map((i) => i.id)).toEqual(['prop_default', 'prop_b', 'prop_a']);
    const a = items.find((i) => i.id === 'prop_a')!;
    expect(a.cloud?.version).toBe(VERSION);
    expect(a.cloud?.editable).toBe(true);
    expect(a.cloud?.preset.initialSelected).toEqual(['w_front']);
    expect(a.cloud?.catalog?.items.w_front.n).toBe('Responsive Web Application');
    expect(items.find((i) => i.id === 'prop_default')?.cloud?.editable).toBe(false);
    expect(a.payload).toEqual({ mode: 'present', prices: { w_front: 6000 } });
  });

  it('keeps a confidential proposal a stub with no text and skips records that are not well formed', () => {
    const items = proposalsFromMirror(
      cacheOf([
        mirrorRec('prop_conf', { confidential: true, name: 'ProjectNightfall plan', notes: 'secret', payload: { x: 1 } }),
        mirrorRec('prop_bad', { version: 'v'.repeat(80) }),
        mirrorRec('', proposalData()),
        { ...mirrorRec('other_kind', proposalData()), kind: 'content_item' },
      ]),
    );
    expect(items).toEqual([{ id: 'prop_conf', confidential: true }]);
    expect(JSON.stringify(items)).not.toContain('Nightfall');
  });

  it('shows a link address only for a live link and only if it is https', () => {
    const live = proposalsFromMirror(cacheOf([mirrorRec('p1', proposalData({ link_status: 'live', link_url: 'https://magnm-pitch.vercel.app/p/AAAA' }))]))[0];
    expect(live.cloud?.link_url).toBe('https://magnm-pitch.vercel.app/p/AAAA');
    for (const url of ['javascript:alert(1)', 'http://magnm-pitch.vercel.app/p/A', 'https://a b', 42]) {
      const item = proposalsFromMirror(cacheOf([mirrorRec('p2', proposalData({ link_status: 'live', link_url: url }))]))[0];
      expect(item.cloud?.link_url, String(url)).toBeNull();
    }
    const withdrawn = proposalsFromMirror(cacheOf([mirrorRec('p3', proposalData({ link_status: 'withdrawn', link_url: 'https://x.example/p/A' }))]))[0];
    expect(withdrawn.cloud?.link_url).toBeNull();
    expect(linkStatusOf(live, [])).toEqual({ state: 'live', waiting: null, url: 'https://magnm-pitch.vercel.app/p/AAAA' });
  });

  it('a cut-down copy can be presented but not edited here', () => {
    const item = proposalsFromMirror(cacheOf([mirrorRec('p1', proposalData({ truncated: true }))]))[0];
    expect(item.cloud?.editable).toBe(false);
  });

  it('merges the live laptop list with the cloud copies: newer payload wins, the cloud info is kept, confidential stays a stub', () => {
    const mirror = proposalsFromMirror(cacheOf([mirrorRec('prop_a', proposalData()), mirrorRec('prop_c', { confidential: true })]));
    const live: PitchProposalItem[] = [
      { id: 'prop_a', name: 'Acme Co · Acme', kind: 'proposal', updated_at: '2026-11-23T11:00:00+00:00', payload: { mode: 'present', prices: { w_front: 6100 } } },
      { id: 'prop_c', name: 'Visible on laptop', kind: 'proposal', updated_at: '2026-11-23T11:00:00+00:00', payload: { leak: true } },
      { id: 'prop_only_laptop', name: 'Not mirrored yet', kind: 'proposal', payload: { a: 1 } },
    ];
    const merged = mergePitchLists(live, mirror);
    const a = merged.find((i) => i.id === 'prop_a')!;
    expect(a.payload).toEqual({ mode: 'present', prices: { w_front: 6100 } });
    expect(a.cloud?.version).toBe(VERSION);
    expect(merged.find((i) => i.id === 'prop_c')).toEqual({ id: 'prop_c', confidential: true });
    expect(merged.find((i) => i.id === 'prop_only_laptop')?.cloud).toBeUndefined();
    // An older laptop copy loses to a newer cloud copy.
    const older = mergePitchLists([{ id: 'prop_a', updated_at: '2020-01-01T00:00:00+00:00', payload: { old: true } }], mirror);
    expect(older.find((i) => i.id === 'prop_a')?.payload).toEqual({ mode: 'present', prices: { w_front: 6000 } });
    expect(pitchProposalsFromCache(cacheOf([mirrorRec('prop_a', proposalData())], live)).map((i) => i.id)).toContain('prop_only_laptop');
  });
});

describe('editing', () => {
  const item = (): PitchProposalItem => proposalsFromMirror(cacheOf([mirrorRec('prop_abc', proposalData())]))[0];

  it('sends only what changed, based on the version the phone saw', () => {
    const cloud = item().cloud!;
    const values = editValuesOf(cloud);
    expect(changedFields(cloud, values)).toEqual({});
    values.client_name = 'Acme Ltd';
    values.contact.email = 'o@acme.example';
    values.lead = 'New lead';
    values.price_overrides = { w_front: 6500 };
    values.hidden_about = ['portfolio'];
    values.sections = { ...values.sections, p2: ['w_front', 'w_anim'] };
    values.initialSelected = ['w_front', 'w_anim'];
    const fields = changedFields(cloud, values);
    expect(fields).toEqual({
      client_name: 'Acme Ltd',
      contact: { email: 'o@acme.example' },
      preset: { lead: 'New lead', initialSelected: ['w_front', 'w_anim'], sections: { p2: ['w_front', 'w_anim'], p3: [], p4: [], p5: [] } },
      hidden_about: ['portfolio'],
      price_overrides: { w_front: 6500 },
    });
    const input = buildUpdateInput(item(), fields);
    expect(input).toMatchObject({ kind: 'pitch_update', target_id: 'prop_abc', payload: { base_version: VERSION } });
    expect(enqueueAction(input, UID, RULES).state).toBe('waiting');
  });

  it('a proposal that has not reached the cloud cannot be edited', () => {
    expect(() => buildUpdateInput({ id: 'x', name: 'Laptop only' }, { notes: 'a' })).toThrow(/cloud/i);
  });

  it('knows about a waiting edit and a waiting link change', () => {
    const it0 = item();
    const edit = enqueueAction(buildUpdateInput(it0, { notes: 'x' }), UID, RULES);
    const link = enqueueAction(buildLinkInput(it0, 'publish'), UID, RULES);
    const actions = loadActions(UID);
    expect(hasWaitingEdit(it0, actions)).toBe(true);
    expect(linkStatusOf(it0, actions)).toEqual({ state: 'none', waiting: 'publish', url: null });
    patchAction(UID, edit.id, { state: 'applied' });
    patchAction(UID, link.id, { state: 'rejected', result: 'Not applied' });
    expect(hasWaitingEdit(it0, loadActions(UID))).toBe(false);
    expect(linkStatusOf(it0, loadActions(UID)).waiting).toBeNull();
  });
});

describe('quote requests', () => {
  const rows = [
    { id: 'q1', owner_id: UID, share_id: 'AAAAAAAAAAAAAAAAAAAAAA', created_at: '2026-11-23T09:00:00Z', seen_at: null, choices: { selected: ['w_front', 'custom_x'], custom_selected: ['custom_x'], total_shown: 7800 }, contact: { name: 'Client One', business: 'Biz', phone: '+91 2', notes: 'Soon' } },
    { id: 'q2', owner_id: 'uid-beta', share_id: 'BBBBBBBBBBBBBBBBBBBBBB', created_at: '2026-11-23T09:00:00Z', choices: {}, contact: {} },
    { id: 7, owner_id: UID, share_id: 'x' },
    'junk',
    null,
  ];

  it('keeps only this account\'s well formed rows and bounds every text', () => {
    const out = cleanQuoteRequests([...rows, { id: 'q3', owner_id: UID, share_id: 'C'.repeat(22), choices: { selected: Array(500).fill('w_front'), total_shown: 'lots' }, contact: { name: 'x'.repeat(900), notes: 'y'.repeat(5000) } }], UID);
    expect(out.map((r) => r.id)).toEqual(['q1', 'q3']);
    expect(out[0]).toMatchObject({ name: 'Client One', selected: ['w_front', 'custom_x'], total_shown: 7800 });
    expect(out[1].name.length).toBeLessThanOrEqual(200);
    expect(out[1].notes.length).toBeLessThanOrEqual(1000);
    expect(out[1].selected.length).toBeLessThanOrEqual(100);
    expect(out[1].total_shown).toBeNull();
    expect(cleanQuoteRequests({ not: 'a list' }, UID)).toEqual([]);
  });

  it('names the chosen services from the catalog and the proposal\'s own custom items', () => {
    const proposals = proposalsFromMirror(cacheOf([mirrorRec('prop_a', proposalData({ payload: { custom_items: [{ id: 'custom_x', n: 'Custom thing' }] } }))]));
    const req = cleanQuoteRequests(rows, UID)[0];
    expect(requestItemNames(req, proposals)).toEqual(['Responsive Web Application', 'Custom thing']);
  });

  it('reads only the signed-in account\'s requests and never marks anything seen', async () => {
    const calls: Array<{ url: string; method: string }> = [];
    const bridge = await bridgeWith(async (req) => {
      calls.push({ url: req.url, method: req.method });
      return { status: 200, headers: {}, body: JSON.stringify(rows) };
    });
    const out = await fetchQuoteRequests(UID, bridge);
    expect(out.map((r) => r.id)).toEqual(['q1']);
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe('GET');
    expect(calls[0].url).toContain(`${SUPA.url}/rest/v1/quote_submissions?owner_id=eq.${UID}`);
    expect(await fetchQuoteRequests('uid-beta', bridge)).toEqual([]);
    expect(await fetchQuoteRequests('', bridge)).toEqual([]);
    const missing = await bridgeWith(async () => ({ status: 404, headers: {}, body: '{"message":"not found"}' }));
    expect(await fetchQuoteRequests(UID, missing)).toEqual([]);
  });
});
