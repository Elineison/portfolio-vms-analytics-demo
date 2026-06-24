# AVM - Analise de Video para Monitoramento

MVP autoral para demonstrar analise de video CFTV por RTSP.

Os sistemas usados como referencia operacional usam SDKs/sidecars especificos por fabricante. Este MVP usa RTSP de proposito para facilitar demonstracao em portfolio, reduzir dependencias e permitir testar com cameras, NVRs ou streams de exemplo de diferentes marcas.

Importante: quem acessa o RTSP e o backend, nao o navegador. Para testar cameras do cliente, rode o backend dentro da rede do cliente ou garanta VPN/roteamento ate a rede das cameras.

O sistema permite cadastrar uma camera por URL RTSP, abrir preview via WebSocket e configurar duas analises:

- Intrusao fora do horario permitido em uma area da imagem.
- Grupo parado/conversando: 3 ou mais pessoas dentro da area por mais de um tempo configuravel.
- Snapshot JPEG da ocorrencia como evidencia visual.
- Envio opcional da evidencia por e-mail quando SMTP estiver configurado.

Este projeto foi desenhado para portfolio e demonstracao comercial. Ele nao depende de SDK proprietario, bancos reais, credenciais da Locktec, modelos privados ou dados de producao.

## Stack

- FastAPI
- OpenCV
- WebSocket JPEG
- Ultralytics YOLO opcional
- Frontend vanilla HTML/CSS/JS
- Docker com CUDA para demo com GPU NVIDIA

## Rodando localmente

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8088
```

Acesse:

```text
http://127.0.0.1:8088
```

Para testar a tela de entrada/cadastro Google sem depender do login durante o desenvolvimento:

```text
http://127.0.0.1:8088/?login=preview
```

## Rodando com Docker + CUDA

Pre-requisitos no host:

- Driver NVIDIA instalado.
- Docker Engine.
- NVIDIA Container Toolkit.
- `nvidia-smi` funcionando.

Suba o MVP:

```bash
docker compose up -d --build
```

Com GPU NVIDIA:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Acesse:

```text
http://127.0.0.1:8088
```

Para acessar pela rede:

```text
http://IP_DO_SERVIDOR:8088
```

Veja mais em [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Login, LGPD e Trial

O MVP usa login Google via OpenID Connect/OAuth. Configure:

```text
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
VMS_SESSION_SECRET=valor-longo-aleatorio
VMS_ADMIN_TOKEN=valor-longo-aleatorio
VMS_SMTP_HOST=smtp.gmail.com
VMS_SMTP_PORT=587
VMS_SMTP_USER=seu-email@gmail.com
VMS_SMTP_PASSWORD=senha-de-app-do-google
VMS_SMTP_FROM=seu-email@gmail.com
VMS_SMTP_TLS=1
```

Use `.env.example` como base, sem versionar o `.env` real.

Cada e-mail autenticado tem cameras, configuracoes, eventos e snapshots separados. O trial padrao e de 7 dias por e-mail.

Para testar o fluxo real do usuario, configure `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` e SMTP. Nao use `VMS_DEV_AUTH_EMAIL` na demo real, porque ele cria uma sessao local e pula o login Google.

Em desenvolvimento, `VMS_DEV_AUTH_EMAIL=seu-email@gmail.com` ainda pode ser usado apenas para validar preview, ROI e analises rapidamente.

Resetar trial:

```bash
python -m app.admin_cli --data-dir ./data reset-trial cliente@gmail.com --days 7
```

Apagar usuario e permitir novo cadastro limpo:

```bash
python -m app.admin_cli --data-dir ./data delete-user cliente@gmail.com
```

## Detector

Por padrao, se `ultralytics` estiver instalado e houver modelo disponivel, o backend usa YOLO para detectar pessoas. Caso contrario, o sistema continua subindo e mostra preview/ROI, mas as deteccoes ficam vazias.

Para usar YOLO:

```bash
pip install -r requirements-yolo.txt
export VMS_YOLO_MODEL=yolov8n.pt
```

O arquivo `.pt` nao deve ser versionado.

Na primeira execucao com `yolov8n.pt`, o Ultralytics pode baixar o modelo publico automaticamente. Para demonstracoes sem internet, coloque o arquivo do modelo localmente e aponte `VMS_YOLO_MODEL=/caminho/modelo.pt`.

## Banco de dados

O MVP atual persiste dados em `data/store.json` para acelerar desenvolvimento local. Isso e suficiente para testes de bancada, mas nao e o ideal para uma demo autenticada com varios clientes.

Recomendacao para demo real:

- Usar PostgreSQL local via Docker Compose.
- Manter usuarios, cameras, configuracoes, trial, eventos e snapshots indexados por `user_id`.
- Deixar snapshots no volume `data/events/` ou storage equivalente, salvando no banco apenas metadados e caminho do arquivo.

SQLite tambem funcionaria para uma demo local simples, mas PostgreSQL deixa o sistema mais proximo de um produto SaaS multiusuario.

## Variaveis uteis

```text
VMS_DATA_DIR=./data
VMS_SESSION_TIMEOUT_S=300
VMS_YOLO_MODEL=yolov8n.pt
VMS_ANALYSIS_FPS=2
VMS_YOLO_DEVICE=auto
VMS_SESSION_SECRET=troque-este-valor
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
VMS_SMTP_HOST=smtp.gmail.com
VMS_SMTP_PORT=587
VMS_SMTP_USER=seu-email@gmail.com
VMS_SMTP_PASSWORD=senha-de-app-do-google
VMS_SMTP_FROM=seu-email@gmail.com
VMS_SMTP_TLS=1
```

## Evidencias e e-mail

Quando uma regra dispara, o backend salva um snapshot com overlay em:

```text
data/events/
```

Se SMTP estiver configurado, o sistema envia a evidencia para o e-mail autenticado pelo Google. Configure:

```text
VMS_SMTP_HOST=smtp.gmail.com
VMS_SMTP_PORT=587
VMS_SMTP_USER=seu-email@gmail.com
VMS_SMTP_PASSWORD=senha-de-app-do-google
VMS_SMTP_FROM=seu-email@gmail.com
VMS_SMTP_TLS=1
```

No Gmail, use uma senha de app da conta remetente. Nao use a senha normal da conta Google.

Recomendacao para demo: use um e-mail remetente exclusivo do sistema, por exemplo `avm.demo@suaempresa.com` ou uma conta Google Workspace/Gmail criada so para o AVM. O cliente continua autenticando com o Google dele; o sistema apenas usa esse remetente SMTP para enviar alertas ao e-mail autenticado do cliente.

Sem SMTP configurado, a mensagem fica salva como `.eml` em:

```text
data/outbox/
```

## Principios importados do ecossistema real

- Preview/live por WebSocket.
- Sessoes de preview com limite de 300 segundos.
- ROI persistida em coordenadas relativas ao frame.
- Analytics independente do frontend.
- Backend e a fonte da verdade para eventos.
- Nenhuma credencial, SDK proprietario ou dado real no repositorio publico.
