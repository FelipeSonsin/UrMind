import { useEffect, useRef, useState } from 'react';
import { frameToJpeg, openCamera, stopStream } from '../services/cameraStream';

export function Camera({
  onCapture,
  onClose,
}: {
  onCapture: (file: File) => void;
  onClose: () => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    let stream: MediaStream | undefined;
    async function open() {
      try {
        stream = await openCamera();
        if (disposed) {
          stopStream(stream);
          return;
        }
        if (video.current) {
          video.current.srcObject = stream;
          await video.current.play();
          if (!disposed) setReady(true);
        }
      } catch (reason) {
        if (!disposed)
          setError(reason instanceof Error ? reason.message : 'Não foi possível abrir a câmera.');
      }
    }
    void open();
    return () => {
      disposed = true;
      stopStream(stream);
    };
  }, []);
  function capture() {
    const frame = video.current;
    if (!frame?.videoWidth) return;
    if (Math.min(frame.videoWidth, frame.videoHeight) < 640) {
      setError(
        `a imagem desta câmera é pequena demais (${frame.videoWidth} × ${frame.videoHeight} px). Use “Escolher foto” para enviar uma foto já tirada.`,
      );
      return;
    }
    frameToJpeg(frame, frame.videoWidth, frame.videoHeight, 'captura.jpg', Date.now()).then(
      onCapture,
      (reason: Error) => setError(reason.message),
    );
  }
  return (
    <section className="camera panel" aria-label="Câmera">
      <video ref={video} playsInline muted aria-label="Prévia da câmera" />
      {error && <p role="alert">Câmera indisponível: {error}</p>}
      <div className="actions">
        <button type="button" disabled={!ready} onClick={capture}>
          Fotografar
        </button>
        <button className="secondary" type="button" onClick={onClose}>
          Fechar câmera
        </button>
      </div>
    </section>
  );
}
