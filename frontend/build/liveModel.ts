import { createHash } from 'node:crypto';
import { existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, relative, resolve } from 'node:path';
import type { Plugin } from 'vite';

type Fetch = (
  url: string,
) => Promise<{ ok: boolean; status: number; arrayBuffer(): Promise<ArrayBuffer> }>;

export type DeliveryResult =
  | { status: 'local_verified' }
  | { status: 'downloaded_verified' }
  | { status: 'no_manifest' }
  | { status: 'withdrawn'; reason: string };

const digest = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');

const CONTENT_ADDRESSED = /^(https:\/\/.+)\/[^/]+\/[0-9a-f]{64}\/[^/]+\.onnx$/;

/**
 * The model bucket is content-addressed (`<model_version>/<sha256>/<file>.onnx`, see
 * `storage_object_path` in the backend). When `LIVE_MODEL_ONNX_URL` follows that layout,
 * the object pinned by the versioned manifest is fetched from the same bucket, so a new
 * ONNX ships with the manifest alone; any other URL is used as is. The bytes are
 * verified against the manifest either way.
 */
export function modelDownloadUrl(
  onnxUrl: string,
  manifest: { model_version?: unknown; onnx: { path: string; sha256: string } },
): string {
  const match = CONTENT_ADDRESSED.exec(onnxUrl);
  if (!match || typeof manifest.model_version !== 'string') return onnxUrl;
  const file = manifest.onnx.path.split('/').pop();
  return `${match[1]}/${manifest.model_version}/${manifest.onnx.sha256}/${file}`;
}

/**
 * Live-detection weights are not in Git (35 MB). The versioned manifest pins the ONNX
 * by SHA-256 and size; this step makes the deployed pair reproducible: a local copy
 * must match the pin (a mismatch fails the build), otherwise the file is downloaded
 * from `onnxUrl` and verified. Without verified weights the manifest is removed from
 * the output, so the app reports the model as unavailable instead of shipping a
 * manifest that points at a missing or different file.
 */
export async function deliverLiveModel(
  outDir: string,
  onnxUrl: string | undefined,
  fetchImpl: Fetch = fetch,
): Promise<DeliveryResult> {
  const manifestPath = join(outDir, 'models', 'live-detection.json');
  if (!existsSync(manifestPath)) return { status: 'no_manifest' };
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as {
    model_version?: unknown;
    onnx: { path: string; sha256: string; size_bytes: number };
  };
  const modelsDir = resolve(outDir, 'models');
  const target = resolve(outDir, manifest.onnx.path.replace(/^\//, ''));
  const inside = relative(modelsDir, target);
  // The manifest path is data: it may never read or write outside dist/models.
  if (!inside || inside.startsWith('..') || isAbsolute(inside)) {
    throw new Error(`caminho do ONNX fora de models/: ${manifest.onnx.path}`);
  }
  if (existsSync(target)) {
    if (digest(readFileSync(target)) !== manifest.onnx.sha256) {
      throw new Error(`ONNX local difere do SHA-256 fixado no manifesto: ${target}`);
    }
    return { status: 'local_verified' };
  }
  const withdraw = (reason: string): DeliveryResult => {
    rmSync(manifestPath);
    return { status: 'withdrawn', reason };
  };
  if (!onnxUrl) return withdraw('LIVE_MODEL_ONNX_URL não definido e ONNX ausente');
  if (!onnxUrl.startsWith('https://')) return withdraw('LIVE_MODEL_ONNX_URL deve ser https');
  try {
    const response = await fetchImpl(modelDownloadUrl(onnxUrl, manifest));
    if (!response.ok) return withdraw(`download HTTP ${response.status}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength !== manifest.onnx.size_bytes || digest(bytes) !== manifest.onnx.sha256) {
      return withdraw('ONNX baixado não confere com tamanho/SHA-256 do manifesto');
    }
    writeFileSync(target, bytes);
    return { status: 'downloaded_verified' };
  } catch (error) {
    return withdraw(`download falhou (${(error as Error).name})`);
  }
}

export function liveModelDelivery(onnxUrl: string | undefined): Plugin {
  let outDir = 'dist';
  return {
    name: 'urmind-live-model-delivery',
    apply: 'build',
    configResolved(config) {
      outDir = config.build.outDir;
    },
    async closeBundle() {
      const result = await deliverLiveModel(outDir, onnxUrl);
      if (result.status === 'withdrawn') {
        this.warn(`detecção ao vivo indisponível neste build: ${result.reason}`);
      }
    },
  };
}
