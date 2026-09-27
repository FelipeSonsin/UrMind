# UrMind

Aplicação para registrar problemas urbanos com foto e localização, revisar relatos e publicar ocorrências confirmadas no mapa.

## Estrutura

- `backend/`: API FastAPI, trabalhador da fila de capturas e migrações.
- `frontend/`: aplicação React para registro, revisão e consulta pública.
- `supabase/`: configuração e migrações do banco e armazenamento.

## Desenvolvimento local

```powershell
cd backend
python -m pip install -e .
python -m uvicorn app.main:app --reload
```

```powershell
cd frontend
npm ci
npm run build
```

A câmera remota pode enviar um quadro como rascunho. A análise e a publicação dependem de revisão humana.
