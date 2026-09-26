import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js';
import { currentSurface } from '../surface';

// Só a publishable key vai ao navegador (MASTER_PLAN §6.2, §17). A secret key
// fica no backend; o token do usuário é validado pelo FastAPI via JWKS.
const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;

export const authConfigured = Boolean(url && publishableKey);

let client: SupabaseClient | null = null;
interface VisitorOperation {
  controller: AbortController;
  signal: AbortSignal;
  ownerId: string | null;
  onCreated?: (id: string) => void;
}
let visitorOperation: VisitorOperation | null = null;
// Keep each candidate tied to its original operation until the SDK settles.
const signupCandidates = new Map<string, VisitorOperation>();
// A área da equipe guarda a sessão numa chave própria: entrar em /admin/ não loga o
// site do cidadão, e a sessão de visitante do site não chega à área da equipe.
const sessionKey = url
  ? `sb-${new URL(url).hostname.split('.')[0]}-${currentSurface() === 'admin' ? 'admin-' : ''}auth-token`
  : '';

function storedOwner(): string | null {
  const stored = localStorage.getItem(sessionKey);
  if (!stored) return null;
  return (JSON.parse(stored) as Session).user?.id ?? null;
}

function checkVisitor(operation: VisitorOperation) {
  operation.signal.throwIfAborted();
  if (visitorOperation !== operation || storedOwner() !== operation.ownerId)
    throw new DOMException('A sessão mudou. Envie novamente.', 'AbortError');
}

function supabase(): SupabaseClient {
  if (!authConfigured) throw new Error('Autenticação não configurada neste build.');
  if (client) return client;
  client = createClient(url as string, publishableKey as string, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      storageKey: sessionKey,
      storage: {
        getItem: (key) => localStorage.getItem(key),
        removeItem: (key) => localStorage.removeItem(key),
        setItem: (key, value) => {
          const session = key === sessionKey ? (JSON.parse(value) as Session) : null;
          const candidate = session && signupCandidates.get(session.access_token);
          if (candidate) {
            // The SDK awaits response parsing before this write. Fetch cancellation
            // alone cannot protect this final persistence boundary.
            checkVisitor(candidate);
            candidate.ownerId = session.user.id;
            candidate.onCreated?.(session.user.id);
          }
          localStorage.setItem(key, value);
        },
      },
    },
    global: {
      fetch: async (input, init) => {
        const endpoint =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (
          endpoint !== `${new URL(url as string).origin}/auth/v1/signup` ||
          init?.method !== 'POST'
        )
          return fetch(input, init);
        const operation = visitorOperation;
        if (!operation) throw new DOMException('Signup sem operação ativa.', 'AbortError');
        checkVisitor(operation);
        const response = await fetch(input, { ...init, signal: operation.signal });
        if (response.ok) {
          const candidate = (await response.clone().json()) as {
            access_token?: string;
            user?: { id?: string; is_anonymous?: boolean };
          };
          checkVisitor(operation);
          if (
            !candidate.access_token ||
            !candidate.user?.id ||
            candidate.user.is_anonymous !== true
          )
            throw new Error('Resposta de sessão anônima inválida.');
          signupCandidates.set(candidate.access_token, operation);
        }
        return response;
      },
    },
  });
  client.auth.onAuthStateChange((event, session) => {
    const operation = visitorOperation;
    if (
      operation &&
      event !== 'INITIAL_SESSION' &&
      (event === 'SIGNED_OUT' || session?.user.id !== operation.ownerId)
    )
      operation.controller.abort();
  });
  return client;
}

export const auth = {
  async session(): Promise<Session | null> {
    // This PWA has no server-side session store; Node/build callers are anonymous.
    if (!authConfigured || typeof localStorage === 'undefined') return null;
    const { data } = await supabase().auth.getSession();
    return data.session;
  },
  async accessToken(): Promise<string | null> {
    return (await this.session())?.access_token ?? null;
  },
  async ensureVisitorSession(
    signal?: AbortSignal,
    expectedOwnerId?: string | null,
    onCreated?: (id: string) => void,
  ): Promise<Session> {
    signal?.throwIfAborted();
    const initial = await this.session();
    if (expectedOwnerId !== undefined && (initial?.user.id ?? null) !== expectedOwnerId)
      throw new DOMException('A sessão mudou. Envie novamente.', 'AbortError');
    visitorOperation?.controller.abort();
    const controller = new AbortController();
    const operation: VisitorOperation = {
      controller,
      signal: signal ? AbortSignal.any([signal, controller.signal]) : controller.signal,
      ownerId: initial?.user.id ?? null,
      onCreated,
    };
    visitorOperation = operation;
    signal = operation.signal;
    try {
      // Fail closed if a stale frontend build points at MAIN while the API is DEV
      // (or vice versa). The publishable origin is not a secret.
      const apiBase = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');
      let origin: string;
      let visitorUploadEnabled = false;
      try {
        const response = await fetch(`${apiBase}/public/auth-origin`, {
          cache: 'no-store',
          signal: signal
            ? AbortSignal.any([signal, AbortSignal.timeout(8000)])
            : AbortSignal.timeout(8000),
        });
        if (!response.ok) throw new Error('Auth origin unavailable');
        const payload = (await response.json()) as {
          auth_origin?: string;
          visitor_upload_enabled?: boolean;
        };
        origin = new URL(payload.auth_origin ?? '').origin;
        visitorUploadEnabled = payload.visitor_upload_enabled === true;
      } catch {
        throw new Error('Registro público indisponível: projeto de autenticação não confirmado.');
      }
      if (origin !== new URL(url as string).origin)
        throw new Error(
          'Registro público bloqueado: frontend e backend apontam para projetos diferentes.',
        );
      signal?.throwIfAborted();
      const existing = await this.session();
      checkVisitor(operation);
      if ((existing?.user.id ?? null) !== operation.ownerId)
        throw new DOMException('A sessão mudou. Envie novamente.', 'AbortError');
      if (!visitorUploadEnabled && (!existing || existing.user.is_anonymous))
        throw new Error(
          'Registro público temporariamente indisponível. Entre com uma conta autorizada.',
        );
      if (existing) return existing;
      const { data, error } = await supabase().auth.signInAnonymously();
      signal?.throwIfAborted();
      if (error || !data.session) {
        throw new Error('Registro público indisponível: não foi possível criar uma sessão segura.');
      }
      return data.session;
    } finally {
      for (const [token, candidate] of signupCandidates)
        if (candidate === operation) signupCandidates.delete(token);
      if (visitorOperation === operation) visitorOperation = null;
    }
  },
  async signIn(email: string, password: string): Promise<void> {
    visitorOperation?.controller.abort();
    const { error } = await supabase().auth.signInWithPassword({ email, password });
    if (error) throw new Error('E-mail ou senha inválidos.');
  },
  async signOut(): Promise<void> {
    visitorOperation?.controller.abort();
    if (authConfigured) await supabase().auth.signOut();
  },
  onChange(callback: (session: Session | null) => void): () => void {
    if (!authConfigured) return () => undefined;
    const { data } = supabase().auth.onAuthStateChange((_event, session) => callback(session));
    return () => data.subscription.unsubscribe();
  },
};

export type RealtimeStatus = 'connected' | 'connecting' | 'unavailable';

export interface BroadcastLink {
  send(payload: Record<string, unknown>): Promise<boolean>;
  close(): Promise<void>;
}

/**
 * Canal efêmero de broadcast no mesmo projeto Supabase: só mensagens pequenas entre
 * aparelhos, sem banco e sem histórico. Quem conhece o nome do canal pode entrar;
 * o conteúdo precisa carregar a própria prova (ex.: token do pareamento).
 */
export function openBroadcastChannel(
  name: string,
  event: string,
  onMessage: (payload: unknown) => void,
  onStatus: (status: RealtimeStatus) => void,
): BroadcastLink {
  if (!authConfigured) {
    onStatus('unavailable');
    return { send: async () => false, close: async () => undefined };
  }
  const channel = supabase().channel(name, {
    config: { broadcast: { self: false, ack: true } },
  });
  channel.on('broadcast', { event }, ({ payload }) => onMessage(payload));
  onStatus('connecting');
  channel.subscribe((status) =>
    onStatus(
      status === 'SUBSCRIBED'
        ? 'connected'
        : status === 'CLOSED' || status === 'CHANNEL_ERROR' || status === 'TIMED_OUT'
          ? 'unavailable'
          : 'connecting',
    ),
  );
  return {
    send: async (payload) => (await channel.send({ type: 'broadcast', event, payload })) === 'ok',
    close: async () => {
      await supabase().removeChannel(channel);
    },
  };
}

// Postgres Changes (MASTER_PLAN §16.1): só `events` e `risk_assessments`. A mudança
// não carrega o dado para a tela; ela só avisa para recarregar via HTTP, o que evita
// evento duplicado e mantém o HTTP como caminho oficial quando o Realtime cair.
export function subscribeToEventChanges(
  onChange: (change: { table: string; eventId: string | null }) => void,
  onStatus: (status: RealtimeStatus) => void,
): () => void {
  if (!authConfigured) {
    onStatus('unavailable');
    return () => undefined;
  }
  const channel = supabase().channel('urmind-events');
  for (const table of ['events', 'risk_assessments', 'captures'] as const) {
    // DELETE payloads do not carry row-level visibility. Capture markers only
    // consume INSERT/UPDATE notifications and re-fetch through the owner API.
    const operations = table === 'captures' ? (['INSERT', 'UPDATE'] as const) : (['*'] as const);
    for (const event of operations)
      channel.on('postgres_changes', { event, schema: 'public', table }, (payload) => {
        const row = (payload.new ?? payload.old) as Record<string, unknown> | undefined;
        const eventId = table === 'events' ? row?.id : row?.event_id;
        onChange({ table, eventId: typeof eventId === 'string' ? eventId : null });
      });
  }
  onStatus('connecting');
  channel.subscribe((status) => {
    onStatus(
      status === 'SUBSCRIBED'
        ? 'connected'
        : status === 'CLOSED' || status === 'CHANNEL_ERROR' || status === 'TIMED_OUT'
          ? 'unavailable'
          : 'connecting',
    );
  });
  return () => {
    void supabase().removeChannel(channel);
  };
}
