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
  /**
   * Foto vinda da câmera de outro aparelho (celular do robô): o GPS deste aparelho não
   * descreve onde ela foi tirada, então o local vem da busca de endereço ou do mapa.
   */
  camera_origin?: 'robot_remote';
  status: 'local_draft';
}
/**
 * No banco local a foto vai como texto (base64): é o formato que todo navegador guarda
 * sem falhar. O WebKit recusa Blob no banco e às vezes derruba a página com bytes crus.
 * Rascunhos antigos, guardados como Blob ou bytes, ainda abrem.
 */
interface StoredDraft extends Omit<CaptureDraft, 'photo'> {
  photo: Blob | { bytes: ArrayBuffer; type: string } | { base64: string; type: string };
}
interface DraftDatabase extends DBSchema {
  drafts: { key: string; value: StoredDraft };
}
const STORAGE_ERROR =
  'Não foi possível guardar a foto neste aparelho. Verifique se há espaço livre e tente de novo.';

/** Bytes em base64, em pedaços (uma foto grande não estoura a pilha). */
function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let start = 0; start < bytes.length; start += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  return btoa(binary);
}
function fromBase64(text: string): Uint8Array<ArrayBuffer> {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}
function fromStored(stored: StoredDraft): CaptureDraft {
  const { photo } = stored;
  const blob =
    photo instanceof Blob
      ? photo
      : 'base64' in photo
        ? new Blob([fromBase64(photo.base64)], { type: photo.type })
        : new Blob([photo.bytes], { type: photo.type });
  return { ...stored, photo: blob };
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
      return (await db.getAll('drafts'))
        .map(fromStored)
        .sort((a, b) => b.created_at.localeCompare(a.created_at));
    } finally {
      db.close();
    }
  },
  async save(draft: CaptureDraft) {
    let stored: StoredDraft;
    try {
      stored = {
        ...draft,
        photo: { base64: toBase64(await draft.photo.arrayBuffer()), type: draft.photo.type },
      };
    } catch {
      throw new Error(STORAGE_ERROR);
    }
    const db = await database().catch(() => {
      throw new Error(STORAGE_ERROR);
    });
    try {
      await db.put('drafts', stored);
    } catch {
      throw new Error(STORAGE_ERROR);
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
export type PhotoPolicy = typeof defaultPhotoPolicy;

/** O que a checagem técnica mediu na foto (mesmo cálculo que o servidor repete). */
export interface PhotoMeasure {
  width: number;
  height: number;
  bytes: number;
  type: string;
  /** Brilho médio, de 0 (preto) a 255 (branco). */
  brightness: number;
  /** Variância do laplaciano: quanto maior, mais nítida. */
  sharpness: number;
}

/**
 * Regras de qualidade, na ordem em que a pessoa deve corrigir: tamanho, luz e foco.
 * Devolve a primeira mensagem que falha, ou `null` quando a foto passa.
 */
export function photoProblem(measure: PhotoMeasure, policy: PhotoPolicy): string | null {
  if (Math.min(measure.width, measure.height) < policy.min_side)
    return `Foto pequena: use pelo menos ${policy.min_side} pixels em cada lado.`;
  if (measure.brightness < policy.brightness_min) return 'Foto muito escura: tente com mais luz.';
  if (measure.brightness > policy.brightness_max)
    return 'Foto muito clara: evite luz direta na câmera.';
  if (measure.sharpness < policy.laplacian_min)
    return 'Foto tremida ou desfocada: estabilize a câmera e ajuste o foco.';
  return null;
}

/** Confere a foto antes de guardar; devolve as medidas para a pessoa ver. */
export async function validatePhoto(
  file: File,
  policy: PhotoPolicy = defaultPhotoPolicy,
): Promise<PhotoMeasure> {
  const measure = await measurePhoto(file);
  const problem = photoProblem(measure, policy);
  if (problem) throw new Error(problem);
  return measure;
}

/** Formato, tamanho e leitura da imagem; depois mede dimensões, brilho e nitidez. */
export async function measurePhoto(file: File): Promise<PhotoMeasure> {
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
    return {
      width: bitmap.width,
      height: bitmap.height,
      bytes: file.size,
      type: file.type,
      brightness,
      sharpness: count ? lapSquares / count - (lapSum / count) ** 2 : 0,
    };
  } finally {
    bitmap.close();
  }
}

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const MAX_UPLOAD_PIXELS = 40_000_000;
const CAMERA_MAX_SIDE = 4032;

/**
 * Phone cameras can exceed the 10 MB / 40 MP upload limits. A photo taken now
 * is re-encoded to fit: its location comes from the device GPS, so the EXIF
 * lost on re-encoding carries nothing the report needs. Gallery photos are
 * never resized here, because their EXIF GPS is the evidence of where they were taken.
 */
export async function fitCameraPhoto(file: File): Promise<File> {
  if (file.size <= MAX_UPLOAD_BYTES * 0.95) {
    let bitmap: ImageBitmap | null = null;
    try {
      bitmap = await createImageBitmap(file);
      if (bitmap.width * bitmap.height <= MAX_UPLOAD_PIXELS) return file;
    } catch {
      return file; // validatePhoto reports unreadable images with its own message.
    } finally {
      bitmap?.close();
    }
  }
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    return file;
  }
  try {
    const scale = Math.min(1, CAMERA_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', 0.88),
    );
    if (!blob) return file;
    const name = file.name.replace(/\.[^.]+$/, '') || 'foto';
    return new File([blob], `${name}.jpg`, { type: 'image/jpeg', lastModified: file.lastModified });
  } finally {
    bitmap.close();
  }
}
