# Luminus — Plano de Melhorias de Design

> **Escopo:** uso pessoal, local-first, sem loja, sem monetização, sem UGC.
> **Direção:** visual novel com rolagem de dados (d20).
> **North star:** “Uma visual novel de mesa: leitura cinematográfica, escolhas com peso e um d20 que aparece quando o destino está em jogo.”
> **Última atualização:** 2026-10-24

---

## 0. Princípios

1. **Cena primeiro, chat depois.** A tela padrão é a cena, não a lista de mensagens.
2. **Dado é drama, não planilha.** O d20 aparece em momentos de risco, com ritual e consequência.
3. **O motor aparece quando importa.** Ficha, fios, inventário e estado existem, mas sem poluir a leitura.
4. **Zero digitação obrigatória.** Dá para jogar só com escolhas e verbos rápidos.
5. **Latência escondida pelo ritual.** A animação do dado cobre o planejamento e a geração.
6. **Nada de loja, mana, streak ou energia.** O objetivo é prazer de jogar, não retenção.
7. **P0 antes de P1.** Não adicionar sistemas novos antes de expor os que já existem.

---

## 1. O loop alvo

```text
Cena (local, hora, presentes)
   -> Fala ou escolha do jogador
   -> Ação pede teste?
        não -> consequência narrada
        sim -> overlay de d20 -> resultado -> consequência autorizada
   -> Atualiza laço/NR, fios, inventário, cena
   -> Próxima escolha ou transição de capítulo
```

**Regra de ouro:** o servidor rola; o cliente só encena. A prosa nunca contradiz o recibo.

---

## 2. O que já existe (não reconstruir)

| Capacidade | Onde está | Como usar no novo design |
|---|---|---|
| Planejador de cena | `server/main.py:903` | Alimenta cabeçalho de cena, transições e capítulos |
| Estado da cena | `state.scene`, `GET /sessions/{sid}/scene` | Local, hora, presentes, título da cena |
| Rolagem d20 | `server/resolution.py` | Overlay de dado + recibo na transcrição |
| Fios e promessas | `threads` em `server/db.py:144`; `GET /commitments` | Diário de fios abertos |
| Elenco e NR | `GET /cast`; `state.cast` | Party strip e ficha de vínculo |
| Inventário e ferimentos | `state.inventory`, `state.injuries` | Ficha do personagem |
| Mundo vivo | `server/offscreen.py` | “Enquanto você estava fora” |
| Vozes do mundo | `server/world_voices.py` | Cartas/rumores no código |
| Memória e capítulos | `server/main.py` | Recaps, capítulos, busca |
| Ramificar/rebobinar | `chat_screen.dart:879` | Árvore de ramificações visível |
| TTS e arte | `server/tts.py`, `server/art.py` | Voz por personagem, cenários e retratos |

**O maior desperdício atual:** o `done` do chat envia `scene`, `pacing`, `commitments`, `relationship_events` e `cast`, mas o `ChatChunk` do Flutter só lê `delta`, `status`, `nr`, `axes` e `vinculo` (`app/lib/api.dart:276`). O cliente joga fora metade do estado.

---

## 3. Benchmark aplicado (sem copiar o modelo comercial)

| Referência | O que copiar | O que ignorar |
|---|---|---|
| **AI Dungeon** | Rewind, memória, cartões de cenário, “ação livre” | UGC, assinatura, contexto pago |
| **AI Game Master** | Dados, party, quests, inventário, XP | Grind, token drain, vídeo |
| **Choices** | Capítulos, cliffhanger, key art, VN, escolhas com peso | Keys, diamantes, anúncios |
| **Eldrum / Fighting Fantasy** | d20, ficha, inventário, múltiplos finais, leitura confortável | Paredes de stats e grind |
| **Davia** | Mundo vivo, diário, papel do personagem | Multiplayer, 5.000 mundos |
| **AFK Journey** | “Enquanto você estava fora”, coleção, temporadas | Gacha, pay-to-win |
| **Character.AI** | Retratos, voz, presença de personagem | Ads, chat sem jogo |

**Conclusão:** o Luminus deve parecer **Choices + Eldrum**, com a profundidade de **AI Dungeon** e o “mundo vivo” de **Davia** — não um clone de chat.

---

## 4. A tela de jogo (visual novel)

### 4.1 Anatomia da cena

```text
+--------------------------------------------------+
| Cap. 2 · Porto de Sal · 21:40          NR 42  ⋯ |
|                                                  |
|                 [ arte de fundo ]                |
|                                                  |
|                 [ retrato do NPC ]               |
|                                                  |
|  +--------------------------------------------+  |
|  | Milim                                      |  |
|  | "Você demorou."                            |  |
|  | Toque para continuar                       |  |
|  +--------------------------------------------+  |
|                                                  |
|  [1] Social   [2] Risco   [3] Investigar         |
|  [ Escrever ação ]              [ Ver ficha ]    |
+--------------------------------------------------+
```

### 4.2 Componentes

- **Fundo:** `scene_image` quando existir; fallback com gradiente + textura sutil.
- **Retrato:** avatar atual em medalhão primeiro; depois retrato gerado com expressões.
- **Caixa de diálogo:** moldura ornamentada, nome do NPC, texto em Lora 16–18, altura 1.6.
- **Typewriter:** 18–25 ms por caractere; toque completa; segundo toque avança.
- **Auto:** avança após 3 s do fim do texto; desliga ao tocar.
- **Skip:** pula para a próxima escolha; mantém backlog.
- **Backlog:** histórico completo, com áudio e “ir para esta cena”.
- **Voz:** botão de replay; TTS por personagem.
- **Topo:** capítulo, local, hora, NR, menu.
- **Party strip:** retratos pequenos com NR e humor; toque abre ficha.

### 4.3 Modos

| Modo | Uso | Padrão |
|---|---|---|
| **Cena** | Visual novel, foco em leitura e drama | Sim |
| **Leitura** | Prosa em largura total, estilo livro | Alternativo |
| **Mesa** | Chat + dado + ficha + inventário | Quando quiser editar/ver estado |

Persistir o modo por sessão.

---

## 5. Sistema de dados (d20)

### 5.1 Quando rolar

Usar a classificação existente em `server/resolution.py`:

- Combate, furtividade, resistência: sempre que houver risco.
- Social: apenas em persuasão, mentira, intimidação, sedução, barganha.
- Investigação: apenas em risco, pressão ou pista escondida.
- Movimento: apenas em fuga, salto, escalada, arrombamento, perseguição.

**Não rolar** em conversa trivial, olhar ao redor sem risco ou andar por uma sala segura.

### 5.2 Fluxo visual

1. Jogador escolhe uma ação de risco.
2. UI mostra “Teste de Social · Dificuldade média” (números exatos opcionais).
3. Overlay de dado começa a girar enquanto o servidor planeja/escreve.
4. Servidor devolve o recibo: `d20`, modificador, CD, total, resultado.
5. Dado “cai” no resultado com som/haptic.
6. Jogador confirma; a prosa mostra a consequência.
7. Recibo fica na transcrição como cartão expansível.

### 5.3 Resultados

| Resultado | Quando | Tom da prosa |
|---|---|---|
| Sucesso crítico | d20=20 ou total >= CD+5 | Vantagem extra, abertura, momento memorável |
| Sucesso | total >= CD | Consequência pretendida, custo baixo |
| Sucesso parcial | total >= CD-2 | Consegue, mas com custo/complicação |
| Falha | total < CD-2 | Bloqueio, revés, nova rota |
| Falha crítica | d20=1 | Revés grave, marca/ferida, escalada |

### 5.4 Recibo na transcrição

Cartão com:

- Ícone de d20 e rótulo do teste.
- `d20 + modificador = total` contra `CD`.
- Rótulo do resultado.
- Uma linha de consequência autorizada.
- Botão “detalhes” para ver intenção, seed e histórico.

### 5.5 Determinismo e “Destino”

- O servidor rola com seed `sessão:turno:sequência:intenção`.
- O cliente nunca sorteia; apenas anima.
- “Destino” (opcional, P1): 1 rerrolagem por capítulo, registra as duas rolagens.
- Não usar mana, moeda ou compra para rerrolar.

### 5.6 Ficha e modificadores (P2)

Hoje o modificador vem de palavras-chave e estado. Depois:

- Atributos: Corpo, Mente, Presença, Sorte.
- Habilidades: Luta, Furtividade, Persuasão, Investigação, Resistência.
- Modificador = atributo + habilidade + item + ferimento.
- A ficha mostra o cálculo; o dado mostra o total.

---

## 6. Apresentação visual novel

### 6.1 Arte

- **Prioridade P0:** usar avatar atual como retrato em medalhão; fundo gerado/`scene_image`.
- **P1:** retratos maiores, com 3 expressões (neutro, tenso, sorriso) e variação por humor (`state.moment.mood`).
- **P1:** cenários por local, com style bible por mundo.
- **Fallback:** gradiente + ícone do mundo; nunca letra gigante como card principal.

### 6.2 Tipografia e leitura

- Títulos: Manrope 700.
- Prosa: Lora 16–20, line-height 1.6–1.7, largura máxima de 62–70 caracteres.
- Diálogo: aspas e travessão, sem markdown visível.
- Ênfase: negrito/itálico existentes; não usar cores aleatórias.
- Tamanho de fonte ajustável e persistido.

### 6.3 Movimento e som

- Transição de cena: fade/ink 200–300 ms.
- Capítulo: cartão de título 1,2 s.
- Dado: giro 800–1200 ms; impacto com haptic.
- Escolha: leve elevação e brilho no toque.
- Ambiente: loop por local/clima; volume separado de voz/SFX.
- SFX: dado, página, UI, passos, porta.
- Respeitar modo silencioso.

---

## 7. Navegação e telas

### 7.1 Nova estrutura

| Aba | Conteúdo |
|---|---|
| **Jogar** | Continuar, cena atual, “enquanto você estava fora”, mundos |
| **Histórias** | Campanhas com capa, capítulo, progresso, busca, pin, renomear |
| **Mundo** | Códice, party, fios, ficha, mapa mental |
| **Criar** | NPC Forge, World Bible, Estúdio de arte, packs |
| **Ajustes** | Conexão, leitura, conteúdo, dados, sobre |

“Estúdio” deixa de ser aba principal e vira ferramenta dentro de **Criar**.

### 7.2 Home

```text
+--------------------------------------------------+
| Luminus                                  status  |
|                                                  |
| [ CAPA DA CAMPANHA ]                              |
| Cap. 3 · A dívida de sal                          |
| "Você ainda lembra do cheiro de ferro?"           |
| [ Retomar ]                                      |
|                                                  |
| Enquanto você estava fora                         |
| - Um mensageiro deixou uma carta...               |
|                                                  |
| Mundos em destaque                                |
| [Nadir] [Porto de Sal] [Academia]                 |
+--------------------------------------------------+
```

### 7.3 Cartão de campanha

- Capa (cena ou retrato).
- Título gerado pela IA (não data/hora).
- Capítulo atual e progresso.
- Última linha da história.
- “Retomar” em destaque.
- Menu: renomear, fixar, arquivar, exportar.

### 7.4 Códice / Diário

- Pessoas, lugares, facções, lore, cartas/vozes.
- Fios abertos: tipo, título, quem está envolvido, urgência.
- Ficha: NR, eixos, inventário, ferimentos, fatos conhecidos.
- Busca full-text (o banco já tem FTS5).

### 7.5 Painel do Diretor

- **P1 (somente leitura):** cena, plano, memória recuperada, offscreen, vozes, última rolagem, latência.
- **P2 (editável):** forçar cena, rerrolar, editar fato/fio, ajustar tom/temperatura, trocar modelo.
- **P2:** botão “copiar estado” para depurar.

---

## 8. Backlog P0 (primeiro ciclo)

| ID | Entrega | Mudanças principais | Aceite |
|---|---|---|---|
| **P0-01** | Contrato de estado no cliente | `ChatChunk` + `chatStream` em `app/lib/api.dart`; `_msgs`/estado em `chat_screen.dart` | Após um turno, a UI recebe `scene`, `pacing`, `commitments`, `cast` e `resolution` sem nova requisição |
| **P0-02** | Emitir rolagem antes da prosa | `server/main.py` chat: evento `status=rolling`, evento `resolution`, incluir no `done` | Dado aparece em até 2 s; resultado da prosa bate com o recibo; testes de resolução passam |
| **P0-03** | Cena como modo padrão | `_vnMode` padrão `true`; seletor Cena/Leitura/Mesa no topo; persistir por sessão | Abrir uma história cai na VN; troca de modo persiste |
| **P0-04** | Overlay de d20 | Novo `app/lib/widgets/dice_roll.dart`; animação, resultado, som/haptic | Toda ação de risco mostra dado; ações triviais não mostram |
| **P0-05** | Recibo na transcrição | Novo `dice_receipt_card.dart`; persistir no histórico | Recibo continua visível ao reabrir a sessão |
| **P0-06** | Escolhas com tags | `POST /choices` retorna `{text,intent,risk,dc}`; novo `ChoiceCard` | 3 escolhas com tags; risco dispara dado; texto livre continua |
| **P0-07** | Party strip | `cast`/`GET /cast`; avatares com NR/humor; toque abre ficha | Party atualiza após cada turno; NR anima |
| **P0-08** | Ficha e inventário | Novo `GET /sessions/{sid}/sheet` (read-only) | Inventário, ferimentos e fios visíveis sem editar |
| **P0-09** | Identidade da campanha | Título gerado no opening/first turn; capa = `scene_image`; últimos dados no card | Nenhuma sessão nova com título “Nome — data/hora” |
| **P0-10** | Verbos rápidos | Barra “Falar / Observar / Agir / Usar / Viajar”; chips de entidades | Um turno completo sem digitar |
| **P0-11** | Warmup não bloqueante | `bootstrap_screen` entra na home; status do modelo em chip | Home abre em <2 s mesmo com modelo frio |
| **P0-12** | Leitura confortável | Tamanho de fonte, backlog, auto/skip, voz | Preferências persistem; backlog navegável |

### Ordem de execução sugerida

1. P0-01 + P0-02: contrato e recibo.
2. P0-03 + P0-04 + P0-05: cena, dado e recibo.
3. P0-06 + P0-07 + P0-08: escolhas, party e ficha.
4. P0-09 + P0-10 + P0-11 + P0-12: identidade, input, abertura e leitura.
5. Polimento, testes e screenshots.

---

## 9. P1, P2 e P3

### P1 — campanha de verdade

- Capítulos/arcos com cartão de título, recap e cliffhanger.
- Diário de fios e “promessas” com payoffs.
- Códice com descobertas e coleção.
- Busca full-text e árvore de ramificações.
- Exportar crônica em Markdown/ePub.
- Retratos com expressões e cenários por local.
- “Enquanto você estava fora” com offscreen + vozes.
- Presets de tom/ritmo/NSFW por sessão.
- Painel do Diretor somente leitura.

### P2 — mesa do mestre

- Atributos/habilidades e modificadores derivados da ficha.
- “Destino”: 1 rerrolagem por capítulo.
- Painel do Diretor editável (estado, fios, fatos, cena).
- Forçar cena, rerrolar, trocar modelo/temperatura.
- Combate por turnos opcional.
- Mapa mental de relações.
- Snapshots de estado com restauração visual.

### P3 — luxo

- Co-op local/dueto.
- Eventos sazonais.
- ePub caprichado.
- Modelo on-device para leitura offline.
- Temas/trilhas por mundo.

---

## 10. Contratos de API e dados

### 10.1 NDJSON do chat

```json
{"status":"planning"}
{"status":"rolling"}
{"resolution":{"intent":"social","label":"social","dc":12,"modifier":1,"roll":14,"total":15,"outcome":"sucesso","outcome_label":"Sucesso","consequences":"...","action":"...","seed":"..."}}
{"delta":"..."}
{"done":true,"scene":{...},"pacing":{...},"commitments":{...},"cast":[...],"resolution":{...}}
```

### 10.2 Endpoints a usar no P0

- `GET /api/sessions/{sid}/scene`
- `GET /api/sessions/{sid}/commitments`
- `GET /api/sessions/{sid}/cast`
- `GET /api/sessions/{sid}/relationship`
- `POST /api/sessions/{sid}/choices` (novo formato)
- `GET /api/sessions/{sid}/sheet` (novo, read-only)
- `GET /api/sessions/{sid}/messages` (incluir `resolution` do `message_states`)

### 10.3 Banco

- P0 não exige migração: `ledger`, `message_states`, `threads`, `session_cast` já guardam o necessário.
- Para exibir recibo ao reabrir, ler `message_states.state.resolution` da mensagem do assistente.
- Título: atualizar `sessions.title` após a abertura; sem coluna nova.
- Capa: usar `sessions.scene_image`; sem coluna nova.
- **P1-08 (identidade visual):** `characters.visual_prompt`, `art_seed` e
  `visual_style` guardam a descrição fixa (rosto, cabelo, olhos, pele, roupa,
  marca e paleta) e a seed usada em capa/retratos. Ao mudar nome, aparência,
  tags ou mundo, a identidade e os retratos gerados são invalidados.

---

## 11. Prompt e conteúdo

- **Tom VN:** prosa dialogada, parágrafos curtos, foco em gesto, voz e silêncio.
- **Dado:** a prosa narra a consequência; não citar “tirei 18” nem “CD 12”.
- **Abertura:** gerar cena + título + gancho; usar `greeting` como semente.
- **Escolhas:** variedade real (social, risco, investigação, vínculo), não três formas da mesma ação.
- **Transições:** usar `cena.transicao` para fade/capítulo.
- **Expressões:** usar `moment.mood`/`npc_emocao` para escolher retrato (P1).
- **NSFW:** toggle por sessão; sem age gate ou política de loja.

---

## 12. Métricas pessoais de sucesso

| Métrica | Meta |
|---|---|
| Tempo para retomar (servidor quente) | < 10 s |
| Tempo para primeira cena (frio) | < 60 s |
| Turnos por sessão | 10+ |
| Turnos sem digitar | > 80% |
| Frequência de dado | 15–35% dos turnos |
| Regenerações | < 10% das respostas |
| Contradições de recibo | 0 por sessão |
| Sensação subjetiva | “quero continuar amanhã” |

---

## 13. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| Dado em tudo vira fadiga | Só em risco; configuração “só conflitos” |
| Latência alta | Dado como máscara; streaming; modo rápido |
| Inconsistência de arte | Style bible, seeds fixas, fallback |
| Perda de estado | Export/world pack + backups do `data.db` |
| Escopo crescer demais | P0 fechado; nada de combate/XP antes |
| VN com retratos ruins | Medalhão do avatar até ter arte boa |
| Rerrolagem quebrar determinismo | Registrar as duas rolagens e a seed |

---

## 14. Decisões abertas

1. **Mostrar CD/números exatos por padrão?**
   - Recomendação: mostrar “Dificuldade média” na escolha e números no overlay/recibo; “modo diretor” mostra tudo.
2. **“Destino” (rerrolagem) no P1?**
   - Recomendação: sim, 1 por capítulo, sem moeda.
3. **Retratos gerados no P1 ou P2?**
   - Recomendação: P1, começando por 3 expressões do NPC principal.
4. **Combate por turnos?**
   - Recomendação: P3 e só se você quiser; o foco é relação/intriga.
5. **Modo padrão Cena ou Leitura?**
   - Recomendação: Cena.

---

## 15. Definition of Done do P0

- [ ] Abrir o app cai na home em < 2 s, mesmo com modelo frio (implementado; falta medir no aparelho).
- [x] Abrir uma história cai na cena VN, com fundo, retrato e diálogo.
- [x] Ação de risco mostra d20 com resultado do servidor.
- [x] Recibo aparece na transcrição e persiste ao reabrir.
- [x] Party strip e NR atualizam após cada turno.
- [x] Ficha mostra inventário, ferimentos e fios.
- [x] Toda campanha nova tem título e capa; cards mostram última linha.
- [x] É possível jogar um turno sem digitar.
- [x] Nenhuma regressão nos testes de resolução e chat.
- [ ] Screenshots atualizados em `preview/`.

---

## 16. Progresso de execução

| Item | Status | Observação |
|---|---|---|
| Plano criado | concluído | Este documento |
| P0-01 — contrato de estado no cliente | concluído | `ChatChunk` agora lê `scene`, `pacing`, `commitments`, `cast` e `resolution`; `chat_screen` armazena e usa cena/fios/resultado |
| P0-02 — rolagem no servidor | concluído | `status=rolling`, evento `resolution` e `resolution` no `done` |
| P0-03 — Cena como padrão | concluído | `_ViewMode` com Cena/Leitura/Mesa; padrão Cena; persistência por sessão |
| P0-04 — overlay de d20 | concluído | `dice_roll.dart` com animação, auto-dismiss, toque e haptic |
| P0-05 — recibo na transcrição | concluído | `_diceReceipt` no chat/leitura; `GET /messages` devolve `resolution` |
| P0-06 — escolhas com tags | concluído | `/choices` devolve `text/intent/label/risk/dc`; cards mostram intenção e CD |
| P0-07 — party strip | concluído | strip com NPC principal + elenco, presente/ausente e ficha do NPC ao tocar |
| P0-08 — ficha e inventário | concluído | `GET /sheet`, `ApiClient.sheet` e bottom sheet com inventário, ferimentos, fios e fatos |
| P0-09 — identidade da campanha | concluído | título gerado na abertura, `last_line`, capa nos cards e última fala na home |
| P0-10 — verbos rápidos | concluído | barra horizontal com Continuar/Observar/Falar/Agir/Usar/Viajar + toggles |
| P0-11 — warmup não bloqueante | concluído | bootstrap entra na home assim que o servidor responde; warmup segue em background |
| P0-12 — leitura confortável | concluído | tamanho de texto persistido + backlog; auto/skip via Mundo vivo/Continuar |

### 16.1 Registro de execução

- **2026-10-24 — P0-01:** `ChatChunk`/`chatStream` passaram a consumir o `done`
  completo. A cena agora pode sobrescrever o título/cabeçalho, o strip de
  memória mostra fios abertos, beat e resultado da última rolagem.
- **2026-10-24 — P0-02:** o servidor emite `status=rolling` quando a ação
  provavelmente pede teste e envia o recibo (`resolution`) antes da prosa.
  O `done` inclui `resolution` para persistência e UI.
- **2026-10-24 — P0-03:** a história abre em **Cena** (VN); seletor
  Cena/Leitura/Mesa no topo e no menu; preferência por sessão. Leitura usa
  prosa em largura total; Mesa mantém o chat.
- **2026-10-24 — P0-04:** overlay `DiceRollOverlay` com giro, resultado,
  consequência, auto-dismiss, toque e haptic. Disparado pelo evento
  `resolution` do servidor.
- **2026-10-24 — P0-05:** recibo compacto na transcrição (chat e leitura);
  `GET /messages` passou a devolver `resolution` do `message_states`, então
  o recibo sobrevive ao reabrir a sessão.
- **2026-10-24 — P0-06:** `Choice` no cliente; `/choices` devolve
  `text/intent/label/risk/dc`; cada sugestão mostra ícone, intenção e
  “Teste · CD” quando há risco.
- **2026-10-24 — P0-07:** party strip com NPC principal e elenco; NPC
  ausente fica esmaecido; toque abre ficha com NR, eixos, humor, objetivo
  e status.
- **2026-10-24 — P0-08:** novo `GET /sheet` e `ApiClient.sheet`; bottom
  sheet “Ficha e inventário” com inventário, ferimentos, fios abertos e
  fatos canônicos.
- **2026-10-24 — P0-09:** a abertura agora gera um título curto de campanha
  (com fallback para “Mundo: NPC”), `last_line` entra em `/sessions`, e os
  cards de história usam capa + última fala.
- **2026-10-24 — P0-10:** barra horizontal de verbos: Continuar, Observar,
  Falar, Agir, Usar, Viajar, além dos toggles. “Observar” envia uma ação
  pronta; os demais focam o compositor com o prefixo.
- **2026-10-24 — P0-11:** o bootstrap entra na home assim que o servidor
  responde; `warmupStart` roda em background. O primeiro turno pode mostrar
  “Preparando a cena” enquanto o modelo carrega.
- **2026-10-24 — P0-12:** slider de tamanho de texto em Ajustes, aplicado
  à cena, leitura e chat; backlog completo no menu. Auto-avanço continua
  via “Mundo vivo”; pular cena via “Continuar”.
- **Testes:** `flutter analyze` sem issues; `flutter test` 7/7; suíte
  `test_resolution` + `test_chat_features` + `test_features_integration`
  12/12.
- **P0 concluído.** Próximo: P1 (capítulos/arcos, diário de fios, códice,
  busca full-text, árvore de ramificações, exportar crônica, retratos com
  expressões, ambiência e “enquanto você estava fora”).

---

## 17. Progresso P1

| Item | Status | Observação |
|---|---|---|
| P1-01 — Diário de fios | concluído | `GET /threads`, cards com status/prazo/setup/payoff/entidades e estatísticas |
| P1-02 — Busca full-text | concluído | `GET /search` com FTS5 + fallback LIKE; sheet de busca com resultado expansível |
| P1-03 — Exportar crônica | concluído | `GET /chronicle` gera Markdown com capítulos e recibos; exportação via SAF |
| P1-04 — “Enquanto você estava fora” | concluído | `GET /digest`, sheet no retorno e item de menu; marca a última crônica vista |
| P1-05 — Capítulos/arcos com cliffhanger | concluído | lista de capítulos, recap em diálogo ao fechar capítulo e menu próprio |
| P1-06 — Códice | concluído | `GET /codex` + tela com pessoas, lugares, lore, fatos por entidade e cartas |
| P1-07 — Árvore de ramificações | concluído | `GET /tree` + lista indentada com atual/abrir outro ramo |
| P1-08 — Retratos com expressões | concluído | 3 humores por NPC; endpoint `/portraits`, card e palco VN por `mood` |
| P1-09 — Ambiência e SFX | concluído (v1) | loop ambiente suave + dados + páginas; toggle de som |
| P1-10 — Painel do Diretor | concluído | `GET /director`, cena, última rolagem, offscreen, métricas e JSON copiável |

### 17.1 Registro de execução P1

- **2026-10-24 — P1-01:** novo endpoint `/api/sessions/{sid}/threads` e
  tela “Diário de fios” com abertos/resolvidos/abandonados, prazo,
  vencimento, setup, payoff e entidades.
- **2026-10-24 — P1-02:** busca na história com FTS5; fallback `LIKE` se a
  consulta FTS falhar. Resultado abre em diálogo com copiar.
- **2026-10-24 — P1-03:** crônica em Markdown com título, recap, capítulos
  e recibos de teste; `LocalFiles.exportText` + MIME configurável no
  `MainActivity`.
- **2026-10-24 — P1-04:** novo `/digest` com eventos offscreen e cartas/
  boatos. Ao abrir a história, se houver novidade desde a última crônica
  vista, aparece “Enquanto você estava fora”; também há item de menu para
  reabrir.
- **2026-10-24 — P1-05:** capítulos carregados por `/chapters`; nova tela
  “Capítulos” com recaps e diálogo de fechamento de capítulo (cliffhanger/
  respiro) após a criação automática.
- **2026-10-24 — P1-10:** novo `/director` e “Painel do Diretor” read-only:
  cena, presentes, ritmo, última rolagem, offscreen, métricas de latência/
  erros e estado bruto copiável em JSON.
- **2026-10-24 — P1-06:** novo `/codex` e tela “Códice” com pessoas,
  lugares (histórico de cena), lore do mundo, fatos por entidade e cartas.
  O estado passou a acumular `codex.people` e `codex.locations`.
- **2026-10-24 — P1-07:** novo `/tree` e tela “Ramificações” com indentação,
  sessão atual e botão para abrir outro ramo. Usa `parent_id`/`branch_from`
  já existentes no banco.
- **2026-10-24 — P1-08:** retratos com 3 expressões por NPC. O banco ganhou
  `characters.portraits`; `POST /api/characters/{cid}/portraits` gera
  `neutral`, `tense` e `smile`; o chat recebe `mood` no `done` e usa o retrato
  correspondente no palco VN. Ação “Gerar retratos” no card do personagem.
- **2026-10-24 — P1-08 (continuidade):** `visual_prompt`, `art_seed` e
  `visual_style` travam a identidade do personagem. A identidade é criada uma
  vez (LLM, com fallback determinístico por seed), reutilizada em capa e nas
  3 expressões, e o mesmo `seed` é mantido. O card ganhou “Identidade visual”,
  “Regerar” (com confirmação) e o formulário preserva a identidade ao gerar
  capa. Mudanças físicas limpam retratos/capa gerada para evitar imagens
  antigas fora de personagem.
- **2026-10-24 — P1-09 (v1):** áudio próprio em `assets/audio` (loop ambiente
  suave, dados e página), `AudioPlayer` no chat, SFX no d20 e nas escolhas e
  toggle “Som” no menu. Loops por local/clima ficam para um refinamento futuro.
- **Testes:** `flutter analyze` sem issues; `flutter test` 7/7; servidor
  21/21; `flutter build apk --debug` concluído.
- **Próximo:** P2 (Diretor editável, atributos/habilidades, combate opcional).

---

## 18. Progresso P2

| Item | Status | Observação |
|---|---|---|
| P2-01 — Camada situacional declarativa | concluído | Itens têm efeito por intenção e ferimentos têm penalidade de teste/CD; strings antigas continuam válidas |
| P2-02 — Ficha com o cálculo | concluído | `/sheet` expõe `inventory_detail`, `injuries_detail` e `modifiers[].total`; o app mostra item, ferimento e base |
| P2-03 — Recibo com as fontes | concluído | `situational_parts` lista item, ferimento, postura e o corte do limite; linha de fontes no recibo do app |
| P2-04 — Dificuldade por rótulo | concluído | Escolhas mostram "Dificuldade média" em vez do CD cru; números seguem no dado e no recibo |
| P2-05 — Regeneração determinística | concluído | `regen` reusa o recibo do turno (rewind guarda `pending_resolution`); teste prova d20/seed/total iguais |
| P2-06 — Destino (1 rerrolagem/capítulo) | pendente | Agora viável: o recibo sobrevive ao regen e pode ser trocado por um novo autorizado |

### 18.1 Registro de execução P2

- **2026-10-31 — P2-01:** `resolution.py` ganhou catálogo declarativo de itens
  (`ITEM_CATALOG`, efeito por intenção) e ferimentos declarativos
  (`penalty`/`dc`). O melhor item por intenção conta; itens fora do catálogo
  não alteram o teste. Buffs/ferimentos continuam na camada situacional e
  nunca editam o atributo base.
- **2026-10-31 — P2-02:** `/sheet` passou a devolver os efeitos por item, os
  ferimentos detalhados e o total com situação. A ficha no app mostra
  “faca · combate +2” e “corte · teste -1 · CD +1”.
- **2026-10-31 — P2-03:** o recibo expõe `situational_parts` (item, ferimento,
  postura e corte do limite ±3) e o bloco autorizado do narrador lista as
  fontes. A soma das partes fecha com `situational`, então o cálculo é auditável.
- **2026-10-31 — P2-04:** `preview` devolve `difficulty` (fácil/média/difícil/
  muito difícil); os cards de escolha usam o rótulo e o dado/recibo mantêm o CD.
- **Persistência:** `aux.py` preserva itens e ferimentos declarativos vindos do
  plano (dicts com `effects`/`penalty`) e `worldcheck.py` compara pelo nome sem
  achatá-los em string.
- **2026-10-31 — P2-05:** a regeneração deixou de sortear. O rewind guarda o
  recibo da resposta apagada em `pending_resolution`; o `regen` reaplica esse
  recibo no prompt e no `done`, e o app só mantém o cartão (sem reencenar o dado).
  `test_chat_regen.py` prova d20/seed/total idênticos.
