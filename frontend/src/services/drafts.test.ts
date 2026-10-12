import 'fake-indexeddb/auto';
import { describe, expect, it } from 'vitest';
import { defaultPhotoPolicy, drafts, photoProblem, type CaptureDraft } from './drafts';

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

describe('checagem técnica da foto', () => {
  const ok = {
    width: 4032,
    height: 3024,
    bytes: 2_400_000,
    type: 'image/jpeg',
    brightness: 128,
    sharpness: 96,
  };

  it('aprova a foto que cumpre a política e devolve null', () => {
    expect(photoProblem(ok, defaultPhotoPolicy)).toBeNull();
  });

  it('aponta um problema por vez, na ordem tamanho, luz e foco', () => {
    const policy = defaultPhotoPolicy;
    expect(photoProblem({ ...ok, height: 480, brightness: 5, sharpness: 1 }, policy)).toBe(
      'Foto pequena: use pelo menos 640 pixels em cada lado.',
    );
    expect(photoProblem({ ...ok, brightness: 5, sharpness: 1 }, policy)).toBe(
      'Foto muito escura: tente com mais luz.',
    );
    expect(photoProblem({ ...ok, brightness: 250 }, policy)).toBe(
      'Foto muito clara: evite luz direta na câmera.',
    );
    expect(photoProblem({ ...ok, sharpness: 24.9 }, policy)).toBe(
      'Foto tremida ou desfocada: estabilize a câmera e ajuste o foco.',
    );
  });

  it('usa os limites recebidos do servidor', () => {
    expect(
      photoProblem({ ...ok, width: 900, height: 900 }, { ...defaultPhotoPolicy, min_side: 1024 }),
    ).toBe('Foto pequena: use pelo menos 1024 pixels em cada lado.');
  });
});
