# Notebooks — conciliação de notas fiscais × cartão (Nekt)

Cada arquivo é um **notebook Python da Nekt**, na ordem de execução. Antes de rodar, procure por `<` em cada arquivo e troque os **placeholders** `<ASSIM>` pelos seus valores (a legenda completa está no [README principal](../README.md#os-códigos-com-placeholders)).

## Ordem de execução

| # | Arquivo | O que faz | Tabela gerada na Nekt |
|---|---|---|---|
| 1 | [`etapa-1-2-reconhecer-classificar.py`](etapa-1-2-reconhecer-classificar.py) | Reconhece anexos e classifica (é nota fiscal?) lendo o conteúdo com IA; extrai o **número** da nota | `Trusted.invoices_classificadas` |
| 2 | [`etapa-4-5-6-arquivar-etiquetar-tabelar.py`](etapa-4-5-6-arquivar-etiquetar-tabelar.py) | Arquiva no Drive (pasta do mês), etiqueta o email, **deduplica por número (mantém PDF)** e monta a tabela de invoices | `Trusted.invoices` |
| 3 | [`etapa-7-extrato-cartao.py`](etapa-7-extrato-cartao.py) | Lê as faturas (IA de visão), mascara portadores e valida o total | `Trusted.transacoes_cartao` |
| 4 | [`etapa-8-9-conciliacao.py`](etapa-8-9-conciliacao.py) | Concilia notas × cartão (1:1 + bloco agregado por competência) | `Trusted.conciliacao` · `Trusted.conciliacao_agregada` |
| 5 | [`etapa-10-resumo-saida.py`](etapa-10-resumo-saida.py) | Gera as tabelas de saída + resumo gerencial → Google Sheets (3 abas) | `Trusted.saida_invoices` · `saida_transacoes` · `saida_resumo` |

> A Etapa 3 (renomear) está embutida na Etapa 2 (passo 4-5-6 usa o nome no padrão `AAAA.MM.DD - Fornecedor - MOEDA VALOR`).

## Dependências (por notebook, na Nekt)
- **1 e 3:** `litellm`, `gcsfs`
- **2:** `requests`, `gcsfs`
- **4 e 5:** nenhuma além do padrão (pandas)

## Pré-requisitos no ambiente
- Conectores de **email** (Gmail/Outlook) e **Drive** ligados na Nekt.
- **Secrets** cadastrados: chave do LLM e credenciais OAuth do Google.
- Um **Volume** com os PDFs das faturas de cartão.
- Um **destination** de Google Sheets (para a Etapa 10).
