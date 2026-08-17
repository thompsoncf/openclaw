-- Lead que RECEBEU a mensagem, mas a ficha dele diz "erro".
--
-- A fila de números faz o alvo tentar um telefone por vez. Quando o primeiro falha
-- (63024, "este número não tem WhatsApp") e o segundo ENTREGA, o alvo termina certo
-- em `wa_status='entregue'` — só que o `wa_erro_codigo` da tentativa que morreu fica
-- pra trás. A tela lê esse campo na ficha do lead (`coalesce(a.wa_erro_msg,'')`), e
-- o dono vê "erro" num lead que foi entregue de verdade.
--
-- É o oposto da 150: lá o erro era a informação nova e o "enviado" atrasado passava
-- por cima; aqui a ENTREGA é a informação nova, e o erro é que é o resto velho. A
-- régua é a mesma dos dois lados — quem chegou a 'entregue'/'lido' recebeu, ponto.
--
-- Não mexe em `wa_tentados` nem em `wa_tentativas`: o número que falhou falhou
-- mesmo, e essa memória é o que impede a fila de tentar de novo no mesmo telefone.
-- O que sai é só o rótulo de erro, que não descreve mais o estado do alvo.
--
-- Idempotente: depois da primeira passada o `where` não casa mais nada.
update public.campanha_alvos
   set wa_erro_codigo = null,
       wa_erro_msg = null
 where wa_status in ('entregue', 'lido')
   and (wa_erro_codigo is not null or wa_erro_msg is not null);
