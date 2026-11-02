# legendar

Gera legendas em português do Brasil para vídeos MKV que só têm legenda em inglês, traduzindo com o Claude pela sua assinatura, sem chave de API.

O vídeo original não é alterado. O resultado é um arquivo `<nome do vídeo>.pt-BR.srt` ao lado dele, que o VLC e outros players carregam como legenda. Feito e testado para uso pessoal no macOS.

A legenda final só leva fala: efeitos, sons, música, letras de música e nomes de quem fala são removidos localmente, antes de qualquer chamada ao modelo.

## Requisitos

- **Python 3**
- **[MKVToolNix](https://mkvtoolnix.download/)**, com `mkvmerge` e `mkvextract` no `PATH`: `brew install mkvtoolnix`
- **Claude Code** (`claude`) com login em uma conta de assinatura. O programa para se `claude auth status` não mostrar `authMethod: claude.ai`, e nunca usa `ANTHROPIC_API_KEY`.
- **Command Line Tools do Xcode**, só para compilar o app: `xcode-select --install`

O MKV precisa de uma trilha de legenda em texto (SRT) marcada como inglês. Legendas em imagem, como PGS, não são aceitas.

## Como usar

### Pelo app

```sh
gui/build.sh
open Legendar.app
```

Arraste o MKV para a janela. Isso só analisa o arquivo, sem gastar limite. A tradução começa quando você clica em **Traduzir**.

O app guarda o caminho do projeto: se mover a pasta, rode `gui/build.sh` de novo.

### Pela linha de comando

Confira primeiro o que seria feito, sem chamar o modelo:

```sh
python3 -I scripts/legendar.py run "/caminho/do/video.mkv" --effort medium --dry-run
```

Depois, traduza o arquivo inteiro:

```sh
python3 -I scripts/legendar.py run "/caminho/do/video.mkv" --effort medium --max-blocks 9999
```

O padrão de `--max-blocks` é 1, para que gastar limite seja sempre uma decisão explícita. Sem ele, só o primeiro bloco é traduzido, e o SRT só é gerado quando todas as legendas estiverem prontas. A entrada também pode ser um arquivo `.srt` em inglês.

Se a saída já existir, ela é substituída.

## Como funciona

1. Extrai a legenda em inglês do MKV.
2. Remove localmente tudo o que não é fala.
3. Traduz com Sonnet em esforço médio, em blocos de cerca de 100 legendas, usando as 5 anteriores como contexto.
4. Valida o resultado e monta o SRT com os tempos originais.

Cada legenda traduzida fica em cache em `.cache/`, então uma execução interrompida retoma de onde parou sem gastar de novo.

## Uso do limite e privacidade

- As chamadas usam `claude -p` e contam nos limites da sua assinatura. Um episódio com 751 legendas levou 8 chamadas. O consumo real depende do plano e não foi medido; acompanhe em `/usage`.
- O texto das legendas é enviado ao Claude para a tradução. O vídeo não é enviado.

## Recusas e legendas sinalizadas

- **Recusa do modelo:** a legenda fica sem tradução e impede a geração do SRT. Para tentar de novo, use `--retry-refused` ou, no app, **Tentar as recusadas**.
- **Sinais de revisão:** falhas de validação, leitura acima de 22 caracteres por segundo e dúvidas marcadas pelo modelo são registradas, mas não são corrigidas automaticamente.
- **Revisão sob pedido:** `legendar.py review` manda ao modelo só o que foi sinalizado. O resultado fica em cache próprio e ainda não entra no SRT final.

## Limitações

- Ainda não existem glossário por série nem incorporação da legenda ao MKV. As decisões registradas em `CLAUDE.md` citam esses recursos, mas eles não foram implementados.
- Sem glossário, o gênero de um personagem pode sair errado quando o texto não deixa claro.
- O limite de 22 caracteres por segundo foi escolhido sem medição.

## Outros comandos

`compare`, `blind` e `unblind` comparam variantes de modelo e esforço, inclusive às cegas. Detalhes em `python3 -I scripts/legendar.py -h`.

Esses comandos gravam trechos de legenda em arquivos. Legendas e vídeos costumam ser conteúdo de terceiros, então não os publique junto com o código. O `.gitignore` cobre os nomes usuais, mas confira antes de um `git add`.

## Testes

```sh
python3 -m unittest discover -s tests
```

Os testes simulam o modelo e não gastam limite.

## Licença

[MIT](LICENSE)
