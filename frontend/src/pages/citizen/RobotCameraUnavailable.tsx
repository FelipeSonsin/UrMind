import { Webcam } from 'lucide-react';
import { EmptyState, PageHeader } from '../../components/ui';

/** Câmera do robô sem a configuração do servidor (Supabase Realtime e STUN). */
export function RobotCameraUnavailable() {
  return (
    <>
      <PageHeader title="Câmera do robô" />
      <div className="panel">
        <EmptyState
          icon={<Webcam size={22} strokeWidth={1.8} />}
          title="Esta função não está habilitada neste ambiente"
          action={
            <a className="button secondary" href="#/registrar">
              Registrar relato com o celular
            </a>
          }
        >
          A câmera remota depende de configuração do servidor. Você ainda pode registrar um relato
          com uma foto.
        </EmptyState>
      </div>
    </>
  );
}
