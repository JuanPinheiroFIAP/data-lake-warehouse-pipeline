-- =============================================================================
-- 07_indices.sql — Índices
-- =============================================================================
-- Dois grupos, com propósitos diferentes:
--
--   1. WATERMARK  — sustentam a extração incremental. Sem eles, todo `WHERE
--      atualizado_em > :ultimo_watermark` vira Seq Scan em tabela de centenas de
--      milhares de linhas, e a "carga incremental" fica mais cara que a full.
--
--   2. FOREIGN KEY — o PostgreSQL indexa a PK automaticamente, mas NÃO indexa
--      a coluna que referencia. Sem isso, o lado N de todo 1:N faz Seq Scan.
-- =============================================================================

-- ---------------------------------------------------------------- watermark --
-- Tabelas mutáveis: extração por atualizado_em (captura INSERT e UPDATE).
CREATE INDEX ix_pacientes_atualizado_em        ON erp.pacientes        (atualizado_em);
CREATE INDEX ix_leads_atualizado_em            ON erp.leads            (atualizado_em);
CREATE INDEX ix_oportunidades_atualizado_em    ON erp.oportunidades    (atualizado_em);
CREATE INDEX ix_agendamentos_atualizado_em     ON erp.agendamentos     (atualizado_em);
CREATE INDEX ix_consultas_atualizado_em        ON erp.consultas        (atualizado_em);
CREATE INDEX ix_prontuarios_atualizado_em      ON erp.prontuarios      (atualizado_em);
CREATE INDEX ix_orcamentos_atualizado_em       ON erp.orcamentos       (atualizado_em);
CREATE INDEX ix_orcamento_itens_atualizado_em  ON erp.orcamento_itens  (atualizado_em);
CREATE INDEX ix_parcelas_atualizado_em         ON erp.parcelas         (atualizado_em);
CREATE INDEX ix_campanhas_atualizado_em        ON erp.campanhas        (atualizado_em);

-- Tabelas append-only: extração por criado_em (só existe INSERT).
CREATE INDEX ix_atividades_crm_criado_em       ON erp.atividades_crm              (criado_em);
CREATE INDEX ix_opo_historico_criado_em        ON erp.oportunidade_historico_etapa (criado_em);
CREATE INDEX ix_orc_historico_criado_em        ON erp.orcamento_historico_status  (criado_em);
CREATE INDEX ix_pagamentos_criado_em           ON erp.pagamentos                  (criado_em);

-- -------------------------------------------------------------- foreign key --
CREATE INDEX ix_funcionarios_unidade      ON erp.funcionarios      (unidade_id);
CREATE INDEX ix_medicos_especialidade     ON erp.medicos           (especialidade_principal_id);
CREATE INDEX ix_medico_unidade_unidade    ON erp.medico_unidade    (unidade_id);
CREATE INDEX ix_leads_campanha            ON erp.leads             (campanha_id);
CREATE INDEX ix_oportunidades_lead        ON erp.oportunidades     (lead_id);
CREATE INDEX ix_oportunidades_paciente    ON erp.oportunidades     (paciente_id);
CREATE INDEX ix_oportunidades_consultor   ON erp.oportunidades     (consultor_id);
CREATE INDEX ix_opo_historico_oportunidade ON erp.oportunidade_historico_etapa (oportunidade_id);
CREATE INDEX ix_atividades_oportunidade   ON erp.atividades_crm    (oportunidade_id);
CREATE INDEX ix_atividades_lead           ON erp.atividades_crm    (lead_id);
CREATE INDEX ix_atividades_funcionario    ON erp.atividades_crm    (funcionario_id);
CREATE INDEX ix_pacientes_lead            ON erp.pacientes         (lead_id);
CREATE INDEX ix_pacientes_unidade         ON erp.pacientes         (unidade_cadastro_id);
CREATE INDEX ix_agendamentos_paciente     ON erp.agendamentos      (paciente_id);
CREATE INDEX ix_agendamentos_medico       ON erp.agendamentos      (medico_id);
CREATE INDEX ix_agendamentos_unidade_data ON erp.agendamentos      (unidade_id, data_hora_agendada);
CREATE INDEX ix_agendamentos_origem       ON erp.agendamentos      (agendamento_origem_id);
CREATE INDEX ix_consultas_paciente        ON erp.consultas         (paciente_id);
CREATE INDEX ix_consultas_medico          ON erp.consultas         (medico_id);
CREATE INDEX ix_prontuarios_paciente      ON erp.prontuarios       (paciente_id);
CREATE INDEX ix_prontuarios_medico        ON erp.prontuarios       (medico_id);
CREATE INDEX ix_orcamentos_paciente       ON erp.orcamentos        (paciente_id);
CREATE INDEX ix_orcamentos_oportunidade   ON erp.orcamentos        (oportunidade_id);
CREATE INDEX ix_orcamentos_consultor      ON erp.orcamentos        (consultor_id);
CREATE INDEX ix_orcamentos_unidade_data   ON erp.orcamentos        (unidade_id, data_emissao);
CREATE INDEX ix_orcamento_itens_proc      ON erp.orcamento_itens   (procedimento_id);
CREATE INDEX ix_bridge_orcamento          ON erp.prontuario_orcamento (orcamento_id);
CREATE INDEX ix_orc_historico_orcamento   ON erp.orcamento_historico_status (orcamento_id);
CREATE INDEX ix_parcelas_vencimento       ON erp.parcelas          (data_vencimento)
    WHERE status_parcela IN ('Aberta', 'Parcial', 'Vencida');

COMMENT ON INDEX erp.ix_parcelas_vencimento IS
    'Índice PARCIAL: a rotina de inadimplência só varre parcela em aberto. Parcela paga '
    'é a maioria da tabela e fica fora do índice — menor, mais barato de manter.';
