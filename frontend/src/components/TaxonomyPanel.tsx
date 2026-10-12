import { useState } from 'react';
import { usePublicData } from '../app/hooks/usePublicData';
import { plain } from '../content/plainLanguage';
import {
  automaticClassCodes,
  issueMatchesSearch,
  modelSupportLabel,
  type IssueTaxonomy,
} from '../domain/public';
import { publicApi } from '../services/publicApi';
import { Skeleton } from './ui';

/**
 * Tipos de problema do catálogo. O que um modelo aprovado reconhece sozinho fica em
 * destaque; sem nenhum, a página pública não mostra o painel (o "Caminho de um relato"
 * já diz que não há análise automática). Só a equipe vê a lista completa, recolhida,
 * como relato com foto avaliado por pessoas, nunca como detecção automática.
 */
export function TaxonomyPanel({
  revision,
  includeDevelopment = false,
}: {
  revision: number;
  /** Só a área da equipe lista os tipos ainda sem detecção automática. */
  includeDevelopment?: boolean;
}) {
  const [search, setSearch] = useState('');
  const { data, error } = usePublicData<IssueTaxonomy>(
    (signal) => publicApi.taxonomy(signal),
    [revision],
  );
  if (error) return null;
  if (!data) return includeDevelopment ? <Skeleton lines={3} /> : null;
  const order = automaticClassCodes();
  const rank = (code: string) => (order.includes(code) ? order.indexOf(code) : order.length);
  const automatic = data.issues
    .filter((issue) => issue.model_may_emit)
    .sort((a, b) => rank(a.issue_code) - rank(b.issue_code));
  const others = data.issues.filter((issue) => !issue.model_may_emit);
  if (!automatic.length && !includeDevelopment) return null;
  const item = (issue: IssueTaxonomy['issues'][number]) => (
    <li key={issue.issue_code} data-support={issue.model_support_status}>
      <strong>{issue.display_name_pt}</strong>
      <span className="badge">{modelSupportLabel(issue)}</span>
      {issue.photo_review_block_reason && <small>{plain(issue.photo_review_block_reason)}</small>}
      {issue.limitations?.map((limitation) => (
        <small key={limitation}>{plain(limitation)}</small>
      ))}
    </li>
  );
  const searchField = (
    <label>
      Buscar tipo de problema
      <input
        type="search"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder="Ex.: buraco, rampa, fios, linhas de ônibus…"
      />
    </label>
  );
  // Área da equipe: consulta recolhida numa linha, no fim da fila.
  if (includeDevelopment)
    return (
      <section className="panel taxonomy-disclosure" aria-label="Tipos de problema">
        <details>
          <summary>Consultar os tipos de problema ({data.issues.length})</summary>
          {automatic.length > 0 && (
            <>
              <p className="muted">
                A análise automática reconhece só estes tipos; os outros são avaliados pela equipe.
              </p>
              <ul className="taxonomy-list">{automatic.map(item)}</ul>
            </>
          )}
          {searchField}
          <ul className="taxonomy-list">
            {others.filter((issue) => issueMatchesSearch(issue, search)).map(item)}
          </ul>
          {!others.some((issue) => issueMatchesSearch(issue, search)) && (
            <p>Nenhum tipo encontrado para esta busca.</p>
          )}
        </details>
      </section>
    );
  return (
    <section className="panel" aria-label="Tipos de problema">
      <h2>Tipos de problema</h2>
      <p className="muted">
        {automatic.length > 0
          ? 'A análise automática reconhece só os tipos abaixo. Os outros podem ser relatados com foto e são avaliados pela equipe.'
          : 'Nenhum tipo de problema é reconhecido automaticamente hoje: a equipe avalia todas as fotos.'}
      </p>
      {automatic.length > 0 && <ul className="taxonomy-list">{automatic.map(item)}</ul>}
      {includeDevelopment && others.length > 0 && (
        <details>
          <summary>
            {automatic.length > 0
              ? `Outros tipos, avaliados pela equipe (${others.length})`
              : `Todos os tipos do catálogo (${others.length})`}
          </summary>
          <label>
            Buscar tipo de problema
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Ex.: buraco, rampa, fios, linhas de ônibus…"
            />
          </label>
          <ul className="taxonomy-list">
            {others.filter((issue) => issueMatchesSearch(issue, search)).map(item)}
          </ul>
          {!others.some((issue) => issueMatchesSearch(issue, search)) && (
            <p>Nenhum tipo encontrado para esta busca.</p>
          )}
        </details>
      )}
    </section>
  );
}
