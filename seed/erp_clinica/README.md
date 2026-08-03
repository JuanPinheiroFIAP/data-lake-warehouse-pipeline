# ERP Rede Clínica — banco operacional fictício

Fonte relacional do pipeline. Simula o ERP de uma rede fictícia de 15 clínicas
médicas: marketing, CRM, agenda, atendimento clínico, orçamentos, faturamento e
cobrança.

> **Nada aqui é real.** Empresa, unidades, convênios, médicos, pacientes, CPFs e
> CNPJs são gerados. Os dígitos verificadores de CPF/CNPJ são calculados
> corretamente apenas para que o pipeline exercite validação de documento — os
> números são derivados de contador, não pertencem a ninguém.

## Por que esse banco existe

Chinook e Pagila são bancos de exemplo excelentes para aprender SQL e péssimos
para demonstrar Engenharia de Dados: não têm CDC, não têm N:N com atributo, não
têm histórico de status, não têm cardinalidade desbalanceada e, principalmente,
não têm um segundo dia. Este banco foi modelado para ter os problemas que
aparecem numa fonte corporativa de verdade — e que o pipeline vai ter que
resolver de forma visível.

O ERP é a **fonte**, não o produto. Ele ocupa o papel do sistema de terceiro que
o time de dados apenas lê.

---

## Como rodar

```bash
docker compose up -d postgres_erp
```

Copie `.env.example` para `.env` e confira as variáveis `ERP_POSTGRES_*`.
Depois, a partir de `seed/erp_clinica/`:

```bash
poetry run python -m gerador carga-inicial --reset
```

Isso derruba o schema, reaplica o DDL e gera o histórico completo
(2023-01-01 → 2026-06-30): 2,4 milhões de linhas em cerca de 3,5 minutos.

Para uma base pequena de desenvolvimento, sobrescreva os volumes por ambiente:

```bash
SEED_PACIENTES=2000 SEED_AGENDAMENTOS=10000 SEED_ORCAMENTOS=4000 SEED_LEADS=4000 SEED_ATIVIDADES_CRM=20000 poetry run python -m gerador carga-inicial --reset
```

### Simular dias de operação

```bash
poetry run python -m gerador simular-dia --dias 5
```

Continua do dia seguinte ao maior movimento já registrado. Cada dia aplica
inserções, atualizações, cancelamentos, reagendamentos, aprovações, vencimentos
e pagamentos. Fim de semana é pulado — a rede não atende.

`--intensidade` escala o volume do dia (`0.2` para feriado, `1.6` para pico), o
que serve para ver como o pipeline reage a volume irregular.

### Conferir

```bash
poetry run python -m gerador resumo
```

```bash
docker exec -i postgres_erp psql -U erp_app -d erp_clinica < ddl/99_validacao.sql
```

O segundo roda 13 checagens de invariante de negócio. Todas devem sair `OK`.
Qualquer `FALHOU` é bug do gerador, não "dado sujo aceitável" — as
inconsistências deste cenário são todas intencionais e estão documentadas nos
`COMMENT` das tabelas.

---

## Estrutura

```
seed/erp_clinica/
├── ddl/
│   ├── 00_reset.sql                 DROP SCHEMA (destrutivo)
│   ├── 01_schema.sql                schema `erp` + domínios
│   ├── 02_corporativo.sql           unidades, especialidades, funcionários, médicos, convênios
│   ├── 03_crm.sql                   campanhas, leads, funil, oportunidades, atividades
│   ├── 04_pacientes_atendimento.sql pacientes, agenda, consultas, prontuários
│   ├── 05_comercial.sql             procedimentos, orçamentos, itens, bridge, histórico
│   ├── 06_financeiro.sql            parcelas, pagamentos
│   ├── 07_indices.sql               watermark + foreign key
│   └── 99_validacao.sql             checagens de invariante
└── gerador/
    ├── config.py                    volumes e probabilidades
    ├── dominios.py                  listas fixas do cenário
    ├── utils.py                     aleatoriedade, datas, dinheiro
    ├── conexao.py                   conexão e COPY
    ├── carga_inicial.py             histórico completo
    ├── carga_incremental.py         um dia de operação
    └── __main__.py                  CLI
```

---

## As 25 tabelas

| Domínio | Tabelas |
|---|---|
| Corporativo | `unidades`, `especialidades`, `funcionarios`, `medicos`, `medico_especialidade`, `medico_unidade`, `convenios` |
| CRM & Marketing | `campanhas`, `leads`, `etapas_funil`, `oportunidades`, `oportunidade_historico_etapa`, `atividades_crm` |
| Pacientes | `pacientes`, `paciente_convenio` |
| Atendimento | `agendamentos`, `consultas`, `prontuarios` |
| Comercial | `procedimentos`, `orcamentos`, `orcamento_itens`, `prontuario_orcamento`, `orcamento_historico_status` |
| Financeiro | `parcelas`, `pagamentos` |

### Fluxo principal

```
campanha 1─N lead 0..1─N oportunidade 1─N atividade_crm
                │                 │
                │ 0..1            │ 0..1
                ▼                 ▼
            paciente 1─N agendamento 1─0..1 consulta 1─0..1 prontuário
                │                                             │
                │                                       N ────┼──── N
                │                                             ▼
                └─────────────── 1─N ───────────────►    orçamento
                                                             │
                                            ┌────────────────┼─────────────────┐
                                            ▼                ▼                 ▼
                                    orçamento_itens       parcelas      histórico_status
                                    (N procedimentos)         │
                                                              ▼
                                                         1─N pagamento
```

---

## Decisões de modelagem

### 1. `prontuario_orcamento` é uma bridge, não uma FK

Um orçamento de "cirurgia + acompanhamento" pode consolidar indicações de dois
prontuários, de dois médicos e duas especialidades diferentes. E um prontuário
extenso pode ser orçado em partes, em orçamentos separados. É N:N nos dois
sentidos.

A tabela carrega `percentual_participacao`, que soma exatamente 100 dentro de
cada orçamento. **Esse é o fator de alocação.** No Warehouse:

- receita **atribuída** (por médico, por especialidade) → multiplica pelo fator;
- receita **total** (por unidade, por mês) → ignora o fator e vai direto no grão
  do orçamento.

Usar o fator nos dois casos subestima o faturamento; não usar em nenhum
multiplica a receita pelo número de prontuários envolvidos. É o erro que a
bridge existe para tornar visível.

### 2. Agendamento e consulta são tabelas separadas

Agendamento é a *intenção* (slot reservado). Consulta é o *atendimento
realizado*. A relação é 1:0..1, imposta por `UNIQUE(agendamento_id)` em
`consultas`.

Isso não é purismo: **falta é agendamento sem consulta**. Num modelo de tabela
única com campo de status, "cancelou com três dias de antecedência" e "não
apareceu" viram a mesma linha, e o dashboard de faltas deixa de existir.

### 3. Duas estratégias de ingestão no mesmo banco, de propósito

| Padrão | Tabelas | Ingestão |
|---|---|---|
| Mutável (sofre UPDATE) | `pacientes`, `orcamentos`, `parcelas`, `agendamentos`, `oportunidades` | watermark por `atualizado_em` + **MERGE** |
| Append-only (só INSERT) | `atividades_crm`, `oportunidade_historico_etapa`, `orcamento_historico_status`, `pagamentos` | watermark por `criado_em`, **append puro** |

Ter os dois padrões é o que justifica ter escrito MERGE de verdade em vez de
`TRUNCATE + INSERT` em tudo. Um pipeline que faz append onde deveria fazer
MERGE duplica o paciente na silver e quebra toda contagem distinta.

### 4. Atribuição de campanha tem caminho quebrado

"Receita por campanha" exige `orcamento → oportunidade → lead → campanha`. Três
saltos, e **cada um pode ser NULL**:

- paciente que entrou direto na recepção não tem `lead_id` (~28%);
- orçamento fechado no consultório não tem `oportunidade_id` (~40%);
- lead orgânico não tem `campanha_id` (~22%).

A receita órfã precisa de um bucket explícito (`Orgânico/Direto`, chave `-1` na
dimensão), nunca de um `INNER JOIN` que a faz sumir do relatório.

### 5. Fan trap entre itens e parcelas

`orcamentos` tem N itens **e** N parcelas, em ramos independentes. A query
`orcamento ⋈ itens ⋈ parcelas` multiplica linhas e infla a receita. A resposta
são duas tabelas fato com grãos distintos, não uma fato larga.

### 6. Chaves compostas naturais

- `orcamento_itens (orcamento_id, item_seq)` — a sequência reinicia em cada orçamento;
- `parcelas (orcamento_id, numero_parcela)` — idem;
- `medico_unidade (medico_id, unidade_id, data_inicio)` — o médico pode voltar
  à mesma unidade depois de um período fora;
- `medicos UNIQUE (crm, crm_uf)` — CRM só é único dentro do estado emissor;
  deduplicar por CRM sozinho colide entre UFs;
- `pagamentos` tem **FK composta** apontando para a PK composta de `parcelas`.

Todas forçam a decisão de surrogate key na silver.

### 7. `medico_unidade` é SCD Tipo 2 na origem

O vínculo médico↔unidade tem `data_inicio`/`data_fim`. Se o Warehouse guardar só
o vínculo atual, a receita de janeiro migra junto quando o médico troca de
unidade em março — o histórico se reescreve sozinho.

### 8. Estados derivados do tempo, não de eventos

Parcela vira `Vencida` sozinha quando a data passa. Ninguém tocou no registro: o
ERP faz esse UPDATE em lote. Isso produz um dia com milhares de linhas alteradas
de uma vez — o pico que derruba pipeline dimensionado pela média.

### 9. Estorno permanece na tabela

Pagamento estornado não é deletado: ganha `estornado = TRUE` e a parcela volta
para `Aberta`. Somar `valor_pago` sem filtrar estorno infla o caixa. A checagem
`pagamentos reconciliam com valor_pago da parcela` em `99_validacao.sql` prova
que a reconciliação correta fecha.

### 10. Preço praticado fica congelado no item

`procedimentos.valor_tabela` é o preço **vigente** e é sobrescrito em reajuste.
O preço da venda mora em `orcamento_itens.valor_unitario`. Recalcular receita
histórica por join no catálogo reprecifica o passado.

### 11. Cardinalidade brutalmente desbalanceada

15 unidades contra 500.000 atividades de CRM, na mesma consulta. É o que
justifica falar de broadcast join, particionamento e Z-order com um caso
concreto em vez de repetir glossário.

---

## Volumes padrão

Medidos numa carga completa (semente 42), **2.405.908 linhas em 217 s**:

| Tabela | Linhas | Origem do volume |
|---|---:|---|
| `atividades_crm` | 500.000 | configurado |
| `orcamento_historico_status` | 266.454 | derivado |
| `agendamentos` | 250.000 | configurado |
| `orcamento_itens` | 212.048 | derivado |
| `parcelas` | 210.207 | derivado |
| `oportunidade_historico_etapa` | 174.602 | derivado |
| `consultas` | 159.359 | derivado |
| `pagamentos` | 115.461 | derivado |
| `prontuarios` | 111.469 | derivado |
| `prontuario_orcamento` | 99.999 | derivado |
| `leads` | 90.000 | configurado |
| `orcamentos` | 90.000 | configurado |
| `pacientes` | 50.000 | configurado |
| `oportunidades` | 44.845 | derivado |
| `paciente_convenio` | 29.676 | derivado |
| demais 10 tabelas | < 1.000 | cadastro |

As **configuradas** saem de `SEED_*` (ver `config.py`). As **derivadas** não são
configuráveis de propósito: o volume delas emerge das regras de negócio. Fixar
"300.000 parcelas" e gerar 37.800 orçamentos aprovados produziria parcela órfã.

O que move o volume derivado são as taxas em `ConfigProbabilidades`. Exemplo
concreto: `parcelas` fica em 210 mil porque a média do `peso_parcelamento`
padrão é 5,5 parcelas por orçamento aprovado. Deslocando o peso para o fim da
cauda (12x mais frequente, cenário de clínica de ticket alto) a média vai a 8,0
e a tabela passa de 300 mil — sem tocar em uma linha de código de geração.

---

## Dashboards que o modelo sustenta

**Comercial** — taxa de conversão, receita, ticket médio, receita por unidade /
médico / campanha / procedimento.
**CRM** — funil, conversão por etapa, tempo médio até a venda, performance de
consultor e de campanha.
**Atendimento** — consultas, faltas, cancelamentos, reagendamentos, tempo entre
consulta e venda.
**Financeiro** — parcelas abertas e pagas, inadimplência, receita mensal, fluxo
de caixa.
**Operacional** — ocupação por unidade (`qtd_consultorios` é o denominador),
utilização de médico, especialidades mais procuradas.
