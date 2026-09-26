import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { deliverLiveModel } from '../build/liveModel';

const weights = new TextEncoder().encode('onnx-sintetico');
const sha = createHash('sha256').update(weights).digest('hex');

function outDir(withOnnx?: Uint8Array) {
  const dir = mkdtempSync(join(tmpdir(), 'urmind-live-'));
  mkdirSync(join(dir, 'models'));
  writeFileSync(
    join(dir, 'models', 'live-detection.json'),
    JSON.stringify({
      onnx: { path: '/models/m.onnx', sha256: sha, size_bytes: weights.byteLength },
    }),
  );
  if (withOnnx) writeFileSync(join(dir, 'models', 'm.onnx'), withOnnx);
  return dir;
}

const serve =
  (bytes: Uint8Array, status = 200) =>
  async () => ({
    ok: status === 200,
    status,
    arrayBuffer: async () => bytes.slice().buffer,
  });

describe('entrega reproduzível do ONNX ao vivo', () => {
  it('aceita a cópia local que confere com o manifesto', async () => {
    await expect(deliverLiveModel(outDir(weights), undefined)).resolves.toEqual({
      status: 'local_verified',
    });
  });

  it.each(['/models/../evil.onnx', '/../m.onnx', '/other/m.onnx'])(
    'recusa caminho de ONNX fora de models/: %s',
    async (path) => {
      const dir = outDir(weights);
      writeFileSync(
        join(dir, 'models', 'live-detection.json'),
        JSON.stringify({ onnx: { path, sha256: sha, size_bytes: weights.byteLength } }),
      );
      await expect(
        deliverLiveModel(dir, 'https://example.test/m.onnx', serve(weights)),
      ).rejects.toThrow('fora de models/');
    },
  );

  it('falha o build quando a cópia local foi alterada', async () => {
    const tampered = outDir(new TextEncoder().encode('outro'));
    await expect(deliverLiveModel(tampered, undefined)).rejects.toThrow('SHA-256');
  });

  it('baixa, confere e grava quando o ONNX não está no build', async () => {
    const dir = outDir();
    await expect(
      deliverLiveModel(dir, 'https://example.test/m.onnx', serve(weights)),
    ).resolves.toEqual({ status: 'downloaded_verified' });
    expect(new Uint8Array(readFileSync(join(dir, 'models', 'm.onnx')))).toEqual(weights);
  });

  it.each([
    ['sem URL', undefined, serve(weights)],
    ['URL sem https', 'http://example.test/m.onnx', serve(weights)],
    ['download 404', 'https://example.test/m.onnx', serve(weights, 404)],
    ['conteúdo adulterado', 'https://example.test/m.onnx', serve(new TextEncoder().encode('x'))],
  ])('retira o manifesto sem peso verificado: %s', async (_name, url, fetchImpl) => {
    const dir = outDir();
    const result = await deliverLiveModel(dir, url, fetchImpl);
    expect(result.status).toBe('withdrawn');
    expect(existsSync(join(dir, 'models', 'live-detection.json'))).toBe(false);
    expect(existsSync(join(dir, 'models', 'm.onnx'))).toBe(false);
  });
});
