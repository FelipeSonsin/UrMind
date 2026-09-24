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
  source_location: 'gps_device' | 'exif' | 'manual' | 'unknown';
  location_timestamp: string | null;
  heading_deg: number | null;
  speed_mps: number | null;
  note: string;
  privacy_version?: string;
  additional_to?: string;
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
export const defaultPhotoPolicy = {
  min_side: 640,
  brightness_min: 20,
  brightness_max: 240,
  laplacian_min: 25,
};
export async function validatePhoto(file: File, policy = defaultPhotoPolicy): Promise<void> {
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type))
    throw new Error('Use uma imagem JPEG, PNG ou WebP.');
  if (!/\.(jpe?g|png|webp)$/i.test(file.name))
    throw new Error('A extensão deve ser JPEG, PNG ou WebP.');
  if (file.size === 0 || file.size > 10 * 1024 * 1024)
    throw new Error('A imagem deve ter conteúdo e no máximo 10 MB (limite local).');
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error('Não foi possível ler o conteúdo desta imagem.');
  }
  try {
    if (bitmap.width * bitmap.height > 40_000_000)
      throw new Error('Imagem com dimensões acima do limite.');
    if (Math.min(bitmap.width, bitmap.height) < policy.min_side)
      throw new Error(`Foto pequena: use pelo menos ${policy.min_side} pixels em cada lado.`);
    const scale = Math.min(1, 512 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) throw new Error('Pré-verificação indisponível neste navegador.');
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const gray = new Float32Array(canvas.width * canvas.height);
    let sum = 0;
    for (let i = 0; i < gray.length; i++) {
      gray[i] = 0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2];
      sum += gray[i];
    }
    const brightness = sum / gray.length;
    if (brightness < policy.brightness_min)
      throw new Error('Foto muito escura: tente com mais luz.');
    if (brightness > policy.brightness_max)
      throw new Error('Foto muito clara: evite luz direta na câmera.');
    let lapSum = 0,
      lapSquares = 0,
      count = 0;
    const w = canvas.width;
    for (let y = 1; y < canvas.height - 1; y++)
      for (let x = 1; x < w - 1; x++) {
        const i = y * w + x;
        const lap = gray[i - 1] + gray[i + 1] + gray[i - w] + gray[i + w] - 4 * gray[i];
        lapSum += lap;
        lapSquares += lap * lap;
        count++;
      }
    if (!count || lapSquares / count - (lapSum / count) ** 2 < policy.laplacian_min)
      throw new Error('Foto tremida ou desfocada: estabilize a câmera e ajuste o foco.');
  } finally {
    bitmap.close();
  }
}
