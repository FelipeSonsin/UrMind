"""Launch the authorized four-class experimental smoke, then one main run.

Run this helper detached on Windows. It never uses a TEST manifest and never
initializes the main process from the smoke checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
CONTRACT = ROOT / "datasets/metadata/yolox_model_experimental_20260925.json"
DATA_REPORT = ROOT / "datasets/reports/detection_experimental_20260925.json"
PRETRAINED = ROOT / "models/pretrained/yolox_s.pth"
RUN_ID = "yolox-s-rdd4-experimental-20260925"
RUN_DIR = ROOT / "models/checkpoints/experimental_20260925_rdd4"
STATE = RUN_DIR / "run_state.json"
RAM_FLOOR_BYTES = 2_000_000_000  # Must equal preflight_training.MIN_SMOKE_RAM_BYTES.
EXPECTED_PRETRAINED_SHA256 = "f55ded7181e1b0c13285c56e7790b8f0e8f8db590fe4edb37f0b7f345c913a30"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_state(document: dict) -> None:
    document["heartbeat_at"] = now()
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, STATE)


def claim_run() -> None:
    """Atomically reserve the run directory before the potentially slow preflight."""
    payload = {"run_id": RUN_ID, "status": "CLAIMED", "launcher_pid": os.getpid(), "claimed_at": now()}
    descriptor = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())


def memory_gate(*, user_override: bool = False) -> int:
    """Measure the project floor after PyTorch import; record explicit user override."""
    measurement = subprocess.run(
        [sys.executable, "-B", "scripts/datasets/preflight_training.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    available = int(json.loads(measurement.stdout)["hardware"]["ram_available_bytes"])
    if available < RAM_FLOOR_BYTES and not user_override:
        raise RuntimeError(f"RAM_AVAILABLE={available} below cautious floor={RAM_FLOOR_BYTES}")
    return available


def command(action: str) -> list[str]:
    return [
        sys.executable, "-u", "-m", "app.ml.training", action,
        "--pretrained", str(PRETRAINED), "--contract", str(CONTRACT),
    ]


def stop_training_tree(pid: int) -> None:
    """Stop only this launcher's training process and its Windows venv child."""
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    processes = root.children(recursive=True) + [root]
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=10)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass


def main() -> int:
    global CONTRACT, RUN_ID, RUN_DIR, STATE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-low-ram-override", action="store_true")
    parser.add_argument("--contract", type=Path, default=CONTRACT)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--max-wall-time-seconds", type=int)
    args = parser.parse_args()
    CONTRACT = args.contract.resolve()
    RUN_ID = args.run_id
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    RUN_DIR = (ROOT / contract["training"]["output_directory"]).resolve()
    if not RUN_DIR.is_relative_to(ROOT / "models/checkpoints"):
        raise ValueError("output_directory must stay under models/checkpoints")
    STATE = RUN_DIR / "run_state.json"
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    claim_run()
    state: dict = {"run_id": RUN_ID, "status": "CLAIMED", "launcher_pid": os.getpid()}
    try:
        return execute_claimed_run(
            state,
            user_override=args.user_low_ram_override,
            budget_seconds=args.max_wall_time_seconds,
        )
    except Exception as exc:
        state["status"] = "FAILED_BEFORE_OR_DURING_MAIN"
        state["error"] = f"{type(exc).__name__}: {exc}"
        write_state(state)
        raise


def execute_claimed_run(
    state: dict, *, user_override: bool, budget_seconds: int | None
) -> int:
    report = json.loads(DATA_REPORT.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if budget_seconds is not None and not 0 < budget_seconds <= 36_000:
        raise ValueError("wall-time budget must be within the user's 10-hour limit")
    budget_started_utc = datetime.now(timezone.utc)
    deadline = time.monotonic() + budget_seconds if budget_seconds is not None else None
    train = ROOT / contract["dataset"]["manifests"]["TRAIN"]
    if report["status"] != "EXPERIMENTAL_NOT_APPROVED_FOR_SERVING" or sha256(train) != report["derived_train_sha256"]:
        raise RuntimeError("experimental data report/manifest mismatch")
    if sha256(PRETRAINED) != EXPECTED_PRETRAINED_SHA256:
        raise RuntimeError("official YOLOX-S pretrained checksum mismatch")
    if contract["class_names"] != ["D00", "D10", "D20", "D40"]:
        raise RuntimeError("experimental class order mismatch")
    initial_ram = memory_gate(user_override=user_override)
    relevant_diff = subprocess.check_output(
        ["git", "diff", "--", "backend/app/ml/training.py", "scripts/datasets/build_detection_manifests.py", "datasets/metadata/artifact_contract.yaml"],
        cwd=ROOT,
    )
    state.update({
        "run_id": RUN_ID,
        "status": "SMOKE_STARTING",
        "started_at": now(),
        "launcher_pid": os.getpid(),
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "dirty_relevant_diff_sha256": hashlib.sha256(relevant_diff).hexdigest(),
        "training_source_sha256": sha256(BACKEND / "app/ml/training.py"),
        "launcher_source_sha256": sha256(Path(__file__)),
        "contract_sha256": sha256(CONTRACT),
        "train_manifest_sha256": sha256(train),
        "pretrained_sha256": EXPECTED_PRETRAINED_SHA256,
        "initial_ram_available_after_torch_bytes": initial_ram,
        "ram_floor_bytes": RAM_FLOOR_BYTES,
        "ram_floor_met": initial_ram >= RAM_FLOOR_BYTES,
        "user_low_ram_override": user_override,
        "user_override_source": "explicit request to start with current PC state, 2026-09-25" if user_override else None,
        "training_classes": contract["class_names"],
        "train_images": report["train_images"],
        "production_approval": "BLOCKED",
        "user_limit_seconds": 36_000 if budget_seconds is not None else None,
        "max_wall_time_seconds": budget_seconds,
        "deadline_at": (
            (budget_started_utc + timedelta(seconds=budget_seconds)).isoformat()
            if budget_seconds is not None else None
        ),
        "commands": {"smoke": command("--smoke"), "main": command("--run")},
    })
    write_state(state)
    env = dict(os.environ, PYTHONPATH=str(BACKEND))
    with (RUN_DIR / "smoke.stdout.log").open("w", encoding="utf-8") as out, (RUN_DIR / "smoke.stderr.log").open("w", encoding="utf-8") as err:
        smoke = subprocess.run(command("--smoke"), cwd=BACKEND, env=env, stdout=out, stderr=err, timeout=300, check=False)
    state["smoke_exit_code"] = smoke.returncode
    if smoke.returncode != 0 or not (RUN_DIR / "smoke.pt").is_file():
        raise RuntimeError(f"smoke failed: exit={smoke.returncode}; see smoke.stderr.log")
    state["status"] = "SMOKE_PASSED_MAIN_STARTING"
    state["smoke_checkpoint_sha256"] = sha256(RUN_DIR / "smoke.pt")
    state["ram_available_after_torch_before_main_bytes"] = memory_gate(user_override=user_override)
    if sha256(CONTRACT) != state["contract_sha256"] or sha256(train) != state["train_manifest_sha256"]:
        raise RuntimeError("config or TRAIN manifest changed between smoke and main")
    if sha256(PRETRAINED) != state["pretrained_sha256"]:
        raise RuntimeError("official pretrained changed between smoke and main")
    write_state(state)
    with (RUN_DIR / "train.stdout.log").open("w", encoding="utf-8") as out, (RUN_DIR / "train.stderr.log").open("w", encoding="utf-8") as err:
        process = subprocess.Popen(command("--run"), cwd=BACKEND, env=env, stdout=out, stderr=err)
        state["training_pid"] = process.pid
        state["status"] = "MAIN_PROCESS_RUNNING"
        write_state(state)
        while process.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                stop_training_tree(process.pid)
                state["training_exit_code"] = process.wait(timeout=15)
                state["status"] = "TIME_BUDGET_STOPPED"
                state["stop_reason"] = "USER_10_HOUR_LIMIT"
                write_state(state)
                return 124
            time.sleep(15)
            state["ram_available_bytes"] = psutil.virtual_memory().available
            write_state(state)
        state["training_exit_code"] = process.returncode
        state["status"] = "COMPLETED" if process.returncode == 0 else "FAILED"
        write_state(state)
        return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
