-- 011_qr_qualidade_imagem.sql
-- Auditoria de qualidade: registra dimensoes e tamanho das fotos/PDFs que chegam
-- Ajuda a diagnosticar se o problema do QR é compressão do app ou algo mais

alter table qr_leituras add column if not exists img_largura int;
alter table qr_leituras add column if not exists img_altura int;
alter table qr_leituras add column if not exists img_bytes int;
