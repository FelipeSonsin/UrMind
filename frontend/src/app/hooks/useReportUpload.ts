import { useRef, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { api, UnauthorizedError } from '../../services/api';
import { auth } from '../../services/auth';
import { drafts, type CaptureDraft } from '../../services/drafts';
import type { Route } from '../routes';

interface UploadDeps {
  session: Session | null;
  setSession: (session: Session | null) => void;
  setRoute: (route: Route) => void;
  setNotice: (text: string) => void;
  setDraftError: (text: string) => void;
  reloadDrafts: () => Promise<void>;
  /** Pede a releitura dos relatos depois do envio. */
  refresh: () => void;
}

/**
 * Envio de um rascunho: garante a sessão de visitante, pergunta se é o mesmo problema
 * de um relato vizinho, envia a foto e abre o acompanhamento. Um envio só vale para a
 * sessão que o começou; trocar de sessão cancela.
 */
export function useReportUpload(deps: UploadDeps) {
  const { session, setSession, setRoute, setNotice, setDraftError, reloadDrafts, refresh } = deps;
  const [sending, setSending] = useState('');
  const uploadOperation = useRef<{ controller: AbortController; ownerId: string | null } | null>(
    null,
  );

  async function send(draft: CaptureDraft) {
    uploadOperation.current?.controller.abort();
    const operation = { controller: new AbortController(), ownerId: session?.user.id ?? null };
    uploadOperation.current = operation;
    const isCurrent = () =>
      uploadOperation.current === operation && !operation.controller.signal.aborted;
    setSending(draft.id);
    setDraftError('');
    try {
      // Supabase anonymous Auth provides an owner-scoped JWT without giving a
      // visitor reviewer/admin privileges. The browser never receives a secret key.
      const active = await auth.ensureVisitorSession(
        operation.controller.signal,
        operation.ownerId,
        (id) => {
          if (isCurrent()) operation.ownerId = id;
        },
      );
      if (!isCurrent()) return;
      operation.ownerId = active.user.id;
      if (session?.access_token !== active.access_token) setSession(active);
      let additionalTo: string | undefined;
      if (draft.coordinate) {
        const nearby = await api.nearbyReports(
          draft.coordinate.latitude,
          draft.coordinate.longitude,
          operation.controller.signal,
          active.access_token,
        );
        if (!isCurrent()) return;
        if (
          nearby.length &&
          window.confirm(
            `Já existe um relato a aproximadamente ${nearby[0].distance_m} m. É o mesmo problema? Confirmar anexa sua foto como evidência, sem criar outro ponto.`,
          )
        ) {
          additionalTo = nearby[0].public_id;
        }
      }
      if (!isCurrent()) return;
      const result = await api.uploadPhoto(
        { ...draft, additional_to: additionalTo },
        operation.controller.signal,
        active.access_token,
      );
      if (!isCurrent()) return;
      location.hash = `#/processando/${result.id}`;
      setRoute({ page: 'processing', captureId: result.id });
      await drafts.remove(draft.id);
      if (!isCurrent()) return;
      await reloadDrafts();
      if (!isCurrent()) return;
      setNotice(
        result.additional_evidence
          ? 'Foto anexada como evidência adicional ao relato que já existia nesse ponto. A equipe confere se é o mesmo problema.'
          : result.requires_manual_location
            ? 'Foto enviada. Sem localização: marque o ponto antes que ela vire ocorrência.'
            : result.created
              ? 'Foto enviada e registrada. O ponto já aparece no seu mapa; a análise segue abaixo.'
              : 'Esta foto já estava registrada; nada foi duplicado.',
      );
      refresh();
    } catch (reason) {
      if (!isCurrent()) return;
      if (reason instanceof UnauthorizedError) setSession(null);
      setDraftError(
        `${(reason as Error).message} O rascunho continua salvo neste navegador para reenviar.`,
      );
    } finally {
      if (isCurrent()) {
        uploadOperation.current = null;
        setSending('');
      }
    }
  }

  /** Troca de sessão: o envio de outra pessoa (ou sem sessão) é cancelado. */
  function releaseFor(next: Session | null) {
    const upload = uploadOperation.current;
    if (upload && (!next || upload.ownerId !== next.user.id)) {
      upload.controller.abort();
      uploadOperation.current = null;
      setSending('');
    }
  }

  function abort() {
    uploadOperation.current?.controller.abort();
  }

  return { sending, send, releaseFor, abort };
}
