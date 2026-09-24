import { useEffect, useRef, useState } from 'react';
import { api } from '../services/api';

type Policy = Awaited<ReturnType<typeof api.photoGatePolicy>>;

export function GroundTruthPage() {
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [data, setData] = useState<Awaited<ReturnType<typeof api.groundTruth>> | null>(null);
  const [summary, setSummary] = useState<Awaited<ReturnType<typeof api.groundTruthSummary>> | null>(
    null,
  );
  const [error, setError] = useState('');
  const [exportError, setExportError] = useState('');
  const [exporting, setExporting] = useState(false);
  const exportController = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void api
      .groundTruthSummary(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setSummary(value);
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => {
      controller.abort();
      exportController.current?.abort();
    };
  }, []);
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
          <p>Contagens de todas as páginas; cada Event mantém todos os votos.</p>
          <ul>
            {Object.entries(summary?.counts_by_class ?? {}).map(([issue, count]) => (
              <li key={issue}>
                {issue}: {count}
              </li>
            ))}
          </ul>
          {summary && (
            <p>
              {summary.reviewed_events} Events revisados; {summary.eligible_events} elegíveis para
              exportação. A exportação não autoriza treinamento.
            </p>
          )}
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
            disabled={exporting || !summary?.eligible_events}
            onClick={() => {
              exportController.current?.abort();
              const controller = new AbortController();
              exportController.current = controller;
              setExporting(true);
              setExportError('');
              void api
                .exportGroundTruth(controller.signal)
                .then((blob) => {
                  if (controller.signal.aborted) return;
                  const url = URL.createObjectURL(blob);
                  const link = document.createElement('a');
                  link.href = url;
                  link.download = 'urmind-ground-truth.ndjson';
                  link.click();
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                })
                .catch((failure: Error) => {
                  if (!controller.signal.aborted) setExportError(failure.message);
                })
                .finally(() => {
                  if (!controller.signal.aborted) setExporting(false);
                });
            }}
          >
            {exporting
              ? 'Exportando…'
              : `Exportar rótulos elegíveis (${summary?.eligible_events ?? 0})`}
          </button>
          {exportError && <p role="alert">{exportError}</p>}
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
  const [days, setDays] = useState(30);
  const [totals, setTotals] = useState<Awaited<ReturnType<typeof api.reportTotals>> | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void api
      .reportTotals(controller.signal, days)
      .then((data) => {
        if (!controller.signal.aborted) setTotals(data);
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => controller.abort();
  }, [days]);
  return (
    <section className="panel">
      <h2>Relatos recebidos</h2>
      <label>
        Período do porteiro
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>7 dias</option>
          <option value={30}>30 dias</option>
          <option value={90}>90 dias</option>
        </select>
      </label>
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
          {totals.gate_metrics && (
            <>
              <p>
                {totals.gate_metrics.accepted} envios aceitos e {totals.gate_metrics.rejected}{' '}
                rejeitados nos últimos {totals.gate_metrics.days} dias.
              </p>
              <p>
                Estimativa de falsa rejeição:{' '}
                {totals.gate_metrics.false_rejection_estimate === null
                  ? 'amostra indisponível'
                  : `${(totals.gate_metrics.false_rejection_estimate * 100).toFixed(1)}%`}
                . Reenvio do mesmo titular em até 24 h, posteriormente confirmado por revisão. Não
                comprova que seja a mesma foto ou uma rejeição incorreta; decisões posteriores
                alteram esta estimativa retrospectiva.
              </p>
              <ul>
                {Object.entries(totals.gate_metrics.rates_by_reason).map(([reason, rate]) => (
                  <li key={reason}>
                    {reason}: {rate === null ? 'indisponível' : `${(rate * 100).toFixed(1)}%`} das
                    tentativas
                  </li>
                ))}
              </ul>
            </>
          )}
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
      <IntegrationHealth />
    </section>
  );
}

function IntegrationHealth() {
  const [rows, setRows] = useState<Awaited<ReturnType<typeof api.integrationHealth>> | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void api
      .integrationHealth(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setRows(data);
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => controller.abort();
  }, []);
  return (
    <section aria-label="Integrações">
      <h2>Integrações</h2>
      <p>
        Saúde observada, somente leitura. Configuração não comprova disponibilidade. Falhas de
        contexto são degradáveis; armazenamento e autenticação são essenciais.
      </p>
      {error && <p role="alert">Saúde indisponível: {error}</p>}
      {!rows && !error && <p role="status">Consultando verificações…</p>}
      {rows?.length === 0 && (
        <p>Nenhuma verificação registrada. Execute live-check --persist no DEV.</p>
      )}
      <ul>
        {rows?.map((row) => (
          <li key={row.name}>
            <strong>
              {row.name}: {row.status}
            </strong>
            <p>{row.detail}</p>
            <p>
              Verificado: {row.checked_at}; latência: {row.latency_ms ?? 'não medida'} ms
            </p>
            <p>
              Último sucesso: {row.last_success ?? 'não observado'}. Última falha:{' '}
              {row.last_failure ?? 'não observada'}.
            </p>
          </li>
        ))}
      </ul>
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
        ? api.operationalModels(cursors.at(-1) ?? null, controller.signal).then((rows) => {
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
          <div className="actions">
            <button
              disabled={cursors.length === 1}
              onClick={() => setCursors(cursors.slice(0, -1))}
            >
              Anterior
            </button>
            <button
              disabled={models.length < 50}
              onClick={() => {
                const last = models.at(-1)!;
                setCursors([...cursors, `${last.created_at}|${last.id}`]);
              }}
            >
              Próxima
            </button>
          </div>
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
