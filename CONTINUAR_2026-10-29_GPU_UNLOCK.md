# Retomar após reinício — gpu-unlock no Luminus (2026-10-29)

Estado salvo desta sessão para continuar depois do reboot.

## Situação

- **Instalado?** Não. Falta rodar o instalador com sudo (senha não é
  interativa nesta sessão) e reiniciar.
- O `gpu_unlock` **não** está carregado (`/proc/cmdline` sem
  `gpu_unlock.targets`, DKMS sem registro).
- Firmware já baixado/gerado e validado em `~/.cache/gpu-unlock` (~605 MB);
  o instalador reaproveita. `make install-* DESTDIR=...` validado, módulo
  compilou limpo no kernel `7.0.11-76070011-generic`.

## Arquivos criados

- `scripts/install_gpu_unlock.sh` (instala / `--status` / `--remove`)
- `scripts/gpu_unlock_bench.py` (TFLOPS + Ollama + ComfyUI)
- `ALTERACOES_2026-10-29.md` (avaliação completa)
- `evals/gpu_unlock_bench_pre.json` (linha de base)

## Linha de base (pré, medida)

```
GPU 0 3060 Ti  BF16 36.8  FP16 36.9  TF32 18.4  FP32 13.4 TFLOPS
GPU 1 3060 Ti  BF16 36.0  FP16 36.2  TF32 17.9  FP32 12.1
GPU 2 2060     BF16  4.4  FP16 32.3  TF32  6.8  FP32  7.0
GPU 3 3060 Ti  BF16 36.5  FP16 36.6  TF32 18.3  FP32 12.7
LLM luminus-27b  prefill 940.4 tok/s  decode 19.3 tok/s  parede 8.7 s
ComfyUI Flux schnell (832x1216, 4 passos)   mediana 19.72 s (frio 68.61 s)
ComfyUI SDXL NoobAI  (832x1216, 28 passos)  mediana 17.87 s (frio 45.34 s)
```

## O que fazer agora (você)

```bash
cd ~/Documentos/luminus
sudo bash scripts/install_gpu_unlock.sh --targets=0000:01:00.0,0000:02:00.0,0000:09:00.0
sudo reboot
```

Escolhi só as três 3060 Ti (Ampere, estável). A RTX 2060 é Turing
experimental; para incluí-la depois:

```bash
sudo bash scripts/install_gpu_unlock.sh            # targets=all (4 GPUs)
sudo reboot
```

Se algo der errado no boot: no menu do systemd-boot edite a entrada e apague
`gpu_unlock.targets=...`, ou dê boot em um kernel anterior; depois
`sudo bash scripts/install_gpu_unlock.sh --remove`.

## O que fazer depois do reboot (me pedir)

1. Verificar que o módulo rodou antes do nvidia:
   ```bash
   journalctl -k -b | grep gpu_unlock
   cat /proc/cmdline | tr ' ' '\n' | grep gpu_unlock
   bash scripts/install_gpu_unlock.sh --status
   ```
2. Medir o pós:
   ```bash
   cd ~/Documentos/luminus
   server/.venv/bin/python scripts/gpu_unlock_bench.py --phase post
   ```
3. Comparar `evals/gpu_unlock_bench_pre.json` × `post.json` e responder:
   - BF16/FP16/TF32 por GPU (esperado ~+80% nas 3060 Ti)
   - prefill/decode do 27B (esperado prefill alto, decode igual)
   - Flux schnell e SDXL (esperado ganho maior no SDXL fp16; Flux Q4 menor)

## Observações da sessão

- O primeiro run do bench falhou no ComfyUI porque `/free` responde corpo
  vazio; `http_json` agora tolera corpo vazio.
- Rodadas "quentes" do ComfyUI davam 0,11 s por cache de execução; agora o
  seed muda por rodada (1000 no frio, 2000+i nas quentes).
- Driver 595.84; o 610.43.03 do upstream é só a origem do firmware, não é
  dependência de driver.
- Efeitos colaterais esperados: tela preta ~1 s no boot; suspend/resume
  re-destrava as placas (~+1 s); hibernação não é coberta.
