import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import type { Session } from '@supabase/supabase-js';
import type { CaptureProcessing } from '../../domain/contracts';
import { api, PROTOCOL_NOT_FOUND, REPORT_NOT_FOUND } from '../../services/api';
import type { Route } from '../routes';

/**
 * Aviso no topo só para o que a página do relato não mostra sozinha. A situação em si
 * (recebido, em análise, confirmado, sem local) fica na trilha e na frase do card.
 */
const STAGE_NOTICES: Record<string, string> = {
  no_supported_detection:
    'A análise automática não reconheceu buraco nem trinca nesta foto. O relato foi preservado.',
  failed: 'A análise falhou. A foto continua preservada para verificação.',
};

/** Etapas em que a consulta periódica para: o relato já tem um destino. */
const FINAL_STAGES = [
  'completed',
  'no_supported_detection',
  'no_event',
  'needs_review',
  'model_not_available',
  'failed',
  'location_required',
];

interface ProcessingDeps {
  route: Route;
  session: Session | null;
  setRoute: Dispatch<SetStateAction<Route>>;
  setNotice: (text: string) => void;
  /** Pede a releitura dos relatos quando o relato chega a uma etapa final. */
  refresh: () => void;
}

/**
 * Acompanhamento do relato aberto em `#/processando/<id>` (ou `#/relato/<protocolo>`).
 * A Capture não tem política de leitura no Realtime: uma consulta a cada 5 s (uma por vez,
 * por até 2 minutos) acompanha só o relato aberto, sem assinar a tabela inteira.
 */
export function useCaptureProcessing({
  route,
  session,
  setRoute,
  setNotice,
  refresh,
}: ProcessingDeps) {
  const processingController = useRef<AbortController | null>(null);
  const [storedCaptureStatus, setCaptureStatus] = useState<CaptureProcessing | null>(null);
  const [storedProcessingError, setProcessingError] = useState('');
  const [processingScope, setProcessingScope] = useState('');
  const [processingRetry, setProcessingRetry] = useState(0);
  // O resultado só vale para a sessão e o relato que o pediram.
  const currentProcessingScope = session ? `${session.user.id}:${route.captureId ?? ''}` : '';
  const captureStatus =
    currentProcessingScope && processingScope === currentProcessingScope
      ? storedCaptureStatus
      : null;
  const processingError =
    currentProcessingScope && processingScope === currentProcessingScope
      ? storedProcessingError
      : '';

  // "Atualizar" e "Confirmar este local" consultam de novo o mesmo relato sem apagar a
  // tela; só outro relato (ou outra sessão) limpa o que estava aparecendo.
  const lastScope = useRef('');
  useEffect(() => {
    if (lastScope.current !== currentProcessingScope) setCaptureStatus(null);
    lastScope.current = currentProcessingScope;
    setProcessingError('');
    setProcessingScope(currentProcessingScope);
    if (route.page !== 'processing' || !route.captureId || !session) return;
    const controller = new AbortController();
    processingController.current = controller;
    let finished = false;
    // Uma consulta por vez: com a API lenta ou acordando (até 70 s), as consultas de 5 em
    // 5 s não se acumulam.
    let inFlight = false;
    // Limite em tempo, não em tentativas: com a API parada (até 70 s por consulta), o aviso
    // chega em uns 2 minutos, como com a API respondendo rápido.
    const startedAt = Date.now();
    const overdue = () => Date.now() - startedAt >= 120_000;
    async function refreshStatus() {
      if (controller.signal.aborted || finished || inFlight) return;
      inFlight = true;
      try {
        const result = await api.captureProcessing(route.captureId!, controller.signal);
        if (controller.signal.aborted || finished) return;
        setCaptureStatus(result);
        const notice = result.additional_evidence
          ? 'Foto anexada como evidência adicional ao relato que já existia nesse ponto. A equipe confere se é o mesmo problema.'
          : STAGE_NOTICES[result.status];
        if (notice) setNotice(notice);
        if (FINAL_STAGES.includes(result.status)) {
          finished = true;
          refresh();
        }
      } catch (error) {
        if (
          !controller.signal.aborted &&
          error instanceof Error &&
          error.message === REPORT_NOT_FOUND
        ) {
          setProcessingError(error.message);
          finished = true;
        }
        if (!controller.signal.aborted && overdue()) {
          setProcessingError(
            error instanceof Error
              ? error.message
              : 'Não foi possível consultar o relato. Toque em “Atualizar” para tentar de novo.',
          );
          finished = true;
        }
      } finally {
        inFlight = false;
      }
      if (overdue() && !finished) {
        finished = true;
        if (!controller.signal.aborted) {
          setProcessingError(
            'O relato ainda está sendo processado. Toque em “Atualizar” daqui a pouco.',
          );
        }
      }
    }
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 5_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.page, route.captureId, session, processingRetry]);

  // `#/relato/<protocolo>`: o protocolo leva ao relato desta sessão. Sem relato com esse
  // protocolo neste aparelho, a página mostra um aviso próprio (não um aviso de sucesso).
  const [notFound, setNotFound] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setNotFound('');
    if (route.protocol && session) {
      api
        .captureByProtocol(route.protocol, controller.signal)
        .then((report) => {
          if (!controller.signal.aborted)
            setRoute((current) => ({ ...current, captureId: report.capture_id }));
        })
        .catch((reason: Error) => {
          if (controller.signal.aborted) return;
          // Só o "não existe" vira a tela de não encontrado; falha de rede pede nova tentativa.
          if (reason.message === PROTOCOL_NOT_FOUND) setNotFound(route.protocol ?? '');
          else
            setProcessingError(
              'Não foi possível abrir o relato agora. Confira a conexão e tente de novo.',
            );
        });
    }
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.protocol, session]);

  return {
    status: captureStatus,
    error: processingError,
    /** Protocolo procurado e não encontrado neste aparelho (vazio quando não é o caso). */
    notFound,
    /** Muda a cada "Consultar novamente" ou localização confirmada. */
    retryCount: processingRetry,
    retry: () => setProcessingRetry((value) => value + 1),
    /** Cancela a consulta em andamento (troca de sessão, saída do app). */
    abort: () => processingController.current?.abort(),
    /** Esquece o resultado: ele era de outra sessão. */
    clear: () => {
      setCaptureStatus(null);
      setProcessingError('');
      setProcessingScope('');
    },
  };
}
export type ProcessingState = ReturnType<typeof useCaptureProcessing>;
