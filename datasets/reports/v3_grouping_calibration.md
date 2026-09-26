# V3 grouping calibration (TRAIN only)

Generated UTC: 2026-09-25T13:54:14.522Z
Seed: 20260925 (deterministic selection and transforms; no RNG used)
Producer: `scripts/datasets/calibrate_v3_grouping.mjs` SHA256 `14c0fc14efe55e64be5596f3f057733d2d5477b61b386674aa903f07af97792d`
Decoder: ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
Input: `datasets/reports/review_summary.json` SHA256 `6ae90f60f7e5748102314e3f510449221857f7375ce74292f72debfefcec2b8e`

Selected source images: 12; countries: India, Japan, Norway.
All inputs are RDD TRAIN proposals; no Frozen Test media was opened.
Positive labels are deterministic transforms of the same source image.
Adjacent RDD indices lack verified same-route labels and are excluded from positive calibration.

Proposed conservative AND rule: pHash distance <= 40 and dHash distance <= 34.
A pair beyond either limit is predicted distinct. These limits are proposals, not group authority.

| Positive category | Pairs | Max pHash | Max dHash | False negatives | FN rate |
|---|---:|---:|---:|---:|---:|
| brightness_060 | 12 | 6 | 9 | 0 | 0.0% |
| contrast_160 | 12 | 8 | 13 | 0 | 0.0% |
| crop_10pct | 12 | 14 | 13 | 0 | 0.0% |
| crop_25pct | 12 | 34 | 23 | 0 | 0.0% |
| crop_40pct | 12 | 40 | 34 | 0 | 0.0% |
| jpeg_aggressive | 12 | 0 | 0 | 0 | 0.0% |
| resize_50pct | 12 | 0 | 4 | 0 | 0.0% |

## Same-country negative candidates

Distinct bytes and source IDs do not certify different scenes. City and route labels are absent.
These pairs are exploratory and are excluded from any confirmed false-positive rate.

| Country | Image A | Image B | pHash distance | dHash distance |
|---|---|---|---:|---:|
| India | `datasets/raw/rdd2022/RDD2022/India/train/images/India_009815.jpg` | `datasets/raw/rdd2022/RDD2022/India/train/images/India_000480.jpg` | 28 | 22 |
| Japan | `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_000320.jpg` | `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_009700.jpg` | 22 | 24 |
| Norway | `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_007540.jpg` | `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_007436.jpg` | 24 | 29 |

## Adjacent-index RDD candidates

Consecutive indices do not prove the same route. These pairs are measured but excluded
from positive false-negative rates until scene evidence is adjudicated.

| Image A | Image B | pHash distance | dHash distance |
|---|---|---:|---:|
| `datasets/raw/rdd2022/RDD2022/India/train/images/India_009707.jpg` | `datasets/raw/rdd2022/RDD2022/India/train/images/India_009708.jpg` | 34 | 23 |
| `datasets/raw/rdd2022/RDD2022/India/train/images/India_000480.jpg` | `datasets/raw/rdd2022/RDD2022/India/train/images/India_000481.jpg` | 32 | 26 |
| `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_011738.jpg` | `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_011739.jpg` | 20 | 18 |
| `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_009700.jpg` | `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_009701.jpg` | 26 | 32 |
| `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_007540.jpg` | `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_007541.jpg` | 28 | 28 |
| `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_003220.jpg` | `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_003221.jpg` | 32 | 25 |
| `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_008030.jpg` | `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_008031.jpg` | 30 | 26 |
| `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_007436.jpg` | `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_007437.jpg` | 32 | 36 |

GROUP_GATE=NO

Reason: verified adjacent same-route positives, confirmed same-country negative labels,
owner authority, and group adjudication are unavailable.

## Source images

- `datasets/raw/rdd2022/RDD2022/India/train/images/India_009815.jpg` SHA256 `aae2455265a430eed4ff1cd2383ebda90038a18c85762f0daa37ed27fc234525`
- `datasets/raw/rdd2022/RDD2022/India/train/images/India_007279.jpg` SHA256 `c9c49333cb6b34eb8359fd7f40d1ca605a3959afdf2f7b1f501418b1fca2f973`
- `datasets/raw/rdd2022/RDD2022/India/train/images/India_009707.jpg` SHA256 `85f467591daec3039f3fe59f97be18407ffb4692567a0f43f8506e41b3776628`
- `datasets/raw/rdd2022/RDD2022/India/train/images/India_000480.jpg` SHA256 `df289a48446a652ac58bc2df980c191d4ce118579a942cbf82b2473215609c3a`
- `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_000320.jpg` SHA256 `f2d9e6261ddf5721d428b0adb9b1c70af1180ab3a3a0048d9b40f688ecc0bfde`
- `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_011738.jpg` SHA256 `4138f55f144cfbdca0ec4347d65d900f30c416b2360bffa19aeb998ffb591887`
- `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_012676.jpg` SHA256 `3d034b18f15845c1615780da9c495737e635c1fc04270ed44ccf9d572f996444`
- `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_009700.jpg` SHA256 `7dcf2d8794fa4139c0c46d4a54ab1828e9ceeef90bcb3978cc8c587d6c78e8f8`
- `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_007540.jpg` SHA256 `1b968a374931ae6c40e4d0a9010bad11c0f7cafca17c81faf8aaf3f7dac7e44d`
- `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_003220.jpg` SHA256 `fa4d4885307f8dfecb024f719d545ef2740c5994e54cd63c31fecc7f75718f21`
- `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_008030.jpg` SHA256 `279f93711676ed3b1784ba3c63d894cdacd2338552f6fce90b561f94132047f7`
- `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_007436.jpg` SHA256 `5a7e19b3972b2577048b142e2ad354ac07619101d7f0bccb9c9e6c92b63a75af`
- `datasets/raw/rdd2022/RDD2022/India/train/images/India_009708.jpg` SHA256 `be1a162bec7849118b7dfbece8e9a69812f8817c71ba8bb79b2a6166e7b8d39a` (adjacent candidate)
- `datasets/raw/rdd2022/RDD2022/India/train/images/India_000481.jpg` SHA256 `7a8993e6f1e5cf91e1c4c4659b62430031d70bdc62a81b9bcbd588519eb10363` (adjacent candidate)
- `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_011739.jpg` SHA256 `65606fbeee11b2aa9b49e0ea19740b3c3dc4e51db653e7673df6316e94a9c969` (adjacent candidate)
- `datasets/raw/rdd2022/RDD2022/Japan/train/images/Japan_009701.jpg` SHA256 `665c6a6e86f22c157e94f4acca9c57d958cb6a9f53004c06c89e15003d118634` (adjacent candidate)
- `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_007541.jpg` SHA256 `f1a9ac085161f953de5ad07b449f78bb8aacf81ef21cff4403dd607ec637b2cd` (adjacent candidate)
- `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_003221.jpg` SHA256 `63b19506aa614bf3f57e6e29e18cf2d77d4c79c039b80765bf9dac482390e96a` (adjacent candidate)
- `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_008031.jpg` SHA256 `628c006299559fb0dde75ec66426f292843c9676f08f4bb60d00cc7455063586` (adjacent candidate)
- `datasets/raw/rdd2022/RDD2022/Norway/train/images/Norway_007437.jpg` SHA256 `8c59245c6610ecbbbdf59d93720f9f76a2741a397f03f0cd73923fa91fd48797` (adjacent candidate)

Body SHA256 (excluding this line): `b87ecf61837c5db9c2761c99845b62a142cac5d45f02345c1fa321d62b00fce4`
