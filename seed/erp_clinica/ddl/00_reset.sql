-- =============================================================================
-- 00_reset.sql — DESTRUTIVO. Derruba o schema inteiro do ERP.
-- =============================================================================
-- Usado apenas para recriar o cenário do zero durante o desenvolvimento do
-- pipeline. Nunca deve ser executado contra um banco que já foi ingerido sem
-- que o Data Lake seja limpo junto — senão o bronze fica com IDs órfãos de uma
-- geração anterior.
-- =============================================================================

DROP SCHEMA IF EXISTS erp CASCADE;
