# Data Lake Warehouse Pipeline

## Abertura

Projeto de Engenharia de Dados desenvolvido para modernizar o fluxo de dados de um sistema ERP.

## Contexto

Projeto desenvolvido com o objetivo de aprimorar conceitos e práticas de Engenharia de Dados por meio da construção de um pipeline de dados completo.

## Arquitetura

**Banco Relacional (PostgreSQL) → Bronze → Silver → Gold → Data Warehouse → Power BI**

## Status

Atualmente, o código consegue extrair duas tabelas estáticas de baixo volume de um banco PostgreSQL e armazená-las no bucket Bronze do MinIO.

O projeto está em desenvolvimento para posteriormente avançar para a arquitetura Medallion, contemplando as camadas Bronze, Silver e Gold.

A etapa atual já realiza:

* Verificação da infraestrutura do bucket no MinIO;
* Extração de dados do PostgreSQL;
* Conversão dos dados para o formato Parquet;
* Armazenamento dos arquivos no MinIO;
* Idempotência da carga, utilizando uma estrutura de particionamento Hive baseada em **ano/mês/dia**;
* Utilização de uma data de referência como parâmetro, permitindo o reprocessamento de períodos anteriores.

## Stack

* MinIO
* PostgreSQL
* psycopg2
* Python
* Pandas
* SQLAlchemy
* boto3
* PyArrow
* Docker
* Poetry

## Como rodar

Antes de executar o pipeline, é necessário subir a infraestrutura:

```bash
docker compose up -d
```

Depois, para executar a ingestão Bronze:

```bash
poetry run python src/app/erp_clinica/bronze/ingestao_postgres.py --data <data desejada>
```

Exemplo:

```bash
poetry run python src/app/erp_clinica/bronze/ingestao_postgres.py --data 2026-09-03
```

## Infraestrutura

| Serviço        | Porta |
| -------------- | ----: |
| MinIO          |  9000 |
| MinIO Console  |  9001 |
| PostgreSQL ERP |  5433 |
| PostgreSQL DW  |  5434 |
| pgAdmin        |  8888 |
| Adminer        |  8081 |

## Descrições Técnicas

### Parquet

O Parquet foi escolhido como formato de armazenamento por ser um formato colunar, permitindo maior eficiência na leitura e processamento de dados quando comparado a formatos orientados a linhas, como CSV.

Além disso, o Parquet possui recursos como compressão e leitura seletiva de colunas, características que são importantes para um Data Lake.

### MinIO

O MinIO é utilizado como armazenamento de objetos para a camada Bronze. Sua API é compatível com o padrão utilizado pelo Amazon S3, permitindo que o projeto utilize uma infraestrutura local durante o desenvolvimento sem ficar preso a uma implementação específica de armazenamento.

A estrutura dos objetos utiliza particionamento no padrão Hive:

```text
ano=2026/mes=09/dia=03/
```

Esse padrão facilita a organização e posteriormente pode permitir que ferramentas de processamento filtrem os dados por partição.

### Idempotência

A carga Bronze foi estruturada para ser idempotente. A data utilizada no caminho do arquivo é recebida como parâmetro em vez de ser definida automaticamente pela data e hora da execução.

Com isso, uma mesma execução pode ser reprocessada para uma determinada data sem criar novos arquivos apenas por ter sido executada novamente.

Exemplo:

```text
bronze/
└── erp_clinica/
    └── especialidades/
        └── ano=2026/
            └── mes=09/
                └── dia=03/
                    └── especialidades.parquet
```

## Roadmap

O projeto está em desenvolvimento e as próximas etapas envolvem a inclusão das demais tabelas de acordo com suas características e estratégias de ingestão.

As tabelas serão tratadas de acordo com seu comportamento, utilizando estratégias como:

* **Snapshot:** para tabelas de baixa frequência de alteração, permitindo sobrescrever o estado atual;
* **Append:** para dados históricos ou eventos que devem ser preservados;
* **Incremental/Mutable:** para tabelas que sofrem alterações ao longo do tempo.

Após a conclusão da ingestão Bronze, o projeto avançará para as etapas de transformação e modelagem das camadas Silver e Gold, seguidas pela construção do Data Warehouse.

## Fontes de dados

### ERP Rede Clínica

Banco PostgreSQL operacional fictício que simula uma rede de clínicas médicas.

* 25 tabelas;
* aproximadamente 2,4 milhões de linhas;
* dados gerados de forma sintética;
* simulação de cargas incrementais e alterações nos dados;
* diferentes comportamentos de atualização para exercitar estratégias de ingestão.

O banco foi desenvolvido especificamente para este projeto de portfólio.

Veja [`seed/erp_clinica/`](seed/erp_clinica/README.md).
