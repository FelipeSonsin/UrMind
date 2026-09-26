import { z } from 'zod';
import {
  issueTaxonomySchema,
  publicEventDetailSchema,
  publicEventSchema,
  publicStatusSchema,
  transparencySchema,
} from '../domain/public';

import { requestJson } from './api';
import { auth } from './auth';
import { captureMarkerSchema } from '../domain/contracts';

// Painel público: sem token e somente leitura, sobre o mesmo cliente HTTP da área logada.
const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '') + '/public';

function get<T>(
  path: string,
  schema: z.ZodType<T>,
  signal?: AbortSignal,
  token?: string | null,
): Promise<T> {
  return requestJson(`${base}${path}`, schema, {
    signal,
    token,
    cache: token ? 'no-store' : undefined,
    messages: {
      network: 'Não foi possível conectar ao UrMind.',
      timeout: 'O UrMind demorou a responder.',
      contract: 'A resposta da API não corresponde ao contrato público.',
    },
  });
}

export interface EventQuery {
  limit?: number;
  urmind_class?: string;
  status?: string;
  bbox?: { south: number; west: number; north: number; east: number };
}

export const addressSearchSchema = z.object({
  results: z.array(
    z.object({
      label: z.string(),
      detail: z.string().nullable(),
      latitude: z.number().min(-90).max(90),
      longitude: z.number().min(-180).max(180),
    }),
  ),
  attribution: z.string(),
});
export type AddressResult = z.infer<typeof addressSearchSchema>['results'][number];

export const publicApi = {
  /**
   * Busca explícita (botão ou Enter), nunca a cada tecla: o servidor respeita o limite
   * de 1 consulta por segundo do Nominatim. POST mantém o endereço fora dos logs de URL.
   */
  searchAddress: (query: string, signal?: AbortSignal) =>
    requestJson(`${base}/geocode`, addressSearchSchema, {
      method: 'POST',
      signal,
      timeoutMs: 12_000,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
      statusError: (status) =>
        status === 429
          ? new Error('A busca está ocupada. Aguarde um instante e tente de novo.')
          : status === 422
            ? new Error('Digite ao menos 3 letras do endereço ou um CEP.')
            : status === 503
              ? new Error('Busca de endereço indisponível agora. Marque o local no mapa.')
              : undefined,
      messages: {
        network: 'Sem conexão para buscar o endereço. Marque o local no mapa.',
        timeout: 'A busca de endereço demorou a responder. Tente de novo.',
        contract: 'A busca de endereço devolveu uma resposta inesperada.',
      },
    }),
  privacyNotice: (signal?: AbortSignal) =>
    get(
      '/privacy-notice',
      z.object({ version: z.string().min(1), text: z.string().min(1) }),
      signal,
    ),
  photoPolicy: (signal?: AbortSignal) =>
    get(
      '/photo-policy',
      z.object({
        min_side: z.number().min(256).max(4096),
        brightness_min: z.number().min(0).max(100),
        brightness_max: z.number().min(150).max(255),
        laplacian_min: z.number().min(1).max(1000),
      }),
      signal,
    ),
  publishedEvent: (id: string, signal?: AbortSignal) =>
    get(`/events/${id}`, publicEventDetailSchema, signal),
  captureMarkers: (signal?: AbortSignal) =>
    get('/capture-markers', z.array(captureMarkerSchema), signal),
  status: (signal?: AbortSignal) => get('/status', publicStatusSchema, signal),
  transparency: (signal?: AbortSignal) => get('/transparency', transparencySchema, signal),
  taxonomy: (signal?: AbortSignal) => get('/taxonomy', issueTaxonomySchema, signal),
  // Existing owner session only: browsing never creates an anonymous account.
  event: async (id: string, signal?: AbortSignal) =>
    get(`/events/${id}`, publicEventDetailSchema, signal, await auth.accessToken()),
  events: (query: EventQuery = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    params.set('limit', String(query.limit ?? 50));
    if (query.urmind_class) params.set('urmind_class', query.urmind_class);
    if (query.status) params.set('status', query.status);
    if (query.bbox)
      for (const [key, value] of Object.entries(query.bbox)) params.set(key, String(value));
    return get(`/events?${params}`, z.array(publicEventSchema), signal);
  },
};
