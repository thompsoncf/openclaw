-- Alvo parado em erro SEM registro de qual número já falhou.
--
-- A 148 registrou o número tentado nos alvos que estavam em `erro` naquele
-- momento. Só que 29 outros estavam gravados como `enviado` por causa do
-- callback fora de ordem (ver 150) — quando a 150 os devolveu pra `erro`, eles
-- chegaram com `wa_tentados` vazio.
--
-- Sem isto, recolocar esses alvos na fila faria a PRIMEIRA tentativa sair pro
-- mesmo número que já tinha falhado: exatamente o desperdício que a fila existe
-- pra evitar, e uma mensagem de marketing cobrada por alvo.
--
-- A ordem de reconstrução espelha finance.campanhas_motor.fila_alvo_wa:
--   1. alvo_telefone — número TRAVADO pelo dono ao jogar na campanha (13 casos
--      aqui); quando existe, foi ele que saiu, não o ⭐;
--   2. o ⭐ `provavel` do decisor;
--   3. o whatsapp/telefone geral da empresa.
--
-- Os dígitos ficam sem o código do país, como faz _chave() — a base grava o mesmo
-- telefone como '+5586...' e '(86) ...', e sem normalizar a comparação não casa.
--
-- Idempotente: só toca em quem está com a lista vazia.
update public.campanha_alvos a
   set wa_tentativas = 1,
       wa_tentados = jsonb_build_array(
         (select case when d like '55%' and length(d) in (12, 13) then substr(d, 3) else d end
            from (select regexp_replace(coalesce(
                    nullif(a.alvo_telefone, ''),
                    (select t->>'formatado' from prospeccao p,
                            jsonb_array_elements(p.decisor_telefones) t
                      where p.id = a.prospeccao_id
                        and coalesce((t->>'provavel')::boolean, false) limit 1),
                    (select coalesce(nullif(p.whatsapp, ''), p.telefone) from prospeccao p
                      where p.id = a.prospeccao_id), ''), '\D', '', 'g') as d) x))
 where a.wa_status = 'erro'
   and coalesce(a.wa_tentativas, 0) = 0
   and coalesce(a.wa_tentados, '[]'::jsonb) = '[]'::jsonb
   and coalesce(a.wa_erro_codigo, '') ~ '^63[0-9]{3}$';

-- Não deu pra reconstruir o número: lista vazia é melhor que um "" que não casa
-- com dígito nenhum. O alvo perde a proteção, mas não ganha lixo.
update public.campanha_alvos
   set wa_tentados = '[]'::jsonb, wa_tentativas = 0
 where wa_tentados = '[""]'::jsonb;
