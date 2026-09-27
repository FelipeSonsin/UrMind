# UrMind

Aplicação para registrar problemas urbanos com foto e localização, revisar relatos e publicar ocorrências confirmadas no mapa.

## Estrutura

- `backend/`: API FastAPI, revisão humana de capturas e migrações.
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

## Planejamento de IA

O próximo ciclo prevê **YOLO11 com treinamento e avaliação no Roboflow** para candidatos visuais e **XGBoost** para análise tabular de severidade ou risco. Esses modelos ainda não foram treinados nem ativados no UrMind. O fluxo atual continua com revisão humana. O escopo e os critérios de ativação estão no [Master Plan](docs/Planinng/MASTER_PLAN.md).
