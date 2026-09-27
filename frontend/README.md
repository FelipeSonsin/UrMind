# Frontend UrMind

Aplicação React, TypeScript e Vite para registrar evidências de problemas urbanos, acompanhar relatos, revisar ocorrências e consultar o mapa público.

## Estado atual

- Fotos podem vir do dispositivo, de um arquivo ou de um quadro da câmera remota do robô. O registro preserva a evidência e solicita localização quando ela falta.
- A interface apresenta a situação real do relato, sem criar detecções ou previsões fictícias.
- A revisão humana decide a classe, a localização e a publicação. O navegador **não carrega YOLOX, YOLO11 ou XGBoost** nesta versão.
- Autenticação e dados vêm da API e do Supabase; rascunhos locais usam IndexedDB.

## Plano de IA

O próximo ciclo prevê **YOLO11 treinado e avaliado no Roboflow** para candidatos visuais e **XGBoost** para análise tabular de severidade ou risco. Ambos estão apenas planejados. A interface só poderá mostrar resultados automáticos quando houver dados autorizados, avaliação, integração e ativação aprovadas. O [Master Plan](../docs/Planinng/MASTER_PLAN.md) é a fonte de verdade para esse ciclo.

## Desenvolvimento local

Requisito: Node.js 22.12 ou superior.

```powershell
cd frontend
npm ci
npm run dev
```

Abra http://127.0.0.1:5173. O Vite encaminha `/api` ao backend local quando o proxy está configurado. Use `frontend/.env.example` como referência para variáveis públicas. Não coloque senhas ou chaves privadas em variáveis `VITE_*`.

## Verificação

```powershell
npm test
npm run build
npm run test:e2e
```

Os testes de navegador exigem o navegador e os serviços previstos na configuração do Playwright. O build local valida a aplicação que a Vercel publica a partir do repositório.
