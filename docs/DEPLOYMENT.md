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

Com GPU NVIDIA:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
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

## Evidencias e E-mail

Snapshots de ocorrencia sao salvos em:

```text
./data/events/
```

Para envio real por e-mail, configurar no `docker-compose.yml`:

```yaml
VMS_SMTP_HOST: smtp.exemplo.com
VMS_SMTP_PORT: "587"
VMS_SMTP_USER: usuario
VMS_SMTP_PASSWORD: senha
VMS_SMTP_FROM: vms-demo@exemplo.com
VMS_SMTP_TLS: "1"
```

Se SMTP nao estiver configurado, o sistema salva o e-mail gerado em:

```text
./data/outbox/
```

## Login Google

Criar credenciais OAuth 2.0 no Google Cloud como aplicacao Web.

Redirect URI local:

```text
http://127.0.0.1:8088/auth/google/callback
```

Redirect URI em rede/cliente:

```text
http://IP_DO_SERVIDOR:8088/auth/google/callback
```

Variaveis:

```text
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
VMS_SESSION_SECRET=valor-longo-aleatorio
VMS_ADMIN_TOKEN=valor-longo-aleatorio
```

O sistema usa o e-mail autenticado para envio de alertas da demo. Nao ha campo manual de e-mail por camera.

## LGPD e Separacao por Usuario

Cada usuario autenticado tem:

- Cameras proprias.
- Configuracoes proprias.
- Eventos proprios.
- Snapshots proprios.
- Trial proprio de 7 dias.

O backend filtra cameras, eventos e snapshots por `user_id`. Um usuario nao deve conseguir acessar dados de outro usuario.

Dados armazenados no MVP:

- E-mail e nome do Google.
- URL RTSP informada pelo usuario.
- Configuracoes de ROI e analise.
- Snapshots de ocorrencias geradas durante a demo.

Para demonstracao comercial, informe ao cliente que a URL RTSP e snapshots sao usados somente para executar a demo e podem ser apagados sob solicitacao.

## Resetar Trial ou Apagar Usuario

Resetar mais 7 dias pelo codigo:

```bash
python -m app.admin_cli --data-dir ./data reset-trial cliente@gmail.com --days 7
```

Apagar usuario, cameras, configuracoes e eventos:

```bash
python -m app.admin_cli --data-dir ./data delete-user cliente@gmail.com
```

Via API administrativa:

```bash
curl -X POST \
  -H "X-Admin-Token: $VMS_ADMIN_TOKEN" \
  "http://127.0.0.1:8088/api/admin/users/cliente@gmail.com/reset-trial?days=7"
```

```bash
curl -X DELETE \
  -H "X-Admin-Token: $VMS_ADMIN_TOKEN" \
  "http://127.0.0.1:8088/api/admin/users/cliente@gmail.com"
```
