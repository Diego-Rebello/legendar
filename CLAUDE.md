# legendar

Gera legendas em pt-BR para vídeos MKV que só têm legenda em inglês. Uso pessoal, no Mac, assistindo no VLC.

## Decisões já tomadas

- **Saída padrão:** `<nome do vídeo>.pt-BR.srt` ao lado do vídeo. O vídeo original nunca é alterado.
- **`--embed` (opcional, ainda não implementado):** gera MKV novo com a trilha pt-BR. Só substitui o original depois de comparar as trilhas byte a byte, e manda o antigo para a Lixeira.
- **Sem API paga.** Toda chamada de modelo usa `claude -p` com os limites da assinatura (plano Pro). Não usar SDK, Batch API nem `ANTHROPIC_API_KEY`.
- **Etapas sem LLM ficam em script.** Só tradução, glossário e revisão consomem limite.
- **A saída só leva fala.** Sem efeitos, sons, música, letras de música nem nome de quem fala. A remoção é local e acontece antes do modelo.

## Pipeline

| Etapa | Executor |
|---|---|
| Extrair legenda, compactar sem tempos, validar, remontar | Python + MKVToolNix |
| Remover efeitos, sons, música, letras e nomes | Python |
| Glossário por série (nomes, apelidos, gênero dos personagens) | Sonnet, uma vez por temporada (ainda não implementado) |
| Tradução | Sonnet em esforço médio, em blocos de cerca de 100 legendas, com as 5 anteriores como contexto |
| Revisão do que foi sinalizado | Só sob pedido, com `legendar.py review`: não compensou como etapa automática |
| Exceções (Sonnet falhou duas vezes, ou pedido manual) | Opus (ainda não implementado) |

Reprocessar só legendas com problema. Guardar o resultado em cache local por legenda, para retomar sem gastar de novo. Ao encurtar uma legenda, conferir se o sentido foi mantido.

Sinais para revisão: falha de validação, velocidade de leitura acima do limite, recusa (`stop_reason: refusal`) e dúvida marcada pelo próprio modelo.

## Interface

`gui/Legendar.swift`, compilado com `gui/build.sh`, gera `Legendar.app` na raiz (fora do git). Usa SwiftUI puro: só com as Command Line Tools, `@State` não compila por falta do plugin de macro, então o estado fica em `ObservableObject`.

- Arrastar o MKV faz um `--dry-run`, sem modelo. Só o clique em Traduzir gasta limite.
- O app só chama `legendar.py run`, sem lógica própria de legenda. Ele lê as linhas `Extraindo:`, `Trilha`, `Blocos nesta rodada:`, `Progresso:`, `Recusadas:`, `Saída já existe:`, `Traduzindo:`, `Bloco` e `Gerado`, travadas em `test_output_lines_read_by_the_app`.
- Cancelar só manda SIGTERM ao Python: é `legendar.py` que encerra o `claude` ou o `mkvextract` em andamento.
- Recusa fica em cache sem tradução e não conta como pronta. Só volta ao modelo com `--retry-refused`, no app o botão Tentar as recusadas, que só aparece quando não há mais blocos inéditos.
- O percentual da tradução conta legendas prontas, incluindo o cache, e não anda durante uma chamada. A barra geral usa pesos estimados.

## Chamada do modelo (testada em 2026-11-02)

```
env -u ANTHROPIC_API_KEY -u CLAUDE_CODE_EFFORT_LEVEL claude -p "<prompt>" --model sonnet --effort medium \
  --tools "" --strict-mcp-config --setting-sources "" \
  --disable-slash-commands --no-session-persistence \
  --system-prompt "<instruções>" --output-format json
```

- Rodar a partir de uma pasta sem `CLAUDE.md`, ou confirmar que este arquivo não entra no contexto.
- Essa combinação custou cerca de 600 tokens de entrada por chamada no piloto. Em 2026-11-13 o custo fixo foi de cerca de 1.150 tokens, contando o `SYSTEM_PROMPT`. Por isso uma sobra de até 1/4 do bloco entra no último bloco.
- Em 2026-11-13, toda a entrada veio como `cacheCreationInputTokens` (TTL de 1 h, US$ 4/M contra US$ 10/M da saída) e nada foi lido do cache: cada prompt é diferente e o prefixo fixo é curto demais para o cache. Isso foi 22% do equivalente API do episódio. `DISABLE_PROMPT_CACHING=1` ainda não foi testado aqui.
- `--bare` não serve: ele só aceita chave de API e ignora o login da assinatura.
- Antes de rodar, conferir `claude auth status`: abortar se `authMethod` não for `claude.ai`.
- `--json-schema` existe para saída estruturada, mas ainda não foi testado aqui.
- Medir tokens por `modelUsage`, não por `usage`: no piloto, `usage` omitiu 3.112 de entrada e 452 de saída, e só `modelUsage` fecha com `total_cost_usd`. Separar por modelo: no piloto, cada chamada incluía uma requisição auxiliar do Haiku com cerca de 3.100 de entrada, mesmo quando o modelo pedido era o Sonnet. No s02e03, em 2026-11-13, ela não apareceu em nenhuma das 9 chamadas.
- `--effort` é sempre explícito e faz parte da chave do cache. `CLAUDE_CODE_EFFORT_LEVEL` tem precedência sobre a flag, por isso sai do ambiente. O JSON não informa o nível efetivo.

## Piloto (concluído em 2026-11-02)

Fica em `pilot/<episodio>/`, que não vai para o git:

- `en.srt` e `en.tsv`: original (964 legendas).
- `pt-BR.srt`: tradução de referência, feita à mão com Opus. Boa base, mas não é gabarito.
- `pt1..4.txt` e `overrides.txt`: partes de que `pt-BR.srt` foi montado. O script que as juntava saiu do repositório (ver o histórico do git).

Comparou Haiku sozinho, Haiku + Sonnet e Sonnet sozinho com a referência, numa amostra de 30 legendas, registrando os tokens de cada variante, para adotar o mais simples que mantivesse a qualidade.

Continua valendo: ao mudar prompt ou numeração, rodar primeiro um bloco só (`--max-blocks 1`) e conferir, antes de gastar limite com o episódio inteiro. Evitar rodadas em paralelo: o plano Pro tem limite baixo.

No primeiro teste, sem contexto, o Haiku traduziu "I got this." como "Eu me entendi com isso." O certo seria "Eu dou conta."

Resultado às cegas de 2026-11-02, bloco 401-507, detalhes em `pilot/<episodio>/cega-resultado.md`:

- Sem as letras (26 legendas, nota de 0 a 3): `haiku:high` 2,58 e `sonnet:medium` 2,85. Nenhuma das duas errou o sentido.
- `sonnet:medium` deixou em inglês as 8 letras do bloco, mas letras agora saem antes do modelo.
- O Sonnet custou de 15 a 20 vezes mais em equivalente API. O consumo do limite do plano não foi medido.
- Avaliação feita pelo Opus, a pedido do usuário.

Haiku + Sonnet, revisando 27 legendas sinalizadas, detalhes em `pilot/<episodio>/cega2-resultado.md`:

- Estimativa nas mesmas 26 legendas: Haiku 2,58, Haiku + Sonnet 2,54, Sonnet 2,85. Só 5 das 25 linhas comparadas eram de fato cegas para o avaliador.
- A revisão zerou os sinais, mas a escala não mede velocidade de leitura. Ao encurtar, a legenda 496 perdeu o sentido.
- A revisão gastou 7.899 tokens de raciocínio. O conjunto custou cerca de 3 vezes o Sonnet sozinho em equivalente API.
- 26 das 27 foram sinalizadas por velocidade. Com 25 cps, seriam 15.
- Decisão do usuário em 2026-11-02: Sonnet médio sozinho. O SRT final de `run` não aplica revisão.
- Em 2026-11-10 o ♪ saiu do `SYSTEM_PROMPT` (o `PROMPT_ID` mudou de 70a6ccdc para d5b5f965). Os caches do piloto ficaram com o ID antigo: `review`, `compare` e `blind` sobre eles não acham mais a tradução, e um `run` novo nesses episódios traduz tudo de novo.

Episódio inteiro com `sonnet:medium` (751 legendas, 8 chamadas): cerca de US$ 0,27 em equivalente API. Uma chamada gastou 4.075 tokens de raciocínio, e as outras sete, de 320 a 643. 75 legendas ficaram acima de 22 cps. O consumo do limite do plano ainda depende da leitura do `/usage`.

## Lições da primeira execução

- O Sonnet às vezes junta duas legendas de uma mesma frase e renumera as seguintes, deslocando as traduções em uma posição sem erro aparente. Em 2026-11-07, no s02e02, aconteceu em 2 de 11 blocos (349-452 e 785-893), e o parser antigo pôs os dois desalinhados no cache. Agora a resposta só entra se os IDs baterem exatamente com o bloco, na ordem; senão o bloco volta inteiro, uma vez, na mesma rodada. Respostas antigas podem ser reauditadas pelo `raw/` do cache.
- O bloco 349-452 errou duas vezes do mesmo jeito: depois de cada ID que faltava por ter sido removido (395, 406, 434, 438), o modelo escrevia esse ID no lugar do seguinte. Ele conta, não copia. Por isso cada prompt de tradução numera as falas a partir de 1, contando o contexto, e converte de volta; cache e SRT usam o ID real. Numerar pela posição no episódio não bastava: ao retomar, um bloco pode juntar trechos distantes do que ficou pendente, e o salto volta. Os números citados em `ID fora do bloco` são os do prompt. A revisão ainda usa os IDs reais.
- A numeração por prompt e o prompt sem ♪ rodaram no s02e03 em 2026-11-13: 9 de 9 blocos alinhados de primeira, sem descarte. 810 legendas, cerca de US$ 0,41 em equivalente API. 53% da saída foi raciocínio, de 0 a 3.623 tokens por bloco.
- Limite de 22 caracteres por segundo foi escolhido sem medir. 96 legendas ainda passam dele; rever o critério. No s02e03, 58 das 103 sinalizadas por cps já passavam de 22 no inglês. Só 54 ficaram acima de 22 e mais de 10% acima do inglês. Com 25 cps e a mesma regra, seriam 31. O fim de cada `run` mostra quantas foram sinalizadas e por quê.
- Ao encurtar, perderam-se detalhes nas legendas 277, 779 e 882.
- O gênero de alguns personagens não dá para saber só pelo texto; registrar no glossário.
- No macOS, `sed` não remove BOM com `\xEF`. Comparar legendas em Python.
- Scripts que leem arquivos baixados: `python3 -I`, com os scripts fora da pasta dos dados.

## Dados

Não publicar `pilot/`, MKVs nem SRTs: são conteúdo de terceiros.
