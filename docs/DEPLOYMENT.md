# Deploy com CUDA e RTSP de Cliente

## Decisao Principal

O frontend apenas envia a URL RTSP para o backend. Quem abre o RTSP e processa o video e o servidor FastAPI/YOLO.

Por isso, para o cliente testar com cameras da rede dele, o backend precisa conseguir chegar no endereco RTSP da camera.

## Cenarios Recomendados

### 1. Demo no local do cliente

Melhor caminho para prova real.

- Levar um notebook/mini PC com GPU NVIDIA, Docker e NVIDIA Container Toolkit.
- Conectar esse equipamento na rede do cliente.
- Rodar o container do MVP.
- Abrir `http://IP_DO_EQUIPAMENTO:8088`.
- Colar o RTSP da camera local do cliente.

Vantagens:

- Nao exige expor camera para internet.
- Baixa latencia.
- Demonstra com cameras reais.
- Menos friccao com firewall.

### 2. Deploy em servidor do cliente

Bom para piloto de alguns dias.

- Instalar Docker + NVIDIA Container Toolkit em uma maquina do cliente.
- Subir `docker compose up -d --build`.
- Acessar pela LAN do cliente ou por VPN/Tailscale.

### 3. Seu PC acessando remoto por Tailscale/Artemis

Funciona somente se o seu PC conseguir rotear ate o RTSP da camera do cliente.

Abrir o frontend no seu PC remotamente nao basta. O RTSP nao e aberto pelo navegador; ele e aberto pelo backend.

Para esse cenario funcionar, precisa de pelo menos um destes:

- Seu PC conectado a uma VPN que alcance a rede das cameras do cliente.
- Tailscale/subnet router no cliente anunciando a sub-rede das cameras.
- Porta RTSP exposta com NAT/firewall, o que geralmente nao e recomendado.
- Um agente/container do MVP rodando dentro da rede do cliente.

## Recomendacao Pratica

Para demonstracoes comerciais, use o MVP em Docker no local do cliente ou em uma maquina dentro da rede dele. Acesse a interface por Tailscale ou pela LAN, mas mantenha o processamento perto das cameras.

## Pre-requisitos GPU

- Driver NVIDIA instalado no host.
- Docker Engine.
- NVIDIA Container Toolkit.
- GPU visivel no host:

```bash
nvidia-smi
```

- GPU visivel no Docker:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## Subir o MVP

```bash
docker compose up -d --build
```

Abrir localmente:

```text
http://127.0.0.1:8088
```

Em outra maquina da mesma rede:

```text
http://IP_DO_SERVIDOR:8088
```

## Logs

```bash
docker compose logs -f
```

## Parar

```bash
docker compose down
```

## Modelo YOLO

Por padrao:

```text
VMS_YOLO_MODEL=yolov8n.pt
```

Na primeira execucao, o Ultralytics pode baixar o modelo publico. Para ambiente sem internet, coloque o `.pt` em `./models` e altere o compose:

```yaml
VMS_YOLO_MODEL: /app/models/yolov8n.pt
```

## URLs RTSP

Exemplos comuns:

```text
rtsp://usuario:senha@192.168.1.50:554/stream1
rtsp://usuario:senha@192.168.1.50:554/cam/realmonitor?channel=1&subtype=0
rtsp://usuario:senha@192.168.1.50:554/Streaming/Channels/101
```

Use substream quando a rede ou a GPU estiverem limitadas. Para demo de analytics, 720p costuma ser suficiente.

