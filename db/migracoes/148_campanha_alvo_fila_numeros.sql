-- Fila de NÚMEROS por alvo, em vez de um número só.
--
-- A campanha tentava o ⭐ que a Credify marca como `provavel` e parava por ali.
-- Medido na produção: 37 alvos parados em erro guardavam 214 números, sendo 129
-- com `whatsapp: true` na própria base — nunca tentados. Um lead chegou a ter 45
-- números e uma única tentativa.
--
-- `wa_tentados` = dígitos dos números que já falharam neste alvo (nunca reenviar
-- pro mesmo, nem quando a base guarda o número repetido com outro `tipo`).
-- `wa_tentativas` = quantas mensagens já foram gastas com este alvo, contra o
-- teto de 3 do motor. Cada tentativa é marketing cobrado.
alter table public.campanha_alvos
  add column if not exists wa_tentados   jsonb not null default '[]'::jsonb,
  add column if not exists wa_tentativas int  not null default 0;

-- Os alvos que já pararam em erro: registra o que foi tentado, pra fila nunca
-- reenviar pro número que acabou de falhar.
--
-- NÃO os devolve pra fila de propósito. Cada tentativa custa R$ 0,3217, e voltar
-- os 37 de uma vez gastaria ~R$ 24 sozinho no deploy — isso é decisão do dono,
-- pelo botão "Colocar na fila" do painel, não de uma migração. Daqui pra frente
-- todo erro novo anda sozinho: o motor tenta o próximo sem marcar `erro`.
--
-- Só os erros 63xxx, que são do DESTINATÁRIO ("este número não tem WhatsApp") —
-- é a mesma divisão do 147: erro 2xxxx é da conta e não diz nada sobre o número.
update public.campanha_alvos a
   set wa_tentativas = 1,
       wa_tentados = jsonb_build_array(
         regexp_replace(coalesce(
           (select t->>'formatado' from prospeccao p,
                   jsonb_array_elements(p.decisor_telefones) t
             where p.id = a.prospeccao_id
               and coalesce((t->>'provavel')::boolean, false) limit 1),
           (select coalesce(nullif(p.whatsapp,''), p.telefone) from prospeccao p
             where p.id = a.prospeccao_id), ''), '\D', '', 'g'))
 where a.wa_status = 'erro'
   and a.wa_tentativas = 0
   and coalesce(a.wa_erro_codigo,'') ~ '^63[0-9]{3}$';

-- Sobrou string vazia quando não deu pra reconstruir o número tentado: melhor
-- lista vazia do que um "" que nunca casa com dígito nenhum.
update public.campanha_alvos
   set wa_tentados = '[]'::jsonb
 where wa_tentados = '[""]'::jsonb;
