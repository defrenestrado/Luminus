# Luminus — Alterações de 2026-10-22

Arquivo de retomada. Projeto: `/home/ledilson/Documentos/luminus`.

## 1. Estado no momento em que este arquivo foi salvo

- A GPU3 (RTX 3060 Ti, PCI `09:00.0`) caiu/entrou em erro de driver:
  `NVRM: GPU3 ... NV_ERR_GPU_IN_FULLCHIP_RESET`.
- O `nvidia-smi` lista só 3 dispositivos: GPU0 (3060 Ti), GPU1 (3060 Ti) e GPU2 (2060). A terceira 3060 Ti (GPU3) sumiu.
- O helper root foi instalado e configurou:
  - `ollama` principal: `CUDA_VISIBLE_DEVICES=0,1`
  - `ollama-utility`: `CUDA_VISIBLE_DEVICES=2`
- Depois do restart, os dois Ollama subiram **sem GPU** (`inference compute id=cpu`, `total_vram="0 B"`), porque o driver CUDA ainda está em reset. **Só um reboot frio (ou reseat da GPU3) resolve.**
- A API do Luminus está rodando na porta 5111, com `active_tier=two_gpu` e modelo ativo `qwen38-9b-heretic:latest`.
- O fallback DeepSeek está **inativo** porque `DEEPSEEK_API_KEY` está vazia.

## 2. O que foi implementado

### 2.1 Segurança de GPU (`server/gpu_guard.py`)

- Lê `nvidia-smi` a cada 20 s e classifica GPUs saudáveis/ausentes/com erro.
- Escolhe o modelo que cabe na capacidade restante:
  - 3 GPUs principais → `luminus-27b`
  - 2 GPUs → `qwen38-9b-heretic:latest` (Q8)
  - 1 GPU → `qwen38-9b-heretic-q5`
  - 0 GPUs → 2060 utilitária ou CPU
- Histerese: 2 leituras ruins para degradar, 3 boas para recuperar.
- Memória de falha de modelo (15 min) para não tentar o 27B repetidamente.
- Expõe `/api/gpu`, `/api/gpu/check` e `/api/gpu/reset`.
- Pede reinício do Ollama ao helper root quando detecta erro fatal de CUDA.
- No boot, imprime o tier e o motivo.

### 2.2 Helper root de GPUs (systemd)

Instalado pelo usuário:

- `/usr/local/sbin/luminus-gpu-env`
- `/etc/systemd/system/luminus-gpu-env.service`
- `/etc/systemd/system/luminus-gpu-env.timer`
- Config: `/etc/luminus/gpu-guard.conf` (`LUMINUS_APP_USER="ledilson"`)
- Drop-ins gerados:
  - `/etc/systemd/system/ollama.service.d/zz-luminus-gpu-env.conf`
  - `/etc/systemd/system/ollama-utility.service.d/zz-luminus-gpu-env.conf`
- Pasta de pedidos de reinício: `/var/lib/luminus-gpu-guard` (dono `ledilson`)
- Scripts no projeto:
  - `scripts/gpu_guard_env.sh`
  - `scripts/install_gpu_guard.sh`

Comandos úteis:

```bash
sudo /usr/local/sbin/luminus-gpu-env --dry-run
journalctl -u luminus-gpu-env -n 50 --no-pager
systemctl list-timers luminus-gpu-env.timer
```

### 2.3 Narrador de emergência DeepSeek (`server/llm.py`)

- Cascata do Narrador:
  `principal → modelo menor no pool → utilitário → DeepSeek`.
- Se não houver GPU principal nem utilitária, DeepSeek vem antes do fallback CPU.
- Configuração em `server/.env`:

```env
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-flash
LUMINUS_DEEPSEEK_FALLBACK=1
LUMINUS_DEEPSEEK_MODEL=deepseek-flash
LUMINUS_DEEPSEEK_THINKING=0
LUMINUS_DEEPSEEK_BUDGET_USD=0
```

`LUMINUS_DEEPSEEK_BUDGET_USD=0` = sem limite. Para limitar a US$ 10, use `10`.
O gasto estimado aparece em `/api/health` (`deepseek.usage`) e em
`logs/deepseek_usage.jsonl` (acumulado por processo).

Endpoints:

- `GET /api/deepseek` — status, modelo, preços e gasto.
- `POST /api/deepseek` com `{"enabled": true}` — força DeepSeek.
- `POST /api/deepseek` com `{"enabled": false}` — volta ao automático.

Para ativar: preencher `DEEPSEEK_API_KEY` em `server/.env` e reiniciar a API.

### 2.4 Criação profunda de NPCs (`server/npc_forge.py`)

- Pensador → Juiz → Síntese.
- Rotas com failover: `freellmpool` (opcional) → DeepSeek (opcional) → GPU utilitária → modelo principal.
- `/api/assist/character` usa o fluxo profundo por padrão (`deep=true`) e cai no caminho simples se falhar.
- Config em `server/.env`:
  `LUMINUS_NPC_DEEP=1`, `LUMINUS_NPC_JUDGE=1`, `LUMINUS_NPC_ALLOW_MAIN=1`,
  `LUMINUS_NPC_POOL_URL=`, `LUMINUS_NPC_USE_DEEPSEEK=0`.
- Script opcional para subir o proxy: `scripts/freellmpool_proxy.sh`.

### 2.5 Recibos de resolução (`server/resolution.py`)

Inspirado no clockwork-dark:

- Classifica ações de risco (combate, furtividade, social, investigação, movimento, resistência).
- Resolve d20 + modificador × CD de forma determinística por seed.
- Resultado: sucesso crítico, sucesso, parcial, falha, falha crítica.
- Injeta um bloco autoritativo no prompt: o Narrador deve mostrar o resultado do recibo.
- Estado guarda `resolution`, `resolution_history`, `roll_seq` e um evento no ledger.
- Checagem heurística de contradição na prosa (apenas loga e incrementa `receipt_contradictions`).
- Flag: `LUMINUS_RESOLUTION=1` (desligar com `0`).

### 2.6 Vida fora de cena (`server/offscreen.py`)

Inspirado em WorldLines/Multihog:

- A cada 4 turnos, 1–2 NPCs **ausentes** avançam uma agenda própria.
- Determinístico, sem LLM.
- Injetado como rumor/contexto; o Narrador não vira onisciente.
- Estado guarda `offscreen` (últimos 12 eventos).
- Flags: `LUMINUS_OFFSCREEN=1`, `LUMINUS_OFFSCREEN_EVERY=4`.

### 2.7 World Voices (`server/world_voices.py`)

Inspirado no RePoG:

- Gera cartas, boatos, avisos e relatórios com autor, visibilidade e conhecimento limitado.
- A alegação **não é automaticamente verdade**; o Narrador deve tratar como alegação.
- A cada 6 turnos, ou sob demanda.
- Tabela nova `world_voices` (criada por `db.init()` no boot).
- Endpoints:
  - `GET /api/sessions/{sid}/voices`
  - `POST /api/sessions/{sid}/voices` (força a geração)
- Flags: `LUMINUS_VOICES=1`, `LUMINUS_VOICES_EVERY=6`.

### 2.8 World Packs (`server/world_pack.py`)

Inspirado no covel:

- Exporta mundo + elenco + fatos/lore + sessões (mensagens, estados, elenco, capítulos, threads, fatos por entidade e World Voices).
- Importa criando um **mundo novo** (não sobrescreve).
- Endpoints:
  - `GET /api/worlds/{wid}/export?include_sessions=true`
  - `POST /api/worlds/import` (corpo JSON do pack)
- Embeddings e checks derivados ficam de fora; reindexar a sessão após importar se quiser RAG.
- Teste rápido:

```bash
curl -s 'http://127.0.0.1:5111/api/worlds/2/export?include_sessions=false' > /tmp/pack.json
curl -s -X POST -H 'Content-Type: application/json' --data @/tmp/pack.json \
  http://127.0.0.1:5111/api/worlds/import
```

## 3. Arquivos criados

- `server/gpu_guard.py`
- `server/npc_forge.py`
- `server/resolution.py`
- `server/offscreen.py`
- `server/world_voices.py`
- `server/world_pack.py`
- `scripts/gpu_guard_env.sh`
- `scripts/install_gpu_guard.sh`
- `scripts/freellmpool_proxy.sh`
- Testes:
  - `server/test_gpu_guard.py`
  - `server/test_npc_forge.py`
  - `server/test_llm_deepseek.py`
  - `server/test_resolution.py`
  - `server/test_offscreen.py`
  - `server/test_world_voices.py`
  - `server/test_world_pack.py`
  - `server/test_features_integration.py`
  - `server/test_chat_features.py`

## 4. Arquivos modificados

- `server/main.py`
- `server/llm.py`
- `server/embeddings.py`
- `server/db.py` (tabela `world_voices`)
- `server/.env`
- `server/.env.example`

Backups criados antes das mudanças:

```text
server/main.py.bak-gpu-guard-20261022221259
server/llm.py.bak-gpu-guard-20261022221259
server/embeddings.py.bak-gpu-guard-20261022221259
server/.env.bak-gpu-guard-20261022221259
```

## 5. Testes

```bash
cd /home/ledilson/Documentos/luminus/server
.venv/bin/python -m unittest \
  test_resolution test_offscreen test_world_voices test_world_pack \
  test_features_integration test_chat_features test_gpu_guard \
  test_npc_forge test_llm_deepseek test_packages
```

Último resultado: **81 testes passando**.
`test_chat_features` usa LLM mockado, então não depende de GPU/Ollama.

## 6. Endpoints novos/alterados

- `GET /api/health` — inclui `gpu`, `deepseek`, `npc_forge`, `active_model`, `active_tier`, `utility_available`.
- `GET /api/gpu`
- `POST /api/gpu/check`
- `POST /api/gpu/reset`
- `GET /api/deepseek`
- `POST /api/deepseek`
- `GET /api/sessions/{sid}/voices`
- `POST /api/sessions/{sid}/voices`
- `GET /api/worlds/{wid}/export`
- `POST /api/worlds/import`
- `/api/metrics` inclui contagem de `world_voices`.

## 7. Depois do reboot — checklist

1. Verificar hardware:

```bash
nvidia-smi -L
nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv,noheader
```

2. Ver o que o helper aplicou:

```bash
journalctl -u luminus-gpu-env -n 30 --no-pager
systemctl show ollama -p Environment | tr ' ' '\n' | grep CUDA_VISIBLE
systemctl show ollama-utility -p Environment | tr ' ' '\n' | grep CUDA_VISIBLE
```

3. Subir a API (ela não é um serviço systemd; o `start.sh` é manual):

```bash
cd /home/ledilson/Documentos/luminus
./start.sh
```

4. Conferir:

```bash
curl -s http://127.0.0.1:5111/api/health | python3 -m json.tool
curl -s http://127.0.0.1:5111/api/gpu | python3 -m json.tool
curl -s http://127.0.0.1:11434/api/ps
```

5. Abrir o app ou chamar `POST /api/warmup` para carregar o modelo.

### O que esperar

- **4 GPUs saudáveis**: `tier=full`, modelo `luminus-27b`, `CUDA_VISIBLE_DEVICES=0,1,3`.
- **GPU3 ainda com erro**: `tier=two_gpu`, modelo `qwen38-9b-heretic:latest` (Q8), `CUDA_VISIBLE_DEVICES=0,1`.
- **Ollama ainda em CPU** mesmo com `CUDA_VISIBLE_DEVICES=0,1`: o driver não voltou; desligar da tomada/reseat da GPU3.
- **Sem GPU e sem DeepSeek**: narração cai para CPU ou falha; configurar a chave DeepSeek é a saída.

## 8. Pendências / próximos passos

- [ ] Reiniciar a máquina e validar se o driver CUDA voltou a inicializar.
- [ ] Preencher `DEEPSEEK_API_KEY` em `server/.env` se quiser o Narrador de emergência.
- [ ] Opcional: definir `LUMINUS_DEEPSEEK_BUDGET_USD=10`.
- [ ] Se a GPU3 voltar: confirmar `tier=full` e o 27B carregando.
- [ ] Se a GPU3 não voltar: manter `two_gpu` (Q8) e considerar reseat/hardware.
- [x] Opcional: adicionar botões no app Flutter para exportar/importar World Pack e listar World Voices (feito em 2026-10-23, ver seção 10).
- [ ] Opcional: transformar a checagem de contradição dos recibos em correção automática via Juiz/LLM.
- [ ] Opcional: usar o pool freellmpool (`LUMINUS_NPC_POOL_URL`) para Pensador/Juiz.

## 9. Observações importantes

- `server/.env` é local e não deve ser commitado; a chave DeepSeek nunca deve ir para o repositório.
- O projeto `luminus` **não é um repositório git**; os backups em `server/*.bak-*` são a rede de segurança.
- O timer `luminus-gpu-env.timer` fica ativo após o boot e reaplica a configuração a cada 20 s.
- O `POST /api/worlds/import` cria um mundo novo; não sobrescreve o original.
- O World Pack não carrega vetores de embedding nem `consistency_checks`/`commitment_checks`; eles são reconstruíveis.

## 10. App mobile — atualizado em 2026-10-23

- `app/lib/api.dart`: modelo `WorldVoice` e métodos `sessionVoices`, `generateSessionVoice`, `exportWorldPack` e `importWorldPack`.
- `app/lib/local_files.dart`: ponte com o seletor de arquivos do Android (SAF), sem adicionar dependências.
- `MainActivity.kt`: métodos nativos `exportFile` (ACTION_CREATE_DOCUMENT) e `pickJson` (ACTION_OPEN_DOCUMENT), com cópia em thread separada.
- `chat_screen.dart`: menu **Vozes do mundo** com listagem, autor, visibilidade, status de verdade e botão **Gerar artefato agora**.
- `settings_screen.dart`: seção **Mundos e World Packs** com escolha do mundo, opção de incluir histórias, exportar e importar.
- APK release `1.0.20261023073536` (build `1792751736`), publicado em `dist/luminus.apk` e `dist/build.json` em 2026-10-23 07:37.
- Validação: `flutter analyze` sem issues e `flutter test` com 6 testes passando.
- O watcher `scripts/watch_apk.py` foi mantido ativo após o build.

## 11. NPC Forge com pool freellmpool — 2026-10-23

Objetivo: Pensador e Juiz usando o pool `freellmpool` (OpenAI-compatible local
em `http://127.0.0.1:8080/v1`) sem deixar a criação inteira refém de um
provedor lento/falho. A Síntese continua preferindo o 27B local.

### Mudanças em `server/npc_forge.py`

- Pensador e Juiz agora pedem `prefer="pool"`: a ordem fica
  `freellmpool → DeepSeek → main → utility`; a Síntese segue
  `LUMINUS_NPC_SYNTH_ROUTE` (padrão `main`).
- Retry no pool: `LUMINUS_NPC_POOL_ATTEMPTS` (padrão 2) e
  `LUMINUS_NPC_POOL_TIMEOUT` (padrão 75s) por chamada. Cobre 404/429/5xx
  transitórios do provedor gratuito sem esperar o timeout da etapa.
- Novo `_collect_dossiers`: o 1º dossiê válido encerra a espera; os outros
  Pensadores ganham `LUMINUS_NPC_THINKER_GRACE` segundos (padrão 8) e são
  cancelados. Antes, um candidato falho segurava tudo por 125s+.
- `GET /api/health` → `npc_forge` agora expõe `thinker_grace`, `pool_attempts`
  e `pool_timeout`.

### Evidência do teste real (antes do ajuste)

Com 2 Pensadores em paralelo, um dossiê voltou do pool em 29,8s; o outro bateu
404 em 90,2s, tentou a GPU utilitária (125s, timeout) e só então o 27B (104,9s).
A criação inteira levou ~7 min. Depois do ajuste, o primeiro sucesso libera a
etapa em ~8s extras, no máximo.

### Config nova em `server/.env`

```env
LUMINUS_NPC_POOL_ATTEMPTS=2
LUMINUS_NPC_POOL_TIMEOUT=75
LUMINUS_NPC_THINKER_GRACE=8
```

Reiniciar a API (`systemctl --user restart luminus-api.service`) para aplicar.
Testes: `test_npc_forge` 12 casos; suíte completa 88 testes passando.

### Contrato JSON no pool

O teste real revelou que o Juiz do pool devolveu chaves em português
(`aprovado`, `issues`...) porque a rota OpenAI-compatible não recebe o
`fmt=schema` do Ollama. Agora:

- `_with_schema()` injeta o JSON Schema no prompt de toda rota remota
  (freellmpool/DeepSeek), exigindo os nomes exatos dos campos.
- `_valid_judge()` valida `ok`, `score`, `issues`, `missing`, `strongest` e
  `verdict`. Se o parecer vier torto, o Juiz pede uma segunda opinião na GPU
  utilitária (formato estruturado); se ainda falhar, segue sem parecer.
- Testes novos: injeção de schema, validação do Juiz e segunda opinião.
