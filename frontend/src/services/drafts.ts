import { openDB, type DBSchema } from 'idb';
import type { Coordinate } from '../domain/contracts';

export interface CaptureDraft {
  id: string;
  photo: Blob;
  filename: string;
  source: 'pwa_photo' | 'exif_upload';
  captured_at: string | null;
  created_at: string;
  coordinate: Coordinate | null;
  source_location: 'gps_device' | 'manual' | 'unknown';
  location_timestamp: string | null;
  heading_deg: number | null;
  speed_mps: number | null;
  note: string;
  status: 'local_draft';
}
interface DraftDatabase extends DBSchema {
  drafts: { key: string; value: CaptureDraft };
}
function database() {
  return openDB<DraftDatabase>('urmind-local-drafts', 1, {
    upgrade(db) {
      db.createObjectStore('drafts', { keyPath: 'id' });
    },
  });
}
export const drafts = {
  async list() {
    const db = await database();
    try {
      return (await db.getAll('drafts')).sort((a, b) => b.created_at.localeCompare(a.created_at));
    } finally {
      db.close();
    }
  },
  async save(draft: CaptureDraft) {
    const db = await database();
    try {
      await db.put('drafts', draft);
    } finally {
      db.close();
    }
  },
  async remove(id: string) {
    const db = await database();
    try {
      await db.delete('drafts', id);
    } finally {
      db.close();
    }
  },
};
export async function validatePhoto(file: File): Promise<void> {
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type))
    throw new Error('Use uma imagem JPEG, PNG ou WebP.');
  if (!/\.(jpe?g|png|webp)$/i.test(file.name))
    throw new Error('A extensão deve ser JPEG, PNG ou WebP.');
  if (file.size === 0 || file.size > 10 * 1024 * 1024)
    throw new Error('A imagem deve ter conteúdo e no máximo 10 MB (limite local).');
  try {
    const bitmap = await createImageBitmap(file);
    bitmap.close();
  } catch {
    throw new Error('Não foi possível ler o conteúdo desta imagem.');
  }
}
