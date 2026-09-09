"""Simula orçamento antes de uma operação; nunca baixa, extrai, copia ou exclui."""

import argparse
import json
import math

from _budget import GB, preflight
from _core import configure_stdout


def main():
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", default="planejamento")
    parser.add_argument("--delta-gb", type=float, default=0)
    parser.add_argument("--peak-gb", type=float, default=0)
    parser.add_argument("--dataset", default=None)
    args = parser.parse_args()
    if any(not math.isfinite(v) or v < 0 for v in (args.delta_gb, args.peak_gb)):
        parser.error("delta e pico precisam ser números finitos não negativos")
    result = preflight(
        args.operation,
        math.ceil(args.delta_gb * GB),
        math.ceil(args.peak_gb * GB),
        dataset_id=args.dataset,
    )
    print(
        json.dumps(
            {"mode": "dry-run-only", "units": "bytes; GB decimal", **result.as_dict()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.allowed else 2


if __name__ == "__main__":
    raise SystemExit(main())
