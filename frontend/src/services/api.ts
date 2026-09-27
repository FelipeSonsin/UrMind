import { z } from 'zod';
import {
  captureProcessingSchema,
  captureMarkerSchema,
  eventDetailSchema,
  eventSchema,
  uploadResultSchema,
  type ReviewPayload,
} from '../domain/contracts';
import { localDayStart } from '../domain/public';
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

export const photoGatePolicySchema = z.object({
  public_capture_markers_enabled: z.boolean(),
  min_side: z.number(),
  brightness_min: z.number(),
  brightness_max: z.number(),
  laplacian_min: z.number(),
  phash_distance: z.number(),
  old_photo_days: z.number(),
  scene_accept_margin: z.number(),
  scene_reject_margin: z.number(),
  dominant_face_ratio: z.number(),
  nearby_radius_m: z.number(),
});

async function authenticatedDownload(path: string, signal: AbortSignal): Promise<Blob> {
  const token = await auth.accessToken();
  if (!token) throw new UnauthorizedError();
  const response = await fetch(`${base}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: 'no-store',
    signal,
  });
  if (response.status === 401) throw new UnauthorizedError();
  if (!response.ok) throw new Error(`Exportação indisponível (${response.status})`);
  const blob = await response.blob();
  if (signal.aborted || (await auth.accessToken()) !== token) throw new UnauthorizedError();
  return blob;
}

export const api = {
  exportReports: async (
    format: 'csv' | 'geojson',
    filters: { status: string; family: string; issue: string; from: string; to: string },
    signal: AbortSignal,
  ) => {
    const params = new URLSearchParams({
      format,
      status: filters.status,
      family: filters.family,
      issue: filters.issue,
    });
    // Mesmo dia civil que o mapa filtrou: meia-noite no fuso de quem exporta.
    if (filters.from) params.set('start', new Date(localDayStart(filters.from)).toISOString());
    if (filters.to)
      params.set('end', new Date(localDayStart(filters.to) + 86_400_000 - 1).toISOString());
    return authenticatedDownload(`/captures/export?${params}`, signal);
  },
  reportTotals: (signal?: AbortSignal, days = 30) =>
    request(
      `/ops/reports?days=${days}`,
      z.object({
        today: z.number(),
        week: z.number(),
        awaiting_review: z.number(),
        without_location: z.number(),
        location_conflicts: z.number(),
        published: z.number(),
        day_timezone: z.literal('UTC'),
        rejected_by_reason: z.record(z.string(), z.number()),
        gate_metrics: z
          .object({
            days: z.number(),
            rejected: z.number(),
            accepted: z.number(),
            retry_confirmed: z.number(),
            rates_by_reason: z.record(z.string(), z.number().nullable()),
            false_rejection_estimate: z.number().nullable(),
            estimate_method: z.string(),
          })
          .optional(),
      }),
      { signal },
    ),
  integrationHealth: (signal?: AbortSignal) =>
    request(
      '/ops/integrations',
      z.array(
        z.object({
          name: z.string(),
          status: z.string(),
          detail: z.string(),
          checked_at: z.string(),
          latency_ms: z.number().nullable(),
          last_success: z.string().nullable(),
          last_failure: z.string().nullable(),
        }),
      ),
      { signal },
    ),
  operationalAudit: (operation: string, cursor: string | null, signal?: AbortSignal) =>
    request(
      `/ops/audit?operation=${encodeURIComponent(operation)}&limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
      z.array(
        z.object({
          id: z.string(),
          operation: z.string(),
          entity_type: z.string(),
          entity_id: z.string(),
          created_at: z.string(),
          event_hash: z.string(),
        }),
      ),
      { signal },
    ),
  photoGatePolicy: (signal?: AbortSignal) =>
    request('/ops/photo-gate', photoGatePolicySchema, { signal }),
  savePhotoGatePolicy: (policy: z.infer<typeof photoGatePolicySchema>, signal?: AbortSignal) =>
    request('/ops/photo-gate', photoGatePolicySchema, {
      method: 'PUT',
      signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(policy),
    }),
  captureReview: (id: string, signal?: AbortSignal) =>
    request(
      `/captures/${id}/review`,
      z.object({
        id: z.string(),
        protocol_code: z.string(),
        user_description: z.string().nullable(),
        location: z
          .object({
            latitude: z.number(),
            longitude: z.number(),
            accuracy_m: z.number().nullable(),
          })
          .nullable(),
        location_source: z.string(),
        location_conflict: z.boolean(),
        photo_gate: z.record(z.string(), z.unknown()).nullable(),
        human_review: z.record(z.string(), z.unknown()).nullable(),
        additional_evidence: z.record(z.string(), z.unknown()).nullable().optional(),
        urban_auxiliary: z
          .object({
            status: z.string(),
            suggestions: z
              .array(z.object({ code: z.string(), probability: z.number() }))
              .optional(),
          })
          .passthrough()
          .nullable()
          .optional(),
        events: z.array(
          z.object({
            id: z.string(),
            public_id: z.string(),
            status: z.string(),
            origin: z.string(),
          }),
        ),
        reviews: z.array(
          z.object({
            id: z.string(),
            decision: z.string(),
            corrected_class: z.string().nullable(),
            notes: z.string().nullable(),
            created_at: z.string(),
          }),
        ),
      }),
      { signal },
    ),
  detachEvidence: (id: string) =>
    request(`/captures/${id}/detach-evidence`, z.object({ detached: z.boolean() }), {
      method: 'POST',
    }),
  nearbyReports: (latitude: number, longitude: number, signal?: AbortSignal, token?: string) =>
    request(
      `/captures/nearby-reports?latitude=${latitude}&longitude=${longitude}`,
      z.array(
        z.object({
          public_id: z.string().regex(/^[a-f0-9]{32}$/),
          distance_m: z.number().nonnegative(),
        }),
      ),
      { signal, token },
    ),
  reviewCapture: (id: string, payload: Record<string, unknown>) =>
    request(
      `/captures/${id}/reviews`,
      z.object({
        review_id: z.string(),
        event_id: z.string().nullable(),
        status: z.string(),
        ground_truth_status: z.string(),
      }),
      {
        method: 'POST',
        body: JSON.stringify(payload),
        headers: { 'Content-Type': 'application/json' },
      },
    ),
  publishEvent: (id: string, payload: Record<string, unknown>) =>
    request(
      `/events/${id}/publication`,
      z.object({ event_id: z.string(), publication_status: z.string() }),
      {
        method: 'POST',
        body: JSON.stringify(payload),
        headers: { 'Content-Type': 'application/json' },
      },
    ),
  captureByProtocol: (protocol: string, signal?: AbortSignal) =>
    request(
      `/captures/by-protocol/${encodeURIComponent(protocol)}`,
      z.object({ capture_id: z.string().uuid(), protocol_code: z.string() }),
      { signal },
    ),
  captureMarkers: (signal?: AbortSignal, onlyMine = false, cursor: string | null = null) =>
    request(
      `/captures/markers?include_unlocated=true&limit=100${onlyMine ? '&only_mine=true' : ''}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
      z.array(captureMarkerSchema),
      { signal },
    ),
  captureImage: (id: string, signal?: AbortSignal) =>
    request(`/captures/${id}/image`, z.object({ image_url: z.string() }), { signal }),
  captureTimeline: (id: string, signal?: AbortSignal) =>
    request(
      `/captures/${id}/timeline`,
      z.array(
        z.object({
          stage: z.string(),
          status: z.string(),
          at: z.string().nullable(),
          reasons: z.array(z.string()).optional(),
          source: z.string().optional(),
        }),
      ),
      { signal },
    ),
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
  events: (signal?: AbortSignal, cursor: string | null = null) =>
    request(
      `/events?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
      z.array(eventSchema),
      { signal },
    ),
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
    if (draft.privacy_version) form.append('privacy_version', draft.privacy_version);
    if (draft.additional_to) form.append('additional_to', draft.additional_to);
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
        if (draft.source_location === 'manual') form.append('manual_overrides_exif', 'true');
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
