import { describeCameraError } from '../domain/liveDetection';

/** A câmera é sempre a do aparelho que abriu o navegador; nada passa pelo servidor. */
export function cameraEnvironment() {
  return {
    secure: globalThis.isSecureContext !== false,
    supported: Boolean(globalThis.navigator?.mediaDevices?.getUserMedia),
  };
}

export class CameraError extends Error {
  constructor(
    readonly code: ReturnType<typeof describeCameraError>['code'],
    message: string,
  ) {
    super(message);
  }
}

/**
 * Tamanho preferido, nunca exigido: sem ele muitos navegadores escolhem 640×480,
 * cujo lado menor reprova a política de foto; com `exact` câmeras compatíveis falhariam.
 */
export async function openCamera(deviceId?: string): Promise<MediaStream> {
  const environment = cameraEnvironment();
  if (!environment.secure || !environment.supported)
    throw new CameraError(...fromDescription(undefined, environment));
  try {
    return await navigator.mediaDevices.getUserMedia({
      video: {
        ...(deviceId
          ? { deviceId: { exact: deviceId } }
          : { facingMode: { ideal: 'environment' } }),
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
      audio: false,
    });
  } catch (reason) {
    throw new CameraError(...fromDescription(reason, environment));
  }
}

function fromDescription(
  reason: unknown,
  environment: ReturnType<typeof cameraEnvironment>,
): [ReturnType<typeof describeCameraError>['code'], string] {
  const { code, message } = describeCameraError(reason, environment);
  return [code, message];
}

export function stopStream(stream: MediaStream | null | undefined): void {
  stream?.getTracks().forEach((track) => track.stop());
}

/** Rótulos só aparecem depois da permissão; por isso a lista é relida após abrir a câmera. */
export async function listCameras(): Promise<MediaDeviceInfo[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return [];
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((device) => device.kind === 'videoinput');
}

/** Quadro puro (sem overlay) em JPEG, pronto para o fluxo canônico de rascunho. */
export function frameToJpeg(
  source: CanvasImageSource,
  width: number,
  height: number,
  filename: string,
  capturedAt: number,
): Promise<File> {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  canvas.getContext('2d')?.drawImage(source, 0, 0, width, height);
  return new Promise((resolve, reject) =>
    canvas.toBlob(
      (blob) => {
        canvas.width = 0;
        canvas.height = 0;
        if (blob)
          resolve(new File([blob], filename, { type: 'image/jpeg', lastModified: capturedAt }));
        else reject(new Error('Não foi possível capturar a imagem.'));
      },
      'image/jpeg',
      0.92,
    ),
  );
}
