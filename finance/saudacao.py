"""A SAUDAÇÃO AUTOMÁTICA DO CELULAR não é gente respondendo (27/09/2026).

O que aconteceu, medido na produção (só leitura): o celular do chip Thiago tem a
"mensagem de saudação" do WhatsApp Business ligada. Segundos depois de um contato
novo, sai sozinho "Olá, tudo bem? me chamo Thiago Pinheiro e sou o gerente de
vendas…" — o mesmo texto em 31/08, 09/09, 13/09, 24/09 e 27/09. O eco dela chega
como mensagem 'humano' do celular, e pra IA do número isso é "alguém da equipe
respondeu": ela sai da conversa antes de dizer oi (`chip_regra.pausar_se_humano`).
E no Desafio a saudação contava como a 1ª resposta da equipe, em 10 segundos.

A regra (docs/mockups/funil_atendimento.html, aprovada pelo dono): é SAUDAÇÃO a
mensagem que sai do celular (autor 'humano', sem membro) ATÉ 2 MINUTOS depois da 1ª
mensagem do cliente naquela conversa, com texto longo (40+ caracteres) IGUAL a um
que o mesmo chip já mandou do mesmo jeito pra outra conversa — também nos 2 minutos
depois da 1ª mensagem de lá. "Oi" digitado rápido não é saudação (curto); a resposta
digitada de verdade não se repete idêntica. Na HORA, a primeira vez que um texto
aparece não tem com o que comparar e conta como gente (a IA pausa, o lado seguro); da
segunda em diante é padrão — e, olhando pra trás (o Desafio, a vista Atendimento),
vale pras duas.

Só leitura, e só colunas que toda base tem (mensagens e conversas): não precisa de
migração, e vale pro que já está gravado.
"""
from __future__ import annotations

JANELA = "2 minutes"
MIN_CHARS = 40


def _primeira_in(cv: str) -> str:
    return (f"(select min(i.criado_em) from mensagens i "
            f"where i.conversa_id = {cv}.id and i.direcao = 'in')")


def sql_e_saudacao(m: str = "m", cv: str = "cv") -> str:
    """A mensagem `m`, da conversa `cv`, é a saudação automática? (SQL, sem parâmetros)"""
    return f"""({m}.direcao = 'out' and {m}.autor = 'humano' and {m}.membro_id is null
        and length(btrim(coalesce({m}.texto,''))) >= {MIN_CHARS}
        and {m}.criado_em <= {_primeira_in(cv)} + interval '{JANELA}'
        and exists (select 1 from mensagens s2 join conversas c2 on c2.id = s2.conversa_id
                     where c2.conta_id = {cv}.conta_id and c2.id <> {cv}.id
                       and c2.chip_id is not distinct from {cv}.chip_id
                       and s2.direcao = 'out' and s2.autor = 'humano' and s2.membro_id is null
                       and btrim(s2.texto) = btrim({m}.texto)
                       and s2.criado_em <= {_primeira_in('c2')} + interval '{JANELA}'))"""


def sql_nao_saudacao(m: str = "m", cv: str = "cv") -> str:
    return f"not coalesce({sql_e_saudacao(m, cv)}, false)"


def e_saudacao(c, conta_id: int, mensagem_id: int) -> bool:
    """Em Python, pra uma mensagem só. Tolerante: na dúvida, não é saudação — é o
    lado de sempre (a IA pausa), que não fala por cima de ninguém."""
    try:
        with c.transaction():
            r = c.execute(f"""select {sql_e_saudacao()} from mensagens m
                                join conversas cv on cv.id = m.conversa_id
                               where m.id = %s and cv.conta_id = %s""",
                          (mensagem_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return False
    return bool(r and r[0])
