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
       -- o dia é o de BRASÍLIA: `criado_em::date` cortava no dia de Londres, e
       -- a conversa das 21h às 24h caía no dia seguinte (24/09/2026)
       and (cv.criado_em at time zone 'America/Sao_Paulo')::date >= %(ini)s
       and (cv.criado_em at time zone 'America/Sao_Paulo')::date <= %(fim)s
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


# ------------------------------------------------------------------ o porquê, por anúncio
#
# Pedido do dono em 24/09/2026, ao aprovar o mockup da aba Anúncios: por criativo,
# QUEM NÃO ERA CLIENTE e QUEM CHEGOU FORA DO HORÁRIO, e o porquê de cada perda. É o
# que o tráfego ajusta: o anúncio que traz currículo e fornecedor pra um espaço de
# festa gasta verba com quem nunca vai comprar.
#
# O universo é o MESMO da tabela acima (conversa de WhatsApp aberta no período), só
# que contado por LEAD: um cliente que abriu duas conversas é uma pessoa só no "não
# era cliente". Só contagens — nome, telefone e frase de cliente não saem daqui,
# porque esta tela é a da agência.

_SQL_LEADS = """
select distinct on (p.id)
       nullif(btrim(coalesce(p.origem_codigo, '')), '') as codigo,
       p.id, p.criado_em, p.status,
       case when coalesce(p.perda_motivo,'') not in ('','outro') then p.perda_motivo
            when p.perda_lida is not null then p.perda_lida
            when coalesce(p.perda_motivo,'') <> '' then p.perda_motivo
            else '_nao_lido' end as motivo,
       p.perda_lida_quem,
       lower(nullif(btrim(p.evento_tipo), '')), p.evento_convidados,
       nullif(btrim(p.segmento), ''), nullif(btrim(p.porte), '')
  from conversas cv
  join prospeccao p on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
 where cv.conta_id = %(conta)s
   and cv.canal = 'whatsapp'
   and (cv.criado_em at time zone 'America/Sao_Paulo')::date >= %(ini)s
   and (cv.criado_em at time zone 'America/Sao_Paulo')::date <= %(fim)s
 order by p.id
"""

#: motivos que NÃO são venda perdida: o lead nunca foi venda. Ficam cinza na barra.
_NAO_VENDA = ("nao_era_cliente", "sem_conversa", "_nao_lido", "outro")


def _turno(h: int) -> str:
    return "madrugada" if h < 6 else "manha" if h < 12 else "tarde" if h < 18 else "noite"


def _barras(cont: dict, rotulos: dict, cinza=()) -> list[dict]:
    if not cont:
        return []
    maxn = max(cont.values())
    return [{"chave": k, "rotulo": rotulos.get(k) or k.replace("_", " ").capitalize(),
             "n": n, "pct": round(100 * n / maxn), "cinza": k in cinza}
            for k, n in sorted(cont.items(), key=lambda x: (x[0] in cinza, -x[1]))]


def detalhes_por_codigo(pool, conta_id: int, ini, fim, perfil: str = "eventos") -> dict:
    """{codigo: detalhe} — `None` é a faixa "sem código". Cada detalhe:

        leads, nao_cliente, nao_cliente_pct, fora, fora_pct,
        perdemos  [{chave, rotulo, n, pct, cinza}]   o motivo que vale, por lead perdido
        quem      [{...}]                             de quem não era cliente, o que queria
        pedem     str                                 no vocabulário do nicho (regra 6)
        turnos    {manha, tarde, noite, madrugada}

    `perfil` decide o vocabulário do "o que pedem": festa pra quem vende festa,
    segmento e porte pra quem vende serviço."""
    from finance import cockpit_dono as _cd
    from finance import funil_regua as _fr
    from finance import motivo_lido as _ml
    from datetime import timezone as _tz
    args = {"conta": conta_id, "ini": ini, "fim": fim}
    with pool.connection() as c:
        try:
            with c.transaction():
                rows = c.execute(_SQL_LEADS, args).fetchall()
        except Exception:  # noqa: BLE001 — base sem a 328/331: sem o porquê, a tabela fica
            return {}
        cfg = _cd._cfg_janela(c, conta_id)
        rot = _ml.rotulos(c, conta_id, perfil)
    grupos: dict = {}
    for codigo, _lid, criado, status, motivo, quem, tipo, conv, seg, porte in rows:
        g = grupos.setdefault(codigo, {"leads": 0, "nao_cliente": 0, "fora": 0,
                                       "_perd": {}, "_quem": {}, "_tipo": {}, "_ate100": [0, 0],
                                       "_seg": {}, "_porte": {},
                                       "turnos": {"manha": 0, "tarde": 0, "noite": 0, "madrugada": 0}})
        g["leads"] += 1
        if criado:
            g["turnos"][_turno(criado.astimezone(_cd._brt()).hour)] += 1
            if not _fr.dentro_da_janela(criado.astimezone(_tz.utc), cfg):
                g["fora"] += 1
        if status == "perdido":
            g["_perd"][motivo] = g["_perd"].get(motivo, 0) + 1
            if motivo == "nao_era_cliente":
                g["nao_cliente"] += 1
                if quem:
                    g["_quem"][quem] = g["_quem"].get(quem, 0) + 1
        if tipo:
            g["_tipo"][tipo] = g["_tipo"].get(tipo, 0) + 1
        if conv:
            g["_ate100"][1] += 1
            if conv <= 100:
                g["_ate100"][0] += 1
        if seg:
            g["_seg"][seg] = g["_seg"].get(seg, 0) + 1
        if porte:
            g["_porte"][porte] = g["_porte"].get(porte, 0) + 1
    out = {}
    for codigo, g in grupos.items():
        n = g["leads"]
        if perfil == "eventos":
            tipos = sorted(g["_tipo"].items(), key=lambda x: -x[1])[:3]
            partes = [f"{t[:1].upper() + t[1:]} {k}" for t, k in tipos]
            if g["_ate100"][1]:
                partes.append(f"até 100 convidados: {g['_ate100'][0]} de {g['_ate100'][1]}")
        else:
            segs = sorted(g["_seg"].items(), key=lambda x: -x[1])[:3]
            portes = sorted(g["_porte"].items(), key=lambda x: -x[1])[:2]
            partes = [f"{s} {k}" for s, k in segs] + [f"porte {p} {k}" for p, k in portes]
        out[codigo] = {
            "leads": n,
            "nao_cliente": g["nao_cliente"],
            "nao_cliente_pct": round(100 * g["nao_cliente"] / n) if n else 0,
            "fora": g["fora"], "fora_pct": round(100 * g["fora"] / n) if n else 0,
            "perdemos": _barras(g["_perd"], {**rot, "_nao_lido": "Ainda não lido"}, _NAO_VENDA),
            "quem": _barras(g["_quem"], _ml.QUEM),
            "pedem": " · ".join(partes),
            "turnos": g["turnos"],
        }
    return out
