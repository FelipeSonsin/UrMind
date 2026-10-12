import { useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { api } from '../../services/api';

/**
 * Permissões da equipe, conferidas no servidor (`/me`) só nas páginas internas. O token
 * guardado junto evita usar a permissão de uma sessão anterior.
 */
export function useTeamAccess(session: Session | null, isVisitor: boolean, privatePage: boolean) {
  const [access, setAccess] = useState<{ token: string; review: boolean; admin: boolean } | null>(
    null,
  );
  const [accessError, setAccessError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setAccess(null);
    setAccessError('');
    if (session && !isVisitor && privatePage) {
      api
        .me(controller.signal)
        .then((me) => {
          if (!controller.signal.aborted)
            setAccess({ token: session.access_token, review: me.can_review, admin: me.can_admin });
        })
        .catch((error: Error) => {
          if (!controller.signal.aborted) setAccessError(error.message);
        });
    }
    return () => controller.abort();
  }, [session, isVisitor, privatePage]);
  const accessCurrent = Boolean(session && access?.token === session.access_token);
  const canReview = accessCurrent && access?.review === true && !isVisitor;
  const canAdmin = canReview && access?.admin === true;
  return { accessCurrent, accessError, canReview, canAdmin };
}
