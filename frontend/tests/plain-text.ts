import { expect, type Page } from '@playwright/test';

/*
 * Varredura de linguagem simples, usada pelas specs que percorrem as telas. Lê tudo o
 * que uma pessoa vê ou ouve, com o que está recolhido aberto, e falha com o trecho
 * exato quando aparece termo de bastidor, código interno ou texto em inglês.
 */

/** Termos que não podem aparecer, com o motivo. */
export const FORBIDDEN: Array<[RegExp, string]> = [
  [/\bbackend\b/i, 'termo de bastidor'],
  [/\bAPI\b/, 'termo de bastidor'],
  [/\bendpoint/i, 'termo de bastidor'],
  [/\bframe\b/i, 'termo de bastidor'],
  [/\bpipeline\b/i, 'termo de bastidor'],
  [/\bLLM\b/, 'termo de bastidor'],
  [/porteiro/i, 'termo de bastidor'],
  [/sanitiza/i, 'termo de bastidor'],
  [/inferência/i, 'termo de bastidor'],
  [/\brótulo/i, 'termo de bastidor'],
  [/adjudic/i, 'termo de bastidor'],
  [/taxonomia/i, 'termo de bastidor'],
  // Nome de modelo com hífen ("synthetic-detector") é identificador, não texto corrido.
  [/(?<![\w-])detector(?![\w-])/i, 'termo de bastidor'],
  [/realtime/i, 'termo de bastidor'],
  [/supabase|postgis/i, 'nome de serviço interno'],
  [/\bcursor/i, 'termo de bastidor'],
  [/\brecorte\b/i, 'termo de bastidor'],
  [/versionad/i, 'termo de bastidor'],
  [/gêmeo digital/i, 'termo de bastidor'],
  [/\benvelope\b/i, 'termo de bastidor'],
  [/\bdataset\b/i, 'termo de bastidor'],
  [/live-check/i, 'instrução de desenvolvimento'],
  [/\b(payload|schema|token|JWT|WebRTC|STUN)\b/i, 'termo de bastidor'],
  [/contrato/i, 'termo de bastidor'],
  [/\bEXIF\b|phash|laplacian/i, 'nome de algoritmo'],
  [/\bp50\b|\bp95\b|latência/i, 'métrica técnica'],
  [/\bscout\b/i, 'hardware fora do escopo'],
  [/urmind-issue|actions-v\d|risk-v\d|rules-v\d|actions_catalog/i, 'versão interna'],
  [/\bURMIND_[A-Z0-9_]+/, 'código de classe'],
  [/\b[a-z]+_[a-z]+(?:_[a-z]+)*\b/, 'código em snake_case'],
  [/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/, 'data em formato de máquina'],
  [/\bzoom \d/i, 'leitura técnica do mapa'],
  [/\b(UTC|GMT)\b/, 'fuso horário técnico'],
  [/\b(hash|UUID|ID)\b/i, 'identificador técnico'],
  [/\b(null|undefined|NaN|true|false)\b|\[object Object\]/, 'valor vazado do código'],
  [/\b(Staging|Production|Fixture|Archived)\b/, 'fase do modelo em inglês'],
  [/(?<![\d.,-])0\.\d+\b/, 'número com ponto decimal'],
  [/não disponível/i, 'linha sem dado'],
  [
    /\b(Zoom in|Zoom out|Find my location|Toggle attribution|Reset bearing|Close popup|Map feedback|Map|Loading|Submit|Cancel)\b/,
    'texto em inglês',
  ],
];

/**
 * Tudo o que a pessoa vê ou ouve na tela atual, com o que está recolhido aberto:
 * texto, dicas, nomes acessíveis, exemplos de preenchimento, descrições de imagem e
 * o título da aba.
 */
export async function screenText(page: Page): Promise<string> {
  await page.evaluate(() =>
    document.querySelectorAll('details').forEach((item) => {
      item.open = true;
    }),
  );
  // O que só carrega ao abrir (histórico, detalhes técnicos sob demanda).
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => undefined);
  await page.waitForTimeout(300);
  return page.evaluate(() => {
    const skip = '.maplibregl-ctrl-attrib-inner, .protocol-code, .record-id';
    const style = document.createElement('style');
    style.textContent = `${skip} { display: none !important; }`;
    document.head.append(style);
    const text = document.body.innerText;
    style.remove();
    const names = ['title', 'aria-label', 'aria-description', 'placeholder', 'alt'];
    const attributes = [
      ...document.querySelectorAll(
        '[title], [aria-label], [aria-description], [placeholder], [alt]',
      ),
    ]
      .filter((element) => !element.closest(skip))
      .flatMap((element) => names.map((name) => element.getAttribute(name) ?? ''))
      .filter(Boolean);
    return [document.title, text, ...attributes].join('\n');
  });
}

export async function expectPlain(page: Page, where: string) {
  await expect(page.locator('main :is(h1, h2)').first()).toBeVisible({ timeout: 15_000 });
  // Espera os blocos que carregam depois (mapa, relatos, indicadores).
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => undefined);
  await page.waitForTimeout(300);
  const text = await screenText(page);
  const found = FORBIDDEN.flatMap(([pattern, reason]) => {
    const match = pattern.exec(text);
    if (!match) return [];
    const around = text.slice(Math.max(0, match.index - 30), match.index + match[0].length + 30);
    return [`"${match[0]}" (${reason}) em: …${around.replace(/\s+/g, ' ')}…`];
  });
  expect(found, `texto técnico em ${where}`).toEqual([]);
}
