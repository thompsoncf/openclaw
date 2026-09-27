"""A pré-consulta: o que o paciente conta antes da consulta, pelo link da ficha.

Desenho aprovado: docs/mockups/clinica_prontuario.html, seções 01 (quem vê o quê) e 12
(a ficha que nasce no agendamento). Migração 411.

É CONTEÚDO CLÍNICO. Por isso mora aqui, separado da ficha:
  - só o PROFISSIONAL DE SAÚDE da clínica lê as respostas (`pode_ler` + `ultima`);
  - a recepção, o dono e o gestor sem registro veem só que foi respondida e se há
    alergia, sem o texto (`resumo`);
  - o agente do WhatsApp nunca importa este módulo nem lê a tabela
    (tests/test_clinica_ficha_link.py trava o repositório se um dia ler).

O agente também não PERGUNTA nada disso na conversa: sintoma, alergia e remédio ficam
no formulário do link, que só o profissional lê.
"""
from __future__ import annotations

import json
from datetime import datetime

#: a pré-consulta de quem vem pela primeira vez (o exemplo do mockup). As perguntas
#: de cada especialidade (dermatologia, fisioterapia) entram quando os profissionais
#: disserem quais são (pergunta aberta da seção 13).
PERGUNTAS = (
    ("queixa", "O que te traz à consulta?", "texto"),
    ("alergia", "Tem alergia a algum remédio?", "sim_qual"),
    ("remedios", "Usa algum remédio todo dia?", "sim_qual"),
    ("gravidez", "Está grávida ou amamentando?", "gravidez"),
)
#: a do retorno (ideia 5 da seção 13): não repetir o formulário inteiro. A alergia é
#: perguntada de novo, com todas as letras: é o aviso que a recepção e a agenda mostram
PERGUNTAS_CURTA = (
    ("alergia", "Tem alergia a algum remédio?", "sim_qual"),
    ("mudou", "Mudou algo nos remédios ou na gravidez desde a última consulta?", "sim_qual"),
    ("queixa", "Quer contar algo para o profissional antes da consulta? (opcional)", "texto"),
)
GRAVIDEZ = (("nao", "Não"), ("sim", "Sim"), ("na", "Não se aplica"))
_MAX = 1000


def perguntas(curta: bool) -> tuple:
    return PERGUNTAS_CURTA if curta else PERGUNTAS


def validar(form: dict, curta: bool) -> tuple[dict | None, str | None]:
    """As respostas do formulário → (respostas, erro)."""
    out = {}
    for chave, _txt, tipo in perguntas(curta):
        if tipo == "texto":
            v = " ".join(str(form.get(chave) or "").split())[:_MAX]
            if chave == "queixa" and not curta and not v:
                return None, "Conte o que te traz à consulta."
            out[chave] = v
        elif tipo == "sim_qual":
            sn = str(form.get(chave) or "")
            if sn not in ("sim", "nao"):
                return None, "Responda todas as perguntas."
            qual = " ".join(str(form.get(chave + "_qual") or "").split())[:_MAX]
            if sn == "sim" and not qual:
                return None, "Conte qual, para o profissional saber."
            out[chave] = {"resposta": sn, "qual": qual if sn == "sim" else ""}
        elif tipo == "gravidez":
            v = str(form.get(chave) or "")
            if v not in dict(GRAVIDEZ):
                return None, "Responda todas as perguntas."
            out[chave] = v
    return out, None


def salvar(c, conta_id: int, cliente_id: int, respostas: dict, *, curta: bool, evento_id: int | None,
           papel: str = "paciente") -> int:
    alergia = respostas.get("alergia") or {}
    tem_alergia = alergia.get("resposta") == "sim"
    if curta and not tem_alergia:
        # no retorno, a alergia contada antes continua valendo até o profissional conferir
        ult = c.execute("""select tem_alergia from clinica_preconsultas where conta_id=%s and cliente_id=%s
                           order by criado_em desc limit 1""", (conta_id, cliente_id)).fetchone()
        tem_alergia = bool(ult and ult[0])
    r = c.execute(
        """insert into clinica_preconsultas (conta_id, cliente_id, evento_id, curta, respostas, tem_alergia,
                                             respondida_por)
           values (%s,%s,%s,%s,%s,%s,%s) returning id""",
        (conta_id, cliente_id, evento_id, curta, json.dumps(respostas, ensure_ascii=False), tem_alergia,
         "responsavel" if papel == "responsavel" else "paciente")).fetchone()
    return r[0]


# ------------------------------------------------------------------ quem lê

def pode_ler(c, conta_id: int, membro_id: int | None) -> bool:
    """Profissional de saúde da clínica, com login e registro no conselho (seção 01).
    Não vem do papel no sistema: o dono que não é profissional de saúde não lê."""
    if not membro_id:
        return False
    try:
        with c.transaction():
            r = c.execute(
                """select 1 from clinica_profissionais
                    where conta_id=%s and membro_id=%s and ativo and acesso <> 'sem_login'
                      and coalesce(conselho, '') <> '' limit 1""", (conta_id, membro_id)).fetchone()
    except Exception:  # noqa: BLE001
        return False
    return bool(r)


def ultima(c, conta_id: int, cliente_id: int) -> dict | None:
    """As respostas da última pré-consulta. SÓ para quem `pode_ler`."""
    try:
        with c.transaction():
            r = c.execute(
                """select curta, respostas, criado_em, respondida_por from clinica_preconsultas
                    where conta_id=%s and cliente_id=%s order by criado_em desc limit 1""",
                (conta_id, cliente_id)).fetchone()
    except Exception:  # noqa: BLE001 — migração 411 ainda não rodou
        return None
    if not r:
        return None
    resp = r[1] if isinstance(r[1], dict) else json.loads(r[1] or "{}")
    linhas = []
    for chave, txt, tipo in perguntas(r[0]):
        v = resp.get(chave)
        if tipo == "sim_qual":
            v = v or {}
            linhas.append((txt, ("Sim: " + v.get("qual", "")) if v.get("resposta") == "sim" else "Não"))
        elif tipo == "gravidez":
            linhas.append((txt, dict(GRAVIDEZ).get(v, "")))
        elif v:
            linhas.append((txt, v))
    from finance.clinica_agenda import local
    return {"curta": r[0], "linhas": linhas, "quando": local(r[2]), "por": r[3]}


def resumo(c, conta_id: int, cliente_ids: list[int]) -> dict[int, dict]:
    """O que a recepção pode ver: quando respondeu, se há alergia e se já houve uma
    pré-consulta completa (o retorno só usa a curta depois dela). Nada do texto."""
    if not cliente_ids:
        return {}
    try:
        with c.transaction():
            rows = c.execute(
                """select distinct on (cliente_id) cliente_id, criado_em, tem_alergia,
                          exists (select 1 from clinica_preconsultas x where x.conta_id = q.conta_id
                                   and x.cliente_id = q.cliente_id and not x.curta)
                     from clinica_preconsultas q where conta_id=%s and cliente_id = any(%s)
                    order by cliente_id, criado_em desc""", (conta_id, list(cliente_ids))).fetchall()
    except Exception:  # noqa: BLE001 — migração 411 ainda não rodou
        return {}
    return {r[0]: {"quando": r[1], "alergia": bool(r[2]), "tem_completa": bool(r[3])} for r in rows}


def respondida_desde(resumo_do_paciente: dict | None, desde: datetime | None) -> bool:
    if not resumo_do_paciente:
        return False
    return desde is None or resumo_do_paciente["quando"] > desde
