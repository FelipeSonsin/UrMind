import {
  expect,
  test,
  type Browser,
  type BrowserContextOptions,
  type Page,
} from '@playwright/test';
import { EVENT_ID, fakeCamera, signedIn, stubPublicApi, syntheticReportPhoto } from './fixtures';
import { CITIZEN_SCREENS, citizenData, openScreen, TEAM_SCREENS, teamData } from './screens';

/*
 * Acabamento visual medido, não só olhado: cada tela do cidadão e da equipe, em
 * celulares, tablets e computadores (emulados no navegador do projeto, com toque
 * nos aparelhos de toque), passa pelas mesmas regras:
 *  - nada de rolagem lateral nem de texto cortado;
 *  - um título principal (h1) e níveis de título sem pular (h2 depois de h1…);
 *  - contraste de texto WCAG AA (4,5:1; 3:1 em texto grande), inclusive nos
 *    textos de exemplo dos campos;
 *  - toda imagem com descrição e todo botão, link ou campo com nome;
 *  - área de toque de pelo menos 24 × 24 px nos aparelhos de toque (WCAG 2.5.8);
 *  - uma família de letra só (Public Sans; códigos em monoespaçada);
 *  - foco visível ao navegar pelo teclado;
 *  - nenhum erro de script.
 * Não substitui o teste num aparelho físico: mede o que a tela desenha.
 */

interface Profile {
  name: string;
  options: BrowserContextOptions;
  touch: boolean;
}

const touchDevice = (width: number, height: number): BrowserContextOptions => ({
  viewport: { width, height },
  deviceScaleFactor: 2,
  isMobile: width < 800,
  hasTouch: true,
});

const PROFILES: Profile[] = [
  { name: 'celular pequeno 320 px', options: touchDevice(320, 640), touch: true },
  { name: 'Android 360 px', options: touchDevice(360, 780), touch: true },
  { name: 'iPhone 390 px', options: touchDevice(390, 844), touch: true },
  { name: 'celular grande 430 px', options: touchDevice(430, 932), touch: true },
  { name: 'celular deitado 844 px', options: touchDevice(844, 390), touch: true },
  { name: 'tablet em pé 768 px', options: touchDevice(768, 1024), touch: true },
  { name: 'tablet deitado 1024 px', options: touchDevice(1024, 768), touch: true },
  { name: 'notebook 1280 px', options: { viewport: { width: 1280, height: 800 } }, touch: false },
  { name: 'monitor 1440 px', options: { viewport: { width: 1440, height: 900 } }, touch: false },
  {
    name: 'monitor grande 1920 px',
    options: { viewport: { width: 1920, height: 1080 } },
    touch: false,
  },
];

/** Problemas de acabamento da tela atual; lista vazia quando está tudo certo. */
function auditScreen(page: Page, touch: boolean): Promise<string[]> {
  return page.evaluate((touch) => {
    const issues: string[] = [];
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const ink = canvas.getContext('2d', { willReadFrequently: true })!;
    /** Qualquer cor CSS (hex, rgb, color-mix, oklch…) em RGBA 0–255. */
    const rgba = (css: string) => {
      ink.clearRect(0, 0, 1, 1);
      ink.fillStyle = '#000';
      ink.fillStyle = css;
      ink.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = ink.getImageData(0, 0, 1, 1).data;
      return { r, g, b, a: a / 255 };
    };
    type Color = ReturnType<typeof rgba>;
    const over = (top: Color, bottom: Color): Color => ({
      r: top.r * top.a + bottom.r * (1 - top.a),
      g: top.g * top.a + bottom.g * (1 - top.a),
      b: top.b * top.a + bottom.b * (1 - top.a),
      a: 1,
    });
    const luminance = ({ r, g, b }: Color) => {
      const channel = (value: number) => {
        const v = value / 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    };
    const contrast = (a: Color, b: Color) => {
      const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return (light + 0.05) / (dark + 0.05);
    };
    /** Fundo efetivo: as camadas de fundo até a primeira opaca. `null` com imagem/gradiente. */
    const background = (element: Element): Color | null => {
      const layers: Color[] = [];
      for (let node: Element | null = element; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (style.backgroundImage !== 'none') return null;
        const color = rgba(style.backgroundColor);
        if (color.a > 0) layers.push(color);
        if (color.a >= 1) break;
      }
      return layers.reduceRight<Color>((under, layer) => over(layer, under), {
        r: 255,
        g: 255,
        b: 255,
        a: 1,
      });
    };
    const visible = (element: Element) => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return (
        box.width > 0 &&
        box.height > 0 &&
        style.visibility !== 'hidden' &&
        style.display !== 'none' &&
        Number(style.opacity) > 0
      );
    };
    const label = (element: Element) => {
      const text = (element.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 50);
      const cls = element.getAttribute('class');
      return `<${element.tagName.toLowerCase()}${cls ? ` class="${cls}"` : ''}> "${text}"`;
    };
    const ownText = (element: Element) =>
      [...element.childNodes].some(
        (node) => node.nodeType === Node.TEXT_NODE && node.textContent!.trim().length > 0,
      );
    const visuallyHidden = (element: Element) => {
      const box = element.getBoundingClientRect();
      return box.width <= 1 || box.height <= 1 || element.closest('.sr-only') !== null;
    };
    const skipped = (element: Element) =>
      element.closest('.maplibregl-map canvas, .maplibregl-ctrl-attrib-inner, .brand-word') !==
      null;

    // Rolagem lateral.
    const root = document.documentElement;
    if (root.scrollWidth > root.clientWidth + 1)
      issues.push(`rolagem lateral: ${root.scrollWidth} px numa tela de ${root.clientWidth} px`);

    // Títulos: um h1 e níveis sem pular.
    const headings = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6')].filter(visible);
    const mainTitles = headings.filter((heading) => heading.tagName === 'H1');
    if (mainTitles.length !== 1)
      issues.push(`títulos principais (h1) visíveis: ${mainTitles.length}`);
    let previous = 0;
    for (const heading of headings) {
      const level = Number(heading.tagName[1]);
      if (previous && level > previous + 1)
        issues.push(`título pula de h${previous} para h${level}: ${label(heading)}`);
      previous = level;
    }

    // Imagens com descrição; botões, links e campos com nome.
    for (const image of document.querySelectorAll('img'))
      if (!image.hasAttribute('alt')) issues.push(`imagem sem descrição: ${image.src.slice(-50)}`);
    const nameOf = (element: Element): string => {
      const aria = element.getAttribute('aria-label')?.trim();
      if (aria) return aria;
      const labelledBy = element.getAttribute('aria-labelledby');
      if (labelledBy) {
        const text = labelledBy
          .split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent ?? '')
          .join(' ')
          .trim();
        if (text) return text;
      }
      if (
        element instanceof HTMLInputElement ||
        element instanceof HTMLSelectElement ||
        element instanceof HTMLTextAreaElement
      ) {
        const fromLabels = [...(element.labels ?? [])]
          .map((item) => item.textContent ?? '')
          .join(' ')
          .trim();
        if (fromLabels) return fromLabels;
        if (element instanceof HTMLInputElement && ['submit', 'button'].includes(element.type))
          return element.value;
        return element.getAttribute('title') ?? '';
      }
      const text = (element.textContent ?? '').trim();
      if (text) return text;
      const image = element.querySelector('img[alt]');
      if (image?.getAttribute('alt')) return image.getAttribute('alt')!;
      return element.getAttribute('title') ?? '';
    };
    const controls = document.querySelectorAll(
      'button, a[href], [role="button"], select, textarea, summary, input:not([type="hidden"])',
    );
    for (const control of controls) {
      const fileInput = control instanceof HTMLInputElement && control.type === 'file';
      if (!visible(control) && !fileInput) continue;
      if (!nameOf(control)) issues.push(`sem nome para leitor de tela: ${label(control)}`);
    }

    // Contraste do texto (e do texto de exemplo dos campos).
    for (const element of document.querySelectorAll('body *')) {
      if (!ownText(element) || !visible(element) || visuallyHidden(element) || skipped(element))
        continue;
      if (element.closest(':disabled, [aria-disabled="true"]')) continue;
      const style = getComputedStyle(element);
      const back = background(element);
      if (!back) continue;
      const fore = over(rgba(style.color), back);
      const size = parseFloat(style.fontSize);
      const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
      const ratio = contrast(fore, back);
      const minimum = large ? 3 : 4.5;
      if (ratio < minimum)
        issues.push(`contraste ${ratio.toFixed(2)} (mínimo ${minimum}): ${label(element)}`);
    }
    for (const field of document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(
      'input[placeholder], textarea[placeholder]',
    )) {
      if (!visible(field) || field.value) continue;
      const back = background(field);
      if (!back) continue;
      const fore = over(rgba(getComputedStyle(field, '::placeholder').color), back);
      const ratio = contrast(fore, back);
      if (ratio < 4.5)
        issues.push(
          `contraste do exemplo ${ratio.toFixed(2)} (mínimo 4,5): "${field.placeholder}"`,
        );
    }

    // Texto cortado: conteúdo maior que a caixa, escondido sem reticências.
    for (const element of document.querySelectorAll('body *')) {
      if (!ownText(element) || !visible(element) || visuallyHidden(element) || skipped(element))
        continue;
      if (element.closest('.maplibregl-map')) continue;
      const style = getComputedStyle(element);
      const clamped =
        style.textOverflow === 'ellipsis' ||
        (style as CSSStyleDeclaration & { webkitLineClamp?: string }).webkitLineClamp !== 'none';
      if (clamped) continue;
      const hiddenX = ['hidden', 'clip'].includes(style.overflowX);
      const hiddenY = ['hidden', 'clip'].includes(style.overflowY);
      if (
        (hiddenX && element.scrollWidth > element.clientWidth + 2) ||
        (hiddenY && element.scrollHeight > element.clientHeight + 2)
      )
        issues.push(`texto cortado: ${label(element)}`);
    }

    // Uma família de letra.
    for (const element of document.querySelectorAll('body *')) {
      if (!ownText(element) || !visible(element) || skipped(element)) continue;
      if (element.closest('.maplibregl-ctrl, code, kbd, pre, samp, .protocol-code, .record-id'))
        continue;
      const family = getComputedStyle(element).fontFamily;
      if (!family.includes('Public Sans'))
        issues.push(`letra fora do padrão (${family.split(',')[0]}): ${label(element)}`);
    }

    // Área de toque nos aparelhos de toque (link dentro de frase é exceção da regra).
    if (touch) {
      const targets = document.querySelectorAll(
        'button, a[href], [role="button"], select, summary, textarea, input:not([type="hidden"]):not([type="file"])',
      );
      for (const target of targets) {
        if (!visible(target) || visuallyHidden(target)) continue;
        const style = getComputedStyle(target);
        if (target.tagName === 'A' && style.display === 'inline') continue;
        const input = target as HTMLInputElement;
        const area =
          input.type === 'checkbox' || input.type === 'radio'
            ? (target.closest('label') ?? target)
            : target;
        const box = area.getBoundingClientRect();
        if (box.width < 24 || box.height < 24)
          issues.push(
            `área de toque ${Math.round(box.width)} × ${Math.round(box.height)} px: ${label(target)}`,
          );
      }
    }
    return [...new Set(issues)];
  }, touch);
}

/** Foco visível nas primeiras paradas do teclado (Tab). */
async function auditFocus(page: Page): Promise<string[]> {
  const issues: string[] = [];
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  for (let stop = 0; stop < 12; stop += 1) {
    await page.keyboard.press('Tab');
    const problem = await page.evaluate(() => {
      const active = document.activeElement;
      if (!active || active === document.body) return null;
      // Campo invisível (opção de um seletor segmentado): o destaque fica no rótulo.
      const hidden =
        Number(getComputedStyle(active).opacity) === 0 || active.getBoundingClientRect().width <= 1;
      const shown = hidden ? (active.closest('label') ?? active) : active;
      const style = getComputedStyle(shown);
      const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
      const ring = style.boxShadow !== 'none';
      if (outline || ring) return null;
      const text = (active.textContent ?? '').trim().slice(0, 40);
      return `<${active.tagName.toLowerCase()}> "${text || active.getAttribute('aria-label') || ''}"`;
    });
    if (problem) issues.push(`foco sem destaque: ${problem}`);
  }
  return issues;
}

async function auditSurface(
  browser: Browser,
  profile: Profile,
  screens: string[],
  prepare: (page: Page) => Promise<void>,
) {
  const context = await browser.newContext(profile.options);
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await prepare(page);
  const report: string[] = [];
  for (const path of screens) {
    await openScreen(page, path);
    await expect(page.locator('main :is(h1, h2)').first()).toBeVisible({ timeout: 15_000 });
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => undefined);
    await page.waitForTimeout(250);
    const issues = [...(await auditScreen(page, profile.touch)), ...(await auditFocus(page))];
    // Com tudo o que está recolhido aberto, as mesmas regras valem.
    await page.evaluate(() =>
      document.querySelectorAll('details').forEach((item) => {
        item.open = true;
      }),
    );
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => undefined);
    await page.waitForTimeout(250);
    issues.push(...(await auditScreen(page, profile.touch)));
    for (const issue of new Set(issues)) report.push(`${path}: ${issue}`);
  }
  await context.close();
  expect(errors, `erros de script em ${profile.name}`).toEqual([]);
  expect(report, `acabamento em ${profile.name}`).toEqual([]);
}

for (const profile of PROFILES) {
  test(`acabamento do site do cidadão: ${profile.name}`, async ({ browser }, info) => {
    test.skip(
      info.project.name === 'mobile',
      'os aparelhos são emulados nos projetos desktop e webkit',
    );
    test.setTimeout(240_000);
    await auditSurface(browser, profile, CITIZEN_SCREENS, async (page) => {
      await citizenData(page);
      await signedIn(page);
    });
  });

  test(`acabamento da área da equipe: ${profile.name}`, async ({ browser }, info) => {
    test.skip(
      info.project.name === 'mobile',
      'os aparelhos são emulados nos projetos desktop e webkit',
    );
    test.setTimeout(240_000);
    await auditSurface(browser, profile, TEAM_SCREENS, (page) => teamData(page));
  });
}

test('com "reduzir movimento" ligado, nada se anima', async ({ browser }, info) => {
  test.skip(info.project.name === 'mobile', 'um navegador de cada motor basta');
  const context = await browser.newContext({ reducedMotion: 'reduce' });
  const page = await context.newPage();
  await citizenData(page);
  await signedIn(page);
  for (const path of CITIZEN_SCREENS) {
    await openScreen(page, path);
    await expect(page.locator('main :is(h1, h2)').first()).toBeVisible({ timeout: 15_000 });
    const moving = await page.evaluate(
      () =>
        document
          .getAnimations()
          .filter(
            (animation) =>
              animation.playState === 'running' &&
              Number(animation.effect?.getComputedTiming().duration ?? 0) > 1,
          ).length,
    );
    expect(moving, `animações em ${path}`).toBe(0);
  }
  await context.close();
});

/** Estados que não são rota: vazio, servidor fora do ar, busca, mapa, foto, rascunho. */
const STATE_PROFILES = PROFILES.filter((profile) =>
  ['celular pequeno 320 px', 'iPhone 390 px', 'tablet em pé 768 px', 'notebook 1280 px'].includes(
    profile.name,
  ),
);

for (const profile of STATE_PROFILES) {
  test(`acabamento dos estados da tela: ${profile.name}`, async ({ browser }, info) => {
    test.skip(
      info.project.name === 'mobile',
      'os aparelhos são emulados nos projetos desktop e webkit',
    );
    test.setTimeout(240_000);
    const context = await browser.newContext(profile.options);
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    const report: string[] = [];
    const check = async (where: string) => {
      await expect(page.locator('main :is(h1, h2)').first()).toBeVisible({ timeout: 15_000 });
      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => undefined);
      await page.waitForTimeout(250);
      for (const issue of await auditScreen(page, profile.touch)) report.push(`${where}: ${issue}`);
    };

    // Sem nenhuma ocorrência publicada.
    await stubPublicApi(page, { events: [] });
    for (const path of ['/', '/#/events', '/#/mapa']) {
      await openScreen(page, path);
      await check(`sem ocorrências ${path}`);
    }

    // Com ocorrências: busca aberta, menu "Mais" e detalhe de um ponto do mapa.
    await stubPublicApi(page);
    await openScreen(page, '/');
    await page
      .getByRole('button', { name: 'Buscar página ou relato' })
      .filter({ visible: true })
      .click();
    await page.keyboard.type('buraco');
    await check('busca aberta');
    await page.keyboard.press('Escape');
    if ((profile.options.viewport?.width ?? 0) < 900) {
      await page.getByRole('navigation', { name: 'Navegação principal' }).getByText('Mais').click();
      await check('menu Mais aberto');
      await page.keyboard.press('Escape');
    }
    await openScreen(page, '/#/mapa');
    await page.getByText(/pontos? em lista/).click();
    await page.getByRole('button', { name: /Selecionar ponto.*Buraco/ }).click();
    await expect(page.getByRole('complementary', { name: 'Detalhe do ponto' })).toBeVisible();
    await check('detalhe do ponto no mapa');

    // Foto tirada agora com o GPS negado: aviso e mapa para marcar o ponto.
    await openScreen(page, '/#/registrar');
    await page.evaluate(() => {
      const fail = (_ok: unknown, error: (reason: { code: number }) => void) => error({ code: 1 });
      Object.defineProperty(navigator, 'geolocation', {
        value: { getCurrentPosition: fail, watchPosition: () => 1, clearWatch: () => undefined },
      });
    });
    const png = await syntheticReportPhoto(page);
    await page.getByLabel('Tirar foto').setInputFiles({
      name: 'agora.png',
      mimeType: 'image/png',
      buffer: Buffer.from(png, 'base64'),
    });
    await expect(page.getByText(/Localização bloqueada para este site/).first()).toBeVisible();
    await check('GPS negado');

    // Câmera do computador sem permissão (só nos tamanhos sem toque).
    if (!profile.touch) {
      const cameraContext = await browser.newContext(profile.options);
      const cameraPage = await cameraContext.newPage();
      await stubPublicApi(cameraPage);
      await fakeCamera(cameraPage, 'NotAllowedError');
      await openScreen(cameraPage, '/#/registrar');
      // O WebKit do Windows diz "ponteiro de toque" até sem toque: aí o site usa o app de
      // câmera do aparelho (como no celular) e a webcam não entra em jogo.
      const coarse = await cameraPage.evaluate(() => matchMedia('(pointer: coarse)').matches);
      if (!coarse) {
        await cameraPage.getByRole('button', { name: 'Tirar foto' }).click();
        await expect(cameraPage.getByRole('alert')).toContainText('Câmera indisponível');
        for (const issue of await auditScreen(cameraPage, false))
          report.push(`câmera sem permissão: ${issue}`);
      }
      await cameraContext.close();
    }

    // Foto sem localização (mapa para marcar o ponto) e o rascunho guardado.
    await openScreen(page, '/#/registrar');
    await page.getByLabel('Escolher foto').setInputFiles({
      name: 'evidencia.png',
      mimeType: 'image/png',
      buffer: Buffer.from(png, 'base64'),
    });
    await expect(page.getByText('Falta o local da foto')).toBeVisible();
    await check('foto escolhida sem local');
    await page.getByRole('checkbox', { name: /Li e aceito/ }).check();
    await page.getByRole('button', { name: 'Enviar relato' }).click();
    // Guardar a foto no aparelho e abrir Rascunhos pode passar de 5 s numa máquina ocupada.
    await expect(page.getByText('Falta o local', { exact: true })).toBeVisible({ timeout: 15_000 });
    await check('rascunho guardado');

    // Servidor fora do ar: os avisos de erro também seguem as regras.
    await page.route('**/api/v1/**', (route) =>
      route.fulfill({ status: 503, json: { detail: 'serviço indisponível' } }),
    );
    for (const path of [
      '/',
      '/#/events',
      `/#/events/${EVENT_ID}`,
      '/#/system',
      '/#/transparency',
    ]) {
      await openScreen(page, path);
      await check(`servidor fora do ar ${path}`);
    }

    await context.close();
    expect(errors, `erros de script em ${profile.name}`).toEqual([]);
    expect(report, `acabamento dos estados em ${profile.name}`).toEqual([]);
  });
}
