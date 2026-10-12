import { useEffect, useRef, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { auth } from '../../services/auth';

interface SessionEvents {
  /** A cada troca de sessão (entrada, saída, outra aba), antes de gravar a nova. */
  onChange: (next: Session | null) => void;
  /** Ao desmontar o app: cancela o que ainda depende da sessão. */
  onDispose: () => void;
}

/**
 * Sessão Supabase do navegador. Visitante anônimo (`is_anonymous`) envia e acompanha os
 * próprios relatos, sem acesso de revisor; a área da equipe exige conta com papel.
 */
export function useSession(events: SessionEvents) {
  const [session, setSession] = useState<Session | null>(null);
  // Até a primeira resposta do Auth, "sem sessão" ainda não é verdade: a tela espera.
  const [ready, setReady] = useState(false);
  // Sempre os manipuladores da renderização mais recente, sem reassinar o Auth.
  const handlers = useRef(events);
  handlers.current = events;
  useEffect(() => {
    let changed = false;
    let disposed = false;
    void auth
      .session()
      .then((initial) => {
        if (!changed && !disposed) setSession(initial);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!disposed) setReady(true);
      });
    const unsubscribe = auth.onChange((nextSession) => {
      changed = true;
      handlers.current.onChange(nextSession);
      setSession(nextSession);
      setReady(true);
    });
    return () => {
      disposed = true;
      unsubscribe();
      handlers.current.onDispose();
    };
  }, []);
  const isVisitor = session?.user.is_anonymous === true;
  return { session, setSession, isVisitor, ready };
}
