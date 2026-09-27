-- 443_novidade_certificado.sql
-- O aviso da fase 4 do prontuário (certificado digital, migração 442), seguindo a seção 5
-- do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (o profissional de saúde pode ter
-- qualquer um dos três papéis; só quem lê o prontuário vê a página do certificado).
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-certificado', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Evolução e receita assinadas com o certificado digital',
 'Cada profissional liga o próprio e-CPF (arquivo A1 ou nuvem) e o Zaq assina as evoluções e os documentos no padrão da ICP-Brasil. A receita sai com o QR para a farmácia conferir no validador do governo.',
 '/painel/clinica/certificado',
 $txt$O prontuário agora assina com o certificado digital (ICP-Brasil).

COMO LIGAR (cada profissional, no próprio login)

- Prontuário › "ligar o certificado", ou direto em /painel/clinica/certificado.
- Certificado em arquivo (A1): envie o .pfx com a senha. O arquivo fica cifrado e a senha não fica guardada: uma vez por dia você digita a senha e o Zaq assina ao finalizar, sem perguntar de novo, até o fim do dia e só naquele aparelho.
- Certificado em nuvem: escolha o provedor. Ao terminar, "Assinar as N no aplicativo" pede uma autorização só, no celular, para todas.

O QUE MUDA

- Evolução e documento continuam assinados na hora como hoje; o certificado entra por cima e aparece a etiqueta "certificado". O que ainda não tem aparece como "falta o certificado".
- Receita, atestado, pedido de exame e laudo saem em PDF assinado, sem a linha para assinar à mão, com o QR e o código para a farmácia conferir em validar.iti.gov.br.
- A evolução assinada tem o "PDF assinado", para entregar ou conferir.
- Sem certificado, nada muda: a assinatura simples continua.$txt$,
 timestamptz '2026-09-28 01:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-certificado';
