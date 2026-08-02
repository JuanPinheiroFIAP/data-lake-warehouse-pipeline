"""Carga inicial: constrói o histórico completo do ERP do zero.

Estratégia geral
----------------
A geração acontece em ordem de dependência (unidade antes de médico, médico
antes de agendamento) e cada etapa guarda em memória apenas os IDs que a etapa
seguinte precisa — nunca a linha inteira. É isso que permite gerar ~2 milhões
de linhas sem estourar a RAM: o que fica retido são listas de inteiros, e as
tuplas completas são escritas direto no COPY como stream.

Coerência é responsabilidade daqui
----------------------------------
O banco tem CHECK e FK, mas nenhum dos dois impede um agendamento anterior ao
cadastro do paciente, ou um orçamento cujos prontuários são de outra pessoa.
Essas invariantes temporais e de identidade são garantidas neste código. É de
propósito: um ERP real também as garante na aplicação, não no schema — e o
pipeline de dados precisa poder confiar nelas sem re-validar tudo.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import accumulate

from faker import Faker

from . import dominios as dom
from .config import ConfigProbabilidades, ConfigVolumes
from .conexao import copiar_linhas, sincronizar_sequencias
from .utils import (
    FUSO_BR,
    data_aleatoria,
    data_aleatoria_util,
    dia_util_seguinte,
    dinheiro,
    distribuir_por_peso,
    documento_numerico,
    escolher,
    gerar_cnpj,
    gerar_cpf,
    horario_comercial,
    local_part_email,
    nome_pessoa,
    sorteia_bool,
)

STATUS_AGENDAMENTO = ["Realizado", "Cancelado", "Falta", "Agendado", "Confirmado"]
PESO_STATUS_AGENDAMENTO = [0.68, 0.12, 0.09, 0.025, 0.015]


class GeradorCargaInicial:
    """Gera e carrega o histórico completo do ERP."""

    def __init__(
        self,
        conexao,
        volumes: ConfigVolumes,
        probabilidades: ConfigProbabilidades,
    ) -> None:
        self.conexao = conexao
        self.vol = volumes
        self.prob = probabilidades

        # Uma única instância de Random para todo o processo. Com a mesma
        # semente, a mesma base sai idêntica — o que torna possível comparar
        # dois runs do pipeline e saber que a diferença veio do código, não do
        # dado.
        self.rng = random.Random(volumes.semente)
        self.fake = Faker("pt_BR")
        Faker.seed(volumes.semente)

        self.inicio = volumes.data_inicio_historico
        self.fim = volumes.data_fim_historico
        # "Hoje" do cenário. Agenda pode ter slot até 30 dias à frente disso.
        self.hoje = volumes.data_fim_historico

        # --- índices em memória, preenchidos ao longo da geração ---
        self.unidade_ids: list[int] = []
        self.unidade_uf: dict[int, str] = {}
        self.especialidade_ids: list[int] = []
        self.area_por_especialidade: dict[int, str] = {}
        self.convenio_ids: list[int] = []
        self.comerciais_por_unidade: dict[int, list[int]] = {}
        self.atendentes_por_unidade: dict[int, list[int]] = {}
        self.funcionarios_financeiro: list[int] = []
        self.medicos_por_unidade: dict[int, list[int]] = {}
        self.especialidades_do_medico: dict[int, list[int]] = {}
        self.procedimentos_por_especialidade: dict[int, list[tuple[int, Decimal]]] = {}
        self.campanhas: list[tuple[int, date, date, int | None]] = []

        self.lead_ids: list[int] = []
        self.lead_campanha: list[int | None] = []
        self.lead_data: list[date] = []
        self.lead_unidade: list[int] = []
        self.lead_vira_paciente: list[bool] = []
        self.lead_vira_oportunidade: list[bool] = []

        self.paciente_por_lead: dict[int, int] = {}
        self.paciente_unidade: dict[int, int] = {}
        self.paciente_desde: dict[int, date] = {}
        self.paciente_ids: list[int] = []

        self.oportunidades_por_paciente: dict[int, list[tuple[int, date, int]]] = {}
        self.prontuarios_por_paciente: dict[
            int, list[tuple[int, int, int, int, date]]
        ] = {}

    # =========================================================================
    # Orquestração
    # =========================================================================
    def executar(self) -> None:
        etapas = [
            ("unidades", self.gerar_unidades),
            ("especialidades", self.gerar_especialidades),
            ("convenios", self.gerar_convenios),
            ("funcionarios", self.gerar_funcionarios),
            ("medicos", self.gerar_medicos),
            ("procedimentos", self.gerar_procedimentos),
            ("etapas_funil", self.gerar_etapas_funil),
            ("campanhas", self.gerar_campanhas),
            ("leads", self.gerar_leads),
            ("pacientes", self.gerar_pacientes),
            ("paciente_convenio", self.gerar_paciente_convenio),
            ("oportunidades", self.gerar_oportunidades),
            ("atividades_crm", self.gerar_atividades_crm),
            ("agenda + consultas + prontuários", self.gerar_agenda),
            ("orçamentos + itens + bridge", self.gerar_orcamentos),
            ("parcelas + pagamentos", self.gerar_financeiro),
        ]

        for nome, etapa in etapas:
            print(f"  → {nome}...", flush=True)
            total = etapa()
            self.conexao.commit()
            if total:
                print(f"    {total:,} linhas".replace(",", "."), flush=True)

        print("  → sincronizando sequências...", flush=True)
        sincronizar_sequencias(self.conexao)

    # =========================================================================
    # Cadastro corporativo
    # =========================================================================
    def gerar_unidades(self) -> int:
        linhas = []
        for indice, (codigo, nome, cidade, uf, regiao, consultorios) in enumerate(
            dom.UNIDADES[: self.vol.unidades], start=1
        ):
            inauguracao = data_aleatoria(self.rng, date(2011, 1, 1), date(2022, 6, 30))
            criado = datetime.combine(inauguracao, datetime.min.time(), tzinfo=FUSO_BR)
            linhas.append(
                (
                    indice,
                    codigo,
                    nome,
                    cidade,
                    uf,
                    regiao,
                    gerar_cnpj(self.rng),
                    inauguracao,
                    consultorios,
                    "08:00",
                    "19:00",
                    True,
                    criado,
                    criado,
                )
            )
            self.unidade_ids.append(indice)
            self.unidade_uf[indice] = uf
            self.comerciais_por_unidade[indice] = []
            self.atendentes_por_unidade[indice] = []
            self.medicos_por_unidade[indice] = []

        return copiar_linhas(
            self.conexao,
            "erp.unidades",
            [
                "unidade_id",
                "codigo_unidade",
                "nome_unidade",
                "cidade",
                "uf",
                "regiao",
                "cnpj",
                "data_inauguracao",
                "qtd_consultorios",
                "horario_abertura",
                "horario_fechamento",
                "ativa",
                "criado_em",
                "atualizado_em",
            ],
            linhas,
        )

    def gerar_especialidades(self) -> int:
        linhas = []
        criado = datetime.combine(self.inicio, datetime.min.time(), tzinfo=FUSO_BR)
        for indice, (codigo, nome, area) in enumerate(dom.ESPECIALIDADES, start=1):
            linhas.append((indice, codigo, nome, area, True, criado))
            self.especialidade_ids.append(indice)
            self.area_por_especialidade[indice] = area

        return copiar_linhas(
            self.conexao,
            "erp.especialidades",
            [
                "especialidade_id",
                "codigo_especialidade",
                "nome_especialidade",
                "area",
                "ativa",
                "criado_em",
            ],
            linhas,
        )

    def gerar_convenios(self) -> int:
        linhas = []
        criado = datetime.combine(self.inicio, datetime.min.time(), tzinfo=FUSO_BR)
        for indice, (nome, tipo, desconto, prazo) in enumerate(
            dom.CONVENIOS[: self.vol.convenios], start=1
        ):
            linhas.append(
                (
                    indice,
                    f"CV{indice:03d}",
                    nome,
                    tipo,
                    dinheiro(desconto),
                    prazo,
                    True,
                    criado,
                    criado,
                )
            )
            self.convenio_ids.append(indice)

        return copiar_linhas(
            self.conexao,
            "erp.convenios",
            [
                "convenio_id",
                "codigo_convenio",
                "nome_convenio",
                "tipo_convenio",
                "percentual_desconto",
                "prazo_repasse_dias",
                "ativo",
                "criado_em",
                "atualizado_em",
            ],
            linhas,
        )

    def gerar_funcionarios(self) -> int:
        """Quadro de colaboradores, com cobertura mínima garantida por unidade.

        Distribuir 600 funcionários aleatoriamente entre 15 unidades deixaria,
        por azar estatístico, alguma unidade sem consultor comercial — e aí
        nenhum orçamento daquela unidade teria vendedor. Por isso o piso fixo
        antes do sorteio.
        """
        linhas: list[tuple] = []
        proximo_id = 1
        emails_usados: set[str] = set()

        def criar(unidade_id: int, departamento: str) -> None:
            nonlocal proximo_id
            cargo = escolher(self.rng, dom.CARGOS_POR_DEPARTAMENTO[departamento])
            nome = nome_pessoa(self.fake)
            admissao = data_aleatoria(
                self.rng, date(2015, 1, 1), self.fim - timedelta(days=30)
            )

            base_email = local_part_email(nome)
            email = f"{base_email}@redeclinica.example"
            sufixo = 1
            while email in emails_usados:
                sufixo += 1
                email = f"{base_email}{sufixo}@redeclinica.example"
            emails_usados.add(email)

            desligado = sorteia_bool(self.rng, self.prob.funcionario_desligado)
            desligamento = (
                data_aleatoria(self.rng, admissao + timedelta(days=90), self.fim)
                if desligado and admissao + timedelta(days=90) < self.fim
                else None
            )
            criado = datetime.combine(admissao, datetime.min.time(), tzinfo=FUSO_BR)
            atualizado = (
                datetime.combine(desligamento, datetime.min.time(), tzinfo=FUSO_BR)
                if desligamento
                else criado
            )

            linhas.append(
                (
                    proximo_id,
                    f"MAT{proximo_id:06d}",
                    nome,
                    cargo,
                    departamento,
                    unidade_id,
                    email,
                    admissao,
                    desligamento,
                    desligamento is None,
                    criado,
                    atualizado,
                )
            )

            if departamento == "Comercial":
                self.comerciais_por_unidade[unidade_id].append(proximo_id)
            elif departamento == "Atendimento":
                self.atendentes_por_unidade[unidade_id].append(proximo_id)
            elif departamento == "Financeiro":
                self.funcionarios_financeiro.append(proximo_id)
            proximo_id += 1

        # Piso: toda unidade opera com o mínimo viável.
        for unidade_id in self.unidade_ids:
            for departamento in (
                "Comercial",
                "Comercial",
                "Atendimento",
                "Atendimento",
                "Financeiro",
                "Gestão",
            ):
                criar(unidade_id, departamento)

        restante = max(self.vol.funcionarios - len(linhas), 0)
        por_departamento = distribuir_por_peso(restante, dom.PESO_DEPARTAMENTO)
        for departamento, quantidade in por_departamento.items():
            for _ in range(quantidade):
                criar(escolher(self.rng, self.unidade_ids), departamento)

        return copiar_linhas(
            self.conexao,
            "erp.funcionarios",
            [
                "funcionario_id",
                "matricula",
                "nome_funcionario",
                "cargo",
                "departamento",
                "unidade_id",
                "email_corporativo",
                "data_admissao",
                "data_desligamento",
                "ativo",
                "criado_em",
                "atualizado_em",
            ],
            linhas,
        )

    def gerar_medicos(self) -> int:
        """Médicos, seus títulos (N:N) e seus vínculos com unidade (N:N vigente)."""
        medicos: list[tuple] = []
        titulos: list[tuple] = []
        vinculos: list[tuple] = []
        crms_usados: set[tuple[str, str]] = set()

        for medico_id in range(1, self.vol.medicos + 1):
            uf = escolher(self.rng, sorted(set(self.unidade_uf.values())))
            while True:
                crm = f"{self.rng.randint(10000, 199999)}"
                if (crm, uf) not in crms_usados:
                    crms_usados.add((crm, uf))
                    break

            especialidade_principal = escolher(self.rng, self.especialidade_ids)
            inicio_atuacao = data_aleatoria(
                self.rng, date(2012, 1, 1), self.fim - timedelta(days=60)
            )
            criado = datetime.combine(
                inicio_atuacao, datetime.min.time(), tzinfo=FUSO_BR
            )

            medicos.append(
                (
                    medico_id,
                    crm,
                    uf,
                    f"Dr(a). {nome_pessoa(self.fake)}",
                    especialidade_principal,
                    escolher(self.rng, ["CLT", "PJ", "Parceiro"], [0.28, 0.54, 0.18]),
                    dinheiro(self.rng.uniform(20, 60)),
                    inicio_atuacao,
                    True,
                    criado,
                    criado,
                )
            )

            # --- títulos (N:N) ---
            especialidades = [especialidade_principal]
            titulos.append(
                (medico_id, especialidade_principal, inicio_atuacao, True, criado)
            )
            if sorteia_bool(self.rng, self.prob.medico_multiplas_especialidades):
                extras = [
                    esp
                    for esp in self.rng.sample(self.especialidade_ids, k=2)
                    if esp != especialidade_principal
                ]
                for extra in extras:
                    titulos.append(
                        (
                            medico_id,
                            extra,
                            data_aleatoria(self.rng, inicio_atuacao, self.fim),
                            False,
                            criado,
                        )
                    )
                    especialidades.append(extra)
            self.especialidades_do_medico[medico_id] = especialidades

            # --- vínculos com unidade (N:N com vigência) ---
            unidade_principal = escolher(self.rng, self.unidade_ids)
            if sorteia_bool(self.rng, self.prob.medico_trocou_de_unidade):
                # Vínculo encerrado + vínculo atual: é o SCD Tipo 2 na origem.
                unidade_anterior = escolher(self.rng, self.unidade_ids)
                fim_anterior = data_aleatoria(
                    self.rng, inicio_atuacao + timedelta(days=180), self.fim
                )
                if unidade_anterior != unidade_principal:
                    vinculos.append(
                        (
                            medico_id,
                            unidade_anterior,
                            inicio_atuacao,
                            fim_anterior,
                            self.rng.choice([20, 30, 40]),
                            criado,
                            criado,
                        )
                    )
                    inicio_vinculo_atual = fim_anterior + timedelta(days=1)
                else:
                    inicio_vinculo_atual = inicio_atuacao
            else:
                inicio_vinculo_atual = inicio_atuacao

            vinculos.append(
                (
                    medico_id,
                    unidade_principal,
                    inicio_vinculo_atual,
                    None,
                    self.rng.choice([20, 30, 40, 44]),
                    criado,
                    criado,
                )
            )
            self.medicos_por_unidade[unidade_principal].append(medico_id)

            if sorteia_bool(self.rng, self.prob.medico_multiplas_unidades):
                segunda = escolher(self.rng, self.unidade_ids)
                if segunda != unidade_principal:
                    vinculos.append(
                        (
                            medico_id,
                            segunda,
                            inicio_vinculo_atual,
                            None,
                            self.rng.choice([8, 12, 16]),
                            criado,
                            criado,
                        )
                    )
                    self.medicos_por_unidade[segunda].append(medico_id)

        # Unidade sem médico não consegue receber agendamento — garante o piso.
        for unidade_id, lista in self.medicos_por_unidade.items():
            if not lista:
                emprestado = escolher(self.rng, list(range(1, self.vol.medicos + 1)))
                vinculos.append(
                    (
                        emprestado,
                        unidade_id,
                        self.inicio,
                        None,
                        8,
                        datetime.combine(
                            self.inicio, datetime.min.time(), tzinfo=FUSO_BR
                        ),
                        datetime.combine(
                            self.inicio, datetime.min.time(), tzinfo=FUSO_BR
                        ),
                    )
                )
                lista.append(emprestado)

        total = copiar_linhas(
            self.conexao,
            "erp.medicos",
            [
                "medico_id",
                "crm",
                "crm_uf",
                "nome_medico",
                "especialidade_principal_id",
                "tipo_vinculo",
                "percentual_repasse",
                "data_inicio_atuacao",
                "ativo",
                "criado_em",
                "atualizado_em",
            ],
            medicos,
        )
        copiar_linhas(
            self.conexao,
            "erp.medico_especialidade",
            [
                "medico_id",
                "especialidade_id",
                "data_certificacao",
                "principal",
                "criado_em",
            ],
            titulos,
        )
        # Deduplica por PK: o sorteio de segunda unidade pode repetir a combinação.
        vistos: set[tuple[int, int, date]] = set()
        unicos = []
        for vinculo in vinculos:
            chave = (vinculo[0], vinculo[1], vinculo[2])
            if chave not in vistos:
                vistos.add(chave)
                unicos.append(vinculo)
        copiar_linhas(
            self.conexao,
            "erp.medico_unidade",
            [
                "medico_id",
                "unidade_id",
                "data_inicio",
                "data_fim",
                "carga_horaria_semanal",
                "criado_em",
                "atualizado_em",
            ],
            unicos,
        )
        return total

    def gerar_procedimentos(self) -> int:
        linhas = []
        criado = datetime.combine(self.inicio, datetime.min.time(), tzinfo=FUSO_BR)

        for especialidade_id in self.especialidade_ids:
            self.procedimentos_por_especialidade[especialidade_id] = []

        for procedimento_id in range(1, self.vol.procedimentos + 1):
            especialidade_id = self.especialidade_ids[
                (procedimento_id - 1) % len(self.especialidade_ids)
            ]
            area = self.area_por_especialidade[especialidade_id]
            nome_base, categoria, minimo, maximo, duracao = escolher(
                self.rng, dom.TEMPLATES_PROCEDIMENTO[area]
            )
            nome_especialidade = dom.ESPECIALIDADES[especialidade_id - 1][1]
            valor = dinheiro(self.rng.uniform(minimo, maximo))
            custo = dinheiro(valor * Decimal(str(self.rng.uniform(0.28, 0.55))))

            linhas.append(
                (
                    procedimento_id,
                    f"PRC{procedimento_id:05d}",
                    f"{nome_base} — {nome_especialidade}",
                    especialidade_id,
                    categoria,
                    valor,
                    custo,
                    duracao,
                    categoria in ("Cirúrgico", "Estético"),
                    True,
                    criado,
                    criado,
                )
            )
            self.procedimentos_por_especialidade[especialidade_id].append(
                (procedimento_id, valor)
            )

        return copiar_linhas(
            self.conexao,
            "erp.procedimentos",
            [
                "procedimento_id",
                "codigo_procedimento",
                "nome_procedimento",
                "especialidade_id",
                "categoria",
                "valor_tabela",
                "custo_estimado",
                "duracao_media_min",
                "exige_retorno",
                "ativo",
                "criado_em",
                "atualizado_em",
            ],
            linhas,
        )

    def gerar_etapas_funil(self) -> int:
        criado = datetime.combine(self.inicio, datetime.min.time(), tzinfo=FUSO_BR)
        linhas = [
            (ordem, ordem, nome, tipo, dinheiro(probabilidade), criado)
            for ordem, nome, tipo, probabilidade in dom.ETAPAS_FUNIL
        ]
        return copiar_linhas(
            self.conexao,
            "erp.etapas_funil",
            [
                "etapa_id",
                "ordem_etapa",
                "nome_etapa",
                "tipo_etapa",
                "probabilidade_conversao",
                "criado_em",
            ],
            linhas,
        )

    # =========================================================================
    # Marketing e CRM
    # =========================================================================
    def gerar_campanhas(self) -> int:
        linhas = []
        for campanha_id in range(1, self.vol.campanhas + 1):
            inicio = data_aleatoria(
                self.rng, self.inicio, self.fim - timedelta(days=20)
            )
            duracao = self.rng.choice([15, 30, 45, 60, 90])
            fim = inicio + timedelta(days=duracao)

            encerrada = fim < self.hoje
            status = (
                escolher(self.rng, ["Encerrada", "Cancelada"], [0.94, 0.06])
                if encerrada
                else escolher(self.rng, ["Ativa", "Pausada"], [0.85, 0.15])
            )
            previsto = dinheiro(self.rng.uniform(8_000, 180_000))
            # Custo só é fechado quando a campanha encerra — campanha ativa fica NULL.
            realizado = (
                dinheiro(previsto * Decimal(str(self.rng.uniform(0.72, 1.18))))
                if status == "Encerrada"
                else None
            )

            # Campanha nacional (unidade NULL) em 30% dos casos: granularidade mista.
            unidade_id = (
                None
                if sorteia_bool(self.rng, 0.30)
                else escolher(self.rng, self.unidade_ids)
            )
            especialidade_id = (
                escolher(self.rng, self.especialidade_ids)
                if sorteia_bool(self.rng, 0.55)
                else None
            )
            criado = datetime.combine(inicio, datetime.min.time(), tzinfo=FUSO_BR)

            linhas.append(
                (
                    campanha_id,
                    f"CMP{campanha_id:04d}",
                    f"{escolher(self.rng, dom.TEMAS_CAMPANHA)} {inicio.year}",
                    escolher(self.rng, dom.CANAIS_CAMPANHA, dom.PESO_CANAL_CAMPANHA),
                    escolher(self.rng, dom.TIPOS_CAMPANHA, dom.PESO_TIPO_CAMPANHA),
                    unidade_id,
                    especialidade_id,
                    inicio,
                    fim,
                    previsto,
                    realizado,
                    status,
                    criado,
                    criado,
                )
            )
            self.campanhas.append((campanha_id, inicio, fim, unidade_id))

        return copiar_linhas(
            self.conexao,
            "erp.campanhas",
            [
                "campanha_id",
                "codigo_campanha",
                "nome_campanha",
                "canal",
                "tipo_campanha",
                "unidade_id",
                "especialidade_id",
                "data_inicio",
                "data_fim",
                "orcamento_previsto",
                "custo_realizado",
                "status_campanha",
                "criado_em",
                "atualizado_em",
            ],
            linhas,
        )

    def gerar_leads(self) -> int:
        """Leads captados. O destino de cada um é decidido aqui e reusado depois."""
        linhas = []
        for lead_id in range(1, self.vol.leads + 1):
            organico = sorteia_bool(self.rng, self.prob.lead_sem_campanha)
            if organico:
                campanha_id = None
                data_captura_dia = data_aleatoria(self.rng, self.inicio, self.hoje)
                unidade_id = escolher(self.rng, self.unidade_ids)
            else:
                campanha_id, inicio_campanha, fim_campanha, unidade_campanha = escolher(
                    self.rng, self.campanhas
                )
                # O lead nasce DENTRO da janela da campanha que o gerou.
                data_captura_dia = data_aleatoria(
                    self.rng, inicio_campanha, min(fim_campanha, self.hoje)
                )
                unidade_id = unidade_campanha or escolher(self.rng, self.unidade_ids)

            vira_paciente = sorteia_bool(self.rng, self.prob.lead_vira_paciente)
            vira_oportunidade = sorteia_bool(self.rng, self.prob.lead_vira_oportunidade)

            if vira_paciente:
                status, motivo = "Convertido", None
            elif vira_oportunidade:
                status, motivo = "Qualificado", None
            else:
                status = escolher(
                    self.rng,
                    ["Novo", "Em contato", "Desqualificado"],
                    [0.22, 0.34, 0.44],
                )
                motivo = (
                    escolher(self.rng, dom.MOTIVOS_DESQUALIFICACAO)
                    if status == "Desqualificado"
                    else None
                )

            captura = horario_comercial(self.rng, data_captura_dia)
            nome = nome_pessoa(self.fake)
            # E-mail é opcional de verdade: captação por telefone não coleta.
            email = None if sorteia_bool(self.rng, 0.18) else self.fake.email()

            linhas.append(
                (
                    lead_id,
                    campanha_id,
                    nome,
                    email,
                    self.fake.msisdn()[:11],
                    self.fake.city()[:80],
                    self.unidade_uf[unidade_id],
                    escolher(self.rng, dom.ORIGENS_LEAD, dom.PESO_ORIGEM_LEAD),
                    unidade_id,
                    (
                        escolher(self.rng, self.especialidade_ids)
                        if sorteia_bool(self.rng, 0.62)
                        else None
                    ),
                    captura,
                    status,
                    motivo,
                    captura,
                    captura,
                )
            )

            self.lead_ids.append(lead_id)
            self.lead_campanha.append(campanha_id)
            self.lead_data.append(data_captura_dia)
            self.lead_unidade.append(unidade_id)
            self.lead_vira_paciente.append(vira_paciente)
            self.lead_vira_oportunidade.append(vira_oportunidade)

        return copiar_linhas(
            self.conexao,
            "erp.leads",
            [
                "lead_id",
                "campanha_id",
                "nome_lead",
                "email",
                "telefone",
                "cidade",
                "uf",
                "origem",
                "unidade_interesse_id",
                "especialidade_interesse_id",
                "data_captura",
                "status_lead",
                "motivo_desqualificacao",
                "criado_em",
                "atualizado_em",
            ],
            linhas,
        )

    def gerar_pacientes(self) -> int:
        """Pacientes: parte convertida de lead, parte entrando direto na recepção."""
        alvo_com_lead = int(self.vol.pacientes * (1 - self.prob.paciente_sem_lead))
        candidatos = [
            indice
            for indice, converteu in enumerate(self.lead_vira_paciente)
            if converteu
        ]
        self.rng.shuffle(candidatos)
        candidatos = candidatos[:alvo_com_lead]

        linhas = []
        paciente_id = 0

        def montar(
            lead_id: int | None, unidade_id: int, primeiro_contato: date
        ) -> tuple:
            nascimento = data_aleatoria(self.rng, date(1945, 1, 1), date(2010, 12, 31))
            criado = horario_comercial(self.rng, primeiro_contato)
            return (
                paciente_id,
                f"PAC{paciente_id:08d}",
                lead_id,
                nome_pessoa(self.fake),
                gerar_cpf(paciente_id),
                nascimento,
                escolher(self.rng, ["M", "F", "O"], [0.46, 0.53, 0.01]),
                None if sorteia_bool(self.rng, 0.12) else self.fake.email(),
                self.fake.msisdn()[:11],
                self.fake.msisdn()[:11] if sorteia_bool(self.rng, 0.27) else None,
                self.fake.postcode(),
                self.fake.street_name()[:150],
                str(self.rng.randint(1, 2999)),
                escolher(self.rng, ["Apto 21", "Casa 2", "Bloco B", None, None]),
                self.fake.bairro()[:80],
                self.fake.city()[:80],
                self.unidade_uf[unidade_id],
                unidade_id,
                primeiro_contato,
                True,
                criado,
                criado,
            )

        for indice in candidatos:
            paciente_id += 1
            lead_id = self.lead_ids[indice]
            # O cadastro acontece alguns dias DEPOIS da captação do lead.
            primeiro_contato = min(
                self.lead_data[indice] + timedelta(days=self.rng.randint(0, 21)),
                self.hoje,
            )
            linhas.append(montar(lead_id, self.lead_unidade[indice], primeiro_contato))
            self.paciente_por_lead[lead_id] = paciente_id
            self.paciente_unidade[paciente_id] = self.lead_unidade[indice]
            self.paciente_desde[paciente_id] = primeiro_contato
            self.paciente_ids.append(paciente_id)

        while paciente_id < self.vol.pacientes:
            paciente_id += 1
            unidade_id = escolher(self.rng, self.unidade_ids)
            primeiro_contato = data_aleatoria(self.rng, self.inicio, self.hoje)
            linhas.append(montar(None, unidade_id, primeiro_contato))
            self.paciente_unidade[paciente_id] = unidade_id
            self.paciente_desde[paciente_id] = primeiro_contato
            self.paciente_ids.append(paciente_id)

        return copiar_linhas(
            self.conexao,
            "erp.pacientes",
            [
                "paciente_id",
                "codigo_paciente",
                "lead_id",
                "nome_paciente",
                "cpf",
                "data_nascimento",
                "sexo",
                "email",
                "telefone",
                "telefone_secundario",
                "cep",
                "logradouro",
                "numero",
                "complemento",
                "bairro",
                "cidade",
                "uf",
                "unidade_cadastro_id",
                "data_primeiro_contato",
                "ativo",
                "criado_em",
                "atualizado_em",
            ],
            linhas,
        )

    def gerar_paciente_convenio(self) -> int:
        linhas = []
        vistos: set[tuple[int, int, date]] = set()

        for paciente_id in self.paciente_ids:
            if not sorteia_bool(self.rng, self.prob.paciente_com_convenio):
                continue

            quantidade = (
                2
                if sorteia_bool(self.rng, self.prob.paciente_multiplos_convenios)
                else 1
            )
            desde = self.paciente_desde[paciente_id]

            for _ in range(quantidade):
                convenio_id = escolher(self.rng, self.convenio_ids)
                inicio = data_aleatoria(
                    self.rng, max(desde - timedelta(days=720), self.inicio), desde
                )
                chave = (paciente_id, convenio_id, inicio)
                if chave in vistos:
                    continue
                vistos.add(chave)

                # 15% já encerraram o plano — é o que obriga o join por intervalo.
                fim = (
                    data_aleatoria(self.rng, inicio + timedelta(days=200), self.hoje)
                    if sorteia_bool(self.rng, 0.15)
                    and inicio + timedelta(days=200) < self.hoje
                    else None
                )
                criado = datetime.combine(inicio, datetime.min.time(), tzinfo=FUSO_BR)
                linhas.append(
                    (
                        paciente_id,
                        convenio_id,
                        inicio,
                        fim,
                        documento_numerico(self.rng, 16),
                        sorteia_bool(self.rng, 0.78),
                        criado,
                        criado,
                    )
                )

        return copiar_linhas(
            self.conexao,
            "erp.paciente_convenio",
            [
                "paciente_id",
                "convenio_id",
                "data_inicio",
                "data_fim",
                "numero_carteirinha",
                "titular",
                "criado_em",
                "atualizado_em",
            ],
            linhas,
        )

    def gerar_oportunidades(self) -> int:
        """Oportunidades e o log imutável de transição de etapa."""
        oportunidades = []
        historico = []
        oportunidade_id = 0
        historico_id = 0

        for indice, vira in enumerate(self.lead_vira_oportunidade):
            if not vira:
                continue

            oportunidade_id += 1
            lead_id = self.lead_ids[indice]
            unidade_id = self.lead_unidade[indice]
            paciente_id = self.paciente_por_lead.get(lead_id)
            consultor_id = escolher(self.rng, self.comerciais_por_unidade[unidade_id])

            abertura_dia = min(
                self.lead_data[indice] + timedelta(days=self.rng.randint(0, 7)),
                self.hoje,
            )
            abertura = horario_comercial(self.rng, abertura_dia)

            resultado = self.rng.random()
            if resultado < self.prob.oportunidade_ainda_aberta:
                status, etapa_final, motivo = (
                    "Aberta",
                    escolher(self.rng, dom.ETAPAS_ABERTAS),
                    None,
                )
                fechamento = None
            elif (
                resultado
                < self.prob.oportunidade_ainda_aberta + self.prob.oportunidade_ganha
            ):
                status, etapa_final, motivo = "Ganha", dom.ETAPA_GANHA, None
                fechamento = None
            else:
                status = "Perdida"
                etapa_final = dom.ETAPA_PERDIDA
                motivo = escolher(self.rng, dom.MOTIVOS_PERDA_OPORTUNIDADE)
                fechamento = None

            if status != "Aberta":
                dias = self.rng.randint(3, 120)
                fechamento_dia = min(abertura_dia + timedelta(days=dias), self.hoje)
                fechamento = horario_comercial(self.rng, fechamento_dia)
                if fechamento < abertura:
                    fechamento = abertura + timedelta(hours=2)

            oportunidades.append(
                (
                    oportunidade_id,
                    f"OPO{oportunidade_id:07d}",
                    lead_id,
                    paciente_id,
                    unidade_id,
                    consultor_id,
                    etapa_final,
                    dinheiro(self.rng.uniform(600, 42_000)),
                    abertura,
                    fechamento,
                    status,
                    motivo,
                    abertura,
                    fechamento or abertura,
                )
            )

            if paciente_id is not None:
                self.oportunidades_por_paciente.setdefault(paciente_id, []).append(
                    (oportunidade_id, abertura_dia, consultor_id)
                )

            # --- trajeto pelo funil (append-only) ---
            trajeto = self._montar_trajeto(etapa_final)
            momento = abertura
            limite = fechamento or horario_comercial(self.rng, self.hoje)
            passo = max((limite - abertura) / max(len(trajeto), 1), timedelta(hours=1))
            etapa_anterior = None
            for etapa in trajeto:
                historico_id += 1
                historico.append(
                    (
                        historico_id,
                        oportunidade_id,
                        etapa_anterior,
                        etapa,
                        consultor_id,
                        momento,
                        momento,
                    )
                )
                etapa_anterior = etapa
                momento = momento + passo

        total = copiar_linhas(
            self.conexao,
            "erp.oportunidades",
            [
                "oportunidade_id",
                "codigo_oportunidade",
                "lead_id",
                "paciente_id",
                "unidade_id",
                "consultor_id",
                "etapa_atual_id",
                "valor_estimado",
                "data_abertura",
                "data_fechamento",
                "status_oportunidade",
                "motivo_perda",
                "criado_em",
                "atualizado_em",
            ],
            oportunidades,
        )
        copiar_linhas(
            self.conexao,
            "erp.oportunidade_historico_etapa",
            [
                "historico_id",
                "oportunidade_id",
                "etapa_origem_id",
                "etapa_destino_id",
                "funcionario_id",
                "data_mudanca",
                "criado_em",
            ],
            historico,
        )
        self.total_oportunidades = oportunidade_id
        return total

    def _montar_trajeto(self, etapa_final: int) -> list[int]:
        """Caminho percorrido no funil até a etapa final.

        Nem toda oportunidade passa por todas as etapas — pular etapa é o
        comportamento normal de um CRM real, e é isso que faz a métrica de
        "conversão etapa a etapa" ter denominadores diferentes por etapa.
        """
        if etapa_final == dom.ETAPA_PERDIDA:
            ate = self.rng.randint(1, 5)
            trajeto = [
                etapa for etapa in range(1, ate + 1) if sorteia_bool(self.rng, 0.75)
            ]
            return (trajeto or [1]) + [dom.ETAPA_PERDIDA]
        if etapa_final == dom.ETAPA_GANHA:
            trajeto = [etapa for etapa in range(1, 6) if sorteia_bool(self.rng, 0.82)]
            return (trajeto or [1]) + [dom.ETAPA_GANHA]
        return [etapa for etapa in range(1, etapa_final + 1)] or [1]

    def gerar_atividades_crm(self) -> int:
        """Maior tabela do banco. Escrita como stream, sem materializar em lista."""
        leads_sem_oportunidade = [
            indice
            for indice, vira in enumerate(self.lead_vira_oportunidade)
            if not vira
        ]
        # ~80% do volume pendura em oportunidade, o resto em lead ainda cru.
        alvo_oportunidade = int(self.vol.atividades_crm * 0.80)
        alvo_lead = self.vol.atividades_crm - alvo_oportunidade

        def linhas():
            atividade_id = 0
            todos_comerciais = [
                funcionario
                for lista in self.comerciais_por_unidade.values()
                for funcionario in lista
            ]

            for _ in range(alvo_oportunidade):
                atividade_id += 1
                oportunidade_id = self.rng.randint(1, self.total_oportunidades)
                dia = data_aleatoria_util(self.rng, self.inicio, self.hoje)
                momento = horario_comercial(self.rng, dia)
                tipo = escolher(self.rng, dom.TIPOS_ATIVIDADE, dom.PESO_TIPO_ATIVIDADE)
                yield (
                    atividade_id,
                    oportunidade_id,
                    None,
                    escolher(self.rng, todos_comerciais),
                    tipo,
                    escolher(
                        self.rng, dom.RESULTADOS_ATIVIDADE, dom.PESO_RESULTADO_ATIVIDADE
                    ),
                    momento,
                    (
                        self.rng.randint(2, 45)
                        if tipo in ("Ligação", "Reunião", "Visita")
                        else None
                    ),
                    None,
                    momento,
                )

            for _ in range(alvo_lead):
                atividade_id += 1
                indice = escolher(self.rng, leads_sem_oportunidade)
                dia = data_aleatoria_util(
                    self.rng,
                    self.lead_data[indice],
                    min(self.lead_data[indice] + timedelta(days=45), self.hoje),
                )
                momento = horario_comercial(self.rng, dia)
                tipo = escolher(self.rng, dom.TIPOS_ATIVIDADE, dom.PESO_TIPO_ATIVIDADE)
                yield (
                    atividade_id,
                    None,
                    self.lead_ids[indice],
                    escolher(
                        self.rng, self.comerciais_por_unidade[self.lead_unidade[indice]]
                    ),
                    tipo,
                    escolher(
                        self.rng, dom.RESULTADOS_ATIVIDADE, dom.PESO_RESULTADO_ATIVIDADE
                    ),
                    momento,
                    (
                        self.rng.randint(2, 45)
                        if tipo in ("Ligação", "Reunião", "Visita")
                        else None
                    ),
                    None,
                    momento,
                )

        return copiar_linhas(
            self.conexao,
            "erp.atividades_crm",
            [
                "atividade_id",
                "oportunidade_id",
                "lead_id",
                "funcionario_id",
                "tipo_atividade",
                "resultado",
                "data_atividade",
                "duracao_minutos",
                "observacao",
                "criado_em",
            ],
            linhas(),
        )

    # =========================================================================
    # Agenda, consultas e prontuários
    # =========================================================================
    def gerar_agenda(self) -> int:
        """Agendamentos, consultas realizadas e prontuários.

        Feito em duas passadas. A primeira monta um "plano" com listas de
        inteiros (quem, quando, qual status, qual agendamento originou). A
        segunda materializa as linhas. O motivo é o reagendamento: o
        agendamento pai só descobre que virou 'Reagendado' quando o filho é
        criado, o que é impossível num único passe de escrita direta no COPY.
        """
        total = self.vol.agendamentos
        limite_agenda = self.hoje + timedelta(days=30)

        plano_paciente: list[int] = []
        plano_unidade: list[int] = []
        plano_medico: list[int] = []
        plano_especialidade: list[int] = []
        plano_dia: list[date] = []
        plano_status: list[str] = []
        plano_origem: list[int | None] = []

        ultimo_do_paciente: dict[int, int] = {}
        # Peso de recorrência: poucos pacientes concentram muitas visitas, a
        # maioria aparece uma ou duas vezes. Distribuição uniforme daria um
        # "todo paciente volta 5 vezes" que não existe em clínica nenhuma.
        pesos_recorrencia = [self.rng.paretovariate(1.4) for _ in self.paciente_ids]

        # Sorteio em UMA chamada, com pesos acumulados prontos.
        # `random.choices(pop, weights=...)` recalcula o acumulado a cada
        # invocação: chamar 250.000 vezes sobre 50.000 pacientes seria O(n·k),
        # ~1,2e10 operações. Passando `cum_weights` e k=total de uma vez, cai
        # para O(n + k·log n) — de horas para menos de um segundo.
        acumulados = list(accumulate(pesos_recorrencia))
        sorteio_pacientes = self.rng.choices(
            self.paciente_ids, cum_weights=acumulados, k=total
        )

        for indice in range(total):
            paciente_id = sorteio_pacientes[indice]
            desde = self.paciente_desde[paciente_id]

            anterior = ultimo_do_paciente.get(paciente_id)
            eh_reagendamento = anterior is not None and sorteia_bool(
                self.rng, self.prob.agendamento_reagendado
            )

            if eh_reagendamento:
                plano_status[anterior] = "Reagendado"
                dia = plano_dia[anterior] + timedelta(days=self.rng.randint(3, 45))
                if dia > limite_agenda:
                    # O slot original já estava no limite do horizonte da agenda.
                    # Grampear em `limite_agenda` faria a remarcação cair na
                    # MESMA data do original — e, com a hora sorteada de novo na
                    # segunda passada, às vezes antes dele. Remarcação empurra
                    # para frente por definição: estoura o horizonte, não colide.
                    dia = plano_dia[anterior] + timedelta(days=self.rng.randint(3, 10))
                dia = dia_util_seguinte(dia)
                unidade_id = plano_unidade[anterior]
                origem = anterior
            else:
                dia = data_aleatoria_util(
                    self.rng, max(desde, self.inicio), limite_agenda
                )
                # 85% atende na unidade de cadastro; o resto circula pela rede.
                unidade_id = (
                    self.paciente_unidade[paciente_id]
                    if sorteia_bool(self.rng, 0.85)
                    else escolher(self.rng, self.unidade_ids)
                )
                origem = None

            medico_id = escolher(self.rng, self.medicos_por_unidade[unidade_id])
            especialidade_id = escolher(
                self.rng, self.especialidades_do_medico[medico_id]
            )

            if dia > self.hoje:
                status = escolher(self.rng, ["Agendado", "Confirmado"], [0.55, 0.45])
            else:
                status = escolher(self.rng, STATUS_AGENDAMENTO, PESO_STATUS_AGENDAMENTO)
                if status in ("Agendado", "Confirmado"):
                    status = "Falta"  # slot antigo sem desfecho não existe no ERP

            plano_paciente.append(paciente_id)
            plano_unidade.append(unidade_id)
            plano_medico.append(medico_id)
            plano_especialidade.append(especialidade_id)
            plano_dia.append(dia)
            plano_status.append(status)
            plano_origem.append(origem)
            ultimo_do_paciente[paciente_id] = indice

        # --- passada 2: materializa agendamentos ---
        def linhas_agendamentos():
            for indice in range(total):
                momento = horario_comercial(self.rng, plano_dia[indice])
                status = plano_status[indice]
                unidade_id = plano_unidade[indice]

                # A linha nasce quando o slot é marcado, não quando ele acontece.
                # Sem essa antecedência, criado_em e data_hora_agendada seriam a
                # mesma coisa e o pipeline nunca veria um agendamento "futuro"
                # chegando na carga incremental de hoje.
                criado = momento - timedelta(days=self.rng.randint(2, 45))
                cancelado = status in ("Cancelado", "Reagendado")
                data_cancelamento = (
                    momento - timedelta(days=self.rng.randint(1, 5))
                    if cancelado
                    else None
                )
                if data_cancelamento is not None and data_cancelamento < criado:
                    data_cancelamento = criado + timedelta(hours=6)
                yield (
                    indice + 1,
                    plano_paciente[indice],
                    plano_medico[indice],
                    unidade_id,
                    plano_especialidade[indice],
                    (
                        escolher(self.rng, self.convenio_ids)
                        if sorteia_bool(self.rng, 0.45)
                        else None
                    ),
                    (
                        plano_origem[indice] + 1
                        if plano_origem[indice] is not None
                        else None
                    ),
                    escolher(self.rng, self.atendentes_por_unidade[unidade_id]),
                    momento,
                    escolher(self.rng, [20, 30, 40, 60], [0.2, 0.45, 0.25, 0.1]),
                    escolher(
                        self.rng,
                        ["Primeira consulta", "Retorno", "Avaliação", "Procedimento"],
                        [0.38, 0.34, 0.18, 0.10],
                    ),
                    escolher(
                        self.rng,
                        ["Telefone", "WhatsApp", "Site", "Presencial", "App"],
                        [0.24, 0.36, 0.14, 0.16, 0.10],
                    ),
                    status,
                    (
                        escolher(self.rng, dom.MOTIVOS_CANCELAMENTO_AGENDA)
                        if cancelado
                        else None
                    ),
                    data_cancelamento,
                    criado,
                    data_cancelamento or momento,
                )

        total_agendamentos = copiar_linhas(
            self.conexao,
            "erp.agendamentos",
            [
                "agendamento_id",
                "paciente_id",
                "medico_id",
                "unidade_id",
                "especialidade_id",
                "convenio_id",
                "agendamento_origem_id",
                "funcionario_agendou_id",
                "data_hora_agendada",
                "duracao_prevista_min",
                "tipo_agendamento",
                "canal_agendamento",
                "status_agendamento",
                "motivo_cancelamento",
                "data_cancelamento",
                "criado_em",
                "atualizado_em",
            ],
            linhas_agendamentos(),
        )
        self.conexao.commit()

        # --- consultas: só o que foi efetivamente realizado ---
        realizados = [
            indice for indice in range(total) if plano_status[indice] == "Realizado"
        ]
        consulta_por_indice: dict[int, int] = {}

        def linhas_consultas():
            consulta_id = 0
            for indice in realizados:
                consulta_id += 1
                consulta_por_indice[indice] = consulta_id
                inicio = horario_comercial(self.rng, plano_dia[indice])
                duracao = self.rng.randint(15, 70)
                yield (
                    consulta_id,
                    indice + 1,
                    plano_paciente[indice],
                    plano_medico[indice],
                    plano_unidade[indice],
                    inicio,
                    inicio + timedelta(minutes=duracao),
                    self.rng.randint(0, 75),
                    sorteia_bool(self.rng, 0.14),
                    inicio,
                    inicio,
                )

        copiar_linhas(
            self.conexao,
            "erp.consultas",
            [
                "consulta_id",
                "agendamento_id",
                "paciente_id",
                "medico_id",
                "unidade_id",
                "data_hora_inicio",
                "data_hora_fim",
                "tempo_espera_min",
                "houve_encaminhamento",
                "criado_em",
                "atualizado_em",
            ],
            linhas_consultas(),
        )
        self.conexao.commit()

        # --- prontuários: 70% das consultas geram registro clínico ---
        def linhas_prontuarios():
            prontuario_id = 0
            for indice in realizados:
                if not sorteia_bool(self.rng, self.prob.consulta_gera_prontuario):
                    continue
                prontuario_id += 1
                paciente_id = plano_paciente[indice]
                medico_id = plano_medico[indice]
                especialidade_id = plano_especialidade[indice]
                area = self.area_por_especialidade[especialidade_id]
                registro = horario_comercial(self.rng, plano_dia[indice])
                indicou = sorteia_bool(self.rng, self.prob.prontuario_gera_indicacao)

                if indicou:
                    # Guarda o TIMESTAMP do registro, não só a data: o orçamento
                    # pode sair no mesmo dia, e a hora é o que garante que ele
                    # não seja emitido antes do prontuário que o originou.
                    self.prontuarios_por_paciente.setdefault(paciente_id, []).append(
                        (
                            prontuario_id,
                            medico_id,
                            especialidade_id,
                            plano_unidade[indice],
                            registro,
                        )
                    )

                yield (
                    prontuario_id,
                    consulta_por_indice[indice],
                    paciente_id,
                    medico_id,
                    especialidade_id,
                    registro,
                    (
                        escolher(self.rng, dom.CID10_EXEMPLOS)
                        if sorteia_bool(self.rng, 0.72)
                        else None
                    ),
                    escolher(self.rng, dom.QUEIXAS_POR_AREA[area]),
                    (
                        self.fake.sentence(nb_words=8)[:255]
                        if sorteia_bool(self.rng, 0.80)
                        else None
                    ),
                    (
                        escolher(self.rng, dom.CONDUTAS)
                        if sorteia_bool(self.rng, 0.88)
                        else None
                    ),
                    sorteia_bool(self.rng, 0.46),
                    indicou,
                    registro,
                    registro,
                )

        copiar_linhas(
            self.conexao,
            "erp.prontuarios",
            [
                "prontuario_id",
                "consulta_id",
                "paciente_id",
                "medico_id",
                "especialidade_id",
                "data_registro",
                "cid10",
                "queixa_principal",
                "diagnostico",
                "conduta",
                "retorno_recomendado",
                "gerou_indicacao",
                "criado_em",
                "atualizado_em",
            ],
            linhas_prontuarios(),
        )
        return total_agendamentos

    # =========================================================================
    # Comercial
    # =========================================================================
    def gerar_orcamentos(self) -> int:
        """Orçamentos, itens, bridge com prontuário e histórico de status."""
        pacientes_com_indicacao = list(self.prontuarios_por_paciente.keys())
        if not pacientes_com_indicacao:
            raise RuntimeError(
                "Nenhum prontuário indicou procedimento — volume baixo demais."
            )

        orcamentos: list[tuple] = []
        itens: list[tuple] = []
        bridge: list[tuple] = []
        historico: list[tuple] = []
        historico_id = 0
        self.aprovados: list[tuple[int, Decimal, date, int, int]] = []

        for orcamento_id in range(1, self.vol.orcamentos + 1):
            paciente_id = escolher(self.rng, pacientes_com_indicacao)
            disponiveis = self.prontuarios_por_paciente[paciente_id]

            # N:N: a maioria dos orçamentos nasce de um prontuário só, mas parte
            # consolida indicações de vários — e é essa parte que exige bridge.
            quantidade = (
                min(self.rng.randint(2, 3), len(disponiveis))
                if sorteia_bool(self.rng, self.prob.orcamento_multiplos_prontuarios)
                else 1
            )
            escolhidos = self.rng.sample(disponiveis, k=quantidade)

            unidade_id = escolhidos[0][3]
            medico_responsavel = escolhidos[0][1]
            registro_mais_recente = max(pront[4] for pront in escolhidos)
            emissao_dia = min(
                registro_mais_recente.date() + timedelta(days=self.rng.randint(0, 10)),
                self.hoje,
            )
            emissao = horario_comercial(self.rng, emissao_dia)
            # Emissão no mesmo dia do prontuário pode cair numa hora anterior à
            # do registro clínico. Empurra para depois: consulta vem antes de
            # orçamento, sempre.
            if emissao <= registro_mais_recente:
                emissao = registro_mais_recente + timedelta(hours=2)

            consultor_id = escolher(self.rng, self.comerciais_por_unidade[unidade_id])

            # Só liga na oportunidade se ela já existia na data da emissão.
            oportunidade_id = None
            if sorteia_bool(self.rng, self.prob.orcamento_com_oportunidade):
                candidatas = [
                    oportunidade
                    for oportunidade in self.oportunidades_por_paciente.get(
                        paciente_id, []
                    )
                    if oportunidade[1] <= emissao_dia
                ]
                if candidatas:
                    oportunidade_id, _, consultor_id = escolher(self.rng, candidatas)

            # --- itens ---
            quantidade_itens = escolher(
                self.rng, [1, 2, 3, 4, 5, 6], [0.34, 0.28, 0.18, 0.11, 0.06, 0.03]
            )
            valor_bruto = Decimal("0.00")
            itens_do_orcamento = []
            for item_seq in range(1, quantidade_itens + 1):
                especialidade_id = escolhidos[self.rng.randrange(len(escolhidos))][2]
                catalogo = self.procedimentos_por_especialidade[especialidade_id]
                procedimento_id, valor_tabela = escolher(self.rng, catalogo)

                unidades_item = escolher(
                    self.rng, [1, 2, 3, 4], [0.72, 0.16, 0.08, 0.04]
                )
                # Preço praticado varia em torno da tabela e fica CONGELADO aqui.
                valor_unitario = dinheiro(
                    valor_tabela * Decimal(str(self.rng.uniform(0.92, 1.12)))
                )
                desconto_item = (
                    dinheiro(self.rng.uniform(3, 18))
                    if sorteia_bool(self.rng, 0.30)
                    else Decimal("0.00")
                )
                total_item = dinheiro(
                    valor_unitario
                    * unidades_item
                    * (Decimal("100") - desconto_item)
                    / Decimal("100")
                )
                valor_bruto += total_item
                itens_do_orcamento.append(
                    (
                        item_seq,
                        procedimento_id,
                        unidades_item,
                        valor_unitario,
                        desconto_item,
                        total_item,
                        especialidade_id,
                    )
                )

            valor_bruto = dinheiro(valor_bruto)
            percentual_desconto = (
                dinheiro(self.rng.uniform(3, 25))
                if sorteia_bool(self.rng, self.prob.orcamento_com_desconto)
                else Decimal("0.00")
            )
            valor_desconto = dinheiro(
                valor_bruto * percentual_desconto / Decimal("100")
            )
            valor_liquido = dinheiro(valor_bruto - valor_desconto)

            # --- desfecho ---
            sorteio = self.rng.random()
            acumulado = self.prob.taxa_aprovacao_orcamento
            if sorteio < acumulado:
                status = "Aprovado"
            elif sorteio < (acumulado := acumulado + self.prob.taxa_recusa_orcamento):
                status = "Recusado"
            elif sorteio < (
                acumulado := acumulado + self.prob.taxa_expiracao_orcamento
            ):
                status = "Expirado"
            elif sorteio < acumulado + self.prob.taxa_cancelamento_orcamento:
                status = "Cancelado"
            else:
                status = "Enviado"

            data_aprovacao = None
            qtd_parcelas = None
            forma_pagamento = None
            motivo_recusa = None

            if status == "Aprovado":
                aprovacao_dia = min(
                    emissao_dia + timedelta(days=self.rng.randint(0, 25)), self.hoje
                )
                data_aprovacao = horario_comercial(self.rng, aprovacao_dia)
                if data_aprovacao < emissao:
                    data_aprovacao = emissao + timedelta(hours=3)
                qtd_parcelas = escolher(
                    self.rng,
                    self.prob.opcoes_parcelamento,
                    self.prob.peso_parcelamento,
                )
                forma_pagamento = escolher(
                    self.rng, dom.FORMAS_PAGAMENTO, dom.PESO_FORMA_PAGAMENTO
                )
                self.aprovados.append(
                    (
                        orcamento_id,
                        valor_liquido,
                        aprovacao_dia,
                        qtd_parcelas,
                        unidade_id,
                    )
                )
            elif status == "Recusado":
                motivo_recusa = escolher(self.rng, dom.MOTIVOS_RECUSA_ORCAMENTO)

            convenio_id = (
                escolher(self.rng, self.convenio_ids)
                if sorteia_bool(self.rng, self.prob.orcamento_com_convenio)
                else None
            )
            codigo_unidade = dom.UNIDADES[unidade_id - 1][0]

            orcamentos.append(
                (
                    orcamento_id,
                    f"{codigo_unidade}-{orcamento_id:07d}",
                    paciente_id,
                    unidade_id,
                    consultor_id,
                    oportunidade_id,
                    medico_responsavel,
                    convenio_id,
                    emissao,
                    emissao_dia + timedelta(days=30),
                    valor_bruto,
                    valor_desconto,
                    valor_liquido,
                    percentual_desconto,
                    status,
                    data_aprovacao,
                    motivo_recusa,
                    qtd_parcelas,
                    forma_pagamento,
                    emissao,
                    data_aprovacao or emissao,
                )
            )

            for (
                item_seq,
                procedimento_id,
                unidades_item,
                valor_unitario,
                desconto_item,
                total_item,
                _,
            ) in itens_do_orcamento:
                executado = status == "Aprovado" and sorteia_bool(self.rng, 0.78)
                data_execucao = (
                    horario_comercial(
                        self.rng,
                        min(
                            data_aprovacao.date()
                            + timedelta(days=self.rng.randint(1, 60)),
                            self.hoje,
                        ),
                    )
                    if executado
                    else None
                )
                itens.append(
                    (
                        orcamento_id,
                        item_seq,
                        procedimento_id,
                        escolher(self.rng, self.medicos_por_unidade[unidade_id]),
                        unidades_item,
                        valor_unitario,
                        desconto_item,
                        total_item,
                        escolher(
                            self.rng,
                            [
                                "Lado direito",
                                "Lado esquerdo",
                                "Bilateral",
                                "Região frontal",
                                None,
                                None,
                            ],
                        ),
                        (
                            "Executado"
                            if executado
                            else ("Cancelado" if status == "Cancelado" else "Previsto")
                        ),
                        data_execucao,
                        emissao,
                        data_execucao or emissao,
                    )
                )

            # --- bridge com fator de alocação somando exatamente 100 ---
            for prontuario_id, percentual in zip(
                [pront[0] for pront in escolhidos], self._repartir_cem(len(escolhidos))
            ):
                bridge.append((prontuario_id, orcamento_id, percentual, emissao))

            # --- histórico de status (append-only) ---
            trilha = ["Em elaboração", "Enviado"]
            if status not in ("Em elaboração", "Enviado"):
                trilha.append(status)
            anterior = None
            momento = emissao
            for novo in trilha:
                historico_id += 1
                historico.append(
                    (
                        historico_id,
                        orcamento_id,
                        anterior,
                        novo,
                        consultor_id,
                        momento,
                        momento,
                    )
                )
                anterior = novo
                momento = momento + timedelta(days=self.rng.randint(1, 9))

        total = copiar_linhas(
            self.conexao,
            "erp.orcamentos",
            [
                "orcamento_id",
                "numero_orcamento",
                "paciente_id",
                "unidade_id",
                "consultor_id",
                "oportunidade_id",
                "medico_responsavel_id",
                "convenio_id",
                "data_emissao",
                "data_validade",
                "valor_bruto",
                "valor_desconto",
                "valor_liquido",
                "percentual_desconto",
                "status_orcamento",
                "data_aprovacao",
                "motivo_recusa",
                "qtd_parcelas",
                "forma_pagamento",
                "criado_em",
                "atualizado_em",
            ],
            orcamentos,
        )
        copiar_linhas(
            self.conexao,
            "erp.orcamento_itens",
            [
                "orcamento_id",
                "item_seq",
                "procedimento_id",
                "medico_executor_id",
                "quantidade",
                "valor_unitario",
                "percentual_desconto_item",
                "valor_total_item",
                "regiao_aplicacao",
                "status_item",
                "data_execucao",
                "criado_em",
                "atualizado_em",
            ],
            itens,
        )
        copiar_linhas(
            self.conexao,
            "erp.prontuario_orcamento",
            ["prontuario_id", "orcamento_id", "percentual_participacao", "criado_em"],
            bridge,
        )
        copiar_linhas(
            self.conexao,
            "erp.orcamento_historico_status",
            [
                "historico_id",
                "orcamento_id",
                "status_anterior",
                "status_novo",
                "funcionario_id",
                "data_mudanca",
                "criado_em",
            ],
            historico,
        )
        return total

    def _repartir_cem(self, partes: int) -> list[Decimal]:
        """Divide 100% em `partes` pedaços que somam exatamente 100.

        Trabalha com inteiros e joga o resto na última fatia. Repartir com float
        e arredondar cada pedaço deixaria a soma em 99,99 ou 100,01 — e aí a
        receita atribuída por médico não fecharia com a receita total, que é
        justamente o erro que a bridge existe para evitar.
        """
        if partes == 1:
            return [Decimal("100.00")]
        cortes = sorted(self.rng.sample(range(15, 86), k=partes - 1))
        pedacos = []
        anterior = 0
        for corte in cortes:
            pedacos.append(corte - anterior)
            anterior = corte
        pedacos.append(100 - anterior)
        return [Decimal(pedaco).quantize(Decimal("0.01")) for pedaco in pedacos]

    # =========================================================================
    # Financeiro
    # =========================================================================
    def gerar_financeiro(self) -> int:
        """Parcelas dos orçamentos aprovados e os pagamentos recebidos."""
        parcelas: list[tuple] = []
        pagamentos: list[tuple] = []
        pagamento_id = 0

        for (
            orcamento_id,
            valor_liquido,
            aprovacao,
            quantidade,
            unidade_id,
        ) in self.aprovados:
            # A última parcela absorve a diferença do arredondamento — é assim
            # que qualquer sistema de cobrança faz, e garante que a soma das
            # parcelas seja exatamente o valor do orçamento.
            base = dinheiro(valor_liquido / quantidade)
            valores = [base] * (quantidade - 1)
            valores.append(dinheiro(valor_liquido - base * (quantidade - 1)))

            for numero, valor in enumerate(valores, start=1):
                vencimento = aprovacao + timedelta(days=30 * numero)
                criado = datetime.combine(
                    aprovacao, datetime.min.time(), tzinfo=FUSO_BR
                )

                if vencimento > self.hoje:
                    status, valor_pago, ultimo_pagamento = (
                        "Aberta",
                        Decimal("0.00"),
                        None,
                    )
                else:
                    sorteio = self.rng.random()
                    if sorteio < self.prob.parcela_paga:
                        status, valor_pago = "Paga", valor
                    elif (
                        sorteio
                        < self.prob.parcela_paga + self.prob.parcela_paga_parcial
                    ):
                        status = "Parcial"
                        valor_pago = dinheiro(
                            valor * Decimal(str(self.rng.uniform(0.2, 0.8)))
                        )
                    elif sorteio < (
                        self.prob.parcela_paga
                        + self.prob.parcela_paga_parcial
                        + self.prob.parcela_inadimplente
                    ):
                        status, valor_pago = "Vencida", Decimal("0.00")
                    elif sorteio < (
                        self.prob.parcela_paga
                        + self.prob.parcela_paga_parcial
                        + self.prob.parcela_inadimplente
                        + self.prob.parcela_cancelada
                    ):
                        status, valor_pago = "Cancelada", Decimal("0.00")
                    else:
                        status, valor_pago = "Aberta", Decimal("0.00")

                    ultimo_pagamento = None
                    if valor_pago > 0:
                        # Quem paga costuma pagar perto do vencimento, mas atraso existe.
                        atraso = escolher(
                            self.rng,
                            [-3, 0, 2, 7, 15, 40],
                            [0.18, 0.32, 0.20, 0.16, 0.09, 0.05],
                        )
                        ultimo_pagamento = min(
                            vencimento + timedelta(days=atraso), self.hoje
                        )

                # --- pagamentos que sustentam esse valor_pago ---
                if ultimo_pagamento is not None and valor_pago > 0:
                    fracionado = sorteia_bool(self.rng, self.prob.pagamento_fracionado)
                    partes = [valor_pago]
                    if fracionado:
                        primeira = dinheiro(valor_pago * Decimal("0.4"))
                        partes = [primeira, dinheiro(valor_pago - primeira)]

                    estornado = sorteia_bool(self.rng, self.prob.pagamento_estornado)
                    for indice_parte, parte in enumerate(partes):
                        pagamento_id += 1
                        data_parte = ultimo_pagamento - timedelta(
                            days=(len(partes) - 1 - indice_parte) * 12
                        )
                        momento = horario_comercial(
                            self.rng, max(data_parte, aprovacao)
                        )
                        forma = escolher(
                            self.rng, dom.FORMAS_PAGAMENTO, dom.PESO_FORMA_PAGAMENTO
                        )
                        pagamentos.append(
                            (
                                pagamento_id,
                                orcamento_id,
                                numero,
                                # Paciente pode pagar em qualquer unidade da rede.
                                (
                                    unidade_id
                                    if sorteia_bool(self.rng, 0.88)
                                    else escolher(self.rng, self.unidade_ids)
                                ),
                                parte,
                                momento,
                                forma,
                                (
                                    escolher(self.rng, dom.BANDEIRAS_CARTAO)
                                    if forma in ("Crédito", "Débito")
                                    else None
                                ),
                                (
                                    documento_numerico(self.rng, 12)
                                    if forma in ("Crédito", "Débito")
                                    else None
                                ),
                                estornado,
                                (
                                    momento + timedelta(days=self.rng.randint(1, 20))
                                    if estornado
                                    else None
                                ),
                                momento,
                            )
                        )

                    # Estorno reabre a parcela: o dinheiro voltou, a dívida também.
                    # A linha de pagamento CONTINUA na tabela — somar sem filtrar
                    # `estornado` é o que infla o caixa na conciliação.
                    if estornado:
                        status, valor_pago, ultimo_pagamento = (
                            "Aberta",
                            Decimal("0.00"),
                            None,
                        )

                parcelas.append(
                    (
                        orcamento_id,
                        numero,
                        valor,
                        vencimento,
                        status,
                        valor_pago,
                        ultimo_pagamento,
                        escolher(
                            self.rng, dom.FORMAS_PAGAMENTO, dom.PESO_FORMA_PAGAMENTO
                        ),
                        criado,
                        datetime.combine(
                            ultimo_pagamento or aprovacao,
                            datetime.min.time(),
                            tzinfo=FUSO_BR,
                        ),
                    )
                )

        total = copiar_linhas(
            self.conexao,
            "erp.parcelas",
            [
                "orcamento_id",
                "numero_parcela",
                "valor_parcela",
                "data_vencimento",
                "status_parcela",
                "valor_pago",
                "data_ultimo_pagamento",
                "forma_pagamento_prevista",
                "criado_em",
                "atualizado_em",
            ],
            parcelas,
        )
        self.conexao.commit()
        copiar_linhas(
            self.conexao,
            "erp.pagamentos",
            [
                "pagamento_id",
                "orcamento_id",
                "numero_parcela",
                "unidade_recebimento_id",
                "valor_pago",
                "data_pagamento",
                "forma_pagamento",
                "bandeira_cartao",
                "codigo_autorizacao",
                "estornado",
                "data_estorno",
                "criado_em",
            ],
            pagamentos,
        )
        return total
