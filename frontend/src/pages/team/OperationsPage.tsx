import { useEffect, useRef, useState } from 'react';
import { PageHeader } from '../../components/ui';
import { api } from '../../services/api';

type Policy = Awaited<ReturnType<typeof api.photoGatePolicy>>;
type NumericField = Exclude<keyof Policy, 'public_capture_markers_enabled'>;

/**
 * Regras da checagem automática das fotos, em grupos. Cada dica diz o efeito de mudar
 * o número, sem o nome do algoritmo (os nomes técnicos ficam nas chaves da API). Os
 * grupos `advanced` ficam recolhidos; `percent` mostra em % o que a API guarda de 0 a 1.
 */
const GROUPS: Array<{
  title: string;
  advanced?: boolean;
  note?: string;
  /** Duas colunas: quatro campos em 2×2, sem um sozinho na última linha. */
  pairs?: boolean;
  /** `percent`: mostra em % o que a API guarda de 0 a `scale` (1, se omitido). */
  fields: Array<{
    key: NumericField;
    label: string;
    hint?: string;
    percent?: boolean;
    scale?: number;
  }>;
}> = [
  {
    title: 'Qualidade da foto',
    pairs: true,
    fields: [
      {
        key: 'min_side',
        label: 'Tamanho mínimo da foto',
        hint: 'Medido no lado menor. Fotos de celular costumam passar de 2.000. Padrão: 640.',
      },
      {
        key: 'laplacian_min',
        label: 'Nitidez mínima',
        hint: 'Quanto maior, mais nítida a foto precisa ser. Padrão: 25.',
      },
      {
        key: 'brightness_min',
        label: 'Luz mínima (%)',
        hint: 'Abaixo disso, a foto está escura demais. Padrão: 8%.',
        percent: true,
        scale: 255,
      },
      {
        key: 'brightness_max',
        label: 'Luz máxima (%)',
        hint: 'Acima disso, a foto está clara demais. Padrão: 94%.',
        percent: true,
        scale: 255,
      },
    ],
  },
  {
    title: 'Fotos repetidas e antigas',
    advanced: true,
    fields: [
      {
        key: 'phash_distance',
        label: 'Diferença máxima entre fotos repetidas',
        hint: 'Quanto menor, mais parecidas duas fotos precisam ser para contar como a mesma. Padrão: 6.',
      },
      {
        key: 'old_photo_days',
        label: 'Avisar quando a foto tiver mais de (dias)',
        hint: 'Conta a partir da data em que a foto foi tirada. Padrão: 30.',
      },
    ],
  },
  {
    title: 'Cena e privacidade',
    advanced: true,
    note: 'Enquanto essas margens não forem ajustadas com fotos reais, a foto vai para a revisão da equipe. A checagem de rostos é feita pelo UrMind ao receber a foto. Passar na checagem não confirma um problema.',
    fields: [
      {
        key: 'scene_accept_margin',
        label: 'Margem para aceitar a cena',
        hint: 'Quanto maior, mais certeza de que a foto mostra a rua. Padrão: 0,02.',
      },
      {
        key: 'scene_reject_margin',
        label: 'Margem para recusar a cena',
        hint: 'Quanto menor, mais certeza antes de recusar. Padrão: -0,02.',
      },
      {
        key: 'dominant_face_ratio',
        label: 'Maior parte da foto que um rosto pode ocupar (%)',
        hint: 'Por exemplo, 15 quer dizer 15% da foto. Padrão: 15%.',
        percent: true,
      },
    ],
  },
];

/** Alteração não salva guardada nesta aba (sessionStorage), para não se perder ao navegar. */
const DRAFT_KEY = 'urmind-administracao-nao-salvo';
function keepDraft(value: string) {
  try {
    sessionStorage.setItem(DRAFT_KEY, value);
  } catch {
    // Sem armazenamento da aba: a alteração se perde, como em qualquer formulário.
  }
}
/** Lê e apaga a alteração guardada (ou `null`, se não houver). */
function takeDraft(): Policy | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    sessionStorage.removeItem(DRAFT_KEY);
    return raw ? (JSON.parse(raw) as Policy) : null;
  } catch {
    return null;
  }
}

/** Valor em % (a API guarda de 0 a 1), sem as casas que a conta em ponto flutuante cria. */
const asPercent = (ratio: number) => Number((ratio * 100).toFixed(ratio * 100 >= 5 ? 0 : 2));

/**
 * Administração: as regras da checagem das fotos, salvas com registro na auditoria.
 * Com alteração não salva, a barra de salvar acompanha a rolagem e o navegador pergunta
 * antes de fechar a aba.
 */
export function OperationsPage() {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [stored, setStored] = useState<Policy | null>(null);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  // Salvar sem mudança não grava nada (nem registro na auditoria): só avisa.
  const [unchanged, setUnchanged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState('');
  // Alteração não salva de uma visita anterior a esta página, recuperada ao voltar.
  const [restored, setRestored] = useState(false);
  const saveController = useRef<AbortController | null>(null);
  const dirty = Boolean(policy && stored && JSON.stringify(policy) !== JSON.stringify(stored));
  // Ir para outra página da equipe não perde a alteração: ela fica guardada nesta aba.
  const latest = useRef({ policy, dirty });
  latest.current = { policy, dirty };
  useEffect(
    () => () => {
      if (latest.current.dirty && latest.current.policy)
        keepDraft(JSON.stringify(latest.current.policy));
    },
    [],
  );
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    addEventListener('beforeunload', warn);
    return () => removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    const controller = new AbortController();
    void api
      .photoGatePolicy(controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        const draft = takeDraft();
        setPolicy(draft ?? value);
        setStored(value);
        setRestored(Boolean(draft));
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => {
      controller.abort();
      saveController.current?.abort();
    };
  }, []);
  /** Um grupo de regras: rótulo, campo e dica alinhados entre as colunas. */
  function renderGroup(group: (typeof GROUPS)[number]) {
    if (!policy) return null;
    return (
      <fieldset key={group.title} className="panel">
        <legend>{group.title}</legend>
        <div className={`settings-grid${group.pairs ? ' is-pairs' : ''}`}>
          {group.fields.map(({ key, label, hint, percent, scale = 1 }) => (
            <label key={key}>
              <span>{label}</span>
              <input
                type="number"
                required
                step="any"
                inputMode="decimal"
                value={
                  policy[key] == null || Number.isNaN(policy[key])
                    ? ''
                    : percent
                      ? asPercent(policy[key] / scale)
                      : policy[key]
                }
                onChange={(event) => {
                  // Campo apagado fica vazio (e o formulário pede o número), não vira 0.
                  const raw = event.target.value;
                  const value = raw === '' ? Number.NaN : Number(raw);
                  setSaved(false);
                  setPolicy({ ...policy, [key]: percent ? (value / 100) * scale : value });
                }}
              />
              {hint && <small>{hint}</small>}
            </label>
          ))}
        </div>
        {group.note && <p className="muted">{group.note}</p>}
      </fieldset>
    );
  }

  return (
    <>
      <PageHeader
        title="Administração"
        description="Regras da checagem automática das fotos. Cada alteração fica registrada na auditoria."
      />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!policy && !error && (
        <p role="status" className="loading-line">
          Carregando configuração…
        </p>
      )}
      {policy && (
        <form
          className="settings-form"
          onSubmit={(event) => {
            event.preventDefault();
            setSaved(false);
            setUnchanged(!dirty);
            if (!dirty) return;
            setBusy(true);
            setSaveError('');
            saveController.current?.abort();
            const controller = new AbortController();
            saveController.current = controller;
            void api
              .savePhotoGatePolicy(policy, controller.signal)
              .then((value) => {
                if (controller.signal.aborted) return;
                setPolicy(value);
                setStored(value);
                setSaved(true);
                setRestored(false);
                takeDraft();
              })
              .catch((failure: Error) => {
                if (!controller.signal.aborted) setSaveError(failure.message);
              })
              .finally(() => {
                if (!controller.signal.aborted) setBusy(false);
              });
          }}
        >
          {GROUPS.filter((group) => !group.advanced).map(renderGroup)}
          <fieldset className="panel">
            <legend>Mapa público</legend>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={policy.public_capture_markers_enabled}
                onChange={(event) => {
                  setSaved(false);
                  setPolicy({ ...policy, public_capture_markers_enabled: event.target.checked });
                }}
              />
              Mostrar no mapa público os relatos ainda em análise, sem foto nem descrição
            </label>
          </fieldset>
          {/* Regras que quase nunca mudam: recolhidas, para não competir com as de uso diário. */}
          <details className="settings-advanced">
            <summary>Regras avançadas</summary>
            {GROUPS.filter((group) => group.advanced).map(renderGroup)}
          </details>
          <div className={`actions settings-actions${dirty ? ' is-dirty' : ''}`}>
            {saveError && (
              <p className="error" role="alert">
                {saveError}
              </p>
            )}
            <button disabled={busy} type="submit">
              {busy ? 'Salvando…' : 'Salvar configuração'}
            </button>
            {dirty && (
              <button
                type="button"
                className="ghost"
                onClick={() => {
                  setPolicy(stored);
                  setRestored(false);
                  takeDraft();
                }}
              >
                Descartar
              </button>
            )}
            <p role="status" className="muted">
              {dirty
                ? restored
                  ? 'Suas alterações não salvas foram mantidas.'
                  : 'Há alterações não salvas.'
                : saved
                  ? 'Configuração salva e registrada na auditoria.'
                  : unchanged
                    ? 'Nenhuma alteração para salvar.'
                    : ''}
            </p>
          </div>
        </form>
      )}
    </>
  );
}

/** Nome de cada operação registrada na auditoria (`audit_log.operation`). */
const OPERATIONS: Record<string, string> = {
  photo_gate_configuration: 'Regras da checagem de fotos alteradas',
  photo_gate_rejected: 'Foto recusada na checagem',
  review: 'Revisão registrada',
  capture_review_state: 'Situação da revisão alterada',
  capture_location: 'Localização de relato informada',
  publish_event: 'Publicação no mapa',
  auto_publish_marker: 'Ponto genérico publicado no mapa',
  attach_report_evidence: 'Foto anexada a um relato',
  detach_report_evidence: 'Foto desvinculada de um relato',
  capture_inference: 'Análise automática de foto',
  history_snapshot: 'Histórico registrado',
  integration_health_check: 'Serviços externos verificados',
};

const ENTITIES: Record<string, string> = {
  operational_configuration: 'configuração',
  capture: 'relato',
  event: 'ocorrência',
};

/** Auditoria: cada mudança registrada, sem identidades nem dados pessoais. */
export function OperationalRegistryPage() {
  const [audit, setAudit] = useState<Awaited<ReturnType<typeof api.operationalAudit>> | null>(null);
  const [operation, setOperation] = useState('');
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setAudit(null);
    setError('');
    void api
      .operationalAudit(operation, cursors.at(-1) ?? null, controller.signal)
      .then((rows) => {
        if (!controller.signal.aborted) setAudit(rows);
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => controller.abort();
  }, [operation, cursors]);
  return (
    <>
      <PageHeader
        title="Auditoria"
        description="Cada mudança feita no UrMind fica registrada aqui. Nomes e dados pessoais não aparecem."
      />
      <section className="panel">
        <div className="filters is-single">
          <div>
            <label htmlFor="audit-operation">Tipo de registro</label>
            <select
              id="audit-operation"
              value={operation}
              onChange={(event) => {
                setOperation(event.target.value);
                setCursors([null]);
              }}
            >
              <option value="">Todos</option>
              {Object.entries(OPERATIONS).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {!error && !audit && <p role="status">Carregando registros…</p>}
        {audit && (
          <>
            {audit.length === 0 && <p className="muted">Nenhum registro neste filtro.</p>}
            <ul className="audit-list">
              {/* Só a operação em palavras; código interno e hash ficam no banco, fora da tela. */}
              {audit.map((row) => (
                <li key={row.id}>
                  <strong>{OPERATIONS[row.operation] ?? 'Outra operação'}</strong>
                  <small>
                    {(ENTITIES[row.entity_type] ?? 'registro').replace(/^./, (first) =>
                      first.toUpperCase(),
                    )}
                    {', '}
                    <time dateTime={row.created_at}>
                      {new Date(row.created_at).toLocaleString('pt-BR', {
                        dateStyle: 'short',
                        timeStyle: 'short',
                      })}
                    </time>
                  </small>
                </li>
              ))}
            </ul>
            {/* Uma página só: sem "Anterior" e "Próxima" desligados. */}
            {(cursors.length > 1 || audit.length >= 50) && (
              <nav className="actions pager" aria-label="Páginas da auditoria">
                <button
                  type="button"
                  className="secondary"
                  disabled={cursors.length === 1}
                  onClick={() => setCursors(cursors.slice(0, -1))}
                >
                  Anterior
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={audit.length < 50}
                  onClick={() => {
                    const last = audit.at(-1)!;
                    setCursors([...cursors, `${last.created_at}|${last.id}`]);
                  }}
                >
                  Próxima
                </button>
                <span className="muted">Página {cursors.length}</span>
              </nav>
            )}
          </>
        )}
      </section>
    </>
  );
}
