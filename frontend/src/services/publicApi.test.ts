import { afterEach, describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { publicApi, withoutAutoRetry } from './publicApi';
import { auth } from './auth';
import { eventDetail } from '../../tests/fixtures';
import { api, requestJson } from './api';
import { oldestPendingRead } from './network';

const status = {
  api: { name: 'api', status: 'ok', detail: null },
  database: { name: 'database', status: 'ok', detail: null },
  detector: { name: 'detector', status: 'degraded', detail: 'modelo em estágio inicial' },
  scout: { name: 'scout', status: 'unavailable', detail: 'nenhum dispositivo registrado' },
  last_event_at: null,
  events_total: 0,
  road_segments_total: 832,
  pilot_area: 'Liberdade / FECAP',
  checked_at: '2026-09-17T12:00:00+00:00',
};

function respond(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const fetchMock = vi.fn(async (_url: string, _options?: RequestInit) => ({
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('cliente do painel público', () => {
  it('envia observação do rascunho sem descartá-la no upload', async () => {
    vi.spyOn(auth, 'accessToken').mockResolvedValue('owner-test-token');
    const fetchMock = respond({
      id: eventDetail.id,
      capture_key: 'fixture',
      created: true,
      requires_manual_location: false,
    });
    const note = 'Observação do cidadão, não validada pelo modelo.';
    await api.uploadPhoto({
      id: 'local-draft',
      photo: new Blob(['fixture']),
      filename: 'foto.jpg',
      source: 'exif_upload',
      captured_at: null,
      created_at: '2026-09-24T00:00:00Z',
      coordinate: { latitude: -23.5, longitude: -46.6, accuracy_m: null },
      source_location: 'manual',
      location_timestamp: null,
      heading_deg: null,
      speed_mps: null,
      note,
      status: 'local_draft',
    });
    expect((fetchMock.mock.calls[0][1]?.body as FormData).get('user_description')).toBe(note);
    expect((fetchMock.mock.calls[0][1]?.body as FormData).get('manual_overrides_exif')).toBe(
      'true',
    );
  });
  it('detalhe usa sessão existente do proprietário sem cache ou novo signup', async () => {
    vi.spyOn(auth, 'accessToken').mockResolvedValue('owner-test-token');
    const visitor = vi.spyOn(auth, 'ensureVisitorSession');
    const fetchMock = respond(eventDetail);
    await publicApi.event(eventDetail.id);
    expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({
      Authorization: 'Bearer owner-test-token',
    });
    expect(fetchMock.mock.calls[0][1]?.cache).toBe('no-store');
    expect(visitor).not.toHaveBeenCalled();
  });

  it('detalhe publicado sem sessão não envia credencial nem cria conta', async () => {
    vi.spyOn(auth, 'accessToken').mockResolvedValue(null);
    const visitor = vi.spyOn(auth, 'ensureVisitorSession');
    const fetchMock = respond(eventDetail);
    await publicApi.event(eventDetail.id);
    expect(JSON.stringify(fetchMock.mock.calls[0][1]?.headers)).not.toMatch(/authorization/i);
    expect(visitor).not.toHaveBeenCalled();
  });
  it('lê o estado do sistema sem enviar credencial', async () => {
    const fetchMock = respond(status);
    const parsed = await publicApi.status();
    expect(parsed.road_segments_total).toBe(832);
    expect(parsed.scout.status).toBe('unavailable');
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/public/status');
    expect(JSON.stringify(options?.headers)).not.toMatch(/authorization|apikey/i);
  });

  it('rejeita resposta que não corresponde ao contrato público', async () => {
    respond({ ...status, events_total: 'muitos' });
    await expect(publicApi.status()).rejects.toThrow(/jeito inesperado/);
  });

  it('propaga o motivo devolvido pela API em vez de inventar um', async () => {
    respond({ detail: 'ocorrência não encontrada' }, { ok: false, status: 404 });
    await expect(publicApi.event('3f8b9d3a-2f0c-4f1e-9b1a-4f6a0d5e7c11')).rejects.toThrow(
      'ocorrência não encontrada',
    );
  });

  it('traduz falha de rede para linguagem do painel', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed');
      }),
    );
    await expect(publicApi.status()).rejects.toThrow('Não foi possível conectar ao UrMind.');
  });

  it('envia limite, filtros e bbox como parâmetros de consulta', async () => {
    const fetchMock = respond([]);
    await publicApi.events({
      limit: 10,
      urmind_class: 'URMIND_ROAD_D40',
      status: 'triaged',
      bbox: { south: -23.6, west: -46.7, north: -23.5, east: -46.6 },
    });
    const url = new URL(fetchMock.mock.calls[0][0], 'https://urmind.local');
    expect(url.pathname).toBe('/api/v1/public/events');
    expect(url.searchParams.get('limit')).toBe('10');
    expect(url.searchParams.get('urmind_class')).toBe('URMIND_ROAD_D40');
    expect(url.searchParams.get('status')).toBe('triaged');
    expect(url.searchParams.get('north')).toBe('-23.5');
  });

  it('omite filtros vazios para não criar consulta sem sentido', async () => {
    const fetchMock = respond([]);
    await publicApi.events({ urmind_class: '', status: '' });
    const url = new URL(fetchMock.mock.calls[0][0], 'https://urmind.local');
    expect(url.searchParams.has('urmind_class')).toBe(false);
    expect(url.searchParams.has('status')).toBe(false);
    expect(url.searchParams.get('limit')).toBe('50');
  });

  it('respeita cancelamento do chamador', async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, options: RequestInit) => {
        options.signal?.throwIfAborted();
        return { ok: true, status: 200, json: async () => status };
      }),
    );
    controller.abort();
    await expect(publicApi.status(controller.signal)).rejects.toThrow();
  });
});

describe('espera pela API (que dorme quando fica parada)', () => {
  it('leitura sem resposta fica registrada até chegar, para a tela explicar a demora', async () => {
    let answer: (value: unknown) => void = () => undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((resolve) => {
            answer = resolve;
          }),
      ),
    );
    expect(oldestPendingRead()).toBeNull();
    const reading = publicApi.status();
    expect(oldestPendingRead()).toEqual(expect.any(Number));
    answer({ ok: true, status: 200, json: async () => status });
    await reading;
    expect(oldestPendingRead()).toBeNull();
  });

  it('envio da foto não conta como leitura demorada: ele tem o próprio progresso', async () => {
    vi.spyOn(auth, 'accessToken').mockResolvedValue('owner-test-token');
    let answer: (value: unknown) => void = () => undefined;
    const fetchMock = vi.fn(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const sending = api.uploadPhoto({
      id: 'local-draft',
      photo: new Blob(['fixture']),
      filename: 'foto.jpg',
      source: 'exif_upload',
      captured_at: null,
      created_at: '2026-09-24T00:00:00Z',
      coordinate: { latitude: -23.5, longitude: -46.6, accuracy_m: null },
      source_location: 'manual',
      location_timestamp: null,
      heading_deg: null,
      speed_mps: null,
      note: '',
      status: 'local_draft',
    });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(oldestPendingRead()).toBeNull();
    answer({
      ok: true,
      status: 200,
      json: async () => ({
        id: eventDetail.id,
        capture_key: 'fixture',
        created: true,
        requires_manual_location: false,
      }),
    });
    await sending;
  });

  it('o tempo limite padrão cobre o despertar da API, perto de um minuto', async () => {
    const timeout = vi.spyOn(AbortSignal, 'timeout');
    respond(status);
    await publicApi.status();
    expect(timeout).toHaveBeenCalledWith(70_000);
  });

  it('a leitura sai do registro também com servidor fora do ar, cancelamento e tempo esgotado', async () => {
    // Servidor fora do ar: frase simples, sem código, e nada pendente.
    respond({ detail: 'erro interno' }, { ok: false, status: 502 });
    await expect(publicApi.status()).rejects.toThrow(
      'O UrMind está fora do ar agora. A página tenta de novo sozinha em instantes.',
    );
    expect(oldestPendingRead()).toBeNull();

    // Uma resposta que nunca chega: só termina quando o sinal é cancelado.
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            options.signal?.addEventListener('abort', () => reject(options.signal?.reason));
          }),
      ),
    );
    const controller = new AbortController();
    const cancelled = publicApi.status(controller.signal);
    expect(oldestPendingRead()).not.toBeNull();
    controller.abort();
    await expect(cancelled).rejects.toThrow();
    expect(oldestPendingRead()).toBeNull();

    const expired = requestJson('/api/v1/public/status', z.object({}), {
      timeoutMs: 20,
      messages: { network: 'sem rede', timeout: 'tempo esgotado', contract: 'inesperado' },
    });
    await expect(expired).rejects.toThrow('tempo esgotado');
    expect(oldestPendingRead()).toBeNull();
  });

  it('na área da equipe, que não relê sozinha, a falha não promete nova tentativa', async () => {
    respond({ detail: 'erro interno' }, { ok: false, status: 503 });
    const failure = await publicApi.taxonomy().then(
      () => '',
      (reason: Error) => reason.message,
    );
    expect(failure).toContain('A página tenta de novo sozinha em instantes.');
    expect(withoutAutoRetry(failure)).toBe(
      'O UrMind está fora do ar agora. Tente de novo em instantes.',
    );
  });
});
