-- 470_novidade_clinica_funil_ate_o_retorno.sql
-- O aviso da entrega 1a do CRM da clínica (docs/mockups/clinica_crm_telas.html, seções
-- 01 e 02, aprovado em 01/10/2026), seguindo a seção 5 do CLAUDE.md.
--
-- O QUE MUDOU: o modelo de funil do ramo clínica ganhou três colunas (Consulta, Em
-- tratamento, Retorno) e nomes novos (Em conversa, Agendado, Plano ou orçamento
-- enviado, Concluído). Nenhuma conta é reescrita: as mudanças aparecem como proposta
-- na Régua, e o dono aceita. Enquanto não aceitar, a agenda, o plano e o pacote
-- seguem movendo o cartão como antes.
--
-- PÚBLICO `clinica`. PRA QUEM: dono e gestor (só eles abrem a Régua).
-- QUEM RECEBE, conferido na produção em 01/10/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Sem schema novo. Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-funil-ate-o-retorno', 'novidade', 'clinica', '{dono,gestor}',
 'O funil da clínica agora vai até o retorno',
 'Três colunas novas: Consulta (o paciente veio), Em tratamento e Retorno. O cartão passa a andar sozinho pela agenda, pelo plano e pelas sessões do pacote.',
 '/painel/prospeccao/regua',
 $txt$O funil da clínica parava em "agendado". Agora ele acompanha o paciente até o retorno.

AS COLUNAS NOVAS

- Consulta: o paciente veio (a recepção marcou Presente na agenda). O cartão fica aqui até o plano ser enviado, então dá para ver o que está esperando a clínica.
- Em tratamento: o paciente aceitou o plano e tem sessões a fazer. Já conta como venda fechada.
- Retorno: o médico pediu retorno e ele ainda não foi feito. Também já conta como venda fechada.

COMO O CARTÃO ANDA

- Presente na agenda: vai para Consulta. Faltou ou cancelou: vai para Follow-up, como antes.
- Ao finalizar, se o médico propôs tratamento: o cartão fica em Consulta até a recepção enviar o plano.
- Ao finalizar, sem tratamento: vai para Retorno, se o médico pediu retorno; senão, Concluído.
- Plano aceito: Em tratamento. Última sessão do pacote: Retorno, se houver; senão, Concluído.
- Sessão de pacote e horário de retorno não tiram o cartão da coluna em que ele está.

PARA ATIVAR

Abra Funil › Régua. Em "As etapas do funil" aparece o modelo da clínica com as mudanças propostas (as colunas novas e os nomes: Em conversa, Agendado, Plano ou orçamento enviado, Concluído). Marque o que quer adotar e aplique. Enquanto você não aplicar, nada muda no seu funil.$txt$,
 timestamptz '2026-10-02 01:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-funil-ate-o-retorno';
