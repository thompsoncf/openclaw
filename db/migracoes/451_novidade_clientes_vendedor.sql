-- 451_novidade_clientes_vendedor.sql
-- O aviso de que a aba Clientes/Fornecedores abriu pro vendedor, seguindo a
-- seção 5 do CLAUDE.md.
--
-- Pedido do dono em 30/09/2026: "liberar a aba clientes/fornecedores pros
-- vendedores de todos os nichos" — hoje só o dono acessava (nem gestor, nem
-- vendedor: o link sumia do menu e a rota tinha trava dura de papel).
-- Decidido com o dono:
--   - vendedor cadastra, edita e consulta — SEM o botão Arquivar;
--   - dar baixa no fiado (lança no caixa) continua só dono/gestor;
--   - na Clínica, onde esta mesma aba já é só "Fornecedores" (paciente de
--     verdade mora em Pacientes), fica só com dono/gestor — fornecedor ali é
--     decisão de compra, não de atendimento da recepção.
--
-- O PORTÃO: `todos` — não é nicho-específico (abre em qualquer nicho que
-- tenha módulo PJ, menos a exceção da Clínica, que o texto do aviso já
-- explica). PRA QUEM: dono, gestor e vendedor — os três ganham ou já tinham a
-- tela; o vendedor é quem realmente muda de rotina aqui.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clientes-vendedor', 'novidade', 'todos', '{dono,gestor,vendedor}',
 'Vendedor agora acessa Clientes/Fornecedores',
 'A aba Clientes/Fornecedores, que só o dono via, abriu pro vendedor: cadastra, edita e consulta — sem o botão Arquivar.',
 '/painel/clientes',
 $txt$Antes, só o dono acessava a aba Clientes/Fornecedores — nem gestor, nem vendedor.

AGORA vendedor e gestor também cadastram, editam e consultam clientes e fornecedores, em qualquer nicho.

O que continua só com dono e gestor:
- o botão "Arquivar" de um cadastro;
- "dar baixa" no fiado (lança no caixa — juros e multa continuam correndo até o dono ou gestor confirmar o recebimento);
- na Clínica, a aba inteira (ali ela já era só "Fornecedores" — paciente de verdade mora em Pacientes, e fornecedor é decisão de compra).$txt$,
 timestamptz '2026-09-30 13:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clientes-vendedor';
