-- 486_novidade_saldo_negativo.sql
-- O aviso do "negativo" no saldo do banco, seguindo a seção 5 do CLAUDE.md.
-- Queixa da Prime em 02/10/2026: "não consegue colocar saldo negativo".
--
-- O QUE MUDOU NA TELA. No "atualizar saldo" do Planejamento da semana (aba
-- Empresa), cada banco ganhou a caixa "negativo": o campo abre o teclado numérico
-- do celular, e o do iPhone não tem o sinal de menos. E o valor que não dá pra
-- entender agora é recusado com aviso — antes virava R$ 0,00 e era gravado.
--
-- O PORTÃO: `empresa` (o Planejamento mora na aba Empresa). PRA QUEM: dono e
-- gestor. Sem resumo: correção de uso, não vai pro site.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, link, corpo, publicado_em) values

('saldo-negativo', 'mudanca', 'empresa', '{dono,gestor}',
 'Saldo do banco no vermelho: agora tem a caixa "negativo"',
 '/painel/empresa#planejamento',
 $txt$No "atualizar saldo" do Planejamento da semana, cada banco ganhou a caixa "negativo".

Pelo celular não dava pra digitar o sinal de menos — o teclado numérico do iPhone não tem. Agora é só digitar o valor e marcar "negativo" quando a conta estiver no vermelho (cheque especial). Digitar com o menos, como no extrato ("2.400,00-" ou "-2.400,00"), também vale.

E se o valor não puder ser entendido, a tela avisa em vez de gravar zero.$txt$,
 timestamptz '2026-10-02 19:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'saldo-negativo';
