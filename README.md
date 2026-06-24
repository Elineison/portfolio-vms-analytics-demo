# Portfolio VMS Analytics MVP

MVP autoral para demonstrar analise de video CFTV por RTSP.

Os sistemas usados como referencia operacional usam SDKs/sidecars especificos por fabricante. Este MVP usa RTSP de proposito para facilitar demonstracao em portfolio, reduzir dependencias e permitir testar com cameras, NVRs ou streams de exemplo de diferentes marcas.

Importante: quem acessa o RTSP e o backend, nao o navegador. Para testar cameras do cliente, rode o backend dentro da rede do cliente ou garanta VPN/roteamento ate a rede das cameras.

O sistema permite cadastrar uma camera por URL RTSP, abrir preview via WebSocket e configurar duas analises:

- Intrusao fora do horario permitido em uma area da imagem.
- Grupo parado/conversando: 3 ou mais pessoas dentro da area por mais de um tempo configuravel.

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

Acesse:

```text
http://127.0.0.1:8088
```

Para acessar pela rede:

```text
http://IP_DO_SERVIDOR:8088
```

Veja mais em [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Detector

Por padrao, se `ultralytics` estiver instalado e houver modelo disponivel, o backend usa YOLO para detectar pessoas. Caso contrario, o sistema continua subindo e mostra preview/ROI, mas as deteccoes ficam vazias.

Para usar YOLO:

```bash
pip install -r requirements-yolo.txt
export VMS_YOLO_MODEL=yolov8n.pt
```

O arquivo `.pt` nao deve ser versionado.

Na primeira execucao com `yolov8n.pt`, o Ultralytics pode baixar o modelo publico automaticamente. Para demonstracoes sem internet, coloque o arquivo do modelo localmente e aponte `VMS_YOLO_MODEL=/caminho/modelo.pt`.

## Variaveis uteis

```text
VMS_DATA_DIR=./data
VMS_SESSION_TIMEOUT_S=300
VMS_YOLO_MODEL=yolov8n.pt
VMS_ANALYSIS_FPS=2
```

## Principios importados do ecossistema real

- Preview/live por WebSocket.
- Sessoes de preview com limite de 300 segundos.
- ROI persistida em coordenadas relativas ao frame.
- Analytics independente do frontend.
- Backend e a fonte da verdade para eventos.
- Nenhuma credencial, SDK proprietario ou dado real no repositorio publico.
