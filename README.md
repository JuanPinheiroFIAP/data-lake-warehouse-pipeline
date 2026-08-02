Projeto de Engenharia de Dados desenvolvido para modernizar o fluxo de dados de um sistema ERP.

Arquitetura:

API → Bronze → Silver → Gold → Data Warehouse → Power BI

Fontes de dados:

- **ERP Rede Clínica** — banco PostgreSQL operacional fictício (rede de clínicas médicas,
  25 tabelas, ~2,4 milhões de linhas, com carga incremental diária para exercitar CDC/MERGE).
  Ver [`seed/erp_clinica/`](seed/erp_clinica/README.md).
- **NYC TLC Trip Data** — arquivos Parquet de alto volume, download direto.
- **Frankfurter** — API pública de câmbio.

Tecnologias:

- Python
- Pandas
- DuckDB
- Delta Lake
- MinIO
- PostgreSQL
- Airflow
- Docker
- dbt (em evolução)
- Power BI