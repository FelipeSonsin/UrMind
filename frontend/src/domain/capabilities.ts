import { robotIceServers } from './robotCamera';

/**
 * O que o UrMind realmente faz hoje, num só lugar. A interface pública só mostra uma
 * capacidade que está ligada aqui; categoria sem modelo nunca aparece como automática.
 * O relato do cidadão (qualquer problema, revisado por pessoas) é separado da detecção
 * automática, que só reconhece as classes de pavimento do modelo ativo.
 */
export const DEFAULT_AUTOMATIC_CLASSES = [
  'URMIND_ROAD_D40',
  'URMIND_ROAD_D00',
  'URMIND_ROAD_D10',
  'URMIND_ROAD_D20',
] as const;

/**
 * Câmera do robô só aparece com o que ela precisa para funcionar de ponta a ponta:
 * o projeto Supabase (sinalização Realtime) e o STUN do WebRTC configurados no build.
 */
export function robotCameraReady(env: Record<string, unknown>): boolean {
  const text = (key: string) => (typeof env[key] === 'string' ? (env[key] as string).trim() : '');
  if (!text('VITE_SUPABASE_URL') || !text('VITE_SUPABASE_PUBLISHABLE_KEY')) return false;
  try {
    robotIceServers(text('VITE_ROBOT_CAMERA_STUN_URL'));
    return true;
  } catch {
    return false;
  }
}

export const activeCapabilities = {
  /** Detecção no próprio aparelho, pela câmera, com o modelo publicado. */
  liveDetection: true,
  /** Localização do aparelho no momento da foto. */
  gps: true,
  /** Localização gravada na própria foto. */
  exif: true,
  /** Mapa com relatos próprios e ocorrências confirmadas. */
  map: true,
  /** Foto enviada como relato, com revisão humana. */
  capture: true,
  /** Busca de endereço para marcar o local quando não há GPS nem EXIF. */
  addressSearch: true,
  /** Celular como câmera sem fio do notebook, com a mesma detecção (WebRTC + Realtime). */
  robotCamera: robotCameraReady(import.meta.env),
  /** Classes que a detecção automática reconhece (códigos internos D40, D00, D10, D20). */
  supportedAutomaticClasses: DEFAULT_AUTOMATIC_CLASSES as readonly string[],
};

/**
 * Classes automáticas vindas da taxonomia servida (`model_may_emit`); sem ela, as quatro
 * do modelo ativo. Nunca inclui categoria marcada como em desenvolvimento.
 */
export function automaticClasses(emittable: readonly string[]): readonly string[] {
  if (!emittable.length) return activeCapabilities.supportedAutomaticClasses;
  // Ordem de leitura pública: buraco primeiro, depois as trincas.
  const known: readonly string[] = DEFAULT_AUTOMATIC_CLASSES;
  const rank = (code: string) => (known.includes(code) ? known.indexOf(code) : known.length);
  return [...emittable].sort((a, b) => rank(a) - rank(b));
}
