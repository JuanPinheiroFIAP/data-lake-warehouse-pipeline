"""Volumes e probabilidades da geração.

Tudo que define o "tamanho" e o "comportamento" do cenário está aqui, e não
espalhado pelo código de geração. O motivo é prático: para testar o pipeline
localmente você roda com 2.000 pacientes; para medir performance de verdade,
com 500.000. Se o volume estivesse hardcoded no meio da lógica, mudar de escala
significaria caçar número mágico em cinco arquivos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from datetime import date


@dataclass
class ConfigVolumes:
    """Quantidade de linhas de cada entidade raiz.

    As entidades derivadas (parcelas, itens, histórico, bridge) não aparecem
    aqui: o volume delas é consequência das regras de negócio. Fixar "300.000
    parcelas" no config e depois gerar 40.000 orçamentos aprovados produziria
    parcela órfã — o número tem que emergir da regra, não ser imposto.
    """

    # --- cadastro corporativo (cardinalidade baixa, muda devagar) ---
    unidades: int = 15
    funcionarios: int = 600
    medicos: int = 220
    convenios: int = 12
    procedimentos: int = 180

    # --- marketing e CRM (maior volume do banco) ---
    campanhas: int = 80
    leads: int = 90_000
    atividades_crm: int = 500_000

    # --- pacientes e atendimento ---
    pacientes: int = 50_000
    agendamentos: int = 250_000

    # --- comercial ---
    orcamentos: int = 90_000

    # Janela histórica coberta pela carga inicial. A carga incremental continua
    # a partir do dia seguinte ao maior `criado_em` encontrado no banco, então
    # mexer nessa data desloca o cenário inteiro sem quebrar a continuidade.
    data_inicio_historico: date = date(2023, 1, 1)
    data_fim_historico: date = date(2026, 6, 30)

    semente: int = 42

    @classmethod
    def do_ambiente(cls) -> "ConfigVolumes":
        """Monta a configuração a partir de variáveis SEED_* do ambiente.

        Permite rodar `SEED_PACIENTES=2000 python -m gerador carga-inicial` sem
        editar código — que é como o pipeline vai querer usar isso em CI.
        """
        valores = {}
        for campo in fields(cls):
            bruto = os.getenv(f"SEED_{campo.name.upper()}")
            if bruto is None:
                continue
            # Todo campo é int, exceto o par de datas que delimita o histórico.
            if campo.name.startswith("data_"):
                valores[campo.name] = date.fromisoformat(bruto)
            else:
                valores[campo.name] = int(bruto)
        return cls(**valores)


@dataclass(frozen=True)
class ConfigProbabilidades:
    """Regras de negócio expressas como taxa.

    Cada número aqui é uma decisão de modelagem de cenário, não um detalhe
    estético. `taxa_aprovacao_orcamento = 0.42` é o que faz o dashboard de
    conversão ter um valor plausível; `taxa_paciente_sem_lead = 0.28` é o que
    garante que exista receita sem campanha atribuída — o caso que quebra
    dashboard construído com INNER JOIN.
    """

    # --- funil de marketing ---
    lead_sem_campanha: float = 0.22  # tráfego orgânico / indicação espontânea
    lead_vira_oportunidade: float = 0.50
    lead_vira_paciente: float = 0.40
    oportunidade_ganha: float = 0.34
    oportunidade_ainda_aberta: float = 0.12

    # --- origem do paciente ---
    paciente_sem_lead: float = 0.28  # entrou direto na recepção
    paciente_nunca_comprou: float = 0.30
    paciente_com_convenio: float = 0.55
    paciente_multiplos_convenios: float = 0.08

    # --- agenda ---
    agendamento_realizado: float = 0.68
    agendamento_cancelado: float = 0.12
    agendamento_falta: float = 0.09
    agendamento_reagendado: float = 0.07
    # o resto (0.04) fica em Agendado/Confirmado — slots ainda no futuro

    consulta_gera_prontuario: float = 0.70
    prontuario_gera_indicacao: float = 0.85

    # --- comercial ---
    orcamento_com_oportunidade: float = 0.60  # o resto fecha direto no consultório
    orcamento_multiplos_prontuarios: float = 0.22
    taxa_aprovacao_orcamento: float = 0.42
    taxa_recusa_orcamento: float = 0.31
    taxa_expiracao_orcamento: float = 0.18
    taxa_cancelamento_orcamento: float = 0.05
    # o resto segue 'Enviado' / 'Em elaboração'

    orcamento_com_convenio: float = 0.35
    orcamento_com_desconto: float = 0.55

    # Distribuição do parcelamento (1 a 12x). É o que determina o volume da
    # tabela `parcelas`, junto com a taxa de aprovação — a média destes pesos é
    # 5,5 parcelas, o que dá ~208 mil parcelas nos volumes padrão.
    # Para chegar perto de 300 mil, empurre o peso para o fim da cauda
    # (ex.: (0.07, 0.04, 0.06, 0.07, 0.14, 0.14, 0.16, 0.32), média 8,0).
    # É uma decisão de cenário: clínica de ticket alto parcela no máximo,
    # clínica de consulta avulsa parcela pouco.
    opcoes_parcelamento: tuple[int, ...] = (1, 2, 3, 4, 6, 8, 10, 12)
    peso_parcelamento: tuple[float, ...] = (
        0.18,
        0.09,
        0.14,
        0.10,
        0.17,
        0.09,
        0.08,
        0.15,
    )

    # --- financeiro ---
    parcela_paga: float = 0.74
    parcela_paga_parcial: float = 0.06
    parcela_inadimplente: float = 0.11  # vencida e não paga
    parcela_cancelada: float = 0.02
    pagamento_estornado: float = 0.015
    pagamento_fracionado: float = 0.09  # parcela quitada em mais de um pagamento

    # --- corpo clínico ---
    medico_multiplas_especialidades: float = 0.30
    medico_multiplas_unidades: float = 0.25
    medico_trocou_de_unidade: float = 0.18  # gera vínculo encerrado (data_fim)
    funcionario_desligado: float = 0.12

    # --- volume relativo de eventos ---
    atividades_por_oportunidade_media: float = 9.0
    itens_por_orcamento_media: float = 2.4


VOLUMES_PADRAO = ConfigVolumes()
PROBABILIDADES = ConfigProbabilidades()
