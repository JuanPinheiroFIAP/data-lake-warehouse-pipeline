"""Utilitários de aleatoriedade e valores monetários."""

from __future__ import annotations

import random
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Sequence

CENTAVO = Decimal("0.01")

# Fuso fixo de Brasília. Usar UTC puro esconderia um problema real: a coluna é
# TIMESTAMPTZ, e a diferença entre "dia da operação" e "dia UTC" desloca linhas
# de partição na virada da meia-noite. Manter o offset força o pipeline a
# decidir explicitamente em qual fuso ele particiona.
FUSO_BR = timezone(timedelta(hours=-3))


def dinheiro(valor: float | Decimal) -> Decimal:
    """Arredonda para duas casas usando Decimal.

    Float não serve aqui: a constraint `valor_liquido = valor_bruto -
    valor_desconto` é uma igualdade exata em NUMERIC. Com float, a soma dos
    itens quase nunca bate no centavo e o INSERT é rejeitado — o que, aliás, é
    exatamente o tipo de bug que aparece em pipeline financeiro real.
    """
    return Decimal(str(valor)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def escolher(
    rng: random.Random, opcoes: Sequence[Any], pesos: Sequence[float] | None = None
) -> Any:
    """Sorteia um elemento, com ou sem peso."""
    if pesos is None:
        return rng.choice(opcoes)
    return rng.choices(opcoes, weights=pesos, k=1)[0]


def sorteia_bool(rng: random.Random, probabilidade: float) -> bool:
    """True com a probabilidade informada."""
    return rng.random() < probabilidade


def data_aleatoria(rng: random.Random, inicio: date, fim: date) -> date:
    """Data uniforme dentro do intervalo (inclusivo nas duas pontas)."""
    dias = (fim - inicio).days
    return inicio + timedelta(days=rng.randint(0, max(dias, 0)))


def horario_comercial(rng: random.Random, dia: date) -> datetime:
    """Timestamp em horário de atendimento (08h–19h), com viés realista.

    A distribuição não é uniforme de propósito: clínica tem pico de manhã e no
    fim da tarde. Um dashboard de ocupação por faixa horária construído sobre
    dado uniforme mostraria uma linha reta e não provaria nada.
    """
    faixas = [(8, 11), (11, 14), (14, 17), (17, 19)]
    pesos = [0.34, 0.20, 0.28, 0.18]
    inicio, fim = escolher(rng, faixas, pesos)
    hora = rng.randint(inicio, fim - 1)
    minuto = escolher(rng, [0, 15, 30, 45])
    return datetime.combine(dia, time(hora, minuto), tzinfo=FUSO_BR)


def dia_util_seguinte(dia: date) -> date:
    """Empurra sábado e domingo para a segunda-feira.

    A rede não atende no fim de semana. Sem isso, cerca de 28% dos agendamentos
    cairiam em dias sem operação e a métrica de ocupação por unidade ficaria
    diluída por dias que não existem no calendário do negócio.
    """
    while dia.weekday() >= 5:
        dia += timedelta(days=1)
    return dia


def data_aleatoria_util(rng: random.Random, inicio: date, fim: date) -> date:
    """Data aleatória garantidamente em dia útil, respeitando o teto.

    Se empurrar para frente estourar o limite, recua para o dia útil anterior —
    caso contrário o teto do intervalo poderia cair num sábado.
    """
    dia = dia_util_seguinte(data_aleatoria(rng, inicio, fim))
    while dia > fim:
        dia -= timedelta(days=1)
    while dia.weekday() >= 5:
        dia -= timedelta(days=1)
    return dia


def distribuir_por_peso(total: int, pesos: dict[str, float]) -> dict[str, int]:
    """Reparte `total` entre as chaves conforme o peso, sem perder nem sobrar.

    O ajuste final na maior fatia existe para que a soma feche exatamente:
    arredondar cada parte isoladamente deixaria uma diferença de algumas
    unidades, e "gerar 597 dos 600 funcionários" é o tipo de detalhe que
    aparece depois como contagem estranha no dashboard.
    """
    resultado = {chave: int(total * peso) for chave, peso in pesos.items()}
    sobra = total - sum(resultado.values())
    if sobra:
        maior = max(resultado, key=lambda chave: pesos[chave])
        resultado[maior] += sobra
    return resultado


def nome_pessoa(fake) -> str:
    """Nome próprio sem tratamento (Sr., Sra., Dr., Srta.).

    O Faker pt_BR devolve "Sra. Isabelly Câmara" em parte dos sorteios. Sistema
    de RH guarda o nome, não o pronome de tratamento — e deixar isso passar
    contamina qualquer deduplicação por nome no Warehouse.
    """
    return re.sub(r"^(sr|sra|srta|dr|dra)\.?\s+", "", fake.name(), flags=re.IGNORECASE)


def local_part_email(nome: str) -> str:
    """Converte um nome próprio na parte local de um e-mail corporativo.

    Dois cuidados que parecem detalhe e não são:

    - NFKD separa o acento da letra e o filtro descarta só o acento, preservando
      a letra. Um `encode('ascii', 'ignore')` direto transformaria "Câmara" em
      "cmara", jogando fora o caractere inteiro.
    - Pontos consecutivos são colapsados e as bordas aparadas. "Sra. Isabelly"
      produziria "sra..isabelly", que não é local-part válida na RFC 5322.
    """
    sem_acento = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", nome)
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"[^a-zA-Z0-9]+", ".", sem_acento).strip(".").lower()


def documento_numerico(rng: random.Random, tamanho: int) -> str:
    """Sequência numérica de tamanho fixo, para carteirinha e autorização."""
    return "".join(str(rng.randint(0, 9)) for _ in range(tamanho))


def gerar_cpf(sequencial: int) -> str:
    """CPF fictício, único por construção, com dígitos verificadores válidos.

    A base de 9 dígitos vem de `sequencial * 7919 mod 10^9`. Como 7919 é primo
    e não divide 10^9, o mapeamento é bijetivo: cada sequencial produz um CPF
    diferente, sem precisar guardar um set de milhares de documentos em memória
    nem arriscar violar o UNIQUE na carga incremental.

    Os dígitos verificadores são calculados de verdade para que a coluna passe
    por qualquer validação de formato no pipeline. O número não pertence a
    ninguém — é derivado de um contador.
    """
    espalhado = (sequencial * 7919) % 1_000_000_000
    base = [int(digito) for digito in f"{espalhado:09d}"]

    for _ in range(2):
        peso = len(base) + 1
        soma = sum(digito * (peso - indice) for indice, digito in enumerate(base))
        verificador = (soma * 10) % 11
        base.append(0 if verificador == 10 else verificador)

    numeros = "".join(map(str, base))
    return f"{numeros[:3]}.{numeros[3:6]}.{numeros[6:9]}-{numeros[9:]}"


def gerar_cnpj(rng: random.Random) -> str:
    """CNPJ fictício com dígitos verificadores válidos, formatado."""
    base = [rng.randint(0, 9) for _ in range(8)] + [0, 0, 0, 1]

    for _ in range(2):
        pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][-(len(base)) :]
        soma = sum(digito * peso for digito, peso in zip(base, pesos))
        resto = soma % 11
        base.append(0 if resto < 2 else 11 - resto)

    numeros = "".join(map(str, base))
    return f"{numeros[:2]}.{numeros[2:5]}.{numeros[5:8]}/{numeros[8:12]}-{numeros[12:]}"
