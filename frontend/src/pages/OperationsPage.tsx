import { useEffect, useRef, useState } from 'react';
import { api } from '../services/api';

type Policy = Awaited<ReturnType<typeof api.photoGatePolicy>>;

export function GroundTruthPage() {
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [data, setData] = useState<Awaited<ReturnType<typeof api.groundTruth>> | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError('');
    void api
      .groundTruth(controller.signal, cursors.at(-1) ?? null)
      .then((rows) => {
        if (!controller.signal.aborted) setData(rows);
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => controller.abort();
  }, [cursors]);
  return (
    <section className="panel">
      <h1>Ground truth</h1>
      <p>
        Consenso e adjudicação não substituem o snapshot anterior à revisão. Exportar não autoriza
        treinamento.
      </p>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Consultando elegibilidade…</p>}
      {data && (
        <>
          <h2>Rótulos por classe</h2>
          <p>Contagens e exportação referentes a esta página; cada Event mantém todos os votos.</p>
          <ul>
            {Object.entries(data.counts_by_class).map(([issue, count]) => (
              <li key={issue}>
                {issue}: {count}
              </li>
            ))}
          </ul>
          {data.entries.length === 0 && <p>Nenhuma revisão de ocorrência disponível.</p>}
          <ul>
            {data.entries.map((entry) => (
              <li key={entry.event_id}>
                <a href={`#/app/eventos/${entry.event_id}`}>
                  {entry.issue_code ?? 'Classe não confirmada'}
                </a>
                <p>
                  {entry.status} ·{' '}
                  {entry.eligible ? 'Elegível para exportação tabular' : entry.reason}
                </p>
              </li>
            ))}
          </ul>
          <button
            disabled={data.rows.length === 0}
            onClick={() => {
              const url = URL.createObjectURL(
                new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
              );
              const link = document.createElement('a');
              link.href = url;
              link.download = 'urmind-tabular-review-export.json';
              link.click();
              setTimeout(() => URL.revokeObjectURL(url), 1000);
            }}
          >
            Exportar snapshot tabular elegível ({data.rows.length})
          </button>
          <a href="#/app/reviews">Consultar revisões de ocorrências</a>
          <div className="actions">
            <button
              disabled={cursors.length === 1}
              onClick={() => setCursors(cursors.slice(0, -1))}
            >
              Anterior
            </button>
            <button
              disabled={!data.next_cursor}
              onClick={() => setCursors([...cursors, data.next_cursor!])}
            >
              Próxima
            </button>
          </div>
        </>
      )}
    </section>
  );
}

export function ReportIndicators() {
  const [totals, setTotals] = useState<Awaited<ReturnType<typeof api.reportTotals>> | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void api
      .reportTotals(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setTotals(data);
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => controller.abort();
  }, []);
  return (
    <section className="panel">
      <h2>Relatos recebidos</h2>
      {error && <p role="alert">Indicadores indisponíveis: {error}</p>}
      {!totals && !error && <p role="status">Consultando relatos…</p>}
      {totals && (
        <>
          <p>Dia civil em UTC; semana = últimos sete dias.</p>
          <dl>
            <dt>Hoje</dt>
            <dd>{totals.today}</dd>
            <dt>Semana</dt>
            <dd>{totals.week}</dd>
            <dt>Aguardando revisão</dt>
            <dd>{totals.awaiting_review}</dd>
            <dt>Sem localização</dt>
            <dd>{totals.without_location}</dd>
            <dt>Conflitos de localização</dt>
            <dd>{totals.location_conflicts}</dd>
            <dt>Publicados</dt>
            <dd>{totals.published}</dd>
          </dl>
          <h3>Rejeições do porteiro por motivo</h3>
          {Object.entries(totals.rejected_by_reason).length === 0 ? (
            <p>Nenhuma rejeição registrada.</p>
          ) : (
            <ul>
              {Object.entries(totals.rejected_by_reason).map(([reason, count]) => (
                <li key={reason}>
                  {reason}: {count}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
const fields = [
  ['min_side', 'Menor lado da imagem (px)'],
  ['brightness_min', 'Brilho mínimo'],
  ['brightness_max', 'Brilho máximo'],
  ['laplacian_min', 'Nitidez mínima'],
  ['phash_distance', 'Distância máxima pHash (duplicatas)'],
  ['old_photo_days', 'Aviso de idade EXIF (dias)'],
  ['scene_accept_margin', 'Margem para aceitar cena (após calibração)'],
  ['scene_reject_margin', 'Margem para rejeitar cena (após calibração)'],
  ['dominant_face_ratio', 'Fração máxima de rosto dominante'],
] as const;

/** The existing administration route owns this persisted policy editor. */
export function OperationsPage() {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const saveController = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void api
      .photoGatePolicy(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setPolicy(value);
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => {
      controller.abort();
      saveController.current?.abort();
    };
  }, []);
  return (
    <section className="panel">
      <h1>Administração</h1>
      <p>
        Configuração operacional do porteiro. Alterações são validadas no servidor e registradas na
        auditoria.
      </p>
      {error && <p role="alert">{error}</p>}
      {!policy && !error && <p role="status">Carregando configuração…</p>}
      {policy && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setBusy(true);
            setSaved(false);
            setError('');
            saveController.current?.abort();
            const controller = new AbortController();
            saveController.current = controller;
            void api
              .savePhotoGatePolicy(policy, controller.signal)
              .then((value) => {
                if (controller.signal.aborted) return;
                setPolicy(value);
                setSaved(true);
              })
              .catch((failure: Error) => {
                if (!controller.signal.aborted) setError(failure.message);
              })
              .finally(() => {
                if (!controller.signal.aborted) setBusy(false);
              });
          }}
        >
          <label>
            <input
              type="checkbox"
              checked={policy.public_capture_markers_enabled}
              onChange={(event) =>
                setPolicy({ ...policy, public_capture_markers_enabled: event.target.checked })
              }
            />
            Exibir camada pública genérica, sem foto nem descrição
          </label>
          {fields.map(([key, label]) => (
            <label key={key}>
              {label}
              <input
                type="number"
                required
                step="any"
                value={policy[key]}
                onChange={(event) => setPolicy({ ...policy, [key]: Number(event.target.value) })}
              />
            </label>
          ))}
          <p>
            Cena exige artefato e calibração próprios; sem calibração, fica pendente de revisão.
            Rostos dependem do YuNet verificado no servidor. Aprovação técnica não confirma um
            problema.
          </p>
          <button disabled={busy} type="submit">
            {busy ? 'Salvando…' : 'Salvar configuração'}
          </button>
          {saved && <p role="status">Configuração salva com auditoria.</p>}
        </form>
      )}
    </section>
  );
}

export function OperationalRegistryPage({ mode }: { mode: 'models' | 'audit' }) {
  const [models, setModels] = useState<Awaited<ReturnType<typeof api.operationalModels>> | null>(
    null,
  );
  const [audit, setAudit] = useState<Awaited<ReturnType<typeof api.operationalAudit>> | null>(null);
  const [operation, setOperation] = useState('');
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setModels(null);
    setAudit(null);
    setError('');
    const request =
      mode === 'models'
        ? api.operationalModels(controller.signal).then((rows) => {
            if (!controller.signal.aborted) setModels(rows);
          })
        : api
            .operationalAudit(operation, cursors.at(-1) ?? null, controller.signal)
            .then((rows) => {
              if (!controller.signal.aborted) setAudit(rows);
            });
    void request.catch((failure: Error) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [mode, operation, cursors]);
  return (
    <section className="panel">
      <h1>{mode === 'models' ? 'Modelos registrados' : 'Auditoria operacional'}</h1>
      {mode === 'audit' && (
        <label>
          Operação
          <input
            value={operation}
            onChange={(event) => {
              setOperation(event.target.value);
              setCursors([null]);
            }}
          />
        </label>
      )}
      {error && <p role="alert">{error}</p>}
      {!error && !models && !audit && <p role="status">Carregando registros…</p>}
      {models && (
        <>
          <p>Somente leitura. Registro não significa modelo autorizado para inferência.</p>
          {models.length === 0 && <p>Nenhum modelo registrado.</p>}
          <ul>
            {models.map((model) => (
              <li key={model.id}>
                <strong>
                  {model.name} — {model.version}
                </strong>
                <p>
                  {model.status} · {model.kind}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
      {audit && (
        <>
          <p>Apenas o envelope da auditoria; identidades e payloads privados não são exibidos.</p>
          {audit.length === 0 && <p>Nenhum registro neste filtro.</p>}
          <ul>
            {audit.map((row) => (
              <li key={row.id}>
                <strong>{row.operation}</strong>
                <p>
                  {row.entity_type} · {row.created_at}
                </p>
                <code>{row.event_hash}</code>
              </li>
            ))}
          </ul>
          <div className="actions">
            <button
              disabled={cursors.length === 1}
              onClick={() => setCursors(cursors.slice(0, -1))}
            >
              Anterior
            </button>
            <button
              disabled={audit.length < 50}
              onClick={() => {
                const last = audit.at(-1)!;
                setCursors([...cursors, `${last.created_at}|${last.id}`]);
              }}
            >
              Próxima
            </button>
          </div>
        </>
      )}
    </section>
  );
}
