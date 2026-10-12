import { TaxonomyPanel } from '../../components/TaxonomyPanel';
import { PageHeader } from '../../components/ui';
import { automaticClassCodes } from '../../domain/public';

/** Sobre e privacidade: o que o UrMind faz, o que guarda e quem decide. */
export function AboutPage({ revision }: { revision: number }) {
  return (
    <>
      <PageHeader
        title="Sobre e privacidade"
        description="O UrMind recebe relatos de problemas urbanos com foto e local, organiza a revisão pela equipe e publica no mapa o que foi confirmado."
      />
      <div className="about-grid">
        <section className="panel" aria-labelledby="about-privacy">
          <h2 id="about-privacy">Privacidade</h2>
          <p>
            Só você e a equipe veem a foto original e o local exato. A foto só vai para o mapa
            depois que a equipe confere que não há rostos, placas de veículos ou dados pessoais à
            mostra, e a cópia publicada não leva os dados guardados no arquivo (como o modelo do
            celular).
          </p>
          <p>Evite fotografar documentos, rostos de perto ou placas de veículos.</p>
        </section>
        <section className="panel" aria-labelledby="about-review">
          <h2 id="about-review">Quem decide</h2>
          <p>
            {automaticClassCodes().length
              ? 'Alguns tipos de problema já têm análise automática, sempre como sugestão: cada relato continua sendo conferido por uma pessoa da equipe.'
              : 'Hoje não há análise automática das fotos: cada relato é conferido por uma pessoa da equipe.'}{' '}
            Um relato recebido só vira ocorrência no mapa depois dessa revisão.
          </p>
          <p>
            O UrMind não avisa a prefeitura: ele organiza os relatos e publica o que foi confirmado.
            Para pedir o conserto, registre também no{' '}
            <a
              href="https://sp156.prefeitura.sp.gov.br/portal"
              target="_blank"
              rel="noopener noreferrer"
            >
              SP156
            </a>
            , o canal oficial da cidade de São Paulo.
          </p>
          <a className="text-button" href="#/transparency">
            Como o UrMind funciona
          </a>
        </section>
      </div>
      {/* Só aparece quando há tipos de problema reconhecidos automaticamente. */}
      <TaxonomyPanel revision={revision} />
    </>
  );
}
