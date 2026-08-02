"""Conexão com o PostgreSQL do ERP e carga em massa via COPY.

Decisão: COPY em vez de INSERT (mesmo com executemany).
Motivo: a carga inicial gera cerca de 2 milhões de linhas. Com INSERT linha a
linha isso leva horas; com executemany em lote, dezenas de minutos; com COPY,
poucos minutos. COPY não passa pelo parser de SQL a cada linha e não gera um
registro de WAL por comando — é o caminho que o próprio pg_restore usa.

Custo dessa escolha: COPY não dá para usar ON CONFLICT. Por isso a carga
INICIAL usa COPY (tabela vazia, sem conflito possível) e a carga INCREMENTAL
usa INSERT/UPDATE normais, onde o volume é pequeno e o upsert é necessário.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from dotenv import load_dotenv

load_dotenv()


def _dsn() -> str:
    """Monta a string de conexão a partir do ambiente.

    Segue a convenção do projeto: credencial nunca é hardcoded, sempre vem de
    variável de ambiente carregada do .env.
    """
    return (
        f"host={os.getenv('ERP_POSTGRES_HOST', 'localhost')} "
        f"port={os.getenv('ERP_POSTGRES_PORT', '5432')} "
        f"dbname={os.getenv('ERP_POSTGRES_DB', 'erp_clinica')} "
        f"user={os.getenv('ERP_POSTGRES_USER', 'erp_app')} "
        f"password={os.getenv('ERP_POSTGRES_PASSWORD', '')}"
    )


@contextmanager
def abrir_conexao(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Abre conexão com o banco do ERP e garante o fechamento."""
    conexao = psycopg.connect(_dsn(), autocommit=autocommit)
    try:
        yield conexao
    finally:
        conexao.close()


def copiar_linhas(
    conexao: psycopg.Connection,
    tabela: str,
    colunas: list[str],
    linhas: Iterable[tuple[Any, ...]],
) -> int:
    """Escreve um iterável de tuplas na tabela usando COPY. Retorna o total.

    `linhas` é consumido como stream: um gerador de 500.000 atividades nunca
    precisa existir inteiro em memória, o que mantém o pico de RAM baixo mesmo
    nas tabelas grandes.
    """
    lista_colunas = ", ".join(colunas)
    total = 0
    with conexao.cursor() as cursor:
        with cursor.copy(f"COPY {tabela} ({lista_colunas}) FROM STDIN") as copy:
            for linha in linhas:
                copy.write_row(linha)
                total += 1
    return total


def sincronizar_sequencias(conexao: psycopg.Connection, schema: str = "erp") -> None:
    """Realinha as sequências das colunas IDENTITY ao maior ID já gravado.

    Necessário porque o gerador atribui os IDs explicitamente — ele precisa
    conhecer o ID do paciente antes de inserir o agendamento que o referencia.
    Como o COPY passa por cima da sequência sem incrementá-la, ela continuaria
    em 1 e o primeiro INSERT da carga incremental estouraria a PK.
    """
    consulta_colunas = """
        SELECT c.table_name, c.column_name
          FROM information_schema.columns c
         WHERE c.table_schema = %s
           AND c.is_identity  = 'YES'
    """
    with conexao.cursor() as cursor:
        cursor.execute(consulta_colunas, (schema,))
        colunas_identity = cursor.fetchall()

        for tabela, coluna in colunas_identity:
            cursor.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{schema}.{tabela}', '{coluna}'),
                    COALESCE((SELECT MAX({coluna}) FROM {schema}.{tabela}), 1),
                    TRUE
                )
                """
            )
    conexao.commit()


def executar_arquivo_sql(conexao: psycopg.Connection, caminho: str) -> None:
    """Executa um arquivo .sql inteiro."""
    with open(caminho, "r", encoding="utf-8") as arquivo:
        conteudo = arquivo.read()
    with conexao.cursor() as cursor:
        cursor.execute(conteudo)
    conexao.commit()
