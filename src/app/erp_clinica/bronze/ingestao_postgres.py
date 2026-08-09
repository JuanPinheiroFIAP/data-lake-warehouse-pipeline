from app.storage.functions import (
    extrair_tabela_completa,
    converter_tabela_para_buffer,
    upload_buffer_para_minio,
    iniciar_client_minio,
    garantir_infraestrutura_bucket,
)
from sqlalchemy import create_engine
from dotenv import load_dotenv
import os

load_dotenv()

# Esse arquivo vai ser responsável por extrair as tabelas do postgres e salvar no MinIO


# --- Tabelas Estaticas ---
def extrair_todas_as_tabelas(engine):
    resultados = {}

    for tabela in TABELAS_ESTATICAS:
        df = extrair_tabela_completa(engine, "erp", tabela)
        resultados[tabela] = converter_tabela_para_buffer(df)

    return resultados


def subir_para_minio(s3_client, lista_buffers: dict):
    for tabela, buffer in lista_buffers.items():
        upload_buffer_para_minio(s3_client, buffer, "bronze", "erp_clinicas", tabela)


if __name__ == "__main__":
    string_conexao = os.getenv("ERP_STRING_CONNECTION")
    TABELAS_ESTATICAS = ["especialidades", "etapas_funil"]

    s3_client = iniciar_client_minio()
    engine = create_engine(string_conexao)

    lista_buckets = ["bronze"]
    garantir_infraestrutura_bucket(s3_client, lista_buckets)
    resultado = extrair_todas_as_tabelas(engine)
    subir_para_minio(s3_client, resultado)
