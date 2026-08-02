-- =============================================================================
-- 01_schema.sql — Schema e tipos de domínio do ERP Rede Clínica
-- =============================================================================
-- Decisão: schema dedicado `erp` em vez de `public`.
-- Motivo: o extractor vai listar tabelas por schema. Com tudo em `public`,
-- qualquer objeto acessório (extensões, tabelas de controle) entraria na
-- varredura. Um schema nomeado deixa o "contrato de ingestão" explícito:
-- tudo que está em `erp` é fonte, o resto não é.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS erp;

-- Domínios reaproveitados. Preferidos a ENUM nativo porque ENUM em PostgreSQL
-- exige ALTER TYPE para ganhar valor novo, e a maioria dos drivers de captura
-- (Debezium, psycopg) devolve ENUM como texto de qualquer forma. Domínio com
-- CHECK dá a mesma garantia de integridade sem travar evolução do cadastro.
CREATE DOMAIN erp.uf_br AS CHAR(2)
    CHECK (VALUE ~ '^[A-Z]{2}$');

CREATE DOMAIN erp.percentual AS NUMERIC(5, 2)
    CHECK (VALUE >= 0 AND VALUE <= 100);

CREATE DOMAIN erp.valor_monetario AS NUMERIC(14, 2)
    CHECK (VALUE >= 0);
