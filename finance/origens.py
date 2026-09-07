"""De onde vieram os leads, e o que aconteceu com cada um depois.

A tela que lê isto é para a AGÊNCIA de tráfego. Por isso o que este módulo mede
para no limite do que o Zaq sabe: conversa, atendimento, compromisso marcado,
comparecimento, venda e faturamento. **Investimento, CPL, CAC, ROAS e ROI não
saem daqui de propósito** — quem mede isso é a agência, e o Zaq publicar um
número que ela não reconhece só faria o Zaq parecer errado quando divergisse.

A ponte entre os dois lados é o CÓDIGO do criativo (`prospeccao.origem_codigo`,
migração 220): a agência tem o código na planilha dela com o custo; o Zaq tem o
mesmo código aqui com o resultado. Cada lado junta pelo código sem precisar
adivinhar o número do outro.

DUAS FAIXAS, NÃO TRÊS. O mockup mostrava "com código", "sem origem" (quem apagou
o texto) e "outras origens" (orgânico). Na hora de escrever a consulta ficou
claro que a segunda e a terceira são a MESMA coisa para o banco: lead sem
código. Separá-las na tela seria inventar uma distinção que o dado não tem, e
essa é a diferença entre um painel que a agência confia e um que ela audita uma
vez e descarta. Então são duas faixas, e o rodapé diz o que "sem código" mistura.

POR QUE A RÉGUA DO COMPROMISSO NÃO É A `_E_VISITA` DO RELATÓRIO. Lá a pergunta é
"este evento da AGENDA é uma visita?", num universo em que a maioria dos eventos
não tem lead nenhum — daí o `titulo ilike 'visita%'`. Aqui a pergunta é outra:
"este LEAD chegou a marcar hora com a gente?". O vínculo com o lead já faz a
filtragem que o título fazia lá, e casar por título deixaria de fora a reunião
de uma conta de serviço recorrente, que nunca se chama "visita" (regra 6). O que
as duas réguas têm em comum é o discriminador que importa: `tipo_evento is null`
separa o NOSSO compromisso da FESTA do cliente.
"""
from __future__ import annotations

from finance import vendas

#: Um lead marcou compromisso quando existe evento na agenda ligado a ele que não
#: é a festa do próprio cliente. Vale para visita (eventos) e reunião
#: (recorrente) — o vocabulário é do perfil, a régua é a mesma.
_E_COMPROMISSO = "(e.prospeccao_id = p.id and e.tipo_evento is null)"

_SQL = """
with conv as (
    select cv.id                              as conversa_id,
           p.id                               as lead_id,
           nullif(btrim(coalesce(p.origem_codigo, '')), '') as codigo,
           (select min(m.criado_em) from mensagens m
             where m.conversa_id = cv.id and m.direcao = 'in')  as primeira_entrada,
           (select min(m.criado_em) from mensagens m
             where m.conversa_id = cv.id and m.direcao = 'out') as primeira_saida,
           exists (select 1 from eventos_agenda e
                    where e.conta_id = p.conta_id and {compromisso}) as marcou,
           exists (select 1 from eventos_agenda e
                    where e.conta_id = p.conta_id and {compromisso}
                      and e.desfecho = %(apareceu)s)                as compareceu,
           exists (select 1 from eventos_agenda e
                    where e.conta_id = p.conta_id and {compromisso}
                      and e.inicio < now())                         as ja_passou,
           exists (select 1 from eventos_agenda e
                    where e.conta_id = p.conta_id and {compromisso}
                      and e.inicio < now() and e.desfecho is null)  as sem_desfecho,
           o.sinal_pago_em                    as sinal_em,
           coalesce(o.primeiro_ano_centavos, 0) as valor
      from conversas cv
      join prospeccao p
        on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
      left join orcamentos o
        on o.id = p.orcamento_id and o.conta_id = p.conta_id
     where cv.conta_id = %(conta)s
       and cv.canal = 'whatsapp'
       and cv.criado_em::date >= %(ini)s
       and cv.criado_em::date <= %(fim)s
)
select codigo,
       count(*)                                                          as conversas,
       count(*) filter (where primeira_saida is not null)                as atendidas,
       count(*) filter (where marcou)                                    as marcaram,
       count(*) filter (where compareceu)                                as compareceram,
       count(*) filter (where ja_passou)                                 as ja_passou,
       count(*) filter (where sem_desfecho)                              as sem_desfecho,
       count(*) filter (where sinal_em is not null)                      as vendas,
       coalesce(sum(valor) filter (where sinal_em is not null), 0)       as faturamento,
       percentile_cont(0.5) within group (
           order by extract(epoch from (primeira_saida - primeira_entrada)) / 60.0
       ) filter (where primeira_saida is not null
                   and primeira_entrada is not null
                   and primeira_saida >= primeira_entrada)               as resposta_mediana,
       count(*) filter (where primeira_saida is not null
                          and primeira_entrada is not null
                          and primeira_saida <= primeira_entrada + interval '1 hour') as ate_1h
  from conv
 group by codigo
 order by conversas desc, codigo nulls last
"""

#: A mediana do CONJUNTO sai de uma consulta própria. Mediana de medianas não é
#: mediana — com uma faixa de 2 conversas e outra de 40, a das duas pesaria igual
#: e o número da tela ficaria errado justo onde ele é citado em reunião.
_SQL_MEDIANA = _SQL.split("select codigo,")[0] + """
select percentile_cont(0.5) within group (
           order by extract(epoch from (primeira_saida - primeira_entrada)) / 60.0
       )
  from conv
 where codigo is not null
   and primeira_saida is not null and primeira_entrada is not null
   and primeira_saida >= primeira_entrada
"""


def _linha(r) -> dict:
    return {
        "codigo": r[0], "conversas": r[1], "atendidas": r[2], "marcaram": r[3],
        "compareceram": r[4], "ja_passou": r[5], "sem_desfecho": r[6],
        "vendas": r[7], "faturamento_centavos": int(r[8] or 0),
        "resposta_mediana_min": None if r[9] is None else int(round(float(r[9]))),
        "ate_1h": r[10],
    }


def dados_origens(pool, conta_id: int, ini, fim, compromisso: str = "visita") -> dict:
    """O que entrou no período, separado por código de anúncio.

    `compromisso` é o vocabulário do perfil da conta ("visita" em eventos,
    "reunião" em recorrente) — quem lê é a tela; aqui ele só viaja junto para o
    rótulo do degrau não ser fixo no código (regra 6).

    Devolve sempre a mesma forma, inclusive sem nenhuma linha: tela que muda de
    formato quando o período está vazio quebra na primeira segunda-feira de
    manhã.
    """
    args = {"conta": conta_id, "ini": ini, "fim": fim,
            "apareceu": vendas.VISITA_APARECEU}
    with pool.connection() as c:
        rows = c.execute(_SQL.replace("{compromisso}", _E_COMPROMISSO), args).fetchall()
        med = c.execute(_SQL_MEDIANA.replace("{compromisso}", _E_COMPROMISSO),
                        args).fetchone()
    mediana = None if not med or med[0] is None else int(round(float(med[0])))
    linhas = [_linha(r) for r in rows]
    com = [l for l in linhas if l["codigo"]]
    sem = [l for l in linhas if not l["codigo"]]

    def _soma(campo, de):
        return sum(l[campo] for l in de)

    total_com = _soma("conversas", com)
    total_sem = _soma("conversas", sem)
    marcaram = _soma("marcaram", com)
    compareceram = _soma("compareceram", com)
    atendidas = _soma("atendidas", com)
    vendas_n = _soma("vendas", com)
    ja_passou = _soma("ja_passou", com)
    sem_desfecho = _soma("sem_desfecho", com)

    return {
        "compromisso": compromisso,
        "resumo": {
            "com_codigo": total_com,
            "sem_codigo": total_sem,
            "total": total_com + total_sem,
        },
        "funil": [
            {"chave": "conversas", "rotulo": "Conversas de anúncio", "n": total_com,
             "taxa": None},
            {"chave": "atendidas", "rotulo": "Atendidas", "n": atendidas,
             "taxa": vendas.taxa_com_cobertura(atendidas, total_com)},
            {"chave": "marcaram", "rotulo": f"Marcaram {compromisso}", "n": marcaram,
             "taxa": vendas.taxa_com_cobertura(marcaram, total_com)},
            {"chave": "compareceram", "rotulo": "Compareceram", "n": compareceram,
             # A taxa sai das RESPONDIDAS, e `base` é tudo que já aconteceu: o
             # compromisso que passou sem ninguém responder não é "não apareceu",
             # é "ninguém sabe" — e é a diferença entre os dois que a cobertura
             # denuncia.
             "taxa": vendas.taxa_com_cobertura(
                 compareceram, ja_passou - sem_desfecho, base=ja_passou)},
            {"chave": "vendas", "rotulo": "Vendas", "n": vendas_n,
             "taxa": vendas.taxa_com_cobertura(vendas_n, compareceram)},
        ],
        "linhas": sorted(com, key=lambda l: (-l["conversas"], l["codigo"] or "")),
        "sem_codigo": sem[0] if sem else None,
        "atendimento": {
            "atendidas": atendidas,
            "sem_resposta": total_com - atendidas,
            "ate_1h": _soma("ate_1h", com),
            "mediana_min": mediana,
        },
        "faturamento_centavos": _soma("faturamento_centavos", com),
        # a cobertura é o que impede o número de mentir por omissão: compromisso
        # que já aconteceu e ninguém respondeu não conta nem como compareceu nem
        # como faltou — e sem dizer quantos são, a taxa acima parece firme.
        "cobertura": {"sem_desfecho": sem_desfecho, "ja_passou": ja_passou},
    }
