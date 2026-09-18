"""Gateway do Scout: da câmera até o núcleo do UrMind (MASTER_PLAN §21.1).

O Gateway roda perto do Scout, pega o quadro da câmera, carimba identidade,
sessão e instante, e entrega ao mesmo endpoint que a PWA usa
(`POST /api/v1/captures/photo`). Ele não tem banco, não tem fila e não decide
nada sobre o que está na imagem — quem faz isso é o núcleo, como já faz para a
foto de operador.

    python -m app.gateway --device SCOUT-01 --source http://192.168.0.50/capture \
        --interval 5 --token <jwt>            # envia enquanto houver quadro
    python -m app.gateway --device SCOUT-01 --source ... --once   # um quadro só

Sem `--source`, nada é inventado: o Gateway informa `no_device` e sai.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import sys
from datetime import UTC, datetime
from typing import Protocol

import httpx
import structlog

from app.schemas.core import CaptureSource, Coordinate, LocationSource
from app.schemas.scout import (
    CameraFrameEnvelope,
    DeviceKind,
    FrameQuality,
    GatewayStatus,
    MissionRef,
    ScoutIdentity,
    ScoutState,
)

log = structlog.get_logger()

GATEWAY_VERSION = "gateway-0.1"
CLOCK_GATEWAY = "gateway_receipt"


class ScoutCameraError(RuntimeError):
    pass


class ScoutCameraSource(Protocol):
    """Fonte de quadros do Scout.

    Hoje existe uma implementação: a ESP32-CAM, que serve um JPEG por HTTP na
    rede local (§0.1, §21.1). Outra fonte entra como outra implementação desta
    interface — não como um segundo caminho dentro do Gateway.
    """

    name: str

    async def frame(self) -> tuple[bytes, FrameQuality]:
        """Devolve os bytes do quadro e o que dá para afirmar sobre ele."""
        ...


class HttpSnapshotSource:
    """ESP32-CAM: um GET devolve o quadro atual em JPEG."""

    def __init__(self, url: str, client: httpx.AsyncClient, timeout: float = 10.0) -> None:
        self.url = url
        self.name = url
        self._client = client
        self._timeout = timeout

    async def frame(self) -> tuple[bytes, FrameQuality]:
        started = asyncio.get_running_loop().time()
        try:
            response = await self._client.get(self.url, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise ScoutCameraError(f"câmera inacessível: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise ScoutCameraError(f"câmera respondeu HTTP {response.status_code}")
        data = response.content
        if not data:
            raise ScoutCameraError("câmera devolveu quadro vazio")
        elapsed_ms = (asyncio.get_running_loop().time() - started) * 1000
        return data, describe_frame(data, elapsed_ms)


def describe_frame(data: bytes, fetch_latency_ms: float | None = None) -> FrameQuality:
    """Mede o quadro. Dimensão só é afirmada quando a imagem é decodificável."""
    from PIL import Image, UnidentifiedImageError

    width = height = None
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError):
        # Quadro ilegível continua sendo enviado: quem recusa conteúdo inválido
        # é o núcleo, com a mesma validação que aplica à foto de operador.
        log.warning("scout_frame_undecodable", bytes=len(data))
    return FrameQuality(
        width=width,
        height=height,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        fetch_latency_ms=round(fetch_latency_ms, 1) if fetch_latency_ms is not None else None,
    )


def envelope_for(
    *,
    device: ScoutIdentity,
    mission: MissionRef | None,
    quality: FrameQuality,
    coordinate: Coordinate | None,
    sequence: int,
) -> CameraFrameEnvelope:
    """Carimba o quadro com o que o Gateway sabe de verdade neste momento.

    Enquanto o TIMEPULSE do NEO-M8N não estiver ligado (§21.2), o instante é o
    do recebimento no Gateway — e `clock_source` registra isso, para ninguém
    tratar o carimbo como hora do GPS.
    """
    return CameraFrameEnvelope(
        device=device,
        mission=mission,
        captured_at=datetime.now(UTC),
        clock_source=CLOCK_GATEWAY,
        sequence=sequence,
        coordinate=coordinate,
        quality=quality,
    )


class CoreClient:
    """Envio ao núcleo pelo mesmo endpoint da PWA, com o mesmo Auth."""

    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = client

    async def send(self, data: bytes, frame: CameraFrameEnvelope) -> dict:
        form: dict[str, str] = {"source": CaptureSource.SCOUT.value}
        if frame.coordinate is not None:
            form["latitude"] = str(frame.coordinate.latitude)
            form["longitude"] = str(frame.coordinate.longitude)
            form["location_source"] = LocationSource.GPS_SCOUT.value
            if frame.coordinate.accuracy_m is not None:
                form["accuracy_m"] = str(frame.coordinate.accuracy_m)
        response = await self._client.post(
            f"{self.base_url}/api/v1/captures/photo",
            headers={"Authorization": f"Bearer {self._token}"},
            files={"file": (f"{frame.device.code}-{frame.sequence}.jpg", data, "image/jpeg")},
            data=form,
            timeout=30.0,
        )
        if response.status_code >= 400:
            raise ScoutCameraError(f"núcleo recusou o quadro: HTTP {response.status_code}")
        return response.json()


async def run(
    *,
    source: ScoutCameraSource | None,
    core: CoreClient | None,
    device: ScoutIdentity,
    mission: MissionRef | None,
    interval_s: float,
    once: bool,
) -> GatewayStatus:
    """Laço do Gateway. Sem câmera ou sem núcleo, o estado é declarado e pronto."""
    status = GatewayStatus(
        gateway_version=GATEWAY_VERSION,
        camera_source=source.name if source else None,
        scout_state=ScoutState.NO_DEVICE if source is None else ScoutState.OFFLINE,
    )
    if source is None or core is None:
        status.last_error = (
            "nenhuma fonte de câmera configurada" if source is None else "sem núcleo"
        )
        return status
    sequence = 0
    while True:
        try:
            data, quality = await source.frame()
            frame = envelope_for(
                device=device,
                mission=mission,
                quality=quality,
                coordinate=None,
                sequence=sequence,
            )
            await core.send(data, frame)
            sequence += 1
            status.frames_sent += 1
            status.last_frame_at = frame.captured_at
            status.scout_state = ScoutState.LIVE
            status.last_error = None
        except ScoutCameraError as exc:
            status.frames_failed += 1
            status.last_error = str(exc)
            # Um quadro perdido não derruba a sessão: o Scout continua andando.
            status.scout_state = ScoutState.DEGRADED if status.frames_sent else ScoutState.OFFLINE
        if once:
            return status
        await asyncio.sleep(interval_s)


async def _main(args: argparse.Namespace) -> int:
    device = ScoutIdentity(
        code=args.device, kind=DeviceKind.ESP32_CAM, firmware_version=args.firmware
    )
    mission = MissionRef(code=args.mission) if args.mission else None
    async with httpx.AsyncClient() as client:
        source = HttpSnapshotSource(args.source, client) if args.source else None
        core = CoreClient(args.core, args.token, client) if args.source and args.token else None
        status = await run(
            source=source,
            core=core,
            device=device,
            mission=mission,
            interval_s=args.interval,
            once=args.once,
        )
    print(json.dumps(status.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0 if status.scout_state is ScoutState.LIVE else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="código estável do Scout")
    parser.add_argument("--source", default=None, help="URL do quadro da ESP32-CAM")
    parser.add_argument("--core", default="http://127.0.0.1:8000", help="base da API do UrMind")
    # O token não tem opção de linha de comando de propósito: argumento fica no
    # histórico do shell e aparece em `ps` para qualquer usuário da máquina.
    parser.add_argument(
        "--token-env",
        default="URMIND_GATEWAY_TOKEN",
        help="variável de ambiente com o JWT do Supabase",
    )
    parser.add_argument("--mission", default=None)
    parser.add_argument("--firmware", default=None)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    args.token = os.environ.get(args.token_env)
    if args.source and not args.token:
        parser.error(f"defina {args.token_env} com o JWT usado para enviar ao núcleo")
    if sys.platform == "win32":
        import selectors

        return asyncio.run(
            _main(args),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
