-- 176_novidade_desconto_no_cockpit.sql
-- O desconto chegou no app do vendedor.
--
-- Seguindo a receita da 175:
--   PORTÃO  → 'servico'. O montador do Cockpit é gateado por `vende_servico`
--             (finance/cockpit.vende_servico → empresa.o_que_vende), o mesmo portão
--             do painel. Não é só de eventos: advocacia, consultoria e construção
--             também têm vendedor montando orçamento.
--   TIPO    → 'novidade'. Ninguém perdeu botão: o vendedor GANHOU onde antes ele
--             mexia no preço na mão. Marca lida ao abrir a tela.
--   QUEM LÊ → dono e gestor (é a tela deles). O aviso conta que a EQUIPE ganhou a
--             ferramenta — quem decide política de desconto é quem manda.
--
-- Aditivo e idempotente.
insert into public.novidades (chave, tipo, publico, titulo, corpo, publicado_em) values

('desconto-no-app-do-vendedor', 'novidade', 'servico',
 'Seu vendedor já pode dar desconto pelo celular',
 $txt$O desconto por item e o do total entraram no app do vendedor.

Antes ele só tinha isso no painel — e como não usa o painel, mexia no preço do item na mão ou criava um item avulso já abatido. O desconto sumia do registro: a folha do cliente não mostrava nada riscado, e a margem que você lê depois não batia.

Agora é o mesmo controle do painel, na linha do serviço: ele escolhe % ou R$, vê o valor cheio riscado na hora, e o cliente recebe a folha mostrando quanto ganhou.

A conta é a mesma nos dois lugares, então orçamento montado no celular e no painel chegam no mesmo número.$txt$,
 timestamptz '2026-08-19 15:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'desconto-no-app-do-vendedor';
