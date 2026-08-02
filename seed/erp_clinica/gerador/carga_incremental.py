"""Simulação de um dia de operação do ERP.

Por que isso existe
-------------------
Uma base estática grande prova pouco: qualquer pipeline consegue ler 2 milhões
de linhas uma vez. O que separa um projeto de portfólio de um projeto de
verdade é o segundo dia — quando parte das linhas é nova, parte foi alterada,
e o pipeline precisa decidir o que fazer com cada uma sem reprocessar tudo.

Cada execução gera os três padrões de mudança que um pipeline tem que tratar:

  INSERT em tabela append-only  → atividade de CRM, pagamento, histórico.
      Ingestão: append por watermark de `criado_em`. Sem MERGE.

  INSERT em tabela mutável      → paciente novo, agendamento novo, orçamento novo.
      Ingestão: MERGE, porque a mesma chave pode voltar amanhã alterada.

  UPDATE em tabela mutável      → telefone que muda, agendamento que vira falta,
      orçamento que é aprovado, parcela que é paga.
      Ingestão: MERGE por `atualizado_em`. É aqui que um pipeline mal feito
      duplica linha em vez de atualizar.

Um detalhe de propósito: a virada de parcela para 'Vencida' acontece por
passagem de tempo, sem ninguém tocar no registro. O ERP faz esse UPDATE em
lote, o que produz um dia com milhares de linhas alteradas de uma vez — o
famoso pico que derruba pipeline dimensionado pela média.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from decimal import Decimal

from faker import Faker

from . import dominios as dom
from .config import PROBABILIDADES, ConfigProbabilidades
from .utils import (
    dia_util_seguinte,
    dinheiro,
    documento_numerico,
    escolher,
    gerar_cpf,
    horario_comercial,
    nome_pessoa,
    sorteia_bool,
)


class SimuladorDiaOperacional:
    """Aplica um dia de movimento sobre uma base já carregada."""

    def __init__(
        self,
        conexao,
        data_operacao: date,
        intensidade: float = 1.0,
        semente: int | None = None,
        probabilidades: ConfigProbabilidades = PROBABILIDADES,
    ) -> None:
        self.conexao = conexao
        self.dia = data_operacao
        # Escala todos os volumes do dia. Útil para simular sexta-feira cheia
        # (1.6) ou feriado (0.2) e ver como o pipeline reage a volume irregular.
        self.intensidade = intensidade
        self.prob = probabilidades

        semente_efetiva = semente if semente is not None else data_operacao.toordinal()
        self.rng = random.Random(semente_efetiva)
        self.fake = Faker("pt_BR")
        Faker.seed(semente_efetiva)

        self.agora = horario_comercial(self.rng, data_operacao)
        self.referencias: dict = {}

    # -------------------------------------------------------------- helpers --
    def _quantidade(self, base: int) -> int:
        """Volume do dia com variação aleatória em torno da base."""
        valor = base * self.intensidade * self.rng.uniform(0.75, 1.3)
        return max(int(valor), 0)

    def _momento(self) -> datetime:
        return horario_comercial(self.rng, self.dia)

    def _consultar(self, sql: str, parametros: tuple = ()) -> list[tuple]:
        with self.conexao.cursor() as cursor:
            cursor.execute(sql, parametros)
            return cursor.fetchall()

    def _executar(self, sql: str, parametros: tuple = ()) -> int:
        with self.conexao.cursor() as cursor:
            cursor.execute(sql, parametros)
            return cursor.rowcount

    def _executar_lote(self, sql: str, linhas: list[tuple]) -> int:
        if not linhas:
            return 0
        with self.conexao.cursor() as cursor:
            cursor.executemany(sql, linhas)
        return len(linhas)

    def _ultimo_id(self, tabela: str, coluna: str) -> int:
        """Maior ID já gravado, para continuar a numeração dos códigos naturais.

        Os códigos de negócio (PAC…, CMP…, ORC…) são sequenciais, não
        aleatórios — é assim que ERP numera documento. Sortear um número e
        torcer para não repetir estoura o UNIQUE assim que o volume cresce.
        """
        return self._consultar(f"SELECT COALESCE(MAX({coluna}), 0) FROM erp.{tabela}")[
            0
        ][0]

    # ---------------------------------------------------------- referências --
    def _carregar_referencias(self) -> None:
        """Lê do banco os cadastros necessários para amarrar as FKs do dia."""
        referencias = self.referencias

        unidades = self._consultar(
            "SELECT unidade_id, codigo_unidade FROM erp.unidades WHERE ativa"
        )
        referencias["unidades"] = [linha[0] for linha in unidades]
        referencias["codigo_unidade"] = dict(unidades)

        referencias["comerciais"] = {}
        referencias["atendentes"] = {}
        for unidade_id, departamento, funcionario_id in self._consultar(
            """
            SELECT unidade_id, departamento, funcionario_id
              FROM erp.funcionarios
             WHERE ativo AND departamento IN ('Comercial', 'Atendimento')
            """
        ):
            chave = "comerciais" if departamento == "Comercial" else "atendentes"
            referencias[chave].setdefault(unidade_id, []).append(funcionario_id)

        referencias["medicos_por_unidade"] = {}
        for medico_id, unidade_id in self._consultar(
            """
            SELECT medico_id, unidade_id
              FROM erp.medico_unidade
             WHERE data_fim IS NULL
            """
        ):
            referencias["medicos_por_unidade"].setdefault(unidade_id, []).append(
                medico_id
            )

        referencias["especialidades_do_medico"] = {}
        for medico_id, especialidade_id in self._consultar(
            "SELECT medico_id, especialidade_id FROM erp.medico_especialidade"
        ):
            referencias["especialidades_do_medico"].setdefault(medico_id, []).append(
                especialidade_id
            )

        # Área da especialidade: define o vocabulário de queixa do prontuário,
        # para que o registro clínico gerado no dia se pareça com o histórico.
        referencias["area_especialidade"] = dict(
            self._consultar("SELECT especialidade_id, area FROM erp.especialidades")
        )

        referencias["convenios"] = [
            linha[0]
            for linha in self._consultar(
                "SELECT convenio_id FROM erp.convenios WHERE ativo"
            )
        ]

        referencias["procedimentos"] = {}
        for procedimento_id, especialidade_id, valor in self._consultar(
            "SELECT procedimento_id, especialidade_id, valor_tabela FROM erp.procedimentos WHERE ativo"
        ):
            referencias["procedimentos"].setdefault(especialidade_id, []).append(
                (procedimento_id, valor)
            )

        # Campanha só capta lead enquanto está no ar.
        referencias["campanhas_ativas"] = [
            linha[0]
            for linha in self._consultar(
                """
                SELECT campanha_id FROM erp.campanhas
                 WHERE status_campanha = 'Ativa'
                   AND data_inicio <= %s
                   AND (data_fim IS NULL OR data_fim >= %s)
                """,
                (self.dia, self.dia),
            )
        ]

    # =========================================================================
    # Orquestração
    # =========================================================================
    def executar(self) -> dict[str, int]:
        self._carregar_referencias()

        resultado: dict[str, int] = {}
        etapas = [
            ("campanhas_encerradas", self.encerrar_campanhas),
            ("campanhas_novas", self.abrir_campanhas),
            ("leads_novos", self.captar_leads),
            ("oportunidades_novas", self.abrir_oportunidades),
            ("oportunidades_avancadas", self.avancar_oportunidades),
            ("atividades_crm", self.registrar_atividades),
            ("pacientes_novos", self.cadastrar_pacientes),
            ("pacientes_atualizados", self.atualizar_cadastro_pacientes),
            ("agendamentos_novos", self.abrir_agendamentos),
            ("agendamentos_reagendados", self.reagendar),
            ("agendamentos_cancelados", self.cancelar_agendamentos),
            ("consultas_realizadas", self.realizar_consultas),
            ("orcamentos_novos", self.emitir_orcamentos),
            ("orcamentos_decididos", self.decidir_orcamentos),
            ("parcelas_vencidas", self.virar_vencimentos),
            ("pagamentos_recebidos", self.receber_pagamentos),
        ]

        for nome, etapa in etapas:
            resultado[nome] = etapa()
            self.conexao.commit()

        return resultado

    # =========================================================================
    # Marketing
    # =========================================================================
    def encerrar_campanhas(self) -> int:
        """Campanha que passou da data de fim é encerrada e ganha custo realizado."""
        return self._executar(
            """
            UPDATE erp.campanhas
               SET status_campanha = 'Encerrada',
                   -- random() devolve double precision; sem o cast explícito o
                   -- produto vira double e o ROUND de duas casas não existe.
                   custo_realizado = ROUND((orcamento_previsto * (0.72 + random() * 0.46))::numeric, 2),
                   atualizado_em   = %s
             WHERE status_campanha IN ('Ativa', 'Pausada')
               AND data_fim < %s
            """,
            (self.agora, self.dia),
        )

    def abrir_campanhas(self) -> int:
        quantidade = 1 if sorteia_bool(self.rng, 0.25 * self.intensidade) else 0
        proximo = self._ultimo_id("campanhas", "campanha_id")
        linhas = []
        for indice in range(quantidade):
            unidade_id = (
                None
                if sorteia_bool(self.rng, 0.30)
                else escolher(self.rng, self.referencias["unidades"])
            )
            linhas.append(
                (
                    f"CMP{proximo + indice + 1:04d}",
                    f"{escolher(self.rng, dom.TEMAS_CAMPANHA)} {self.dia.year}",
                    escolher(self.rng, dom.CANAIS_CAMPANHA, dom.PESO_CANAL_CAMPANHA),
                    escolher(self.rng, dom.TIPOS_CAMPANHA, dom.PESO_TIPO_CAMPANHA),
                    unidade_id,
                    self.dia,
                    self.dia + timedelta(days=self.rng.choice([30, 45, 60])),
                    dinheiro(self.rng.uniform(8_000, 180_000)),
                    self.agora,
                )
            )

        inseridas = self._executar_lote(
            """
            INSERT INTO erp.campanhas
                (codigo_campanha, nome_campanha, canal, tipo_campanha, unidade_id,
                 data_inicio, data_fim, orcamento_previsto, status_campanha,
                 criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Ativa', %s, %s)
            """,
            [linha + (linha[-1],) for linha in linhas],
        )
        if inseridas:
            self._carregar_referencias()
        return inseridas

    def captar_leads(self) -> int:
        quantidade = self._quantidade(120)
        ativas = self.referencias["campanhas_ativas"]
        linhas = []

        for _ in range(quantidade):
            organico = not ativas or sorteia_bool(self.rng, self.prob.lead_sem_campanha)
            campanha_id = None if organico else escolher(self.rng, ativas)
            unidade_id = escolher(self.rng, self.referencias["unidades"])
            momento = self._momento()

            linhas.append(
                (
                    campanha_id,
                    nome_pessoa(self.fake),
                    None if sorteia_bool(self.rng, 0.18) else self.fake.email(),
                    self.fake.msisdn()[:11],
                    self.fake.city()[:80],
                    escolher(self.rng, dom.ORIGENS_LEAD, dom.PESO_ORIGEM_LEAD),
                    unidade_id,
                    momento,
                    momento,
                    momento,
                )
            )

        return self._executar_lote(
            """
            INSERT INTO erp.leads
                (campanha_id, nome_lead, email, telefone, cidade, origem,
                 unidade_interesse_id, data_captura, status_lead, criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Novo', %s, %s)
            """,
            linhas,
        )

    # =========================================================================
    # CRM
    # =========================================================================
    def abrir_oportunidades(self) -> int:
        """Promove leads recentes ainda sem oportunidade."""
        candidatos = self._consultar(
            """
            SELECT l.lead_id, COALESCE(l.unidade_interesse_id, %s)
              FROM erp.leads l
             WHERE l.status_lead IN ('Novo', 'Em contato')
               AND l.data_captura >= %s
               AND NOT EXISTS (SELECT 1 FROM erp.oportunidades o WHERE o.lead_id = l.lead_id)
             ORDER BY l.data_captura DESC
             LIMIT %s
            """,
            (
                self.referencias["unidades"][0],
                self.dia - timedelta(days=21),
                self._quantidade(70),
            ),
        )

        leads_promovidos = []
        total = 0

        # INSERT em laço com RETURNING (e não executemany) porque a entrada no
        # funil também é um EVENTO: sem gravar a primeira transição aqui, o
        # histórico dessa oportunidade começaria pela segunda etapa e o cálculo
        # de "tempo até a primeira etapa" ficaria sem ponto de partida.
        with self.conexao.cursor() as cursor:
            for lead_id, unidade_id in candidatos:
                comerciais = self.referencias["comerciais"].get(unidade_id)
                if not comerciais:
                    continue

                momento = self._momento()
                consultor_id = escolher(self.rng, comerciais)
                cursor.execute(
                    """
                    INSERT INTO erp.oportunidades
                        (codigo_oportunidade, lead_id, unidade_id, consultor_id,
                         etapa_atual_id, valor_estimado, data_abertura,
                         status_oportunidade, criado_em, atualizado_em)
                    VALUES (%s, %s, %s, %s, 1, %s, %s, 'Aberta', %s, %s)
                    RETURNING oportunidade_id
                    """,
                    (
                        f"OPO{self.dia:%Y%m%d}{lead_id}",
                        lead_id,
                        unidade_id,
                        consultor_id,
                        dinheiro(self.rng.uniform(600, 42_000)),
                        momento,
                        momento,
                        momento,
                    ),
                )
                oportunidade_id = cursor.fetchone()[0]

                cursor.execute(
                    """
                    INSERT INTO erp.oportunidade_historico_etapa
                        (oportunidade_id, etapa_origem_id, etapa_destino_id,
                         funcionario_id, data_mudanca, criado_em)
                    VALUES (%s, NULL, 1, %s, %s, %s)
                    """,
                    (oportunidade_id, consultor_id, momento, momento),
                )
                leads_promovidos.append((self.agora, lead_id))
                total += 1
        # O lead muda de status junto — é UPDATE numa tabela mutável.
        self._executar_lote(
            "UPDATE erp.leads SET status_lead = 'Qualificado', atualizado_em = %s WHERE lead_id = %s",
            leads_promovidos,
        )
        return total

    def avancar_oportunidades(self) -> int:
        """Move oportunidades abertas pelo funil, gravando o evento no histórico.

        Duas escritas para a mesma mudança: UPDATE no estado atual e INSERT no
        log. É exatamente o par que obriga o pipeline a usar duas estratégias
        de ingestão diferentes na mesma execução.
        """
        # Só mexe em oportunidade aberta em dia anterior: a que nasceu hoje
        # ainda não teve tempo de andar, e fechá-la agora produziria fechamento
        # anterior à abertura (as duas caem no mesmo dia, em horas aleatórias).
        abertas = self._consultar(
            """
            SELECT oportunidade_id, etapa_atual_id, consultor_id
              FROM erp.oportunidades
             WHERE status_oportunidade = 'Aberta'
               AND data_abertura::date < %s
             ORDER BY random()
             LIMIT %s
            """,
            (self.dia, self._quantidade(260)),
        )

        atualizacoes_abertas, atualizacoes_fechadas, eventos = [], [], []

        for oportunidade_id, etapa_atual, consultor_id in abertas:
            sorteio = self.rng.random()
            if sorteio < 0.18:
                nova_etapa, status, motivo = (
                    dom.ETAPA_PERDIDA,
                    "Perdida",
                    escolher(self.rng, dom.MOTIVOS_PERDA_OPORTUNIDADE),
                )
            elif sorteio < 0.30 and etapa_atual >= 4:
                nova_etapa, status, motivo = dom.ETAPA_GANHA, "Ganha", None
            else:
                nova_etapa = min(etapa_atual + 1, 5)
                if nova_etapa == etapa_atual:
                    continue
                status, motivo = "Aberta", None

            eventos.append(
                (
                    oportunidade_id,
                    etapa_atual,
                    nova_etapa,
                    consultor_id,
                    self.agora,
                    self.agora,
                )
            )
            if status == "Aberta":
                atualizacoes_abertas.append((nova_etapa, self.agora, oportunidade_id))
            else:
                atualizacoes_fechadas.append(
                    (
                        nova_etapa,
                        status,
                        motivo,
                        self.agora,
                        self.agora,
                        oportunidade_id,
                    )
                )

        self._executar_lote(
            """
            INSERT INTO erp.oportunidade_historico_etapa
                (oportunidade_id, etapa_origem_id, etapa_destino_id, funcionario_id,
                 data_mudanca, criado_em)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            eventos,
        )
        self._executar_lote(
            """
            UPDATE erp.oportunidades
               SET etapa_atual_id = %s, atualizado_em = %s
             WHERE oportunidade_id = %s
            """,
            atualizacoes_abertas,
        )
        self._executar_lote(
            """
            UPDATE erp.oportunidades
               SET etapa_atual_id = %s, status_oportunidade = %s, motivo_perda = %s,
                   -- GREATEST protege a invariante fechamento >= abertura mesmo
                   -- que o horário sorteado do dia caia antes da abertura.
                   data_fechamento = GREATEST(data_abertura + interval '1 hour', %s),
                   atualizado_em = %s
             WHERE oportunidade_id = %s
            """,
            atualizacoes_fechadas,
        )
        return len(eventos)

    def registrar_atividades(self) -> int:
        alvos = self._consultar(
            """
            SELECT oportunidade_id, consultor_id
              FROM erp.oportunidades
             WHERE status_oportunidade = 'Aberta'
             ORDER BY random()
             LIMIT %s
            """,
            (self._quantidade(700),),
        )

        linhas = []
        for oportunidade_id, consultor_id in alvos:
            for _ in range(self.rng.randint(1, 3)):
                tipo = escolher(self.rng, dom.TIPOS_ATIVIDADE, dom.PESO_TIPO_ATIVIDADE)
                momento = self._momento()
                linhas.append(
                    (
                        oportunidade_id,
                        consultor_id,
                        tipo,
                        escolher(
                            self.rng,
                            dom.RESULTADOS_ATIVIDADE,
                            dom.PESO_RESULTADO_ATIVIDADE,
                        ),
                        momento,
                        (
                            self.rng.randint(2, 45)
                            if tipo in ("Ligação", "Reunião", "Visita")
                            else None
                        ),
                        momento,
                    )
                )

        return self._executar_lote(
            """
            INSERT INTO erp.atividades_crm
                (oportunidade_id, funcionario_id, tipo_atividade, resultado,
                 data_atividade, duracao_minutos, criado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            linhas,
        )

    # =========================================================================
    # Pacientes
    # =========================================================================
    def cadastrar_pacientes(self) -> int:
        """Novos pacientes: parte convertida de lead qualificado, parte walk-in."""
        quantidade = self._quantidade(55)
        convertidos = self._consultar(
            """
            SELECT l.lead_id, COALESCE(l.unidade_interesse_id, %s), l.nome_lead,
                   l.telefone, l.email
              FROM erp.leads l
             WHERE l.status_lead = 'Qualificado'
               AND NOT EXISTS (SELECT 1 FROM erp.pacientes p WHERE p.lead_id = l.lead_id)
             ORDER BY random()
             LIMIT %s
            """,
            (self.referencias["unidades"][0], int(quantidade * 0.7)),
        )

        linhas = []
        leads_convertidos = []
        proximo = self._ultimo_id("pacientes", "paciente_id")

        for lead_id, unidade_id, nome, telefone, email in convertidos:
            proximo += 1
            linhas.append(
                self._linha_paciente(
                    proximo, lead_id, unidade_id, nome, telefone, email
                )
            )
            leads_convertidos.append((self.agora, lead_id))

        for _ in range(quantidade - len(convertidos)):
            proximo += 1
            linhas.append(
                self._linha_paciente(
                    proximo,
                    None,
                    escolher(self.rng, self.referencias["unidades"]),
                    nome_pessoa(self.fake),
                    self.fake.msisdn()[:11],
                    None if sorteia_bool(self.rng, 0.12) else self.fake.email(),
                )
            )

        total = self._executar_lote(
            """
            INSERT INTO erp.pacientes
                (codigo_paciente, lead_id, nome_paciente, cpf, data_nascimento, sexo,
                 email, telefone, cep, logradouro, numero, bairro, cidade, uf,
                 unidade_cadastro_id, data_primeiro_contato, criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    (SELECT uf FROM erp.unidades WHERE unidade_id = %s), %s, %s, %s, %s)
            """,
            linhas,
        )
        self._executar_lote(
            "UPDATE erp.leads SET status_lead = 'Convertido', atualizado_em = %s WHERE lead_id = %s",
            leads_convertidos,
        )
        return total

    def _linha_paciente(
        self, sequencial, lead_id, unidade_id, nome, telefone, email
    ) -> tuple:
        nascimento = date(
            self.rng.randint(1945, 2010),
            self.rng.randint(1, 12),
            self.rng.randint(1, 28),
        )
        return (
            f"PAC{sequencial:08d}",
            lead_id,
            nome,
            gerar_cpf(sequencial),
            nascimento,
            escolher(self.rng, ["M", "F", "O"], [0.46, 0.53, 0.01]),
            email,
            telefone,
            self.fake.postcode(),
            self.fake.street_name()[:150],
            str(self.rng.randint(1, 2999)),
            self.fake.bairro()[:80],
            self.fake.city()[:80],
            unidade_id,
            unidade_id,
            self.dia,
            self.agora,
            self.agora,
        )

    def atualizar_cadastro_pacientes(self) -> int:
        """Mudança de contato e endereço — o UPDATE puro do CDC.

        Nenhuma linha nova é criada: só o valor antigo é sobrescrito e o
        `atualizado_em` avança. Se o pipeline fizer append em vez de MERGE, o
        paciente aparece duas vezes na silver e toda contagem distinta quebra.
        """
        alvos = self._consultar(
            """
            SELECT paciente_id FROM erp.pacientes
             WHERE ativo
             ORDER BY random()
             LIMIT %s
            """,
            (self._quantidade(180),),
        )

        atualizacoes = []
        for (paciente_id,) in alvos:
            sorteio = self.rng.random()
            if sorteio < 0.45:
                atualizacoes.append(
                    (self.fake.msisdn()[:11], None, None, None, self.agora, paciente_id)
                )
            elif sorteio < 0.72:
                atualizacoes.append(
                    (None, self.fake.email(), None, None, self.agora, paciente_id)
                )
            else:
                atualizacoes.append(
                    (
                        None,
                        None,
                        self.fake.street_name()[:150],
                        self.fake.postcode(),
                        self.agora,
                        paciente_id,
                    )
                )

        return self._executar_lote(
            """
            UPDATE erp.pacientes
               SET telefone      = COALESCE(%s, telefone),
                   email         = COALESCE(%s, email),
                   logradouro    = COALESCE(%s, logradouro),
                   cep           = COALESCE(%s, cep),
                   atualizado_em = %s
             WHERE paciente_id = %s
            """,
            atualizacoes,
        )

    # =========================================================================
    # Agenda
    # =========================================================================
    def abrir_agendamentos(self) -> int:
        pacientes = self._consultar(
            """
            SELECT paciente_id, unidade_cadastro_id FROM erp.pacientes
             WHERE ativo
             ORDER BY random()
             LIMIT %s
            """,
            (self._quantidade(320),),
        )

        linhas = []
        for paciente_id, unidade_cadastro in pacientes:
            unidade_id = (
                unidade_cadastro
                if sorteia_bool(self.rng, 0.85)
                else escolher(self.rng, self.referencias["unidades"])
            )
            medicos = self.referencias["medicos_por_unidade"].get(unidade_id)
            atendentes = self.referencias["atendentes"].get(unidade_id)
            if not medicos or not atendentes:
                continue

            medico_id = escolher(self.rng, medicos)
            especialidades = self.referencias["especialidades_do_medico"].get(medico_id)
            if not especialidades:
                continue

            # O slot é marcado hoje para uma data futura — é isso que alimenta
            # a agenda dos próximos dias da simulação.
            data_slot = dia_util_seguinte(
                self.dia + timedelta(days=self.rng.randint(1, 45))
            )
            linhas.append(
                (
                    paciente_id,
                    medico_id,
                    unidade_id,
                    escolher(self.rng, especialidades),
                    (
                        escolher(self.rng, self.referencias["convenios"])
                        if sorteia_bool(self.rng, 0.45)
                        else None
                    ),
                    escolher(self.rng, atendentes),
                    horario_comercial(self.rng, data_slot),
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
                    self.agora,
                    self.agora,
                )
            )

        return self._executar_lote(
            """
            INSERT INTO erp.agendamentos
                (paciente_id, medico_id, unidade_id, especialidade_id, convenio_id,
                 funcionario_agendou_id, data_hora_agendada, duracao_prevista_min,
                 tipo_agendamento, canal_agendamento, status_agendamento,
                 criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Agendado', %s, %s)
            """,
            linhas,
        )

    def reagendar(self) -> int:
        """Remarcação: o slot antigo vira 'Reagendado' e nasce um novo apontando para ele.

        Um INSERT e um UPDATE descrevendo o mesmo fato. No Warehouse isso vira
        cadeia auto-referenciada — contar "quantas vezes esse paciente remarcou"
        exige percorrer a cadeia, não contar linhas.
        """
        alvos = self._consultar(
            """
            SELECT agendamento_id, paciente_id, medico_id, unidade_id, especialidade_id,
                   convenio_id, funcionario_agendou_id, data_hora_agendada,
                   duracao_prevista_min, tipo_agendamento
              FROM erp.agendamentos
             WHERE status_agendamento IN ('Agendado', 'Confirmado')
               AND data_hora_agendada > %s
             ORDER BY random()
             LIMIT %s
            """,
            (self.agora, self._quantidade(45)),
        )

        novos, antigos = [], []
        for (
            agendamento_id,
            paciente_id,
            medico_id,
            unidade_id,
            especialidade_id,
            convenio_id,
            funcionario_id,
            data_antiga,
            duracao,
            tipo,
        ) in alvos:
            nova_data = horario_comercial(
                self.rng,
                dia_util_seguinte(
                    data_antiga.date() + timedelta(days=self.rng.randint(3, 40))
                ),
            )
            novos.append(
                (
                    paciente_id,
                    medico_id,
                    unidade_id,
                    especialidade_id,
                    convenio_id,
                    agendamento_id,
                    funcionario_id,
                    nova_data,
                    duracao,
                    tipo,
                    self.agora,
                    self.agora,
                )
            )
            antigos.append(
                (
                    escolher(self.rng, dom.MOTIVOS_CANCELAMENTO_AGENDA),
                    self.agora,
                    self.agora,
                    agendamento_id,
                )
            )

        self._executar_lote(
            """
            UPDATE erp.agendamentos
               SET status_agendamento = 'Reagendado', motivo_cancelamento = %s,
                   data_cancelamento = %s, atualizado_em = %s
             WHERE agendamento_id = %s
            """,
            antigos,
        )
        return self._executar_lote(
            """
            INSERT INTO erp.agendamentos
                (paciente_id, medico_id, unidade_id, especialidade_id, convenio_id,
                 agendamento_origem_id, funcionario_agendou_id, data_hora_agendada,
                 duracao_prevista_min, tipo_agendamento, canal_agendamento,
                 status_agendamento, criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Telefone', 'Agendado', %s, %s)
            """,
            novos,
        )

    def cancelar_agendamentos(self) -> int:
        alvos = self._consultar(
            """
            SELECT agendamento_id FROM erp.agendamentos
             WHERE status_agendamento IN ('Agendado', 'Confirmado')
               AND data_hora_agendada > %s
             ORDER BY random()
             LIMIT %s
            """,
            (self.agora, self._quantidade(38)),
        )
        return self._executar_lote(
            """
            UPDATE erp.agendamentos
               SET status_agendamento = 'Cancelado', motivo_cancelamento = %s,
                   data_cancelamento = %s, atualizado_em = %s
             WHERE agendamento_id = %s
            """,
            [
                (
                    escolher(self.rng, dom.MOTIVOS_CANCELAMENTO_AGENDA),
                    self.agora,
                    self.agora,
                    linha[0],
                )
                for linha in alvos
            ],
        )

    def realizar_consultas(self) -> int:
        """Fecha os slots do dia: realizado, falta, ou cancelado na última hora."""
        do_dia = self._consultar(
            """
            SELECT agendamento_id, paciente_id, medico_id, unidade_id, especialidade_id,
                   data_hora_agendada
              FROM erp.agendamentos
             WHERE status_agendamento IN ('Agendado', 'Confirmado')
               AND data_hora_agendada::date <= %s
            """,
            (self.dia,),
        )

        realizados, faltas, cancelados = [], [], []
        consultas, prontuarios = [], []

        for (
            agendamento_id,
            paciente_id,
            medico_id,
            unidade_id,
            especialidade_id,
            data_agendada,
        ) in do_dia:
            sorteio = self.rng.random()
            if sorteio < self.prob.agendamento_realizado:
                realizados.append((self.agora, agendamento_id))
                duracao = self.rng.randint(15, 70)
                consultas.append(
                    (
                        agendamento_id,
                        paciente_id,
                        medico_id,
                        unidade_id,
                        data_agendada,
                        data_agendada + timedelta(minutes=duracao),
                        self.rng.randint(0, 75),
                        sorteia_bool(self.rng, 0.14),
                        self.agora,
                        self.agora,
                    )
                )
                if sorteia_bool(self.rng, self.prob.consulta_gera_prontuario):
                    prontuarios.append(
                        (
                            agendamento_id,
                            paciente_id,
                            medico_id,
                            especialidade_id,
                            data_agendada,
                            sorteia_bool(self.rng, self.prob.prontuario_gera_indicacao),
                            self.agora,
                        )
                    )
            elif (
                sorteio < self.prob.agendamento_realizado + self.prob.agendamento_falta
            ):
                faltas.append((self.agora, agendamento_id))
            elif sorteio < (
                self.prob.agendamento_realizado
                + self.prob.agendamento_falta
                + self.prob.agendamento_cancelado
            ):
                cancelados.append(
                    (
                        escolher(self.rng, dom.MOTIVOS_CANCELAMENTO_AGENDA),
                        self.agora,
                        self.agora,
                        agendamento_id,
                    )
                )
            # O restante fica pendente e é resolvido num dia seguinte.

        for status, lote in (("Realizado", realizados), ("Falta", faltas)):
            self._executar_lote(
                f"""
                UPDATE erp.agendamentos
                   SET status_agendamento = '{status}', atualizado_em = %s
                 WHERE agendamento_id = %s
                """,
                lote,
            )
        self._executar_lote(
            """
            UPDATE erp.agendamentos
               SET status_agendamento = 'Cancelado', motivo_cancelamento = %s,
                   data_cancelamento = %s, atualizado_em = %s
             WHERE agendamento_id = %s
            """,
            cancelados,
        )

        total = self._executar_lote(
            """
            INSERT INTO erp.consultas
                (agendamento_id, paciente_id, medico_id, unidade_id, data_hora_inicio,
                 data_hora_fim, tempo_espera_min, houve_encaminhamento,
                 criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            consultas,
        )

        # O prontuário se pendura na consulta recém-criada — resolvido por
        # subquery para não precisar do RETURNING de cada INSERT anterior.
        self._executar_lote(
            """
            INSERT INTO erp.prontuarios
                (consulta_id, paciente_id, medico_id, especialidade_id, data_registro,
                 queixa_principal, conduta, gerou_indicacao, criado_em, atualizado_em)
            SELECT c.consulta_id, %s, %s, %s, %s, %s, %s, %s, %s, %s
              FROM erp.consultas c
             WHERE c.agendamento_id = %s
            """,
            [
                (
                    paciente_id,
                    medico_id,
                    especialidade_id,
                    data_agendada,
                    escolher(
                        self.rng,
                        dom.QUEIXAS_POR_AREA[
                            self.referencias["area_especialidade"][especialidade_id]
                        ],
                    ),
                    (
                        escolher(self.rng, dom.CONDUTAS)
                        if sorteia_bool(self.rng, 0.88)
                        else None
                    ),
                    indicou,
                    self.agora,
                    self.agora,
                    agendamento_id,
                )
                for (
                    agendamento_id,
                    paciente_id,
                    medico_id,
                    especialidade_id,
                    data_agendada,
                    indicou,
                    _,
                ) in prontuarios
            ],
        )
        return total

    # =========================================================================
    # Comercial
    # =========================================================================
    def emitir_orcamentos(self) -> int:
        """Emite orçamentos a partir de prontuários recentes que indicaram procedimento."""
        candidatos = self._consultar(
            """
            SELECT p.prontuario_id, p.paciente_id, p.especialidade_id, p.medico_id,
                   c.unidade_id
              FROM erp.prontuarios p
              JOIN erp.consultas c ON c.consulta_id = p.consulta_id
             WHERE p.gerou_indicacao
               AND p.data_registro >= %s
               -- Estritamente ANTERIOR ao momento da emissão: o prontuário de
               -- hoje às 18h não pode originar um orçamento emitido às 13h.
               AND p.data_registro < %s
               AND NOT EXISTS (
                   SELECT 1 FROM erp.prontuario_orcamento po
                    WHERE po.prontuario_id = p.prontuario_id
               )
             ORDER BY random()
             LIMIT %s
            """,
            (self.dia - timedelta(days=20), self.agora, self._quantidade(110)),
        )

        total = 0
        proximo = self._ultimo_id("orcamentos", "orcamento_id")
        with self.conexao.cursor() as cursor:
            for (
                prontuario_id,
                paciente_id,
                especialidade_id,
                medico_id,
                unidade_id,
            ) in candidatos:
                comerciais = self.referencias["comerciais"].get(unidade_id)
                catalogo = self.referencias["procedimentos"].get(especialidade_id)
                if not comerciais or not catalogo:
                    continue

                itens = []
                valor_bruto = Decimal("0.00")
                for item_seq in range(1, self.rng.randint(1, 4) + 1):
                    procedimento_id, valor_tabela = escolher(self.rng, catalogo)
                    quantidade = escolher(self.rng, [1, 2, 3], [0.72, 0.20, 0.08])
                    unitario = dinheiro(
                        valor_tabela * Decimal(str(self.rng.uniform(0.92, 1.12)))
                    )
                    total_item = dinheiro(unitario * quantidade)
                    valor_bruto += total_item
                    itens.append(
                        (
                            item_seq,
                            procedimento_id,
                            medico_id,
                            quantidade,
                            unitario,
                            total_item,
                        )
                    )

                valor_bruto = dinheiro(valor_bruto)
                percentual = (
                    dinheiro(self.rng.uniform(3, 25))
                    if sorteia_bool(self.rng, self.prob.orcamento_com_desconto)
                    else Decimal("0.00")
                )
                desconto = dinheiro(valor_bruto * percentual / Decimal("100"))
                liquido = dinheiro(valor_bruto - desconto)

                cursor.execute(
                    """
                    INSERT INTO erp.orcamentos
                        (numero_orcamento, paciente_id, unidade_id, consultor_id,
                         medico_responsavel_id, oportunidade_id, data_emissao, data_validade,
                         valor_bruto, valor_desconto, valor_liquido, percentual_desconto,
                         status_orcamento, criado_em, atualizado_em)
                    VALUES (%s, %s, %s, %s, %s,
                            (SELECT o.oportunidade_id FROM erp.oportunidades o
                              WHERE o.paciente_id = %s AND o.status_oportunidade = 'Aberta'
                              ORDER BY o.data_abertura DESC LIMIT 1),
                            %s, %s, %s, %s, %s, %s, 'Enviado', %s, %s)
                    RETURNING orcamento_id
                    """,
                    (
                        f"{self.referencias['codigo_unidade'][unidade_id]}-{proximo + total + 1:07d}",
                        paciente_id,
                        unidade_id,
                        escolher(self.rng, comerciais),
                        medico_id,
                        paciente_id,
                        self.agora,
                        self.dia + timedelta(days=30),
                        valor_bruto,
                        desconto,
                        liquido,
                        percentual,
                        self.agora,
                        self.agora,
                    ),
                )
                orcamento_id = cursor.fetchone()[0]

                cursor.executemany(
                    """
                    INSERT INTO erp.orcamento_itens
                        (orcamento_id, item_seq, procedimento_id, medico_executor_id,
                         quantidade, valor_unitario, valor_total_item, criado_em, atualizado_em)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [(orcamento_id, *item, self.agora, self.agora) for item in itens],
                )
                cursor.execute(
                    """
                    INSERT INTO erp.prontuario_orcamento
                        (prontuario_id, orcamento_id, percentual_participacao, criado_em)
                    VALUES (%s, %s, 100.00, %s)
                    """,
                    (prontuario_id, orcamento_id, self.agora),
                )
                cursor.executemany(
                    """
                    INSERT INTO erp.orcamento_historico_status
                        (orcamento_id, status_anterior, status_novo, data_mudanca, criado_em)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [
                        (orcamento_id, None, "Em elaboração", self.agora, self.agora),
                        (
                            orcamento_id,
                            "Em elaboração",
                            "Enviado",
                            self.agora,
                            self.agora,
                        ),
                    ],
                )
                total += 1

        return total

    def decidir_orcamentos(self) -> int:
        """Aprova, recusa ou expira orçamentos enviados — e cria as parcelas."""
        pendentes = self._consultar(
            """
            SELECT orcamento_id, valor_liquido, consultor_id, data_validade
              FROM erp.orcamentos
             WHERE status_orcamento = 'Enviado'
               AND data_emissao <= %s
             ORDER BY random()
             LIMIT %s
            """,
            (self.agora - timedelta(days=1), self._quantidade(95)),
        )

        total = 0
        with self.conexao.cursor() as cursor:
            for orcamento_id, valor_liquido, consultor_id, validade in pendentes:
                if validade < self.dia:
                    novo_status = "Expirado"
                else:
                    novo_status = escolher(
                        self.rng,
                        ["Aprovado", "Recusado", "Cancelado"],
                        [0.46, 0.46, 0.08],
                    )

                if novo_status == "Aprovado":
                    quantidade = escolher(
                        self.rng,
                        self.prob.opcoes_parcelamento,
                        self.prob.peso_parcelamento,
                    )
                    forma = escolher(
                        self.rng, dom.FORMAS_PAGAMENTO, dom.PESO_FORMA_PAGAMENTO
                    )
                    cursor.execute(
                        """
                        UPDATE erp.orcamentos
                           SET status_orcamento = 'Aprovado', data_aprovacao = %s,
                               qtd_parcelas = %s, forma_pagamento = %s, atualizado_em = %s
                         WHERE orcamento_id = %s
                        """,
                        (self.agora, quantidade, forma, self.agora, orcamento_id),
                    )

                    base = dinheiro(valor_liquido / quantidade)
                    valores = [base] * (quantidade - 1)
                    valores.append(dinheiro(valor_liquido - base * (quantidade - 1)))
                    cursor.executemany(
                        """
                        INSERT INTO erp.parcelas
                            (orcamento_id, numero_parcela, valor_parcela, data_vencimento,
                             status_parcela, forma_pagamento_prevista, criado_em, atualizado_em)
                        VALUES (%s, %s, %s, %s, 'Aberta', %s, %s, %s)
                        """,
                        [
                            (
                                orcamento_id,
                                numero,
                                valor,
                                self.dia + timedelta(days=30 * numero),
                                forma,
                                self.agora,
                                self.agora,
                            )
                            for numero, valor in enumerate(valores, start=1)
                        ],
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE erp.orcamentos
                           SET status_orcamento = %s, motivo_recusa = %s, atualizado_em = %s
                         WHERE orcamento_id = %s
                        """,
                        (
                            novo_status,
                            (
                                escolher(self.rng, dom.MOTIVOS_RECUSA_ORCAMENTO)
                                if novo_status == "Recusado"
                                else None
                            ),
                            self.agora,
                            orcamento_id,
                        ),
                    )

                cursor.execute(
                    """
                    INSERT INTO erp.orcamento_historico_status
                        (orcamento_id, status_anterior, status_novo, funcionario_id,
                         data_mudanca, criado_em)
                    VALUES (%s, 'Enviado', %s, %s, %s, %s)
                    """,
                    (orcamento_id, novo_status, consultor_id, self.agora, self.agora),
                )
                total += 1

        return total

    # =========================================================================
    # Financeiro
    # =========================================================================
    def virar_vencimentos(self) -> int:
        """UPDATE em lote disparado pela passagem do tempo, não por um evento.

        Nenhum usuário fez nada: a parcela venceu sozinha. Isso produz picos de
        milhares de linhas alteradas num único dia — o pipeline precisa aguentar
        o pior dia, não o dia médio.
        """
        return self._executar(
            """
            UPDATE erp.parcelas
               SET status_parcela = 'Vencida', atualizado_em = %s
             WHERE status_parcela = 'Aberta'
               AND data_vencimento < %s
            """,
            (self.agora, self.dia),
        )

    def receber_pagamentos(self) -> int:
        """Recebimentos do dia, incluindo atrasados e pagamentos parciais."""
        abertas = self._consultar(
            """
            SELECT p.orcamento_id, p.numero_parcela, p.valor_parcela, p.valor_pago,
                   o.unidade_id
              FROM erp.parcelas p
              JOIN erp.orcamentos o ON o.orcamento_id = p.orcamento_id
             WHERE p.status_parcela IN ('Aberta', 'Vencida', 'Parcial')
               AND p.data_vencimento <= %s
             ORDER BY random()
             LIMIT %s
            """,
            (self.dia + timedelta(days=3), self._quantidade(420)),
        )

        pagamentos, atualizacoes = [], []
        for orcamento_id, numero, valor_parcela, ja_pago, unidade_id in abertas:
            restante = valor_parcela - ja_pago
            if restante <= 0:
                continue

            parcial = sorteia_bool(self.rng, 0.12)
            valor = (
                dinheiro(restante * Decimal(str(self.rng.uniform(0.3, 0.7))))
                if parcial
                else dinheiro(restante)
            )
            momento = self._momento()
            forma = escolher(self.rng, dom.FORMAS_PAGAMENTO, dom.PESO_FORMA_PAGAMENTO)

            pagamentos.append(
                (
                    orcamento_id,
                    numero,
                    # Recebimento pode acontecer em outra unidade da rede.
                    (
                        unidade_id
                        if sorteia_bool(self.rng, 0.88)
                        else escolher(self.rng, self.referencias["unidades"])
                    ),
                    valor,
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
                    momento,
                )
            )
            atualizacoes.append(
                (
                    "Parcial" if parcial else "Paga",
                    valor,
                    self.dia,
                    self.agora,
                    orcamento_id,
                    numero,
                )
            )

        total = self._executar_lote(
            """
            INSERT INTO erp.pagamentos
                (orcamento_id, numero_parcela, unidade_recebimento_id, valor_pago,
                 data_pagamento, forma_pagamento, bandeira_cartao, codigo_autorizacao, criado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            pagamentos,
        )
        self._executar_lote(
            """
            UPDATE erp.parcelas
               SET status_parcela = %s,
                   valor_pago     = valor_pago + %s,
                   data_ultimo_pagamento = %s,
                   atualizado_em  = %s
             WHERE orcamento_id = %s AND numero_parcela = %s
            """,
            atualizacoes,
        )
        return total
