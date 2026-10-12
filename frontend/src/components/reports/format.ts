import type { CaptureMarker } from '../../domain/contracts';
import { labelFor } from '../../domain/public';

/**
 * Nome do relato nas listas: o tipo confirmado pela equipe ("Buraco · Grave") ou,
 * antes da revisão, o que a pessoa escreveu ao enviar.
 */
export function reportTitle(report: CaptureMarker): string {
  // Só o tipo: a gravidade e a situação têm lugar próprio na linha do relato.
  if (report.urmind_class) return labelFor(report.urmind_class);
  return report.user_description?.trim() || 'Relato sem descrição';
}

/** Rua e bairro do relato, para listas; `null` sem endereço confirmado. */
export function reportPlace(report: CaptureMarker): string | null {
  const address = report.address;
  if (address?.status !== 'ok') return null;
  return [address.road, address.suburb].filter(Boolean).join(', ') || null;
}

/** Rua e bairro do relato (a cidade só sem bairro), no mesmo formato das listas. */
export function reportAddress(report: CaptureMarker): string | null {
  const address = report.address;
  if (address?.status !== 'ok') return null;
  return [address.road, address.suburb || address.city].filter(Boolean).join(', ') || null;
}

/** De onde veio a localização do relato, em palavras simples. */
export const LOCATION_SOURCES: Record<string, string> = {
  gps_device: 'GPS do aparelho',
  gps_scout: 'GPS do robô',
  exif: 'localização gravada na foto',
  manual: 'ponto marcado no mapa',
  imported: 'ponto importado',
  unknown: 'origem não informada',
};

/** Latitude e longitude em graus decimais (5 casas, cerca de 1 m), prontas para copiar. */
export function formatCoordinates(point: { latitude: number; longitude: number }): string {
  return `${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}`;
}
