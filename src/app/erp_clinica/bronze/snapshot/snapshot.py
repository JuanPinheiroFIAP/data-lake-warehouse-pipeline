from app.storage.functions import (
    extrair_tabela_completa,
    converter_tabela_para_buffer,
    upload_buffer_para_minio_snapshot,
)
from dotenv import load_dotenv
from datetime import date

load_dotenv()

# Esse arquivo vai ser responsável por extrair as tabelas do postgres e salvar no MinIO
TABELAS = {"especialidades", "etapas_funil"}


# --- Tabelas snapshot ---
# Extração de poucos dados e volumes, se tratando de poucas linhas
def extrair_todas_as_tabelas_estaticas(engine):
    resultados = {}
    for valor in TABELAS:
        df = extrair_tabela_completa(engine, "erp", valor)
        resultados[valor] = converter_tabela_para_buffer(df)
    return resultados


# --- Função Generica para subir para MinIO ---
def subir_para_minio(s3_client, lista_buffers: dict, data: date):
    for tabela, buffer in lista_buffers.items():
        upload_buffer_para_minio_snapshot(
            s3_client, buffer, "bronze", "erp_clinicas", tabela, data
        )


# --- Função que roda a pipe que extrai as tabelas estaticas ---
def rodar_extracao_tabelas_estaticas(engine, s3_client, data: date):
    buffers = extrair_todas_as_tabelas_estaticas(engine)
    subir_para_minio(s3_client, buffers, data)
