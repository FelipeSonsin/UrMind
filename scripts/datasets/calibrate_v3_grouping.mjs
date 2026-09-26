// Reproducible TRAIN-only calibration. Requires the repository's ffmpeg executable.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const sourcePath = join(root, 'datasets/reports/review_summary.json');
const outputPath = join(root, 'datasets/reports/v3_grouping_calibration.md');
const sha = data => createHash('sha256').update(data).digest('hex');
const seed = 20260925;

function decode(file, filter, width, height) {
  const bytes = execFileSync('ffmpeg', [
    '-v', 'error', '-i', file, '-vf', `${filter ? `${filter},` : ''}scale=${width}:${height}:flags=lanczos,format=gray`,
    '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1',
  ], { maxBuffer: 1024 * 1024 });
  if (bytes.length !== width * height) throw new Error(`Cannot decode ${file}`);
  return bytes;
}

function phash(pixels) {
  const values = [];
  for (let u = 0; u < 8; u++) for (let v = 0; v < 8; v++) {
    let sum = 0;
    for (let y = 0; y < 32; y++) for (let x = 0; x < 32; x++) {
      sum += pixels[y * 32 + x] * Math.cos(Math.PI * (x + .5) * u / 32) *
        Math.cos(Math.PI * (y + .5) * v / 32);
    }
    values.push(sum);
  }
  const ordered = values.slice(1).sort((a, b) => a - b);
  const median = ordered[Math.floor(ordered.length / 2)];
  return values.reduce((bits, value, i) => bits | ((i > 0 && value > median ? 1n : 0n) << BigInt(i)), 0n);
}

function dhash(pixels) {
  let bits = 0n;
  for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) {
    if (pixels[y * 9 + x + 1] > pixels[y * 9 + x]) bits |= 1n << BigInt(y * 8 + x);
  }
  return bits;
}

function hashes(file, filter = '') {
  return [phash(decode(file, filter, 32, 32)), dhash(decode(file, filter, 9, 8))];
}

function distance(a, b) {
  let bits = a ^ b;
  let count = 0;
  while (bits) { count++; bits &= bits - 1n; }
  return count;
}

function main() {
  const sourceBytes = readFileSync(sourcePath);
  const source = JSON.parse(sourceBytes);
  const byCountry = new Map();
  for (const row of source.proposals) {
    const name = row.evidence_chain.original_image_path;
    const match = name?.match(/\/RDD2022\/([^/]+)\/train\/images\//);
    if (row.SOURCE !== 'rdd2022' || !match) continue;
    const items = byCountry.get(match[1]) ?? [];
    if (items.length < 4) items.push([name, row.IMAGE_SHA256]);
    byCountry.set(match[1], items);
  }
  const selected = [...byCountry].sort(([a], [b]) => a.localeCompare(b)).flatMap(([, values]) => values);
  if (selected.length < 4 || byCountry.size < 2) throw new Error('Insufficient TRAIN images');
  const categories = {
    crop_10pct: 'crop=iw*0.90:ih*0.90',
    crop_25pct: 'crop=iw*0.75:ih*0.75',
    crop_40pct: 'crop=iw*0.60:ih*0.60',
    resize_50pct: 'scale=iw/2:ih/2:flags=lanczos',
    brightness_060: 'eq=brightness=-0.15',
    contrast_160: 'eq=contrast=1.6',
    jpeg_aggressive: null,
  };
  // JPEG is encoded and decoded explicitly below; the filter entry is only a category marker.
  const measured = Object.fromEntries(Object.keys(categories).map(key => [key, []]));
  const originals = new Map();
  for (const [name, expected] of selected) {
    const file = join(root, name);
    if (sha(readFileSync(file)) !== expected) throw new Error(`Stale input: ${name}`);
    const reference = hashes(file);
    originals.set(name, reference);
    for (const [category, filter] of Object.entries(categories)) {
      let pair;
      if (category === 'jpeg_aggressive') {
        const jpeg = execFileSync('ffmpeg', [
          '-v', 'error', '-i', file, '-frames:v', '1', '-q:v', '30',
          '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1',
        ], { maxBuffer: 8 * 1024 * 1024 });
        const pipe = (width, height) => execFileSync('ffmpeg', [
          '-v', 'error', '-f', 'mjpeg', '-i', 'pipe:0', '-vf',
          `scale=${width}:${height}:flags=lanczos,format=gray`, '-frames:v', '1',
          '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1',
        ], { input: jpeg, maxBuffer: 1024 * 1024 });
        pair = [phash(pipe(32, 32)), dhash(pipe(9, 8))];
      } else {
        pair = hashes(file, filter);
      }
      measured[category].push([distance(reference[0], pair[0]), distance(reference[1], pair[1])]);
    }
  }
  const all = Object.values(measured).flat();
  const pLimit = Math.max(...all.map(([p]) => p));
  const dLimit = Math.max(...all.map(([, d]) => d));
  const negativeCandidates = [...byCountry].sort(([a], [b]) => a.localeCompare(b)).map(([country, items]) => {
    const a = items[0][0], b = items.at(-1)[0];
    return [country, a, b, distance(originals.get(a)[0], originals.get(b)[0]),
      distance(originals.get(a)[1], originals.get(b)[1])];
  });
  const adjacentCandidates = [];
  for (const [name, digest] of selected) {
    const match = name.match(/^(.*_)(\d{6})(\.jpg)$/);
    if (!match) continue;
    const neighbor = `${match[1]}${String(Number(match[2]) + 1).padStart(6, '0')}${match[3]}`;
    const file = join(root, neighbor);
    if (!existsSync(file)) continue;
    const neighborDigest = sha(readFileSync(file));
    const a = originals.get(name), b = hashes(file);
    adjacentCandidates.push([name, digest, neighbor, neighborDigest,
      distance(a[0], b[0]), distance(a[1], b[1])]);
  }
  const relProducer = 'scripts/datasets/calibrate_v3_grouping.mjs';
  const lines = [
    '# V3 grouping calibration (TRAIN only)', '',
    `Generated UTC: ${new Date().toISOString()}`,
    `Seed: ${seed} (deterministic selection and transforms; no RNG used)`,
    `Producer: \`${relProducer}\` SHA256 \`${sha(readFileSync(join(root, relProducer)))}\``,
    `Decoder: ${execFileSync('ffmpeg', ['-version']).toString().split('\n')[0]}`,
    `Input: \`${relative(root, sourcePath).replaceAll('\\', '/')}\` SHA256 \`${sha(sourceBytes)}\``, '',
    `Selected source images: ${selected.length}; countries: ${[...byCountry.keys()].sort().join(', ')}.`,
    'All inputs are RDD TRAIN proposals; no Frozen Test media was opened.',
    'Positive labels are deterministic transforms of the same source image.',
    'Adjacent RDD indices lack verified same-route labels and are excluded from positive calibration.', '',
    `Proposed conservative AND rule: pHash distance <= ${pLimit} and dHash distance <= ${dLimit}.`,
    'A pair beyond either limit is predicted distinct. These limits are proposals, not group authority.', '',
    '| Positive category | Pairs | Max pHash | Max dHash | False negatives | FN rate |',
    '|---|---:|---:|---:|---:|---:|',
  ];
  for (const [category, pairs] of Object.entries(measured).sort(([a], [b]) => a.localeCompare(b))) {
    const missed = pairs.filter(([p, d]) => p > pLimit || d > dLimit).length;
    lines.push(`| ${category} | ${pairs.length} | ${Math.max(...pairs.map(([p]) => p))} | ` +
      `${Math.max(...pairs.map(([, d]) => d))} | ${missed} | ${(100 * missed / pairs.length).toFixed(1)}% |`);
  }
  lines.push('', '## Same-country negative candidates', '',
    'Distinct bytes and source IDs do not certify different scenes. City and route labels are absent.',
    'These pairs are exploratory and are excluded from any confirmed false-positive rate.', '',
    '| Country | Image A | Image B | pHash distance | dHash distance |',
    '|---|---|---|---:|---:|');
  for (const [country, a, b, p, d] of negativeCandidates) lines.push(`| ${country} | \`${a}\` | \`${b}\` | ${p} | ${d} |`);
  lines.push('', '## Adjacent-index RDD candidates', '',
    'Consecutive indices do not prove the same route. These pairs are measured but excluded',
    'from positive false-negative rates until scene evidence is adjudicated.', '',
    '| Image A | Image B | pHash distance | dHash distance |',
    '|---|---|---:|---:|');
  for (const [a, , b, , p, d] of adjacentCandidates) {
    lines.push(`| \`${a}\` | \`${b}\` | ${p} | ${d} |`);
  }
  if (!adjacentCandidates.length) lines.push('| none available in local TRAIN | — | — | — |');
  lines.push('', 'GROUP_GATE=NO', '',
    'Reason: verified adjacent same-route positives, confirmed same-country negative labels,',
    'owner authority, and group adjudication are unavailable.', '', '## Source images', '');
  for (const [name, digest] of selected) lines.push(`- \`${name}\` SHA256 \`${digest}\``);
  for (const [, , name, digest] of adjacentCandidates) {
    lines.push(`- \`${name}\` SHA256 \`${digest}\` (adjacent candidate)`);
  }
  const body = lines.join('\n') + '\n';
  writeFileSync(outputPath, body + `\nBody SHA256 (excluding this line): \`${sha(body)}\`\n`);
}

main();
