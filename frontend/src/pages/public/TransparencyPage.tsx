import { usePublicData } from '../../app/hooks/usePublicData';
import { SystemFlow } from '../../components/SystemFlow';
import { TaxonomyPanel } from '../../components/TaxonomyPanel';
import { PageHeader, Skeleton } from '../../components/ui';
import { plain, ruleName, sourceName } from '../../content/plainLanguage';
import { systemFlow } from '../../content/systemFlow';
import { automaticClassCodes, type Transparency } from '../../domain/public';
import { publicApi } from '../../services/publicApi';

const TITLE = 'Como o UrMind funciona';

/** Como o UrMind funciona: o caminho de um relato, as fontes, as regras e os limites. */
export function TransparencyPage({ revision }: { revision: number }) {
  const { data, error, loading } = usePublicData<Transparency>(
    (signal) => publicApi.transparency(signal),
    [revision],
  );
  if (loading && !data) return <Skeleton lines={6} />;
  if (error)
    return (
      <>
        <PageHeader title={TITLE} />
        <p className="error" role="alert">
          {error}
        </p>
      </>
    );
  if (!data) return null;
  return (
    <>
      <PageHeader
        title={TITLE}
        description="Do envio da foto ao mapa público: o que acontece em cada etapa e quem decide."
      />
      <section className="panel" aria-labelledby="flow-title">
        <h2 id="flow-title">Caminho de um relato</h2>
        <p className="muted">Escolha uma etapa para ver o que acontece nela.</p>
        <SystemFlow
          stages={systemFlow({
            transparency: data,
            automaticClasses: automaticClassCodes(),
          })}
          label="Etapas de um relato"
        />
      </section>
      {/* Só aparece quando há tipos de problema reconhecidos automaticamente. */}
      <TaxonomyPanel revision={revision} />
      <section className="panel" aria-labelledby="rules-title">
        <h2 id="rules-title">Dados e regras</h2>
        <div className="transparency-grid">
          <div>
            <h3>Fontes externas</h3>
            <ul className="context-list">
              {/* Fontes com o mesmo nome (o OpenStreetMap serve endereço e ruas) numa linha. */}
              {Object.entries(
                data.context_sources.reduce<Record<string, string[]>>((all, source) => {
                  const name = sourceName(source.source);
                  (all[name] ??= []).push(plain(source.use));
                  return all;
                }, {}),
              ).map(([name, uses]) => (
                <li key={name}>
                  <strong>{name}</strong>
                  <span>{uses.join('; ')}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3>Regras</h3>
            <dl className="data-list rules-list">
              {Object.entries(data.rules).map(([name, value]) => (
                <div key={name}>
                  <dt>{ruleName(name)}</dt>
                  <dd>{plain(String(value))}</dd>
                </div>
              ))}
            </dl>
          </div>
          {data.limitations.length > 0 && (
            <div>
              <h3>Limitações atuais</h3>
              <ul className="limitations">
                {data.limitations.map((item) => (
                  <li key={item}>{plain(item)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </section>
    </>
  );
}
