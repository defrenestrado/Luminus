# Alterações — 2026-10-29 — gpu-unlock (tensor cores GeForce)

Avaliação e integração do <https://github.com/Zagitta/gpu-unlock> ao Luminus.
Ele remove, antes do driver `nvidia` carregar, o limite que a NVIDIA impõe às
GeForce em FP16/BF16/TF32 com acúmulo FP32 (o mesmo silício vendido como
RTX A-series roda no dobro). Não troca driver, não altera VBIOS.

## Encaixe no nosso hardware

| GPU | Arquitetura | Suporte upstream |
| --- | --- | --- |
| 3× RTX 3060 Ti (`01`, `02`, `09`) | Ampere | estável (100+ relatos) |
| RTX 2060 12 GB (`05`) | Turing | experimental (sem BF16 nativo) |

Driver atual 595.84; o `610.43.03` fixado pelo projeto serve só para extrair o
firmware do boot, não é dependência de driver. O módulo compilou limpo contra
os headers do kernel `7.0.11-76070011-generic`.

## Onde o Luminus ganha (e onde não ganha)

- **Imagem (ComfyUI/SDXL FP16)** — ganho esperado de +30–55%. É o maior
  beneficiado no nosso fluxo (adulto/fotorreal usam SDXL fp16).
- **Cena padrão (FLUX schnell Q4 GGUF)** — quantizado: ganho menor, aparece
  principalmente no cálculo BF16.
- **Prefill de LLM (27B em BF16/FP16)** — +25–55%.
- **Ollama com GGUF quantizado (nosso narrador Q5/Q8)** — 0–15%, só em
  contexto longo. O decode (geração token a token) não muda: é limitado por
  memória.
- **TTS/embeddings/INT8/INT4/FP8 e jogos** — sem mudança.

## Arquivos adicionados

- `scripts/install_gpu_unlock.sh` — instala (DKMS + firmware + hook de
  initramfs), ativa `gpu_unlock.targets=` via `kernelstub` (Pop!_OS/systemd-
  boot), mostra status e remove. Não usa `apt-get build-dep` (o Pop!_OS não
  tem `deb-src`); instala o módulo direto pelo alvo DKMS do upstream.
- `scripts/gpu_unlock_bench.py` — mede tensor (TFLOPS BF16/FP16/TF32 por GPU),
  LLM (prefill/decode do Ollama) e imagem (FLUX schnell e SDXL NoobAI no
  ComfyUI). Resultados em `evals/gpu_unlock_bench_<fase>.json`. O seed varia
  por rodada para não bater no cache de execução do ComfyUI.

## Linha de base medida (antes, `--phase pre`)

```
GPU 0 3060 Ti  BF16 36.8  FP16 36.9  TF32 18.4  FP32 13.4 TFLOPS
GPU 1 3060 Ti  BF16 36.0  FP16 36.2  TF32 17.9  FP32 12.1
GPU 2 2060     BF16  4.4  FP16 32.3  TF32  6.8  FP32  7.0
GPU 3 3060 Ti  BF16 36.5  FP16 36.6  TF32 18.3  FP32 12.7
LLM luminus-27b  prefill 940 tok/s  decode 19.3 tok/s
ComfyUI Flux schnell (832×1216, 4 passos)  mediana 19,72 s
ComfyUI SDXL NoobAI  (832×1216, 28 passos) mediana 17,87 s
```

Referência do upstream para 3060 Ti: BF16 37 → 68 TFLOPS (~+80%). O BF16 do
2060 (4,4) é emulado por software no Turing; o ganho dele deve aparecer em FP16.

## Instalar / medir / reverter

```bash
sudo bash scripts/install_gpu_unlock.sh            # 3× 3060 Ti + 2060
sudo bash scripts/install_gpu_unlock.sh \
  --targets=0000:01:00.0,0000:02:00.0,0000:09:00.0 # só as 3060 Ti (mais seguro)
sudo reboot

journalctl -k -b | grep gpu_unlock
server/.venv/bin/python scripts/gpu_unlock_bench.py --phase post

sudo bash scripts/install_gpu_unlock.sh --status
sudo bash scripts/install_gpu_unlock.sh --remove   # + reboot
```

Riscos conhecidos: a tela fica preta ~1 s no boot (reset da GPU de vídeo antes
do `nvidia-drm`); suspend/resume re-destrava as placas, custando ~1 s a mais;
hibernação não é coberta. O cache de firmware (~605 MB) fica em
`~/.cache/gpu-unlock` e é reaproveitado.

---

## Narrador no llama.cpp com MTP (2026-10-30)

O `gpu_unlock` não acelera o texto: o GGUF Q4_K_M roda em kernels INT8 (MMQ),
que não usam os tensor cores FP16/BF16 destravados. Para acelerar o 27B
quantizado trocamos o motor **do narrador** por `llama.cpp` com decodificação
especulativa MTP (o mesmo head `blk.64.nextn.*` do GGUF).

### O que foi medido (3× 3060 Ti, ctx 32768, prompt 7,8k tokens, 192 tokens)

| Motor | prefill | decode |
| --- | --- | --- |
| Ollama 0.32.14 | 1076 tok/s | 18,6 tok/s |
| llama.cpp `-sm layer` | 1259 | 20,0 |
| llama.cpp `-sm layer` + MTP n=3 | 858 | **29,6** |

Em turnos reais do narrador (prosa, temp 0.8) o MTP entrega ~24 tok/s com
aceitação de 38–40%: cerca de **+28% no decode**, pagando ~20–30% a mais no
prefill. O ponto de virada é prompt ≈ 27× o tamanho da resposta; para turnos
de RPG (2–6k de contexto, 300–1000 tokens) ainda compensa.

Testes descartados:

- `-sm tensor` (tensor parallel): prefill despenca para **53 tok/s**. Links
  PCIe reais são Gen2 x8 / Gen2 x8 / Gen2 x1 e `topo -p2p` = `CNS/NS` (sem
  P2P). Não usar.
- `-sm row`: falha com `device CUDA0 does not support split buffers` (exige
  P2P).
- Geezuz (`ohgeebtw/geezuz-qwen3.8-27b-dual-gpu`): mais rápido, mas exige
  2×24 GB e P2P — incompatível.

### Como ficou

- `scripts/install_llamacpp.sh` — baixa o binário CUDA do llama.cpp
  (`b11998`, ~750 MB, sem compilar/sudo) em `~/.local/share/luminus/llamacpp`,
  liga o GGUF do Ollama por symlink e grava `config.env`.
- `scripts/llamacpp_shim.py` — ponte **compatível com a API do Ollama**.
  Modelos narradores vão para o `llama-server`; os demais (utilitário 9B)
  continuam no Ollama real. O app não mudou.
- `scripts/llamacpp.sh on|off|status|warm|log|restart` — aponta/desaponta
  `OLLAMA_URL` no `server/.env` e reinicia `luminus-api`.
- `start.sh` — se o `server/.env` aponta para a ponte, sobe o serviço
  `luminus-llamacpp` e dispara o warmup em segundo plano; sem isso, segue no
  Ollama normal.

Ativar / desativar:

```bash
bash scripts/install_llamacpp.sh     # uma vez
bash scripts/llamacpp.sh on          # llama.cpp + MTP
bash scripts/llamacpp.sh warm        # pré-carrega (~25 s)
bash scripts/llamacpp.sh status
bash scripts/llamacpp.sh off         # volta ao Ollama
```

O `gpu_guard` continua valendo: se as GPUs caírem, o app degrada e o
`unload` do modelo para o `llama-server`, liberando a VRAM.

---

## Correção da ficha: perícias no app (2026-10-30)

O motor já somava perícia ao modificador (`resolution.py`) e o servidor já
devolvia `skills` no `/sheet` e no recibo, mas o app só lia parte disso.

- **Formulário:** etapa Alma ganhou a seção *Perícias* (0–5, orçamento 10) com
  passos −/+. O rascunho de IA agora preenche e o salvamento envia `skills`,
  então perícias geradas deixam de ser descartadas. Revisão lista só as treinadas.
- **Recibo do dado:** a fórmula passou a mostrar a perícia
  (`+ Luta 3 (+3)`) antes da sorte, fechando a soma exibida com o total.
- **Ficha (modal):** nova seção *Perícias*; mostra as treinadas ou "sem treino".

Testes: `flutter test` (8) e `unittest test_character_attributes test_resolution`
(27) passando.
