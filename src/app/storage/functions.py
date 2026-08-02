import boto3
import os
import sys
from botocore.client import Config
from botocore.exceptions import ClientError
from datetime import datetime
import io
from dotenv import load_dotenv

load_dotenv()


def iniciar_client_minio():
    """
    Inicializa e retorna o cliente Boto3 configurado para o MinIO local.
    """
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"),
        aws_access_key_id=os.getenv("MINIO_ROOT_USER"),
        aws_secret_access_key=os.getenv("MINIO_ROOT_PASSWORD"),
        config=Config(signature_version="s3v4"),
    )


def verifica_se_bucket_existe(s3_client, nome_bucket: str) -> bool:
    """Verifica se o bucket existe no MinIO."""
    try:
        response = s3_client.list_buckets()
        lista_nomes_buckets = [bucket["Name"] for bucket in response["Buckets"]]
        return nome_bucket in lista_nomes_buckets
    except Exception as e:
        print(e)
        return False


def criar_bucket(s3_client, nome_bucket: str) -> None:
    """Cria um novo bucket no MinIO."""
    s3_client.create_bucket(Bucket=nome_bucket)


def garantir_infraestrutura_bucket(s3_client, list_names: list) -> None:
    """Garante que o bucket de destino exista antes de iniciar a carga."""
    for item in list_names:
        if not verifica_se_bucket_existe(s3_client, item):
            print(f"Bucket '{item}' não encontrado. Tentando criar...")
            try:
                criar_bucket(s3_client, item)
            except ClientError as e:
                print(
                    f"Falha crítica: Não foi possível criar o bucket '{item}'. Detalhes: {e}"
                )
                sys.exit(1)
        else:
            print(f"Bucket '{item}' verificado. Seguindo o fluxo...")


def upload_buffer_para_minio(
    s3_client,
    buffer_arquivo: io.BytesIO,
    bucket_name: str,
    nome_sistema: str,
    tabela: str,
) -> None:
    """Faz o upload de um buffer de memória direto para o caminho virtual do MinIO."""

    agora = datetime.now()
    ano = agora.strftime("%Y")
    mes = agora.strftime("%m")
    dia = agora.strftime("%d")
    horario_carga = agora.strftime("%Hh%Mmin")
    nome_destino_s3 = f"{nome_sistema}/{tabela}/ano={ano}/mes={mes}/dia={dia}/{tabela}_{horario_carga}.parquet"

    try:
        s3_client.upload_fileobj(
            Fileobj=buffer_arquivo, Bucket=bucket_name, Key=nome_destino_s3
        )
    except ClientError as e:
        print(e)
        raise e
