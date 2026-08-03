"""CLI do gerador do ERP Rede Clínica.

    python -m gerador criar-schema
    python -m gerador carga-inicial [--reset]
    python -m gerador simular-dia [--dias 3] [--intensidade 1.0] [--data 2026-07-01]
    python -m gerador resumo

Executar a partir de `seed/erp_clinica/`.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from .carga_incremental import SimuladorDiaOperacional
from .carga_inicial import GeradorCargaInicial
from .conexao import abrir_conexao, executar_arquivo_sql
from .config import PROBABILIDADES, ConfigVolumes

DIRETORIO_DDL = Path(__file__).resolve().parent.parent / "ddl"

ARQUIVOS_SCHEMA = [
    "01_schema.sql",
    "02_corporativo.sql",
    "03_crm.sql",
    "04_pacientes_atendimento.sql",
    "05_comercial.sql",
    "06_financeiro.sql",
    "07_indices.sql",
]

TABELAS = [
    "unidades",
    "especialidades",
    "funcionarios",
    "medicos",
    "medico_especialidade",
    "medico_unidade",
    "convenios",
    "campanhas",
    "leads",
    "etapas_funil",
    "oportunidades",
    "oportunidade_historico_etapa",
    "atividades_crm",
    "pacientes",
    "paciente_convenio",
    "agendamentos",
    "consultas",
    "prontuarios",
    "procedimentos",
    "orcamentos",
    "orcamento_itens",
    "prontuario_orcamento",
    "orcamento_historico_status",
    "parcelas",
    "pagamentos",
]


def criar_schema(conexao, resetar: bool) -> None:
    if resetar:
        print("Derrubando schema existente...")
        executar_arquivo_sql(conexao, str(DIRETORIO_DDL / "00_reset.sql"))

    for arquivo in ARQUIVOS_SCHEMA:
        print(f"Aplicando {arquivo}...")
        executar_arquivo_sql(conexao, str(DIRETORIO_DDL / arquivo))
    print("Schema pronto.")


def comando_carga_inicial(argumentos) -> None:
    volumes = ConfigVolumes.do_ambiente()
    print(
        f"Volumes: {volumes.pacientes:,} pacientes | {volumes.agendamentos:,} agendamentos | "
        f"{volumes.orcamentos:,} orçamentos | {volumes.atividades_crm:,} atividades".replace(
            ",", "."
        )
    )
    print(f"Janela: {volumes.data_inicio_historico} → {volumes.data_fim_historico}\n")

    inicio = time.perf_counter()
    with abrir_conexao() as conexao:
        if argumentos.reset:
            criar_schema(conexao, resetar=True)
        GeradorCargaInicial(conexao, volumes, PROBABILIDADES).executar()

    print(f"\nCarga inicial concluída em {time.perf_counter() - inicio:.1f}s.")


def comando_simular_dia(argumentos) -> None:
    with abrir_conexao() as conexao:
        data_inicial = argumentos.data or _proximo_dia_operacional(conexao)

        for deslocamento in range(argumentos.dias):
            dia = data_inicial + timedelta(days=deslocamento)
            if dia.weekday() >= 5:
                print(f"{dia} — fim de semana, rede fechada.")
                continue

            simulador = SimuladorDiaOperacional(
                conexao, dia, intensidade=argumentos.intensidade
            )
            resultado = simulador.executar()

            print(f"\n=== {dia:%d/%m/%Y} ===")
            for nome, total in resultado.items():
                if total:
                    print(f"  {nome:<26} {total:>7,}".replace(",", "."))


def _proximo_dia_operacional(conexao) -> date:
    """Continua do dia seguinte ao maior movimento já registrado."""
    with conexao.cursor() as cursor:
        cursor.execute(
            """
            SELECT GREATEST(
                (SELECT MAX(criado_em::date) FROM erp.agendamentos),
                (SELECT MAX(criado_em::date) FROM erp.atividades_crm)
            )
            """
        )
        ultimo = cursor.fetchone()[0]
    if ultimo is None:
        raise SystemExit("Base vazia. Rode `carga-inicial` antes de simular dias.")
    return ultimo + timedelta(days=1)


def comando_resumo(argumentos) -> None:
    with abrir_conexao() as conexao, conexao.cursor() as cursor:
        print(f"{'tabela':<32}{'linhas':>14}")
        print("-" * 46)
        total_geral = 0
        for tabela in TABELAS:
            cursor.execute(f"SELECT count(*) FROM erp.{tabela}")
            total = cursor.fetchone()[0]
            total_geral += total
            print(f"{tabela:<32}{total:>14,}".replace(",", "."))
        print("-" * 46)
        print(f"{'TOTAL':<32}{total_geral:>14,}".replace(",", "."))


def main() -> None:
    analisador = argparse.ArgumentParser(prog="gerador", description=__doc__)
    subcomandos = analisador.add_subparsers(dest="comando", required=True)

    schema = subcomandos.add_parser("criar-schema", help="Aplica o DDL")
    schema.add_argument("--reset", action="store_true", help="Derruba o schema antes")
    schema.set_defaults(funcao=lambda a: _com_conexao(criar_schema, a.reset))

    inicial = subcomandos.add_parser("carga-inicial", help="Gera o histórico completo")
    inicial.add_argument(
        "--reset", action="store_true", help="Recria o schema do zero antes de gerar"
    )
    inicial.set_defaults(funcao=comando_carga_inicial)

    dia = subcomandos.add_parser("simular-dia", help="Aplica dias de operação")
    dia.add_argument("--dias", type=int, default=1)
    dia.add_argument(
        "--intensidade",
        type=float,
        default=1.0,
        help="Multiplicador de volume do dia (0.2 = feriado, 1.6 = pico)",
    )
    dia.add_argument(
        "--data",
        type=date.fromisoformat,
        default=None,
        help="Data inicial; por padrão continua de onde a base parou",
    )
    dia.set_defaults(funcao=comando_simular_dia)

    resumo = subcomandos.add_parser("resumo", help="Contagem de linhas por tabela")
    resumo.set_defaults(funcao=comando_resumo)

    argumentos = analisador.parse_args()
    argumentos.funcao(argumentos)


def _com_conexao(funcao, *args) -> None:
    with abrir_conexao() as conexao:
        funcao(conexao, *args)


if __name__ == "__main__":
    sys.exit(main())
