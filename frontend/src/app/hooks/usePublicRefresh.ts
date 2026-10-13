import { useEffect, useState } from 'react';
import { registerTaxonomy } from '../../domain/public';
import { oldestPendingRead } from '../../services/network';
import { publicApi } from '../../services/publicApi';

/** Releitura do painel público: curta o bastante para parecer vivo, longa o bastante
 * para não pesar na API. */
const PUBLIC_REFRESH_MS = 30_000;

/**
 * Versão dos dados públicos. O Realtime do Supabase exige sessão (as políticas de
 * SELECT valem para `authenticated`), então o visitante público não recebe aviso de
 * mudança: uma releitura periódica cobre isso, pausada com a aba oculta. Quem tem
 * sessão acompanha na hora, porque cada `revision` nova também renova esta.
 */
export function usePublicRefresh(revision: number) {
  const [publicRevision, setPublicRevision] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => {
      // A releitura nunca cancela uma leitura ainda sem resposta: com a API lenta ou parada,
      // ela chega ao tempo limite e a tela mostra o erro, em vez de recomeçar a cada 30 s.
      if (document.visibilityState === 'visible' && oldestPendingRead() === null)
        setPublicRevision((value) => value + 1);
    }, PUBLIC_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => setPublicRevision((value) => value + 1), [revision]);
  return [publicRevision, () => setPublicRevision((value) => value + 1)] as const;
}

/**
 * Nomes das classes vindos da taxonomia canônica; os rótulos estáticos ficam só como
 * reserva sem rede. A versão guardada faz a tela reler os nomes quando chegam.
 */
export function useTaxonomyLabels() {
  const [version, setVersion] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    publicApi
      .taxonomy(controller.signal)
      .then((taxonomy) => {
        registerTaxonomy(taxonomy);
        setVersion(taxonomy.taxonomy_version);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  return version;
}
