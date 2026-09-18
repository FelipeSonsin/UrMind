import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js';

// Só a publishable key vai ao navegador (MASTER_PLAN §6.2, §17). A secret key
// fica no backend; o token do usuário é validado pelo FastAPI via JWKS.
const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;

export const authConfigured = Boolean(url && publishableKey);

let client: SupabaseClient | null = null;
function supabase(): SupabaseClient {
  if (!authConfigured) throw new Error('Autenticação não configurada neste build.');
  client ??= createClient(url as string, publishableKey as string, {
    auth: { persistSession: true, autoRefreshToken: true },
  });
  return client;
}

export const auth = {
  async session(): Promise<Session | null> {
    if (!authConfigured) return null;
    const { data } = await supabase().auth.getSession();
    return data.session;
  },
  async accessToken(): Promise<string | null> {
    return (await this.session())?.access_token ?? null;
  },
  async signIn(email: string, password: string): Promise<void> {
    const { error } = await supabase().auth.signInWithPassword({ email, password });
    if (error) throw new Error('E-mail ou senha inválidos.');
  },
  async signOut(): Promise<void> {
    if (authConfigured) await supabase().auth.signOut();
  },
  onChange(callback: (session: Session | null) => void): () => void {
    if (!authConfigured) return () => undefined;
    const { data } = supabase().auth.onAuthStateChange((_event, session) => callback(session));
    return () => data.subscription.unsubscribe();
  },
};

export type RealtimeStatus = 'connected' | 'connecting' | 'unavailable';

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
  for (const table of ['events', 'risk_assessments'] as const) {
    channel.on('postgres_changes', { event: '*', schema: 'public', table }, (payload) => {
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
