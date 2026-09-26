-- 385_novidade_assinatura_da_clinica.sql
-- O aviso da assinatura da clínica (finance/clinica_assinaturas.py, /painel/clinica/assinaturas),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção ativa a assinatura;
-- o plano é do dono).
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-assinatura', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Assinatura da clínica: receita que entra todo mês',
 'Cadastre planos com mensalidade e benefícios (sessão inclusa, desconto, prioridade nas vagas); a mensalidade vira conta a receber sozinha e o benefício vale na agenda, no plano de tratamento e na fila de vagas.',
 '/painel/clinica/assinaturas',
 $txt$A clínica agora pode vender assinatura, e o faturamento deixa de depender só do movimento da agenda.

O PLANO (dono e gestor cadastram)

- Nome, mensalidade e dia de cobrança.
- O que ele dá: sessões inclusas por mês de um atendimento do catálogo (ex.: 1 limpeza de pele), desconto em procedimentos, desconto em produtos e prioridade na fila de vagas liberadas.
- Mudar o preço vale para quem assinar daqui pra frente; quem já assinou fica com o que combinou.

O ASSINANTE (a recepção ativa)

- Ative no fim do atendimento. A tela mostra quem já veio 3 vezes ou mais no ano: é pra quem vale oferecer.
- A mensalidade do mês vira uma conta a receber no Financeiro, no dia de cobrança do plano.

O BENEFÍCIO VALE SOZINHO

- Finalizou o atendimento incluso: a sessão do mês é usada e não baixa do pacote.
- No plano de tratamento, o desconto do assinante já vem preenchido e não pede aprovação do dono.
- Na vaga liberada, o assinante é chamado primeiro.

Por enquanto a cobrança não é automática no cartão: ela entra quando a clínica tiver a própria conta no Asaas.

Fica em Agenda › Pacotes › Assinaturas.$txt$,
 timestamptz '2026-09-27 00:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-assinatura';
