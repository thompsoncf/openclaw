-- 125_prospeccao_decisor_email.sql
-- E-mail PESSOAL do decisor (Credify "Pesquisa Email PF", IdConsulta 359) — vem
-- junto com o telefone na mesma consulta por CPF (ver finance/credify.py
-- decisor_com_telefone / decisor_por_lead). Não confundir com prospeccao.email,
-- que é o e-mail da EMPRESA (site/Receita).

alter table public.prospeccao add column if not exists decisor_email text;
