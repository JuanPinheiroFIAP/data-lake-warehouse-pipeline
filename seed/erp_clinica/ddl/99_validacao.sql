-- =============================================================================
-- 99_validacao.sql — Prova que as invariantes de negócio se sustentam
-- =============================================================================
-- Não faz parte do schema. É o conjunto de perguntas que eu faria a qualquer
-- fonte antes de escrever a primeira linha de pipeline em cima dela.
--
-- Toda linha do resultado com `violacoes > 0` é bug do gerador, não "dado
-- sujo aceitável". As inconsistências deste cenário são todas intencionais e
-- estão documentadas nos COMMENT das tabelas — o que estiver aqui é regra dura.
--
--   psql ... -f ddl/99_validacao.sql
-- =============================================================================

WITH checagens AS (

    -- Financeiro: a soma das parcelas tem que reproduzir o valor do orçamento.
    SELECT 'parcelas somam o valor do orçamento' AS regra, count(*) AS violacoes
      FROM (
            SELECT o.orcamento_id
              FROM erp.orcamentos o
              JOIN erp.parcelas p ON p.orcamento_id = o.orcamento_id
             WHERE o.status_orcamento = 'Aprovado'
             GROUP BY o.orcamento_id, o.valor_liquido
            HAVING sum(p.valor_parcela) <> o.valor_liquido
      ) AS falhas

    UNION ALL
    -- O ERP guarda qtd_parcelas redundante; tem que bater com a contagem real.
    SELECT 'qtd_parcelas bate com as parcelas geradas', count(*)
      FROM (
            SELECT o.orcamento_id
              FROM erp.orcamentos o
              JOIN erp.parcelas p ON p.orcamento_id = o.orcamento_id
             WHERE o.status_orcamento = 'Aprovado'
             GROUP BY o.orcamento_id, o.qtd_parcelas
            HAVING count(*) <> o.qtd_parcelas
      ) AS falhas

    UNION ALL
    -- Bridge: o fator de alocação tem que somar exatamente 100 por orçamento.
    SELECT 'fator de alocação da bridge soma 100', count(*)
      FROM (
            SELECT orcamento_id
              FROM erp.prontuario_orcamento
             GROUP BY orcamento_id
            HAVING sum(percentual_participacao) <> 100.00
      ) AS falhas

    UNION ALL
    -- Identidade: o prontuário que originou o orçamento é do mesmo paciente.
    SELECT 'bridge não cruza pacientes diferentes', count(*)
      FROM erp.prontuario_orcamento po
      JOIN erp.prontuarios pr ON pr.prontuario_id = po.prontuario_id
      JOIN erp.orcamentos  o  ON o.orcamento_id  = po.orcamento_id
     WHERE pr.paciente_id <> o.paciente_id

    UNION ALL
    -- Caixa: pagamento não estornado tem que reproduzir o valor_pago da parcela.
    SELECT 'pagamentos reconciliam com valor_pago da parcela', count(*)
      FROM (
            SELECT p.orcamento_id, p.numero_parcela
              FROM erp.parcelas p
              LEFT JOIN erp.pagamentos g
                     ON g.orcamento_id   = p.orcamento_id
                    AND g.numero_parcela = p.numero_parcela
                    AND NOT g.estornado
             GROUP BY p.orcamento_id, p.numero_parcela, p.valor_pago
            HAVING COALESCE(sum(g.valor_pago), 0) <> p.valor_pago
      ) AS falhas

    UNION ALL
    -- Tempo: ninguém agenda antes de existir no cadastro.
    SELECT 'agendamento não antecede o cadastro do paciente', count(*)
      FROM erp.agendamentos a
      JOIN erp.pacientes p ON p.paciente_id = a.paciente_id
     WHERE a.data_hora_agendada::date < p.data_primeiro_contato

    UNION ALL
    -- Tempo: o orçamento não pode ser emitido antes do prontuário que o originou.
    SELECT 'orçamento não antecede o prontuário de origem', count(*)
      FROM erp.prontuario_orcamento po
      JOIN erp.prontuarios pr ON pr.prontuario_id = po.prontuario_id
      JOIN erp.orcamentos  o  ON o.orcamento_id  = po.orcamento_id
     WHERE o.data_emissao < pr.data_registro

    UNION ALL
    -- Cardinalidade: consulta existe só onde o agendamento foi realizado.
    SELECT 'consulta só existe para agendamento realizado', count(*)
      FROM erp.consultas c
      JOIN erp.agendamentos a ON a.agendamento_id = c.agendamento_id
     WHERE a.status_agendamento <> 'Realizado'

    UNION ALL
    -- Denormalização: paciente da consulta tem que bater com o do agendamento.
    SELECT 'consulta e agendamento apontam o mesmo paciente', count(*)
      FROM erp.consultas c
      JOIN erp.agendamentos a ON a.agendamento_id = c.agendamento_id
     WHERE c.paciente_id <> a.paciente_id OR c.medico_id <> a.medico_id

    UNION ALL
    -- Itens: a soma dos itens tem que reproduzir o valor bruto do orçamento.
    SELECT 'itens somam o valor bruto do orçamento', count(*)
      FROM (
            SELECT o.orcamento_id
              FROM erp.orcamentos o
              JOIN erp.orcamento_itens i ON i.orcamento_id = o.orcamento_id
             GROUP BY o.orcamento_id, o.valor_bruto
            HAVING sum(i.valor_total_item) <> o.valor_bruto
      ) AS falhas

    UNION ALL
    -- Reagendamento: o slot novo é sempre posterior ao que ele substituiu.
    SELECT 'reagendamento é posterior ao slot original', count(*)
      FROM erp.agendamentos novo
      JOIN erp.agendamentos antigo ON antigo.agendamento_id = novo.agendamento_origem_id
     WHERE novo.data_hora_agendada <= antigo.data_hora_agendada

    UNION ALL
    -- Funil: todo evento de histórico pertence a uma oportunidade existente e
    -- o primeiro evento é o único que pode ter etapa de origem nula.
    SELECT 'só o primeiro evento do funil tem origem nula', count(*)
      FROM (
            SELECT oportunidade_id,
                   row_number() OVER (PARTITION BY oportunidade_id ORDER BY data_mudanca) AS ordem,
                   etapa_origem_id
              FROM erp.oportunidade_historico_etapa
      ) AS eventos
     WHERE (ordem = 1) <> (etapa_origem_id IS NULL)

    UNION ALL
    -- Vigência: nenhum médico tem dois vínculos abertos na mesma unidade.
    SELECT 'vínculo médico/unidade sem duplicidade em aberto', count(*)
      FROM (
            SELECT medico_id, unidade_id
              FROM erp.medico_unidade
             WHERE data_fim IS NULL
             GROUP BY medico_id, unidade_id
            HAVING count(*) > 1
      ) AS falhas
)
SELECT regra,
       violacoes,
       CASE WHEN violacoes = 0 THEN 'OK' ELSE 'FALHOU' END AS resultado
  FROM checagens
 ORDER BY violacoes DESC, regra;
