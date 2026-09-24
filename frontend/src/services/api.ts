import { z } from 'zod';
import {
  captureProcessingSchema,
  captureMarkerSchema,
  eventDetailSchema,
  eventSchema,
  uploadResultSchema,
  type ReviewPayload,
} from '../domain/contracts';
import { auth } from './auth';
import type { CaptureDraft } from './drafts';

const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');

export class UnauthorizedError extends Error {
  constructor() {
    super('Sessão expirada ou ausente. Entre novamente.');
  }
}

export interface JsonRequestOptions extends RequestInit {
  timeoutMs?: number;
  token?: string | null;
  messages: { network: string; timeout: string; contract: string };
  /** Erro específico para um status HTTP; `undefined` cai no tratamento genérico. */
  statusError?: (status: number) => Error | undefined;
}

/** Único cliente HTTP do frontend: fetch + timeout + zod. Área logada e painel público o reutilizam. */
export async function requestJson<T>(
  url: string,
  schema: z.ZodType<T>,
  options: JsonRequestOptions,
): Promise<T> {
  const { timeoutMs = 8000, signal, token, messages, statusError, ...rest } = options;
  const timeout = AbortSignal.timeout(timeoutMs);
  try {
    const response = await fetch(url, {
      ...rest,
      cache: token ? 'no-store' : rest.cache,
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      headers: {
        Accept: 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...rest.headers,
      },
    });
    const specific = statusError?.(response.status);
    if (specific) throw specific;
    if (!response.ok) {
      const detail = await response
        .json()
        .then((body: { detail?: unknown }) => (typeof body.detail === 'string' ? body.detail : ''))
        .catch(() => '');
      throw new Error(detail || `A API respondeu com erro ${response.status}.`);
    }
    const parsed = schema.safeParse(await response.json());
    if (!parsed.success) throw new Error(messages.contract);
    return parsed.data;
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof TypeError) throw new Error(messages.network);
    if (error instanceof DOMException && error.name === 'TimeoutError')
      throw new Error(messages.timeout);
    throw error;
  }
}

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  init: RequestInit & {
    timeoutMs?: number;
    token?: string;
    statusError?: (status: number) => Error | undefined;
  } = {},
): Promise<T> {
  const { statusError, token, ...requestInit } = init;
  return requestJson(`${base}${path}`, schema, {
    ...requestInit,
    token: token ?? (await auth.accessToken()),
    messages: {
      network: 'Não foi possível conectar ao backend.',
      timeout: 'O backend demorou a responder. Tente novamente.',
      contract: 'A resposta da API não corresponde ao contrato esperado.',
    },
    statusError: (status) =>
      statusError?.(status) ??
      (status === 401
        ? new UnauthorizedError()
        : status === 503
          ? new Error('Banco ou integração indisponível ou ainda não configurado.')
          : undefined),
  });
}

export const api = {
  captureMarkers: (signal?: AbortSignal, onlyMine = false) =>
    request(
      `/captures/markers${onlyMine ? '?only_mine=true&include_unlocated=true' : ''}`,
      z.array(captureMarkerSchema),
      { signal },
    ),
  captureImage: (id: string, signal?: AbortSignal) =>
    request(`/captures/${id}/image`, z.object({ image_url: z.string() }), { signal }),
  captureLocation: (id: string, latitude: number, longitude: number, signal?: AbortSignal) =>
    request(
      `/captures/${id}/location`,
      z.object({ capture_id: z.string().uuid(), location_source: z.literal('manual') }),
      {
        method: 'PATCH',
        body: JSON.stringify({ latitude, longitude }),
        headers: { 'Content-Type': 'application/json' },
        signal,
      },
    ),
  opsMetrics: (signal?: AbortSignal) =>
    request(
      '/ops/metrics',
      z.object({
        queue: z.object({
          pending: z.number().nonnegative(),
          processing: z.number().nonnegative(),
          archived: z.number().nonnegative(),
          retried: z.number().nonnegative(),
        }),
        outcomes: z.record(z.string(), z.number().nonnegative()),
        latency_ms: z.object({
          p50_ms: z.number().nullable(),
          p95_ms: z.number().nullable(),
          samples: z.number().nonnegative(),
        }),
      }),
      { signal },
    ),
  health: (signal?: AbortSignal) =>
    request('/health', z.object({ status: z.string(), database: z.string().optional() }), {
      signal,
    }),
  events: (signal?: AbortSignal) => request('/events?limit=500', z.array(eventSchema), { signal }),
  me: (signal?: AbortSignal) =>
    request(
      '/me',
      z.object({
        id: z.string(),
        email: z.string().nullable(),
        can_review: z.boolean(),
        can_admin: z.boolean().default(false),
      }),
      { signal },
    ),
  event: (id: string, signal?: AbortSignal) =>
    request(`/events/${id}`, eventDetailSchema, { signal }),
  captureProcessing: (id: string, signal?: AbortSignal) =>
    request(`/captures/${id}/processing`, captureProcessingSchema, {
      signal,
      statusError: (status) =>
        status === 404 ? new Error('Captura não encontrada ou sem acesso.') : undefined,
    }),
  review: (id: string, payload: ReviewPayload) =>
    request(`/events/${id}/reviews`, z.object({ status: z.string() }), {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: { 'Content-Type': 'application/json' },
    }),
  uploadPhoto(draft: CaptureDraft, signal?: AbortSignal, token?: string) {
    const form = new FormData();
    form.append('file', draft.photo, draft.filename || 'foto.jpg');
    form.append('source', draft.source);
    if (draft.note.trim()) form.append('user_description', draft.note);
    if (draft.coordinate) {
      form.append('latitude', String(draft.coordinate.latitude));
      form.append('longitude', String(draft.coordinate.longitude));
      if (draft.coordinate.accuracy_m != null)
        form.append('accuracy_m', String(draft.coordinate.accuracy_m));
      if (draft.source_location !== 'exif') {
        form.append(
          'location_source',
          draft.source_location === 'manual' ? 'manual' : 'gps_device',
        );
        if (draft.source_location === 'gps_device') {
          if (draft.location_timestamp) form.append('location_timestamp', draft.location_timestamp);
          if (draft.heading_deg != null) form.append('heading_deg', String(draft.heading_deg));
          if (draft.speed_mps != null) form.append('speed_mps', String(draft.speed_mps));
        }
      } else {
        // The server reads the original EXIF and persists its own authoritative parse.
        form.delete('latitude');
        form.delete('longitude');
        form.delete('accuracy_m');
      }
    }
    if (draft.source === 'pwa_photo' && draft.captured_at)
      form.append('captured_at', draft.captured_at);
    return request('/captures/photo', uploadResultSchema, {
      method: 'POST',
      body: form,
      timeoutMs: 60000,
      signal,
      token,
    });
  },
};
