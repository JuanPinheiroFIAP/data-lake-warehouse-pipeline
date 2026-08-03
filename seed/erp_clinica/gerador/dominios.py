"""Listas de domínio do cenário fictício.

IMPORTANTE — nada aqui referencia empresa, operadora, clínica ou sistema real.
Os nomes de convênio e de unidade são compostos inventados. Antes de publicar o
repositório, vale conferir se nenhum deles colidiu por acaso com marca existente.

Estas listas ficam em Python (e não como INSERT no DDL) porque o gerador precisa
conhecer os IDs para amarrar as FKs em memória. Ler de volta do banco a cada
execução seria um round-trip desnecessário num dado que é constante por definição.
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# Unidades — 15 clínicas, distribuídas pelas cinco regiões.
# (codigo, nome, cidade, uf, regiao, qtd_consultorios)
# -----------------------------------------------------------------------------
UNIDADES: list[tuple[str, str, str, str, str, int]] = [
    ("SP01", "Clínica Paulista", "São Paulo", "SP", "Sudeste", 18),
    ("SP02", "Clínica Morumbi", "São Paulo", "SP", "Sudeste", 12),
    ("SP03", "Clínica Campinas", "Campinas", "SP", "Sudeste", 9),
    ("RJ01", "Clínica Botafogo", "Rio de Janeiro", "RJ", "Sudeste", 14),
    ("RJ02", "Clínica Barra", "Rio de Janeiro", "RJ", "Sudeste", 10),
    ("MG01", "Clínica Savassi", "Belo Horizonte", "MG", "Sudeste", 11),
    ("ES01", "Clínica Praia do Canto", "Vitória", "ES", "Sudeste", 6),
    ("PR01", "Clínica Batel", "Curitiba", "PR", "Sul", 10),
    ("RS01", "Clínica Moinhos", "Porto Alegre", "RS", "Sul", 11),
    ("SC01", "Clínica Beira-Mar", "Florianópolis", "SC", "Sul", 7),
    ("DF01", "Clínica Asa Sul", "Brasília", "DF", "Centro-Oeste", 13),
    ("GO01", "Clínica Marista", "Goiânia", "GO", "Centro-Oeste", 8),
    ("BA01", "Clínica Itaigara", "Salvador", "BA", "Nordeste", 9),
    ("PE01", "Clínica Boa Viagem", "Recife", "PE", "Nordeste", 8),
    ("CE01", "Clínica Aldeota", "Fortaleza", "CE", "Nordeste", 7),
]

# -----------------------------------------------------------------------------
# Especialidades — (codigo, nome, area)
# -----------------------------------------------------------------------------
ESPECIALIDADES: list[tuple[str, str, str]] = [
    ("CARD", "Cardiologia", "Clínica"),
    ("DERM", "Dermatologia", "Clínica"),
    ("ORTO", "Ortopedia", "Cirúrgica"),
    ("GAST", "Gastroenterologia", "Clínica"),
    ("GINE", "Ginecologia", "Clínica"),
    ("URO", "Urologia", "Cirúrgica"),
    ("OFT", "Oftalmologia", "Cirúrgica"),
    ("OTOR", "Otorrinolaringologia", "Cirúrgica"),
    ("ENDO", "Endocrinologia", "Clínica"),
    ("NEUR", "Neurologia", "Clínica"),
    ("PNEU", "Pneumologia", "Clínica"),
    ("PED", "Pediatria", "Clínica"),
    ("PSIQ", "Psiquiatria", "Clínica"),
    ("REUM", "Reumatologia", "Clínica"),
    ("NUTR", "Nutrologia", "Clínica"),
    ("CPLA", "Cirurgia Plástica", "Estética"),
    ("DEST", "Dermatologia Estética", "Estética"),
    ("RADI", "Radiologia e Imagem", "Diagnóstico"),
]

# -----------------------------------------------------------------------------
# Etapas do funil — (ordem, nome, tipo, probabilidade)
# A ordem é o que permite distinguir avanço de retrocesso no histórico.
# -----------------------------------------------------------------------------
ETAPAS_FUNIL: list[tuple[int, str, str, float]] = [
    (1, "Qualificação", "Aberta", 10.0),
    (2, "Agendamento", "Aberta", 25.0),
    (3, "Avaliação clínica", "Aberta", 45.0),
    (4, "Proposta", "Aberta", 65.0),
    (5, "Negociação", "Aberta", 82.0),
    (6, "Fechado ganho", "Ganha", 100.0),
    (7, "Fechado perdido", "Perdida", 0.0),
]

ETAPA_GANHA = 6
ETAPA_PERDIDA = 7
ETAPAS_ABERTAS = (1, 2, 3, 4, 5)

# -----------------------------------------------------------------------------
# Convênios — nomes inventados. (nome, tipo, desconto, prazo_repasse_dias)
# -----------------------------------------------------------------------------
CONVENIOS: list[tuple[str, str, float, int]] = [
    ("Vitalis Saúde", "Saúde", 22.0, 45),
    ("PlenaMed", "Saúde", 18.5, 30),
    ("SanusVida", "Saúde", 25.0, 60),
    ("Prisma Assistencial", "Saúde", 15.0, 45),
    ("NexoMed", "Saúde", 20.0, 30),
    ("AmparoVida", "Saúde", 27.5, 75),
    ("CliniPlus Nacional", "Saúde", 12.0, 30),
    ("Integra Saúde", "Saúde", 19.0, 45),
    ("OdontoPrisma", "Odontológico", 30.0, 60),
    ("SorrisoNexo", "Odontológico", 28.0, 45),
    ("BemEstar Corporativo", "Corporativo", 16.0, 30),
    ("Aliança Empresarial", "Corporativo", 14.5, 40),
]

# -----------------------------------------------------------------------------
# Cargos por departamento
# -----------------------------------------------------------------------------
CARGOS_POR_DEPARTAMENTO: dict[str, list[str]] = {
    "Comercial": [
        "Consultor Comercial",
        "Consultor Comercial Sênior",
        "Coordenador Comercial",
        "Gerente Comercial",
    ],
    "Atendimento": [
        "Recepcionista",
        "Auxiliar de Atendimento",
        "Supervisor de Atendimento",
    ],
    "Financeiro": [
        "Analista Financeiro",
        "Auxiliar de Cobrança",
        "Coordenador Financeiro",
    ],
    "Marketing": [
        "Analista de Marketing",
        "Gestor de Tráfego",
        "Coordenador de Marketing",
    ],
    "Operações": [
        "Auxiliar de Enfermagem",
        "Técnico de Enfermagem",
        "Coordenador de Enfermagem",
        "Auxiliar Administrativo",
    ],
    "Gestão": ["Gerente de Unidade", "Diretor Regional"],
}

# Peso de cada departamento no quadro. Comercial é o maior porque é o
# departamento que gera as linhas de CRM — precisa de gente para o volume fechar.
PESO_DEPARTAMENTO: dict[str, float] = {
    "Comercial": 0.34,
    "Atendimento": 0.26,
    "Operações": 0.22,
    "Financeiro": 0.09,
    "Marketing": 0.06,
    "Gestão": 0.03,
}

# -----------------------------------------------------------------------------
# Marketing
# -----------------------------------------------------------------------------
CANAIS_CAMPANHA = [
    "Google Ads",
    "Meta Ads",
    "TikTok Ads",
    "E-mail",
    "Outdoor",
    "Rádio",
    "Indicação",
    "Parceria",
]
PESO_CANAL_CAMPANHA = [0.26, 0.24, 0.10, 0.09, 0.08, 0.05, 0.11, 0.07]

TIPOS_CAMPANHA = ["Aquisição", "Retenção", "Reativação", "Institucional"]
PESO_TIPO_CAMPANHA = [0.58, 0.18, 0.16, 0.08]

TEMAS_CAMPANHA = [
    "Check-up Anual",
    "Dia da Saúde",
    "Setembro Amarelo",
    "Outubro Rosa",
    "Novembro Azul",
    "Verão Saudável",
    "Volta às Aulas",
    "Cuidar de Quem Cuida",
    "Semana do Coração",
    "Pele Protegida",
    "Visão em Dia",
    "Sorriso Renovado",
    "Movimento sem Dor",
    "Equilíbrio Hormonal",
    "Respire Melhor",
]

ORIGENS_LEAD = [
    "Formulário site",
    "WhatsApp",
    "Telefone",
    "Indicação",
    "Landing page",
    "Instagram",
    "Recepção",
    "Evento",
]
PESO_ORIGEM_LEAD = [0.22, 0.21, 0.14, 0.13, 0.11, 0.09, 0.06, 0.04]

MOTIVOS_DESQUALIFICACAO = [
    "Fora da área de cobertura",
    "Sem interesse real",
    "Contato inexistente",
    "Procurava outro serviço",
    "Já é paciente ativo",
    "Buscava apenas preço",
    "Não retornou contato",
]

MOTIVOS_PERDA_OPORTUNIDADE = [
    "Preço acima do esperado",
    "Fechou com concorrente",
    "Sem urgência",
    "Não obteve crédito",
    "Mudou de cidade",
    "Convênio não cobre",
    "Perdeu contato",
    "Adiou tratamento",
]

# -----------------------------------------------------------------------------
# Atendimento
# -----------------------------------------------------------------------------
MOTIVOS_CANCELAMENTO_AGENDA = [
    "Imprevisto do paciente",
    "Médico indisponível",
    "Reagendado a pedido",
    "Problema de saúde",
    "Conflito de horário",
    "Cancelamento por convênio",
    "Unidade fechada",
]

QUEIXAS_POR_AREA: dict[str, list[str]] = {
    "Clínica": [
        "Dor persistente há mais de duas semanas",
        "Cansaço e falta de disposição",
        "Alteração recente em exame de rotina",
        "Episódios de tontura",
        "Acompanhamento de quadro crônico",
        "Dificuldade para dormir",
        "Retorno para avaliação de tratamento",
    ],
    "Cirúrgica": [
        "Limitação de movimento após esforço",
        "Dor localizada com piora progressiva",
        "Avaliação pré-operatória",
        "Retorno pós-cirúrgico",
        "Lesão em atividade física",
        "Indicação de procedimento eletivo",
    ],
    "Estética": [
        "Insatisfação com aspecto da pele",
        "Avaliação para procedimento estético",
        "Manutenção de tratamento anterior",
        "Consulta sobre alternativas de tratamento",
    ],
    "Diagnóstico": [
        "Encaminhamento para exame de imagem",
        "Investigação de sintoma inespecífico",
        "Controle periódico de imagem",
    ],
}

CID10_EXEMPLOS = [
    "I10",
    "E11.9",
    "M54.5",
    "J45.0",
    "K21.0",
    "E78.5",
    "F41.1",
    "M17.1",
    "H52.1",
    "L20.9",
    "N39.0",
    "G43.9",
    "R51",
    "E66.9",
    "M75.1",
    "K59.0",
]

CONDUTAS = [
    "Solicitados exames complementares",
    "Prescrição medicamentosa ajustada",
    "Encaminhado para especialista",
    "Indicado procedimento eletivo",
    "Orientação e retorno em 30 dias",
    "Mantida conduta anterior",
    "Solicitada avaliação por imagem",
    "Iniciado protocolo de acompanhamento",
]

# -----------------------------------------------------------------------------
# Procedimentos — templates por área. O gerador combina cada template com as
# especialidades da área para chegar ao volume configurado.
# (nome, categoria, faixa de valor mínima, máxima, duração média em minutos)
# -----------------------------------------------------------------------------
TEMPLATES_PROCEDIMENTO: dict[str, list[tuple[str, str, int, int, int]]] = {
    "Clínica": [
        ("Consulta inicial", "Consulta", 180, 450, 40),
        ("Consulta de retorno", "Consulta", 0, 250, 30),
        ("Avaliação diagnóstica ampliada", "Consulta", 300, 700, 60),
        ("Protocolo de acompanhamento trimestral", "Terapêutico", 900, 2400, 45),
        ("Sessão de terapia assistida", "Terapêutico", 220, 600, 50),
        ("Programa preventivo anual", "Preventivo", 1200, 3200, 60),
        ("Teste funcional", "Exame", 160, 540, 35),
    ],
    "Cirúrgica": [
        ("Consulta pré-operatória", "Consulta", 250, 600, 40),
        ("Procedimento ambulatorial", "Cirúrgico", 1800, 6500, 90),
        ("Procedimento cirúrgico de médio porte", "Cirúrgico", 6000, 22000, 150),
        ("Procedimento cirúrgico de alta complexidade", "Cirúrgico", 18000, 58000, 240),
        ("Infiltração guiada", "Terapêutico", 900, 2800, 45),
        ("Revisão pós-operatória", "Consulta", 0, 300, 25),
    ],
    "Estética": [
        ("Sessão de toxina botulínica", "Estético", 1200, 3400, 50),
        ("Preenchimento facial", "Estético", 1800, 5200, 60),
        ("Protocolo de bioestimulação", "Estético", 2400, 7800, 70),
        ("Peeling profissional", "Estético", 600, 1900, 45),
        ("Cirurgia plástica reparadora", "Cirúrgico", 12000, 46000, 210),
        ("Avaliação estética personalizada", "Consulta", 200, 650, 40),
    ],
    "Diagnóstico": [
        ("Exame de imagem simples", "Exame", 180, 620, 25),
        ("Exame de imagem contrastado", "Exame", 700, 2400, 50),
        ("Painel laboratorial completo", "Exame", 320, 1100, 20),
        ("Laudo especializado", "Exame", 150, 480, 30),
    ],
}

BANDEIRAS_CARTAO = ["Visa", "Mastercard", "Elo", "American Express", "Hipercard"]

FORMAS_PAGAMENTO = [
    "Dinheiro",
    "PIX",
    "Débito",
    "Crédito",
    "Boleto",
    "Convênio",
    "Financiamento",
]
PESO_FORMA_PAGAMENTO = [0.05, 0.22, 0.11, 0.34, 0.13, 0.09, 0.06]

MOTIVOS_RECUSA_ORCAMENTO = [
    "Valor acima do orçamento",
    "Vai pensar",
    "Optou por convênio",
    "Buscou segunda opinião",
    "Sem condição financeira no momento",
    "Discordou da indicação",
    "Prazo de execução inadequado",
]

TIPOS_ATIVIDADE = [
    "Ligação",
    "WhatsApp",
    "E-mail",
    "Reunião",
    "Visita",
    "Follow-up",
    "Proposta enviada",
]
PESO_TIPO_ATIVIDADE = [0.24, 0.31, 0.13, 0.08, 0.04, 0.14, 0.06]

RESULTADOS_ATIVIDADE = [
    "Sucesso",
    "Sem resposta",
    "Reagendado",
    "Recusado",
    "Caixa postal",
]
PESO_RESULTADO_ATIVIDADE = [0.42, 0.26, 0.13, 0.09, 0.10]
