# Da caixa de email ao fechamento, sem digitar

### Como construir uma conciliação de notas fiscais × cartão de crédito conversando com uma IA, em cima da Nekt — e por que finanças pode confiar nisso

> Por **Julia Mello**, Head of Finance na [Nekt](https://nekt.com) · `julia@nekt.com`

Este repositório acompanha o workshop em que essa automação foi construída. Aqui está o resumo direcional de **tudo que foi pedido (os prompts) e tudo que foi gerado (os códigos)**, na ordem em que aconteceu — para que você reproduza o mesmo fluxo no seu ambiente. Cada passo traz o *porquê* em linguagem de negócio.

> **Como reproduzir:** entregue este guia a um assistente de IA com acesso à sua [Nekt](https://nekt.com) e diga *"me ajude a construir isto, uma etapa por vez"*. Os prompts abaixo são genéricos — adapte nomes de tabelas, pastas e fornecedores ao seu caso. *(Os números e exemplos deste README são ilustrativos; nenhum dado real da empresa é exposto.)*

---

## O problema

Todo mês, o time de finanças faz a mesma maratona: abrir a fatura do cartão corporativo, achar a nota fiscal de cada lançamento perdida no email, renomear, arquivar no Drive, e então **bater uma a uma** — o que conciliou, o que não conciliou, e por quê. É manual, demora horas, e a divergência costuma aparecer só no fechamento.

**A proposta:** deixar a parte repetitiva com uma IA — de forma **rastreável o suficiente para auditoria**.

---

## A ideia em uma frase

> **Claude é o cérebro. Nekt é a memória.**

- **O cérebro (a IA)** entende linguagem, lê documentos, decide "isto é uma nota fiscal", escreve o código. Mas cérebro sem memória confiável *inventa*.
- **A memória (a Nekt)** conecta os sistemas (email, Drive, cartão), guarda tudo num lugar **governado, versionado e auditável**, e entrega para a IA um dado em que dá para confiar.
- **A ponte entre os dois é o MCP** (*Model Context Protocol*) — uma "tomada universal" que liga a IA aos seus dados na Nekt com segurança, sem gambiarra.

**Você não fica preso a uma IA só.** A Nekt conversa com vários modelos (via uma camada de roteamento de LLMs). Aqui usamos **Claude**, mas dá para usar **ChatGPT, Codex, Gemini** e outros — trocar é, na prática, **mudar uma linha** (o nome do modelo). A arquitetura não amarra a empresa a nenhum fornecedor de IA.

---

## Pré-requisitos

| Componente | Papel |
|---|---|
| **Nekt** (plataforma de dados governada) | Conecta os sistemas, organiza em camadas, versiona e audita; executa os *notebooks*. |
| **Assistente de IA com acesso via MCP** | Lê as tabelas da Nekt, classifica documentos e gera código. Ex.: Claude, ChatGPT/Codex, Gemini. |
| **Conector de email** (Gmail/Outlook) na Nekt | Traz emails (e anexos) para dentro da plataforma. |
| **Conector de armazenamento** (Drive) na Nekt | Para arquivar os documentos organizados. |
| **Credenciais em cofre (Secrets da Nekt)** | Chaves OAuth guardadas como *secrets*, nunca no código. |

**Princípio de fronteira:** *conectar a caixa de email é feito por uma pessoa, na interface da Nekt* (login OAuth com a própria conta) — não pela IA. Autorizar acesso à própria caixa é como dar a chave de casa: só o dono assina.

---

## Arquitetura, em um diagrama

```
  ENTRADA 1: NOTAS FISCAIS                         ENTRADA 2: CARTÃO DE CRÉDITO
  ┌────────────┐                                   ┌────────────────┐
  │  Gmail     │  emails + anexos                  │  PDFs de       │
  │ (conector  │                                   │  fatura        │
  │  na Nekt)  │                                   │                │
  └─────┬──────┘                                   └───────┬────────┘
        │                                                  │
        ▼                                                  ▼
  ┌───────────────────────────  N E K T  ───────────────────────────┐
  │  IA classifica (é nota fiscal?)      IA de visão lê cada PDF,   │
  │  renomeia, extrai data/valor          valida total da fatura    │
  │         │                                      │                │
  │         ▼                                      ▼                │
  │  Drive (pasta do mês) + label    Trusted.transacoes_cartao      │
  │  Trusted.invoices (com link)                                    │
  │         └──────────────┬───────────────────────┘                │
  │                        ▼                                        │
  │              CONCILIAÇÃO (1:1 + bloco agregado p/ recorrentes)  │
  │                        ▼                                        │
  │         conciliado · não-conciliado · resumo gerencial          │
  └────────────────────────┬────────────────────────────────────────┘
                           ▼
                  Google Sheets (3 abas, formatado)
```

Duas origens, uma memória governada, um resultado auditável.

---

## Os 10 passos — prompts e códigos, na ordem

Cada etapa virou um **notebook Python na Nekt**, gerado a partir de um pedido em português. Construa **uma etapa por vez** e valide antes de seguir.

### Etapa i + ii — Reconhecer e classificar

**O que pedi (direcional):**
> "Varra o histórico de emails na Nekt, separe os que têm anexo, e use IA para decidir quais são **nota fiscal / invoice / recibo** de algo que a empresa comprou. Inclua NF/NFS-e/invoice/receipt/recibo; **exclua** boleto, fatura sem nota, guias de imposto (DARF, DAS, ISS, PIS, COFINS), contratos, notificações de pagamento recebido e marketing. Nada de jogar fora em silêncio — o que estiver em dúvida vira pilha de *revisar*."

**O que a IA construiu:** um pré-filtro por palavra-chave (barato) seguido de classificação por conteúdo com IA, em paralelo. Grava `Trusted.invoices_classificadas` com todos os anexos, candidatos classificados e ruído marcado. O prompt de classificação:

```
Você é um classificador rigoroso de documentos financeiros do contas a pagar.
Diga se este email contém uma NOTA FISCAL, INVOICE, RECEIPT ou RECIBO de um
produto/serviço que a empresa COMPROU ou PAGOU.
... Responda APENAS com JSON: is_documento_fiscal, tipo_documento,
fornecedor, confianca (alta/media/baixa), motivo.
Na dúvida, use confianca "baixa". Seja conservador.
```

**Por quê:** classificar pelo *nome do arquivo* erra nos dois sentidos — deixa passar guia de imposto e quase perde "Nota Fiscal TMW". A IA lê o **conteúdo** e, na dúvida, **marca para revisar**. *Detectar ≠ classificar.*

---

### Etapa iii — Renomear num padrão único

**O que pedi (direcional):**
> "Para cada nota, leia o PDF com IA de visão e extraia **fornecedor, número, moeda, valor e data**. Renomeie para `AAAA.MM.DD - Fornecedor - Moeda Valor`."

**O que a IA construiu:** leitura do documento com IA de visão e a função de renomeação. O prompt de extração:

```
Extraia os dados deste documento fiscal. Responda APENAS com JSON:
fornecedor, numero, moeda, valor (só o número), data (AAAA.MM.DD).
```

**Por quê:** acabou o `documento (3).pdf`. Tudo fica buscável e com data/valor confiáveis para a conciliação.

---

### Etapa iv + v + vi — Arquivar, etiquetar e tabelar

**O que pedi (direcional):**
> "Salve cada nota no Drive, na **pasta do mês da nota** (derive a pasta da data, sem lista manual). Não duplique se eu rodar de novo. Marque o email com a label **anexo salvo** ou **anexo não salvo** — e **mantenha o email não lido**. Por fim, monte a tabela de invoices com fornecedor, data, moeda, valor, anotação da IA e o **link** do arquivo."

**O que a IA construiu:** arquivamento idempotente (dedup por nome+tamanho) na pasta do mês, aplicação de labels mantendo o email não lido, e gravação de `Trusted.invoices` com o link do Drive.

**Por quê:** a label é uma **trilha de auditoria visível** no próprio Gmail. O "não lido" garante que a automação não bagunça a caixa de ninguém. A tabela é a base governada da conciliação.

---

### Etapa viii — Extrato do cartão *(preparatório)*

**O que pedi (direcional):**
> "Leia os **PDFs de fatura** dos cartões com IA de visão, extraia cada lançamento, **mascare dados sensíveis** (ex.: nome do portador), e **valide** a soma contra o total de cada fatura. Só grave a tabela se cada fatura bater."

**O que a IA construiu:** extração por visão dos extratos, mascaramento de portadores, validação banco-a-banco contra o total, e gravação de `Trusted.transacoes_cartao` — **com trava: só grava se conferir**.

**Por quê é preparatório:** essa etapa é feita *antes* e validada por uma pessoa. É conferência, não chute — e mostra que dado sensível (extrato) entra na Nekt já tratado e governado.

---

### Etapa vii + ix — Conciliação

**O que pedi (em português):**
> "Concilie as notas com as transações do cartão. Case por **valor + moeda + data**. Gastos recorrentes pulverizados (ex.: anúncios — Meta/Facebook, Google Ads) entram em **bloco agregado por competência** (várias cobranças do mês contra as notas daquela competência). Deixe o **não-conciliado visível** — é controle, não erro."

**O que a IA construiu:** conciliação 1:1 por valor/moeda/data para fornecedores normais + um bloco agregado mensal para gastos recorrentes. Grava `Trusted.conciliacao` (agregados marcados como `status='agregado'`) e `Trusted.conciliacao_agregada` (cobrado × notas × diferença).

**Por quê:** é o momento de maior impacto. A coluna **não-conciliado** é um **controle de auditoria visível** — mostrada com orgulho, não escondida.

---

### Etapa x — Resumo gerencial e saída

**O que pedi (direcional):**
> "Gere as tabelas de apresentação — invoices, transações e um **resumo gerencial** (nº de transações, total no cartão, nº de invoices, total conciliado, divergências por competência) — organizadas, com status e a anotação da IA. Entregue num **Google Sheets** de 3 abas."

**O que a IA construiu:** `Trusted.saida_invoices`, `saida_transacoes` e `saida_resumo`, entregues a um *destination* do Google Sheets na Nekt. A formatação visual (cores dos status e moedas) é aplicada na planilha e **persiste entre as atualizações**.


---

## Segurança e governança — por que finanças pode confiar

| Princípio | Como aparece no projeto |
|---|---|
| **SOC 2 Type II** | A Nekt é auditada de forma independente. |
| **Permissão por tabela/camada** | Cada pessoa vê só a camada que pode ver. |
| **Lineage (rastreabilidade)** | Dá para rastrear de onde veio cada número. |
| **Chaves em cofre (Secrets)** | As credenciais OAuth nunca ficam no código; são revogáveis a qualquer momento. |
| **Nada descartado em silêncio** | Dúvidas viram pilhas de *revisar*; pontos cegos são marcados e contados. |
| **Não-conciliado é controle** | A divergência é exibida como métrica, não como erro a esconder. |

---

## Notas de adaptação

- **Modo mutirão × incremental:** a primeira execução processa todo o histórico (pode levar minutos). No dia a dia, ligue o **modo incremental** — processe só os emails novos; leva segundos.
- **Anexos como arquivo governado:** quando a source de email traz os anexos para um *volume* da Nekt, a IA lê o conteúdo de lá — e enxerga exatamente o que você curou.
- **Fornecedores agregados:** ajuste a lista de gastos recorrentes (anúncios, nuvem, SaaS por consumo) que entram no bloco agregado por competência.
- **Deduplicação por número:** a mesma nota costuma chegar em vários formatos (PDF, XML, imagem) ou cópias. A etapa 1+2 extrai o **número único** do documento, e a etapa 4+6 deduplica por **(fornecedor + número)**, mantendo o **PDF** — assim a mesma nota vira 1, mas notas distintas com mesmo valor/data (ex.: várias assinaturas) **não** são fundidas. Sem número legível, faz dedup conservador por valor+data e marca `dedup_status = "sem_numero_revisar"` para auditoria.
- **Resultado:** em um mês real, este fluxo capturou **centenas de invoices** e **dezenas de transações de cartão**, conciliando a maioria 1:1 e deixando o restante como controle visível. Os números variam conforme o volume da empresa.

---

## Os códigos (com placeholders)

O código de cada notebook está na pasta [`notebooks/`](notebooks/) — um `.py` por etapa, já com os pontos confidenciais e específicos do nosso ambiente trocados por **placeholders** `<ASSIM>`. Abra o arquivo, procure por `<` e substitua pelos seus valores.

**Legenda dos placeholders:**

| Placeholder | O que colocar |
|---|---|
| `<URL_DO_PROXY_LLM>` | Endpoint do seu gateway de LLM (ex.: LiteLLM) ou a API direta do provedor |
| `<NOME_DO_SECRET_LLM>` | Nome do *secret* (na Nekt) que guarda a chave de API do modelo |
| `<MODELO_LLM>` | ID do modelo — ex.: `anthropic/claude-...`, `gemini/...`, `openai/gpt-...` |
| `<SECRET_GOOGLE_CLIENT_ID>` · `<SECRET_GOOGLE_CLIENT_SECRET>` · `<SECRET_GOOGLE_REFRESH_TOKEN>` | Nomes dos *secrets* com as credenciais OAuth do Google (Gmail + Drive) |
| `<ID_DA_PASTA_MAE_NO_DRIVE>` | ID da pasta do Google Drive onde arquivar os anexos |
| `<NOME_DO_VOLUME_COM_AS_FATURAS>` | Nome do *Volume* (na Nekt) com os PDFs das faturas de cartão |
| `<NOME_DA_SUA_EMPRESA>` | Razão social / nome da empresa (para ignorar como "portador") |
| `<BANCO_1..3>` · `<TOTAL_FATURA_1..3>` | Bancos dos cartões e o total de cada fatura (trava de validação) |
| `<palavra_no_nome_do_arquivo_*>` | Trecho do nome do arquivo da fatura que identifica o banco |
| `<APELIDO_NO_CARTAO>` · `<NOME_CANONICO>` | Quando um fornecedor aparece com nome diferente no cartão vs na nota |
| `<MES/ANO>` | Competência das faturas processadas (ex.: 05/2026) |

> Os nomes de tabela/camada (`Raw`, `Trusted`, `Conciliacao`, `invoices`…) seguem o padrão de camadas da Nekt — ajuste se você usar outra organização. As funções `nekt.load_table`, `nekt.save_table`, `nekt.load_volume`, `nekt.load_secret` são da Nekt.

| Etapa | Arquivo (clique para abrir) | Tabela(s) gerada(s) na Nekt |
|---|---|---|
| **1 + 2** — Reconhecer & Classificar | [`notebooks/etapa-1-2-reconhecer-classificar.py`](notebooks/etapa-1-2-reconhecer-classificar.py) | `Trusted.invoices_classificadas` |
| **4 + 5 + 6** — Arquivar, Etiquetar & Tabelar | [`notebooks/etapa-4-5-6-arquivar-etiquetar-tabelar.py`](notebooks/etapa-4-5-6-arquivar-etiquetar-tabelar.py) | `Trusted.invoices` |
| **7** — Extrato do cartão | [`notebooks/etapa-7-extrato-cartao.py`](notebooks/etapa-7-extrato-cartao.py) | `Trusted.transacoes_cartao` |
| **8 + 9** — Conciliação | [`notebooks/etapa-8-9-conciliacao.py`](notebooks/etapa-8-9-conciliacao.py) | `Trusted.conciliacao` · `Trusted.conciliacao_agregada` |
| **10** — Resumo & Saída | [`notebooks/etapa-10-resumo-saida.py`](notebooks/etapa-10-resumo-saida.py) | `Trusted.saida_invoices` · `saida_transacoes` · `saida_resumo` |

> A ordem de execução, as dependências e os pré-requisitos de cada notebook estão em [`notebooks/README.md`](notebooks/README.md).


---

## Quer fazer igual?

- 📦 **O código deste projeto está aqui, aberto.** Use, adapte, melhore.
- 📅 **Quer ver isso rodando no _seu_ cenário?** Marque uma call com o **Antonio** — a gente mostra a Nekt no seu contexto, com seus dados.

> *Construído com Claude (cérebro) + Nekt (memória).*
