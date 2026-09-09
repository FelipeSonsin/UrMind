import { useEffect, useRef, useState } from 'react';

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
        if (!navigator.mediaDevices?.getUserMedia)
          throw new Error('A câmera exige HTTPS ou localhost e um navegador compatível.');
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' } },
          audio: false,
        });
        if (disposed) {
          stream.getTracks().forEach((track) => track.stop());
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
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, []);
  function capture() {
    const frame = video.current;
    if (!frame?.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = frame.videoWidth;
    canvas.height = frame.videoHeight;
    canvas.getContext('2d')?.drawImage(frame, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (blob) onCapture(new File([blob], 'captura.jpg', { type: 'image/jpeg' }));
        else setError('Não foi possível capturar a imagem.');
      },
      'image/jpeg',
      0.92,
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
