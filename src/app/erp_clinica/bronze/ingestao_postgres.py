from app.storage.functions import (
    iniciar_client_minio,
    garantir_infraestrutura_bucket,
)
from app.erp_clinica.bronze.snapshot.snapshot import rodar_extracao_tabelas_estaticas
import os
from sqlalchemy import create_engine
from dotenv import load_dotenv
from datetime import date
import argparse

load_dotenv()


# --- Função que roda o fluxo da camada Bronze ---
def rodar_fluxo_bronze_erp(data: date):
    # String de conexão como o postgres do ERP Ficticio
    string_conexao = os.getenv("ERP_STRING_CONNECTION")

    # Lista dos buckets para verificação
    lista_buckets = ["bronze"]

    # Realizando a conexão com o MinIO
    s3_client = iniciar_client_minio()
    engine = create_engine(string_conexao)

    # Rodando o fluxo
    # Verificando se a estrutura inicial do MinIO esta ok
    garantir_infraestrutura_bucket(s3_client, lista_buckets)

    rodar_extracao_tabelas_estaticas(engine, s3_client, data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Injestão de Tabelas na camada Bronze."
    )
    parser.add_argument(
        "--data",
        type=date.fromisoformat,
        required=True,
        help="Data em que a função foi executada.",
    )
    args = parser.parse_args()

    rodar_fluxo_bronze_erp(args.data)
