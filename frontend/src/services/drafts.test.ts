import 'fake-indexeddb/auto';
import { describe, expect, it } from 'vitest';
import { drafts, type CaptureDraft } from './drafts';

describe('rascunhos offline', () => {
  it('persiste foto sem localização, atualiza pelo mesmo ID e exclui apenas o alvo', async () => {
    const draft: CaptureDraft = {
      id: 'rascunho-a',
      photo: new Blob(['imagem-local'], { type: 'image/jpeg' }),
      filename: 'foto.jpg',
      source: 'exif_upload',
      captured_at: null,
      created_at: '2026-09-07T12:00:00Z',
      coordinate: null,
      source_location: 'unknown',
      location_timestamp: null,
      heading_deg: null,
      speed_mps: null,
      note: '',
      status: 'local_draft',
    };
    await drafts.save(draft);
    await drafts.save({ ...draft, id: 'rascunho-b' });
    await drafts.save({ ...draft, note: 'Observação atualizada' });
    const stored = await drafts.list();
    expect(stored).toHaveLength(2);
    const first = stored.find((item) => item.id === draft.id)!;
    expect(first.coordinate).toBeNull();
    expect(first.captured_at).toBeNull();
    expect(first.status).toBe('local_draft');
    expect(first.note).toBe('Observação atualizada');
    expect(await first.photo.text()).toBe('imagem-local');
    await drafts.remove(draft.id);
    expect((await drafts.list()).map((item) => item.id)).toEqual(['rascunho-b']);
    await drafts.remove('rascunho-b');
  });
});
