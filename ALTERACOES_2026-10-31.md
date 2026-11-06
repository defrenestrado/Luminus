# Alterações — 2026-10-31 — camada situacional declarativa

Aplicação do playbook `luminus-rpg` ao ponto que mais violava a regra
“dado é drama, não planilha”: itens e ferimentos eram resolvidos por
palavras-chave escondidas dentro de `resolution.py`, e o recibo só mostrava
`+ situação (+2)` sem dizer de onde vinha.

## O problema

- `_situational_modifier` procurava `"faca"`/`"espada"` no inventário e dava
  `+2` em combate, mas só combate; qualquer outro item era decorativo e mudo.
- Ferimentos valiam sempre `-1` no teste e `+1` no CD, mesmo “costela
  quebrada” e “arranhão”.
- A ficha mostrava o modificador final, sem o cálculo; o recibo escondia a
  fonte da camada situacional.
- `aux.py` achatava inventário/ferimentos em `str(item)`, então um item
  declarativo (`{"name", "effects"}`) perderia o efeito no próximo plano.

## O que mudou

### Motor (`server/resolution.py`)

- `ITEM_CATALOG`: dado declarativo com `id`, apelidos e efeito por intenção.
  Arma `+2` em combate, disfarce `+1` em furtividade, traje `+1` em social,
  kit `+1` em investigação, corda `+1` em movimento, tônico `+1` em
  resistência.
- Itens aceitam string legada (busca no catálogo) ou dict declarativo:
  `{"name": "Faca de sal", "effects": {"combate": 1, "furtividade": 2}}`.
  Só o melhor item por intenção conta; item fora do catálogo não altera o teste.
- `normalize_injuries`/`injury_info`: ferimento declarativo com `penalty`
  (teste) e `dc` (dificuldade); strings antigas continuam valendo `1/1`.
- `situational_breakdown` devolve o total **e as fontes** (item, postura,
  ferimento, corte do limite ±3). A soma das partes fecha com o total.
- `Receipt.situational_parts` entra em `as_dict()` e no bloco autorizado do
  narrador (`Camada situacional: faca +2, corte -1`).
- `modifier_table(..., state=...)` expõe `item`, `item_bonus`,
  `injury_penalty`, `situational` e `total` por intenção.
- `preview` devolve `difficulty` (fácil/média/difícil/muito difícil).
- `inventory_detail` e `injuries_detail` alimentam a ficha.

### Estado (`server/aux.py`, `server/worldcheck.py`)

- Itens e ferimentos em dict são preservados (nome + efeitos/penalidade) em
  vez de virarem `str(dict)`.
- Inventário/ferimentos no prompt continuam legíveis (só o nome).
- `worldcheck` compara itens pelo nome e mantém a estrutura declarativa.

### App (`app/lib/api.dart`, `app/lib/screens/chat_screen.dart`)

- `Choice.difficulty`; os cards mostram `Teste · Dificuldade média` (o CD
  exato continua no dado e no recibo).
- Ficha: item mostra efeito (`faca · combate +2`), ferimento mostra
  `teste -1 · CD +1`, e cada modificador mostra total + base + situação.
- Recibo do dado ganhou uma linha com as fontes da camada situacional.

## Compatibilidade

- Strings antigas de inventário/ferimentos continuam funcionando: `"faca"`
  entra no catálogo e `"corte"` vale `-1/+1`.
- `modifiers[].modifier` mantém o valor base (atributo + perícia + sorte);
  o novo `total` já inclui a situação, então clientes antigos não quebram.
- Nenhuma migração de banco: tudo vive no ledger/estado já existente.

## Testes

```
cd server && .venv/bin/python -m unittest \
  test_resolution test_character_attributes test_chat_features \
  test_features_integration test_aux_scene test_world_pack \
  test_packages test_quality test_offscreen test_world_voices \
  test_npc_forge test_guard
# 96 testes OK

cd app && flutter analyze   # No issues found
cd app && flutter test      # 8/8
```

## Próximo

- **Destino** (1 rerrolagem por capítulo) só faz sentido quando o recibo já
  mostra as fontes e a prosa pode ser reescrita a partir do novo resultado.
- O catálogo é intencionalmente pequeno; itens de mundo podem declarar
  `effects` próprios sem tocar no motor.

---

## Regeneração determinística (2026-10-31)

**Problema:** o botão “Regenerar” apagava a resposta, fazia rewind e pedia o
texto de novo com `regen=true`. O servidor pulava a resolução nesse caminho: o
recibo sumia e a prosa podia transformar uma falha em sucesso — um rerrolar
grátis, contra a regra 4 da skill (`sessão:turno:sequência:intenção`).

**Correção:**

- `resolution.Receipt.from_dict` reconstrói o recibo persistido.
- `POST /sessions/{sid}/rewind` guarda o recibo da resposta apagada em
  `pending_resolution` no ledger (só quando o alvo é uma fala do jogador).
- No `regen`, o servidor reutiliza o recibo pendente (ou o `resolution` atual,
  quando a ação bate) e o injeta de novo no bloco autorizado; **não rola d20**.
- O app reescreve via rewind (sem deletar antes) e não reencena o dado no regen.
- `pending_resolution` é limpo no turno seguinte.

**Teste:** `server/test_chat_regen.py` — d20, seed e total idênticos após
rewind + regen; o recibo reaparece na mensagem reescrita.

---

## Auditoria com as skills do `claude-skills`

- **skill-security-auditor:** PASS em `performance-profiler`,
  `api-test-suite-builder` e `pr-review-expert`; WARN só na fixture de exemplo
  do `tech-debt-tracker` (não executada).
- **tech-debt-tracker:** 37 arquivos / 19.332 linhas. Débito real:
  `worldcheck.validate` (complexidade 107, 155 linhas) e
  `world_pack.import_world` (98, 281 linhas). O resto é ruído
  (docstrings, duplicação de boilerplate, TODO falso-positivo em “todos”).
- **performance-profiler:** nenhum arquivo grande ou dependência suspeita; o
  gargalo real continua sendo latência do LLM, já coberta pelo dado como máscara.
- **api-test-suite-builder:** 86 rotas, 55 sem teste direto. A rota crítica deste
  ciclo (regeneração) ganhou teste; CRUD de places/facts e rotas de mídia ficam
  para o próximo ciclo.
- **pr-review-expert:** a revisão do fluxo de chat encontrou o furo do regen
  acima; sem segredos, sem quebra de contrato (apenas campos aditivos).

---

## Velocidade de geração — avaliação e ajuste (2026-10-31)

Benchmark controlado no llama.cpp `b11998`, 27B Q4_K_M, 3×3060 Ti (GPUs 0,1,3),
KV `q8_0`, mesmo prompt, aquecimento antes de medir, 200 tokens, `cache_prompt`.

| Configuração | ctx 8k | ctx 32k (~6,2k de prompt) |
|---|---:|---:|
| Sem especulação | 17,7 tok/s | — |
| MTP n=1 | 22,2 | — |
| **MTP n=2** | **25,6** | **25,7–27,4** |
| MTP n=3 (antigo) | 23,9 | 25,1 |
| MTP n=4 | 18,4 | — |
| MTP n=3 + ngram | 23,7 | — |

O n=3 reproduziu o log real (~24,5 tok/s), então a comparação é confiável.

### Aplicado

- `scripts/install_llamacpp.sh`, `scripts/llamacpp_shim.py` e o `config.env`
  ativo passaram de `DRAFT_N=3` para `DRAFT_N=2`.
- Serviço `luminus-llamacpp` reiniciado; GPUs e shim verificados.
- Ganho: ~7% em 8k e ~2,5–9% em 32k, sem custo de qualidade (mesmo modelo,
  só menos tokens de rascunho por passo).

### Draft model: testado e descartado

- `piskle/Qwen3.8-0.6B-GGUF` não serve: apesar do nome, é um Qwen3 com
  vocabulário 151.936 (`pre=qwen2`); o alvo tem 248.320 (`pre=qwen35`).
  O llama.cpp recusa com “target and draft vocabs are not compatible”.
- `TEKIBO/Qwen3.8-1.0B-A0.6B-Q4_0` tem o vocabulário correto (248.320,
  hash `49930cc8a75188cf`) e carrega, mas aceitou **0 de 296** tokens
  (3,4 tok/s). Combinar `draft-mtp,draft-simple` falha ao criar o contexto MTP.
- Conclusão: `draft-simple` não é viável neste modelo; o MTP continua sendo o
  único especulador que compensa.

### Outras conclusões

- `ngram-simple`/`ngram-map-k4v` aceitaram exatamente os mesmos tokens do MTP
  puro (zero ganho em prosa criativa): manter desligado.
- O cache de prompt já reusa o prefixo (4 tokens reprocessados por turno);
  soluções tipo SGLang/vLLM com RadixAttention não ajudariam aqui.
- Próximo ganho real: reduzir tokens gerados (`NARRATION_TOKENS=1000`) e a
  latência do planejador, não trocar o engine.
