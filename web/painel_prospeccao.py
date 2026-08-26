"""Aba "Prospecção" do painel — pipeline de outbound do vendedor.

Kanban por status, ficha do alvo (dados enriquecidos + histórico + registrar
contato), captação de leads (Manual / CSV / Google Maps) e enriquecimento de
CNPJ. Dono e gestor veem a carteira inteira, filtram por vendedor e atribuem;
o vendedor só enxerga os alvos dele.

Reusa o motor do portal: _render/_env (base, gate, nav) + conta_logada + o login
por membro (contas.equipe). Escopo multi-tenant sagrado: toda query filtra por
conta[0]. As fontes externas (Places, BrasilAPI) vivem em finance.prospeccao_fontes.
"""
import base64
import csv as _csv
import io
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, Request, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from psycopg.errors import UniqueViolation

from db.conexao import get_pool
from contas import equipe as eq
from finance import campanhas_motor as _cm
from finance import funil_regua as _fr
from finance import prospec_convite as _prospec_convite
from finance import prospec_inbound as _prospec_inbound
from finance import prospeccao_fontes as fontes
from finance import servicos_catalogo as scat
from finance import validadoc as _validadoc
from finance.email_sender import remetente_configurado
from web.portal import _render, _env, conta_logada, brl

router = APIRouter()

# ---------------------------------------------------------------- domínio (rótulos)
STATUS = [
    ("novo", "Novo"), ("contatado", "Contatado"), ("qualificado", "Qualificado"),
    ("proposta", "Proposta"), ("ganho", "Ganho"), ("perdido", "Perdido"),
]
STATUS_OK = {s for s, _ in STATUS}
STATUS_ROT = dict(STATUS)
# Etapas do funil personalizáveis por conta (migração 130). As três "fixas" podem ser
# renomeadas mas não removidas/reordenadas: 'novo' é a entrada (o lead nasce nela) e
# 'ganho'/'perdido' são resultado (relatórios dependem delas). O miolo é livre.
_ETAPAS_PADRAO = [
    ("novo", "Novo", 0, True), ("contatado", "Contatado", 10, False),
    ("qualificado", "Qualificado", 20, False), ("proposta", "Proposta", 30, False),
    ("ganho", "Ganho", 900, True), ("perdido", "Perdido", 910, True),
]
_ORDEM_GANHO = 900     # etapas de VENDA entram antes disso (o miolo fica < 900)
_ORDEM_PERDIDO = 910   # e as de PÓS-VENDA depois daqui (migração 177)


def _etapas(c, conta_id: int) -> list[dict]:
    """Etapas do funil da conta, ordenadas. Semeia o padrão na 1ª vez (conta sem
    etapas ainda). Retorna [{id, chave, rotulo, ordem, fixa}]."""
    sql = "select id, chave, rotulo, ordem, fixa from funil_etapas where conta_id=%s order by ordem, id"
    rows = c.execute(sql, (conta_id,)).fetchall()
    if not rows:
        for chave, rotulo, ordem, fixa in _ETAPAS_PADRAO:
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa)
                         values (%s,%s,%s,%s,%s) on conflict (conta_id, chave) do nothing""",
                      (conta_id, chave, rotulo, ordem, fixa))
        c.commit()
        rows = c.execute(sql, (conta_id,)).fetchall()
    return [{"id": r[0], "chave": r[1], "rotulo": r[2], "ordem": r[3], "fixa": r[4]} for r in rows]
TEMPERATURAS = [("frio", "Frio"), ("morno", "Morno"), ("quente", "Quente")]
TEMP_OK = {t for t, _ in TEMPERATURAS}
TEMP_COR = {"frio": "#5b9bd5", "morno": "var(--ambar)", "quente": "var(--coral)"}
TEMP_PILL = {"frio": ("#14273a", "#7bb8e6"), "morno": ("#2e2713", "#e0b25a"),
             "quente": ("#3a1a1a", "#f0917f")}
TIPOS = [("ligacao", "Ligação"), ("whatsapp", "WhatsApp"), ("email", "E-mail"),
         ("reuniao", "Reunião"), ("visita", "Visita"), ("nota", "Nota")]
TIPO_OK = {t for t, _ in TIPOS}
TIPO_ROT = dict(TIPOS)
RESULTADOS = [("", "—"), ("sem_resposta", "Sem resposta"), ("retornar", "Retornar"),
              ("interessado", "Interessado"), ("sem_interesse", "Sem interesse"),
              ("agendado", "Agendado"), ("fechado", "Fechado")]
RESULTADO_OK = {r for r, _ in RESULTADOS if r}
RESULTADO_ROT = dict(RESULTADOS)
_RES_VERDE = {"interessado", "agendado", "fechado"}
_RES_AMBAR = {"sem_resposta", "sem_interesse", "retornar"}


def _agora():
    return datetime.now(timezone.utc)


def _hora_br(dt, fmt: str = "%d/%m %H:%M") -> str:
    """Data/hora em horário de Brasília (UTC-3). O banco guarda tudo em UTC, e o
    inbox mostrava a hora crua — 3 horas adiantada pra quem está no Brasil. Fixo
    em -3 de propósito: o país não tem horário de verão desde 2019, e é a mesma
    conta que o resto do painel já fazia (ex.: histórico de envios)."""
    return (dt - timedelta(hours=3)).strftime(fmt) if dt else ""


def _so_digitos(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _wa_equivalentes(numero: str) -> list[str]:
    """As formas do MESMO celular brasileiro: com e sem o nono dígito.

    O WhatsApp não é coerente: o mesmo contato aparece ora como 55 86 98392961
    (12 dígitos, formato antigo), ora como 55 86 998392961 (13). Quem casa por
    igualdade crua de `contato_ref` acha que são duas pessoas — e a mensagem vai
    parar na conversa errada, ou em nenhuma.

    Por que não os últimos 8 dígitos, que é o atalho usado pra achar LEAD neste
    módulo: ali um engano dá em não achar o dono do número; aqui daria em gravar
    a mensagem na conversa de OUTRA pessoa, porque '98392961' pode ser o final de
    um celular do 86 e de um do 11. As duas grafias do mesmo número resolvem o
    caso real sem abrir essa porta.

    Fora do Brasil (ou número curto demais pra ter forma dupla) devolve só ele.

    SOBRE A FORMA DA CONSULTA que usa isto. Quem procura conversa por número filtra
    SEMPRE em duas etapas:

        right(regexp_replace(contato_ref, '\\D', '', 'g'), 8) = <8 finais>   -- índice
        and regexp_replace(contato_ref, '\\D', '', 'g') = any(<equivalentes>)  -- precisão

    A primeira linha é literalmente a expressão do índice `idx_conversas_num8`
    (migração 156) — e índice de EXPRESSÃO só é usado quando a consulta repete a
    expressão idêntica, inclusive o '\\D' e o 'g'. Sem ela, cada mensagem que chega
    varre a tabela de conversas inteira; foi assim que a agenda de um pareamento
    derrubou o app em 2.446 respostas 502 no dia 15/08. A segunda linha é o que
    impede o casamento frouxo: as duas grafias do MESMO número compartilham os 8
    finais, mas os 8 finais sozinhos também casam com um celular de outro DDD.
    Índice pra achar rápido, igualdade exata pra não errar de pessoa.

    Se algum dia estas consultas mudarem de forma, o índice muda junto."""
    d = _so_digitos(numero)
    if not d:
        return []
    formas = [d]
    if d.startswith("55") and len(d) == 12 and d[4] in "6789":
        formas.append(d[:4] + "9" + d[4:])       # 55 DDD 8392961 → 55 DDD 9 8392961
    elif d.startswith("55") and len(d) == 13 and d[4] == "9":
        formas.append(d[:4] + d[5:])             # o contrário
    return formas


def _zap_link(numero: str) -> str:
    d = _so_digitos(numero)
    if not d:
        return ""
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]                          # tira o DDI pra normalizar o miolo
    if len(d) == 10 and d[2] in "6789":
        d = d[:2] + "9" + d[2:]            # celular sem o 9 → insere (ex.: 86 9434-8180)
    if not d.startswith("55"):
        d = "55" + d
    return "https://wa.me/" + d


def _zap_link_texto(numero: str, texto: str) -> str:
    """wa.me com a mensagem já preenchida (o vendedor confere e envia em 1 clique)."""
    base = _zap_link(numero)
    if not base:
        return ""
    return base + "?text=" + quote(texto or "")


def _tem_ia() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _tem_credify() -> bool:
    from finance import credify as cf
    return cf.tem_credenciais()


def _ein_remetente(pool, conta_id):
    """E-mail que a empresa usa pra enviar (caixa própria ou global)."""
    from finance import email_inbound as _ein
    return _ein.remetente_conta(pool, conta_id)


def _membro_contato(pool, conta_id: int, vid):
    """nome + e-mail do responsável (pra assinar a mensagem e virar Reply-To)."""
    if not vid:
        return None, None
    with pool.connection() as c:
        r = c.execute("select coalesce(nullif(nome,''), email), email from membros "
                      "where id=%s and conta_id=%s", (vid, conta_id)).fetchone()
    return (r[0], r[1]) if r else (None, None)


# ---------------------------------------------------------------- acesso / escopo
def _acesso(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if not eq.caps_do_papel(papel).get("vendas"):
        return None, RedirectResponse("/painel", status_code=303)
    if not conta[11]:  # prospecção é ferramenta de EMPRESA (módulo PJ) — não de conta PF
        return None, RedirectResponse("/painel", status_code=303)
    _membro_id = request.session.get("membro_id")
    _gerencia = papel in ("dono", "gestor")
    ctx = {"conta": conta, "conta_id": conta[0], "papel": papel,
           "membro_id": _membro_id,
           "gerencia": _gerencia,                     # vê a carteira toda + filtra
           "pode_atribuir": papel == "dono",          # só o dono atribui/reatribui
           # Campanha liberada individualmente pelo dono (migração 183). Só se
           # consulta pra quem NÃO é gerência: dono e gestor já passam pelo gate
           # antigo, e assim a tela deles não paga uma consulta por request.
           "pode_campanha": (not _gerencia) and eq.pode_campanha(
               get_pool(), conta[0], _membro_id)}
    # Atalho usado nos gates de campanha/enriquecimento: gerência OU liberado.
    ctx["gere_campanha"] = ctx["gerencia"] or ctx["pode_campanha"]
    return ctx, None


def _vendedores(pool, conta_id: int) -> list[dict]:
    """Quem pode receber alvos: o dono (aparece pelo nome) + vendedores/gestores.
    O dono vem primeiro e rotulado, pra ele poder ficar com leads no próprio nome."""
    with pool.connection() as c:
        rows = c.execute(
            """select id, coalesce(nullif(nome,''), email), papel
                 from membros
                where conta_id=%s and ativo and papel in ('dono','vendedor','gestor')
                order by (papel<>'dono'), nome""", (conta_id,)).fetchall()
    out = []
    for r in rows:
        nome = r[1] + " (você)" if r[2] == "dono" else r[1]
        out.append({"id": r[0], "nome": nome, "papel": r[2]})
    return out


def _carrega_alvo(pool, conta_id: int, alvo_id: int):
    with pool.connection() as c:
        r = c.execute(
            """select p.id, p.empresa, p.cnpj, p.segmento, p.cidade, p.uf,
                      p.contato, p.cargo, p.telefone, p.whatsapp, p.email,
                      p.status, p.temperatura, p.valor_estimado_centavos, p.origem,
                      p.obs, p.instagram, p.socio, p.regime_tributario, p.porte,
                      p.ultimo_contato_em, p.proximo_contato_em, p.vendedor_id,
                      m.nome, p.orcamento_id, p.tem_site, p.maps_url, p.receita,
                      p.site_url, p.decisor_nome, p.decisor_cargo, p.decisor_telefone,
                      p.decisor_whatsapp, p.decisor_em, p.decisor_telefones,
                      p.tipo, p.cpf,
                      p.cep, p.endereco, p.numero, p.bairro, p.nascimento
                 from prospeccao p
                 left join membros m on m.id = p.vendedor_id
                where p.id=%s and p.conta_id=%s""", (alvo_id, conta_id)).fetchone()
    if not r:
        return None
    cols = ["id", "empresa", "cnpj", "segmento", "cidade", "uf", "contato", "cargo",
            "telefone", "whatsapp", "email", "status", "temperatura", "valor",
            "origem", "obs", "instagram", "socio", "regime_tributario", "porte",
            "ultimo_contato_em", "proximo_contato_em", "vendedor_id", "vendedor_nome",
            "orcamento_id", "tem_site", "maps_url", "receita", "site_url",
            "decisor_nome", "decisor_cargo", "decisor_telefone", "decisor_whatsapp", "decisor_em",
            "decisor_telefones", "tipo", "cpf",
            "cep", "endereco", "numero", "bairro", "nascimento"]
    d = dict(zip(cols, r))
    # PF x PJ: o que a ficha precisa saber pra trocar rótulo, documento e esconder o
    # que só existe em empresa (sócio, regime, porte, Receita, decisor).
    d["eh_pf"] = (d.get("tipo") == "pf")
    d["doc"] = d["cpf"] if d["eh_pf"] else d["cnpj"]
    d["doc_fmt"] = _fmt_doc(d["doc"])
    d["doc_rot"] = "CPF" if d["eh_pf"] else "CNPJ"
    d["zap_link"] = _zap_link(d["whatsapp"] or d["telefone"])
    d["insta_url"] = _prospec_inbound.normalizar_instagram(d.get("instagram") or "")
    d["tel_link"] = "tel:" + _so_digitos(d["telefone"]) if d["telefone"] else ""
    d["site_dominio"] = _dominio(d.get("site_url"))
    d["decisor_zap"] = _zap_link(d["decisor_telefone"]) if d.get("decisor_whatsapp") else ""
    d["decisor_tel_link"] = "tel:" + _so_digitos(d["decisor_telefone"]) if d.get("decisor_telefone") else ""
    # enriquece cada telefone do decisor com links (ligar / WhatsApp) pro template
    _TIPO_TEL = {"COMERCIAL": "Comercial", "RESIDENCIAL": "Residencial", "RECADO": "Recado",
                 "CELULAR": "Celular"}
    tels = d.get("decisor_telefones") if isinstance(d.get("decisor_telefones"), list) else []
    for t in tels:
        fmt = t.get("formatado") or ""
        t["tel_link"] = "tel:" + _so_digitos(fmt) if fmt else ""
        t["zap_link"] = _zap_link(fmt) if t.get("whatsapp") and fmt else ""
        t["tipo_rot"] = _TIPO_TEL.get((t.get("tipo") or "").upper(), (t.get("tipo") or "").title())
    d["decisor_telefones"] = tels
    return d


def _dominio(url: str | None) -> str:
    """Só o domínio, pra mostrar o link curto na ficha (sem http:// nem /caminho)."""
    u = (url or "").strip()
    if not u:
        return ""
    u = u.split("://", 1)[-1]
    u = u.split("/", 1)[0]
    return u[4:] if u.startswith("www.") else u


def _pode_ver(alvo: dict, ctx: dict) -> bool:
    if ctx["gerencia"]:
        return True
    return alvo["vendedor_id"] is not None and alvo["vendedor_id"] == ctx["membro_id"]


def _pode_campanha(ctx: dict, camp_id: int, c=None) -> bool:
    """Quem pode ver/gerenciar uma campanha: dono/gestor (tudo) OU o membro
    vinculado como responsável dela (espelha o vendedor_id do lead). Confirma que a
    campanha é da conta antes — evita vazar campanha de outra empresa. Sem `c`, abre
    uma conexão própria (pra usar como guarda no topo das rotas)."""
    if c is None:
        with get_pool().connection() as _c:
            return _pode_campanha(ctx, camp_id, _c)
    r = c.execute("select responsavel_id from campanhas where id=%s and conta_id=%s",
                  (camp_id, ctx["conta_id"])).fetchone()
    if not r:
        return False
    return ctx["gerencia"] or (r[0] is not None and r[0] == ctx["membro_id"])


def _vendedor_destino(ctx: dict, vendedor_id: str, pool, conta_id: int):
    """Pra quem vai o alvo captado: só o dono escolhe (validando a conta);
    vendedor/gestor sempre pra si mesmo."""
    if not ctx["pode_atribuir"]:
        return ctx["membro_id"]
    if not (vendedor_id or "").isdigit():
        return None
    vid = int(vendedor_id)
    with pool.connection() as c:
        ok = c.execute("select 1 from membros where id=%s and conta_id=%s",
                       (vid, conta_id)).fetchone()
    return vid if ok else None


def _eh_ajax(request: Request) -> bool:
    return request.headers.get("x-requested-with") == "fetch"


def _nome_vendedor(pool, conta_id: int, vid):
    if not vid:
        return None
    with pool.connection() as c:
        r = c.execute("select coalesce(nullif(nome,''), email) from membros where id=%s and conta_id=%s",
                      (vid, conta_id)).fetchone()
    return r[0] if r else None


def _lead_card(id_, empresa, segmento, cidade, uf, temperatura, valor, vendedor):
    """Payload mínimo pra o JS montar um card no kanban (sem recarregar)."""
    return {"id": id_, "empresa": empresa, "segmento": segmento or "", "cidade": cidade or "",
            "uf": uf or "", "temperatura": temperatura, "valor": int(valor or 0), "vendedor": vendedor}


def _fmt_cnpj(cnpj: str | None) -> str:
    """00000000000000 -> 00.000.000/0000-00. Sobra como veio se não tiver 14 dígitos."""
    d = "".join(ch for ch in (cnpj or "") if ch.isdigit())
    if len(d) != 14:
        return cnpj or ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def _fmt_doc(doc: str | None) -> str:
    """CPF ou CNPJ mascarado pra exibição (11 ou 14 dígitos). Sobra como veio se não
    for nem um nem outro — documento meia-boca do passado continua legível."""
    return _validadoc.formatar(doc) or (doc or "")


def _doc_lead(tipo: str, cnpj: str, cpf: str, documento: str = "") -> tuple[str, str | None, str | None, str]:
    """Resolve o documento de um lead pros campos certos. Devolve
    (tipo, cnpj, cpf, erro) — cnpj/cpf já só em dígitos, prontos pro banco.

    Quem manda na COLUNA é o tamanho do documento (11 = CPF, 14 = CNPJ), não o botão
    que o usuário deixou marcado: colar um CPF com "Pessoa Jurídica" ligado gravaria
    o CPF na coluna cnpj e furaria a deduplicação das duas. O `tipo` do formulário só
    decide o rótulo quando não há documento nenhum — que é o caso comum, já que o
    documento é opcional dos dois lados.

    O CPF é validado de verdade (dígito verificador, finance/validadoc) antes de
    entrar: coluna nova, sem histórico pra respeitar. O CNPJ segue como sempre foi —
    aceito como veio, porque a base já tem CNPJ vindo de Google/CSV/Receita e
    reprovar agora quebraria cadastro que hoje funciona."""
    tipo = tipo if tipo in ("pf", "pj") else ""
    bruto = (documento or "").strip() or (cpf or "").strip() or (cnpj or "").strip()
    achado, digitos = _validadoc.classificar(bruto)
    if not digitos:
        return (tipo or "pj", None, None, "")
    if achado is None:
        return (tipo or "pj", None, None,
                "Documento inválido: informe um CPF (11 dígitos) ou um CNPJ (14).")
    if achado == "pf":
        if not _validadoc.valida_cpf(digitos):
            return ("pf", None, None, f"CPF {_fmt_doc(digitos)} não existe — confira os números.")
        return ("pf", None, digitos, "")
    return ("pj", digitos, None, "")


def _lead_duplicado_info(pool, conta_id: int, cnpj: str, empresa_tentativa: str,
                         campo: str = "cnpj") -> dict:
    """Depois de um UniqueViolation em (conta_id, cnpj) ou (conta_id, cpf): acha o lead
    que já existe e onde ele está (Base, funil ou campanha) — quem tentou cadastrar de
    novo geralmente quer ir direto ver/ajustar aquele lead, não só saber que ele já
    existe."""
    campo = campo if campo in ("cnpj", "cpf") else "cnpj"
    with pool.connection() as c:
        ex = c.execute(f"select id, empresa, estagio from prospeccao where conta_id=%s and {campo}=%s",
                        (conta_id, cnpj)).fetchone()
        if not ex:
            return {"msg": f"“{empresa_tentativa}” já está cadastrado — não dá pra duplicar.",
                    "link_url": None, "link_label": None}
        ex_id, ex_nome, ex_estagio = ex
        camps = c.execute(
            """select cp.id, cp.nome from campanha_alvos a join campanhas cp on cp.id=a.campanha_id
                where a.prospeccao_id=%s order by a.criado_em desc""", (ex_id,)).fetchall()
    ficha_url = f"/painel/prospeccao/{ex_id}"
    if camps:
        nomes = ", ".join(f"“{n}”" for _, n in camps)
        onde = f"está na campanha {nomes}" if len(camps) == 1 else f"está em {len(camps)} campanhas: {nomes}"
        if len(camps) == 1:
            link_url, link_label = f"/painel/prospeccao/campanhas/{camps[0][0]}", "Ver campanha ›"
        else:
            link_url, link_label = ficha_url, "Ver lead ›"
    elif ex_estagio == "lead":
        onde, link_url, link_label = "já virou lead e está no funil", ficha_url, "Ver lead ›"
    else:
        onde, link_url, link_label = "está na Base, ainda sem campanha", ficha_url, "Ver lead ›"
    rot = "CPF" if campo == "cpf" else "CNPJ"
    return {"msg": f"“{ex_nome}” já está cadastrado ({rot} {_fmt_doc(cnpj)}) — {onde}.",
            "link_url": link_url, "link_label": link_label}


_RECEITA_KEYS = ["fonte", "razao_social", "nome_fantasia", "situacao", "abertura",
                 "capital_social", "natureza", "endereco", "inscricao_estadual",
                 "atividade_principal", "atividades_secundarias"]


def _receita_extras(d: dict) -> dict:
    """Só os campos ricos da Receita que valem guardar/mostrar (o resto já vai
    pras colunas)."""
    return {k: d.get(k) for k in _RECEITA_KEYS if d.get(k)}


# ================================================================ KANBAN
def _promover_para_lead(c, conta_id, pros_id) -> None:
    """Engajou (topou/respondeu/interesse/manual) → dado da base vira lead: entra no
    funil em Novo + Quente. Quem já é lead só esquenta, sem resetar a etapa do funil."""
    c.execute(
        """update prospeccao
              set estagio='lead',
                  status = case when estagio='base' then 'novo' else status end,
                  temperatura='quente',
                  atualizado_em=now()
            where id=%s and conta_id=%s""",
        (pros_id, conta_id))


@router.get("/painel/prospeccao", response_class=HTMLResponse)
def prospeccao_kanban(request: Request, vendedor: str = ""):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    conta_id = ctx["conta_id"]
    where = ["p.conta_id = %s"]
    params: list = [conta_id]
    filtro_vend = ""
    if not ctx["gerencia"]:
        where.append("p.vendedor_id = %s")
        params.append(ctx["membro_id"])
    else:
        filtro_vend = (vendedor or "").strip()
        if filtro_vend == "nao":
            where.append("p.vendedor_id is null")
        elif filtro_vend.isdigit():
            where.append("p.vendedor_id = %s")
            params.append(int(filtro_vend))
    where.append("p.estagio = 'lead'")   # funil = só quem engajou; o resto fica na aba Base
    with pool.connection() as c:
        etapas = _etapas(c, conta_id)
        # dois_chips decide se o apelido do chip aparece no selo de campanha — com um
        # chip só não existe "de qual chip" pra confundir (mesma regra do Inbox).
        dois_chips = _tem_dois_chips(c, conta_id)
        rows = c.execute(
            f"""select p.id, p.empresa, p.segmento, p.cidade, p.uf, p.status,
                       p.temperatura, p.valor_estimado_centavos, p.proximo_contato_em,
                       p.telefone, p.whatsapp, p.vendedor_id, m.nome,
                       p.email, p.instagram, p.enriquecido_em, ca.cnome
                  from prospeccao p
                  left join membros m on m.id = p.vendedor_id
                  left join lateral (
                     select cp.nome as cnome
                       from campanha_alvos a join campanhas cp on cp.id=a.campanha_id
                      where a.prospeccao_id=p.id
                      order by a.ultima_msg_em desc nulls last, a.id desc limit 1
                  ) ca on true
                 where {' and '.join(where)}
                 order by p.proximo_contato_em asc nulls last, p.atualizado_em desc""",
            tuple(params)).fetchall()
        # O selo do canal só vira BOTÃO (abre o chat) quando existe conversa de
        # verdade — não quando só tem telefone/e-mail cadastrado. Uma query em
        # lote pra todo o board (= any), não uma por card: o índice único
        # (conta_id, prospeccao_id, canal) da 080 garante no máximo 1 linha por par.
        # `chip_id` vem JUNTO — é a mesma conversa que abre o 💬, então é ela (e só
        # ela) que decide "de qual chip" pro selo de campanha. Nasceu como uma
        # lateral separada (correlacionada por prospeccao_id) e foi trocada por
        # isso: mesma fonte que o Inbox usa, sem chance de escolher outra conversa.
        conv_por_lead: dict[int, dict[str, int]] = {}
        chip_por_lead: dict[int, int] = {}
        lead_ids = [r[0] for r in rows]
        if lead_ids:
            for pid, canal, cid, chip_id in c.execute(
                """select prospeccao_id, canal, id, chip_id from conversas
                    where conta_id=%s and prospeccao_id = any(%s)
                      and canal in ('whatsapp','email','instagram')""",
                (conta_id, lead_ids)).fetchall():
                conv_por_lead.setdefault(pid, {})[canal] = cid
                if canal == "whatsapp":
                    chip_por_lead[pid] = chip_id
        # apelido de cada chip usado no board + o do chip principal (mesmo par de
        # colunas que o Inbox lê: contas.nome pro secundário, canais_config.rotulo
        # pro principal — ver comunicacao_chip_apelido).
        chip_nomes: dict[int, str] = {}
        rotulo_principal = ""
        if dois_chips and chip_por_lead:
            ids_chip = [v for v in set(chip_por_lead.values()) if v]
            if ids_chip:
                chip_nomes = dict(c.execute(
                    "select id, coalesce(nullif(btrim(nome),''),'') from contas where id = any(%s)",
                    (ids_chip,)).fetchall())
            r = c.execute(
                "select coalesce(nullif(btrim(rotulo),''),'') from canais_config "
                "where conta_id=%s and canal='whatsapp'", (conta_id,)).fetchone()
            rotulo_principal = (r[0] if r else "") or ""
        # o mesmo número atendido pelo OUTRO chip — uma consulta pro funil inteiro, e
        # nenhuma numa empresa de um chip só (que é o caso de quase todas)
        gemeos = _gemeos_de_outro_chip(c, conta_id, [r[0] for r in rows])
    colunas = {e["chave"]: [] for e in etapas}
    primeira = etapas[0]["chave"] if etapas else "novo"
    total_valor = 0
    for r in rows:
        conv = conv_por_lead.get(r[0], {})
        chip_apelido = None
        # só resolve apelido quando o lead TEM conversa de WhatsApp de verdade — sem
        # isso, "sem chip nenhum" e "chip principal" ficavam indistinguíveis e o selo
        # mostrava "· 📱 <rótulo do principal>" até pra quem nunca trocou mensagem.
        if dois_chips and r[0] in chip_por_lead:
            chip_id_lead = chip_por_lead[r[0]]
            chip_apelido = (chip_nomes.get(chip_id_lead) if chip_id_lead
                            else rotulo_principal) or None
        card = {"id": r[0], "empresa": r[1], "segmento": r[2], "cidade": r[3],
                "uf": r[4], "status": r[5], "temperatura": r[6], "valor": r[7],
                "proximo": r[8], "telefone": r[9], "whatsapp": r[10],
                "vendedor_id": r[11], "vendedor": r[12],
                "tem_email": bool(r[13]), "tem_whatsapp": bool(r[10]),
                "tem_instagram": bool(r[14]), "enriquecido": bool(r[15]),
                "conv_whatsapp": conv.get("whatsapp"), "conv_email": conv.get("email"),
                "conv_instagram": conv.get("instagram"),
                "campanha": r[16] or None,
                "chip_apelido": chip_apelido,
                "gemeo": _aviso_gemeo(gemeos.get(r[0])),
                "gemeo_lead": ((gemeos.get(r[0]) or {}).get("lead_id")
                               if _gemeo_abre(gemeos.get(r[0]), ctx) else None)}
        colunas.get(r[5], colunas[primeira]).append(card)
        if r[5] != "perdido":
            total_valor += int(r[7] or 0)
    # rótulos (chave, rotulo) pro template + etapas ricas (com nº de leads) pro editor
    status_tpl = [(e["chave"], e["rotulo"]) for e in etapas]
    etapas_edit = [{**e, "n": len(colunas.get(e["chave"], []))} for e in etapas]
    vends = _vendedores(pool, conta_id) if ctx["gerencia"] else []
    return _render("prospeccao", request, titulo="Prospecção", secao_ativa="prospeccao",
                   status=status_tpl, etapas=etapas_edit, colunas=colunas, temp_cor=TEMP_COR, temp_pill=TEMP_PILL,
                   temperaturas_all=TEMPERATURAS, gerencia=ctx["gerencia"], pode_atribuir=ctx["pode_atribuir"],
                   vendedores=vends, filtro_vend=filtro_vend, total_valor=total_valor,
                   total_alvos=len(rows), tem_places=fontes.tem_chave_places(),
                   tem_maps_js=fontes.tem_chave_maps_js(), maps_js_key=fontes.chave_maps_js(),
                   aviso=request.session.pop("prosp_aviso", None))


# ================================================================ BASE (captados)
@router.get("/painel/prospeccao/base", response_class=HTMLResponse)
def prospeccao_base(request: Request, q: str = "", segmento: str = "", cidade: str = "",
                    ver_camp: str = ""):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = ctx["conta_id"]
    where = ["p.conta_id=%s", "p.estagio='base'"]
    params: list = [conta_id]
    # por padrão a lista "enxuga": quem já está numa campanha some (evita reenviar/
    # duplicar). O toggle ?ver_camp=1 INVERTE — mostra só quem está em campanha (a
    # mesma condição do contador "📣 N já em campanha", só negada, pra nunca destoar).
    if ver_camp == "1":
        where.append("exists (select 1 from campanha_alvos a where a.prospeccao_id=p.id)")
    else:
        where.append("not exists (select 1 from campanha_alvos a where a.prospeccao_id=p.id)")
    if not ctx["gerencia"]:
        where.append("p.vendedor_id=%s"); params.append(ctx["membro_id"])
    if (q or "").strip():
        where.append("p.empresa ilike %s"); params.append("%" + q.strip() + "%")
    if (segmento or "").strip():
        where.append("p.segmento ilike %s"); params.append("%" + segmento.strip() + "%")
    if (cidade or "").strip():
        where.append("p.cidade ilike %s"); params.append("%" + cidade.strip() + "%")
    wsql = " and ".join(where)
    esc = "" if ctx["gerencia"] else " and p.vendedor_id=%s"
    bp = [conta_id] if ctx["gerencia"] else [conta_id, ctx["membro_id"]]
    with get_pool().connection() as c:
        rows = c.execute(f"""
            select p.id, p.empresa, p.segmento, p.cidade, p.uf, p.whatsapp, p.email, p.telefone,
                   ca.cnome, ca.wa_status, ca.passo_atual, ca.ult_txt, ca.ult_raw,
                   (case when coalesce(p.decisor_nome,'')<>'' then 1 else 0 end),
                   (case when p.enriquecido_em is not null then 1 else 0 end),
                   p.instagram, p.decisor_nome, p.decisor_telefone, p.decisor_telefones, p.cnpj
              from prospeccao p
              left join lateral (
                 select cp.nome as cnome, a.wa_status, a.passo_atual, a.ultima_msg_em as ult_raw,
                        to_char(a.ultima_msg_em - interval '3 hours','DD/MM HH24:MI') as ult_txt
                   from campanha_alvos a join campanhas cp on cp.id=a.campanha_id
                  where a.prospeccao_id=p.id
                  order by a.ultima_msg_em desc nulls last, a.id desc limit 1
              ) ca on true
             where {wsql}
             order by coalesce(ca.ult_raw, p.atualizado_em) desc nulls last, p.id desc
             limit 300""", tuple(params)).fetchall()
        na_base = c.execute(f"select count(*) from prospeccao p where p.conta_id=%s and p.estagio='base'{esc}", tuple(bp)).fetchone()[0]
        com_wpp = c.execute(f"select count(*) from prospeccao p where p.conta_id=%s and p.estagio='base'{esc} and coalesce(nullif(p.whatsapp,''),nullif(p.telefone,'')) is not null", tuple(bp)).fetchone()[0]
        com_mail = c.execute(f"select count(*) from prospeccao p where p.conta_id=%s and p.estagio='base'{esc} and coalesce(nullif(p.email,''),'')<>''", tuple(bp)).fetchone()[0]
        em_camp = c.execute(f"select count(distinct p.id) from prospeccao p join campanha_alvos a on a.prospeccao_id=p.id where p.conta_id=%s and p.estagio='base'{esc}", tuple(bp)).fetchone()[0]
        virou = c.execute(f"select count(*) from prospeccao p where p.conta_id=%s and p.estagio='lead'{esc}", tuple(bp)).fetchone()[0]
        # empresas que aparecem 2+ vezes na base (duplicadas) — pra marcar/alertar
        dup_rows = c.execute(f"""select lower(trim(p.empresa)) from prospeccao p
                                  where p.conta_id=%s and p.estagio='base'{esc}
                                    and coalesce(trim(p.empresa),'')<>''
                                  group by lower(trim(p.empresa)) having count(*)>1""",
                             tuple(bp)).fetchall()
        camp_rows = c.execute("select id, nome, status from campanhas where conta_id=%s order by criado_em desc",
                              (conta_id,)).fetchall() if ctx["gerencia"] else []
    dup_set = {r[0] for r in dup_rows}
    campanhas = [{"id": r[0], "nome": r[1], "status_rot": _STATUS_ROT_CP.get(r[2], r[2])} for r in camp_rows]
    _TIPO_TEL = {"COMERCIAL": "Comercial", "RESIDENCIAL": "Residencial", "RECADO": "Recado", "CELULAR": "Celular"}
    leads = []
    for r in rows:
        tels_raw = r[18] if isinstance(r[18], list) else []
        dec_tels = [{"formatado": t.get("formatado") or "", "provavel": bool(t.get("provavel")),
                     "whatsapp": bool(t.get("whatsapp")),
                     "tipo_rot": _TIPO_TEL.get((t.get("tipo") or "").upper(), (t.get("tipo") or "").title())}
                    for t in tels_raw if t.get("formatado")]
        # o "melhor" é quem a campanha usaria sozinha (mesma regra do
        # _melhor_de_lista): o ⭐ provável; sem provável, o 1º da lista. É esse
        # que já vem marcado no checkbox de "jogar pra campanha".
        _melhor_i = next((i for i, t in enumerate(dec_tels) if t["provavel"]), 0 if dec_tels else None)
        for i, t in enumerate(dec_tels):
            t["melhor"] = (i == _melhor_i)
        # a lista de decisor pode vir grande (Credify traz até 5-6 números por
        # empresa); por padrão só mostra os que são WhatsApp confirmado — o
        # resto fica atrás de um "+N números ›" pra não inchar a linha. Sem
        # nenhum WhatsApp confirmado, mostra só o melhor palpite (não esconde
        # o único jeito de contatar o decisor).
        dec_tels_visiveis = [t for t in dec_tels if t["whatsapp"]] \
            or ([dec_tels[_melhor_i]] if dec_tels else [])
        _vis_ids = {id(t) for t in dec_tels_visiveis}
        dec_tels_ocultos = [t for t in dec_tels if id(t) not in _vis_ids]
        leads.append({"id": r[0], "empresa": r[1], "segmento": r[2], "cidade": r[3], "uf": r[4],
                      "tem_wpp": bool(r[5] or r[7]), "tem_mail": bool(r[6]), "campanha": r[8],
                      "toque_wa": 1 if r[9] == "enviado" else 0, "toque_mail": int(r[10] or 0),
                      "ult": r[11], "tem_decisor": bool(r[13]), "verificado": bool(r[14]),
                      "whats": (r[5] or r[7] or ""), "email_v": (r[6] or ""), "insta": (r[15] or ""),
                      "dec_nome": (r[16] or ""), "dec_tel": (r[17] or ""), "dec_tels": dec_tels,
                      "dec_tels_visiveis": dec_tels_visiveis, "dec_tels_ocultos": dec_tels_ocultos,
                      "dup": ((r[1] or "").strip().lower() in dup_set),
                      "cnpj": (_fmt_cnpj(r[19]) if r[19] else "")})
    metr = {"na_base": na_base, "com_wpp": com_wpp, "com_mail": com_mail, "em_camp": em_camp,
            "virou": virou, "n_dup": len(dup_set)}
    vends = _vendedores(get_pool(), conta_id) if ctx["gerencia"] else []
    return _render("prospeccao_base", request, titulo="Base", secao_ativa="prospeccao",
                   leads=leads, metr=metr, q=q, segmento=segmento, cidade=cidade,
                   gerencia=ctx["gerencia"], pode_atribuir=ctx["pode_atribuir"], vendedores=vends,
                   campanhas=campanhas, temperaturas_all=TEMPERATURAS, tem_places=fontes.tem_chave_places(),
                   tem_maps_js=fontes.tem_chave_maps_js(), maps_js_key=fontes.chave_maps_js(),
                   ver_camp=(ver_camp == "1"),
                   aviso=request.session.pop("prosp_aviso", None))


@router.post("/painel/prospeccao/base/promover")
def prospeccao_base_promover(request: Request, ids: list[str] = Form([]), only: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    alvos = [only] if (only or "").strip() else ids
    n = 0
    with get_pool().connection() as c:
        for i in alvos:
            try:
                pid = int(i)
            except (ValueError, TypeError):
                continue
            _promover_para_lead(c, ctx["conta_id"], pid)
            n += 1
        c.commit()
    request.session["prosp_aviso"] = (f"{n} contato(s) promovido(s) a lead 🔥 — já estão no funil."
                                      if n else "Selecione ao menos um contato pra promover.")
    return RedirectResponse("/painel/prospeccao/base", status_code=303)


@router.post("/painel/prospeccao/base/add-campanha")
async def prospeccao_base_add_campanha(request: Request):
    """Da Base: joga os contatos MARCADOS numa campanha específica — existente ou uma
    NOVA (nasce em rascunho, com a sequência padrão, pra você definir a abordagem antes
    de ativar). Cada campanha = uma abordagem; nada entra no lugar errado.
    Cada lead pode vir com um número escolhido no checkbox (tel_<id>, o ⭐ mais
    provável já vem marcado) — trava esse número pra campanha (ver
    finance/campanhas_motor.fila_alvo_wa); sem escolha, o motor decide sozinho
    em tempo de envio, como sempre foi."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gere_campanha"]:
        request.session["prosp_aviso"] = "Só o dono/gestor gerencia campanhas."
        return RedirectResponse("/painel/prospeccao/base", status_code=303)
    form = await request.form()
    ids = form.getlist("ids")
    campanha_id = form.get("campanha_id", "")
    novo_nome = form.get("novo_nome", "")
    pids = [int(i) for i in ids if str(i).isdigit()]
    if not pids:
        request.session["prosp_aviso"] = "Marque ao menos um contato pra jogar na campanha."
        return RedirectResponse("/painel/prospeccao/base", status_code=303)
    # 1º checkbox marcado de cada lead (a ordem no form segue a ordem da tela,
    # decisor primeiro) — os demais marcados ficam só de reserva visual por
    # ora, não geram mais de 1 alvo por lead.
    telefones = {pid: ((form.getlist(f"tel_{pid}") or [None])[0] or "").strip() or None for pid in pids}
    nova = (campanha_id == "__nova__") or (not campanha_id and (novo_nome or "").strip())
    with get_pool().connection() as c:
        if nova:
            nome = (novo_nome or "").strip()[:120] or "Nova campanha"
            # mesma marcação da rota /campanhas/nova: quem nasce com _PASSOS_PADRAO
            # nasce com o modelo 'generico' registrado, senão o resultado não tem causa
            cid = c.execute("""insert into campanhas (conta_id, nome, criado_por, modelo_codigo)
                               values (%s,%s,%s,'generico') returning id""",
                            (ctx["conta_id"], nome, ctx["membro_id"])).fetchone()[0]
            for (ordem, dias, assunto, corpo, ia) in _PASSOS_PADRAO:
                c.execute("""insert into campanha_passos (campanha_id, ordem, dias_apos, assunto, corpo, usar_ia)
                             values (%s,%s,%s,%s,%s,%s)""", (cid, ordem, dias, assunto, corpo, ia))
        else:
            try:
                cid = int(campanha_id)
            except (ValueError, TypeError):
                request.session["prosp_aviso"] = "Escolha uma campanha ou crie uma nova."
                return RedirectResponse("/painel/prospeccao/base", status_code=303)
            row = c.execute("select nome from campanhas where id=%s and conta_id=%s",
                            (cid, ctx["conta_id"])).fetchone()
            if not row:
                request.session["prosp_aviso"] = "Campanha não encontrada."
                return RedirectResponse("/painel/prospeccao/base", status_code=303)
            nome = row[0]
        n = 0
        for pid in pids:
            n += c.execute(
                """insert into campanha_alvos (campanha_id, prospeccao_id, alvo_telefone)
                     select %s, p.id, %s from prospeccao p
                      where p.conta_id=%s and p.id=%s on conflict do nothing""",
                (cid, telefones.get(pid), ctx["conta_id"], pid)).rowcount
        c.commit()
    # nova campanha → vai pra tela dela (define abordagem + ativar); existente → volta pra Base
    if nova:
        request.session["prosp_aviso"] = (f"Campanha '{nome}' criada com {n} contato(s) ✓ — "
                                          "ajuste a abordagem e ative quando quiser.")
        return RedirectResponse(f"/painel/prospeccao/campanhas/{cid}", status_code=303)
    request.session["prosp_aviso"] = f"{n} contato(s) adicionados à campanha '{nome}' ✓"
    return RedirectResponse("/painel/prospeccao/base", status_code=303)


@router.post("/painel/prospeccao/base/tirar-campanha")
def prospeccao_base_tirar_campanha(request: Request, ids: list[str] = Form([])):
    """Da lista '📣 já em campanha' (Base com ?ver_camp=1): tira os contatos MARCADOS
    de toda e qualquer campanha da conta — apaga o vínculo em campanha_alvos. Eles
    voltam a aparecer na lista normal da Base, livres pra enriquecer/reenviar do zero."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gere_campanha"]:
        request.session["prosp_aviso"] = "Só o dono/gestor gerencia campanhas."
        return RedirectResponse("/painel/prospeccao/base?ver_camp=1", status_code=303)
    pids = [int(i) for i in ids if str(i).isdigit()]
    if not pids:
        request.session["prosp_aviso"] = "Marque ao menos um contato pra tirar da campanha."
        return RedirectResponse("/painel/prospeccao/base?ver_camp=1", status_code=303)
    with get_pool().connection() as c:
        n = c.execute(
            """delete from campanha_alvos where prospeccao_id = any(%s)
                 and campanha_id in (select id from campanhas where conta_id=%s)""",
            (pids, ctx["conta_id"])).rowcount
        c.commit()
    request.session["prosp_aviso"] = f"{n} contato(s) tirados da campanha ✓ — já podem ser enriquecidos e reenviados."
    return RedirectResponse("/painel/prospeccao/base", status_code=303)


@router.get("/painel/prospeccao/base/historico")
def prospeccao_base_historico(request: Request):
    """Histórico de envios às campanhas: dia → campanha → empresas. Alimenta o
    painel embutido na Base (JSON, carregado sob demanda)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    esc = "" if ctx["gerencia"] else " and p.vendedor_id=%s"
    params = [ctx["conta_id"]] if ctx["gerencia"] else [ctx["conta_id"], ctx["membro_id"]]
    with get_pool().connection() as c:
        rows = c.execute(f"""
            select to_char(a.criado_em - interval '3 hours','DD/MM/YYYY') as dia,
                   cp.nome, p.empresa
              from campanha_alvos a
              join campanhas cp on cp.id=a.campanha_id
              join prospeccao p on p.id=a.prospeccao_id
             where p.conta_id=%s{esc}
             order by a.criado_em desc, cp.nome, p.empresa
             limit 3000""", tuple(params)).fetchall()
    dias: list = []
    idx_dia: dict = {}
    for dia, cnome, empresa in rows:
        d = idx_dia.get(dia)
        if d is None:
            d = {"dia": dia, "total": 0, "_camp": {}, "campanhas": []}
            idx_dia[dia] = d
            dias.append(d)
        cg = d["_camp"].get(cnome)
        if cg is None:
            cg = {"nome": cnome or "(sem nome)", "n": 0, "empresas": []}
            d["_camp"][cnome] = cg
            d["campanhas"].append(cg)
        cg["empresas"].append(empresa or "—")
        cg["n"] += 1
        d["total"] += 1
    for d in dias:
        d.pop("_camp", None)
    return JSONResponse({"ok": True, "dias": dias})


@router.post("/painel/prospeccao/base/explorium")
async def prospeccao_base_explorium(request: Request):
    """TESTE de conexão com a Explorium (Vibe): pega o 1º lead marcado, roda o
    businesses/match (nome + domínio do site) e devolve a resposta CRUA — pra
    confirmar que a EXPLORIUM_API_KEY funciona e ver o formato real antes de
    construir o enrich de firmografia/decisor."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gere_campanha"]:
        return JSONResponse({"ok": False, "erro": "Peça ao dono pra liberar campanhas pra você."})
    from finance import explorium as ex
    if not ex.tem_credenciais():
        return JSONResponse({"ok": False, "erro": "EXPLORIUM_API_KEY não configurada no Render (Environment)."})
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    if not ids:
        return JSONResponse({"ok": False, "erro": "Marque um lead pra testar."})
    with get_pool().connection() as c:
        row = c.execute("select empresa, site_url from prospeccao where id=%s and conta_id=%s",
                        (ids[0], ctx["conta_id"])).fetchone()
    if not row:
        return JSONResponse({"ok": False, "erro": "Lead não encontrado."})
    empresa, site = row
    from urllib.parse import urlparse
    s = (site or "").strip()
    if s and not s.startswith("http"):
        s = "https://" + s
    try:
        dom = (urlparse(s).hostname or "").lower().replace("www.", "") if s else ""
    except Exception:  # noqa: BLE001
        dom = ""
    return JSONResponse({"ok": True, "empresa": empresa, "dominio": dom,
                         "resposta": ex.match_business(empresa or "", dom)})


def _explorium_filtros(form) -> dict:
    """Monta os filtros da Explorium a partir do formulário da Base."""
    filtros: dict = {}
    pais = (form.get("pais", "br") or "br").strip().lower()
    if pais:
        filtros["country_code"] = {"values": [pais]}
    tamanhos = [t for t in form.getlist("tamanho") if t.strip()]
    if tamanhos:
        filtros["company_size"] = {"values": tamanhos}
    regioes = [r.strip().upper() for r in (form.get("regioes", "") or "").split(",") if r.strip()]
    if regioes:
        filtros["company_region_country_code"] = {"values": regioes}
    categoria = (form.get("categoria", "") or "").strip()
    if categoria:
        campo = "linkedin_category" if form.get("cat_tipo") == "linkedin" else "google_category"
        filtros[campo] = {"values": [categoria]}
    return filtros


@router.post("/painel/prospeccao/base/explorium-estimar")
async def prospeccao_explorium_estimar(request: Request):
    """Tamanho do mercado pro filtro (grátis) — antes de gastar crédito importando."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gere_campanha"]:
        return JSONResponse({"ok": False, "erro": "Peça ao dono pra liberar campanhas pra você."})
    from finance import explorium as ex
    if not ex.tem_credenciais():
        return JSONResponse({"ok": False, "erro": "EXPLORIUM_API_KEY não configurada no Render."})
    form = await request.form()
    r = ex.stats(_explorium_filtros(form))
    if not r.get("ok"):
        return JSONResponse({"ok": False, "erro": f"Explorium status {r.get('status')}: {r.get('data') or r.get('erro')}"})
    data = r.get("data") or {}
    return JSONResponse({"ok": True, "total": data.get("total_results", 0), "bruto": data})


@router.post("/painel/prospeccao/base/explorium-importar")
async def prospeccao_explorium_importar(request: Request):
    """Importa empresas do filtro para a Base, já com o DECISOR + contato (Explorium).
    Consome crédito: fetch empresas + prospects + enrich de contato. Teto por rodada."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gere_campanha"]:
        return JSONResponse({"ok": False, "erro": "Peça ao dono pra liberar campanhas pra você."})
    from finance import explorium as ex
    if not ex.tem_credenciais():
        return JSONResponse({"ok": False, "erro": "EXPLORIUM_API_KEY não configurada no Render."})
    form = await request.form()
    try:
        qtd = max(1, min(10, int(form.get("qtd", "5"))))   # teto: 10 por rodada (protege crédito)
    except (ValueError, TypeError):
        qtd = 5
    filtros = _explorium_filtros(form)
    cargos = [c for c in form.getlist("cargo") if c.strip()] or ["owner", "founder", "cxo", "partner"]
    conta_id = ctx["conta_id"]

    rb = ex.fetch_businesses(filtros, size=qtd)
    if not rb.get("ok"):
        return JSONResponse({"ok": False, "erro": f"fetch empresas: status {rb.get('status')}: {rb.get('data') or rb.get('erro')}"})
    empresas = ((rb.get("data") or {}).get("data")) or []
    if not empresas:
        return JSONResponse({"ok": True, "n": 0, "msg": "Nenhuma empresa achada pro filtro."})
    bmap = {b.get("business_id"): b for b in empresas if b.get("business_id")}
    ids = list(bmap.keys())

    rp = ex.fetch_prospects(ids, job_levels=cargos, size=qtd)
    prospects = ((rp.get("data") or {}).get("data")) if rp.get("ok") else []
    prospects = prospects or []

    inseridos, ja_tinha = 0, 0
    import json as _json
    with get_pool().connection() as c:
        vistos_biz = set()
        for p in prospects[:qtd]:
            try:
                bid = p.get("business_id")
                if bid in vistos_biz:      # 1 decisor por empresa nesta rodada
                    continue
                vistos_biz.add(bid)
                b = bmap.get(bid, {})
                empresa = (p.get("company_name") or b.get("name") or "").strip()
                if not empresa:
                    continue
                dominio = (b.get("domain") or "").strip()
                # dedup por domínio/empresa
                dup = c.execute(
                    "select 1 from prospeccao where conta_id=%s and (lower(empresa)=lower(%s) "
                    "or (%s<>'' and site_url ilike %s)) limit 1",
                    (conta_id, empresa, dominio, "%" + dominio + "%")).fetchone()
                if dup:
                    ja_tinha += 1
                    continue
                # contato direto do decisor (enrich — consome crédito)
                ci = {}
                if p.get("prospect_id"):
                    ec = ex.enrich_contact(p["prospect_id"])
                    ci = (ec.get("data") or {}).get("data") if ec.get("ok") else {}
                    ci = ci or {}
                email = ci.get("professions_email") or (ci.get("emails") or [None])[0] or (p.get("emails") or [None])[0]
                fone = ci.get("mobile_phone") or ci.get("phone_numbers")
                tels = [t for t in [ci.get("mobile_phone"), ci.get("phone_numbers")] if t]
                obs = f"Explorium · funcionários {b.get('number_of_employees_range','?')} · {b.get('country_name','')}".strip()
                c.execute(
                    """insert into prospeccao (conta_id, empresa, site_url, segmento, email, whatsapp,
                         decisor_nome, decisor_cargo, decisor_telefone, decisor_whatsapp,
                         decisor_telefones, decisor_em, obs, origem, temperatura, status, estagio,
                         enriquecido_em, criado_por, vendedor_id)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,now(),%s,'explorium','frio','novo','base',now(),%s,%s)""",
                    (conta_id, empresa[:250], dominio, (b.get("google_category") or b.get("linkedin_category") or "")[:120],
                     email, fone, (p.get("full_name") or "")[:200], (p.get("job_title") or "")[:120],
                     fone, bool(ci.get("mobile_phone")),
                     _json.dumps([{"formatado": t, "provavel": (i == 0)} for i, t in enumerate(tels)]),
                     # vendedor_id: gerência importa pra base da empresa (None, como
                     # sempre); vendedor liberado importa PRA SI — senão ele paga a
                     # consulta e o lead nasce sem dono, invisível na carteira dele.
                     obs[:500], ctx["membro_id"],
                     None if ctx["gerencia"] else ctx["membro_id"]))
                inseridos += 1
            except Exception:  # noqa: BLE001
                pass
    return JSONResponse({"ok": True, "n": inseridos, "ja_tinha": ja_tinha,
                         "empresas": len(empresas), "prospects": len(prospects)})


# ================================================================ ADD MANUAL
@router.post("/painel/prospeccao/novo")
def prospeccao_novo(request: Request, empresa: str = Form(...), segmento: str = Form(""),
                    cidade: str = Form(""), uf: str = Form(""), contato: str = Form(""),
                    telefone: str = Form(""), whatsapp: str = Form(""), email: str = Form(""),
                    cnpj: str = Form(""), cpf: str = Form(""), documento: str = Form(""),
                    tipo: str = Form(""), temperatura: str = Form("frio"),
                    valor: str = Form(""), origem: str = Form("manual"),
                    vendedor_id: str = Form(""), obs: str = Form(""),
                    socio: str = Form(""), regime_tributario: str = Form(""),
                    porte: str = Form(""), cargo: str = Form(""), instagram: str = Form(""),
                    site_url: str = Form(""), receita: str = Form(""), voltar: str = Form(""),
                    destino: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    ajax = _eh_ajax(request)
    empresa = (empresa or "").strip()
    volta = voltar if voltar in ("/painel/prospeccao", "/painel/prospeccao/captar") else "/painel/prospeccao"
    tipo_lead, cnpj_limpo, cpf_limpo, erro_doc = _doc_lead(tipo, cnpj, cpf, documento)
    if not empresa:
        falta = "o nome da pessoa" if tipo_lead == "pf" else "o nome da empresa"
        if ajax:
            return JSONResponse({"ok": False, "erro": f"Informe ao menos {falta}."}, status_code=400)
        request.session["prosp_aviso"] = f"Informe ao menos {falta}."
        return RedirectResponse(volta, status_code=303)
    if erro_doc:
        if ajax:
            return JSONResponse({"ok": False, "erro": erro_doc}, status_code=400)
        request.session["prosp_aviso"] = erro_doc
        return RedirectResponse(volta, status_code=303)
    if tipo_lead == "pf":
        # sócio/regime/porte são do quadro societário — pessoa física não tem
        socio = regime_tributario = porte = ""
    temperatura = temperatura if temperatura in TEMP_OK else "frio"
    # ONDE o lead nasce. O padrão histórico é a BASE: a captação em massa (Google
    # Maps, CSV, Explorium) traz quem NÓS fomos atrás, e isso ainda não é lead. Mas o
    # formulário manual que abre DENTRO do funil manda `destino=funil` — ali o cadastro
    # é de quem já chegou, e nascer na base fazia o card aparecer na tela e sumir no
    # primeiro refresh: o funil só lista `estagio='lead'` (ver prospeccao_kanban).
    estagio = "lead" if (destino or "").strip() == "funil" else "base"
    site_link = (site_url or "").strip()
    if site_link and "://" not in site_link:
        site_link = "https://" + site_link
    tem_site = True if site_link else None   # tem link → tem site; senão desconhecido
    # pacote da Receita que o "Buscar Receita" capturou (hidden), só as chaves ricas
    receita_json = None
    if (receita or "").strip():
        try:
            receita_json = json.dumps(_receita_extras(json.loads(receita)))
        except Exception:  # noqa: BLE001
            receita_json = None
    pool = get_pool()
    vend = _vendedor_destino(ctx, vendedor_id, pool, ctx["conta_id"])
    etapa1 = ""
    try:
        with pool.connection() as c:
            # o rótulo da 1ª etapa é da CONTA (dá pra renomear "Novo"), então o aviso
            # tem que perguntar pro banco em vez de chutar o nome padrão
            if estagio == "lead":
                etapas = _etapas(c, ctx["conta_id"])
                etapa1 = etapas[0]["rotulo"] if etapas else "Novo"
            row = c.execute(
                """insert into prospeccao (conta_id, vendedor_id, empresa, segmento, cidade,
                     uf, contato, cargo, telefone, whatsapp, email, cnpj, temperatura,
                     valor_estimado_centavos, origem, obs, socio, regime_tributario, porte,
                     instagram, site_url, tem_site, receita, criado_por, tipo, cpf, estagio)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s) returning id""",
                (ctx["conta_id"], vend, empresa, segmento.strip() or None, cidade.strip() or None,
                 (uf or "").strip()[:2].upper() or None, contato.strip() or None, cargo.strip() or None,
                 telefone.strip() or None, whatsapp.strip() or None, email.strip().lower() or None,
                 cnpj_limpo, temperatura, _reais_para_centavos(valor),
                 (origem or "manual").strip() or None, obs.strip() or None,
                 socio.strip() or None, regime_tributario.strip() or None, porte.strip() or None,
                 instagram.strip() or None, site_link or None, tem_site, receita_json,
                 ctx["membro_id"], tipo_lead, cpf_limpo, estagio)).fetchone()
            c.commit()
    except UniqueViolation:
        doc, campo = (cpf_limpo, "cpf") if cpf_limpo else (cnpj_limpo, "cnpj")
        info = (_lead_duplicado_info(pool, ctx["conta_id"], doc, empresa, campo) if doc
                else {"msg": f"“{empresa}” já está cadastrado — não dá pra duplicar.",
                      "link_url": None, "link_label": None})
        if ajax:
            return JSONResponse({"ok": False, "erro": info["msg"],
                                  "link_url": info["link_url"], "link_label": info["link_label"]},
                                 status_code=409)
        request.session["prosp_aviso"] = info["msg"]
        return RedirectResponse(volta, status_code=303)
    # "entrou na prospecção" não dizia NADA: quem cadastrava pelo funil ia procurar
    # o lead ali e não achava (tinha caído na Base). O aviso agora nomeia o lugar — e,
    # quando é Base, ensina o caminho de volta pro funil.
    msg = (f"“{empresa}” entrou no funil, em {etapa1}." if estagio == "lead" else
           f"“{empresa}” entrou na Base — ainda NÃO aparece no funil. "
           f"Pra jogar no funil: 📇 Base → marque o lead → Promover.")
    if ajax:
        lead = _lead_card(row[0], empresa, segmento.strip(), cidade.strip(),
                          (uf or "").strip()[:2].upper(), temperatura, _reais_para_centavos(valor),
                          _nome_vendedor(pool, ctx["conta_id"], vend) if ctx["gerencia"] else None)
        return JSONResponse({"ok": True, "lead": lead, "estagio": estagio, "msg": msg})
    request.session["prosp_aviso"] = msg
    return RedirectResponse(volta, status_code=303)


# ================================================================ CAPTAÇÃO
def _render_captar(request, ctx, aba="manual", resultados=None, busca=None):
    pool = get_pool()
    vends = _vendedores(pool, ctx["conta_id"]) if ctx["gerencia"] else []
    return _render("prospeccao_captar", request, titulo="Captar leads",
                   secao_ativa="prospeccao", aba=aba, gerencia=ctx["gerencia"], pode_atribuir=ctx["pode_atribuir"],
                   vendedores=vends, temperaturas_all=TEMPERATURAS,
                   tem_places=fontes.tem_chave_places(), resultados=resultados,
                   busca=busca or {}, temp_cor=TEMP_COR,
                   aviso=request.session.pop("prosp_aviso", None))


@router.get("/painel/prospeccao/cnpj")
def cnpj_lookup(request: Request, cnpj: str = ""):
    """Consulta 1 CNPJ na Receita (BrasilAPI) e devolve os dados pro autofill."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    res = fontes.enriquecer_cnpj(cnpj)
    if not res.get("ok"):
        return JSONResponse({"ok": False, "erro": res.get("erro")})
    return JSONResponse({"ok": True, "dados": res["dados"]})


@router.get("/painel/prospeccao/captar", response_class=HTMLResponse)
def captar_pagina(request: Request, aba: str = "manual"):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    aba = aba if aba in ("manual", "csv", "google") else "manual"
    return _render_captar(request, ctx, aba=aba)


@router.post("/painel/prospeccao/captar/csv", response_class=HTMLResponse)
async def captar_csv(request: Request, arquivo: UploadFile = File(...),
                     vendedor_id: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    vend = _vendedor_destino(ctx, vendedor_id, pool, ctx["conta_id"])
    raw = await arquivo.read()
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = raw.decode("latin-1", errors="replace")
    # detecta separador (vírgula ou ponto-e-vírgula)
    amostra = texto[:2000]
    sep = ";" if amostra.count(";") > amostra.count(",") else ","
    leitor = _csv.DictReader(io.StringIO(texto), delimiter=sep)
    mapa = {"empresa": ["empresa", "nome", "razao_social", "razão social", "fantasia"],
            "telefone": ["telefone", "fone", "tel"], "whatsapp": ["whatsapp", "zap", "celular"],
            "cidade": ["cidade", "municipio", "município"], "uf": ["uf", "estado"],
            "segmento": ["segmento", "ramo", "categoria"], "contato": ["contato", "responsavel", "responsável"],
            "email": ["email", "e-mail"], "cnpj": ["cnpj"],
            "cpf": ["cpf", "documento", "doc"]}
    inseridos, pulados, repetidos = 0, 0, 0
    with pool.connection() as c:
        for linha in leitor:
            norm = {(k or "").strip().lower(): (v or "").strip() for k, v in linha.items() if k}
            def pega(campo):
                for h in mapa[campo]:
                    if norm.get(h):
                        return norm[h]
                return ""
            empresa = pega("empresa")
            if not empresa:
                # sem cabeçalho reconhecível: usa a 1ª coluna como empresa
                empresa = next((v for v in norm.values() if v), "")
            if not empresa:
                pulados += 1
                continue
            # a planilha pode trazer CPF, CNPJ ou uma coluna "documento" solta — o
            # tamanho decide a coluna. Documento inválido não derruba a linha: entra
            # sem documento, que é melhor que perder o lead inteiro na importação.
            _tp, _cnpj, _cpf, _erro = _doc_lead("", pega("cnpj"), pega("cpf"))
            if _erro:
                _tp, _cnpj, _cpf = "pj", None, None
            # cada linha num savepoint: documento repetido (o índice único de CNPJ, e
            # agora o de CPF) derrubava a transação inteira e a importação voltava
            # ZERO leads por causa de uma linha. Agora só aquela linha cai.
            try:
                with c.transaction():
                    c.execute(
                        """insert into prospeccao (conta_id, vendedor_id, empresa, segmento,
                             cidade, uf, contato, telefone, whatsapp, email, cnpj, temperatura,
                             origem, criado_por, tipo, cpf)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'frio','csv',%s,%s,%s)""",
                        (ctx["conta_id"], vend, empresa[:250], pega("segmento") or None,
                         pega("cidade") or None, pega("uf")[:2].upper() or None,
                         pega("contato") or None, pega("telefone") or None, pega("whatsapp") or None,
                         (pega("email").lower() or None), _cnpj, ctx["membro_id"], _tp, _cpf))
            except UniqueViolation:
                repetidos += 1
                continue
            inseridos += 1
        c.commit()
    msg = (f"{inseridos} lead(s) importado(s) do CSV."
           + (f" {pulados} linha(s) sem nome ignorada(s)." if pulados else "")
           + (f" {repetidos} com documento já cadastrado." if repetidos else ""))
    if _eh_ajax(request):
        return JSONResponse({"ok": True, "inseridos": inseridos, "pulados": pulados,
                             "repetidos": repetidos, "msg": msg})
    request.session["prosp_aviso"] = msg
    return RedirectResponse("/painel/prospeccao", status_code=303)


@router.post("/painel/prospeccao/captar/buscar", response_class=HTMLResponse)
def captar_buscar(request: Request, segmento: str = Form(...), cidade: str = Form(""),
                  bairro: str = Form(""), rua: str = Form(""),
                  esconder_redes: str = Form(""), lat: str = Form(""), lng: str = Form(""),
                  raio_km: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    # cercar no mapa (lat/lng/raio) SUBSTITUI cidade/bairro/rua — a área desenhada
    # já é a restrição geográfica, não faz sentido combinar com texto de região.
    lat_f = lng_f = raio_f = None
    try:
        if lat and lng:
            lat_f, lng_f = float(lat), float(lng)
            raio_f = float(raio_km) if raio_km else 3.0
    except ValueError:
        lat_f = lng_f = raio_f = None
    if lat_f is not None:
        cidade = bairro = rua = ""
    res = fontes.buscar_places(segmento, cidade, bairro=bairro, rua=rua,
                               lat=lat_f, lng=lng_f, raio_km=raio_f)
    itens = res.get("itens", [])
    esconder = esconder_redes == "1"
    if esconder:
        itens = [i for i in itens if not i["rede"]]
    for i in itens:
        i["pack"] = _pack({"e": i["empresa"], "t": i["telefone"], "c": i.get("cidade") or cidade.strip(),
                           "u": i.get("uf") or "", "sg": i.get("segmento") or "",
                           "p": i["place_id"], "s": 1 if i["tem_site"] else 0,
                           "tp": i["temperatura"], "en": i["endereco"],
                           "r": i.get("rating"), "n": i.get("avaliacoes"),
                           "m": i.get("maps_uri") or "", "st": i.get("status") or "",
                           "w": i.get("site") or ""})
    _marcar_duplicados(itens, ctx["conta_id"])
    n_redes = sum(1 for x in res.get("itens", []) if x["rede"]) if esconder else 0
    busca = {"segmento": segmento, "cidade": cidade, "bairro": bairro, "rua": rua,
             "esconder": esconder, "ok": res.get("ok"), "erro": res.get("erro"), "n_redes": n_redes,
             "lat": lat_f, "lng": lng_f, "raio_km": raio_f}
    if _eh_ajax(request):
        enxuto = [{"empresa": i["empresa"], "telefone": i["telefone"], "rating": i.get("rating"),
                   "tem_site": i["tem_site"], "endereco": i["endereco"],
                   "segmento": i.get("segmento") or "", "cidade": i.get("cidade") or "",
                   "uf": i.get("uf") or "", "aberto": i.get("aberto", True),
                   "temperatura": i["temperatura"], "pack": i["pack"],
                   "dup": i.get("dup", False), "dup_campanha": i.get("dup_campanha", "")} for i in itens]
        return JSONResponse({"ok": res.get("ok"), "erro": res.get("erro"),
                             "itens": enxuto, "n_redes": n_redes})
    return _render_captar(request, ctx, aba="google", resultados=itens, busca=busca)


def _marcar_duplicados(itens: list, conta_id: int) -> None:
    """Cruza os resultados recém-buscados (Google Maps) com a base da conta ANTES
    de importar — por place_id ou telefone. Marca em cada item se já existe
    (`dup`) e, principalmente, se já está numa campanha (`dup_campanha` = nome
    dela) — pra decidir na hora de olhar a lista, sem descobrir só depois."""
    place_ids = [i["place_id"] for i in itens if i.get("place_id")]
    fones = list({_so_digitos(i.get("telefone") or "") for i in itens if _so_digitos(i.get("telefone") or "")})
    if not place_ids and not fones:
        return
    dup_by_place, dup_by_fone = {}, {}
    with get_pool().connection() as c:
        rows = c.execute(
            """select p.place_id, regexp_replace(coalesce(p.telefone,''), '\\D', '', 'g'), cp.nome
                 from prospeccao p
                 left join lateral (
                    select camp.nome from campanha_alvos a join campanhas camp on camp.id=a.campanha_id
                     where a.prospeccao_id=p.id order by a.id desc limit 1
                 ) cp on true
                where p.conta_id=%s and (p.place_id = any(%s) or
                      regexp_replace(coalesce(p.telefone,''), '\\D', '', 'g') = any(%s))""",
            (conta_id, place_ids or [""], fones or [""])).fetchall()
    for pid_, fone_, camp_nome in rows:
        if pid_:
            dup_by_place[pid_] = camp_nome
        if fone_:
            dup_by_fone[fone_] = camp_nome
    for i in itens:
        fone_norm = _so_digitos(i.get("telefone") or "")
        existe_place = i.get("place_id") in dup_by_place
        existe_fone = bool(fone_norm) and fone_norm in dup_by_fone
        i["dup"] = existe_place or existe_fone
        i["dup_campanha"] = (dup_by_place.get(i.get("place_id")) if existe_place else None) \
            or (dup_by_fone.get(fone_norm) if existe_fone else None) or ""


@router.post("/painel/prospeccao/captar/importar")
async def captar_importar(request: Request):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    form = await request.form()
    escolhidos = form.getlist("itens")
    vend = _vendedor_destino(ctx, form.get("vendedor_id", ""), pool, ctx["conta_id"])
    nome_vend = _nome_vendedor(pool, ctx["conta_id"], vend) if ctx["gerencia"] else None
    inseridos, dup, leads = 0, 0, []
    with pool.connection() as c:
        for token in escolhidos:
            d = _unpack(token)
            if not d or not d.get("e"):
                continue
            pid = d.get("p") or None
            if pid and c.execute("select 1 from prospeccao where conta_id=%s and place_id=%s",
                                 (ctx["conta_id"], pid)).fetchone():
                dup += 1
                continue
            partes = []
            if d.get("en"):
                partes.append(d["en"])
            if d.get("r"):
                partes.append(f"nota {d['r']} ({d.get('n', 0)} aval.)")
            if d.get("st") and d["st"] != "OPERATIONAL":
                partes.append("status: " + d["st"])
            obs = " · ".join(partes)   # link do mapa vai na coluna própria, não na obs
            temp = d.get("tp") if d.get("tp") in TEMP_OK else "frio"
            row = c.execute(
                """insert into prospeccao (conta_id, vendedor_id, empresa, segmento, cidade,
                     uf, telefone, temperatura, tem_site, place_id, origem, obs, maps_url, site_url, criado_por)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'google_places',%s,%s,%s,%s) returning id""",
                (ctx["conta_id"], vend, d["e"][:250], d.get("sg") or None, d.get("c") or None,
                 (d.get("u") or "")[:2].upper() or None, d.get("t") or None, temp,
                 bool(d.get("s")), pid, obs or None, d.get("m") or None, d.get("w") or None,
                 ctx["membro_id"])).fetchone()
            leads.append(_lead_card(row[0], d["e"][:250], d.get("sg") or "", d.get("c") or "",
                                    (d.get("u") or "")[:2].upper(), temp, 0, nome_vend))
            inseridos += 1
        c.commit()
    msg = f"{inseridos} lead(s) adicionado(s) do Google." + (f" {dup} já existia(m)." if dup else "")
    if _eh_ajax(request):
        return JSONResponse({"ok": True, "inseridos": inseridos, "dup": dup, "leads": leads, "msg": msg})
    request.session["prosp_aviso"] = msg
    return RedirectResponse("/painel/prospeccao", status_code=303)


# ================================================================ COMUNICAÇÃO (inbox)
_CANAIS_COMM = ("email", "whatsapp")


def _preview(descricao: str) -> str:
    """Prévia curta da mensagem: pega o corpo (depois do cabeçalho) e a 1ª linha."""
    corpo = (descricao or "").split("\n\n", 1)
    txt = corpo[1] if len(corpo) > 1 else corpo[0]
    return " ".join(txt.split())[:90]


CANAL_ROT = {"email": "✉️ E-mail", "whatsapp": "💬 WhatsApp",
             "messenger": "🔵 Messenger", "instagram": "📸 Instagram"}
CANAIS_TODOS = ("email", "whatsapp", "messenger", "instagram")
_QR_ROT = {"conectado": "✅ Conectado! Já pode captar leads por aqui.",
           "aguardando_qr": "📱 Escaneie o QR no WhatsApp do celular (Aparelhos conectados › Conectar aparelho).",
           "reconectando": "🔄 Reconectando…",
           "desconectado": "Desconectado. Clique em Gerar QR."}


def _canal_ident(c, conta_id, canal):
    """Identificador (número/página) do canal daquela empresa, se configurado."""
    r = c.execute("select identificador from canais_config where conta_id=%s and canal=%s and ativo",
                  (conta_id, canal)).fetchone()
    return r[0] if r else None


def _conta_por_ident(c, canal, ident_digitos):
    """Roteia o inbound: acha a empresa dona do número que recebeu (últimos 11 díg)."""
    r = c.execute(
        r"""select conta_id from canais_config
             where canal=%s and ativo
               and right(regexp_replace(identificador,'\D','','g'), 11) = right(%s, 11)
             limit 1""", (canal, ident_digitos)).fetchone()
    return r[0] if r else None


def _meta_token_valido(tok) -> bool:
    """Token real da Meta: 'EAA...' (Page token, login do Facebook) ou 'IGAA/IGQ...'
    (token do Instagram, login do Instagram). Serve pra não mostrar 'Conectado' com
    um valor de teste/placeholder."""
    t = (tok or "").strip()
    return t.startswith(("EAA", "IGAA", "IGQ")) and len(t) >= 50


def _canais_status(pool, conta_id: int) -> dict:
    """Status de cada canal: credencial global + número/página da empresa (banco)."""
    twilio = bool(os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN"))
    meta_app = bool(os.environ.get("META_APP_SECRET") and os.environ.get("META_VERIFY_TOKEN"))
    nums = {}
    email_senha = None
    tokens = {}
    provs = {}
    wa_phone = {}
    tmpls = {"convite": "", "lembrete": ""}
    with pool.connection() as c:
        for (canal, ident, tok, prov, waid, t_conv, t_lemb) in c.execute(
                """select canal, identificador, token, coalesce(provedor,'twilio'), wa_phone_id,
                          tmpl_convite_sid, tmpl_lembrete_sid
                     from canais_config where conta_id=%s and ativo""",
                (conta_id,)).fetchall():
            nums[canal] = ident
            tokens[canal] = tok
            provs[canal] = prov
            wa_phone[canal] = waid
            if canal == "whatsapp":
                tmpls = {"convite": t_conv or "", "lembrete": t_lemb or ""}
        er = c.execute(
            "select imap_senha from canais_config where conta_id=%s and canal='email'",
            (conta_id,)).fetchone()
        email_senha = er[0] if er else None
        er2 = c.execute(
            "select imap_senha from canais_config where conta_id=%s and canal='email2'",
            (conta_id,)).fetchone()
        email2_senha = er2[0] if er2 else None
    email_ident = nums.get("email")
    email2_ident = nums.get("email2")
    email2_rx = bool(email2_ident) and bool(email2_senha)
    # RECEBER precisa de endereço + senha (própria no banco, ou a do env quando é a mesma caixa do SMTP)
    env_ok = bool(email_ident) and (email_ident.strip().lower()
             == (os.environ.get("SMTP_USER") or "").strip().lower()) and bool(os.environ.get("SMTP_SENHA"))
    email_rx = bool(email_ident) and (bool(email_senha) or env_ok)
    # Messenger/Instagram: precisa do app (env) + Página/IG id + Page Token VÁLIDO (por conta)
    messenger_ok = meta_app and bool(nums.get("messenger")) and _meta_token_valido(tokens.get("messenger"))
    instagram_ok = meta_app and bool(nums.get("instagram")) and _meta_token_valido(tokens.get("instagram"))
    # token preenchido mas com cara de inválido (ex.: placeholder) → alerta em vez de "Conectado"
    msg_tok_ruim = bool(tokens.get("messenger")) and not _meta_token_valido(tokens.get("messenger"))
    ig_tok_ruim = bool(tokens.get("instagram")) and not _meta_token_valido(tokens.get("instagram"))
    # WhatsApp: por provedor — Cloud API (número próprio) precisa de phone_id + token +
    # app da Meta; Twilio precisa das credenciais globais + número da empresa.
    wa_prov = provs.get("whatsapp") or "twilio"
    if wa_prov == "cloud":
        whatsapp_ok = meta_app and bool(wa_phone.get("whatsapp")) and _meta_token_valido(tokens.get("whatsapp"))
    elif wa_prov == "qr":
        from finance import whatsapp_qr as _wq
        whatsapp_ok = _wq.configurado()   # serviço ligado; a sessão em si aparece na aba QR
    else:
        whatsapp_ok = twilio and bool(nums.get("whatsapp"))
    from finance import email_inbound as _ein
    rem_conta = _ein.remetente_conta(pool, conta_id)     # caixa da EMPRESA (ou global)
    # última mensagem RECEBIDA por canal — prova de que o inbound está chegando
    with pool.connection() as c:
        ult_in = dict(c.execute(
            """select cv.canal, to_char(max(m.criado_em) - interval '3 hours','DD/MM HH24:MI')
                 from conversas cv join mensagens m on m.conversa_id=cv.id
                where cv.conta_id=%s and m.direcao='in'
                group by cv.canal""", (conta_id,)).fetchall())
    return {
        "ult_in": ult_in,
        # o carimbo sozinho não acusa nada: "15/08 13:28" só vira sintoma quando se
        # sabe que já são três horas atrás. Aqui a conta é feita e fica escrita.
        "wa_sem_receber": _ha_quanto(_wa_minutos_sem_receber(conta_id)),
        "email": bool(rem_conta),                         # ENVIAR (caixa da empresa/global)
        "email_remetente": rem_conta or "",               # o e-mail que vai no From
        "email_rx": email_rx,                             # RECEBER (caixa da conta)
        "email_ident": email_ident or "",
        "email2": bool(email2_ident), "email2_ident": email2_ident or "", "email2_rx": email2_rx,
        "whatsapp": whatsapp_ok,
        "wa_provedor": wa_prov,
        "wa_phone_set": bool(wa_phone.get("whatsapp")),
        "wa_phone_id": wa_phone.get("whatsapp") or "",
        "tmpl_convite": tmpls["convite"], "tmpl_lembrete": tmpls["lembrete"],
        "messenger": messenger_ok,
        "instagram": instagram_ok,
        "msg_tok_ruim": msg_tok_ruim, "ig_tok_ruim": ig_tok_ruim,
        "twilio": twilio, "meta": meta_app, "numeros": nums,
        "tokens_set": {k: bool(v) for k, v in tokens.items()},
        # os chips de WhatsApp desta empresa (migração 171). Numa empresa de um chip
        # só — todas hoje — a lista tem um item, e a tela desenha o bloco de sempre.
        "chips": _chips_para_tela(pool, conta_id),
    }


def _chips_para_tela(pool, conta_id: int) -> list[dict]:
    """`chips_da_conta` com o pool já aberto, tolerante a falha.

    Best-effort de propósito: um erro aqui não pode derrubar a aba de Canais
    inteira. Sem a lista, a tela cai no bloco de um chip só — que é o de hoje.
    """
    try:
        with pool.connection() as c:
            return chips_da_conta(c, conta_id)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("prospeccao.chips").warning(
            "não deu pra listar os chips da conta %s", conta_id, exc_info=True)
        return []


_AGENTE_PADRAO = {"ativo": False, "limiar_confianca": 80, "horario": "comercial",
                  "tom": "informal", "max_trocas": 20, "escalar_para": "dono_lead",
                  "pode_responder": True, "pode_qualificar": True, "pode_agendar": True,
                  "pode_orcamento": True, "orcamento_proativo": False}


def _agente_config(c, conta_id: int) -> dict:
    """Config do agente da empresa (defaults se ainda não salvou)."""
    r = c.execute(
        """select ativo, limiar_confianca, horario, tom, max_trocas, escalar_para,
                  pode_responder, pode_qualificar, pode_agendar, pode_orcamento, orcamento_proativo
             from agente_config where conta_id=%s""", (conta_id,)).fetchone()
    if not r:
        return dict(_AGENTE_PADRAO)
    ks = ["ativo", "limiar_confianca", "horario", "tom", "max_trocas", "escalar_para",
          "pode_responder", "pode_qualificar", "pode_agendar", "pode_orcamento", "orcamento_proativo"]
    return dict(zip(ks, r))


def _agente_conhecimento(c, conta_id: int) -> dict:
    """Base de conhecimento: {instrucoes: texto, faqs: [{id, pergunta, resposta}]}."""
    rows = c.execute(
        "select id, tipo, pergunta, resposta from agente_conhecimento where conta_id=%s order by ordem, id",
        (conta_id,)).fetchall()
    instr, faqs = "", []
    for (id_, tipo, perg, resp) in rows:
        if tipo == "instrucoes":
            instr = resp or ""
        else:
            faqs.append({"id": id_, "pergunta": perg or "", "resposta": resp or ""})
    return {"instrucoes": instr, "faqs": faqs}


# Dobra de acento feita na MÃO, dos dois lados da comparação.
#
# O projeto não tem a extensão `unaccent` ligada no Postgres — está escrito em
# finance/livro_caixa.py, e é o mesmo motivo pelo qual o casamento de produto de
# cupom também dobra na mão. Sem isto, quem digita "jacque" no celular (ninguém põe
# acento no celular) não acha "Jacqueline", e quem digita "joao" não acha "João" —
# que é justamente o nome que veio do perfil do WhatsApp, onde acento tem de sobra.
_ACENTOS = "áàâãäéèêëíìîïóòôõöúùûüçñ"
_SEM_ACENTO = "aaaaaeeeeiiiiooooouuuucn"


def _dobrar(col: str) -> str:
    """A expressão SQL que devolve a coluna em minúscula e sem acento."""
    return f"translate(lower(coalesce({col},'')), '{_ACENTOS}', '{_SEM_ACENTO}')"


def _termo_dobrado(termo: str) -> str:
    return (termo or "").lower().translate(str.maketrans(_ACENTOS, _SEM_ACENTO))


# Quantos dígitos um termo precisa ter pra valer como busca de NÚMERO. Abaixo disso
# é ruído: "ana 2" tem um dígito e continua sendo busca de nome.
_MIN_DIGITOS = 4


def _conversas_onde(conta_id, gerencia, membro_id, canal="", vend="", escopo="",
                    busca=""):
    """O recorte da caixa, em uma função só — a lista e a CONTAGEM têm que enxergar
    exatamente o mesmo conjunto.

    Separado porque a contagem "100 de 264" só é verdade se os dois lados aplicarem
    o mesmo where; duas cópias da regra viram dois números que se contradizem na
    mesma linha da tela."""
    where = ["cv.conta_id=%s"]
    params = [conta_id]
    if not gerencia:
        where.append("p.vendedor_id=%s")
        params.append(membro_id)
    elif (vend or "").isdigit():   # filtro por vendedor (só dono/gestor)
        where.append("p.vendedor_id=%s")
        params.append(int(vend))
    elif vend == "sem":
        # os leads órfãos, que é o que a gestão quer caçar. Exige `cv.prospeccao_id
        # not null` de propósito: conversa que ainda não virou lead também tem
        # vendedor_id nulo (por não ter lead nenhum) e entupiria o filtro — na conta
        # do chamado seriam 158 linhas irrelevantes no meio de 21 que importam.
        where.append("cv.prospeccao_id is not null and p.vendedor_id is null")
    if escopo == "email":
        where.append("cv.canal='email'")
    elif canal in CANAIS_TODOS and canal != "email":
        where.append("cv.canal=%s")
        params.append(canal)
    elif escopo == "msg":
        where.append("cv.canal <> 'email'")
    # --------------------------------------------------------------- a busca
    #
    # POR QUE ELA VEM PRO SERVIDOR, e não filtra o que já está na tela: a lista sai
    # ordenada por `ultima_msg_em desc limit 100`. A Prime tem 264 conversas e o
    # Rawilson 254 — filtrar as 100 carregadas acharia só o que já dava pra ver
    # rolando, e as 164 que faltam são justamente as que se procura. Metade delas
    # (139 na Prime) nem tem lead vinculado: aparecem pelo nome do perfil do
    # WhatsApp, ou pelo número cru quando nem isso veio.
    termo = (busca or "").strip()[:60]
    if termo:
        dig = re.sub(r"\D", "", termo)
        alvos = []
        # NÚMERO. A mesma pessoa é gravada de quatro jeitos — 86995167171,
        # 5586995167171, com e sem o nono dígito — e o painel já resolveu isto uma
        # vez: casa pelos 8 ÚLTIMOS dígitos (ver _conversa_wa_do_contato). A partir
        # de 8 dígitos a comparação é de igualdade, que é o que o índice
        # idx_conversas_num8 (conta_id, canal, right(regexp_replace(...), 8))
        # enxerga; abaixo disso vira sufixo, que varre — e varrer 264 linhas de uma
        # conta é barato, varrer com LIKE '%x%' no lugar errado é que não seria.
        if len(dig) >= _MIN_DIGITOS:
            fim = dig[-8:]
            comp = "= %s" if len(dig) >= 8 else "like %s"
            valor = fim if len(dig) >= 8 else "%" + fim
            for col in ("cv.contato_ref", "p.whatsapp", "p.telefone"):
                alvos.append(
                    f"right(regexp_replace(coalesce({col},''), '\\D', '', 'g'), 8) {comp}")
                params.append(valor)
        # NOME. Três campos porque é em três lugares que o nome pode estar: a ficha
        # do lead, o nome do perfil do WhatsApp (conversa sem lead) e o contato da
        # ficha. Procurar em só um deixa metade da caixa inalcançável.
        alvo = "%" + _termo_dobrado(termo) + "%"
        for col in ("p.empresa", "cv.contato_nome", "p.contato"):
            alvos.append(f"{_dobrar(col)} like %s")
            params.append(alvo)
        where.append("(" + " or ".join(alvos) + ")")
    return where, params


def _conversas_total(c, conta_id, gerencia, membro_id, canal="", vend="", escopo=""):
    """Quantas conversas o recorte tem AO TODO — sem busca e sem o limite de 100.

    É a segunda metade de "100 de 264". Sem ela a tela dizia só "100 conversa(s)", o
    que é verdade e esconde o que importa: que existem 164 fora do alcance."""
    where, params = _conversas_onde(conta_id, gerencia, membro_id, canal, vend, escopo)
    return int(c.execute(
        f"""select count(*) from conversas cv
              left join prospeccao p on p.id = cv.prospeccao_id
             where {' and '.join(where)}""", tuple(params)).fetchone()[0])


def _conversas_list(c, conta_id, gerencia, membro_id, canal="", vend="", escopo="",
                    busca=""):
    """Lista de conversas (topo por última msg) — usada na página e no polling.
    escopo: 'email' = só e-mail (aba E-mails); 'msg' = só mensageiros (aba Conversas).
    busca: nome ou número; vazio = a caixa inteira, como sempre foi."""
    where, params = _conversas_onde(conta_id, gerencia, membro_id, canal, vend, escopo,
                                    busca)
    rows = c.execute(f"""
        select cv.id, cv.canal, cv.status, cv.ultima_msg_em, cv.prospeccao_id,
               -- sem lead vinculado, mostra o nome do perfil do WhatsApp em vez do
               -- número cru; o número continua visível no cabeçalho da conversa.
               coalesce(p.empresa, nullif(cv.contato_nome,''), cv.contato_ref, '—'),
               p.cidade, p.uf,
               lm.texto, lm.autor, lm.membro_id, mm.nome, cnt.n, lm.id,
               -- o DONO do lead (vm), que não é quem falou por último (mm): a lista
               -- mostrava só o segundo, e os dois se confundem quando o vendedor
               -- responde por último.
               p.vendedor_id, vm.nome,
               -- por qual CHIP a conversa entrou. Nulo = o chip principal, que é todo
               -- o histórico; aí vale o apelido gravado no canal da própria empresa.
               cv.chip_id,
               coalesce(nullif(btrim(chp.nome),''), nullif(btrim(cc1.rotulo),''), ''),
               -- o número CRU: a etiqueta que explica por que a linha apareceu numa
               -- busca por número. Só a busca por número a mostra — fora dela seria
               -- a mesma informação repetida em 100 linhas.
               coalesce(nullif(cv.contato_ref,''), p.whatsapp, p.telefone, '')
          from conversas cv
          left join contas chp on chp.id = cv.chip_id
          left join canais_config cc1 on cv.chip_id is null and cc1.conta_id = cv.conta_id
                                     and cc1.canal='whatsapp' 
          left join prospeccao p on p.id = cv.prospeccao_id
          left join lateral (select id, texto, autor, membro_id from mensagens
                              where conversa_id=cv.id order by criado_em desc limit 1) lm on true
          left join membros mm on mm.id = lm.membro_id
          left join membros vm on vm.id = p.vendedor_id
          join lateral (select count(*) n from mensagens where conversa_id=cv.id) cnt on true
         where {' and '.join(where)}
         order by cv.ultima_msg_em desc limit 100""", tuple(params)).fetchall()
    # a empresa tem mais de um chip? decide se a etiqueta aparece — uma consulta só,
    # fora do laço
    dois_chips = _tem_dois_chips(c, conta_id)
    out = []
    for r in rows:
        if r[9] == "bot":
            quem = "🤖 Agente"
        elif r[9] == "lead":
            quem = r[5]
        elif r[10] and r[10] == membro_id:
            quem = "Você"
        else:
            quem = r[11] or "—"
        out.append({"id": r[0], "canal": r[1], "canal_rot": CANAL_ROT.get(r[1], r[1]),
                    "status": r[2], "quando": r[3], "empresa": r[5], "cidade": r[6],
                    "uf": r[7], "preview": _preview(r[8]), "quem": quem, "n": r[12],
                    "ult_autor": r[9], "ult_msg_id": r[13] or 0,
                    # `eh_lead` decide se a linha ganha marcador de dono. Conversa que
                    # ainda não virou lead não tem responsável nem o que atribuir — e
                    # é a MAIORIA da caixa; marcá-las de "sem responsável" seria
                    # alarme falso em quase toda linha.
                    "eh_lead": r[4] is not None, "lead_id": r[4],
                    "dono_id": r[14], "dono": r[15] or "",
                    # etiqueta do chip: só é preenchida quando a empresa TEM dois.
                    # Numa empresa de um chip só seria a mesma palavra em 100 linhas.
                    "chip_id": r[16], "chip_rot": (r[17] or "") if dois_chips else "",
                    "numero": _so_digitos(r[18] or "")})
    return out


def _leads_sem_dono(c, conta_id, escopo="") -> list[int]:
    """Os leads da caixa que estão sem responsável — a lista, não só a contagem, pra
    a atribuição em lote agir exatamente sobre o que a tela mostrou.

    Conta lead, não conversa: um lead com duas conversas é UM lead sem dono. O
    escopo acompanha a aba (mensageiros × e-mail) pra o número bater com a lista."""
    onde = ["cv.conta_id=%s", "cv.prospeccao_id is not null", "p.vendedor_id is null"]
    args: list = [conta_id]
    if escopo == "email":
        onde.append("cv.canal='email'")
    elif escopo == "msg":
        onde.append("cv.canal <> 'email'")
    rows = c.execute(
        "select distinct p.id from conversas cv "
        "join prospeccao p on p.id = cv.prospeccao_id "
        "where " + " and ".join(onde), tuple(args)).fetchall()
    return [r[0] for r in rows]


_WA_NOME_CACHE: dict = {}


def _wa_nome_conectado(conta_id: int) -> str:
    """Nome do perfil do WhatsApp conectado por QR (o `me.name` que o Baileys
    guarda na credencial). É quem está do outro lado quando a mensagem sai pelo
    celular. Cache de 60s: a thread é consultada a cada 4s por aba aberta e esse
    nome só muda quando o vendedor troca o perfil dele. Tolerante: qualquer erro
    (tabela ausente, credencial sem nome) devolve '' e o inbox cai no genérico."""
    agora = _agora()
    em_cache = _WA_NOME_CACHE.get(conta_id)
    if em_cache and (agora - em_cache[0]).total_seconds() < 60:
        return em_cache[1]
    nome = ""
    try:
        with get_pool().connection() as c:
            r = c.execute("""select conteudo::json->'me'->>'name' from wa_qr_auth
                              where conta_id=%s and arquivo='creds'""", (conta_id,)).fetchone()
        nome = ((r[0] if r else "") or "").strip()[:60]
    except Exception:  # noqa: BLE001
        nome = ""
    _WA_NOME_CACHE[conta_id] = (agora, nome)
    return nome


_WA_SYNC_CACHE: dict = {}   # conta_id -> (quando, {"sincronizando": bool})


def _wa_qr_sincronizando(conta_id) -> bool:
    """A sessão de WhatsApp por QR ainda está importando histórico? Só faz sentido
    pro provedor 'qr'. Com cache de 3s: a lista é consultada a cada 4s por CADA aba
    aberta, e sem isso cada poll viraria uma chamada HTTP ao serviço Node."""
    import time
    agora = time.time()
    hit = _WA_SYNC_CACHE.get(conta_id)
    if hit and (agora - hit[0]) < 3:
        return hit[1]
    sincronizando = False
    try:
        with get_pool().connection() as c:
            qr = c.execute("""select 1 from canais_config
                                where conta_id=%s and canal='whatsapp' and ativo
                                  and coalesce(provedor,'twilio')='qr'""",
                           (conta_id,)).fetchone()
        if qr:
            from finance import whatsapp_qr as _qr
            if _qr.configurado():
                sincronizando = bool(_qr.status(conta_id).get("sincronizando"))
    except Exception:  # noqa: BLE001
        sincronizando = False        # aviso some na dúvida; nunca quebra a lista
    _WA_SYNC_CACHE[conta_id] = (agora, sincronizando)
    return sincronizando


_WA_CHIP_CACHE: dict = {}   # conta_id -> (quando, dict)

# A partir de quantos minutos sem receber vale AVISAR na faixa do chip. Uma hora é
# silêncio comum (almoço, cliente sem assunto); o que a faixa denuncia é o silêncio
# que já não combina com "conectado" — e, com o vigia do serviço religando sessão
# muda em até 45min, silêncio longo aqui virou coisa de conta parada mesmo.
_WA_SILENCIO_MIN = 60


def _wa_minutos_sem_receber(conta_id) -> int | None:
    """Minutos desde a última mensagem RECEBIDA no WhatsApp desta empresa.
    None = nunca recebeu nada (conta nova), que não é silêncio suspeito."""
    with get_pool().connection() as c:
        r = c.execute(
            """select extract(epoch from now() - max(m.criado_em))/60
                 from mensagens m join conversas cv on cv.id=m.conversa_id
                where cv.conta_id=%s and cv.canal='whatsapp' and m.direcao='in'""",
            (conta_id,)).fetchone()
    return int(r[0]) if r and r[0] is not None else None


def _minutos_desde(quando) -> int | None:
    """Minutos entre `quando` e agora — o par de `_ha_quanto`, que espera minutos.

    Tolera data ingênua (sem fuso) tratando-a como UTC: as colunas do banco são
    timestamptz e chegam com fuso, mas teste e importação às vezes passam datetime
    solto, e comparar aware com naive levanta TypeError no meio da tela."""
    if quando is None:
        return None
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - quando).total_seconds() // 60))


def _ha_quanto(minutos) -> str:
    """'há 40min' · 'há 3h' · 'há 2 dias'. '' quando não há silêncio a relatar —
    é o que a tela usa pra decidir se mostra alguma coisa."""
    if minutos is None or minutos < _WA_SILENCIO_MIN:
        return ""
    if minutos < 120:
        return f"há {int(minutos)}min"
    if minutos < 48 * 60:
        return f"há {int(minutos // 60)}h"
    return f"há {int(minutos // 1440)} dias"


def _wa_chip(conta_id) -> dict:
    """Qual chip está enviando o WhatsApp desta empresa, e como ele está.

    O número existia no banco e não aparecia em tela nenhuma: quem manda pelo QR é o
    aparelho da credencial (`wa_qr_auth.creds` → `me.id`), e o `canais_config.
    identificador` guarda só um rótulo interno ('qr:35'). Sem isso, quem dispara
    campanha não sabia de qual número ia sair.

    Devolve {provedor, nome, numero, estado}. `estado`: conectado | sincronizando |
    caido | sem_chip. Cache de 15s — o estado vem de um HTTP ao serviço Node, e o
    cabeçalho é renderizado em toda navegação do painel.
    """
    import time
    agora = time.time()
    hit = _WA_CHIP_CACHE.get(conta_id)
    if hit and (agora - hit[0]) < 15:
        return hit[1]
    chip = {"provedor": "", "nome": "", "numero": "", "estado": "sem_chip", "apelido": ""}
    try:
        with get_pool().connection() as c:
            r = c.execute("""select coalesce(provedor,'twilio'), coalesce(identificador,''),
                                    coalesce(rotulo,'')
                               from canais_config
                              where conta_id=%s and canal='whatsapp' and ativo""",
                          (conta_id,)).fetchone()
            if r:
                chip["provedor"] = r[0]
                # o APELIDO que a pessoa digitou em Canais vence o nome do perfil do
                # WhatsApp. É ele que aparece no inbox e que o relatório agrupa —
                # dois nomes pro mesmo chip em telas diferentes é o começo da confusão
                chip["apelido"] = (r[2] or "").strip()[:60]
                if r[0] == "qr":
                    cred = c.execute(
                        """select conteudo::json->'me'->>'name',
                                  conteudo::json->'me'->>'id'
                             from wa_qr_auth where conta_id=%s and arquivo='creds'""",
                        (conta_id,)).fetchone()
                    if cred:
                        # o nome do perfil vira reserva: vale enquanto ninguém batizou
                        chip["nome"] = chip.get("apelido") or (cred[0] or "").strip()[:60]
                        # "558698392961:14@s.whatsapp.net" → só os dígitos do número
                        chip["numero"] = _tel_fmt_br((cred[1] or "").split(":")[0].split("@")[0])
                else:
                    # twilio/cloud: o identificador É o número da empresa
                    chip["numero"] = _tel_fmt_br(r[1])
        if chip["provedor"] == "qr":
            from finance import whatsapp_qr as _qr
            if _qr.configurado():
                st = (_qr.status(conta_id) or {}).get("status") or ""
                chip["estado"] = ("sincronizando" if _wa_qr_sincronizando(conta_id)
                                  else "conectado" if st == "conectado" else "caido")
            else:
                chip["estado"] = "caido"
        elif chip["provedor"]:
            # na API oficial não existe "sessão que cai": ou o canal está configurado
            # ou não está. Não invento estado que o provedor não reporta.
            chip["estado"] = "conectado" if chip["numero"] else "sem_chip"
        if chip["estado"] == "conectado":
            chip["sem_receber"] = _ha_quanto(_wa_minutos_sem_receber(conta_id))
    except Exception:  # noqa: BLE001
        chip = {"provedor": "", "nome": "", "numero": "", "estado": "sem_chip", "apelido": ""}
    _WA_CHIP_CACHE[conta_id] = (agora, chip)
    return chip


_WA_CHIP2_CACHE: dict = {}   # conta_id -> (quando, dict|None)


def _wa_chip2(conta_id) -> dict | None:
    """O SEGUNDO chip da empresa, no mesmo formato do `_wa_chip` — ou None.

    None é a resposta das 22 contas de hoje, e a faixa desenha uma linha discreta
    dizendo que não há segundo chip. Some seria mais limpo e pior: quem tem dois e vê
    uma linha só não sabe se o outro caiu ou se a tela é que não mostra.

    Cache próprio, de 15s como o do chip 1, porque o estado também vem de um HTTP ao
    serviço Node e o cabeçalho é renderizado em toda navegação.
    """
    import time
    agora = time.time()
    hit = _WA_CHIP2_CACHE.get(conta_id)
    if hit and (agora - hit[0]) < 15:
        return hit[1]
    chip = None
    try:
        with get_pool().connection() as c:
            r = c.execute(
                """select ct.id, coalesce(ct.nome,''),
                          coalesce(cc.ativo,false),
                          (select conteudo::json->'me'->>'id' from wa_qr_auth
                            where conta_id=ct.id and arquivo='creds')
                     from contas ct
                     left join canais_config cc
                            on cc.conta_id=ct.id and cc.canal='whatsapp'
                           and coalesce(cc.provedor,'twilio')='qr'
                    where ct.chip_de=%s
                    order by ct.id limit 1""", (conta_id,)).fetchone()
        if r:
            chip = {"id": r[0], "provedor": "qr", "apelido": (r[1] or "").strip()[:60],
                    "nome": (r[1] or "").strip()[:60],
                    "numero": _tel_fmt_br((r[3] or "").split(":")[0].split("@")[0]) if r[3] else "",
                    "estado": "sem_chip", "sem_receber": ""}
            if not r[2]:
                # canal desligado (recém-criado, ou desconectado pelo painel)
                chip["estado"] = "caido" if chip["numero"] else "sem_chip"
            else:
                from finance import whatsapp_qr as _qr
                if _qr.configurado():
                    st = (_qr.status(r[0]) or {}).get("status") or ""
                    chip["estado"] = "conectado" if st == "conectado" else "caido"
                else:
                    chip["estado"] = "caido"
            if chip["estado"] == "conectado":
                with get_pool().connection() as c:
                    m = c.execute(
                        """select extract(epoch from now() - max(m.criado_em))/60
                             from mensagens m join conversas cv on cv.id=m.conversa_id
                            where coalesce(cv.chip_id, cv.conta_id)=%s
                              and cv.canal='whatsapp' and m.direcao='in'""",
                        (r[0],)).fetchone()
                chip["sem_receber"] = _ha_quanto(int(m[0]) if m and m[0] is not None else None)
    except Exception:  # noqa: BLE001
        chip = None
    _WA_CHIP2_CACHE[conta_id] = (agora, chip)
    return chip


@router.get("/painel/prospeccao/comunicacao/lista")
def prospeccao_comunicacao_lista(request: Request, canal: str = "", vendedor: str = "",
                                 escopo: str = "", q: str = ""):
    """Lista de conversas em JSON (pro polling em tempo real).

    `q` é a busca por nome ou número. Ela entra AQUI, na rota que a caixa já chama de
    4 em 4 segundos, e não numa rota nova: assim o resultado da busca continua vivo
    enquanto a pessoa lê — mensagem que chega numa conversa que casou com o termo
    aparece sozinha, do mesmo jeito que aparece na caixa sem busca."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False}, status_code=401)
    escopo = escopo if escopo in ("email", "msg") else "msg"
    with get_pool().connection() as c:
        convs = _conversas_list(c, ctx["conta_id"], ctx["gerencia"], ctx["membro_id"],
                                canal=canal, vend=vendedor, escopo=escopo, busca=q)
        # o TOTAL é sempre do recorte SEM busca: é ele que dá o "de 264" e que diz,
        # no vazio, quantas conversas foram varridas de verdade
        total = _conversas_total(c, ctx["conta_id"], ctx["gerencia"], ctx["membro_id"],
                                 canal=canal, vend=vendedor, escopo=escopo)
        # ...e sem NENHUM filtro de canal, pra o vazio poder oferecer "buscar em todos
        # os mensageiros" com um número em vez de um palpite
        fora = 0
        if q and canal:
            fora = len(_conversas_list(c, ctx["conta_id"], ctx["gerencia"],
                                       ctx["membro_id"], canal="", vend=vendedor,
                                       escopo=escopo, busca=q))
        # a contagem é da CAIXA, não da página: com o filtro ligado a lista mostra só
        # os órfãos, e a faixa continuaria dizendo o mesmo número — o que esconderia
        # que ainda há outros fora do filtro atual.
        orfaos = len(_leads_sem_dono(c, ctx["conta_id"], escopo)) if ctx["pode_atribuir"] else 0
    for cv in convs:
        cv["quando"] = _hora_br(cv["quando"])
    return JSONResponse({"ok": True, "convs": convs, "sem_dono": orfaos,
                         "total": total, "fora_do_filtro": fora,
                         "sincronizando": _wa_qr_sincronizando(ctx["conta_id"])})


@router.get("/painel/prospeccao/comunicacao", response_class=HTMLResponse)
def prospeccao_comunicacao(request: Request, aba: str = "conversas", canal: str = "",
                           vendedor: str = "", abrir: str = "", q: str = ""):
    """Hub omnichannel: Conversas · E-mails · Agente · Canais (lê de conversas/mensagens)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    aba = aba if aba in ("conversas", "emails", "agente", "canais") else "conversas"
    pool = get_pool()
    filtro_vend = (vendedor or "").strip() if ctx["gerencia"] else ""
    escopo = "email" if aba == "emails" else "msg"
    convs = []
    busca = (q or "").strip()[:60]
    with pool.connection() as c:
        convs = _conversas_list(c, ctx["conta_id"], ctx["gerencia"], ctx["membro_id"],
                                canal=canal, vend=filtro_vend, escopo=escopo,
                                busca=busca)
        # A busca é do JS (campo digitado, sem recarregar). O `q` aqui serve pro
        # RECARREGAR não perder o termo — F5, voltar do navegador, link colado no
        # grupo. Sem ele a página voltava com a lista inteira e o campo cheio,
        # dizendo uma coisa e mostrando outra.
        total_convs = _conversas_total(c, ctx["conta_id"], ctx["gerencia"],
                                       ctx["membro_id"], canal=canal, vend=filtro_vend,
                                       escopo=escopo)
        ag_cfg, ag_conhec = None, None
        dist_cfg, dist_membros, dist_chips, dist_qr = None, [], [], False
        perfil = {"instagram": "", "cargo": "", "material": "", "material_tipo": "link"}
        if aba == "agente":
            ag_cfg = _agente_config(c, ctx["conta_id"])
            ag_conhec = _agente_conhecimento(c, ctx["conta_id"])
            from finance import distribuicao as _dist
            dist_cfg = _dist.config(c, ctx["conta_id"])
            dist_membros = _dist.membros_fila_ui(c, ctx["conta_id"])
            # pra escolher por qual número o aviso sai. Reusa a mesma listagem dos
            # cartões de QR — inclusive o estado, que é o que faz a escolha informada.
            dist_chips = chips_da_conta(c, ctx["conta_id"])
            # QR x Twilio/Meta decide QUAL tela mostrar: no QR o aviso é texto livre e
            # há chip pra escolher; em Twilio/Meta a janela de 24h obriga template e não
            # existe chip nenhum. Pedir template pra quem está no QR — como esta tela
            # fazia — esconde um recurso que já funciona atrás de uma exigência que não
            # é dele.
            dist_qr = (c.execute(
                "select coalesce(provedor,'twilio') from canais_config "
                "where conta_id=%s and canal='whatsapp'", (ctx["conta_id"],)).fetchone()
                or ("twilio",))[0] == "qr"
            c.commit()   # o config() semeia a linha de distribuicao na 1ª vez
            prow = c.execute(
                "select coalesce(prospec_instagram,''), coalesce(prospec_cargo,''), "
                "coalesce(prospec_material,''), coalesce(nullif(prospec_material_tipo,''),'link') "
                "from contas where id=%s",
                (ctx["conta_id"],)).fetchone() or ("", "", "", "link")
            perfil = {"instagram": prow[0], "cargo": prow[1], "material": prow[2],
                      "material_tipo": prow[3]}
    vends = _vendedores(pool, ctx["conta_id"]) if ctx["gerencia"] else []
    return _render("prospeccao_comunicacao", request, titulo="Comunicação",
                   secao_ativa="prospeccao", aba=aba, convs=convs, escopo=escopo, canal=canal,
                   canais=_canais_status(pool, ctx["conta_id"]), canal_rot=CANAL_ROT,
                   gerencia=ctx["gerencia"], vendedores=vends, filtro_vend=filtro_vend,
                   busca=busca, total_convs=total_convs,
                   pode_atribuir=ctx["pode_atribuir"], chip=_wa_chip(ctx["conta_id"]), chip2=_wa_chip2(ctx["conta_id"]),
                   remetente=_ein_remetente(pool, ctx["conta_id"]), tem_ia=_tem_ia(),
                   ag_cfg=ag_cfg, ag_conhec=ag_conhec, perfil=perfil,
                   dist_cfg=dist_cfg, dist_membros=dist_membros, dist_chips=dist_chips,
                   dist_qr=dist_qr,
                   abrir=abrir, embed=request.query_params.get("embed") == "1",
                   aviso=request.session.pop("prosp_aviso", None))


@router.get("/painel/prospeccao/comunicacao/thread/{conversa_id}")
def prospeccao_comunicacao_thread(request: Request, conversa_id: int):
    """Thread de uma conversa: mensagens in/out (read-only nesta fase)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    with pool.connection() as c:
        cv = c.execute(
            """select cv.canal, cv.status, cv.prospeccao_id,
                      coalesce(p.empresa, nullif(cv.contato_nome,'')), p.segmento,
                      p.cidade, p.uf, p.whatsapp, p.telefone, p.email, p.status, m.nome,
                      p.vendedor_id, cv.contato_ref, cv.agente_ativo
                 from conversas cv
                 left join prospeccao p on p.id = cv.prospeccao_id
                 left join membros m on m.id = p.vendedor_id
                where cv.id=%s and cv.conta_id=%s""", (conversa_id, ctx["conta_id"])).fetchone()
        if not cv:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        # conversa sem lead (prospeccao_id null) não tem vendedor — só gerência ou
        # quem já é responsável vê; vendedor comum não acessa conversa órfã.
        if not ctx["gerencia"] and cv[2] is not None and cv[12] != ctx["membro_id"]:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        if not ctx["gerencia"] and cv[2] is None:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        # Só as ÚLTIMAS 100 mensagens: com o histórico importado, uma conversa ativa
        # passa fácil de 500 msgs — carregar tudo (e re-carregar a cada poll de 4s)
        # deixava o painel segundos no "Carregando…" e pesava a renderização.
        rows = c.execute(
            """select canal, direcao, autor, criado_em, texto, membro_id, nome, status, meta
                 from (select msg.canal, msg.direcao, msg.autor, msg.criado_em, msg.texto,
                              msg.membro_id, mm.nome as nome, msg.status, msg.meta, msg.id as mid
                         from mensagens msg left join membros mm on mm.id = msg.membro_id
                        where msg.conversa_id=%s
                        order by msg.criado_em desc, msg.id desc limit 100) ult
                order by criado_em asc, mid asc""", (conversa_id,)).fetchall()
        # o mesmo número em atendimento pelo OUTRO chip. Conversa sem lead não tem
        # gêmeo pra procurar — o casamento é entre fichas, não entre números soltos.
        gemeo = _gemeos_de_outro_chip(c, ctx["conta_id"], [cv[2]]).get(cv[2]) if cv[2] else None
    # Quem mandou, quando saiu do celular e não do Zaq: a mensagem chega pelo eco
    # do Baileys (ou vem do histórico importado) e não tem membro_id — o inbox
    # mostrava só "—". O nome do perfil do WhatsApp conectado é exatamente quem
    # apertou enviar, então é ele que entra nesse lugar.
    nome_celular = _wa_nome_conectado(ctx["conta_id"]) if cv[0] == "whatsapp" else ""
    msgs = []
    for (cn, direcao, autor, quando, texto, mid, nome, mstatus, mmeta) in rows:
        # só e-mail separa assunto (cabeçalho) do corpo; os outros canais são texto puro
        if cn == "email" and "\n\n" in (texto or ""):
            cab, _, corpo = (texto or "").partition("\n\n")
        else:
            cab, corpo = "", (texto or "")
        if autor == "bot":
            quem = "🤖 Agente"
        elif autor == "lead":
            quem = cv[3] or "Lead"
        elif mid:
            quem = "Você" if mid == ctx["membro_id"] else (nome or "—")
        else:
            # saiu pelo celular (ou veio do histórico): sem membro, mas dá pra
            # dizer quem foi — é o dono do WhatsApp conectado
            quem = ("📱 " + nome_celular) if nome_celular else "📱 Pelo celular"
        # por que não chegou — a mesma frase que a ficha do lead já usa
        # (prospec_convite.rotulo_erro_alvo), pra não existirem dois vocabulários
        # pro mesmo erro do mesmo provedor
        erro_rot = ""
        if mstatus == "erro" and isinstance(mmeta, dict):
            from finance.prospec_convite import rotulo_erro_alvo
            erro_rot = rotulo_erro_alvo(mmeta.get("erro_codigo"), mmeta.get("erro_msg"))
        msgs.append({"canal": cn, "direcao": direcao, "autor": autor,
                     "quando": _hora_br(quando),
                     "quem": quem, "cabecalho": cab.strip(), "corpo": corpo.strip(),
                     "status": mstatus or "", "erro": erro_rot})
    destino = cv[7] or cv[8] or cv[13]     # telefone do lead OU contato_ref (conversa sem lead)
    pode_wa = False
    if cv[0] == "whatsapp" and bool(destino):
        from finance import whatsapp_out
        with pool.connection() as _c:
            pode_wa = whatsapp_out.configurado_conta(_c, ctx["conta_id"])
    elif cv[0] == "email":
        _dest_mail = (cv[9] or cv[13] or "")            # e-mail do lead OU do contato órfão
        pode_wa = bool(_ein_remetente(pool, ctx["conta_id"])) and ("@" in _dest_mail)
    elif cv[0] in ("messenger", "instagram"):
        with pool.connection() as _c:
            _t = _c.execute("select token from canais_config where conta_id=%s and canal=%s and ativo",
                            (ctx["conta_id"], cv[0])).fetchone()
        pode_wa = bool(_t and _t[0]) and bool(cv[13])   # token da Página + PSID/IGSID (contato_ref)
    lead = {"id": cv[2], "empresa": cv[3] or cv[13] or "—", "canal": cv[0], "canal_rot": CANAL_ROT.get(cv[0], cv[0]),
            "segmento": cv[4], "cidade": cv[5], "uf": cv[6],
            "whatsapp": destino, "email": cv[9], "vendedor": cv[11],
            # o id vai junto do nome pra caixa poder pré-selecionar quem já é dono
            "vendedor_id": cv[12],
            "status_rot": STATUS_ROT.get(cv[10], cv[10] or "")}
    # Trocar o responsável direto do chat: antes só dava na ficha do lead, e o dono
    # que atende pelo inbox tinha que sair da conversa pra descobrir de quem era.
    # A lista só vai pra quem pode atribuir, e só quando a conversa já é lead.
    vends = ([{"id": v["id"], "nome": v["nome"]}
              for v in _vendedores(pool, ctx["conta_id"])]
             if (ctx["pode_atribuir"] and cv[2]) else [])
    # o aviso do chat é a MESMA frase do funil, com "quando foi a última" no fim: no
    # chat a pergunta prática é se a outra conversa está viva agora ou parada há uma
    # semana. `conversa_id` do gêmeo vai junto pro botão abrir direto lá.
    gemeo_txt = _aviso_gemeo(gemeo)
    if gemeo_txt and gemeo.get("quando"):
        gemeo_txt = gemeo_txt.rstrip(".") + " · última mensagem " + (
            _ha_quanto(_minutos_desde(gemeo["quando"])) or "agora há pouco") + "."
    return JSONResponse({"ok": True, "lead": lead, "msgs": msgs,
                         "gemeo": ({"texto": gemeo_txt,
                                    "conversa_id": (gemeo["conversa_id"]
                                                    if _gemeo_abre(gemeo, ctx) else None)}
                                   if gemeo_txt else None),
                         "conversa_id": conversa_id, "pode_responder": pode_wa,
                         "agente_ativo": bool(cv[14]),
                         "pode_atribuir": bool(ctx["pode_atribuir"] and cv[2]),
                         "vendedores": vends,
                         "truncado": len(msgs) >= 100})


@router.post("/painel/prospeccao/comunicacao/responder")
def comunicacao_responder(request: Request, conversa_id: int = Form(...), texto: str = Form(...)):
    """Responde numa conversa (Fase B: WhatsApp via Twilio, dentro da janela 24h)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    texto = (texto or "").strip()
    if not texto:
        return JSONResponse({"ok": False, "erro": "vazio"})
    pool = get_pool()
    with pool.connection() as c:
        cv = c.execute(
            """select cv.canal, cv.prospeccao_id, cv.contato_ref, p.whatsapp, p.telefone,
                      p.vendedor_id, p.email
                 from conversas cv left join prospeccao p on p.id = cv.prospeccao_id
                where cv.id=%s and cv.conta_id=%s""", (conversa_id, ctx["conta_id"])).fetchone()
        if not cv:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        if not ctx["gerencia"] and cv[5] != ctx["membro_id"]:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        canal = cv[0]
        if canal == "email":
            destino = (cv[6] or "").strip() or (cv[2] or "").strip()   # e-mail do lead OU contato órfão
            if "@" not in destino:
                return JSONResponse({"ok": False, "erro": "Sem e-mail de destino nesta conversa."})
            if not _ein_remetente(pool, ctx["conta_id"]):
                return JSONResponse({"ok": False, "erro": "E-mail não configurado (configure a caixa da empresa na aba Canais)."})
            ult = c.execute("""select texto from mensagens where conversa_id=%s and canal='email'
                                order by criado_em desc limit 1""", (conversa_id,)).fetchone()
            base = (ult[0] or "").split("\n\n", 1)[0].strip() if ult else ""
            base = re.sub(r"(?i)^\s*(re|fwd?):\s*", "", base).strip()
            assunto = ("Re: " + base) if base else (f"Contato · {ctx['conta'][2] or ''}").strip()
            _nome_rem, email_rem = _membro_contato(pool, ctx["conta_id"], ctx["membro_id"])
            html = ("<div style=\"font-family:var(--body);font-size:15px;"
                    "line-height:1.6;color:#222\">"
                    + "".join(f"<p style=\"margin:0 0 12px\">{_html_escape(par)}</p>"
                              for par in texto.split("\n\n")) + "</div>")
            from finance import email_inbound as _ein
            ok = _ein.enviar_conta(pool, ctx["conta_id"], destino, assunto, html, texto_alt=texto,
                                   reply_to=email_rem or None, from_nome=(ctx["conta"][2] or None))
            if not ok:
                return JSONResponse({"ok": False, "erro": "Não consegui enviar o e-mail (confira a caixa da empresa na aba Canais)."})
            _add_msg(c, conversa_id, "email", "out", "humano", f"{assunto}\n\n{texto}", ctx["membro_id"])
            c.commit()
            return JSONResponse({"ok": True})
        if canal in ("messenger", "instagram"):
            from finance import meta_msg
            r = c.execute("select token from canais_config where conta_id=%s and canal=%s and ativo",
                          (ctx["conta_id"], canal)).fetchone()
            res = meta_msg.enviar(r[0] if r else None, (cv[2] or "").strip(), texto, canal)
            if not res.get("ok"):
                return JSONResponse({"ok": False, "erro": "Não consegui enviar (fora da janela de 24h, ou falta conectar a Página/token na aba Canais)."})
            _add_msg(c, conversa_id, canal, "out", "humano", texto, ctx["membro_id"], res.get("sid"))
            c.commit()
            return JSONResponse({"ok": True})
        if canal != "whatsapp":
            return JSONResponse({"ok": False, "erro": "canal_sem_resposta"})
        from finance import whatsapp_out
        numero = cv[3] or cv[4] or cv[2]
        # sai pelo MESMO chip que recebeu — senão o lead escreve pra um número e é
        # respondido por outro, que do lado dele parece outra empresa
        res = whatsapp_out.enviar(
            c, ctx["conta_id"], numero, texto,
            chip_id=whatsapp_out.chip_da_conversa(c, ctx["conta_id"], conversa_id))
        if not res.get("ok"):
            erros = {"nao_configurado": "WhatsApp não conectado (falta credencial Twilio no Render).",
                     "sem_numero_empresa": "Configure o WhatsApp desta empresa na aba Canais.",
                     "numero_invalido": "Número do lead inválido.",
                     "sem_whatsapp": "Esse número não tem WhatsApp. Confira o número na "
                                     "ficha do lead — costuma faltar o 9 do celular.",
                     "qr_indisponivel": "A conexão por QR ainda não está ligada — use Twilio ou Cloud API.",
                     "desconectado": "O WhatsApp está reconectando (normal por ~1 minuto após uma "
                                     "atualização do sistema). Espere alguns segundos e envie de novo — "
                                     "NÃO clique em Desconectar nem escaneie QR."}
            return JSONResponse({"ok": False, "erro": erros.get(res.get("erro"), "Não consegui enviar (janela de 24h fechada? use template).")})
        _add_msg(c, conversa_id, "whatsapp", "out", "humano",
                 texto, ctx["membro_id"], res.get("sid"))
        # vendedor respondeu = assumiu a conversa: pausa o bot (não fala por cima).
        # Volta ao automático clicando "devolver ao agente".
        c.execute("update conversas set status='pendente', agente_ativo=false where id=%s",
                  (conversa_id,))
        c.commit()
    return JSONResponse({"ok": True})


@router.post("/painel/prospeccao/comunicacao/agente-conversa")
def comunicacao_agente_conversa(request: Request, conversa_id: int = Form(...), ativar: str = Form("")):
    """Assumir (desliga o bot nessa conversa) ou devolver ao agente (liga)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    on = ativar == "1"
    # devolver ao agente → 'aberta' (o webhook pode reativar); assumir → 'pendente'
    # (sticky: o webhook respeita e não reativa sozinho).
    novo_status = "aberta" if on else "pendente"
    with get_pool().connection() as c:
        r = c.execute(
            "update conversas set agente_ativo=%s, status=%s where id=%s and conta_id=%s returning id",
            (on, novo_status, conversa_id, ctx["conta_id"])).fetchone()
        c.commit()
    if not r:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
    return JSONResponse({"ok": True, "agente_ativo": on})


@router.post("/painel/prospeccao/comunicacao/email-sync")
def comunicacao_email_sync(request: Request):
    """Puxa os e-mails recebidos da caixa (IMAP) pra dentro do inbox, sob demanda."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    from finance import email_inbound as ein
    pool = get_pool()
    try:
        n = ein.sincronizar(pool, ctx["conta_id"])
    except Exception:  # noqa: BLE001
        n = 0
    if n:
        return JSONResponse({"ok": True, "novos": n})
    # nada novo: roda o diagnóstico pra explicar o porquê (IMAP off? login? spam?)
    try:
        diag = ein.diagnostico(pool, ctx["conta_id"])
    except Exception:  # noqa: BLE001
        diag = {"msg": "Nenhum e-mail novo."}
    return JSONResponse({"ok": True, "novos": 0, "detalhe": diag.get("msg") or "Nenhum e-mail novo."})


@router.post("/painel/prospeccao/comunicacao/email-config")
def comunicacao_email_config(request: Request, endereco: str = Form(...),
                             senha: str = Form(""), host: str = Form(""), slot: str = Form("principal")):
    """Salva uma CAIXA de e-mail (endereço + senha de app) da conta pra ENVIAR/RECEBER.
    slot='principal' → caixa 'email'; slot='secundario' → caixa 'email2'. Só dono/gestor."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    destino = "/painel/prospeccao/comunicacao?aba=canais"
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "Só o dono/gestor configura os canais."
        return RedirectResponse(destino, status_code=303)
    endereco = (endereco or "").strip()
    if "@" not in endereco:
        request.session["prosp_aviso"] = "Informe um e-mail válido."
        return RedirectResponse(destino, status_code=303)
    canal = "email2" if (slot or "").strip().lower() == "secundario" else "email"
    from finance import email_inbound as ein
    try:
        ein.salvar_config(get_pool(), ctx["conta_id"], endereco, senha, host, canal=canal)
        request.session["prosp_aviso"] = "Caixa de e-mail salva ✓ — clique em “Testar conexão”."
    except Exception:  # noqa: BLE001
        request.session["prosp_aviso"] = "Não consegui salvar a caixa de e-mail."
    return RedirectResponse(destino, status_code=303)


@router.post("/painel/prospeccao/comunicacao/email-testar")
def comunicacao_email_testar(request: Request, slot: str = Form("principal")):
    """Testa a conexão IMAP de uma caixa da conta (principal/secundária) e devolve o
    diagnóstico (pro botão)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    canal = "email2" if (slot or "").strip().lower() == "secundario" else "email"
    from finance import email_inbound as ein
    try:
        diag = ein.diagnostico(get_pool(), ctx["conta_id"], canal)
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": "Falha no teste."})
    return JSONResponse({"ok": bool(diag.get("ok")), "msg": diag.get("msg") or ""})


# Campos de segredo que o olhinho pode revelar: slug -> (canal, coluna). É uma
# WHITELIST de propósito — o nome da coluna nunca vem do request (senão viraria
# uma porta pra ler qualquer coluna da tabela).
_SEGREDOS_REVELAVEIS = {
    "email":     ("email",     "imap_senha"),
    "email2":    ("email2",    "imap_senha"),
    "whatsapp":  ("whatsapp",  "token"),
    "messenger": ("messenger", "token"),
    "instagram": ("instagram", "token"),
}


@router.post("/painel/prospeccao/comunicacao/revelar-segredo")
def comunicacao_revelar_segredo(request: Request, campo: str = Form(...)):
    """Devolve a senha/token JÁ SALVO de um canal, pro botão do olhinho — o dono
    precisa conseguir reler o que ele mesmo cadastrou (ex.: reaproveitar a senha
    de app do Gmail no SMTP) sem ter que gerar tudo de novo.

    O valor NÃO vai no HTML da página: a tela renderiza o campo vazio e só busca
    aqui quando o usuário clica no olho. Assim o segredo não fica em toda
    renderização, no cache do navegador nem num print da tela.

    Trancado em três camadas: só dono/gestor (mesmo gate de quem configura o
    canal), sempre escopado no conta_id da sessão (multi-tenant) e restrito à
    whitelist de campos acima. Cada revelação vai pro log — é acesso a
    credencial, tem que deixar rastro."""
    import logging
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "Só o dono/gestor vê as senhas."},
                            status_code=403)
    alvo = _SEGREDOS_REVELAVEIS.get((campo or "").strip().lower())
    if not alvo:
        return JSONResponse({"ok": False, "erro": "Campo desconhecido."}, status_code=400)
    canal, coluna = alvo
    with get_pool().connection() as c:
        r = c.execute(
            f"select {coluna} from canais_config where conta_id=%s and canal=%s",  # noqa: S608
            (ctx["conta_id"], canal)).fetchone()
    valor = (r[0] if r else None) or ""
    logging.getLogger("prospeccao.canais").info(
        "revelar-segredo: conta_id=%s papel=%s campo=%s achou=%s",
        ctx["conta_id"], ctx["papel"], campo, bool(valor))
    if not valor:
        return JSONResponse({"ok": False, "erro": "Nada salvo nesse campo ainda."})
    return JSONResponse({"ok": True, "valor": valor})


@router.post("/painel/prospeccao/comunicacao/whatsapp-testar")
def comunicacao_whatsapp_testar(request: Request, numero: str = Form("")):
    """Manda um WhatsApp de teste pelo Cloud API da conta (confirma phone_id+token)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "Só o dono/gestor testa os canais."}, status_code=403)
    numero = (numero or "").strip()
    if not numero:
        return JSONResponse({"ok": False, "msg": "Informe um número (com DDD) pra receber o teste."})
    with get_pool().connection() as c:
        r = c.execute("""select coalesce(provedor,'twilio'), wa_phone_id, token
                           from canais_config where conta_id=%s and canal='whatsapp' and ativo""",
                      (ctx["conta_id"],)).fetchone()
    if not r or r[0] != "cloud":
        return JSONResponse({"ok": False, "msg": "Salve primeiro o WhatsApp no modo Cloud API (Phone Number ID + token)."})
    if not (r[1] and r[2]):
        return JSONResponse({"ok": False, "msg": "Falta o Phone Number ID ou o token — salve os dois e tente de novo."})
    from finance import whatsapp_cloud as wac
    res = wac.enviar_texto(r[1], r[2], numero,
                           "✅ Teste do ZAQ — se você recebeu esta mensagem, seu WhatsApp "
                           "(Cloud API) está conectado e pronto pra captar leads. 🎉")
    if res.get("ok"):
        return JSONResponse({"ok": True, "msg": "Enviado! Confira o WhatsApp desse número. 📲"})
    det = str(res.get("erro") or "")[:180]
    return JSONResponse({"ok": False, "msg": "Não enviou. A Meta respondeu: " + (det or "erro desconhecido")
                         + " · (token válido? número do destino verificado no painel da Meta?)"})


def _qr_relogio_retencao(conta_id: int, status: str | None) -> None:
    """Zera o marco da retenção quando o QR é OBSERVADO conectado.

    Só aqui, e só com status 'conectado'. A tentação é zerar no
    /whatsapp-qr-iniciar, que é a rota que liga o canal — e seria um furo: o
    painel chama essa rota SOZINHO a cada abertura da tela de Canais
    (qrAutoReconectar), então o dono reiniciaria os 30 dias só por olhar a
    página, com o WhatsApp ainda desconectado. Conectou de verdade é o único
    fato que para o relógio.

    Best-effort: é contabilidade de prazo, não pode derrubar a tela do QR."""
    if (status or "") != "conectado":
        return
    try:
        with get_pool().connection() as c:
            c.execute("""update canais_config set desconectado_em=null
                          where conta_id=%s and canal='whatsapp'
                            and desconectado_em is not null""", (int(conta_id),))
            c.commit()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger("prospeccao.wa_qr").warning(
            "não deu pra zerar o relógio de retenção da conta %s: %s", conta_id, e)


def chips_da_conta(c, conta_id: int) -> list[dict]:
    """Os chips de WhatsApp desta empresa, o principal primeiro.

    O principal é a própria linha da empresa (`chip_de` nulo); os demais são as
    linhas que apontam pra ela. `apelido` é `contas.nome` — na empresa esse campo é
    o nome do titular, que não serve de rótulo de chip, então ali ele vem vazio e a
    tela mostra "Chip 1" até alguém batizar.
    """
    # `ultima` e `sem_receber` são POR CHIP de propósito. Ficavam no topo da aba, no
    # nível da empresa, e com dois chips isso passa a mentir: "última recebida 21:40"
    # não diz em QUAL número, e é justamente essa a pergunta de quem desconfia que um
    # deles parou. `coalesce(cv.chip_id, cv.conta_id)` casa o histórico inteiro no
    # chip principal, que é onde ele sempre esteve.
    linhas = c.execute(
        """select ct.id, coalesce(ct.nome,''), ct.chip_de,
                  coalesce(cc.identificador,''), coalesce(cc.ativo,false),
                  coalesce(cc.rotulo,''),
                  -- mesmo formato do `ult_in` do resto da aba (BRT via -3h, como o
                  -- arquivo já faz), mais os minutos crus pro aviso de silêncio
                  (select to_char(max(m.criado_em) - interval '3 hours','DD/MM HH24:MI')
                     from mensagens m join conversas cv on cv.id = m.conversa_id
                    where coalesce(cv.chip_id, cv.conta_id) = ct.id
                      and cv.canal='whatsapp' and m.direcao='in') ultima,
                  (select extract(epoch from now() - max(m.criado_em))/60
                     from mensagens m join conversas cv on cv.id = m.conversa_id
                    where coalesce(cv.chip_id, cv.conta_id) = ct.id
                      and cv.canal='whatsapp' and m.direcao='in') min_sem
             from contas ct
             left join canais_config cc
                    on cc.conta_id = ct.id and cc.canal='whatsapp'
                   and coalesce(cc.provedor,'twilio')='qr'
            where ct.id = %s or ct.chip_de = %s
            order by (ct.chip_de is not null), ct.id""",
        (conta_id, conta_id)).fetchall()
    saida = []
    for i, (cid, nome, chip_de, ident, ativo, rotulo, ultima, min_sem) in enumerate(linhas):
        principal = chip_de is None
        # o apelido do principal mora no canal (migração 172); o do secundário, em
        # contas.nome da linha dele — ver o cabeçalho da 172 pro porquê
        apelido = (rotulo or "").strip() if principal else (nome or "").strip()
        numero = "" if ident.startswith("qr:") else ident
        saida.append({"id": cid, "principal": principal, "apelido": apelido,
                      "rotulo": apelido or f"Chip {i + 1}",
                      "numero": numero, "ativo": bool(ativo),
                      "pareado": bool(numero),
                      "ultima": ultima or "",
                      "sem_receber": _ha_quanto(int(min_sem) if min_sem is not None else None)})
    return saida


def _tem_dois_chips(c, conta_id: int) -> bool:
    """A empresa tem um segundo chip? Uma linha, por chave estrangeira indexada.

    Vale a chamada porque TODO o aviso cruzado depende dela: com um chip só não existe
    "o mesmo número na outra campanha", e a consulta cara nem chega a rodar. Hoje isso
    é verdade em 21 das 22 contas."""
    return bool(c.execute("select 1 from contas where chip_de=%s limit 1",
                          (conta_id,)).fetchone())


def _gemeos_de_outro_chip(c, conta_id: int, lead_ids) -> dict:
    """O MESMO número sendo atendido por OUTRO chip: `{lead_id: {...}}`.

    Numa empresa de dois números, o mesmo telefone pode estar em duas campanhas — uma
    por chip — e aí são dois leads e duas conversas, de propósito: o que entra pelo
    chip 1 sai pelo chip 1 e o que entra pelo 2 sai pelo 2 (ver a busca de lead em
    `_wa_inbound_conversa`). Separar é o certo; o que faltava era AVISAR, senão dois
    vendedores negociam com a mesma pessoa sem saber um do outro, cada um com um preço.

    Devolve, pra cada lead pedido, o gêmeo MAIS RECENTE do outro chip:
    `{"lead_id", "nome", "conversa_id", "quando", "campanha", "dono", "chip_rot",
    "vendedor_id"}`. O `vendedor_id` é do GÊMEO, e serve pra decidir se o link abre: o
    vendedor comum não enxerga ficha dos outros (`_pode_ver`), então pra ele o aviso
    aparece sem link em vez de com um link que só redireciona.
    Lead sem gêmeo simplesmente não aparece no dicionário — quem chama itera o que veio.

    Empresa de um chip só sai na primeira linha, sem tocar em `prospeccao`: não existe
    "outro chip" pra comparar e a consulta seria varredura à toa em toda tela do funil.

    O casamento é pelos 8 dígitos finais, o mesmo de todo o resto do arquivo (o nono
    dígito aparece ou não conforme quem digitou — ver `_wa_equivalentes`). Aqui ele é
    o final SOZINHO, sem a igualdade exata que a busca de conversa faz por cima: um
    falso positivo aqui custa um aviso a mais numa tela, não uma mensagem no lugar
    errado, e o número aparece no aviso pra pessoa conferir."""
    ids = [int(i) for i in (lead_ids or []) if i]
    if not ids or not _tem_dois_chips(c, conta_id):
        return {}
    try:
        linhas = _gemeos_consulta(c, conta_id, ids)
    except Exception:  # noqa: BLE001
        # o aviso é um extra; o funil, o chat e a resposta do agente não são. Mesmo
        # tratamento do `_chips_para_tela`: registra e devolve vazio, que é a tela de
        # ontem. O SAVEPOINT está lá dentro — sem ele o erro abortaria a transação
        # inteira e derrubaria junto o que vem DEPOIS desta chamada.
        import logging
        logging.getLogger("prospeccao.chips").warning(
            "aviso de chip gêmeo falhou na conta %s", conta_id, exc_info=True)
        return {}
    return {r[0]: {"lead_id": r[1], "nome": r[2], "conversa_id": r[3], "quando": r[4],
                   "campanha": r[5], "dono": r[6], "chip_rot": r[7],
                   "vendedor_id": r[8]} for r in linhas}


def _gemeos_consulta(c, conta_id: int, ids: list):
    """Só a consulta do `_gemeos_de_outro_chip`, num SAVEPOINT. Separada pra deixar a
    função de cima legível — o try/except em volta de 30 linhas de SQL esconde o SQL."""
    with c.transaction():
        return c.execute(
            r"""with meu as (
                  select p.id,
                         right(regexp_replace(coalesce(p.whatsapp, p.telefone, ''), '\D', '', 'g'), 8) as n8,
                         cv.chip_id as chip
                    from prospeccao p
                    left join conversas cv on cv.conta_id = p.conta_id
                                          and cv.prospeccao_id = p.id and cv.canal='whatsapp'
                   where p.conta_id = %s and p.id = any(%s))
                select distinct on (meu.id)
                       meu.id, o.id,
                       coalesce(nullif(btrim(o.contato),''), nullif(btrim(o.empresa),''), ''),
                       cvo.id, cvo.ultima_msg_em,
                       coalesce(cp.nome,''), coalesce(vm.nome,''),
                       coalesce(nullif(btrim(chp.nome),''), nullif(btrim(cc1.rotulo),''), ''),
                       o.vendedor_id
                  from meu
                  join prospeccao o
                    on o.conta_id = %s and o.id <> meu.id and length(meu.n8) = 8
                   and right(regexp_replace(coalesce(o.whatsapp, o.telefone, ''), '\D', '', 'g'), 8) = meu.n8
                  join conversas cvo
                    on cvo.conta_id = o.conta_id and cvo.prospeccao_id = o.id
                   and cvo.canal = 'whatsapp' and cvo.chip_id is distinct from meu.chip
                  left join contas chp on chp.id = cvo.chip_id
                  left join canais_config cc1 on cvo.chip_id is null and cc1.conta_id = cvo.conta_id
                                             and cc1.canal = 'whatsapp'
                  left join membros vm on vm.id = o.vendedor_id
                  left join lateral (
                        select camp.nome from campanha_alvos a
                          join campanhas camp on camp.id = a.campanha_id
                         where a.prospeccao_id = o.id order by a.id desc limit 1) cp on true
                 order by meu.id, cvo.ultima_msg_em desc nulls last, cvo.id desc""",
            (conta_id, ids, conta_id)).fetchall()


def _gemeo_abre(g, ctx) -> bool:
    """Este usuário consegue abrir a ficha/conversa do gêmeo? Mesma régua do
    `_pode_ver`: gerência vê tudo, vendedor vê o que é dele. Sem isto o aviso oferecia
    um "Abrir →" que só redirecionava de volta — pior que não ter link nenhum."""
    if not g:
        return False
    return bool(ctx.get("gerencia")) or (
        g.get("vendedor_id") is not None and g["vendedor_id"] == ctx.get("membro_id"))


def _aviso_gemeo(g: dict) -> str:
    """A frase do aviso, uma só pras três telas (funil, chat e agente).

    Um texto só porque três textos diferentes pra mesma coisa é como o vendedor
    aprende que são coisas diferentes."""
    if not g:
        return ""
    onde = g.get("campanha") or (("chip " + g["chip_rot"]) if g.get("chip_rot") else "outro chip")
    frase = "Este número também está em atendimento na " + onde
    if g.get("dono"):
        frase += " — com " + g["dono"]
    return frase + "."


def _chip_da_conta(c, conta_id: int, chip_id) -> int | None:
    """Valida que `chip_id` é desta empresa e devolve o id — ou None.

    Sem esta volta, um id na querystring mandaria o painel de uma empresa gerar QR,
    desconectar ou renomear o chip de outra. É o mesmo cuidado do `_posse` do cockpit.
    """
    if not chip_id:
        return conta_id
    try:
        chip_id = int(chip_id)
    except (TypeError, ValueError):
        return None
    if chip_id == conta_id:
        return conta_id
    r = c.execute("select 1 from contas where id=%s and chip_de=%s", (chip_id, conta_id)).fetchone()
    return chip_id if r else None


@router.post("/painel/prospeccao/comunicacao/chip-novo")
def comunicacao_chip_novo(request: Request, apelido: str = Form("")):
    """Cria um SEGUNDO chip de WhatsApp nesta empresa.

    A linha nasce em `contas` porque é lá que `wa_qr_auth`, `wa_qr_sessao_lock` e
    `wa_qr_enviadas` apontam — o chip precisa de um id próprio pra ter cofre e trava
    próprios, e é justamente isso que o mantém isolado do chip 1. `chip_de` é o que
    impede essa linha de virar uma empresa de verdade.

    NÃO pareia nada aqui: cria a linha e o canal desligado. O QR é o passo seguinte,
    no botão do cartão — separar os dois é o que deixa criar o chip sem risco nenhum
    pra sessão que está no ar.
    """
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    apelido = " ".join((apelido or "").split())[:60]
    if not apelido:
        return JSONResponse({"ok": False, "msg": "Dê um apelido pro chip (ex.: Agência Beta)."})
    with get_pool().connection() as c:
        # um chip extra por vez: dois cartões vazios na tela é convite pra parear
        # o mesmo número duas vezes
        pend = c.execute("""select 1 from contas ct
                             left join canais_config cc on cc.conta_id=ct.id and cc.canal='whatsapp'
                            where ct.chip_de=%s and coalesce(cc.identificador,'') like 'qr:%%'""",
                         (ctx["conta_id"],)).fetchone()
        if pend:
            return JSONResponse({"ok": False, "msg": "Já existe um chip esperando pareamento. "
                                                    "Leia o QR dele antes de criar outro."})
        tipo = (c.execute("select coalesce(tipo,'pj') from contas where id=%s",
                          (ctx["conta_id"],)).fetchone() or ["pj"])[0]
        chip = c.execute("""insert into contas (tipo, nome, chip_de) values (%s,%s,%s)
                            returning id""", (tipo, apelido, ctx["conta_id"])).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor, ativo)
                     values (%s,'whatsapp',%s,'qr',false)""", (chip, "qr:" + str(chip)))
        c.commit()
    return JSONResponse({"ok": True, "chip": chip, "apelido": apelido})


@router.post("/painel/prospeccao/comunicacao/chip-apelido")
def comunicacao_chip_apelido(request: Request, chip: str = Form(""), apelido: str = Form("")):
    """Renomeia um chip. Vale pro principal também — é o rótulo do relatório.

    No chip principal o apelido é gravado em `canais_config.rotulo`, não em
    `contas.nome`: aquele campo é o nome do titular da conta e aparece em contrato,
    cobrança e e-mail. Trocá-lo por "Agência Alfa" arrumaria a etiqueta do inbox e
    estragaria cinco outros lugares.
    """
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    apelido = " ".join((apelido or "").split())[:60]
    with get_pool().connection() as c:
        alvo = _chip_da_conta(c, ctx["conta_id"], chip)
        if alvo is None:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        if alvo == ctx["conta_id"]:
            c.execute("""update canais_config set rotulo=%s
                          where conta_id=%s and canal='whatsapp'""", (apelido, alvo))
        else:
            c.execute("update contas set nome=%s where id=%s and chip_de=%s",
                      (apelido or "Chip 2", alvo, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True, "apelido": apelido})


@router.post("/painel/prospeccao/comunicacao/whatsapp-qr-iniciar")
def comunicacao_whatsapp_qr_iniciar(request: Request, chip: str = Form("")):
    """Coloca o chip no modo QR e pede o QR ao serviço Node pra exibir/escanear.

    `chip` vazio = o chip principal, que é a própria empresa e o caminho de sempre.
    Com um id de chip secundário, tudo daqui pra baixo age NAQUELE id — canal,
    sessão e relógio de retenção. É o que faz gerar o QR do chip 2 não encostar em
    nada do chip 1: o serviço abre outro socket, com outro cofre e outra trava.
    """
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    from finance import whatsapp_qr as wq
    if not wq.configurado():
        return JSONResponse({"ok": False, "msg": "O serviço de QR ainda não está ligado (falta WA_QR_SERVICE_URL no ambiente)."})
    with get_pool().connection() as c:
        alvo = _chip_da_conta(c, ctx["conta_id"], chip)
        if alvo is None:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        # vira o provedor pra 'qr', mas PRESERVA o identificador de um provedor anterior
        # (ex.: número do Twilio) — só usa o placeholder 'qr:<id>' quando é a 1ª config.
        c.execute(
            """insert into canais_config (conta_id, canal, identificador, provedor, ativo)
               values (%s,'whatsapp',%s,'qr',true)
               on conflict (conta_id, canal)
               do update set provedor='qr', ativo=true, atualizado_em=now()""",
            (alvo, "qr:" + str(alvo)))
        c.commit()
    r = wq.iniciar(alvo)
    _qr_relogio_retencao(alvo, r.get("status"))
    return JSONResponse({"ok": bool(r.get("ok", True) and not r.get("erro")),
                         "status": r.get("status"), "qr": r.get("qr"),
                         # passa reto, INCLUSIVE o None: a tela precisa distinguir
                         # "não tem credencial" de "não deu pra saber" (ver qrShow).
                         "pareada": r.get("pareada"),
                         "sincronizando": bool(r.get("sincronizando")), "sync_progress": r.get("syncProgress") or 0,
                         "msg": _QR_ROT.get(r.get("status"), "") if r.get("status") else (r.get("erro") or "")})


@router.get("/painel/prospeccao/comunicacao/whatsapp-qr-status")
def comunicacao_whatsapp_qr_status(request: Request, chip: str = ""):
    """Consulta o status da sessão QR (pro polling do painel enquanto escaneia).

    Cada cartão da tela faz o próprio polling, no próprio chip — dois status
    independentes, como as duas sessões."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    from finance import whatsapp_qr as wq
    with get_pool().connection() as c:
        alvo = _chip_da_conta(c, ctx["conta_id"], chip)
    if alvo is None:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    r = wq.status(alvo)
    st = r.get("status") or "desconectado"
    _qr_relogio_retencao(alvo, st)
    return JSONResponse({"ok": True, "status": st, "qr": r.get("qr"),
                         "pareada": r.get("pareada"),
                         "sincronizando": bool(r.get("sincronizando")), "sync_progress": r.get("syncProgress") or 0,
                         "msg": _QR_ROT.get(st, "")})


@router.get("/painel/prospeccao/comunicacao/whatsapp-aparelhos")
def comunicacao_whatsapp_aparelhos(request: Request, chip: str = ""):
    """Quantos aparelhos estão ligados neste WhatsApp — e quanto ainda sai por fora.

    Depois que o Cockpit parou de oferecer a saída pro celular, sobrou a pergunta
    que só o dono responde: quem ainda tem o número ligado no aparelho. O sistema
    MOSTRA; desligar é no celular dono da conta, e nenhum sistema faz por ninguém.

    Vêm dois números de fontes diferentes de propósito. Os APARELHOS dizem que a
    porta existe e dependem do WhatsApp responder; a SAÍDA POR FORA sai do banco e
    diz se alguém passa por ela. Um cobre o outro quando a sessão está fora do ar.
    """
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    from finance import cockpit as ck
    # POR CHIP: numa empresa de dois números, um "95% saiu por fora" somado não diz
    # em qual aparelho bater. `chip` vazio = a empresa inteira, o de sempre.
    with get_pool().connection() as _c:
        alvo = _chip_da_conta(_c, ctx["conta_id"], chip)
    if alvo is None:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    fora = ck.saida_por_fora(get_pool(), ctx["conta_id"],
                             chip_id=alvo if chip else None)
    # A CONSULTA AO WHATSAPP SÓ SAI COM `perguntar=1`. Sem isso a rota devolve só o
    # número do banco — que é o que a tela carrega sozinha. Perguntar ao WhatsApp
    # é caro e repetição é o que queima número em cliente não oficial.
    if request.query_params.get("perguntar") != "1":
        return JSONResponse({"ok": True, "aparelhos": None, "fora": fora})
    from finance import whatsapp_qr as wq
    ap = wq.aparelhos(alvo) or {}
    return JSONResponse({"ok": True,
                         "aparelhos": ap if ap.get("ok") else None,
                         "fora": fora})


@router.post("/painel/prospeccao/comunicacao/whatsapp-qr-sair")
def comunicacao_whatsapp_qr_sair(request: Request, chip: str = Form("")):
    """Encerra a sessão QR no serviço Node. NÃO apaga a config do WhatsApp da empresa —
    pra não derrubar o canal sem querer (ela continua no provedor 'qr', desconectada;
    pra trocar de provedor é só usar o seletor de Canais).

    Age no chip informado, e SÓ nele. `wq.sair` chama /session/<chip>/sair, que apaga
    a credencial daquele id — outra linha do cofre, outra trava, outro socket. É por
    isso que o Desconectar de um cartão não alcança o outro nem por engano.
    """
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    from finance import whatsapp_qr as wq
    with get_pool().connection() as c:
        alvo = _chip_da_conta(c, ctx["conta_id"], chip)
    if alvo is None:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    # devolve o resultado DE VERDADE. Antes respondia {"ok": True} sempre, mesmo
    # quando o serviço não respondia — e o painel dizia "Desconectado." com a
    # sessão inteira ainda de pé, credencial e histórico intactos. O usuário ia
    # escanear um QR novo achando que tinha desconectado.
    r = wq.sair(alvo)
    if not r.get("ok"):
        import logging
        logging.getLogger("prospeccao.wa_qr").warning(
            "whatsapp_qr_sair: chip=%s falhou — %s", alvo, r.get("erro"))
        return JSONResponse({"ok": False, "erro": r.get("erro") or "falha"}, status_code=502)
    return JSONResponse({"ok": True})


@router.get("/painel/prospeccao/comunicacao/historico-resumo")
def comunicacao_historico_resumo(request: Request):
    """Quanto histórico de WhatsApp existe, pra o botão de apagar dizer o que vai
    levar ANTES de levar. Confirmação sem número não informa nada."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    from finance import retencao
    try:
        d = retencao.resumo_historico(get_pool(), ctx["conta_id"])
    except Exception as e:  # noqa: BLE001 — a tela de Canais abre de qualquer jeito
        import logging
        logging.getLogger("prospeccao.retencao").warning(
            "historico_resumo: conta_id=%s falhou: %s", ctx["conta_id"], e)
        return JSONResponse({"ok": False, "erro": "indisponivel"}, status_code=502)
    d["ok"] = True
    d["dias_retencao"] = retencao.DIAS_RETENCAO
    return JSONResponse(d)


@router.post("/painel/prospeccao/comunicacao/historico-apagar")
def comunicacao_historico_apagar(request: Request):
    """Apaga o histórico de conversa do WhatsApp desta empresa, a pedido do dono.

    EXIGE O CANAL DESCONECTADO. Não é burocracia: com a sessão de pé o celular
    continua sincronizando e o serviço de QR reescreveria parte do que acabou de
    ser apagado, deixando um resultado pela metade que ninguém entende. Além
    disso, apagar por engano o histórico de um canal EM USO é o pior estrago
    possível nesta tela — e desconectar primeiro é uma barreira deliberada.

    Só dono/gestor (`gerencia`): vendedor não apaga o histórico da empresa."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    import logging
    log = logging.getLogger("prospeccao.retencao")
    from finance import whatsapp_qr as wq
    # pergunta ao SERVIÇO, não ao banco: `canais_config.ativo` é intenção de
    # configuração, e o que importa aqui é se existe sessão viva agora. Quando o
    # serviço não responde, o certo é recusar — apagar às cegas não tem volta.
    try:
        st = (wq.status(ctx["conta_id"]) or {}).get("status") or ""
    except Exception as e:  # noqa: BLE001
        log.warning("historico_apagar: conta_id=%s sem status do serviço: %s",
                    ctx["conta_id"], e)
        return JSONResponse(
            {"ok": False, "erro": "O serviço de QR não respondeu. Tente de novo em "
                                  "instantes — nada foi apagado."}, status_code=502)
    if st and st != "desconectado":
        return JSONResponse(
            {"ok": False, "erro": "Desconecte o WhatsApp antes de apagar o histórico."},
            status_code=409)
    from finance import retencao
    try:
        r = retencao.apagar_historico_whatsapp(get_pool(), ctx["conta_id"])
    except Exception as e:  # noqa: BLE001
        log.warning("historico_apagar: conta_id=%s falhou: %s: %s",
                    ctx["conta_id"], type(e).__name__, e)
        return JSONResponse(
            {"ok": False, "erro": "Não deu pra apagar. Nada foi removido."},
            status_code=500)
    log.warning("historico_apagar: conta_id=%s apagado a pedido do dono — %s", ctx["conta_id"], r)
    r["ok"] = True
    return JSONResponse(r)


def _tel_fmt_br(numero: str) -> str:
    """55869948673 88 -> +55 86 99486-7388, pra o vendedor CONFERIR o número antes de
    virar lead (número cru de 13 dígitos ninguém lê). Fora do formato brasileiro,
    devolve com o + na frente e pronto — não inventa máscara de país que não conhece."""
    d = _so_digitos(numero)
    if not d:
        return ""
    if d.startswith("55") and len(d) in (12, 13):
        ddd, resto = d[2:4], d[4:]
        return f"+55 {ddd} {resto[:-4]}-{resto[-4:]}"
    return "+" + d


def _contato_sugerido(c, conta_id: int, canal: str, ref: str, contato_nome: str) -> tuple[str, str]:
    """(nome, fonte) pra pré-preencher o "Levar para o lead". A escada é a mesma do
    caminho automático (_wa_inbound_conversa): agenda do celular primeiro — foi o
    vendedor que salvou aquele nome —, depois o nome que já ficou guardado na conversa
    (pushName do WhatsApp ou o remetente do e-mail). Sem nenhum dos dois, devolve vazio
    de propósito: o modal abre pedindo o nome em vez de gravar "5586…" como empresa."""
    if canal == "whatsapp":
        nome = _nome_da_agenda(c, conta_id, ref)
        if nome:
            return (nome, "agenda")
    nome = (contato_nome or "").strip()
    if nome:
        return (nome, "email" if canal == "email" else "perfil")
    return ("", "")


def _lead_por_telefone(c, conta_id: int, numero: str):
    """Lead que já usa esse telefone, casando pelos últimos 8 dígitos (mesma chave do
    resto do módulo: ignora o 9 extra e variações de DDI). (id, empresa) ou None."""
    d = _so_digitos(numero)
    if len(d) < 8:
        return None
    return c.execute(
        r"""select id, empresa from prospeccao
             where conta_id=%s and right(regexp_replace(coalesce(whatsapp, telefone, ''), '\D', '', 'g'), 8) = %s
             order by atualizado_em desc limit 1""", (conta_id, d[-8:])).fetchone()


@router.get("/painel/prospeccao/comunicacao/virar-lead/{conversa_id}")
def comunicacao_virar_lead_dados(request: Request, conversa_id: int):
    """O que o modal "Levar para o lead" mostra já preenchido: nome (agenda/perfil),
    telefone formatado, e-mail, e o aviso de que já existe lead com aquele número."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    with pool.connection() as c:
        cv = c.execute(
            "select canal, prospeccao_id, contato_ref, contato_nome from conversas where id=%s and conta_id=%s",
            (conversa_id, ctx["conta_id"])).fetchone()
        if not cv:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        canal, ja_lead, ref, contato_nome = cv[0], cv[1], (cv[2] or "").strip(), cv[3]
        if ja_lead:
            return JSONResponse({"ok": True, "ja_lead": True, "lead_id": ja_lead})
        nome, fonte = _contato_sugerido(c, ctx["conta_id"], canal, ref, contato_nome)
        eh_email = canal == "email" or "@" in ref
        dup = None if eh_email else _lead_por_telefone(c, ctx["conta_id"], ref)
        # deixar o responsável em branco não quer dizer a mesma coisa nos dois casos:
        # com o rodízio ligado, quem decide é a fila; desligado, o lead fica sem dono
        # mesmo. O modal precisa saber pra rotular a opção sem mentir.
        try:
            from finance import distribuicao as _dist
            rodizio_on = bool(_dist.config(c, ctx["conta_id"])["ativo"]) and bool(
                _dist.fila_ids(c, ctx["conta_id"]))
        except Exception:  # noqa: BLE001
            rodizio_on = False
    vends = _vendedores(pool, ctx["conta_id"]) if ctx["pode_atribuir"] else []
    return JSONResponse({
        "ok": True, "ja_lead": False, "canal": canal,
        # quem manda mensagem de WhatsApp/DM é uma pessoa; e-mail de contato@ costuma
        # ser empresa. É só o palpite inicial — a pílula do modal troca em um clique.
        "tipo": "pj" if eh_email else "pf",
        "nome": nome, "nome_fonte": fonte,
        "email": ref if eh_email else "",
        "telefone": "" if eh_email else _tel_fmt_br(ref),
        "pode_atribuir": bool(ctx["pode_atribuir"]),
        "vendedores": [{"id": v["id"], "nome": v["nome"]} for v in vends],
        "rodizio": rodizio_on,
        "duplicado": ({"id": dup[0], "empresa": dup[1]} if dup else None),
    })


@router.post("/painel/prospeccao/comunicacao/atribuir-lote")
def comunicacao_atribuir_lote(request: Request, vendedor_id: str = Form(""),
                              rodizio: str = Form(""), escopo: str = Form("")):
    """Dá dono a todos os leads órfãos da caixa de uma vez — pro vendedor escolhido,
    ou repartindo pela fila do rodízio.

    Atalho, não regra nova: faz em lote o que a troca individual já fazia um por um,
    com o mesmo guard (`pode_atribuir`) e o mesmo escopo de conta. E não trava nada —
    o dono continua podendo trocar qualquer um deles a qualquer momento, na lista, no
    chat ou na ficha."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["pode_atribuir"]:
        return JSONResponse({"ok": False, "erro": "Só o dono atribui."}, status_code=403)
    pool = get_pool()
    conta_id = ctx["conta_id"]
    escopo = escopo if escopo in ("email", "msg") else "msg"
    with pool.connection() as c:
        leads = _leads_sem_dono(c, conta_id, escopo)
        if not leads:
            return JSONResponse({"ok": True, "n": 0, "aviso": "Nenhum lead sem responsável."})
        if rodizio == "1":
            from finance import distribuicao as _dist
            fila = _dist.fila_ids(c, conta_id)
            if not fila:
                return JSONResponse({"ok": False, "erro":
                                     "Monte a fila do rodízio primeiro (aba Comunicação)."},
                                    status_code=400)
            # reparte em volta, na ordem da fila. Não uso `proximo_vendedor` porque ele
            # desiste quando a distribuição automática está desligada — e aqui quem
            # mandou distribuir foi o dono, agora, no clique.
            destinos = [fila[i % len(fila)] for i in range(len(leads))]
            ativo = bool(_dist.config(c, conta_id)["ativo"])
        else:
            alvo = _vendedor_destino(ctx, vendedor_id, pool, conta_id)
            if not alvo:
                return JSONResponse({"ok": False, "erro": "Escolha um vendedor."},
                                    status_code=400)
            destinos = [alvo] * len(leads)
            ativo = True          # não é caso de avisar sobre o rodízio
        for lead_id, dest in zip(leads, destinos):
            c.execute("update prospeccao set vendedor_id=%s, atualizado_em=now() "
                      "where id=%s and conta_id=%s", (dest, lead_id, conta_id))
            c.execute("update conversas set responsavel_membro_id=%s "
                      "where conta_id=%s and prospeccao_id=%s", (dest, conta_id, lead_id))
        c.commit()
    # o alerta que importa: repartir os de hoje não impede os de amanhã de nascerem
    # órfãos. Foi exatamente assim que a conta do chamado acumulou 21.
    aviso = ("" if ativo else
             "Pronto — mas a distribuição automática está DESLIGADA: os próximos leads "
             "vão continuar entrando sem responsável.")
    return JSONResponse({"ok": True, "n": len(leads), "aviso": aviso})


@router.post("/painel/prospeccao/comunicacao/virar-lead")
def comunicacao_virar_lead(request: Request, conversa_id: int = Form(...),
                           nome: str = Form(""), empresa: str = Form(""),
                           telefone: str = Form(""), email: str = Form(""),
                           tipo: str = Form(""), vendedor_id: str = Form(""),
                           temperatura: str = Form("morno")):
    """Promove uma conversa órfã (e-mail/WhatsApp de remetente novo, sem lead) a um
    lead da prospecção — só quando VOCÊ decide que vale.

    O nome e o telefone vêm do modal, já conferidos. Sem eles (chamada antiga, sem
    corpo), cai na mesma escada de nomes do caminho automático em vez de gravar o
    número cru como empresa — que era o que acontecia e enchia o funil de "5586…"."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    with pool.connection() as c:
        cv = c.execute(
            "select canal, prospeccao_id, contato_ref, contato_nome from conversas where id=%s and conta_id=%s",
            (conversa_id, ctx["conta_id"])).fetchone()
        if not cv:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        if cv[1]:
            return JSONResponse({"ok": True, "lead_id": cv[1]})     # já é lead
        canal, ref = cv[0], (cv[2] or "").strip()
        eh_email = canal == "email" or "@" in ref
        tipo_lead = tipo if tipo in ("pf", "pj") else ("pj" if eh_email else "pf")
        nome = (nome or "").strip()
        if not nome:
            nome, _fonte = _contato_sugerido(c, ctx["conta_id"], canal, ref, cv[3])
        # em PJ o nome do funil é o da empresa (o contato é a pessoa que fala por ela);
        # em PF os dois são a mesma pessoa
        empresa_final = ((empresa or "").strip() if tipo_lead == "pj" else "") or nome
        if not empresa_final:
            # nem agenda, nem perfil, nem digitado: ainda assim o lead precisa de um
            # nome — o número formatado é bem mais legível que os 13 dígitos crus.
            empresa_final = (ref if eh_email else _tel_fmt_br(ref)) or "Contato"
        tel = (telefone or "").strip() or ("" if eh_email else _tel_fmt_br(ref))
        mail = ((email or "").strip() or (ref if eh_email else "")).lower() or None
        temp = temperatura if temperatura in TEMP_OK else "morno"
        # só o dono escolhe o responsável; pro resto continua como sempre foi —
        # gerência deixa livre, vendedor fica com o próprio lead
        vend = (_vendedor_destino(ctx, vendedor_id, pool, ctx["conta_id"])
                if ctx["pode_atribuir"] else (None if ctx["gerencia"] else ctx["membro_id"]))
        lead_id = c.execute(
            """insert into prospeccao (conta_id, vendedor_id, empresa, contato, email,
                 whatsapp, telefone, tipo, origem, temperatura, status, estagio)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'novo','lead') returning id""",
            (ctx["conta_id"], vend, empresa_final[:250], nome[:250] or None, mail,
             ("+" + _so_digitos(ref)) if (ref and not eh_email) else None, tel or None,
             tipo_lead, "email_inbound" if eh_email else "whatsapp_inbound", temp)).fetchone()[0]
        c.execute("update conversas set prospeccao_id=%s where id=%s", (lead_id, conversa_id))
        # Sem responsável escolhido, o rodízio decide — mesma regra do lead que entra
        # sozinho pelo WhatsApp. Antes só aquele caminho chamava a distribuição, então
        # "— livre —" aqui queria dizer "de ninguém": o lead nascia órfão mesmo com a
        # fila montada e ligada.
        #
        # O `with c.transaction()` é SAVEPOINT, e não enfeite: distribuir é acessório,
        # criar o lead é o pedido. Sem ele, um erro de SQL aqui dentro aborta a
        # transação toda — o `except` engole a exceção, o `commit()` de baixo vira
        # rollback e a rota responde {"ok": true, "lead_id": N} com o lead inexistente.
        # Com o savepoint, quem cai é só a distribuição: o lead fica, sem dono.
        if vend is None:
            try:
                from finance import distribuicao as _dist
                with c.transaction():
                    _mid = _dist.atribuir_se_sem_dono(c, ctx["conta_id"], lead_id)
                if _mid:
                    import threading
                    # sem `primeira`: aqui o lead nasce de cadastro, não de mensagem —
                    # não existe texto de cliente pra citar. O link, sim, vale igual.
                    threading.Thread(target=_dist.avisar_vendedor,
                                     args=(pool, ctx["conta_id"], _mid, empresa_final[:250]),
                                     kwargs={"lead_id": lead_id}, daemon=True).start()
            except Exception:  # noqa: BLE001
                import logging          # local, como no resto do arquivo
                logging.getLogger("prospeccao.rodizio").warning(
                    "não consegui distribuir o lead %s (conta %s) — fica sem dono",
                    lead_id, ctx["conta_id"], exc_info=True)
        c.commit()
    return JSONResponse({"ok": True, "lead_id": lead_id})


@router.post("/painel/prospeccao/comunicacao/detectar-ig")
def comunicacao_detectar_ig(request: Request):
    """Descobre o ID da conta do Instagram a partir do token IGAA salvo (o servidor
    chama a Meta) e preenche o IG Account ID sozinho — evita o Graph API Explorer."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "Só o dono/gestor."}, status_code=403)
    from finance import meta_msg
    with get_pool().connection() as c:
        row = c.execute("select token from canais_config where conta_id=%s and canal='instagram'",
                        (ctx["conta_id"],)).fetchone()
    tok = (row[0] if row else "") or ""
    if not tok.startswith("IGAA"):
        return JSONResponse({"ok": False, "erro": "Salve o token do Instagram (IGAA...) primeiro."})
    res = meta_msg.resolver_conta_ig(tok)
    if not res.get("ok"):
        return JSONResponse({"ok": False, "erro": res.get("erro") or "não consegui detectar"})
    with get_pool().connection() as c:
        c.execute("""update canais_config set identificador=%s, atualizado_em=now()
                       where conta_id=%s and canal='instagram'""", (res["user_id"], ctx["conta_id"]))
        c.commit()
    # inscreve a conta no webhook 'messages' (subscribed_apps) — sem isso a DM real não chega
    sub = meta_msg.assinar_conta_ig(tok)
    # troca o token curto (~1h) por um de longa duração (~60 dias) — pra não expirar
    token_longo = False
    longo = meta_msg.trocar_token_longo(tok)
    if longo.get("ok") and longo.get("token"):
        with get_pool().connection() as c:
            c.execute("""update canais_config set token=%s, atualizado_em=now()
                           where conta_id=%s and canal='instagram'""", (longo["token"], ctx["conta_id"]))
            c.commit()
        token_longo = True
    return JSONResponse({"ok": True, "user_id": res["user_id"], "username": res.get("username", ""),
                         "assinado": bool(sub.get("ok")), "assinar_erro": (sub.get("erro") or ""),
                         "token_longo": token_longo})


@router.post("/painel/prospeccao/comunicacao/detectar-fb")
def comunicacao_detectar_fb(request: Request):
    """Descobre a Página do Facebook (id + token da Página) a partir do token salvo,
    preenche o Page ID e inscreve a Página no webhook 'messages' — pro Messenger."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "Só o dono/gestor."}, status_code=403)
    from finance import meta_msg
    with get_pool().connection() as c:
        row = c.execute("select token from canais_config where conta_id=%s and canal='messenger'",
                        (ctx["conta_id"],)).fetchone()
    tok = (row[0] if row else "") or ""
    if not tok:
        return JSONResponse({"ok": False, "erro": "Salve o token do Facebook primeiro."})
    # tenta deixar o user token de longa duração (aí o token da Página não expira)
    longo = meta_msg.trocar_user_token_longo(tok)
    user_tok = longo["token"] if (longo.get("ok") and longo.get("token")) else tok
    res = meta_msg.resolver_pagina_fb(user_tok)
    if not res.get("ok"):
        return JSONResponse({"ok": False, "erro": res.get("erro") or "não consegui detectar"})
    page_token = res.get("page_token") or tok    # usa o token DA PÁGINA (não expira se o user token for longo)
    with get_pool().connection() as c:
        c.execute("""update canais_config set identificador=%s, token=%s, atualizado_em=now()
                       where conta_id=%s and canal='messenger'""", (res["page_id"], page_token, ctx["conta_id"]))
        c.commit()
    sub = meta_msg.assinar_pagina_fb(res["page_id"], page_token)
    return JSONResponse({"ok": True, "page_id": res["page_id"], "nome": res.get("nome", ""),
                         "assinado": bool(sub.get("ok")), "assinar_erro": (sub.get("erro") or ""),
                         "varias": bool(res.get("varias"))})


@router.post("/painel/prospeccao/comunicacao/canal-numero")
def comunicacao_canal_numero(request: Request, canal: str = Form(...), numero: str = Form(""),
                             token: str = Form(""), provedor: str = Form(""),
                             wa_phone_id: str = Form("")):
    """Vincula (ou limpa) o identificador de um canal à empresa (nº WhatsApp Twilio,
    WhatsApp Cloud API do número próprio, ou Página/IG id + Page Token). Só dono/gestor."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    destino = "/painel/prospeccao/comunicacao?aba=canais"
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "Só o dono/gestor configura os canais."
        return RedirectResponse(destino, status_code=303)
    if canal not in ("whatsapp", "messenger", "instagram"):
        return RedirectResponse(destino, status_code=303)
    from finance import whatsapp_twilio as wa
    token = (token or "").strip()
    provedor = (provedor or "").strip()
    wa_phone_id = (wa_phone_id or "").strip()
    pool = get_pool()
    try:
        with pool.connection() as c:
            if canal == "whatsapp":
                # NÃO-DESTRUTIVO: guarda os dados de TODOS os provedores; troca só o
                # provedor ativo e sobrescreve APENAS os campos que vierem preenchidos
                # (campo vazio mantém o que já estava salvo; nada é apagado sozinho).
                prov = provedor if provedor in ("twilio", "cloud", "qr") else "twilio"
                novo_num = wa.normalizar_from(numero) if (numero or "").strip() else None
                # UPDATE primeiro, INSERT só se não existir. O `insert ... on
                # conflict` daqui não passava `identificador`, que é NOT NULL — e o
                # Postgres valida o NOT NULL ao montar a tupla, ANTES de resolver o
                # conflito. Ou seja: estourava sempre, mesmo com a linha já existindo
                # e só precisando trocar o provedor. O except lá embaixo engolia o
                # erro e a tela dizia "salvo". Na prática, quem conectava o QR não
                # conseguia mais voltar pro Twilio pela tela.
                trocou = c.execute(
                    """update canais_config set provedor=%s, ativo=true, atualizado_em=now()
                        where conta_id=%s and canal='whatsapp'""",
                    (prov, ctx["conta_id"])).rowcount
                if not trocou:
                    if not novo_num:
                        request.session["prosp_aviso"] = (
                            "Informe o número do WhatsApp desta empresa pra conectar.")
                        return RedirectResponse(destino, status_code=303)
                    c.execute(
                        """insert into canais_config (conta_id, canal, identificador, provedor, ativo)
                           values (%s,'whatsapp',%s,%s,true)""",
                        (ctx["conta_id"], novo_num, prov))
                elif novo_num:
                    c.execute("update canais_config set identificador=%s where conta_id=%s and canal='whatsapp'",
                              (novo_num, ctx["conta_id"]))
                if prov == "cloud" and wa_phone_id:
                    c.execute("update canais_config set wa_phone_id=%s where conta_id=%s and canal='whatsapp'",
                              (wa_phone_id, ctx["conta_id"]))
                if token:      # access token — vazio mantém o atual
                    c.execute("update canais_config set token=%s where conta_id=%s and canal='whatsapp'",
                              (token, ctx["conta_id"]))
                falta = None
                if prov == "cloud":
                    r = c.execute("select wa_phone_id from canais_config where conta_id=%s and canal='whatsapp'",
                                  (ctx["conta_id"],)).fetchone()
                    if not (r and r[0]):
                        falta = "Salvo — mas falta o Phone Number ID pra Cloud API enviar."
                c.commit()
                request.session["prosp_aviso"] = falta or "WhatsApp salvo ✓ (nada é apagado — só sobrescreve o que você preencher)."
                return RedirectResponse(destino, status_code=303)
            # messenger / instagram
            ident = (numero or "").strip()
            if ident:
                c.execute(
                    """insert into canais_config (conta_id, canal, identificador, provedor, ativo)
                       values (%s,%s,%s,'twilio',true)
                       on conflict (conta_id, canal)
                       do update set identificador=excluded.identificador, ativo=true,
                                     atualizado_em=now()""",
                    (ctx["conta_id"], canal, ident))
                if token:      # Page Access Token (Meta) — vazio mantém o atual
                    c.execute("update canais_config set token=%s where conta_id=%s and canal=%s",
                              (token, ctx["conta_id"], canal))
                msg = "Canal vinculado a esta empresa ✓"
            else:
                c.execute("delete from canais_config where conta_id=%s and canal=%s", (ctx["conta_id"], canal))
                msg = "Canal removido."
            c.commit()
    except UniqueViolation:
        msg = "Esse número já está vinculado a outra empresa."
    except Exception as e:  # noqa: BLE001 — qualquer outra: não some, e não mente
        # Antes TODA exceção virava "número já vinculado". Foi assim que uma
        # NotNullViolation no identificador passou meses parecendo colisão de
        # número — e, na tela do WhatsApp, chegou a devolver "salvo ✓" pra um
        # salvamento que não aconteceu. Erro que não é colisão vai pro log.
        import logging
        logging.getLogger("prospeccao.canais").exception(
            "canal-numero: falhou pra conta %s canal %s", ctx["conta_id"], canal)
        msg = f"Não consegui salvar ({type(e).__name__}). O erro foi registrado."
    request.session["prosp_aviso"] = msg
    return RedirectResponse(destino, status_code=303)


@router.post("/painel/prospeccao/comunicacao/canal-templates")
def comunicacao_canal_templates(request: Request, tmpl_convite_sid: str = Form(""),
                                tmpl_lembrete_sid: str = Form("")):
    """Templates da AGENDA desta empresa (convite de reunião e aviso antes).

    Ao contrário de canal-numero, aqui campo vazio LIMPA: o SID aparece no campo
    (não é segredo mascarado como o token), então precisa haver como remover um
    SID errado. Mesmo contrato de distribuicao.salvar.

    Sem validar o formato 'HX…' de propósito: no provedor cloud o campo carrega o
    NOME do template aprovado na Meta, e validação estrita quebraria esse caso."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    destino = "/painel/prospeccao/comunicacao?aba=canais"
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "Só o dono/gestor configura os canais."
        return RedirectResponse(destino, status_code=303)
    convite = (tmpl_convite_sid or "").strip()[:64] or None
    lembrete = (tmpl_lembrete_sid or "").strip()[:64] or None
    with get_pool().connection() as c:
        # UPDATE, nunca INSERT: `identificador` é NOT NULL e entra no índice único
        # (canal, identificador) — criar a linha aqui com um placeholder vazio
        # colidiria entre contas. Template sem número configurado não serviria
        # pra nada mesmo, então avisa em vez de gravar solto.
        n = c.execute(
            """update canais_config set tmpl_convite_sid=%s, tmpl_lembrete_sid=%s,
                      atualizado_em=now()
                where conta_id=%s and canal='whatsapp'""",
            (convite, lembrete, ctx["conta_id"])).rowcount
        c.commit()
    if not n:
        request.session["prosp_aviso"] = "Configure o WhatsApp da empresa aqui em cima antes de salvar os templates."
    else:
        request.session["prosp_aviso"] = (
            "Templates da agenda salvos ✓" if (convite or lembrete)
            else "Templates da agenda limpos — o convite volta a sair só pelo link manual.")
    return RedirectResponse(destino, status_code=303)


_AG_DESTINO = "/painel/prospeccao/comunicacao?aba=agente"


@router.post("/painel/prospeccao/comunicacao/agente-config")
async def comunicacao_agente_config(request: Request):
    """Salva a config do Agente (por empresa). Só dono/gestor."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "Só o dono/gestor configura o agente."
        return RedirectResponse(_AG_DESTINO, status_code=303)
    f = await request.form()
    def _b(k):
        return bool(f.get(k))
    def _i(k, pad, lo, hi):
        try:
            return max(lo, min(hi, int(f.get(k) or pad)))
        except (ValueError, TypeError):
            return pad
    horario = f.get("horario") if f.get("horario") in ("comercial", "24h") else "comercial"
    tom = f.get("tom") if f.get("tom") in ("informal", "formal") else "informal"
    escalar = f.get("escalar_para") if f.get("escalar_para") in ("dono_lead", "plantao") else "dono_lead"
    vals = (_b("ativo"), _i("limiar_confianca", 80, 50, 95), horario, tom,
            _i("max_trocas", 20, 1, 100), escalar, _b("pode_responder"), _b("pode_qualificar"),
            _b("pode_agendar"), _b("pode_orcamento"), _b("orcamento_proativo"))
    with get_pool().connection() as c:
        c.execute(
            """insert into agente_config (conta_id, ativo, limiar_confianca, horario, tom,
                 max_trocas, escalar_para, pode_responder, pode_qualificar, pode_agendar,
                 pode_orcamento, orcamento_proativo)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (conta_id) do update set
                 ativo=excluded.ativo, limiar_confianca=excluded.limiar_confianca,
                 horario=excluded.horario, tom=excluded.tom, max_trocas=excluded.max_trocas,
                 escalar_para=excluded.escalar_para, pode_responder=excluded.pode_responder,
                 pode_qualificar=excluded.pode_qualificar, pode_agendar=excluded.pode_agendar,
                 pode_orcamento=excluded.pode_orcamento, orcamento_proativo=excluded.orcamento_proativo,
                 atualizado_em=now()""",
            (ctx["conta_id"], *vals))
        c.commit()
    request.session["prosp_aviso"] = "Agente atualizado ✓"
    return RedirectResponse(_AG_DESTINO, status_code=303)


@router.post("/painel/prospeccao/comunicacao/distribuicao")
async def comunicacao_distribuicao(request: Request):
    """Salva o rodízio de distribuição de leads (nível empresa). Só dono/gestor.
    A ordem dos checkboxes 'vend' marcados = a ordem da fila (o drag reordena no DOM)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "Só o dono/gestor configura a distribuição."
        return RedirectResponse(_AG_DESTINO, status_code=303)
    f = await request.form()
    ativo = str(f.get("ativo") or "").lower() in ("1", "on", "true", "sim")
    avisar = str(f.get("avisar") or "").lower() in ("1", "on", "true", "sim")
    template_sid = (f.get("aviso_template_sid") or "").strip()
    aviso_zap = str(f.get("aviso_zap") or "").lower() in ("1", "on", "true", "sim")
    zap_chip = (f.get("aviso_zap_chip_id") or "").strip()
    zap_texto = (f.get("aviso_zap_texto") or "").strip()
    ids = [int(x) for x in f.getlist("vend") if str(x).isdigit()]
    from finance import distribuicao as _dist
    with get_pool().connection() as c:
        validos = {r[0] for r in c.execute(
            "select id from membros where conta_id=%s and ativo", (ctx["conta_id"],)).fetchall()}
        ids = [i for i in ids if i in validos]
        # o chip vem da tela, e a tela é editável: sem esta volta, um id na mão faria
        # o aviso de uma empresa sair pelo número de OUTRA. Mesmo cuidado do `_posse`.
        chip_ok = _chip_da_conta(c, ctx["conta_id"], zap_chip) if zap_chip else None
        _dist.salvar(c, ctx["conta_id"], ativo, avisar, ids, aviso_template_sid=template_sid,
                     aviso_zap=aviso_zap,
                     # "" (chip principal) tem que CHEGAR no salvar pra apagar a escolha
                     # anterior — por isso 0, e não None, que significa "não mexe".
                     aviso_zap_chip_id=(chip_ok if chip_ok and chip_ok != ctx["conta_id"] else 0),
                     aviso_zap_texto=zap_texto)
        c.commit()
    request.session["prosp_aviso"] = "Distribuição de leads salva ✓"
    return RedirectResponse(_AG_DESTINO, status_code=303)


@router.post("/painel/prospeccao/comunicacao/agente-instrucoes")
def comunicacao_agente_instrucoes(request: Request, texto: str = Form("")):
    """Salva as instruções gerais do agente (uma linha tipo='instrucoes' por conta)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse(_AG_DESTINO, status_code=303)
    with get_pool().connection() as c:
        c.execute("delete from agente_conhecimento where conta_id=%s and tipo='instrucoes'", (ctx["conta_id"],))
        if (texto or "").strip():
            c.execute("""insert into agente_conhecimento (conta_id, tipo, resposta, ordem)
                         values (%s,'instrucoes',%s,0)""", (ctx["conta_id"], texto.strip()[:4000]))
        c.commit()
    request.session["prosp_aviso"] = "Instruções salvas ✓"
    return RedirectResponse(_AG_DESTINO, status_code=303)


@router.post("/painel/prospeccao/comunicacao/agente-faq")
def comunicacao_agente_faq(request: Request, pergunta: str = Form(""), resposta: str = Form(""),
                           excluir: str = Form("")):
    """Adiciona ou exclui uma pergunta/resposta da base do agente."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse(_AG_DESTINO, status_code=303)
    with get_pool().connection() as c:
        if excluir.isdigit():
            c.execute("delete from agente_conhecimento where id=%s and conta_id=%s and tipo='faq'",
                      (int(excluir), ctx["conta_id"]))
            msg = "Pergunta removida."
        elif (pergunta or "").strip() and (resposta or "").strip():
            c.execute("""insert into agente_conhecimento (conta_id, tipo, pergunta, resposta, ordem)
                         values (%s,'faq',%s,%s,
                           coalesce((select max(ordem)+1 from agente_conhecimento where conta_id=%s and tipo='faq'),1))""",
                      (ctx["conta_id"], pergunta.strip()[:300], resposta.strip()[:2000], ctx["conta_id"]))
            msg = "Pergunta adicionada ✓"
        else:
            msg = "Preencha pergunta e resposta."
        c.commit()
    request.session["prosp_aviso"] = msg
    return RedirectResponse(_AG_DESTINO, status_code=303)


@router.post("/painel/prospeccao/comunicacao/prospec-perfil")
async def comunicacao_prospec_perfil(request: Request):
    """Perfil do 1º contato: Instagram (referência do lead), cargo de quem envia e
    material padrão. O material tem os mesmos tipos da campanha (link/vídeo/PDF/foto,
    com upload) e é enviado no 'Quero o material' quando o lead está fora de campanha."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse(_AG_DESTINO, status_code=303)
    form = await request.form()
    insta = (form.get("prospec_instagram") or "").strip()[:200]
    cargo = (form.get("prospec_cargo") or "").strip()[:60]
    tipo = (form.get("prospec_material_tipo") or "link").strip().lower()
    if tipo not in ("link", "video", "pdf", "foto"):
        tipo = "link"
    # material: None = mantém o que já está (trocar de aba sem novo arquivo não apaga)
    material = None
    if tipo == "link":
        material = (form.get("material_link") or "").strip()[:2000]
    elif tipo == "video":
        material = (form.get("material_video") or "").strip()[:2000]
    else:  # pdf/foto → upload (se veio arquivo novo)
        up = form.get("material_pdf") if tipo == "pdf" else form.get("material_foto")
        if up is not None and getattr(up, "filename", ""):
            try:
                conteudo = await up.read()
                from finance.upload_foto import subir_material
                material = subir_material(conteudo, up.filename, getattr(up, "content_type", "") or "")
            except Exception as e:  # noqa: BLE001
                request.session["prosp_aviso"] = f"Não consegui subir o arquivo: {e}"
                return RedirectResponse(_AG_DESTINO, status_code=303)
    with get_pool().connection() as c:
        if material is None:
            c.execute("""update contas set prospec_instagram=%s, prospec_cargo=%s,
                           prospec_material_tipo=%s where id=%s""",
                      (insta or None, cargo or None, tipo, ctx["conta_id"]))
        else:
            c.execute("""update contas set prospec_instagram=%s, prospec_cargo=%s,
                           prospec_material=%s, prospec_material_tipo=%s where id=%s""",
                      (insta or None, cargo or None, (material or None), tipo, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Perfil do 1º contato salvo ✓"
    return RedirectResponse(_AG_DESTINO, status_code=303)


def _nome_da_agenda(c, conta_id, numero: str) -> str:
    """Nome que o vendedor tem salvo na AGENDA do celular pra esse número (tabela
    wa_contatos, alimentada pelo /webhooks/wa-qr/contatos). '' quando não tem.
    Casa pelos últimos 8 dígitos, igual ao resto do módulo."""
    n = _so_digitos(numero)
    if not n:
        return ""
    alvo8 = n[-8:] if len(n) >= 8 else n
    r = c.execute("select nome from wa_contatos where conta_id=%s and numero8=%s",
                  (conta_id, alvo8)).fetchone()
    return ((r[0] if r else "") or "").strip()[:120]


#: o nome que o funil usa quando ainda não sabe de quem é o número (ver a escada
#: em `_wa_inbound_conversa`). É texto fixo, então dá pra reconhecer depois.
NOME_PROVISORIO = "Contato WhatsApp"

#: lead que nunca foi batizado de verdade: sem nome, com o número cru no lugar do
#: nome, ou com o texto provisório. Mesma família que o `scripts/backfill_nome_lead`
#: já conhece — aqui ela ganha o texto provisório, que faltava lá.
_SQL_LEAD_SEM_NOME = (r"(p.empresa = %(prov)s or coalesce(btrim(p.empresa),'') = ''"
                      r" or p.empresa ~ '^\+?[0-9 ()\-]+$')")


def _batiza_lead_pendente(c, conta_id: int, conv_id: int | None = None) -> int:
    """Desce pro lead o nome que a CONVERSA já sabe.

    O lead é batizado no instante em que a mensagem chega, e nesse instante o nome
    pode não existir em lugar nenhum: a agenda do celular ainda não sincronizou e o
    pushName não veio. A escada cai no texto provisório — e nunca mais era
    revisitada. Na Doce Mell isso deu 8 leads chamados "Contato WhatsApp" cujo nome
    estava, sete minutos depois, na conversa ligada a eles.

    Então o nome desce onde ele CHEGA, não onde o lead nasce. Chamado dos três
    pontos que aprendem nome: a mensagem que entra, a importação de histórico e a
    sincronização de contatos.

    Só toca em lead que nunca foi batizado (ver `_SQL_LEAD_SEM_NOME`). Nome que
    alguém digitou fica onde está, mesmo que o pushName discorde — quem escreveu
    sabe mais que o WhatsApp.

    Dentro de SAVEPOINT pela mesma razão do rodízio, algumas linhas abaixo: isto é
    acessório, e um erro aqui sem a trava abortaria a transação inteira — a MENSAGEM
    RECEBIDA se perderia, ou a sincronização de contatos inteira cairia. Batismo que
    falha custa um nome feio; transação abortada custa o cliente."""
    sql = (r"""update prospeccao p
                  set empresa = btrim(cv.contato_nome),
                      contato = case when coalesce(btrim(p.contato),'') in ('', %(prov)s)
                                       or p.contato ~ '^\+?[0-9 ()\-]+$'
                                     then btrim(cv.contato_nome) else p.contato end,
                      atualizado_em = now()
                 from conversas cv
                where cv.prospeccao_id = p.id and cv.conta_id = p.conta_id
                  and p.conta_id = %(conta)s
                  and coalesce(btrim(cv.contato_nome),'') <> ''
                  and """ + _SQL_LEAD_SEM_NOME)
    par = {"conta": conta_id, "prov": NOME_PROVISORIO}
    if conv_id is not None:
        sql += " and cv.id = %(conv)s"
        par["conv"] = conv_id
    try:
        with c.transaction():
            return c.execute(sql, par).rowcount or 0
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("prospeccao.nome").warning(
            "batismo do lead falhou conta=%s conv=%s — mensagem preservada",
            conta_id, conv_id, exc_info=True)
        return 0


# "não filtre por chip" — diferente de `chip_id=None`, que é o chip PRINCIPAL (ver
# _chip_gravavel). Sem este sentinela não dá pra dizer as duas coisas num parâmetro
# só, e quem não sabe o chip (o botão "Agora não", que vem da tela) acabaria pedindo
# o principal por engano — perdendo a conversa do chip 2.
_QUALQUER_CHIP = object()


def _sql_filtro_chip(chip_id, empresa_id):
    """`(pedaço de SQL, params)` pro filtro de chip de uma consulta em `conversas`.

    `is not distinct from` e não `=`: o chip principal é gravado como NULO (todo o
    histórico e toda empresa de um chip só), e `chip_id = NULL` não casa com nada —
    a busca voltaria vazia e cada mensagem abriria uma conversa nova."""
    if chip_id is _QUALQUER_CHIP:
        return "", []
    return " and chip_id is not distinct from %s", [_chip_gravavel(chip_id, empresa_id)]


def _conversa_wa_do_contato(c, conta_id, lead_id, numero, *, chip_id=_QUALQUER_CHIP):
    """A conversa de WhatsApp deste contato: a do LEAD, se ele já tiver uma; senão a
    do NÚMERO, nas duas grafias (ver _wa_equivalentes). Devolve (id, prospeccao_id)
    ou None. Usada pelos três caminhos que gravam conversa — entrada, eco de saída e
    o botão "Agora não" — que antes repetiam a mesma busca com diferenças sutis.

    `chip_id` limita a busca ao chip por onde a mensagem entrou. Numa empresa de DOIS
    números o mesmo contato tem duas conversas — uma por chip — e casar pelo número
    sem olhar o chip devolvia a do OUTRO: a resposta saía pelo número errado e as duas
    campanhas se misturavam numa thread só. Medido na conta 34 em 21/08/2026, com a
    Prime rodando "Zarb" no chip principal e "Thiago" no chip 36. Sem o argumento a
    busca é de QUALQUER chip, que é o comportamento de sempre (e o certo pra quem
    chega pelo lead, não pelo número).

    DUAS CONSULTAS, e não uma com OR, porque o OR custa a tabela inteira. Medido
    com 60 mil conversas: a versão com `prospeccao_id=%s or right(...)=%s` fazia
    Seq Scan e removia 60.000 linhas pelo filtro (34ms por mensagem recebida);
    separadas, cada metade cai num índice que já existe —

        idx_conversas_lead_canal   (conta_id, prospeccao_id, canal), UNIQUE
        idx_conversas_num8         (conta_id, canal, right(regexp_replace(...), 8))

    O planner não combina os dois num BitmapOr aqui porque o segundo lado é uma
    expressão. E varredura de conversas por mensagem é exatamente o que derrubou o
    app em 15/08 (2.446 respostas 502 durante a importação de uma agenda) — a
    migração 156 nasceu disso; não faz sentido reintroduzir o problema ao lado.

    A busca por número tem SEMPRE os dois filtros: `right(...)=<8 finais>` é o que o
    índice enxerga, e a igualdade exata é o que impede confundir com um celular de
    outro DDD que termine igual."""
    d = _so_digitos(numero)
    alvo8 = d[-8:] if len(d) >= 8 else d
    filtro, par_chip = _sql_filtro_chip(chip_id, conta_id)
    if lead_id:
        # UNIQUE (conta_id, prospeccao_id, canal): é uma linha ou nenhuma. O filtro de
        # chip aqui é redundante por construção (quem escolheu o lead já descartou os
        # que são de outro chip) e fica de cinto: uma linha vinda de outro chip é bug,
        # e é melhor ela não voltar do que voltar e mandar resposta pelo número errado.
        r = c.execute(
            """select id, prospeccao_id from conversas
                where conta_id=%s and canal='whatsapp' and prospeccao_id=%s""" + filtro,
            (conta_id, lead_id, *par_chip)).fetchone()
        if r:
            return r
    return c.execute(
        r"""select id, prospeccao_id from conversas
             where conta_id=%s and canal='whatsapp'
               and right(regexp_replace(contato_ref, '\D', '', 'g'), 8) = %s
               and regexp_replace(contato_ref, '\D', '', 'g') = any(%s)""" + filtro + """
             order by (prospeccao_id is not null) desc, ultima_msg_em desc limit 1""",
        (conta_id, alvo8, _wa_equivalentes(d) or [d], *par_chip)).fetchone()


def _ja_conversou(c, conta_id, lead_id) -> bool:
    """Já houve troca de verdade com esse lead — ou seja, a mensagem que está chegando
    agora não é a primeira coisa que acontece.

    Conta como troca: uma mensagem recebida anterior (a pessoa insistiu) ou uma
    enviada DEPOIS de algo recebido (a empresa/agente respondeu e ela voltou). O
    disparo da campanha em si não conta — senão todo alvo já entraria "com histórico"
    e a trava não pegaria nada, que é o defeito óbvio dessa checagem.
    """
    r = c.execute(
        """with msg as (
             select m.direcao, m.criado_em
               from mensagens m join conversas cv on cv.id=m.conversa_id
              where cv.conta_id=%s and cv.prospeccao_id=%s)
           select count(*) filter (where direcao='in'),
                  count(*) filter (where direcao='out' and criado_em > coalesce(
                                     (select min(criado_em) from msg where direcao='in'),
                                     'infinity'::timestamptz))
             from msg""",
        (conta_id, lead_id)).fetchone()
    return bool(r and ((r[0] or 0) >= 1 or (r[1] or 0) >= 1))


def _wa_inbound_conversa(c, conta_id, remetente, corpo, sid, nome_perfil, agente_on,
                         *, exigir_continuidade=False, chip_id=None):
    """WhatsApp de ENTRADA (Twilio OU Cloud API): resolve lead+conversa pelo telefone,
    grava a mensagem e reabre a janela/reativa o agente. Devolve (conv_id, nova) — se
    a mensagem entrou agora ou já estava lá. Um humano que 'assumiu'
    (status='pendente') não é reativado.

    O dedup é por (conversa_id, provider_sid), e o par importa: o id do WhatsApp é o
    MESMO nas duas pontas da mensagem, então dedup global fazia a conta que RECEBE
    perder a mensagem quando a conta que ENVIOU era outra conta do mesmo Zaq — ver
    migração 159.

    `nova` existe porque o dedup era SILENCIOSO: o `on conflict do nothing` engolia a
    repetição e quem chamava seguia como se fosse mensagem nova. O wa-qr reentrega a
    mesma mensagem quando a conexão oscila (`messages.upsert` type 'append'), e em
    15/08 uma única mensagem do cliente ("?") foi entregue TRÊS vezes ao webhook: a
    mensagem não duplicou no banco, mas o agente foi acionado três vezes e o cliente
    recebeu três respostas diferentes, uma pedindo desculpa pela confusão da outra.

    `exigir_continuidade` liga a trava contra resposta automática: um contato da BASE
    que responde algo não reconhecível como aceite não vira lead quente na primeira
    mensagem — vira na segunda, ou depois que a empresa responder. Bot de empresa
    manda o "não estamos disponíveis" e cala; pessoa continua. Só faz sentido onde há
    campanha com template (Twilio/Cloud); no QR o disparo é texto solto e a regra
    segue como sempre foi."""
    remetente = _so_digitos(remetente)
    alvo8 = remetente[-8:] if len(remetente) >= 8 else remetente
    # as duas grafias do número (com e sem o nono dígito) — o mesmo contato chega das
    # duas formas, e casar por igualdade crua criava uma conversa nova a cada troca
    equivalentes = _wa_equivalentes(remetente) or [remetente]
    # O lead deste número — mas NÃO um que já seja de OUTRO chip.
    #
    # Numa empresa de dois números o mesmo telefone pode estar em duas campanhas, uma
    # por chip, e aí são DOIS leads e DUAS conversas: o que entra pelo chip 1 sai pelo
    # chip 1, o que entra pelo 2 sai pelo 2. Sem o `not exists`, a mensagem que chegava
    # no chip 2 encontrava o lead do chip 1 e ia parar na conversa dele — a resposta
    # saía pelo número errado e as duas campanhas viravam uma thread só (conta 34,
    # 21/08/2026: a "Thiago" no chip 36 respondeu como se fosse a "Zarb").
    #
    # Quem marca o chip do lead é a CONVERSA dele (prospeccao não tem coluna de chip, e
    # não precisa ter): lead sem conversa de WhatsApp ainda não é de ninguém e o
    # primeiro chip que falar com ele fica com ele. Numa empresa de um chip só, todas
    # as conversas têm `chip_id` nulo e o chip que chega também é nulo — `is distinct
    # from` dá falso em todas, o `not exists` é sempre verdadeiro, e a consulta é
    # exatamente a de antes.
    #
    # O aviso cruzado (o gêmeo na outra campanha) é o `_gemeos_de_outro_chip`,
    # que é só leitura de tela — aqui a regra é apenas: não roubar o lead do outro chip.
    lead = c.execute(
        r"""select p.id, coalesce(p.origem,'') from prospeccao p
             where p.conta_id=%s
               and right(regexp_replace(coalesce(p.whatsapp, p.telefone, ''), '\D', '', 'g'), 8) = %s
               and not exists (select 1 from conversas cv
                                where cv.conta_id=p.conta_id and cv.prospeccao_id=p.id
                                  and cv.canal='whatsapp'
                                  and cv.chip_id is distinct from %s)
             order by p.atualizado_em desc limit 1""",
        (conta_id, alvo8, _chip_gravavel(chip_id, conta_id))).fetchone()
    lead_id = lead[0] if lead else None
    de_prospeccao = bool(lead) and lead[1] not in ("whatsapp_inbound", "email_inbound")
    lead_novo = False
    nome_lead_novo = None
    # Cliente de casa cuja conversa foi REIMPORTADA, e não contato novo — ver o bloco
    # da órfã logo abaixo. Nasce falso e só o caminho da órfã liga.
    retomada = False
    if not lead_id:
        # Conversa ÓRFÃ desse número (importada do histórico do WhatsApp por QR, de
        # ANTES de conectar — ver _wa_historico_conversa). ANTES isso era um beco sem
        # saída: a mensagem era anexada e pronto, o contato nunca entrava no funil e
        # ninguém era avisado. Numa padaria, esse "contato antigo" é o cliente pedindo
        # bolo — e o pedido ficava sem dono, invisível pra fila.
        #
        # Agora a conversa órfã só serve pra herdar o NOME (melhor que o pushName) e
        # o fluxo segue pra criar o lead. O vínculo da conversa existente com o lead
        # novo é feito logo abaixo, junto com as mensagens que ela já tinha.
        # do MESMO chip: uma órfã do chip 2 não pode ser adotada pelo lead que nasce
        # no chip 1 — ela é a thread do outro número, com o outro histórico.
        orfa = c.execute(
            r"""select id, coalesce(nullif(contato_nome,''),'') from conversas
                 where conta_id=%s and canal='whatsapp' and prospeccao_id is null
                   and right(regexp_replace(contato_ref, '\D', '', 'g'), 8) = %s
                   and regexp_replace(contato_ref, '\D', '', 'g') = any(%s)
                   and chip_id is not distinct from %s
                 order by ultima_msg_em desc limit 1""",
            (conta_id, alvo8, equivalentes, _chip_gravavel(chip_id, conta_id))).fetchone()
        nome_orfa = orfa[1] if orfa else ""
        # ...e a órfã diz TAMBÉM se este contato é novo ou velho, que é coisa diferente.
        #
        # Um re-pareamento reimporta o histórico inteiro, e TODA conversa volta órfã. Aí
        # a primeira mensagem de cada cliente antigo caía aqui e era anunciada como lead
        # novo: em 22/08, depois de repartear a Doce Mell, a vendedora recebeu 21 e-mails
        # "🔥 Novo lead pra você" em duas horas — dez deles de gente que ela atende desde
        # julho, incluindo uma conversa com 918 mensagens ENVIADAS por ela mesma.
        #
        # Virar lead continua certo (o pedido não pode ficar sem dono, que é a razão
        # deste bloco existir). O que estava errado era a ETIQUETA. Órfã que já tem
        # mensagem é cliente voltando; órfã sem mensagem nenhuma — ou órfã nenhuma — é
        # contato novo de verdade. A conta é feita ANTES de gravar a mensagem desta
        # chamada, senão todo mundo pareceria ter histórico.
        retomada = bool(orfa) and c.execute(
            "select 1 from mensagens where conversa_id=%s limit 1", (orfa[0],)).fetchone() is not None
        # contato NOVO de verdade (landing/WhatsApp) → vira lead QUENTE, não atribuído (o dono distribui).
        # Nome: primeiro o da AGENDA do vendedor (o melhor que existe), depois o do
        # perfil do WhatsApp (pushName) quando o contato deixa público; sem nenhum
        # dos dois, o WhatsApp não manda outro nome pra gente pegar.
        # `nome_orfa` entra logo depois da agenda: numa conversa importada ele é o nome
        # com que o contato já aparecia no celular, melhor que o pushName do momento.
        nome = _nome_da_agenda(c, conta_id, remetente) or nome_orfa \
            or (nome_perfil or "").strip() or NOME_PROVISORIO
        # tipo 'pf': quem manda mensagem é uma pessoa. Mesmo palpite do botão "Levar
        # para o lead" — os dois caminhos nascendo diferentes era o que confundia. Um
        # clique na ficha troca pra empresa quando o número for de um comércio.
        lead_id = c.execute(
            """insert into prospeccao (conta_id, vendedor_id, empresa, contato, whatsapp,
                 tipo, origem, temperatura, status, estagio)
               values (%s, null, %s, %s, %s, 'pf', 'whatsapp_inbound', 'quente', 'novo', 'lead') returning id""",
            (conta_id, nome[:250], nome[:250], "+" + remetente)).fetchone()[0]
        # `lead_novo` governa o "Retornar contato: X" agendado pra daqui a 2h (mais
        # abaixo). Numa retomada isso é tarefa inventada: ninguém deixou de responder
        # esse cliente — a conversa é que voltou a existir do zero pro sistema.
        lead_novo = not retomada
        nome_lead_novo = nome
    else:
        # lead da BASE respondeu/topou no WhatsApp → promove pro funil (Novo + Quente).
        # Com `exigir_continuidade`, a PRIMEIRA mensagem de quem ainda está na base não
        # promove: a resposta automática das empresas ("no momento não estamos
        # disponíveis") entrava aqui e virava lead QUENTE, sujando o funil e o placar
        # do vendedor. Quem clicou no botão nem chega aqui — `_tratar_botao_prospec`
        # resolve antes, então o aceite continua imediato.
        #
        # A trava vale pra quem NÓS fomos atrás (Google, Explorium, importação, cadastro
        # manual) — não pra quem procurou a empresa. Quem chegou pelo WhatsApp/e-mail já
        # é cliente falando com a gente: a mensagem dele esquenta na hora, sempre.
        #
        # Não dá pra usar `estagio='base'` aqui, que era a leitura óbvia. Na prática a
        # base é esvaziada em lote pelo botão "Promover" ANTES da campanha rodar, então
        # na hora que o bot responde o alvo já é 'lead' — a trava nunca disparava. Quem
        # já engajou de verdade continua esquentando por `_ja_conversou`, que é o que
        # separa cliente ativo de alvo frio, e não depende do estágio.
        if not (exigir_continuidade and de_prospeccao) or _ja_conversou(c, conta_id, lead_id):
            _promover_para_lead(c, conta_id, lead_id)
    # Acha a conversa do lead OU qualquer uma do mesmo número (e vincula ela ao lead,
    # se estiver órfã). A conversa deste lead vem primeiro; depois as que já têm dono;
    # por último a mais recente. Exigir `prospeccao_id is null` pra casar por número,
    # como era antes, deixava de fora a conversa pendurada em OUTRA ficha do mesmo
    # telefone — e o inbox ganhava uma segunda thread do mesmo cliente.
    conv = _conversa_wa_do_contato(c, conta_id, lead_id, remetente, chip_id=chip_id)
    if conv:
        conv_id = conv[0]
        if conv[1] is None:
            c.execute("update conversas set prospeccao_id=%s where id=%s", (lead_id, conv_id))
    else:
        conv_id = c.execute(
            # `chip_id` guarda POR QUAL CHIP a conversa entrou — é o que faz a
            # resposta sair pelo mesmo número. Nulo = o chip da própria empresa, que
            # é o estado de todo o histórico e da empresa de um chip só.
            """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, status,
                 agente_ativo, contato_nome, chip_id)
               values (%s,%s,'whatsapp',%s,'aberta',%s,
                       (select nome from wa_contatos where conta_id=%s and numero8=%s),%s)
               returning id""",
            (conta_id, lead_id, remetente, agente_on, conta_id, alvo8,
             _chip_gravavel(chip_id, conta_id))).fetchone()[0]
    cur = c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid)
                 values (%s,'whatsapp','in','lead',%s,%s)
                 on conflict (conversa_id, provider_sid) where provider_sid is not null do nothing""",
                    (conv_id, (corpo or "")[:8000], sid))
    # entrou agora, ou é a mesma mensagem chegando de novo? Ver o `nova` no docstring:
    # sem esta resposta o dedup era silencioso e o agente respondia uma vez por entrega.
    nova = cur.rowcount > 0
    # reabre a janela de 24h e REATIVA o agente — a menos que um humano tenha assumido
    # (status='pendente'). O CASE lê o status ANTIGO da linha, então 'pendente' fica preservado.
    #
    # O agente-mestre LIGA a conversa, mas nunca mais a DESLIGA. Antes esta linha era
    # `agente_ativo = <mestre>`, e com o mestre desligado toda mensagem que chegava
    # zerava a conversa: quem ligasse o agente numa conversa só — que é como o painel
    # manda testar — via a chave voltar sozinha no primeiro "oi" do cliente. Ligar numa
    # conversa é ato explícito de gente; mensagem que chega não desfaz ato de gente.
    c.execute(
        """update conversas set ultima_msg_em=now(),
             janela_expira_em=now()+interval '24 hours',
             -- pushName é só reserva: não derruba o nome da AGENDA que já estiver lá
             contato_nome=coalesce(nullif(contato_nome,''), nullif(%s,'')),
             status = case when status='pendente' then 'pendente' else 'aberta' end,
             agente_ativo = case when status='pendente' then agente_ativo
                                 when %s then true
                                 else agente_ativo end
           where id=%s""", ((nome_perfil or "").strip()[:120], agente_on, conv_id))
    # o nome que acabou de chegar desce pro lead, se ele ainda não tiver um de
    # verdade. Sem isto o lead fica com o texto provisório pra sempre, mesmo com o
    # nome ali do lado na conversa.
    _batiza_lead_pendente(c, conta_id, conv_id)
    # rodízio: se o lead ainda não tem dono, distribui pro próximo vendedor da fila.
    # Cobre contato NOVO e resposta de campanha (ambos passam por aqui). Best-effort —
    # nunca deixa a entrada da mensagem quebrar; o aviso vai numa thread solta.
    #
    # O SAVEPOINT não é decoração: sem ele, um erro aqui aborta a transação inteira e o
    # `commit` lá do webhook vira ROLLBACK calado — a MENSAGEM RECEBIDA se perde e o
    # WhatsApp leva 200 como se tivesse dado tudo certo. Falhar o rodízio custa um lead
    # sem dono; perder a mensagem custa o cliente.
    _mid = None
    try:
        with c.transaction():
            from finance import distribuicao as _dist
            _mid = _dist.atribuir_se_sem_dono(c, conta_id, lead_id)
            if _mid:
                _emp = (c.execute("select coalesce(empresa,'') from prospeccao where id=%s",
                                  (lead_id,)).fetchone() or [""])[0]
                import threading
                # `retomada` muda só o TEXTO do aviso — a distribuição é a mesma, porque
                # cliente que voltou também precisa de dono. Ver o bloco da órfã.
                # `lead_id` monta o link direto pra ficha (antes caía no login) e `corpo`
                # é a mensagem do cliente, que o aviso por WhatsApp cita cortada.
                threading.Thread(target=_dist.avisar_vendedor,
                                 args=(get_pool(), conta_id, _mid, _emp),
                                 kwargs={"retomada": retomada, "lead_id": lead_id,
                                         "primeira": corpo or ""}, daemon=True).start()
    except Exception:  # noqa: BLE001
        import logging
        _mid = None
        logging.getLogger("prospeccao.rodizio").warning(
            "rodízio falhou no inbound conta=%s lead=%s — mensagem preservada",
            conta_id, lead_id, exc_info=True)
    # Lead novo de verdade (não resposta de alguém que já tinha entrada na base) já
    # sai da caixa com um retorno agendado — assim ninguém esquece de responder.
    # Best-effort: nunca deixa a entrada da mensagem quebrar por isso.
    if lead_novo:
        try:
            from finance import agenda as _agenda
            _agenda.criar_evento(
                get_pool(), conta_id,
                f"Retornar contato: {nome_lead_novo}",
                _agenda.agora_brt() + timedelta(hours=2),
                membro_id=_mid, tipo="empresa",
                descricao="Lead novo pelo WhatsApp — retornar contato o quanto antes.",
                prospeccao_id=lead_id,
            )
        except Exception:  # noqa: BLE001
            pass
    # PUSH no celular de quem atende. Fica DEPOIS do rodízio de propósito: se o lead
    # acabou de ser atribuído (`_mid`), `avisar_vendedor` já mandou o "🔥 Novo lead" e
    # duas notificações pela mesma mensagem seria barulho. Da segunda mensagem em
    # diante — que antes era silêncio total — quem avisa é este.
    #
    # Thread solta e FORA da transação, pela mesma regra que já governa o rodízio
    # aqui em cima: falhar o aviso custa um aviso; abortar a transação custa a
    # MENSAGEM RECEBIDA, e o WhatsApp leva 200 como se estivesse tudo bem.
    # `nova` pela mesma razão que o agente olha pra ela: a mesma mensagem chega mais
    # de uma vez (reentrega do provedor), e notificar de novo por uma entrega repetida
    # é avisar de algo que não aconteceu.
    if nova and not _mid:
        try:
            from finance import cockpit as _ck
            import threading
            threading.Thread(target=_ck.avisar_mensagem,
                             args=(get_pool(), conta_id, lead_id, conv_id, corpo),
                             daemon=True).start()
        except Exception:  # noqa: BLE001
            pass
    return conv_id, nova


def _agente_atende(c, conv_id, agente_on) -> bool:
    """Vale acordar o agente pra esta conversa?

    Duas portas, e a segunda é a que faltava: o agente-mestre ligado atende tudo, e
    uma CONVERSA ligada à mão atende mesmo com o mestre desligado. É assim que se
    testa o agente antes de soltá-lo na caixa inteira — ligar numa conversa só,
    acompanhar as respostas, e só então ligar pra todo mundo.

    Antes só existia a primeira porta: o webhook nem chamava o agente quando o mestre
    estava desligado, então o botão da conversa não fazia nada e parecia defeito."""
    if agente_on:
        return True
    r = c.execute("select coalesce(agente_ativo,false) from conversas where id=%s",
                  (conv_id,)).fetchone()
    return bool(r and r[0])


def _wa_conversa_simples(c, conta_id, lead_id, remetente, corpo, sid) -> int:
    """Acha/cria a conversa de WhatsApp do lead e grava a mensagem de entrada, SEM
    esquentar/promover (usado no "Agora não": o lead recusou, não vira lead quente).

    Mesma busca do caminho normal de entrada (_wa_inbound_conversa): as duas grafias
    do número e sem exigir conversa órfã — senão o "Agora não" abre uma thread
    paralela justamente de quem já estava conversando."""
    remetente = _so_digitos(remetente)
    conv = _conversa_wa_do_contato(c, conta_id, lead_id, remetente)
    if conv:
        conv_id = conv[0]
        if conv[1] is None:
            c.execute("update conversas set prospeccao_id=%s where id=%s", (lead_id, conv_id))
    else:
        conv_id = c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, status, agente_ativo)
               values (%s,%s,'whatsapp',%s,'aberta',false) returning id""",
            (conta_id, lead_id, remetente)).fetchone()[0]
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid)
                 values (%s,'whatsapp','in','lead',%s,%s)
                 on conflict (conversa_id, provider_sid) where provider_sid is not null do nothing""",
              (conv_id, (corpo or "")[:8000], sid))
    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conv_id,))
    return conv_id


def _tratar_botao_prospec(c, conta_id, remetente, tipo, texto, sid, nome) -> bool:
    """Clique num botão do template de 1º contato ("Quero te conhecer" / "Quero o
    material" / "Agora não"). Responde deterministicamente (Instagram/material),
    esquenta o lead e para a sequência. Devolve True se agiu; False pra deixar o
    fluxo normal (IA) seguir (ex.: número que não é de nenhum lead da conta)."""
    from finance import prospec_inbound as _pi, whatsapp_out as _wout
    from finance.campanhas_motor import _conta_identidade
    rem = _so_digitos(remetente)
    alvo8 = rem[-8:] if len(rem) >= 8 else rem
    lead = c.execute(
        r"""select id from prospeccao
             where conta_id=%s and right(regexp_replace(coalesce(whatsapp, telefone, ''), '\D', '', 'g'), 8) = %s
             order by atualizado_em desc limit 1""", (conta_id, alvo8)).fetchone()
    if not lead:
        return False
    lead_id = lead[0]
    if tipo == "nao":
        # recusou: para a sequência e NÃO esquenta
        c.execute("""update campanha_alvos set status='concluido', proximo_envio_em=null
                       where prospeccao_id=%s and status in ('fila','enviado')""", (lead_id,))
        conv_id = _wa_conversa_simples(c, conta_id, lead_id, rem, texto, sid)
    else:
        # aceitou: cria/acha conversa, esquenta (promove base→lead) e reativa o agente
        master = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                           (conta_id,)).fetchone()
        agente_on = bool(master and master[0])
        conv_id, _nova = _wa_inbound_conversa(c, conta_id, rem, texto, sid, nome, agente_on)
        c.execute("""update campanha_alvos set status='respondeu', wa_status='respondeu', proximo_envio_em=null
                       where prospeccao_id=%s and status in ('fila','enviado')""", (lead_id,))
    idn = _conta_identidade(c, conta_id)
    material = ""
    if tipo == "material":
        from finance.campanhas_motor import material_token, _app_url as _mat_app_url
        mrow = c.execute(
            """select coalesce(cp.material,''), cp.id from campanha_alvos a
                 join campanhas cp on cp.id=a.campanha_id
                where a.prospeccao_id=%s and cp.conta_id=%s and coalesce(cp.material,'')<>''
                order by cp.criado_em desc limit 1""", (lead_id, conta_id)).fetchone()
        if mrow and mrow[0]:
            material = (_mat_app_url() + "/material?t=" + material_token(conta_id, lead_id, mrow[1])
                        + "&canal=whatsapp")
        else:                  # fora de campanha → material padrão da conta (fallback, sem tracking)
            drow = c.execute("select coalesce(prospec_material,'') from contas where id=%s",
                             (conta_id,)).fetchone()
            material = (drow[0] if drow else "") or ""
    txt = _pi.resposta(tipo, idn, material)
    _res = {}
    try:
        _res = _wout.enviar(c, conta_id, rem, txt,
                            chip_id=_wout.chip_da_conversa(c, conta_id, conv_id)) or {}
    except Exception:  # noqa: BLE001
        pass
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid, status)
                 values (%s,'whatsapp','out','bot',%s,%s,%s)""",
              (conv_id, txt[:8000], _res.get("sid"), "enviado" if _res.get("sid") else None))
    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conv_id,))
    from finance.campanhas_motor import evento as _ev, _campanha_do_lead as _cdl
    _rot_btn = {"conhecer": "Quero te conhecer", "material": "Quero o material", "nao": "Agora não"}
    _ev(c, _cdl(c, lead_id), lead_id, "whatsapp",
        "respondeu" if tipo != "nao" else "clicou", _rot_btn.get(tipo, tipo))
    return True


@router.post("/webhooks/twilio")
async def webhook_twilio(request: Request, background_tasks: BackgroundTasks):
    """Recebe mensagens do WhatsApp (Twilio). Valida a assinatura, acha/cria a
    conversa pelo telefone→lead e grava a mensagem (entrada). Abre a janela de 24h."""
    from finance import whatsapp_twilio as wa
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    assinatura = request.headers.get("X-Twilio-Signature", "")
    # a Twilio assina com a URL PÚBLICA exata que ela chamou. Atrás do proxy do
    # Render, request.url vem com host/esquema internos — então reconstruímos a
    # partir dos cabeçalhos encaminhados (Host público + https).
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        proto = request.headers.get("x-forwarded-proto", "https")
        url = f"{proto}://{host}{request.url.path}"
    else:
        url = wa.url_webhook() or str(request.url)
    if not wa.validar_assinatura(url, params, assinatura):
        import logging
        logging.getLogger("prospeccao").info(
            "webhook_twilio: assinatura inválida · url=%s · From=%s · To=%s",
            url, params.get("From", ""), params.get("To", ""))
        return Response(status_code=403)
    corpo = params.get("Body", "")
    remetente = _so_digitos(params.get("From", ""))
    destino = _so_digitos(params.get("To", ""))       # o número da empresa que recebeu
    sid = params.get("MessageSid") or params.get("SmsMessageSid")
    pool = get_pool()
    with pool.connection() as c:
        conta_id = _conta_por_ident(c, "whatsapp", destino)
        if not conta_id:
            return Response("<Response></Response>", media_type="application/xml")
        # --- RSVP de convite de reunião (botões do template) ---
        # O convite sai pelo número da empresa, então a resposta do convidado volta
        # PRA CÁ. Intercepta ANTES do inbox de prospecção, senão o "Confirmar"
        # viraria uma conversa de lead e o status do convite não mudaria.
        from finance import convites as _cv, whatsapp_out as _wout
        _txt = corpo or params.get("ButtonText") or params.get("ButtonPayload") or ""
        _st = _cv.rsvp_por_texto(_txt)
        if _st:
            _pend = _cv.pendentes_por_numero(pool, remetente)
            if _pend:
                _conv = _cv.responder(pool, _pend[0]["token"], _st, canal="whatsapp")
                if _conv:
                    # DEPOIS de `responder` o RSVP já está gravado, e daqui pra baixo é
                    # só cortesia: avisar o dono e agradecer ao convidado. Levantar aqui
                    # devolvia 500 pro Twilio, que reentrega a mesma mensagem — e na
                    # reentrega o convite já não está mais 'pendente', então
                    # `pendentes_por_numero` volta vazio, o fluxo cai no inbox e é a IA
                    # que responde. Foi o que o convidado leu em 23/08, logo após
                    # confirmar: "Confirmar presença em quê? Não tenho nada registrado
                    # aqui sobre um evento". O status estava certo no banco; quem errou
                    # foi a resposta. Por isso o try é largo e o `return` fica fora dele:
                    # confirmou, acabou — o resto não pode desfazer isso aos olhos de
                    # quem respondeu.
                    try:
                        if _conv.get("mudou"):                         # só avisa se mudou
                            _cv.pos_resposta(pool, _conv)              # aviso ao DONO: sempre
                        # a resposta ao CONVIDADO é opt-out por conta (Agenda › Lembrete)
                        from finance import agenda as _ag
                        if _ag.get_config(pool, _conv["conta_id"]).get("enviar_confirmacao", True):
                            # a conversa deste contato pode nem existir (convidado não é
                            # lead): sem ela o chip é o da própria empresa, que é o certo
                            # — e no Twilio, que é por onde o convite com botões sai,
                            # `chip_id` nem é lido. `conv_id` NÃO serve aqui: ele só é
                            # atribuído lá embaixo, no inbox, e usá-lo antes era
                            # exatamente o UnboundLocalError que derrubava esta rota.
                            _cvv = _conversa_wa_do_contato(c, conta_id, None, remetente)
                            _wout.enviar(c, conta_id, remetente, _cv.confirmacao_texto(_conv),
                                         chip_id=_wout.chip_da_conversa(
                                             c, conta_id, _cvv[0] if _cvv else None))
                        c.commit()
                    except Exception:  # noqa: BLE001
                        import logging
                        logging.getLogger("prospeccao.convites").warning(
                            "RSVP gravado, mas o pós-resposta falhou (conta=%s, token=%s)",
                            conta_id, _pend[0]["token"], exc_info=True)
                    return Response("<Response></Response>", media_type="application/xml")
        # --- Botões do template de 1º contato da prospecção (quick reply) ---
        # O convite frio sai com 3 botões; o clique volta PRA CÁ. Respondemos
        # deterministicamente (Instagram/material), esquentamos e paramos a sequência
        # ANTES do inbox/IA — assim "Quero o material" manda o material na hora, em
        # vez de virar uma conversa que depende da IA adivinhar. Só age se o número
        # for de um lead conhecido da conta; senão, segue o fluxo normal.
        from finance import prospec_inbound as _pi
        _botao = _pi.classificar(_txt)
        if _botao and _tratar_botao_prospec(c, conta_id, remetente, _botao, _txt, sid,
                                            params.get("ProfileName")):
            c.commit()
            return Response("<Response></Response>", media_type="application/xml")
        # o agente assume conversa nova quando o master está ligado (o vendedor pode
        # "assumir" depois, o que desliga o bot só naquela conversa).
        master = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                           (conta_id,)).fetchone()
        agente_on = bool(master and master[0])
        # Twilio: aqui existe campanha com template, então a trava da resposta
        # automática vale. O clique no botão não passa por aqui — `_tratar_botao_prospec`
        # já resolveu acima.
        conv_id, nova = _wa_inbound_conversa(c, conta_id, remetente, corpo, sid,
                                            params.get("ProfileName"), agente_on,
                                            exigir_continuidade=True)
        c.commit()
    # deixa o agente atender em background (não segura a resposta pro Twilio) — mas só
    # se a mensagem entrou agora: reentrega da mesma mensagem não merece outra resposta
    if nova:
        from finance import agente as _ag
        background_tasks.add_task(_ag.atender, get_pool(), conta_id, conv_id)
    return Response("<Response></Response>", media_type="application/xml")


# Twilio → status por mensagem (queued/sent/delivered/read/failed). Mapeia pro alvo
# da campanha pelo SID de saída (wa_sid) e sobe o status sem deixar rebaixar por
# callback fora de ordem (enviado < entregue < lido).
_WA_STATUS_MAP = {"delivered": "entregue", "read": "lido",
                  "sent": "enviado", "queued": "enviado", "sending": "enviado",
                  "failed": "erro", "undelivered": "erro"}

# 'erro' empata com 'enviado' de propósito. Os callbacks chegam fora de ordem —
# em produção veio "enviado → erro 63024 → enviado" em 3 segundos — e enquanto o
# erro valia 0 o "enviado" atrasado passava por cima e ressuscitava a falha como
# sucesso. Só sobrava o wa_erro_codigo órfão: o alvo saía da fila de reenvio, o
# KPI de erros contava a menos e o "Números não tentados" nem via o lead.
# Empatado, o "enviado" tardio não sobrescreve mais; 'entregue'/'lido' ainda sim,
# porque aí a mensagem chegou de verdade e o sucesso é a informação mais nova.
_WA_STATUS_RANK = {"erro": 1, "enviado": 1, "entregue": 2, "lido": 3, "respondeu": 4}
_SQL_RANK = ("case %s when 'enviado' then 1 when 'erro' then 1 "
             "when 'entregue' then 2 when 'lido' then 3 else 0 end")


def aplicar_status_wa(c, sid: str, novo: str, erro_codigo: str = "", erro_msg: str = "") -> None:
    """Aplica um status de entrega ao alvo da campanha e à mensagem do inbox.

    Ponto ÚNICO da regra, de propósito. São três provedores com payloads bem
    diferentes — Twilio (form do StatusCallback), Cloud API (statuses[] do webhook
    da Meta) e QR (itens do serviço Node) — e cada um tinha, ou ia ter, a sua
    própria cópia da lógica. Foi assim que o Cloud API ficou meses sem NUNCA marcar
    entregue/lido/erro no alvo: ele só lia o `pricing` pra corrigir custo.

    Cada webhook agora só traduz o payload dele em (sid, novo, erro) e chama aqui.
    `novo` já vem normalizado: 'enviado' | 'entregue' | 'lido' | 'erro'.
    Não faz commit — quem chama é dono da transação."""
    if novo == "erro":
        # erro não rebaixa quem já chegou a entregue/lido: aí a mensagem foi
        # entregue de verdade e o relato de falha é a informação velha.
        alvo = c.execute(
            """select id, prospeccao_id, campanha_id from campanha_alvos
                where wa_sid=%s and coalesce(wa_status,'') not in ('entregue','lido')""",
            (sid,)).fetchone()
        if alvo:
            # a mensagem saiu e não chegou: risca o número, conta a tentativa e
            # devolve o alvo pra fila enquanto sobrar número. Este é o caminho da
            # MAIORIA das falhas (63024 e afins) — o da API é a minoria.
            from finance.campanhas_motor import falha_na_entrega
            parou = falha_na_entrega(c, alvo[0], erro_codigo, erro_msg)
        # O MOTIVO junto, não só o status. O provedor manda ErrorCode/ErrorMessage
        # no callback e isto aqui descartava os dois: quem mandou via "⚠ falhou" e
        # mais nada. Aconteceu na conta 3 em 18/08 — uma mensagem 51h depois da
        # última resposta do contato, ou seja, fora da janela de 24h, e descobrir
        # isso exigiu cruzar o histórico da conversa na mão.
        #
        # Em alvo de campanha o motivo já era gravado (campanha_alvos.wa_erro_codigo);
        # o buraco era só a conversa comum do inbox. Vai pro `meta` da mensagem, que
        # é jsonb e já existe — sem tabela nova e sem coluna nova.
        erro_meta = {}
        if erro_codigo:
            erro_meta["erro_codigo"] = str(erro_codigo)
        if erro_msg:
            erro_meta["erro_msg"] = erro_msg[:300]
        if erro_meta:
            import json as _json
            c.execute("""update mensagens
                            set status='erro', meta = coalesce(meta,'{}'::jsonb) || %s::jsonb
                          where provider_sid=%s and coalesce(status,'') not in ('entregue','lido')""",
                      (_json.dumps(erro_meta), sid))
        else:
            c.execute("""update mensagens set status='erro'
                           where provider_sid=%s and coalesce(status,'') not in ('entregue','lido')""",
                      (sid,))
        if alvo:
            from finance.campanhas_motor import evento
            detalhe = (f"{erro_codigo}: {erro_msg}" if erro_codigo and erro_msg
                       else (erro_msg or erro_codigo))
            evento(c, alvo[2], alvo[1], "whatsapp",
                   "erro" if parou else "numero_falhou", detalhe)
        return
    # guarda de rank: nunca rebaixa (erro=enviado < entregue < lido)
    rank = _WA_STATUS_RANK[novo]
    cur = c.execute(
        "update campanha_alvos set wa_status=%s, wa_em=now() "
        "where wa_sid=%s and coalesce(" + _SQL_RANK.replace("%s", "wa_status") +
        ", 0) < %s returning prospeccao_id, campanha_id", (novo, sid, rank))
    transicao = cur.fetchone()
    c.execute(
        "update mensagens set status=%s "
        "where provider_sid=%s and coalesce(" + _SQL_RANK.replace("%s", "status") +
        ", 0) < %s", (novo, sid, rank))
    if transicao:
        from finance.campanhas_motor import evento
        evento(c, transicao[1], transicao[0], "whatsapp", novo)
        # leu no WhatsApp (sinal confiável) → esquenta o lead + avisa o vendedor
        if novo == "lido":
            from finance.campanhas_motor import engajou_lead
            engajou_lead(get_pool(), transicao[0], "leu seu WhatsApp", alerta=True)


@router.post("/webhooks/twilio-status")
async def webhook_twilio_status(request: Request):
    """StatusCallback do Twilio (entregue/lido/falhou) por mensagem de WhatsApp."""
    from finance import whatsapp_twilio as wa
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    assinatura = request.headers.get("X-Twilio-Signature", "")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        proto = request.headers.get("x-forwarded-proto", "https")
        url = f"{proto}://{host}{request.url.path}"
    else:
        url = wa.url_status() or str(request.url)
    if not wa.validar_assinatura(url, params, assinatura):
        return Response(status_code=403)
    msid = params.get("MessageSid") or params.get("SmsSid") or ""
    novo = _WA_STATUS_MAP.get((params.get("MessageStatus") or "").lower())
    if msid and novo:
        try:
            with get_pool().connection() as c:
                aplicar_status_wa(c, msid, novo,
                                  params.get("ErrorCode") or "",
                                  (params.get("ErrorMessage") or "")[:300])
                c.commit()
        except Exception:  # noqa: BLE001
            pass
    return Response("", media_type="text/plain")


def _descad_page(titulo: str, corpo: str) -> str:
    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{titulo}</title><style>body{{margin:0;background:#0b0b0c;color:#e9eae6;"
        "font-family:var(--body);display:grid;place-items:center;min-height:100vh}"
        ".c{max-width:440px;background:#141416;border:1px solid #26262b;border-radius:14px;padding:1.6rem;margin:1rem}"
        "h1{font-size:1.2rem;margin:0 0 .6rem}p{color:#b9beb6;line-height:1.5}"
        ".b{background:#e0574f;border:0;color:#fff;border-radius:9px;padding:.6rem 1rem;font-size:.95rem;cursor:pointer;font-family:inherit}"
        "</style></head><body><div class='c'><h1>" + titulo + "</h1>" + corpo + "</div></body></html>")


@router.get("/descadastrar", response_class=HTMLResponse)
def descadastrar_form(request: Request, t: str = ""):
    from finance.campanhas_motor import descad_verify
    conta_id, email = descad_verify(t)
    if not conta_id:
        return HTMLResponse(_descad_page("Link inválido", "<p>Esse link de descadastro não é válido ou expirou.</p>"),
                            status_code=400)
    import html as _h
    return HTMLResponse(_descad_page(
        "Descadastrar",
        f"<p>Confirmar que <b>{_h.escape(email)}</b> não quer mais receber estes e-mails?</p>"
        f"<form method='post' action='/descadastrar'><input type='hidden' name='t' value='{_h.escape(t)}'>"
        "<button class='b'>Sim, quero descadastrar</button></form>"))


@router.post("/descadastrar", response_class=HTMLResponse)
def descadastrar_do(request: Request, t: str = Form("")):
    from finance.campanhas_motor import descad_verify, registrar_descadastro
    conta_id, email = descad_verify(t)
    if not conta_id:
        return HTMLResponse(_descad_page("Link inválido", "<p>Link inválido.</p>"), status_code=400)
    try:
        registrar_descadastro(get_pool(), conta_id, email, t)
    except Exception:  # noqa: BLE001
        pass
    import html as _h
    return HTMLResponse(_descad_page("Pronto ✓",
        f"<p>Feito! <b>{_h.escape(email)}</b> não vai mais receber nossos e-mails. Obrigado! 🙏</p>"))


@router.post("/descadastrar-oc")
def descadastrar_oneclick(t: str = ""):
    """Descadastro em 1 clique (List-Unsubscribe-Post, RFC 8058). O Gmail/Yahoo faz
    POST aqui com o corpo 'List-Unsubscribe=One-Click'; a gente lê o token da URL,
    registra e devolve 200. Sem login, idempotente."""
    from finance.campanhas_motor import descad_verify, registrar_descadastro
    conta_id, email = descad_verify(t)
    if conta_id:
        try:
            registrar_descadastro(get_pool(), conta_id, email, t)
        except Exception:  # noqa: BLE001
            pass
    return Response("", media_type="text/plain")


# GIF transparente 1x1 (pixel de rastreio de abertura de e-mail)
_PIXEL_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


@router.get("/e/abrir.gif")
def email_pixel(t: str = ""):
    """Pixel de abertura: quando o e-mail é aberto e as imagens carregam, registra a
    abertura no alvo da campanha. Público (sem login) e best-effort — sempre devolve
    o GIF, mesmo com token inválido, pra nunca quebrar a imagem no cliente."""
    from finance.campanhas_motor import abrir_verify
    conta_id, pid, camp_id = abrir_verify(t)
    if conta_id:
        try:
            with get_pool().connection() as c:
                c.execute("""update campanha_alvos set aberturas=coalesce(aberturas,0)+1
                             where campanha_id=%s and prospeccao_id=%s""", (camp_id, pid))
                primeira = c.execute("""update campanha_alvos set aberto_em=now()
                                          where campanha_id=%s and prospeccao_id=%s and aberto_em is null
                                          returning 1""", (camp_id, pid)).fetchone()
                if primeira:
                    from finance.campanhas_motor import evento
                    evento(c, camp_id, pid, "email", "aberto")
                c.commit()
            if primeira:   # 1ª abertura → timeline + esquenta (sem alerta: Gmail infla abertura)
                from finance.campanhas_motor import engajou_lead
                engajou_lead(get_pool(), pid, "abriu o e-mail", alerta=False)
        except Exception:  # noqa: BLE001
            pass
    return Response(content=_PIXEL_GIF, media_type="image/gif",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                             "Pragma": "no-cache"})


@router.get("/tenho-interesse", response_class=HTMLResponse)
def tenho_interesse(request: Request, t: str = ""):
    """CTA da campanha: o lead clicou 'Tenho interesse'. Para a sequência, esquenta o
    lead, manda o material na hora e o agente IA assume. Página pública (sem login)."""
    from finance.campanhas_motor import interesse_verify, registrar_interesse
    conta_id, pid, camp_id = interesse_verify(t)
    if not conta_id:
        return HTMLResponse(_descad_page("Link inválido", "<p>Esse link não é válido ou expirou.</p>"),
                            status_code=400)
    try:
        registrar_interesse(get_pool(), conta_id, pid, camp_id)
    except Exception:  # noqa: BLE001
        pass
    return HTMLResponse(_descad_page("Recebemos! 🎉",
        "<p>Obrigado pelo interesse! Acabamos de te enviar um material por e-mail e "
        "já vamos falar com você. 😊</p>"))


@router.get("/privacidade", response_class=HTMLResponse)
def politica_privacidade(request: Request):
    """Política de Privacidade pública (para publicar o app na Meta e para os
    usuários). Página estática, sem login."""
    corpo = (
        "<p style='text-align:left'>Esta Política descreve como o aplicativo "
        "<b>Zaq Lead</b>, operado por <b>Aladdin Consultoria e Tecnologia</b> (\"ZAQ\"), "
        "trata dados ao integrar canais de mensagem (WhatsApp, Instagram, Messenger e "
        "e-mail) para atendimento e prospecção B2B.</p>"
        "<h3 style='text-align:left'>Dados que tratamos</h3>"
        "<p style='text-align:left'>Dados de contato de empresas e leads (nome, e-mail, "
        "telefone/WhatsApp, perfil de Instagram) e o conteúdo das mensagens trocadas nos "
        "canais conectados, para registrar a conversa e responder o interessado.</p>"
        "<h3 style='text-align:left'>Como usamos</h3>"
        "<p style='text-align:left'>Exclusivamente para prestar o serviço de atendimento/"
        "CRM ao cliente titular da conta: receber mensagens, responder (inclusive por "
        "assistente de IA) e organizar o relacionamento comercial. <b>Não vendemos</b> "
        "dados pessoais e não os usamos para finalidades alheias ao serviço.</p>"
        "<h3 style='text-align:left'>Plataformas da Meta</h3>"
        "<p style='text-align:left'>Ao conectar Instagram/Messenger/WhatsApp, usamos as "
        "APIs oficiais da Meta apenas para receber e enviar mensagens dentro das regras "
        "da plataforma (por exemplo, a janela de 24h para respostas).</p>"
        "<h3 style='text-align:left'>Retenção e exclusão</h3>"
        "<p style='text-align:left'>Mantemos os dados enquanto durar a relação com o "
        "cliente titular. Para solicitar acesso, correção ou <b>exclusão</b> dos seus "
        "dados, escreva para <b>thompsoncf@hotmail.com</b> — atendemos no prazo legal.</p>"
        "<h3 style='text-align:left'>Contato</h3>"
        "<p style='text-align:left'>Aladdin Consultoria e Tecnologia — "
        "thompsoncf@hotmail.com</p>"
        "<p style='text-align:left;color:#888;font-size:.85rem'>Última atualização: 2026.</p>")
    return HTMLResponse(_descad_page("Política de Privacidade — ZAQ", corpo))


@router.get("/material")
def abrir_material(t: str = "", canal: str = "email"):
    """Link rastreado do material (PDF/foto/vídeo/link) mandado por e-mail ou
    WhatsApp: loga o evento 'baixou' no histórico da campanha e manda o lead pro
    arquivo/link de verdade. Página pública (sem login) — é o que o lead clica."""
    from finance.campanhas_motor import material_verify, evento as _ev
    conta_id, pid, camp_id = material_verify(t)
    if not conta_id:
        return HTMLResponse(_descad_page("Link inválido", "<p>Esse link não é válido ou expirou.</p>"),
                            status_code=400)
    with get_pool().connection() as c:
        row = c.execute("select coalesce(material,'') from campanhas where id=%s and conta_id=%s",
                        (camp_id, conta_id)).fetchone()
        url = (row[0] if row else "") or ""
        if url:
            _ev(c, camp_id, pid, canal if canal in ("email", "whatsapp") else "email", "baixou")
            c.commit()
    if not url:
        return HTMLResponse(_descad_page("Material indisponível",
            "<p>Esse material não está mais disponível — chama a gente que a "
            "gente te manda de novo.</p>"), status_code=404)
    return RedirectResponse(url, status_code=302)


def _conta_por_meta(c, plataforma, ident):
    """Roteia o inbound da Meta: acha a empresa dona da Página/IG que recebeu."""
    r = c.execute(
        "select conta_id from canais_config where canal=%s and ativo and identificador=%s limit 1",
        (plataforma, str(ident))).fetchone()
    return r[0] if r else None


def _conversa_meta(c, conta_id, plataforma, sender):
    """Acha/cria a conversa (órfã, sem lead) daquele remetente Messenger/Instagram."""
    conv = c.execute(
        """select id from conversas where conta_id=%s and canal=%s and prospeccao_id is null
            and contato_ref=%s order by ultima_msg_em desc limit 1""",
        (conta_id, plataforma, str(sender))).fetchone()
    if conv:
        return conv[0]
    return c.execute(
        """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, status, ultima_msg_em)
           values (%s,null,%s,%s,'aberta',now()) returning id""",
        (conta_id, plataforma, str(sender))).fetchone()[0]


@router.get("/webhooks/meta")
def webhook_meta_verify(request: Request):
    """Verificação do webhook da Meta (GET com hub.challenge)."""
    from finance import meta_msg
    q = request.query_params
    ch = meta_msg.verificar_challenge(q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"))
    if ch is None:
        return Response(status_code=403)
    return Response(ch, media_type="text/plain")


@router.post("/webhooks/meta")
async def webhook_meta(request: Request, background_tasks: BackgroundTasks):
    """Recebe mensagens de Messenger + Instagram (Meta). Valida a assinatura, roteia
    pela Página/IG que recebeu, grava como conversa órfã (você decide se vira lead) e
    dispara o agente."""
    from finance import meta_msg
    body = await request.body()
    if not meta_msg.validar_assinatura(body, request.headers.get("x-hub-signature-256", "")):
        return Response(status_code=403)
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return Response("ok", media_type="text/plain")
    eventos = meta_msg.parse_eventos(payload)
    pool = get_pool()
    disparar = []
    with pool.connection() as c:
        for ev in eventos:
            if ev["plataforma"] == "whatsapp":
                # WhatsApp Cloud API (número próprio): roteia pelo phone_number_id
                r = c.execute("""select conta_id from canais_config
                                  where canal='whatsapp' and ativo and wa_phone_id=%s limit 1""",
                              (ev["conta_ident"],)).fetchone()
                conta_id = r[0] if r else None
                if not conta_id:
                    continue
                m = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                              (conta_id,)).fetchone()
                agente_on = bool(m and m[0])
                # Cloud API: mesma trava do Twilio — é o outro provedor com template.
                conv_id, nova = _wa_inbound_conversa(c, conta_id, ev["sender"], ev["texto"],
                                                     ev.get("sid"), ev.get("nome"), agente_on,
                                                     exigir_continuidade=True)
                if nova and _agente_atende(c, conv_id, agente_on):
                    disparar.append((conta_id, conv_id))
                continue
            conta_id = _conta_por_meta(c, ev["plataforma"], ev["conta_ident"])
            if not conta_id:
                continue
            conv_id = _conversa_meta(c, conta_id, ev["plataforma"], ev["sender"])
            _add_msg(c, conv_id, ev["plataforma"], "in", "lead", ev["texto"])
            master = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                               (conta_id,)).fetchone()
            if master and master[0]:
                c.execute("update conversas set agente_ativo=true where id=%s and status<>'pendente'",
                          (conv_id,))
                disparar.append((conta_id, conv_id))
        # custo real do WhatsApp: o status da Cloud API traz a categoria e se é cobrável
        # (pricing). Corrige a estimativa gravada no envio — ex.: FEP vem grátis.
        from finance import wa_precos as _wp
        for stt in meta_msg.parse_status_whatsapp(payload):
            cobr = stt["cobravel"]
            custo = _wp.custo_brl(stt["categoria"], cobr if cobr is not None else True)
            # `wa_custo` é o acumulado do alvo (a fila manda até 3 mensagens pra ele).
            # Corrigir é TROCAR a parcela desta mensagem, não substituir o total —
            # senão o preço real da última apagaria o que as anteriores custaram, e o
            # teto da campanha voltava a não ver o reenvio.
            # Reentrante: se o mesmo status chegar duas vezes, tira e põe o mesmo
            # valor. Ver db/migracoes/168_campanha_alvo_custo_por_mensagem.sql.
            c.execute("""update campanha_alvos
                           set wa_categoria=%s, wa_cobravel=%s,
                               wa_custo=greatest(coalesce(wa_custo,0)
                                                 - coalesce(wa_custo_msg,0) + %s, 0),
                               wa_custo_msg=%s
                         where wa_sid=%s""",
                      (stt["categoria"] or None, cobr, custo, custo, stt["sid"]))
        # ENTREGA: mesma regra do Twilio, num ponto só (aplicar_status_wa). Isto
        # faltava — o Cloud API lia só o `pricing` e nunca marcava entregue/lido/erro
        # no alvo, então os KPIs ficavam zerados e a fila de números não andava.
        for ent in meta_msg.parse_entrega_whatsapp(payload):
            aplicar_status_wa(c, ent["sid"], ent["status"],
                              ent["erro_codigo"], ent["erro_msg"])
        c.commit()
    from finance import agente as _ag
    for (cid, cvid) in disparar:
        background_tasks.add_task(_ag.atender, get_pool(), cid, cvid)
    return Response("ok", media_type="text/plain")


def _qr_segredo_ok(request: Request) -> bool:
    """O serviço Node se identifica pelo segredo compartilhado. `compare_digest`
    em vez de `==` pra não vazar o segredo pelo tempo de resposta."""
    import hmac
    segredo = os.environ.get("WA_QR_SHARED_SECRET") or ""
    return bool(segredo) and hmac.compare_digest(
        str(request.headers.get("x-wa-secret") or ""), segredo)


def _conta_em_qr(c, conta_id: int) -> bool:
    """A empresa está mesmo com o WhatsApp no modo QR? O segredo é UM só pro
    serviço inteiro — sem esta segunda trava, um conta_id forjado (ou trocado por
    engano) tocaria os dados de qualquer outra empresa, inclusive as do Twilio."""
    return bool(c.execute(
        """select 1 from canais_config
             where conta_id=%s and canal='whatsapp' and ativo
               and coalesce(provedor,'twilio')='qr'""", (conta_id,)).fetchone())


def _resolver_chip(c, chip_id: int) -> tuple[int, int] | None:
    """`(empresa_id, chip_id)` do id que o wa-qr mandou — ou None se ele não é um
    canal QR válido.

    O serviço manda o id do CHIP que recebeu a mensagem; para ele "conta_id" sempre
    foi só o número de uma conexão (ele nem conhece a tabela `contas`). Enquanto cada
    empresa tem um chip só, os dois números coincidem. Com um segundo chip, a linha
    dele em `contas` traz `chip_de` apontando pro dono — e é ISSO que faz o lead do
    chip 2 nascer no funil da mesma empresa, em vez de virar uma conta à parte.

    Duas coisas que parecem detalhe e não são:

    * o portão olha o `canais_config` DO CHIP, não o da empresa. É o que permite
      desligar o chip 2 sem desligar o chip 1 — e é o mesmo motivo pelo qual o
      /deslogado não pode traduzir (ver lá).
    * `coalesce(chip_de, id)` devolve o próprio id quando `chip_de` é nulo, que é o
      estado de todas as 22 contas hoje. Com uma empresa de um chip só, esta função
      responde exatamente o que `_conta_em_qr` respondia.
    """
    r = c.execute(
        """select coalesce(ct.chip_de, ct.id)
             from contas ct
             join canais_config cc on cc.conta_id = ct.id
            where ct.id = %s and cc.canal='whatsapp' and cc.ativo
              and coalesce(cc.provedor,'twilio')='qr'""", (chip_id,)).fetchone()
    return (int(r[0]), int(chip_id)) if r else None


def _chip_gravavel(chip_id, empresa_id):
    """O que vai em `conversas.chip_id`: o chip, ou NULO quando ele É a empresa.

    Nulo já significa "responde pelo chip da própria empresa" — é o que valem as 1.101
    conversas que existem hoje. Gravar o id da empresa ali diria a mesma coisa com
    outro valor, e aí passariam a existir duas formas de dizer "chip principal": a
    consulta que filtrasse por uma delas erraria metade das conversas, dependendo de
    terem entrado antes ou depois desta mudança.
    """
    return None if (chip_id is None or int(chip_id) == int(empresa_id)) else int(chip_id)


@router.post("/webhooks/wa-qr")
async def webhook_wa_qr(request: Request, background_tasks: BackgroundTasks):
    """Entrada do WhatsApp por QR (serviço Node services/wa-qr). Autentica pelo
    segredo compartilhado, roteia pela conta_id que o serviço já resolveu e trata
    igual aos outros canais (lead + agente).

    Logado em cada ponto de saída silenciosa (webhook_wa_qr:...) — o serviço Node
    só responde "ok" mesmo quando descarta, então sem log aqui não dava pra saber
    ONDE uma mensagem se perdia (segredo errado, payload incompleto, canal não
    configurado como 'qr', etc.).

    O trabalho de banco roda na THREADPOOL. O corpo deste handler e' psycopg
    SINCRONO; deixado no event loop ele congela o worker inteiro -- painel
    incluso -- a cada mensagem que entra. Com dois workers no Render bastava os
    dois estarem numa chamada dessas pra fila toda parar: em 22/08/2026 a
    resposta saiu de 527 ms pra ~50 s com a CPU em 0,7%, ou seja, esperando e
    nao trabalhando. Mesmo motivo do webhook_wa_qr_contatos, que ja' fazia assim.
    """
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    if not _qr_segredo_ok(request):
        log.warning("webhook_wa_qr: segredo ausente ou não confere (WA_QR_SHARED_SECRET "
                    "configurado=%s)", bool(os.environ.get("WA_QR_SHARED_SECRET")))
        return Response(status_code=403)
    corpo = await request.body()
    return await run_in_threadpool(_webhook_wa_qr_sync, corpo, background_tasks)


def _webhook_wa_qr_sync(corpo: bytes, background_tasks: BackgroundTasks):
    """O trabalho de verdade — sincrono, fora do event loop."""
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    try:
        payload = json.loads(corpo.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        log.warning("webhook_wa_qr: corpo não é JSON válido")
        return Response("ok", media_type="text/plain")
    try:
        conta_id = int(payload.get("conta_id") or 0)
    except (TypeError, ValueError):
        conta_id = 0
    sender = str(payload.get("sender") or "").strip()
    texto = (payload.get("texto") or "").strip()
    log.info("webhook_wa_qr: recebido conta_id=%s sender=%s%s texto_len=%d",
             conta_id, sender[:4], "…" if sender else "", len(texto))
    if not conta_id or not sender or not texto:
        log.warning("webhook_wa_qr: payload incompleto (conta_id=%s sender=%s texto=%s) — descartado",
                    bool(conta_id), bool(sender), bool(texto))
        return Response("ok", media_type="text/plain")
    pool = get_pool()
    with pool.connection() as c:
        # confere que o chip está mesmo no modo QR (evita id forjado tocar outra via)
        # e traduz chip -> empresa. Com um chip só, empresa == chip, como sempre foi.
        alvo = _resolver_chip(c, conta_id)
        if not alvo:
            log.warning("webhook_wa_qr: chip=%s sem canal whatsapp/qr ativo em canais_config — descartado",
                        conta_id)
            return Response("ok", media_type="text/plain")
        empresa_id, chip_id = alvo
        m = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                      (empresa_id,)).fetchone()
        agente_on = bool(m and m[0])
        conv_id, nova = _wa_inbound_conversa(c, empresa_id, sender, texto,
                                            payload.get("id") or None, payload.get("nome"),
                                            agente_on, chip_id=chip_id)
        # lê DENTRO da transação: o update acima já valeu, então a conversa ligada à
        # mão aparece aqui mesmo com o agente-mestre desligado (ver _agente_atende).
        # `nova` corta a reentrega: o wa-qr manda a mesma mensagem de novo quando a
        # conexão oscila, e sem isto o cliente recebia uma resposta por entrega.
        atender = nova and _agente_atende(c, conv_id, agente_on)
        c.commit()
    log.info("webhook_wa_qr: chip=%s empresa=%s conv_id=%s gravado ✓ (mestre=%s nova=%s atende=%s)",
             chip_id, empresa_id, conv_id, agente_on, nova, atender)
    if atender:
        from finance import agente as _ag
        background_tasks.add_task(_ag.atender, get_pool(), empresa_id, conv_id)
    return Response("ok", media_type="text/plain")


def _wa_historico_conversa(c, conta_id, remetente, corpo, sid, quando, de_mim=False,
                           nome_perfil="", *, chip_id=None) -> int:
    """WhatsApp IMPORTADO do histórico (mensagem de ANTES de conectar por QR, ver
    /webhooks/wa-qr/historico): grava a conversa preservando a data original da
    mensagem. `de_mim=True` = mensagem que o VENDEDOR enviou (o histórico traz os
    dois lados; sem isso a conversa importada ficava pela metade). NUNCA cria ou
    promove prospecção sozinho — ao contrário de _wa_inbound_conversa (mensagem
    NOVA, em tempo real). O vendedor decide se vale virar lead (botão "virar
    lead" no inbox de Comunicação). Reusa QUALQUER conversa do número (órfã ou já
    ligada a lead — preferindo a ligada), senão um re-pareamento duplicava a aba
    de quem já tinha virado lead. "Do número" nas duas grafias, com e sem o nono
    dígito (ver _wa_equivalentes): o histórico é justamente onde chega o formato
    antigo, e casando cru ele virava uma segunda conversa do mesmo contato."""
    remetente = _so_digitos(remetente)
    alvo8 = remetente[-8:] if len(remetente) >= 8 else remetente
    conv = _conversa_wa_do_contato(c, conta_id, None, remetente, chip_id=chip_id)
    if conv:
        conv_id = conv[0]
    else:
        # já nasce com o nome da agenda: na importação inicial a agenda chega ANTES
        # das mensagens, então renomear depois não alcançaria estas conversas
        conv_id = c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, status,
                 agente_ativo, ultima_msg_em, contato_nome, chip_id)
               values (%s,null,'whatsapp',%s,'aberta',false,coalesce(%s,now()),
                       (select nome from wa_contatos where conta_id=%s and numero8=%s),%s)
               returning id""",
            (conta_id, remetente, quando, conta_id, alvo8,
             _chip_gravavel(chip_id, conta_id))).fetchone()[0]
    c.execute(
        """insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid, criado_em)
             values (%s,'whatsapp',%s,%s,%s,%s,coalesce(%s,now()))
             on conflict (conversa_id, provider_sid) where provider_sid is not null do nothing""",
        (conv_id, "out" if de_mim else "in", "humano" if de_mim else "lead",
         (corpo or "")[:8000], sid, quando))
    c.execute("""update conversas set ultima_msg_em=greatest(ultima_msg_em, coalesce(%s,now())),
                   contato_nome=coalesce(nullif(contato_nome,''), nullif(%s,'')) where id=%s""",
              (quando, (nome_perfil or "").strip()[:120], conv_id))
    # a importação de histórico é a que mais chega ATRASADA — é justamente ela que
    # trouxe o nome sete minutos depois do lead nascer sem ele.
    _batiza_lead_pendente(c, conta_id, conv_id)
    return conv_id


@router.post("/webhooks/wa-qr/historico")
async def webhook_wa_qr_historico(request: Request):
    """Entrada do HISTÓRICO de WhatsApp por QR (serviço Node services/wa-qr, evento
    messaging-history.set do Baileys — só dispara logo depois de conectar/parear,
    limitado aos últimos 30 dias no lado do Node). Mensagens antigas viram conversa
    ÓRFÃ (sem lead automático) — ver _wa_historico_conversa. Só o vendedor decide
    se vale virar lead pra um número antigo.

    O trabalho de banco roda na THREADPOOL. O corpo deste handler e' psycopg
    SINCRONO; deixado no event loop ele congela o worker inteiro -- painel
    incluso -- a cada mensagem que entra. Com dois workers no Render bastava os
    dois estarem numa chamada dessas pra fila toda parar: em 22/08/2026 a
    resposta saiu de 527 ms pra ~50 s com a CPU em 0,7%, ou seja, esperando e
    nao trabalhando. Mesmo motivo do webhook_wa_qr_contatos, que ja' fazia assim.
    """
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    if not _qr_segredo_ok(request):
        log.warning("webhook_wa_qr_historico: segredo ausente ou não confere")
        return Response(status_code=403)
    corpo = await request.body()
    return await run_in_threadpool(_webhook_wa_qr_historico_sync, corpo)


def _webhook_wa_qr_historico_sync(corpo: bytes):
    """O trabalho de verdade — sincrono, fora do event loop."""
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    try:
        payload = json.loads(corpo.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        log.warning("webhook_wa_qr_historico: corpo não é JSON válido")
        return Response("ok", media_type="text/plain")
    try:
        conta_id = int(payload.get("conta_id") or 0)
    except (TypeError, ValueError):
        conta_id = 0
    sender = str(payload.get("sender") or "").strip()
    texto = (payload.get("texto") or "").strip()
    quando = None
    ts = payload.get("quando")
    if ts:
        try:
            quando = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            quando = None
    if not conta_id or not sender or not texto:
        return Response("ok", media_type="text/plain")
    pool = get_pool()
    with pool.connection() as c:
        alvo = _resolver_chip(c, conta_id)
        if not alvo:
            log.warning("webhook_wa_qr_historico: chip=%s sem canal whatsapp/qr ativo — descartado",
                        conta_id)
            return Response("ok", media_type="text/plain")
        empresa_id, chip_id = alvo
        conv_id = _wa_historico_conversa(c, empresa_id, sender, texto, payload.get("id") or None,
                                         quando, de_mim=bool(payload.get("de_mim")),
                                         nome_perfil=str(payload.get("nome") or ""),
                                         chip_id=chip_id)
        c.commit()
    log.info("webhook_wa_qr_historico: chip=%s empresa=%s conv_id=%s importado ✓",
             chip_id, empresa_id, conv_id)
    return Response("ok", media_type="text/plain")


def _wa_saida_conversa(c, conta_id, destinatario, corpo, sid, *, chip_id=None):
    """Mensagem que o VENDEDOR mandou DIRETO pelo WhatsApp do celular (fora do
    Zaq) — o Baileys ecoa de volta como fromMe. Dedup por (conversa_id,
    provider_sid): se a mensagem já saiu PELO Zaq (que grava na hora do envio em
    comunicacao_responder), a inserção aqui vira no-op. O par com a conversa é o
    que impede o dedup de atravessar contas — ver migração 159.

    NUNCA cria LEAD: quem o vendedor procura pelo celular não vira lead sozinho —
    o funil é dele pra encher, não do WhatsApp. Mas CRIA CONVERSA (órfã, sem
    lead), e é aí que estava o buraco: antes, eco sem conversa existente era
    descartado em silêncio, e quem escreve PRIMEIRO pelo celular é o caso normal
    do vendedor. Visto em produção: às 15:56 o vendedor mandou um "oi" pra um
    número novo e sumiu; às 15:57 a pessoa respondeu, a resposta criou o lead e a
    conversa — e a thread nasceu começando pela RESPOSTA, sem a pergunta. É o
    mesmo tratamento que o histórico importado já dá (_wa_historico_conversa):
    a conversa aparece na caixa, e o vendedor decide se vira lead.

    A conversa é procurada pelas duas grafias do número (com e sem o nono
    dígito — ver _wa_equivalentes), e sem exigir que ela esteja órfã: conversa já
    ligada a um lead casava só pelo `prospeccao_id`, então bastava o número estar
    numa ficha DIFERENTE (dois leads com o mesmo telefone) pra ela não ser
    encontrada e a mensagem sumir."""
    destinatario = _so_digitos(destinatario)
    alvo8 = destinatario[-8:] if len(destinatario) >= 8 else destinatario
    # sem roubar o lead do OUTRO chip, pela mesma razão do inbound: numa empresa de
    # dois números o vendedor que escreve pelo celular do chip 2 não pode pendurar a
    # mensagem na ficha que pertence ao chip 1.
    lead = c.execute(
        r"""select p.id from prospeccao p
             where p.conta_id=%s
               and right(regexp_replace(coalesce(p.whatsapp, p.telefone, ''), '\D', '', 'g'), 8) = %s
               and not exists (select 1 from conversas cv
                                where cv.conta_id=p.conta_id and cv.prospeccao_id=p.id
                                  and cv.canal='whatsapp'
                                  and cv.chip_id is distinct from %s)
             order by p.atualizado_em desc limit 1""",
        (conta_id, alvo8, _chip_gravavel(chip_id, conta_id))).fetchone()
    lead_id = lead[0] if lead else None
    conv = _conversa_wa_do_contato(c, conta_id, lead_id, destinatario, chip_id=chip_id)
    if conv:
        conv_id = conv[0]
    else:
        # nasce já com o nome da agenda do celular, igual aos outros caminhos — sem
        # isso a conversa que o vendedor abriu aparece na caixa como número cru
        conv_id = c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, status,
                 agente_ativo, ultima_msg_em, contato_nome, chip_id)
               values (%s,%s,'whatsapp',%s,'aberta',false,now(),
                       (select nome from wa_contatos where conta_id=%s and numero8=%s),%s)
               returning id""",
            (conta_id, lead_id, destinatario, conta_id, alvo8,
             _chip_gravavel(chip_id, conta_id))).fetchone()[0]
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid)
                 values (%s,'whatsapp','out','humano',%s,%s)
                 on conflict (conversa_id, provider_sid) where provider_sid is not null do nothing""",
              (conv_id, (corpo or "")[:8000], sid))
    c.execute("update conversas set ultima_msg_em=greatest(ultima_msg_em, now()) where id=%s", (conv_id,))
    return conv_id


@router.post("/webhooks/wa-qr/saida")
async def webhook_wa_qr_saida(request: Request):
    """Eco de mensagem que o vendedor mandou DIRETO pelo WhatsApp do celular
    (sem passar pelo Zaq) — pra não ficar cego do que já foi respondido. Abre a
    conversa quando ela ainda não existe (o vendedor escrevendo primeiro é o caso
    normal); lead, nunca — ver _wa_saida_conversa.

    O trabalho de banco roda na THREADPOOL. O corpo deste handler e' psycopg
    SINCRONO; deixado no event loop ele congela o worker inteiro -- painel
    incluso -- a cada mensagem que entra. Com dois workers no Render bastava os
    dois estarem numa chamada dessas pra fila toda parar: em 22/08/2026 a
    resposta saiu de 527 ms pra ~50 s com a CPU em 0,7%, ou seja, esperando e
    nao trabalhando. Mesmo motivo do webhook_wa_qr_contatos, que ja' fazia assim.
    """
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    if not _qr_segredo_ok(request):
        return Response(status_code=403)
    corpo = await request.body()
    return await run_in_threadpool(_webhook_wa_qr_saida_sync, corpo)


def _webhook_wa_qr_saida_sync(corpo: bytes):
    """O trabalho de verdade — sincrono, fora do event loop."""
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    try:
        payload = json.loads(corpo.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return Response("ok", media_type="text/plain")
    try:
        conta_id = int(payload.get("conta_id") or 0)
    except (TypeError, ValueError):
        conta_id = 0
    destinatario = str(payload.get("sender") or "").strip()
    texto = (payload.get("texto") or "").strip()
    if not conta_id or not destinatario or not texto:
        return Response("ok", media_type="text/plain")
    pool = get_pool()
    with pool.connection() as c:
        alvo = _resolver_chip(c, conta_id)
        if not alvo:
            return Response("ok", media_type="text/plain")
        empresa_id, chip_id = alvo
        conv_id = _wa_saida_conversa(c, empresa_id, destinatario, texto,
                                     payload.get("id") or None, chip_id=chip_id)
        c.commit()
    if conv_id:
        log.info("webhook_wa_qr_saida: chip=%s empresa=%s conv_id=%s registrado ✓",
                 chip_id, empresa_id, conv_id)
    else:
        # não deve mais acontecer (a conversa é criada quando falta), então isto aqui
        # é sinal de defeito de verdade — número impossível de normalizar, insert que
        # não voltou id. Fica gravado pra não sumir em silêncio como sumia antes.
        log.warning("webhook_wa_qr_saida: empresa=%s não consegui registrar o eco do número %s…",
                    empresa_id, destinatario[:6])
    return Response("ok", media_type="text/plain")


@router.post("/webhooks/wa-qr/contatos")
async def webhook_wa_qr_contatos(request: Request):
    """Nomes de contato vindos do WhatsApp por QR. `da_agenda=True` = fullName da
    AGENDA do celular (o nome que o vendedor salvou, via sincronização de
    app-state) — é o melhor nome que existe e SOBRESCREVE o que estiver lá.
    `da_agenda=False` = pushName (nome que a própria pessoa pôs no perfil), que
    é só reserva: preenche apenas quando a conversa ainda não tem nome.

    GUARDA o nome em wa_contatos e ATUALIZA a conversa existente. Guardar é o que
    faz a importação inicial funcionar: o WhatsApp manda a agenda ANTES/no meio da
    enxurrada de mensagens, então quase toda conversa ainda não existe quando o
    nome chega — antes disso o nome era descartado e a conversa nascia com o número
    cru. Nunca cria conversa nem lead a partir de um contato da agenda (senão a
    agenda inteira do vendedor viraria conversa).

    O trabalho no banco é DUAS queries por lote (não duas por contato) e roda em
    thread separada — ver _gravar_contatos_wa. O caminho de desistência devolve
    'ok' de propósito: o serviço Node só loga o não-ok e segue, e a agenda volta
    inteira no próximo resync, então recusar não recuperaria nada."""
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    if not _qr_segredo_ok(request):
        return Response(status_code=403)
    try:
        payload = json.loads((await request.body()).decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return Response("ok", media_type="text/plain")
    try:
        conta_id = int(payload.get("conta_id") or 0)
    except (TypeError, ValueError):
        conta_id = 0
    contatos = payload.get("contatos") or []
    if not conta_id or not isinstance(contatos, list) or not contatos:
        return Response("ok", media_type="text/plain")
    da_agenda = bool(payload.get("da_agenda"))

    por_numero = _dedup_contatos_wa(contatos, da_agenda)
    if not por_numero:
        return Response("ok", media_type="text/plain")

    from starlette.concurrency import run_in_threadpool
    # psycopg é síncrono: rodar aqui no event loop travaria o servidor INTEIRO
    # (painel incluso) enquanto o lote não terminasse. Foi o que fez a
    # importação inicial de uma agenda virar 502 em série no Render.
    n = await run_in_threadpool(
        _gravar_contatos_wa, conta_id, list(por_numero.keys()),
        list(por_numero.values()), da_agenda)
    if n:
        log.info("webhook_wa_qr_contatos: conta_id=%s %s conversas renomeadas (agenda=%s)",
                 conta_id, n, da_agenda)
    return Response("ok", media_type="text/plain")


def _dedup_contatos_wa(contatos: list, da_agenda: bool) -> dict[str, str]:
    """Peneira o lote cru do WhatsApp em {numero8: nome}, no máximo 500.

    Dedupar é obrigatório, não otimização: a agenda do celular repete contato
    (mesmo número salvo duas vezes, ou dois números cujos 8 finais coincidem) e
    `on conflict do update` estoura com "cannot affect row a second time" se a
    mesma chave aparecer duas vezes no MESMO comando.

    Quem fica é o mesmo que ficava no laço de antes: com a agenda, o ÚLTIMO
    (cada volta sobrescrevia); com pushName, o PRIMEIRO (depois da primeira
    volta a conversa já tinha nome, e o resto batia no coalesce e desistia)."""
    por_numero: dict[str, str] = {}
    for ct in contatos[:500]:
        numero = _so_digitos(str((ct or {}).get("numero") or ""))
        nome = str((ct or {}).get("nome") or "").strip()[:120]
        if not numero or not nome:
            continue
        chave = numero[-8:] if len(numero) >= 8 else numero
        if da_agenda or chave not in por_numero:
            por_numero[chave] = nome
    return por_numero


def _gravar_contatos_wa(conta_id: int, numeros8: list[str], nomes: list[str],
                        da_agenda: bool, pool=None) -> int:
    """Grava o lote de contatos e renomeia as conversas. SÍNCRONO — quem chama é
    responsável por tirar do event loop.

    Duas queries pro lote todo, via unnest dos dois arrays em paralelo. Antes era
    um insert + um update POR CONTATO, e o update varre conversas quando não há
    índice pra expressão dos 8 dígitos (ver migração 156) — 200 contatos viravam
    400 idas ao banco e 200 varreduras, tudo travando o event loop.

    `pool` só existe pros testes apontarem pro banco descartável; em produção
    fica None e vale o pool de sempre.

    Devolve quantas conversas foram renomeadas."""
    with (pool or get_pool()).connection() as c:
        # traduz: a agenda é o caderno de contatos DA EMPRESA. Os dois chips podem
        # ter listas diferentes no celular, e aqui elas se juntam — mesmo número
        # salvo com nomes diferentes fica com o do sync mais recente. É o
        # comportamento certo pra uma empresa só, mas é escolha, não consequência.
        alvo = _resolver_chip(c, conta_id)
        if not alvo:
            return 0
        conta_id = alvo[0]
        # guarda na agenda (vale pra conversa que ainda NEM EXISTE). Nome da
        # agenda sobrescreve; pushName não derruba um nome que veio da agenda.
        c.execute("""insert into wa_contatos (conta_id, numero8, nome, da_agenda, atualizado)
                     select %s, t.n8, t.nome, %s, now()
                       from unnest(%s::text[], %s::text[]) as t(n8, nome)
                     on conflict (conta_id, numero8) do update
                        set nome=excluded.nome, da_agenda=excluded.da_agenda,
                            atualizado=now()
                      where excluded.da_agenda or not wa_contatos.da_agenda""",
                  (conta_id, da_agenda, numeros8, nomes))
        # nome da agenda manda; pushName só entra se ainda não houver nome
        cond = "" if da_agenda else " and coalesce(conversas.contato_nome,'')=''"
        r = c.execute(
            r"""update conversas set contato_nome = t.nome
                  from unnest(%s::text[], %s::text[]) as t(n8, nome)
                 where conversas.conta_id=%s and conversas.canal='whatsapp'
                   and right(regexp_replace(conversas.contato_ref, '\D', '', 'g'), 8) = t.n8"""
            + cond, (numeros8, nomes, conta_id))
        n = r.rowcount or 0
        # a agenda chegando tarde é o outro jeito de o nome aparecer depois do lead
        # nascer. Sem conv_id: vale pra toda conversa da conta que acabou de ganhar
        # nome nesta mesma passada.
        _batiza_lead_pendente(c, conta_id)
        c.commit()
    return n


@router.post("/webhooks/wa-qr/audio")
async def webhook_wa_qr_audio(request: Request):
    """Áudio do WhatsApp vira TEXTO, pro vendedor responder digitando.

    Vale pros DOIS lados da conversa: o que o cliente manda e o que o vendedor
    grava pelo celular. Casa por `provider_sid` e não olha direção de propósito —
    foi o que permitiu passar a transcrever a saída sem rota nova. Antes só a
    entrada era transcrita, e a conversa ficava pela metade: a pergunta do cliente
    em texto e a resposta do vendedor como um "🎤 Áudio (0:09)" mudo.

    A mensagem já entrou antes com a marca "🎤 Áudio (0:18)" (ver textoDaMsg no
    serviço Node); aqui a transcrição é acrescentada por cima. Reusa o mesmo
    Transcritor do bot do Telegram — Whisper, com a dica de vocabulário que
    acerta 'Zaq', 'Pix', 'boleto'.

    Degrada com elegância: sem STT_API_KEY, ou se o provedor falhar, a mensagem
    fica só com a marca de áudio. Nunca quebra a conversa por causa disso."""
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    if not _qr_segredo_ok(request):
        return Response(status_code=403)
    try:
        payload = json.loads((await request.body()).decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return Response("ok", media_type="text/plain")
    try:
        conta_id = int(payload.get("conta_id") or 0)
    except (TypeError, ValueError):
        conta_id = 0
    sid = str(payload.get("id") or "").strip()
    b64 = payload.get("audio_b64") or ""
    if not conta_id or not sid or not b64:
        return Response("ok", media_type="text/plain")
    with get_pool().connection() as c:
        # traduz: o áudio cola numa mensagem que já é da empresa
        alvo = _resolver_chip(c, conta_id)
        if not alvo:
            log.warning("webhook_wa_qr_audio: chip=%s não está em QR — descartado", conta_id)
            return Response("ok", media_type="text/plain")
        conta_id = alvo[0]
    from core.transcribe import transcritor_se_configurado
    tr = transcritor_se_configurado()
    if tr is None:
        log.info("webhook_wa_qr_audio: STT não configurado — áudio fica só com a marca")
        return Response("ok", media_type="text/plain")
    try:
        import base64
        from starlette.concurrency import run_in_threadpool
        dados = base64.b64decode(b64)
        # .ogg porque é o que o WhatsApp usa (opus); a API olha a extensão.
        # Em thread separada: transcrever é bloqueante e seguraria o event loop
        # do servidor inteiro por alguns segundos a cada áudio.
        texto = await run_in_threadpool(tr.transcrever, dados, "audio.ogg")
    except Exception:  # noqa: BLE001 - transcrição é um extra, nunca derruba a conversa
        log.exception("webhook_wa_qr_audio: falha ao transcrever conta_id=%s", conta_id)
        return Response("ok", media_type="text/plain")
    texto = (texto or "").strip()[:4000]
    if not texto:
        return Response("ok", media_type="text/plain")
    with get_pool().connection() as c:
        # Acrescenta embaixo da marca, preservando a duração. O regex casa SÓ a
        # marca crua e inteira: garante que a transcrição não entra duas vezes
        # (depois da 1ª o texto deixa de casar) e que nunca encosta numa mensagem
        # de texto de verdade, mesmo que alguém escreva "🎤 Áudio (0:18)" no meio
        # de uma frase.
        r = c.execute(
            r"""update mensagens m set texto = m.texto || %s
                  from conversas cv
                 where cv.id = m.conversa_id and cv.conta_id=%s
                   and m.provider_sid=%s and m.canal='whatsapp'
                   and m.texto ~ '^(🎤|🎵) Áudio \(\d+:\d{2}\)$'""",
            ("\n" + texto, conta_id, sid))
        c.commit()
    log.info("webhook_wa_qr_audio: conta_id=%s transcrito (%s linhas, %s chars)",
             conta_id, r.rowcount, len(texto))
    return Response("ok", media_type="text/plain")


@router.post("/webhooks/wa-qr/status")
async def webhook_wa_qr_status(request: Request):
    """Recibo de entrega/leitura do WhatsApp por QR: vira ✓ / ✓✓ / 👀 no inbox
    (mesma coluna `status` que o Twilio já preenchia pelo StatusCallback). Casa
    pelo provider_sid, que é o id da mensagem no Baileys — gravado tanto no envio
    pelo Zaq quanto no eco do que saiu pelo celular.

    Nunca REGRIDE: os recibos chegam fora de ordem com frequência, e sem essa
    trava uma mensagem já lida voltava pra "entregue" na tela. 'erro' passa por
    cima de qualquer um — se falhou, é isso que o vendedor precisa ver.

    O trabalho de banco roda na THREADPOOL. O corpo deste handler e' psycopg
    SINCRONO; deixado no event loop ele congela o worker inteiro -- painel
    incluso -- a cada mensagem que entra. Com dois workers no Render bastava os
    dois estarem numa chamada dessas pra fila toda parar: em 22/08/2026 a
    resposta saiu de 527 ms pra ~50 s com a CPU em 0,7%, ou seja, esperando e
    nao trabalhando. Mesmo motivo do webhook_wa_qr_contatos, que ja' fazia assim.
    """
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    if not _qr_segredo_ok(request):
        return Response(status_code=403)
    corpo = await request.body()
    return await run_in_threadpool(_webhook_wa_qr_status_sync, corpo)


def _webhook_wa_qr_status_sync(corpo: bytes):
    """O trabalho de verdade — sincrono, fora do event loop."""
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    try:
        payload = json.loads(corpo.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return Response("ok", media_type="text/plain")
    try:
        conta_id = int(payload.get("conta_id") or 0)
    except (TypeError, ValueError):
        conta_id = 0
    itens = payload.get("itens") or []
    if not conta_id or not isinstance(itens, list) or not itens:
        return Response("ok", media_type="text/plain")
    validos = {"enviado", "entregue", "lido", "erro"}
    n = 0
    with get_pool().connection() as c:
        # traduz, e aqui é obrigatório: o recibo casa por `cv.conta_id`, e a conversa
        # foi gravada com o id da EMPRESA. Sem traduzir, recibo de mensagem do chip 2
        # não acharia nada e o status ficaria congelado em "enviado" pra sempre.
        alvo = _resolver_chip(c, conta_id)
        if not alvo:
            log.warning("webhook_wa_qr_status: chip=%s não está em QR — descartado", conta_id)
            return Response("ok", media_type="text/plain")
        conta_id = alvo[0]
        for it in itens[:500]:
            sid = str((it or {}).get("id") or "").strip()
            novo = str((it or {}).get("status") or "").strip()
            if not sid or novo not in validos:
                continue
            # 'erro' que CHEGA continua passando por cima de qualquer status (regra
            # desta rota: se falhou, é o que o vendedor precisa ver). O que muda é o
            # outro lado: na escada, 'erro' já registrado empata com 'enviado', pra
            # um recibo atrasado não apagar a falha — foi o que aconteceu no Twilio.
            r = c.execute(
                "update mensagens m set status=%s "
                "  from conversas cv "
                " where cv.id = m.conversa_id and cv.conta_id=%s "
                "   and m.provider_sid=%s and m.canal='whatsapp' "
                "   and (%s = 'erro' or (" + _SQL_RANK.replace("%s", "coalesce(m.status,'')")
                + ") < (" + _SQL_RANK + "))",
                (novo, conta_id, sid, novo, novo))
            n += r.rowcount or 0
        c.commit()
    if n:
        log.info("webhook_wa_qr_status: conta_id=%s %s mensagens atualizadas", conta_id, n)
    return Response("ok", media_type="text/plain")


@router.post("/webhooks/wa-qr/deslogado")
async def webhook_wa_qr_deslogado(request: Request):
    """WhatsApp por QR deslogou DE VEZ (não é queda temporária — só dispara quando
    o Baileys recebe DisconnectReason.loggedOut). Desliga o canal e avisa o dono.

    NÃO apaga nada. Antes esta rota apagava mensagens, conversas e a agenda de
    contatos da conta inteira — e deslogar acontece sem querer (trocou de celular,
    o pareamento caiu, alguém apertou "sair" no aparelho). O histórico de conversa
    com os leads é o ativo comercial da empresa; ele não pode depender de um
    pareamento de WhatsApp. Reconectar o QR volta a funcionar em cima do que já
    está aqui.

    O trabalho de banco roda na THREADPOOL. O corpo deste handler e' psycopg
    SINCRONO; deixado no event loop ele congela o worker inteiro -- painel
    incluso -- a cada mensagem que entra. Com dois workers no Render bastava os
    dois estarem numa chamada dessas pra fila toda parar: em 22/08/2026 a
    resposta saiu de 527 ms pra ~50 s com a CPU em 0,7%, ou seja, esperando e
    nao trabalhando. Mesmo motivo do webhook_wa_qr_contatos, que ja' fazia assim.
    """
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    if not _qr_segredo_ok(request):
        return Response(status_code=403)
    corpo = await request.body()
    return await run_in_threadpool(_webhook_wa_qr_deslogado_sync, corpo)


def _webhook_wa_qr_deslogado_sync(corpo: bytes):
    """O trabalho de verdade — sincrono, fora do event loop."""
    import logging
    log = logging.getLogger("prospeccao.wa_qr")
    try:
        payload = json.loads(corpo.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return Response("ok", media_type="text/plain")
    try:
        conta_id = int(payload.get("conta_id") or 0)
    except (TypeError, ValueError):
        conta_id = 0
    if not conta_id:
        return Response("ok", media_type="text/plain")
    pool = get_pool()
    with pool.connection() as c:
        # ────────────────────────────────────────────────────────────────────────
        # ESTE É O ÚNICO WEBHOOK QUE **NÃO** TRADUZ CHIP -> EMPRESA.
        #
        # Deslogar é fato sobre a CONEXÃO, não sobre o lead. O `update` abaixo apaga
        # o canal do id que veio: se aqui a gente trocasse o chip pela empresa, o
        # chip 2 caindo desligaria o `canais_config` da empresa — e a empresa é onde
        # mora o canal do CHIP 1, que está conectado e recebendo. Uma queda no
        # aparelho secundário derrubaria o principal, calada.
        #
        # A regra vale pra todo o caminho: fato sobre o LEAD sobe pra empresa; fato
        # sobre a CONEXÃO fica no chip. Só `_resolver_chip` para validar (é o mesmo
        # portão), e o id usado daqui pra baixo continua sendo o do chip.
        # ────────────────────────────────────────────────────────────────────────
        alvo = _resolver_chip(c, conta_id)
        if not alvo:
            log.warning("webhook_wa_qr_deslogado: chip=%s não está em QR — ignorado", conta_id)
            return Response("ok", media_type="text/plain")
        empresa_id, chip_id = alvo
        # `desconectado_em` é o MARCO ZERO da retenção de 30 dias (migração 165).
        # `coalesce` de propósito: só carimba a PRIMEIRA desconexão. Deslogar de
        # novo sem ter reconectado no meio não pode empurrar o prazo pra frente,
        # senão um pareamento que cai em looping segura o histórico pra sempre.
        c.execute("""update canais_config
                        set ativo=false,
                            desconectado_em=coalesce(desconectado_em, now())
                      where conta_id=%s and canal='whatsapp'
                        and coalesce(provedor,'twilio')='qr'""", (chip_id,))
        c.commit()
    log.warning("webhook_wa_qr_deslogado: chip=%s (empresa %s) deslogou — canal do chip "
                "desligado (histórico preservado)", chip_id, empresa_id)
    try:
        from finance import notificar
        # o aviso vai pro dono da EMPRESA — é ele quem reconecta. Quando o chip que
        # caiu não é o principal, o texto diz QUAL: "seu WhatsApp desconectou" numa
        # empresa de dois números manda a pessoa conferir o aparelho errado.
        qual = ""
        if chip_id != empresa_id:
            with pool.connection() as c2:
                nm = c2.execute("select coalesce(nullif(btrim(nome),''),'') from contas where id=%s",
                                (chip_id,)).fetchone()
            qual = f" ({nm[0]})" if nm and nm[0] else " (o segundo número)"
        notificar.enviar_para_dono(
            pool, empresa_id,
            f"⚠️ Seu WhatsApp{qual} desconectou do Zaq. Nada foi perdido — as conversas "
            "estão todas aqui. Pra voltar a enviar e receber, leia o QR Code de novo "
            "em Comunicação › Canais.")
    except Exception as e:  # noqa: BLE001 — o aviso nunca segura a resposta do webhook
        log.warning("webhook_wa_qr_deslogado: não deu pra avisar o dono da empresa %s: %s",
                    empresa_id, e)
    return Response("ok", media_type="text/plain")


# ================================================================ CAMPANHAS (Fase 2)
# (definido ANTES de /{alvo_id} pra "campanhas" não cair na rota da ficha)
_PASSOS_PADRAO = [
    (0, 0, "Uma ideia pra {empresa}", "", True),                 # D0: o agente escreve
    (1, 3, "Só reforçando — vale 2 min?",
     "Oi, {empresa}!\n\nSemana passada te mandei uma ideia rápida pra atender melhor sem "
     "aumentar a equipe. Vale 2 minutinhos essa semana pra eu te mostrar?", False),
    (2, 7, "Fecho por aqui 👋",
     "Oi, {empresa}!\n\nÉ a última vez que passo por aqui 😊 Se fizer sentido, me chama no "
     "WhatsApp quando quiser — fica à vontade.", False),
]
# ---- Biblioteca de modelos de sequência por nicho (ponto de partida frio) -------
# A campanha COPIA (snapshot) os passos do modelo escolhido; depois é dona da própria
# sequência. O dono ainda pode salvar os seus próprios modelos (tabela campanha_modelos).
_MODELOS_BASE = [
    {"codigo": "generico", "nome": "Genérico", "wa_texto": "", "passos": [
        {"dias": 0, "assunto": "Uma ideia pra {empresa}", "corpo": "", "ia": True},
        {"dias": 3, "assunto": "Só reforçando — vale 2 min?",
         "corpo": "Oi, {empresa}!\n\nSemana passada te mandei uma ideia rápida pra atender melhor sem "
                  "aumentar a equipe. Vale 2 minutinhos essa semana pra eu te mostrar?", "ia": False},
        {"dias": 7, "assunto": "Fecho por aqui 👋",
         "corpo": "Oi, {empresa}!\n\nÉ a última vez que passo por aqui 😊 Se fizer sentido, me chama no "
                  "WhatsApp quando quiser — fica à vontade.", "ia": False},
    ]},
    {"codigo": "petshop", "nome": "Pet shop · Banho & tosa", "wa_texto": "", "passos": [
        {"dias": 0, "assunto": "Mais banho & tosa na agenda da {empresa}?",
         "corpo": "Oi, {empresa}! 🐾\n\nAjudo pet shops de {cidade} a encher a agenda de banho & tosa e a "
                  "fazer o tutor voltar sempre (lembrete automático da próxima vez, retorno com desconto…).\n\n"
                  "Posso te mostrar em 2 minutinhos como ficaria aí?", "ia": False},
        {"dias": 3, "assunto": "Lembrete que traz o cliente de volta",
         "corpo": "Oi, {empresa}!\n\nSó reforçando: dá pra avisar o tutor sozinho quando chega a hora do "
                  "próximo banho — enche a agenda sem esforço. Vale 2 min essa semana?", "ia": False},
        {"dias": 7, "assunto": "Fecho por aqui 🐶",
         "corpo": "Oi, {empresa}! É a última vez que passo por aqui 😊 Se quiser ver como fica, me chama no "
                  "WhatsApp quando puder.", "ia": False},
    ]},
    {"codigo": "frutaria", "nome": "Frutaria · Hortifruti", "wa_texto": "", "passos": [
        {"dias": 0, "assunto": "Menos perda e mais giro na {empresa}",
         "corpo": "Oi, {empresa}! 🍎\n\nAjudo hortifrutis de {cidade} a perder menos (controle do que sai e "
                  "do que está vencendo) e a acertar o preço no dia certo pra girar mais rápido.\n\n"
                  "Posso te mostrar em 2 minutinhos como funcionaria aí?", "ia": False},
        {"dias": 3, "assunto": "Controle rápido pra não perder fruta",
         "corpo": "Oi, {empresa}!\n\nSó reforçando: dá pra ver na hora o que está saindo e o que precisa "
                  "vender hoje pra não perder. Vale 2 min essa semana?", "ia": False},
        {"dias": 7, "assunto": "Fecho por aqui 🍊",
         "corpo": "Oi, {empresa}! Última vez que passo por aqui 😊 Se fizer sentido, me chama no WhatsApp "
                  "quando quiser.", "ia": False},
    ]},
    {"codigo": "clinica", "nome": "Clínica · Consultório", "wa_texto": "", "passos": [
        {"dias": 0, "assunto": "Agenda mais cheia na {empresa}",
         "corpo": "Oi, {empresa}! 🦷\n\nAjudo clínicas de {cidade} a reduzir faltas (confirmação automática) e "
                  "a reativar pacientes antigos — a agenda enche sem depender só de indicação.\n\n"
                  "Posso te mostrar em 2 minutinhos como ficaria aí?", "ia": False},
        {"dias": 3, "assunto": "Menos falta, agenda cheia",
         "corpo": "Oi, {empresa}!\n\nSó reforçando: confirmação automática e retorno de paciente antigo "
                  "costumam encher a agenda rápido. Vale 2 min essa semana pra eu te mostrar?", "ia": False},
        {"dias": 7, "assunto": "Fecho por aqui 😊",
         "corpo": "Oi, {empresa}! É a última vez que passo por aqui. Se quiser ver como fica, me chama no "
                  "WhatsApp quando puder.", "ia": False},
    ]},
]

# ---- v2: "pergunta única" ------------------------------------------------------
# Por que existe uma v2, e o que ela muda.
#
# O diagnóstico da conta 3 (ago/2026): das 166 mensagens entregues, 80 foram LIDAS
# (48%) e 26 pessoas apertaram um botão — 21 delas em "Agora não", contra 5
# positivas. Não é problema de atenção: a mensagem é lida, entendida e RECUSADA,
# 4 pra 1. E a taxa de leitura é parecida em todos os segmentos (Pet 58%, Salão
# 61%, Estética 42%), o que descarta "a lista está errada" — se fosse a audiência,
# algum nicho destoaria.
#
# Então a v2 muda o PEDIDO, não a redação. A v1 pede uma reunião ("Posso te mostrar
# em 2 minutinhos?"), que é caro pra um desconhecido: a resposta barata é "não". A
# v2 pede uma palavra, dá permissão explícita pra recusar, e só oferece exemplo
# depois. A hipótese é que parte dos 21 "Agora não" está recusando o COMPROMISSO,
# não o produto.
#
# D0 com texto FIXO de propósito (a v1 usa IA e escreve diferente pra cada lead):
# mensagem que muda a cada envio não pode ser testada — não se mede o que não se
# repete.
_V2_FECHO = ("Oi, {empresa}! Última mensagem, prometido. 😊\n\n"
             "Se um dia o controle do caixa virar dor de cabeça aí, é só me chamar no "
             "WhatsApp — respondo na hora, sem compromisso.\n\nBoas vendas!")


def _v2(codigo, nome, emoji, dor, pergunta, exemplo):
    """Monta a sequência v2 — mesma estrutura pros nichos, só a dor muda."""
    return {"codigo": codigo, "nome": nome, "wa_texto": "", "passos": [
        {"dias": 0, "assunto": "Pergunta rápida, {empresa}",
         "corpo": f"Oi, {{empresa}}! {emoji}\n\nVou direto, pra não tomar seu tempo.\n\n{dor}\n\n"
                  f"Uma pergunta só: {pergunta}\n\n"
                  "Se já estiver, me responde “já resolvi” que eu paro por aqui — sem insistir. 😊",
         "ia": False},
        {"dias": 3, "assunto": "Sem compromisso — só um exemplo",
         "corpo": f"Oi, {{empresa}}!\n\nNão vou pedir reunião. Só deixo o exemplo:\n\n{exemplo}\n\n"
                  "Se quiser ver com os números do seu negócio, responde “quero ver”. "
                  "Se não for o momento, pode ignorar que eu não volto. 👍",
         "ia": False},
        {"dias": 7, "assunto": "Encerro por aqui, {empresa}", "corpo": _V2_FECHO, "ia": False},
    ]}


_MODELOS_BASE += [
    _v2("generico_v2", "Genérico · pergunta única (v2)", "👋",
        "A parte chata de controlar dinheiro — quanto entrou, quanto saiu, o que sobrou — "
        "dá pra resolver conversando no WhatsApp. Sem sistema novo, sem planilha, sem treinar ninguém.",
        "fechar o caixa te dá trabalho hoje, ou já está resolvido?",
        "Você manda “paguei 240 no fornecedor” e o resto se organiza sozinho: categoria, "
        "relatório do mês e o que vence essa semana."),
    _v2("beleza_v2", "Salão & estética · pergunta única (v2)", "💅",
        "Em salão o dinheiro se perde no detalhe: comissão da profissional, produto que acabou "
        "sem ninguém ver, cliente que sumiu e não voltou.",
        "fechar a comissão do mês te dá trabalho hoje, ou já está redondo?",
        "Você manda “Ana fez 3 escovas hoje” e a comissão dela sai calculada no fim do mês, "
        "junto com o que entrou no caixa."),
    _v2("clinica_v2", "Clínica · pergunta única (v2)", "🦷",
        "Em clínica o furo costuma ser o mesmo: paciente que falta sem avisar, retorno que "
        "ninguém lembra de chamar, e o caixa que só fecha lá no fim do mês.",
        "falta de paciente é problema aí hoje, ou já está sob controle?",
        "A confirmação sai sozinha um dia antes, e quem não voltou há meses entra numa "
        "lista pra você chamar — sem ninguém ficar catando na agenda."),
    _v2("petshop_v2", "Pet shop · pergunta única (v2)", "🐾",
        "Em pet shop o tutor some sem avisar: fez o banho, gostou, e só volta quando lembra. "
        "Enquanto isso a agenda tem buraco no meio da semana.",
        "encher a agenda de banho & tosa te dá trabalho hoje, ou já está resolvido?",
        "Quando chega a hora do próximo banho, o tutor recebe o lembrete sozinho — "
        "e a agenda enche sem você caçar cliente."),
]

_MODELOS_BASE_COD = {m["codigo"] for m in _MODELOS_BASE}


def _slug_modelo(s: str) -> str:
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:40] or "modelo"


def _modelos_lista(c, conta_id) -> list:
    """Modelos disponíveis pro dropdown: os do dono (override) + os base."""
    user = c.execute("select codigo, nome from campanha_modelos where conta_id=%s order by nome",
                     (conta_id,)).fetchall()
    ucod = {r[0] for r in user}
    out = [{"codigo": m["codigo"], "nome": m["nome"], "origem": "base"}
           for m in _MODELOS_BASE if m["codigo"] not in ucod]
    out += [{"codigo": r[0], "nome": r[1], "origem": "meu"} for r in user]
    return out


def _modelo_passos(c, conta_id, codigo) -> list | None:
    """Passos de um modelo (do dono primeiro; senão o base). None se não existir."""
    row = c.execute("select passos from campanha_modelos where conta_id=%s and codigo=%s",
                    (conta_id, codigo)).fetchone()
    if row:
        p = row[0]
        if isinstance(p, str):
            import json
            p = json.loads(p or "[]")
        return p or []
    for m in _MODELOS_BASE:
        if m["codigo"] == codigo:
            return m["passos"]
    return None


_STATUS_ROT_CP = {"rascunho": "✎ Rascunho", "ativa": "● Ativa",
                  "pausada": "❚❚ Pausada", "concluida": "✓ Concluída"}
_STATUS_CURTO_CP = {"rascunho": "rascunho", "pausada": "pausada", "concluida": "concluída"}
_ALVO_ROT = {"fila": "Na fila", "enviado": "Em sequência", "respondeu": "Respondeu ✓",
             "descadastrou": "Descadastrou", "erro": "Erro", "concluido": "Concluído"}


def _campanha_publico_where(conta_id, camp_id, seg, cidade, temp):
    """WHERE dos leads elegíveis: tem um canal validado (e-mail válido OU WhatsApp/
    telefone), está fora da campanha e não descadastrou. Google Maps às vezes traz só
    o número — o lead entra na mesma, e a campanha usa o canal que tiver."""
    where = ["p.conta_id=%s",
             "(p.email_ok or coalesce(nullif(trim(p.whatsapp),''), nullif(trim(p.telefone),'')) is not null)",
             "p.id not in (select prospeccao_id from campanha_alvos where campanha_id=%s)",
             "lower(coalesce(p.email,'')) not in (select lower(email) from descadastros where conta_id=%s)"]
    params = [conta_id, camp_id, conta_id]
    if (seg or "").strip():
        where.append("p.segmento ilike %s"); params.append("%" + seg.strip() + "%")
    if (cidade or "").strip():
        where.append("p.cidade ilike %s"); params.append("%" + cidade.strip() + "%")
    if temp in ("frio", "morno", "quente"):
        where.append("p.temperatura=%s"); params.append(temp)
    return " and ".join(where), params


def _reais(v) -> str:
    return "R$ " + f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _motor_status(c) -> dict:
    """Sinal de vida do motor de campanhas (thread de fundo em web/app.py, grava
    em app_config.prospec_motor_ultimo_ciclo a cada ~2min). Compara com agora e
    devolve {"estado": ok|lento|parado|nunca, "ic", "texto"} pra Campanhas
    mostrar se o motor tá rodando de verdade sem precisar checar log/banco na
    mão — mesmo texto/estado servem pro render inicial e pro polling ao vivo."""
    row = c.execute(
        "select valor, atualizado_em, now() from app_config where chave='prospec_motor_ultimo_ciclo'"
    ).fetchone()
    if not row:
        return {"estado": "nunca", "ic": "⚪",
                "texto": "Motor iniciando · ainda sem nenhum ciclo registrado nesse processo"}
    valor, atualizado_em, agora = row
    delta_seg = (agora - atualizado_em).total_seconds()
    n = int(valor) if (valor or "").isdigit() else 0
    if delta_seg < 60:
        quando = "agora mesmo"
    elif delta_seg < 3600:
        quando = f"há {int(delta_seg // 60)} min"
    else:
        h, m = int(delta_seg // 3600), int((delta_seg % 3600) // 60)
        quando = f"há {h}h{m:02d}"
    if delta_seg <= 180:
        return {"estado": "ok", "ic": "🟢",
                "texto": f"Motor ativo · último ciclo {quando} · processou {n}"}
    if delta_seg <= 480:
        return {"estado": "lento", "ic": "🟡",
                "texto": f"Motor meio devagar · último ciclo {quando} (o normal é a cada 2 min)"}
    return {"estado": "parado", "ic": "🔴",
            "texto": f"Motor parado · nenhum ciclo {quando} · normalmente roda a cada 2 min"}


def _reserva_numeros(c, camp_id):
    """Os números que sobraram nos alvos que já pararam: quantos são, e a lista
    por lead pro painel "Números não tentados".

    A base guarda dezenas de telefones por lead e marca `whatsapp: true/false` em
    cada um; o disparo só usava o ⭐. Sem esta conta na tela, uma campanha que mal
    encostou na base parece esgotada.

    Lead com número TRAVADO (`alvo_telefone`, escolhido no checkbox da Base) entra
    na lista marcado: o disparo automático não adivinha em cima da escolha do dono,
    então a reserva dele só fica alcançável se ele destravar aqui — que é o que o
    botão "Colocar na fila" faz. Mostrar sem a marca seria prometer números que o
    disparo sozinho nunca usaria."""
    rows = c.execute(
        """select a.id, p.id, p.empresa, coalesce(a.wa_erro_codigo,''),
                  coalesce(p.decisor_telefones,'[]'::jsonb), coalesce(a.wa_tentados,'[]'::jsonb),
                  coalesce(a.wa_tentativas,0), coalesce(a.alvo_telefone,'')
             from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
            where a.campanha_id=%s and a.wa_status='erro'
              and a.status = any(%s)
            order by p.empresa""",
        (camp_id, list(_cm._STATUS_WA_ELEGIVEL))).fetchall()
    leads, total = [], 0
    for (aid, pid, empresa, cod, tels, tentados, tentativas, travado) in rows:
        sobra = _cm.fila_numeros(tels, ja_tentados=tentados)
        if not sobra:
            continue
        total += len(sobra)
        leads.append({
            "aid": aid, "pid": pid, "empresa": empresa, "cod": cod,
            "tentativas": tentativas, "travado": travado,
            # já gastou as 3 tentativas: continua na lista (o dono quer ver que
            # sobrou número), mas não dá pra recolocar — o teto é o teto
            "no_teto": tentativas >= _cm._WA_TENTATIVAS,
            "tentados": [t for t in (tentados or [])][-1:],   # o último que falhou
            "sobra": len(sobra), "proximos": sobra[:3],
        })
    leads.sort(key=lambda x: -x["sobra"])
    return {"total": total, "leads": leads}


def _campanhas_dados(c, conta_id, membro_id=None):
    """Métricas por canal das campanhas da conta (lista + polling). Uma consulta
    LATERAL agrega tudo por campanha. Devolve (camps, totais).

    membro_id != None restringe às campanhas em que ESSE membro é o responsável
    (visão do vendedor); None = todas as da conta (visão do dono/gestor)."""
    from datetime import date as _date
    from finance import wa_precos
    _RATE = wa_precos.TARIFA_BRL["marketing"]
    _CIRC = 131.9  # 2*pi*21 — raio do gauge no CSS/SVG
    _hoje = _date.today()
    rows = c.execute(
        """select cp.id, cp.nome, cp.status, cp.limite_dia, coalesce(cp.wa_ativo,false),
                  cp.teto_wa, coalesce(cp.enviados_hoje,0), cp.dia_contagem,
                  coalesce(cp.wa_template_sid,''), coalesce(cp.wa_bloqueio,''),
                  ag.n, ag.email_env, ag.email_resp, ag.email_abriu, ag.email_err, ag.virou,
                  ag.wa_env, ag.wa_resp, ag.wa_err, ag.wa_gasto, coalesce(ev.bounces,0),
                  to_char((select min(a.proximo_envio_em) from campanha_alvos a
                            where a.campanha_id=cp.id and a.proximo_envio_em is not null)
                          - interval '3 hours', 'HH24:MI'),
                  (select coalesce(nullif(m.nome,''), m.email) from membros m
                    where m.id=cp.responsavel_id)
             from campanhas cp
             left join lateral (
               select count(*) n,
                 count(*) filter (where a.status='enviado') email_env,
                 count(*) filter (where a.status='respondeu') email_resp,
                 count(*) filter (where coalesce(a.aberturas,0) > 0) email_abriu,
                 count(*) filter (where a.status='erro') email_err,
                 count(*) filter (where p.estagio='lead') virou,
                 count(*) filter (where a.wa_status in ('enviado','entregue','lido','respondeu')) wa_env,
                 count(*) filter (where a.wa_status='respondeu') wa_resp,
                 count(*) filter (where a.wa_status='erro') wa_err,
                 coalesce(sum(a.wa_custo),0) wa_gasto
               from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
               where a.campanha_id=cp.id) ag on true
             left join lateral (
               select count(*) bounces from campanha_eventos e
               where e.campanha_id=cp.id and e.evento='bounce') ev on true
            where cp.conta_id=%s
              and (%s::bigint is null or cp.responsavel_id=%s)
            order by cp.criado_em desc""",
        (conta_id, membro_id, membro_id)).fetchall()
    # leads únicos que clicaram cada botão do template de WhatsApp (qualquer
    # campanha da conta) — os 3 saem juntos no mesmo clique do lead, então dá
    # pra somar sem contar 2x: "Agora não" grava evento='clicou', os outros
    # dois ("Quero te conhecer" / "Quero o material") gravam evento='respondeu'
    # (ver _rot_btn na rota que processa o clique do botão).
    _esc = " and (%s::bigint is null or cp.responsavel_id=%s)"
    sem_interesse = c.execute(
        """select count(distinct e.prospeccao_id) from campanha_eventos e
             join campanhas cp on cp.id=e.campanha_id
            where cp.conta_id=%s and e.canal='whatsapp' and e.evento='clicou'
              and e.detalhe='Agora não'""" + _esc, (conta_id, membro_id, membro_id)).fetchone()[0]
    quer_conhecer = c.execute(
        """select count(distinct e.prospeccao_id) from campanha_eventos e
             join campanhas cp on cp.id=e.campanha_id
            where cp.conta_id=%s and e.canal='whatsapp' and e.evento='respondeu'
              and e.detalhe='Quero te conhecer'""" + _esc, (conta_id, membro_id, membro_id)).fetchone()[0]
    quer_material = c.execute(
        """select count(distinct e.prospeccao_id) from campanha_eventos e
             join campanhas cp on cp.id=e.campanha_id
            where cp.conta_id=%s and e.canal='whatsapp' and e.evento='respondeu'
              and e.detalhe='Quero o material'""" + _esc, (conta_id, membro_id, membro_id)).fetchone()[0]
    # Os três de cima só olham o WhatsApp, de propósito — mudá-los mudaria o
    # significado de um número que o dono já acompanha. Os quatro abaixo são
    # informação NOVA, que hoje não aparece em lugar nenhum e some sem ninguém ver:
    #   · quem clicou "Tenho interesse" pelo E-MAIL (o CTA da régua de e-mail);
    #   · quem ABRIU o material (evento 'baixou', que nunca teve contador);
    #   · quem ESCREVEU no chat sem apertar botão nenhum — o maior buraco: nesta
    #     base, 52 leads, e a maioria invisível pros três de cima;
    #   · e, desses, quantos nunca receberam resposta de GENTE (só do bot). Este
    #     último é o que vira dinheiro: um lead que perguntou e ficou no vácuo.
    interesse_email = c.execute(
        """select count(distinct e.prospeccao_id) from campanha_eventos e
             join campanhas cp on cp.id=e.campanha_id
            where cp.conta_id=%s and e.canal='email' and e.evento='respondeu'
              and e.detalhe='Tenho interesse'""" + _esc, (conta_id, membro_id, membro_id)).fetchone()[0]
    baixou_material = c.execute(
        """select count(distinct e.prospeccao_id) from campanha_eventos e
             join campanhas cp on cp.id=e.campanha_id
            where cp.conta_id=%s and e.evento='baixou'""" + _esc,
        (conta_id, membro_id, membro_id)).fetchone()[0]
    _chat = c.execute(
        """select count(*), count(*) filter (where sem_humano)
             from (select cv.prospeccao_id,
                          count(*) filter (where m.direcao='out' and m.autor<>'bot') = 0 as sem_humano
                     from conversas cv
                     join mensagens m on m.conversa_id=cv.id
                     join prospeccao p on p.id=cv.prospeccao_id
                    where cv.conta_id=%s and cv.prospeccao_id is not null
                      and (%s::bigint is null or p.vendedor_id=%s)
                    group by 1
                   having count(*) filter (where m.direcao='in' and m.autor='lead') > 0) t""",
        (conta_id, membro_id, membro_id)).fetchone()
    conversou, sem_humano = (_chat or (0, 0))
    camps = []
    tot_gasto = tot_msgs = tot_email = tot_teto = perto = 0
    for r in rows:
        (cid, nome, status, limite, wa_ativo, teto, env_hoje, dia_cont, wa_sid, wa_bloq,
         n, e_env, e_resp, e_abriu, e_err, virou, w_env, w_resp, w_err, w_gasto, e_volta,
         proximo, resp_nome) = r
        limite = limite or 0
        n = n or 0
        hoje_n = env_hoje if dia_cont == _hoje else 0
        pct_dia = min(100, round(100 * hoje_n / limite)) if limite else 0
        gasto = float(w_gasto or 0)
        teto_f = float(teto) if teto is not None else None
        if teto_f and teto_f > 0:
            pct = min(100, round(100 * gasto / teto_f))
            alerta = "coral" if gasto >= teto_f else ("amar" if pct >= 80 else "ok")
            tot_teto += teto_f
            if alerta in ("amar", "coral"):
                perto += 1
        else:
            pct, alerta = None, None
        tot_gasto += gasto
        tot_msgs += (w_env or 0)
        tot_email += (e_env or 0)
        camps.append({
            "id": cid, "nome": nome, "status": status,
            "status_rot": _STATUS_ROT_CP.get(status, status),
            "status_curto": _STATUS_CURTO_CP.get(status, status),
            "limite": limite, "n": n, "virou": virou or 0, "wa": wa_ativo,
            "wa_pronto": not (_prospec_convite.motivo_bloqueio(c, conta_id, wa_sid) or wa_bloq),
            "hoje": hoje_n, "hoje_offset": round(_CIRC * (1 - pct_dia / 100), 1),
            "erros": (e_err or 0) + (w_err or 0), "proximo": proximo or "",
            "email": {"env": e_env or 0, "resp": e_resp or 0, "abriu": e_abriu or 0,
                      "volta": e_volta or 0, "err": e_err or 0,
                      "pct": min(100, round(100 * (e_env or 0) / n)) if n else 0,
                      "pct_resp": min(100, round(100 * (e_resp or 0) / n)) if n else 0},
            "wa_env": w_env or 0, "wa_resp": w_resp or 0, "wa_err": w_err or 0,
            "gasto": gasto, "gasto_fmt": _reais(gasto),
            "teto": teto_f, "teto_fmt": (_reais(teto_f) if teto_f else ""),
            "pct": pct, "alerta": alerta, "previsto_fmt": _reais(n * _RATE),
            "responsavel": resp_nome or "",
        })
    totais = {"gasto_fmt": _reais(tot_gasto), "msgs": tot_msgs, "emails": tot_email,
              "teto_fmt": _reais(tot_teto), "perto": perto, "sem_interesse": sem_interesse,
              "quer_conhecer": quer_conhecer, "quer_material": quer_material,
              "interesse_email": interesse_email, "baixou_material": baixou_material,
              "conversou": conversou, "sem_humano": sem_humano,
              "custo_lead_fmt": _reais(tot_gasto / tot_msgs if tot_msgs else 0),
              "motor": _motor_status(c)}
    return camps, totais


@router.get("/painel/prospeccao/campanhas", response_class=HTMLResponse)
def prospeccao_campanhas(request: Request):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    # dono/gestor vê todas; vendedor vê só as campanhas em que é o responsável.
    escopo_membro = None if ctx["gerencia"] else ctx["membro_id"]
    with get_pool().connection() as c:
        camps, totais = _campanhas_dados(c, ctx["conta_id"], escopo_membro)
        eleg = c.execute("""select count(*) from prospeccao where conta_id=%s
                             and (email_ok or coalesce(nullif(trim(whatsapp),''), nullif(trim(telefone),'')) is not null)""",
                         (ctx["conta_id"],)).fetchone()[0]
    return _render("prospeccao_campanhas", request, titulo="Campanhas", secao_ativa="prospeccao",
                   camps=camps, totais=totais, elegiveis=eleg, gerencia=ctx["gerencia"],
                   aviso=request.session.pop("prosp_aviso", None))


# ------------------------------------------------- quem está por trás de cada KPI
# Os contadores de "Gastos das campanhas" respondem QUANTOS. Estas rotas respondem
# QUEM — com nome, campanha e telefone, que é o que permite ligar. Sem isso o número
# é uma métrica de vaidade: nesta base havia lead perguntando "qual exatamente é o
# produto que você oferece?" e ninguém sabia que ele existia.

_KPI_SINAIS = {
    # chave       (canal,       evento,      detalhe)          — None = qualquer
    "sem_interesse":   ("whatsapp", "clicou",    "Agora não"),
    "quer_conhecer":   ("whatsapp", "respondeu", "Quero te conhecer"),
    "quer_material":   ("whatsapp", "respondeu", "Quero o material"),
    "interesse_email": ("email",    "respondeu", "Tenho interesse"),
    "baixou_material": (None,       "baixou",    None),
}

# Frases com que o WhatsApp Business do PRÓPRIO lead responde sozinho. Não é gente:
# é o autoatendimento dele. Marcado, NUNCA escondido — a regra é heurística e vai
# errar em algum caso; esconder um lead de verdade por causa de um palpite custa
# mais caro que mostrar um ruído rotulado.
_AUTORESPOSTA = ("agradece seu contato", "agradecemos sua mensagem", "agradecemos seu contato",
                 "não estamos disponíve", "nao estamos disponive", "horário de funcionamento",
                 "horario de funcionamento", "seja bem-vind", "seja muito bem-vind",
                 "como podemos ajud", "retornaremos assim que", "nosso atendimento está")


def _parece_autoresposta(txt: str) -> bool:
    t = (txt or "").lower()
    return any(p in t for p in _AUTORESPOSTA)


def _kpi_leads(c, conta_id: int, escopo_membro, sinal: str):
    """Leads por trás de um KPI. Devolve (itens, rotulo)."""
    idn = c.execute("""select regexp_replace(coalesce(telefone,''),'\\D','','g'), lower(coalesce(email,''))
                         from contas where id=%s""", (conta_id,)).fetchone() or ("", "")
    fone_dono, email_dono = (idn[0] or "@@"), (idn[1] or "@@")
    if sinal in _KPI_SINAIS:
        canal, evento, detalhe = _KPI_SINAIS[sinal]
        cur = c.execute(
            """select p.id, p.empresa, p.segmento, p.cidade, p.uf, p.temperatura,
                      coalesce(nullif(trim(p.whatsapp),''), nullif(trim(p.telefone),'')) as fone,
                      nullif(trim(p.email),'') as email,
                      coalesce(nullif(m.nome,''), m.email, '—') as vendedor,
                      cp.nome as campanha, max(e.quando) as quando, count(*) as vezes,
                      '' as detalhe_extra, 0 as msgs_lead, 0 as resp_humana
                 from campanha_eventos e
                 join campanhas cp on cp.id=e.campanha_id
                 join prospeccao p on p.id=e.prospeccao_id
                 left join membros m on m.id=p.vendedor_id
                where cp.conta_id=%(conta)s and e.evento=%(ev)s
                  and (%(canal)s::text is null or e.canal=%(canal)s)
                  and (%(det)s::text is null or e.detalhe=%(det)s)
                  and (%(membro)s::bigint is null or p.vendedor_id=%(membro)s)
                group by p.id, p.empresa, p.segmento, p.cidade, p.uf, p.temperatura,
                         coalesce(nullif(trim(p.whatsapp),''), nullif(trim(p.telefone),'')),
                         nullif(trim(p.email),''),
                         coalesce(nullif(m.nome,''), m.email, '—'), cp.nome
                order by max(e.quando) desc""",
            {"conta": conta_id, "ev": evento, "canal": canal, "det": detalhe,
             "membro": escopo_membro})
    else:  # 'conversou' e 'sem_humano': quem ESCREVEU, não quem clicou
        cur = c.execute(
            """select p.id, p.empresa, p.segmento, p.cidade, p.uf, p.temperatura,
                      coalesce(nullif(trim(p.whatsapp),''), nullif(trim(p.telefone),'')) as fone,
                      nullif(trim(p.email),'') as email,
                      coalesce(nullif(m.nome,''), m.email, '—') as vendedor,
                      coalesce((select cp.nome from campanha_alvos a
                                 join campanhas cp on cp.id=a.campanha_id
                                where a.prospeccao_id=p.id order by a.id desc limit 1), '—') as campanha,
                      max(msg.criado_em) as quando, 1 as vezes,
                      (array_agg(msg.texto order by msg.criado_em)
                         filter (where msg.direcao='in' and msg.autor='lead'))[1] as detalhe_extra,
                      count(*) filter (where msg.direcao='in' and msg.autor='lead') as msgs_lead,
                      count(*) filter (where msg.direcao='out' and msg.autor<>'bot') as resp_humana
                 from conversas cv
                 join mensagens msg on msg.conversa_id=cv.id
                 join prospeccao p on p.id=cv.prospeccao_id
                 left join membros m on m.id=p.vendedor_id
                where cv.conta_id=%(conta)s
                  and (%(membro)s::bigint is null or p.vendedor_id=%(membro)s)
                group by p.id, p.empresa, p.segmento, p.cidade, p.uf, p.temperatura,
                         coalesce(nullif(trim(p.whatsapp),''), nullif(trim(p.telefone),'')),
                         nullif(trim(p.email),''),
                         coalesce(nullif(m.nome,''), m.email, '—')
               having count(*) filter (where msg.direcao='in' and msg.autor='lead') > 0
                  and (%(so_mudo)s = false
                       or count(*) filter (where msg.direcao='out' and msg.autor<>'bot') = 0)
                order by count(*) filter (where msg.direcao='in' and msg.autor='lead') desc,
                         max(msg.criado_em) desc""",
            {"conta": conta_id, "membro": escopo_membro, "so_mudo": sinal == "sem_humano"})
    cols = [d.name for d in cur.description]
    itens = []
    for r in cur.fetchall():
        it = dict(zip(cols, r))
        dig = "".join(ch for ch in (it["fone"] or "") if ch.isdigit())
        # Lead com o telefone/e-mail da PRÓPRIA conta é teste do dono. Some nos KPIs
        # como se fosse cliente — marcar é o mínimo pra ele não confundir o próprio
        # número com demanda de mercado.
        it["eh_teste"] = bool((dig and fone_dono in dig) or
                              (it["email"] and it["email"].lower() == email_dono))
        it["eh_ruido"] = _parece_autoresposta(it.get("detalhe_extra") or "")
        it["quando_br"] = ((it["quando"] - timedelta(hours=3)).strftime("%d/%m %H:%M")
                           if it["quando"] else "")
        it["wa_link"] = ("https://wa.me/" + _so_digitos_wa(it["fone"])) if it["fone"] else ""
        it["trecho"] = " ".join((it.get("detalhe_extra") or "").split())[:120]
        itens.append(it)
    return itens


@router.get("/painel/prospeccao/campanhas/kpi/{sinal}", response_class=HTMLResponse)
def prospeccao_kpi_leads(request: Request, sinal: str):
    """Lista (HTML parcial) de quem está por trás de um KPI. Carregada sob demanda."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return HTMLResponse("", status_code=401)
    if sinal not in _KPI_SINAIS and sinal not in ("conversou", "sem_humano"):
        return HTMLResponse("", status_code=404)
    escopo_membro = None if ctx["gerencia"] else ctx["membro_id"]
    with get_pool().connection() as c:
        itens = _kpi_leads(c, ctx["conta_id"], escopo_membro, sinal)
    return _env.from_string(_KPI_LISTA_TPL).render(itens=itens, sinal=sinal)


@router.get("/painel/prospeccao/campanhas/kpi/{sinal}/csv")
def prospeccao_kpi_csv(request: Request, sinal: str):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if sinal not in _KPI_SINAIS and sinal not in ("conversou", "sem_humano"):
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    escopo_membro = None if ctx["gerencia"] else ctx["membro_id"]
    with get_pool().connection() as c:
        itens = _kpi_leads(c, ctx["conta_id"], escopo_membro, sinal)
    buf = io.StringIO()
    w = _csv.writer(buf, delimiter=";")
    w.writerow(["Empresa", "Campanha", "Segmento", "Cidade", "WhatsApp", "E-mail",
                "Vendedor", "Temperatura", "Quando", "Msgs do lead", "Resposta humana",
                "Teste da casa", "Provável autoresposta", "Trecho"])
    for i in itens:
        w.writerow([i["empresa"] or "", i["campanha"] or "", i["segmento"] or "",
                    i["cidade"] or "", i["fone"] or "", i["email"] or "", i["vendedor"],
                    i["temperatura"] or "", i["quando_br"], i["msgs_lead"], i["resp_humana"],
                    "sim" if i["eh_teste"] else "", "sim" if i["eh_ruido"] else "", i["trecho"]])
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{sinal}.csv"'})


# --------------------------------------------------------------- Radar: quem atacar
# A tela de campanha responde "como foi a campanha". Esta responde "pra quem eu ligo
# agora", que é a pergunta que move dinheiro. Por isso vive FORA de uma campanha: os
# leads que levantaram a mão estão espalhados por várias, e uma aba por campanha
# mostraria um lead em cada e esconderia o conjunto.
#
# Os baldes saem dos botões do template de WhatsApp e dos cliques do e-mail. A
# tradução exata está em prospec_inbound/_rot_btn: "Agora não" grava evento='clicou'
# e os outros dois gravam 'respondeu'. Confundir os dois inverte a lista — quem
# recusou viraria quem levantou a mão.
_RADAR_BALDES = ("mao", "leu", "nao", "frio")

# Abertura de e-mail NÃO entra em balde nenhum, de propósito: o proxy de imagem do
# Gmail/Apple busca o pixel sozinho. Medido nesta base: 62 das 69 "aberturas"
# aconteceram em menos de 1 minuto após o envio, com 2,9 aberturas em média. Usar
# isso como sinal mandaria o vendedor ligar pra quem nunca leu nada.


def _radar_dados(c, conta_id: int, escopo_membro, balde: str = "", camp_id: int = 0):
    """Leads da conta com o sinal mais forte que cada um deu, prontos pra contato.

    Um lead pode estar em várias campanhas; o `distinct on` fica com a passagem mais
    recente, que é a que o vendedor vai citar quando ligar."""
    sql = """
    with sinais as (
      select e.prospeccao_id,
             bool_or(e.evento='respondeu' and e.detalhe in ('Quero te conhecer','Tenho interesse')) as quer_falar,
             bool_or(e.evento='respondeu' and e.detalhe='Quero o material') as quer_material,
             bool_or(e.evento='baixou') as baixou,
             bool_or(e.evento='clicou' and e.detalhe='Agora não') as agora_nao,
             max(e.quando) filter (where e.evento in ('respondeu','baixou','clicou')) as sinal_em
        from campanha_eventos e join campanhas cp on cp.id=e.campanha_id
       where cp.conta_id=%(conta)s
       group by 1
    ), base as (
      select distinct on (p.id)
             p.id as pid, p.empresa, p.segmento, p.cidade, p.uf, p.temperatura,
             coalesce(nullif(trim(p.whatsapp),''), nullif(trim(p.telefone),'')) as fone,
             nullif(trim(p.email),'') as email,
             p.ultimo_contato_em, p.vendedor_id,
             coalesce(nullif(m.nome,''), m.email, '—') as vendedor,
             cp.id as camp_id, cp.nome as campanha,
             coalesce(a.wa_status,'') as wa_status,
             coalesce(s.quer_falar,false) as quer_falar,
             coalesce(s.quer_material,false) as quer_material,
             coalesce(s.baixou,false) as baixou,
             coalesce(s.agora_nao,false) as agora_nao,
             greatest(s.sinal_em, a.wa_em, a.ultima_msg_em) as quando
        from campanha_alvos a
        join campanhas cp on cp.id=a.campanha_id
        join prospeccao p on p.id=a.prospeccao_id
        left join sinais s on s.prospeccao_id=p.id
        left join membros m on m.id=p.vendedor_id
       where cp.conta_id=%(conta)s
         and (%(membro)s::bigint is null or p.vendedor_id=%(membro)s)
         and (%(camp)s::bigint is null or cp.id=%(camp)s)
       order by p.id, greatest(s.sinal_em, a.wa_em, a.ultima_msg_em) desc nulls last
    )
    select *, case when quer_falar or quer_material or baixou then 'mao'
                   when agora_nao then 'nao'
                   when wa_status in ('lido','respondeu') then 'leu'
                   else 'frio' end as balde
      from base
    """
    p = {"conta": conta_id, "membro": escopo_membro, "camp": camp_id or None}
    cur = c.execute(sql, p)
    cols = [d.name for d in cur.description]
    itens = [dict(zip(cols, r)) for r in cur.fetchall()]
    for it in itens:
        sinal = []
        if it["quer_falar"]:
            sinal.append("quer falar")
        if it["quer_material"]:
            sinal.append("pediu o material")
        if it["baixou"]:
            sinal.append("abriu o material")
        if it["agora_nao"]:
            sinal.append("disse “agora não”")
        if not sinal and it["wa_status"] in ("lido", "respondeu"):
            sinal.append("leu no WhatsApp")
        it["sinal"] = " · ".join(sinal) or "sem sinal"
        it["quando_br"] = ((it["quando"] - timedelta(hours=3)).strftime("%d/%m %H:%M")
                           if it["quando"] else "")
        it["falado_br"] = ((it["ultimo_contato_em"] - timedelta(hours=3)).strftime("%d/%m")
                           if it["ultimo_contato_em"] else "")
        it["wa_link"] = ("https://wa.me/" + _so_digitos_wa(it["fone"])) if it["fone"] else ""
    contagem = {b: sum(1 for i in itens if i["balde"] == b) for b in _RADAR_BALDES}
    if balde in _RADAR_BALDES:
        itens = [i for i in itens if i["balde"] == balde]
    ordem = {b: n for n, b in enumerate(_RADAR_BALDES)}
    itens.sort(key=lambda i: (ordem[i["balde"]], i["ultimo_contato_em"] is not None,
                              -(i["quando"].timestamp() if i["quando"] else 0)))
    return itens, contagem


def _so_digitos_wa(fone: str) -> str:
    """Telefone em dígitos com DDI pro link wa.me (assume BR quando vem sem)."""
    d = "".join(ch for ch in (fone or "") if ch.isdigit())
    if d and not d.startswith("55") and len(d) <= 11:
        d = "55" + d
    return d


@router.get("/painel/prospeccao/radar", response_class=HTMLResponse)
def prospeccao_radar(request: Request, balde: str = "", camp: int = 0):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    # dono/gestor vê a carteira toda; vendedor vê só os leads dele.
    escopo_membro = None if ctx["gerencia"] else ctx["membro_id"]
    with get_pool().connection() as c:
        itens, contagem = _radar_dados(c, ctx["conta_id"], escopo_membro, balde, camp)
        camps = c.execute("""select id, nome from campanhas where conta_id=%s
                              order by criado_em desc""", (ctx["conta_id"],)).fetchall()
    return _render("prospeccao_radar", request, titulo="Quem atacar",
                   secao_ativa="prospeccao", nav_ativo="radar",
                   itens=itens, contagem=contagem, balde=balde,
                   camps=[{"id": r[0], "nome": r[1]} for r in camps], camp_sel=camp,
                   gerencia=ctx["gerencia"])


@router.post("/painel/prospeccao/radar/contatado")
def prospeccao_radar_contatado(request: Request, pid: int = Form(0), desfazer: str = Form("")):
    """Marca (ou desmarca) que o vendedor já falou com o lead.

    Grava atividade além de carimbar `ultimo_contato_em`: sem a trilha, ninguém
    sabe QUEM disse que falou — e numa carteira dividida entre 3 vendedores isso
    vira discussão."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    with get_pool().connection() as c:
        dono = c.execute("select vendedor_id from prospeccao where id=%s and conta_id=%s",
                         (pid, ctx["conta_id"])).fetchone()
        if not dono:
            return JSONResponse({"ok": False, "erro": "lead"}, status_code=404)
        # vendedor só mexe no que é dele; dono/gestor mexe em qualquer um
        if not ctx["gerencia"] and dono[0] != ctx["membro_id"]:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        if desfazer:
            c.execute("update prospeccao set ultimo_contato_em=null, atualizado_em=now() "
                      "where id=%s and conta_id=%s", (pid, ctx["conta_id"]))
        else:
            c.execute("update prospeccao set ultimo_contato_em=now(), atualizado_em=now() "
                      "where id=%s and conta_id=%s", (pid, ctx["conta_id"]))
            c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, descricao)
                         values (%s,%s,'nota','Marcado como contatado pelo Radar')""",
                      (pid, ctx["membro_id"]))
        c.commit()
    return JSONResponse({"ok": True, "desfeito": bool(desfazer)})


@router.get("/painel/prospeccao/campanhas/metricas")
def prospeccao_campanhas_metricas(request: Request):
    """JSON leve pro polling da lista de campanhas — mesmos números, sem HTML."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False}, status_code=401)
    escopo_membro = None if ctx["gerencia"] else ctx["membro_id"]
    with get_pool().connection() as c:
        camps, totais = _campanhas_dados(c, ctx["conta_id"], escopo_membro)
    return JSONResponse({"ok": True, "camps": camps, "totais": totais})


@router.post("/painel/prospeccao/campanhas/nova")
def prospeccao_campanha_nova(request: Request, nome: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gere_campanha"]:
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    nome = (nome or "").strip() or "Nova campanha"
    with get_pool().connection() as c:
        # `modelo_codigo` marcado na criação: _PASSOS_PADRAO É o modelo 'generico'.
        # Sem isso a campanha nasce sem dizer que mensagem está usando, e depois não
        # há como comparar um texto com outro — 7 das 8 campanhas desta base ficaram
        # órfãs assim, e o resultado delas virou um número sem causa.
        # responsavel_id: sem isto o vendedor cria a campanha e NÃO CONSEGUE ABRIR
        # o que acabou de criar — o _pode_campanha exige gerência ou ser o
        # responsável, e `criado_por` sozinho não conta. Pra gerência fica NULL
        # como sempre foi (campanha da empresa, sem dono individual).
        _resp = None if ctx["gerencia"] else ctx["membro_id"]
        cid = c.execute("""insert into campanhas (conta_id, nome, criado_por, modelo_codigo,
                                                  responsavel_id)
                           values (%s,%s,%s,'generico',%s) returning id""",
                        (ctx["conta_id"], nome[:120], ctx["membro_id"], _resp)).fetchone()[0]
        for (ordem, dias, assunto, corpo, ia) in _PASSOS_PADRAO:
            c.execute("""insert into campanha_passos (campanha_id, ordem, dias_apos, assunto, corpo, usar_ia)
                         values (%s,%s,%s,%s,%s,%s)""", (cid, ordem, dias, assunto, corpo, ia))
        c.commit()
    return RedirectResponse(f"/painel/prospeccao/campanhas/{cid}", status_code=303)


@router.get("/painel/prospeccao/campanhas/{camp_id}", response_class=HTMLResponse)
def prospeccao_campanha_det(request: Request, camp_id: int, seg: str = "", cidade: str = "", temp: str = ""):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not _pode_campanha(ctx, camp_id):
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    with get_pool().connection() as c:
        cp = c.execute("""select id, nome, status, limite_dia, coalesce(enviados_hoje,0), dia_contagem,
                                 coalesce(material,''), coalesce(wa_ativo,false), coalesce(limite_wa_dia,30),
                                 coalesce(wa_enviados_hoje,0), wa_dia_contagem, coalesce(material_tipo,'link'),
                                 coalesce(modelo_codigo,''), coalesce(wa_template_sid,''),
                                 coalesce(reengajar_ativo,false), coalesce(reengajar_dias,3),
                                 coalesce(remetente_slot,'principal'), coalesce(wa_mmlite,false),
                                 teto_wa, responsavel_id, coalesce(wa_bloqueio,'')
                            from campanhas where id=%s and conta_id=%s""",
                       (camp_id, ctx["conta_id"])).fetchone()
        if not cp:
            return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
        passos = c.execute("""select ordem, dias_apos, assunto, corpo, usar_ia
                                from campanha_passos where campanha_id=%s order by ordem""",
                           (camp_id,)).fetchall()
        st = dict(c.execute("select status, count(*) from campanha_alvos where campanha_id=%s group by status",
                            (camp_id,)).fetchall())
        na_camp = c.execute("select count(*) from campanha_alvos where campanha_id=%s", (camp_id,)).fetchone()[0]
        enviados = c.execute("select count(*) from campanha_alvos where campanha_id=%s and ultima_msg_em is not null",
                             (camp_id,)).fetchone()[0]
        abriram = c.execute("select count(*) from campanha_alvos where campanha_id=%s and aberto_em is not null",
                            (camp_id,)).fetchone()[0]
        email_detectados, wa_detectados = c.execute(
            """select count(*) filter (where nullif(trim(p.email),'') is not null),
                      count(*) filter (where coalesce(nullif(trim(p.whatsapp),''),
                                                       nullif(trim(p.telefone),'')) is not null)
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s""", (camp_id,)).fetchone()
        # "Na fila" só vale pra quem AINDA TEM por onde sair. Um alvo sem e-mail e
        # com o WhatsApp já resolvido está parado em `fila` pra sempre: o motor de
        # e-mail exige '@' e a fila do WhatsApp é `wa_status is null`. Contando os
        # dois juntos, a tela prometia trabalho que não ia acontecer — 122 alvos em
        # 5 campanhas desta conta apareciam "na fila" com os dois canais esgotados.
        fila_viva, fila_sem_canal = c.execute(
            """select count(*) filter (where position('@' in coalesce(p.email,'')) > 1
                                          or (a.wa_status is null and %s)),
                      count(*) filter (where position('@' in coalesce(p.email,'')) <= 1
                                         and not (a.wa_status is null and %s))
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s and a.status='fila'""",
            (cp[7], cp[7], camp_id)).fetchone()
        leads = c.execute(
            """select p.empresa, p.email, a.status, a.passo_atual,
                      to_char(a.proximo_envio_em - interval '3 hours','DD/MM HH24:MI'),
                      to_char(a.ultima_msg_em - interval '3 hours','DD/MM HH24:MI'), p.id,
                      coalesce(a.aberturas,0),
                      to_char(a.aberto_em - interval '3 hours','DD/MM HH24:MI'),
                      coalesce(a.wa_status,''),
                      coalesce(nullif(trim(p.whatsapp),''), nullif(trim(p.telefone),'')),
                      coalesce(a.wa_erro_msg,''), coalesce(a.wa_erro_codigo,'')
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s order by a.ultima_msg_em desc nulls last, a.id desc limit 200""",
            (camp_id,)).fetchall()
        wsql, wparams = _campanha_publico_where(ctx["conta_id"], camp_id, seg, cidade, temp)
        eleg = c.execute(f"select count(*) from prospeccao p where {wsql}", tuple(wparams)).fetchone()[0]
        sample = c.execute(
            """select p.empresa, p.segmento, p.cidade, p.uf, p.whatsapp, p.email
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s order by a.id limit 1""", (camp_id,)).fetchone()
        if not sample:
            sample = c.execute(
                """select empresa, segmento, cidade, uf, whatsapp, email from prospeccao
                    where conta_id=%s and (email_ok or coalesce(nullif(trim(whatsapp),''), nullif(trim(telefone),'')) is not null) order by atualizado_em desc limit 1""",
                (ctx["conta_id"],)).fetchone()
    resp = st.get("respondeu", 0)
    from datetime import date as _date
    hoje = cp[4] if cp[5] == _date.today() else 0
    wa_hoje = cp[9] if cp[10] == _date.today() else 0
    with get_pool().connection() as c:
        wa_counts = dict(c.execute(
            "select wa_status, count(*) from campanha_alvos where campanha_id=%s and wa_status is not null group by wa_status",
            (camp_id,)).fetchall())
        wa_custo_row = c.execute(
            """select count(*) filter (where wa_custo is not null),
                      coalesce(sum(wa_custo),0),
                      count(*) filter (where coalesce(wa_custo,0) > 0),
                      count(*) filter (where wa_cobravel is false),
                      count(*) filter (where wa_cobravel is not null)
                 from campanha_alvos where campanha_id=%s""", (camp_id,)).fetchone()
        modelos = _modelos_lista(c, ctx["conta_id"])
        # a config de HOJE manda (é o que o dono resolve agora); se estiver tudo
        # certo por aqui, sobra o que o PROVEDOR recusou no último disparo, que só
        # o motor viu (ex.: twilio_20003, credencial não autentica).
        wa_bloqueio = _prospec_convite.motivo_bloqueio(c, ctx["conta_id"], cp[13]) or cp[20]
        reserva = _reserva_numeros(c, camp_id)
    # "pulado" (já respondeu) e "sem_numero" não são disparos reais — fora da conta
    wa_enviados = sum(v for k, v in wa_counts.items() if k not in ("pulado", "sem_numero"))
    # "respondeu" implica que já foi entregue e lido — soma nos dois (não é status
    # exclusivo, é o próximo degrau depois de "lido")
    wa_entregues = wa_counts.get("entregue", 0) + wa_counts.get("lido", 0) + wa_counts.get("respondeu", 0)
    wa_lidos = wa_counts.get("lido", 0) + wa_counts.get("respondeu", 0)
    wa_erros = wa_counts.get("erro", 0)
    # custo do WhatsApp (tarifa BR): total, cobradas, grátis, custo por lead
    _cn, _ctot, _ccobr, _cfree, _cconf = (wa_custo_row or (0, 0, 0, 0, 0))
    _ctot = float(_ctot or 0)

    def _brl(v):
        return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    wa_custo = {"tem": _cn or 0, "total": _brl(_ctot), "cobradas": _ccobr or 0,
                "gratis": _cfree or 0, "confirmado": _cconf or 0,
                "por_lead": _brl(_ctot / _cn if _cn else 0.0),
                "tarifa_mkt": _brl(0.3217)}
    camp = {"id": cp[0], "nome": cp[1], "status": cp[2], "limite": cp[3],
            "status_rot": _STATUS_ROT_CP.get(cp[2], cp[2]), "material": cp[6],
            "wa_ativo": cp[7], "limite_wa": cp[8], "wa_hoje": wa_hoje, "wa_enviados": wa_enviados,
            "wa_template_sid": cp[13],
            "wa_pronto": not wa_bloqueio, "material_tipo": cp[11],
            "wa_bloqueio": _prospec_convite.rotulo_bloqueio(wa_bloqueio),
            "modelo_codigo": cp[12], "reengajar_ativo": cp[14], "reengajar_dias": cp[15],
            "remetente_slot": cp[16], "wa_mmlite": cp[17],
            "teto_wa": (f"{float(cp[18]):.2f}" if cp[18] is not None else ""),
            "responsavel_id": cp[19]}
    metr = {"total": na_camp, "fila": fila_viva, "sem_canal": fila_sem_canal,
            "enviados": enviados, "responderam": resp,
            "descadastros": st.get("descadastrou", 0), "erros": st.get("erro", 0),
            "concluidos": st.get("concluido", 0), "hoje": hoje, "abriram": abriram,
            "taxa": (round(100 * resp / enviados) if enviados else 0),
            "taxa_abertura": (round(100 * abriram / enviados) if enviados else 0),
            "wa_enviados": wa_enviados, "wa_entregues": wa_entregues, "wa_lidos": wa_lidos,
            "wa_erros": wa_erros,
            "wa_taxa_entrega": (round(100 * wa_entregues / wa_enviados) if wa_enviados else 0),
            "wa_taxa_leitura": (round(100 * wa_lidos / wa_enviados) if wa_enviados else 0),
            "email_detectados": email_detectados, "wa_detectados": wa_detectados,
            "erros_total": st.get("erro", 0) + wa_erros, "wa_custo": wa_custo,
            "wa_reserva": reserva["total"]}
    passos_l = [{"dias": p[1], "assunto": p[2], "corpo": p[3], "ia": p[4]} for p in passos]
    from finance.campanhas_motor import _fmt as _cfmt
    cadencia = " · ".join("D" + str(p["dias"]) for p in passos_l) or "—"
    previa = None
    if passos_l and sample:
        _ld = {"empresa": sample[0], "segmento": sample[1], "cidade": sample[2], "uf": sample[3],
               "whatsapp": sample[4], "email": sample[5]}
        p0 = passos_l[0]
        previa = {"ia": bool(p0["ia"]), "empresa": _ld["empresa"], "email": _ld["email"],
                  "whatsapp": _ld["whatsapp"],
                  "assunto": _cfmt(p0["assunto"] or "Uma ideia pra {empresa}", _ld),
                  "corpo": ("O agente escreve um e-mail único pra este lead — clique em “Gerar prévia com IA” "
                            "pra ver um exemplo." if p0["ia"] else _cfmt(p0["corpo"] or "", _ld))}
    _WA_ROT = {"enviado": "💬 enviado", "entregue": "✅ entregue", "lido": "👀 lido",
               "respondeu": "🔥 respondeu", "erro": "💬 erro", "sem_numero": "💬 sem nº",
               "pulado": "↩︎ já respondeu"}
    leads_l = [{"empresa": r[0], "email": r[1], "status": r[2], "passo": r[3],
                "prox": r[4], "ult": r[5], "pid": r[6], "rot": _ALVO_ROT.get(r[2], r[2]),
                "abriu": r[7], "aberto": r[8], "wa": r[9], "wa_rot": _WA_ROT.get(r[9], ""),
                # a falha quase sempre chega por webhook com CÓDIGO e sem texto, e a
                # ficha só lia a mensagem — o motivo estava gravado e a tela não usava
                "fone": r[10] or "",
                "wa_erro": (_prospec_convite.rotulo_erro_alvo(r[12], r[11])
                            if r[9] == "erro" else "")}
               for r in leads]
    from finance import email_inbound as _ein_mod
    email_principal = _ein_mod.remetente_conta(get_pool(), ctx["conta_id"], "email") or ""
    email_secundario = _ein_mod.remetente_conta(get_pool(), ctx["conta_id"], "email2") or ""
    # responsável: só o dono atribui (mesma regra do lead). O nome mostra pra todos.
    vendedores = _vendedores(get_pool(), ctx["conta_id"]) if ctx["pode_atribuir"] else []
    responsavel_nome = _nome_vendedor(get_pool(), ctx["conta_id"], camp.get("responsavel_id"))
    return _render("prospeccao_campanha", request, titulo=camp["nome"], secao_ativa="prospeccao",
                   camp=camp, passos=passos_l, elegiveis=eleg, na_camp=na_camp, st=st, metr=metr,
                   leads=leads_l, previa=previa, cadencia=cadencia, remetente=email_principal,
                   modelos=modelos, seg=seg, cidade=cidade, temp=temp,
                   email_principal=email_principal, email_secundario=email_secundario,
                   reserva=reserva, tentativas_teto=_cm._WA_TENTATIVAS,
                   vendedores=vendedores, responsavel_nome=responsavel_nome,
                   pode_atribuir=ctx["pode_atribuir"], gerencia=ctx["gerencia"],
                   aviso=request.session.pop("prosp_aviso", None))


@router.get("/painel/prospeccao/campanhas/{camp_id}/lead/{pid}/historico")
def prospeccao_campanha_historico(request: Request, camp_id: int, pid: int):
    """Linha do tempo de um lead na campanha, separada por canal (📧 / 💬)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    with get_pool().connection() as c:
        if not _pode_campanha(ctx, camp_id, c):
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        rows = c.execute(
            """select canal, evento, coalesce(detalhe,''),
                      to_char(quando - interval '3 hours','DD/MM HH24:MI')
                 from campanha_eventos where campanha_id=%s and prospeccao_id=%s
                order by quando asc, id asc""", (camp_id, pid)).fetchall()
    _rot = {"enviado": "Enviado", "aberto": "Abriu 👁", "entregue": "Entregue ✓✓", "lido": "Leu 👀",
            "respondeu": "Respondeu 🔥", "clicou": "Clicou", "baixou": "Abriu o material 📎",
            "bounce": "Retornou (inválido) ⚠", "erro": "Falhou ⚠", "descadastrou": "Descadastrou"}
    email = [{"rot": _rot.get(e, e), "detalhe": d, "quando": q} for (cn, e, d, q) in rows if cn == "email"]
    wpp = [{"rot": _rot.get(e, e), "detalhe": d, "quando": q} for (cn, e, d, q) in rows if cn == "whatsapp"]
    return JSONResponse({"ok": True, "email": email, "whatsapp": wpp})


@router.get("/painel/prospeccao/campanhas/{camp_id}/exportar-cliques")
def prospeccao_campanha_exportar_cliques(request: Request, camp_id: int):
    """CSV com 1 linha por lead da campanha: quando abriu o e-mail, clicou 'Tenho
    interesse', baixou o material, leu no WhatsApp, clicou 'Agora não' e descadastrou
    (o que já existe; nada disso é tracking novo — só agrega campanha_eventos por lead)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not _pode_campanha(ctx, camp_id):
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    with get_pool().connection() as c:
        camp = c.execute("select nome from campanhas where id=%s and conta_id=%s",
                         (camp_id, ctx["conta_id"])).fetchone()
        if not camp:
            return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
        leads = c.execute(
            """select a.prospeccao_id, p.empresa,
                      coalesce(nullif(trim(p.whatsapp),''), nullif(trim(p.telefone),'')),
                      nullif(trim(p.email),'')
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s order by p.empresa""", (camp_id,)).fetchall()
        eventos = c.execute(
            """select prospeccao_id, canal, evento, coalesce(detalhe,''), min(quando)
                 from campanha_eventos where campanha_id=%s
                group by prospeccao_id, canal, evento, coalesce(detalhe,'')""", (camp_id,)).fetchall()
        descads = dict(c.execute(
            """select lower(p.email), d.criado_em
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                 join descadastros d on d.conta_id=%s and lower(d.email)=lower(p.email)
                where a.campanha_id=%s""", (ctx["conta_id"], camp_id)).fetchall())
    por_lead = {}
    for pid, canal, evento, detalhe, quando in eventos:
        por_lead.setdefault(pid, []).append((canal, evento, detalhe, quando))

    def _fmt(dt):
        return (dt - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M") if dt else ""

    def _min_quando(evs, pred):
        achados = [q for (canal, evento, detalhe, q) in evs if pred(canal, evento, detalhe)]
        return min(achados) if achados else None

    buf = io.StringIO()
    w = _csv.writer(buf, delimiter=";")
    w.writerow(["Empresa", "WhatsApp", "E-mail", "Abriu e-mail", "Clicou \"Tenho interesse\"",
                "Baixou material", "Leu no WhatsApp", "Clicou \"Agora não\"", "Descadastrou"])
    for pid, empresa, telefone, email in leads:
        evs = por_lead.get(pid, [])
        abriu = _min_quando(evs, lambda cn, e, d: cn == "email" and e == "aberto")
        interesse = _min_quando(evs, lambda cn, e, d: cn == "email" and e == "respondeu" and d == "Tenho interesse")
        baixou = _min_quando(evs, lambda cn, e, d: e == "baixou")
        leu_wa = _min_quando(evs, lambda cn, e, d: cn == "whatsapp" and e == "lido")
        agora_nao = _min_quando(evs, lambda cn, e, d: cn == "whatsapp" and e == "clicou")
        descad = descads.get((email or "").lower())
        w.writerow([empresa or "", telefone or "", email or "", _fmt(abriu), _fmt(interesse),
                    _fmt(baixou), _fmt(leu_wa), _fmt(agora_nao), _fmt(descad)])
    nome_arq = _slug_modelo(camp[0]) + "-cliques.csv"
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nome_arq}"'})


@router.post("/painel/prospeccao/campanhas/{camp_id}/publico")
def prospeccao_campanha_publico(request: Request, camp_id: int, seg: str = Form(""),
                                cidade: str = Form(""), temp: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not _pode_campanha(ctx, camp_id):
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    with get_pool().connection() as c:
        if not c.execute("select 1 from campanhas where id=%s and conta_id=%s",
                         (camp_id, ctx["conta_id"])).fetchone():
            return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
        wsql, wparams = _campanha_publico_where(ctx["conta_id"], camp_id, seg, cidade, temp)
        n = c.execute(
            f"""insert into campanha_alvos (campanha_id, prospeccao_id)
                select %s, p.id from prospeccao p where {wsql} on conflict do nothing""",
            (camp_id, *wparams)).rowcount
        c.commit()
    request.session["prosp_aviso"] = f"{n} lead(s) adicionado(s) à campanha ✓"
    return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)


def _campanha_dona(c, conta_id, camp_id):
    return c.execute("select 1 from campanhas where id=%s and conta_id=%s", (camp_id, conta_id)).fetchone()


@router.post("/painel/prospeccao/campanhas/{camp_id}/config")
async def prospeccao_campanha_config(request: Request, camp_id: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not _pode_campanha(ctx, camp_id):
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    form = await request.form()
    nome = (form.get("nome") or "").strip()
    try:
        lim = max(1, min(500, int(form.get("limite_dia") or 40)))
    except (ValueError, TypeError):
        lim = 40
    try:
        lim_wa = max(1, min(200, int(form.get("limite_wa_dia") or 30)))
    except (ValueError, TypeError):
        lim_wa = 30
    wa_on = str(form.get("wa_ativo") or "").strip().lower() in ("1", "on", "true", "sim")
    wa_aviso = None
    if wa_on:
        from finance import whatsapp_out as _wa_out
        with get_pool().connection() as _c:
            if not _wa_out.configurado_conta(_c, ctx["conta_id"]):
                wa_on = False
                wa_aviso = "Conecte o WhatsApp da empresa na aba Canais antes de ativar nas campanhas."
    wa_mm = str(form.get("wa_mmlite") or "").strip().lower() in ("1", "on", "true", "sim")
    # teto de gasto do WhatsApp (R$). Aceita "50", "50,00", "1.200,50" ou vazio (sem teto).
    _traw = (form.get("teto_wa") or "").replace("R$", "").strip()
    if "," in _traw and "." in _traw:
        _traw = _traw.replace(".", "")
    _traw = _traw.replace(",", ".")
    try:
        teto_wa = round(float(_traw), 2) if _traw else None
    except (ValueError, TypeError):
        teto_wa = None
    if teto_wa is not None and teto_wa <= 0:
        teto_wa = None
    wa_sid = (form.get("wa_template_sid") or "").strip()[:64]
    reeng_on = str(form.get("reengajar_ativo") or "").strip().lower() in ("1", "on", "true", "sim")
    try:
        reeng_dias = max(1, min(30, int(form.get("reengajar_dias") or 3)))
    except (ValueError, TypeError):
        reeng_dias = 3
    remet_slot = (form.get("remetente_slot") or "principal").strip().lower()
    if remet_slot not in ("principal", "secundario"):
        remet_slot = "principal"
    tipo = (form.get("material_tipo") or "link").strip().lower()
    if tipo not in ("link", "video", "pdf", "foto"):
        tipo = "link"
    # material: None = mantém o que já está (troca de aba sem novo arquivo não apaga)
    material = None
    if tipo == "link":
        material = (form.get("material_link") or "").strip()[:2000]
    elif tipo == "video":
        material = (form.get("material_video") or "").strip()[:2000]
    else:  # pdf/foto → upload (se veio arquivo novo)
        up = form.get("material_pdf") if tipo == "pdf" else form.get("material_foto")
        if up is not None and getattr(up, "filename", ""):
            try:
                conteudo = await up.read()
                from finance.upload_foto import subir_material
                material = subir_material(conteudo, up.filename, getattr(up, "content_type", "") or "")
            except Exception as e:  # noqa: BLE001
                request.session["prosp_aviso"] = f"Não consegui subir o arquivo: {e}"
                return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)
    with get_pool().connection() as c:
        if material is None:
            c.execute("""update campanhas set nome=coalesce(nullif(%s,''),nome), limite_dia=%s,
                           material_tipo=%s, wa_ativo=%s, limite_wa_dia=%s, wa_template_sid=%s,
                           reengajar_ativo=%s, reengajar_dias=%s, remetente_slot=%s, wa_mmlite=%s,
                           teto_wa=%s, atualizado_em=now()
                         where id=%s and conta_id=%s""",
                      (nome[:120], lim, tipo, wa_on, lim_wa, (wa_sid or None),
                       reeng_on, reeng_dias, remet_slot, wa_mm, teto_wa, camp_id, ctx["conta_id"]))
        else:
            c.execute("""update campanhas set nome=coalesce(nullif(%s,''),nome), limite_dia=%s,
                           material=%s, material_tipo=%s, wa_ativo=%s, limite_wa_dia=%s,
                           wa_template_sid=%s, reengajar_ativo=%s, reengajar_dias=%s,
                           remetente_slot=%s, wa_mmlite=%s, teto_wa=%s, atualizado_em=now()
                         where id=%s and conta_id=%s""",
                      (nome[:120], lim, material, tipo, wa_on, lim_wa, (wa_sid or None),
                       reeng_on, reeng_dias, remet_slot, wa_mm, teto_wa, camp_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = wa_aviso or "Configuração salva ✓"
    return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)


@router.post("/painel/prospeccao/campanhas/{camp_id}/remover-lead")
def prospeccao_campanha_remover_lead(request: Request, camp_id: int, prospeccao_id: str = Form("")):
    """Tira um lead da campanha (apaga só o vínculo em campanha_alvos). O lead
    continua na Base, disponível pra reenviar certo. Usado quando cai errado."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not _pode_campanha(ctx, camp_id):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    try:
        pid = int(prospeccao_id)
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "erro": "id"}, status_code=400)
    with get_pool().connection() as c:
        if not _campanha_dona(c, ctx["conta_id"], camp_id):
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        n = c.execute("delete from campanha_alvos where campanha_id=%s and prospeccao_id=%s",
                      (camp_id, pid)).rowcount
        c.commit()
    return JSONResponse({"ok": True, "removidos": n})


@router.post("/painel/prospeccao/campanhas/{camp_id}/remover-leads")
def prospeccao_campanha_remover_leads(request: Request, camp_id: int, ids: list[str] = Form([])):
    """Tira vários leads da campanha de uma vez (mesmo efeito do ✕ individual, em lote).
    Os leads continuam na Base, disponíveis pra reenviar certo — usado quando um lote
    caiu sem a riqueza de informação necessária ou sem interesse."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not _pode_campanha(ctx, camp_id):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    pids = []
    for v in ids:
        try:
            pids.append(int(v))
        except (ValueError, TypeError):
            pass
    if not pids:
        return JSONResponse({"ok": False, "erro": "sem_ids"}, status_code=400)
    with get_pool().connection() as c:
        if not _campanha_dona(c, ctx["conta_id"], camp_id):
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        n = c.execute("delete from campanha_alvos where campanha_id=%s and prospeccao_id = any(%s)",
                      (camp_id, pids)).rowcount
        c.commit()
    return JSONResponse({"ok": True, "removidos": n})


@router.post("/painel/prospeccao/campanhas/{camp_id}/recolocar-na-fila")
def prospeccao_campanha_recolocar_na_fila(request: Request, camp_id: int,
                                          ids: list[str] = Form([])):
    """Devolve alvos parados pra fila de disparo do WhatsApp, com os OUTROS números.

    O contador de tentativas é PRESERVADO de propósito. Zerando, um lead que já
    recebeu uma mensagem ganharia o teto inteiro de novo e poderia levar 4 no
    total — mais insistente do que o teto de 3 que a regra promete. Preservando,
    "3 tentativas por lead" vale de verdade, e um alvo que já esgotou o teto não
    volta pra fila mesmo se for marcado aqui.

    `wa_tentados` também não é limpo: o número que já falhou não volta nunca. E
    isto é um botão, não automático, porque cada tentativa é uma mensagem de
    marketing cobrada — quem decide gastar é o dono.

    Só recoloca quem ainda é elegível pro WhatsApp (`_STATUS_WA_ELEGIVEL`): quem
    respondeu ou se descadastrou não volta, por mais que seja marcado aqui.

    DESTRAVA o `alvo_telefone`. Quando o dono escolheu um número no checkbox da
    Base, o disparo automático não adivinha em cima dessa escolha — mas se aquele
    número falhou e ele está apertando este botão, é justamente isso que ele está
    pedindo: tente os outros. Sem destravar aqui, 16 leads em produção ficavam com
    87 telefones guardados e um botão que não fazia nada.

    Recebe `campanha_alvos.id` (não prospeccao_id): o painel lista por alvo."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not _pode_campanha(ctx, camp_id):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    aids = []
    for v in ids:
        try:
            aids.append(int(v))
        except (ValueError, TypeError):
            pass
    if not aids:
        return JSONResponse({"ok": False, "erro": "sem_ids"}, status_code=400)
    with get_pool().connection() as c:
        if not _campanha_dona(c, ctx["conta_id"], camp_id):
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        n = c.execute(
            """update campanha_alvos
                 set wa_status=null, wa_erro_codigo=null, wa_erro_msg=null,
                     wa_sid=null, wa_numero=null, alvo_telefone=null
               where campanha_id=%s and id = any(%s) and wa_status='erro'
                 and coalesce(wa_tentativas,0) < %s
                 and status = any(%s)""",
            (camp_id, aids, _cm._WA_TENTATIVAS,
             list(_cm._STATUS_WA_ELEGIVEL))).rowcount
        c.commit()
    return JSONResponse({"ok": True, "recolocados": n})


@router.post("/painel/prospeccao/campanhas/{camp_id}/usar-modelo")
def prospeccao_campanha_usar_modelo(request: Request, camp_id: int, codigo: str = Form("")):
    """Copia (snapshot) os passos do modelo escolhido pra sequência da campanha."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not _pode_campanha(ctx, camp_id):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    codigo = (codigo or "").strip()
    with get_pool().connection() as c:
        if not _campanha_dona(c, ctx["conta_id"], camp_id):
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        passos = _modelo_passos(c, ctx["conta_id"], codigo)
        if passos is None:
            return JSONResponse({"ok": False, "erro": "modelo"}, status_code=404)
        c.execute("delete from campanha_passos where campanha_id=%s", (camp_id,))
        for ordem, p in enumerate(passos):
            c.execute("""insert into campanha_passos (campanha_id, ordem, dias_apos, assunto, corpo, usar_ia)
                         values (%s,%s,%s,%s,%s,%s)""",
                      (camp_id, ordem, max(0, min(120, int(p.get("dias") or 0))),
                       (p.get("assunto") or "").strip()[:300], (p.get("corpo") or "").strip()[:8000],
                       bool(p.get("ia"))))
        c.execute("update campanhas set modelo_codigo=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (codigo, camp_id, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True, "codigo": codigo})


@router.post("/painel/prospeccao/campanhas/{camp_id}/salvar-modelo")
def prospeccao_campanha_salvar_modelo(request: Request, camp_id: int, nome: str = Form("")):
    """Salva a sequência atual da campanha como um modelo do dono (reutilizável)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not _pode_campanha(ctx, camp_id):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    nome = (nome or "").strip()[:80]
    if not nome:
        return JSONResponse({"ok": False, "erro": "nome"}, status_code=400)
    codigo = _slug_modelo(nome)
    if codigo in _MODELOS_BASE_COD:
        codigo += "-meu"          # não sombreia um modelo base
    import json
    with get_pool().connection() as c:
        if not _campanha_dona(c, ctx["conta_id"], camp_id):
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        rows = c.execute("""select dias_apos, assunto, corpo, usar_ia from campanha_passos
                             where campanha_id=%s order by ordem""", (camp_id,)).fetchall()
        arr = [{"dias": r[0], "assunto": r[1], "corpo": r[2], "ia": bool(r[3])} for r in rows]
        c.execute("""insert into campanha_modelos (conta_id, codigo, nome, passos)
                       values (%s,%s,%s,%s::jsonb)
                     on conflict (conta_id, codigo) do update
                       set nome=excluded.nome, passos=excluded.passos, atualizado_em=now()""",
                  (ctx["conta_id"], codigo, nome, json.dumps(arr)))
        c.execute("update campanhas set modelo_codigo=%s where id=%s and conta_id=%s",
                  (codigo, camp_id, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True, "codigo": codigo, "nome": nome})


@router.post("/painel/prospeccao/campanhas/{camp_id}/status")
def prospeccao_campanha_status(request: Request, camp_id: int, status: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if status not in ("rascunho", "ativa", "pausada", "concluida") or not _pode_campanha(ctx, camp_id):
        return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)
    with get_pool().connection() as c:
        c.execute("update campanhas set status=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (status, camp_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = {"ativa": "Campanha ativada ✓ (o disparo entra na próxima etapa)",
                                      "pausada": "Campanha pausada.",
                                      "rascunho": "Voltou pra rascunho."}.get(status, "Status atualizado.")
    return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)


@router.post("/painel/prospeccao/campanhas/{camp_id}/responsavel")
def prospeccao_campanha_responsavel(request: Request, camp_id: int, vendedor_id: str = Form("")):
    """Vincula (ou desvincula) o responsável da campanha — o membro que passa a vê-la e
    gerenciá-la (espelha a atribuição de lead ao vendedor). Só o dono atribui; '— livre —'
    (vazio) tira o responsável, e aí só dono/gestor veem a campanha."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["pode_atribuir"]:
        return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)
    vend = _vendedor_destino(ctx, vendedor_id, get_pool(), ctx["conta_id"])
    with get_pool().connection() as c:
        c.execute("update campanhas set responsavel_id=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (vend, camp_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = ("Responsável vinculado ✓ — ele já vê e gerencia essa campanha."
                                      if vend else "Campanha sem responsável (só dono/gestor veem).")
    return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)


@router.post("/painel/prospeccao/campanhas/{camp_id}/sequencia")
def prospeccao_campanha_sequencia(request: Request, camp_id: int,
                                  dias: list[str] = Form([]), assunto: list[str] = Form([]),
                                  corpo: list[str] = Form([]), usar_ia: list[str] = Form([])):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not _pode_campanha(ctx, camp_id):
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    with get_pool().connection() as c:
        if not _campanha_dona(c, ctx["conta_id"], camp_id):
            return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
        c.execute("delete from campanha_passos where campanha_id=%s", (camp_id,))
        ordem = 0
        for i in range(len(dias)):
            try:
                d = max(0, min(120, int(dias[i])))
            except (ValueError, TypeError):
                d = 0
            a = (assunto[i] if i < len(assunto) else "").strip()[:300]
            co = (corpo[i] if i < len(corpo) else "").strip()[:8000]
            ia = (usar_ia[i] if i < len(usar_ia) else "0") == "1"
            if not a and not co and not ia:
                continue                      # passo vazio → ignora
            c.execute("""insert into campanha_passos (campanha_id, ordem, dias_apos, assunto, corpo, usar_ia)
                         values (%s,%s,%s,%s,%s,%s)""", (camp_id, ordem, d, a, co, ia))
            ordem += 1
        if ordem == 0:                         # garante ao menos 1 passo
            c.execute("""insert into campanha_passos (campanha_id, ordem, dias_apos, assunto, corpo, usar_ia)
                         values (%s,0,0,%s,'',true)""", (camp_id, "Uma ideia pra {empresa}"))
        c.commit()
    request.session["prosp_aviso"] = "Sequência salva ✓"
    return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)


@router.post("/painel/prospeccao/campanhas/{camp_id}/excluir")
def prospeccao_campanha_excluir(request: Request, camp_id: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    with get_pool().connection() as c:
        c.execute("delete from campanhas where id=%s and conta_id=%s", (camp_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Campanha excluída."
    return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)


@router.post("/painel/prospeccao/campanhas/{camp_id}/reiniciar")
def prospeccao_campanha_reiniciar(request: Request, camp_id: int):
    """Recomeça a campanha do zero: todos os leads voltam pra fila (passo 0), o
    acompanhamento (aberturas, status de WhatsApp, timeline de Desempenho) é
    zerado e a campanha fica PAUSADA — o disparo só recomeça no ▶ Ativar. As
    conversas do inbox (mensagens) são preservadas."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not _pode_campanha(ctx, camp_id):
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    with get_pool().connection() as c:
        if not c.execute("select 1 from campanhas where id=%s and conta_id=%s",
                         (camp_id, ctx["conta_id"])).fetchone():
            return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
        n = c.execute(
            """update campanha_alvos
                  set status='fila', passo_atual=0, proximo_envio_em=null,
                      ultima_msg_em=null, wa_status=null, wa_em=null, wa_sid=null,
                      aberto_em=null, aberturas=0, reengajado_em=null
                where campanha_id=%s""", (camp_id,)).rowcount
        c.execute("delete from campanha_eventos where campanha_id=%s", (camp_id,))
        c.execute("""update campanhas set status='pausada', enviados_hoje=0,
                        wa_enviados_hoje=0, atualizado_em=now() where id=%s""", (camp_id,))
        c.commit()
    request.session["prosp_aviso"] = (
        f"Campanha reiniciada 🔄 — {n} lead(s) voltaram pra fila (passo 0) e o acompanhamento foi zerado. "
        "Está pausada; clique ▶ Ativar pra o motor recomeçar o disparo do 1º e-mail.")
    return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)


@router.post("/painel/prospeccao/campanhas/{camp_id}/previa-ia")
def prospeccao_campanha_previa_ia(request: Request, camp_id: int):
    """Gera uma prévia real do e-mail que o agente escreveria (1 lead de exemplo)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not _pode_campanha(ctx, camp_id):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    pool = get_pool()
    with pool.connection() as c:
        if not _campanha_dona(c, ctx["conta_id"], camp_id):
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        s = c.execute(
            """select p.empresa, p.segmento, p.cidade, p.uf, p.whatsapp, p.email
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s order by a.id limit 1""", (camp_id,)).fetchone()
        if not s:
            s = c.execute("""select empresa,segmento,cidade,uf,whatsapp,email from prospeccao
                              where conta_id=%s and (email_ok or coalesce(nullif(trim(whatsapp),''), nullif(trim(telefone),'')) is not null) order by atualizado_em desc limit 1""",
                          (ctx["conta_id"],)).fetchone()
    if not s:
        return JSONResponse({"ok": False, "erro": "Adicione leads (com e-mail) primeiro."})
    lead = {"empresa": s[0], "segmento": s[1], "cidade": s[2], "uf": s[3], "whatsapp": s[4], "email": s[5]}
    try:
        from finance.campanhas_motor import _email_ia, _conta_identidade, _tira_assinatura, _assinatura_texto
        m = _email_ia(pool, ctx["conta_id"], lead)
        with pool.connection() as c:
            idn = _conta_identidade(c, ctx["conta_id"])
        corpo = _tira_assinatura(m["corpo"]) + "\n\n" + _assinatura_texto(idn)
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": "Não consegui gerar a prévia."})
    return JSONResponse({"ok": True, "empresa": lead["empresa"], "assunto": m["assunto"], "corpo": corpo})


def _numeros_candidatos(alvo: dict) -> list[dict]:
    """Todos os números já captados do lead, ORDENADOS do mais provável/quente pra
    baixo, pro seletor de envio no WhatsApp. Prioriza: telefone do decisor marcado
    ⭐ (mais provável) > decisor com WhatsApp > decisor > WhatsApp do lead > telefone
    da empresa. Dedup por dígitos."""
    vistos, out = set(), []

    def add(numero, label, score, whatsapp=False):
        d = _so_digitos(numero or "")
        if len(d) < 10 or d in vistos:
            return
        vistos.add(d)
        out.append({"numero": numero, "label": label, "score": score, "whatsapp": whatsapp})

    for t in (alvo.get("decisor_telefones") or []):
        fmt = t.get("formatado") or ""
        wpp = bool(t.get("whatsapp"))
        prov = bool(t.get("provavel"))
        rot = ("⭐ " if prov else "") + "Decisor"
        if wpp:
            rot += " · WhatsApp"
        if t.get("tipo_rot"):
            rot += " · " + t["tipo_rot"]
        add(fmt, rot, 60 + (40 if prov else 0) + (30 if wpp else 0), wpp)
    if alvo.get("whatsapp"):
        add(alvo["whatsapp"], "WhatsApp do lead", 45, True)
    if alvo.get("telefone"):
        add(alvo["telefone"], "Telefone da empresa", 20, False)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


# ================================================================ FICHA DO ALVO
# ================================================================ RÉGUA DO FUNIL
# ATENÇÃO À POSIÇÃO: estas rotas TÊM que ser declaradas antes de
# `/painel/prospeccao/{alvo_id}` (logo abaixo). O FastAPI casa por ORDEM DE
# REGISTRO, não por especificidade: com a ficha do lead declarada primeiro, ela
# engole /painel/prospeccao/regua tratando "regua" como id de lead, e a tela
# responde 422 "unable to parse string as an integer". Foi assim que a Régua
# subiu quebrada em produção — ver test_regua_vem_antes_da_ficha_do_lead.
# A configuração da empresa num lugar só: as etapas (nome, fase, ordem), o gatilho
# que traz o lead pra cada uma, o prazo que ela aguenta, e os dois interruptores.
# Nasce tudo desligado — quem liga é o dono, um gatilho de cada vez.
_UNIDADES = [("min", "minutos", 1), ("h", "horas", 60), ("d", "dias", 1440)]
_UNI_MIN = {u: m for u, _r, m in _UNIDADES}


def _min_par(m):
    """240 -> (4, 'h'). Escolhe a maior unidade que divide certo, pra tela mostrar
    '4 horas' e não '240 minutos'."""
    if not m:
        return ("", "h")
    for uni, _rot, mult in reversed(_UNIDADES):
        if m % mult == 0:
            return (m // mult, uni)
    return (m, "min")


def _par_min(n, uni):
    """('4','h') -> 240. Vazio/zero/negativo = sem prazo (None), que é como a etapa
    diz 'não cobro ninguém' sem precisar de uma segunda coluna pra isso."""
    try:
        v = int(str(n).strip())
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v * _UNI_MIN.get(uni, 60)


@router.get("/painel/prospeccao/regua", response_class=HTMLResponse)
def regua_pagina(request: Request):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "A régua é configuração da empresa — só dono/gestor."
        return RedirectResponse("/painel/prospeccao", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        _etapas(c, ctx["conta_id"])                 # semeia o padrão na 1ª visita
        cfg = _fr.config(c, ctx["conta_id"])
        c.commit()
        linhas = _fr.etapas(c, ctx["conta_id"])
        # quantos leads em cada coluna, pro dono ver o que ele está mexendo (e pra
        # tela saber quando o botão de remover pode ficar habilitado)
        n_por = dict(c.execute(
            """select status, count(*) from prospeccao
                where conta_id=%s and estagio='lead' group by status""",
            (ctx["conta_id"],)).fetchall())
        # o histórico já roda com tudo desligado — mostrar o tamanho dele é o que
        # justifica esperar antes de ligar
        n_mov = c.execute("select count(*) from funil_movimentos where conta_id=%s",
                          (ctx["conta_id"],)).fetchone()[0]
    for e in linhas:
        e["n"] = n_por.get(e["chave"], 0)
        e["prazo_n"], e["prazo_u"] = _min_par(e["prazo_min"])
        e["gatilho_rot"] = _fr.EVENTOS.get(e["gatilho"] or "", "")
    conv = [{"chave": k, "rotulo": v, "prazo_n": _min_par(cfg[c_])[0], "prazo_u": _min_par(cfg[c_])[1]}
            for k, v, c_ in (("sem_resposta", "Sem resposta", "sem_resposta_min"),
                             ("bola_nossa", "Bola com você", "bola_nossa_min"),
                             ("bola_cliente", "Bola com o cliente", "bola_cliente_min"))]
    return _render("prospeccao_regua", request, titulo="Régua do funil",
                   secao_ativa="prospeccao", nav_ativo="regua", gerencia=True,
                   etapas=linhas, cfg=cfg, conv=conv, eventos=sorted(_fr.EVENTOS.items()),
                   unidades=[(u, r) for u, r, _m in _UNIDADES],
                   dias_on=_fr._dias(cfg), n_mov=n_mov,
                   aviso=request.session.pop("prosp_aviso", None))


@router.post("/painel/prospeccao/regua/config")
async def regua_config(request: Request):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse("/painel/prospeccao", status_code=303)
    f = await request.form()

    def modo(nome):
        v = (f.get(nome) or "off").strip()
        return v if v in _fr.MODOS else "off"

    dias = ",".join(d for d in (f.getlist("dias") or []) if d in "1234567")
    abre = (f.get("abre") or "08:00").strip()[:5]
    fecha = (f.get("fecha") or "19:00").strip()[:5]
    with get_pool().connection() as c:
        _fr.config(c, ctx["conta_id"])          # garante a linha
        c.execute("""update funil_regua set gatilhos_modo=%s, cobranca_modo=%s,
                       janela_dias=%s, janela_abre=%s, janela_fecha=%s,
                       sem_resposta_min=coalesce(%s, sem_resposta_min),
                       bola_nossa_min=coalesce(%s, bola_nossa_min),
                       bola_cliente_min=coalesce(%s, bola_cliente_min),
                       escala_min=coalesce(%s, escala_min),
                       teto_avisos_dia=greatest(1, coalesce(%s, teto_avisos_dia)),
                       atualizado_em=now()
                     where conta_id=%s""",
                  (modo("gatilhos_modo"), modo("cobranca_modo"), dias, abre, fecha,
                   _par_min(f.get("sem_resposta_n"), f.get("sem_resposta_u")),
                   _par_min(f.get("bola_nossa_n"), f.get("bola_nossa_u")),
                   _par_min(f.get("bola_cliente_n"), f.get("bola_cliente_u")),
                   _par_min(f.get("escala_n"), f.get("escala_u")),
                   _par_min(f.get("teto"), "min"), ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Régua salva ✓"
    return RedirectResponse("/painel/prospeccao/regua", status_code=303)


@router.post("/painel/prospeccao/regua/etapa/{eid}")
async def regua_etapa(request: Request, eid: int):
    """Salva UMA etapa: rótulo, prazo, gatilho e a chave que liga esse gatilho.
    Uma linha por vez de propósito — o dono pediu pra ligar um de cada vez, e um
    formulário só pra sete etapas transformaria isso em tudo-ou-nada."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    f = await request.form()
    rot = (f.get("rotulo") or "").strip()[:40]
    gat = (f.get("gatilho") or "").strip()
    gat = gat if gat in _fr.EVENTOS else None
    ativo = str(f.get("gatilho_ativo") or "").lower() in ("1", "on", "true", "sim")
    prazo = _par_min(f.get("prazo_n"), f.get("prazo_u"))
    with get_pool().connection() as c:
        r = c.execute("select chave, fixa from funil_etapas where id=%s and conta_id=%s",
                      (eid, ctx["conta_id"])).fetchone()
        if not r:
            return JSONResponse({"ok": False, "erro": "etapa"}, status_code=404)
        # Etapa de resultado não tem prazo: "está em Perdido há 30 dias" não é uma
        # cobrança, é o fim da história. Deixar o campo aberto só convidaria alguém
        # a criar um alarme que nunca deveria tocar.
        if r[0] in ("ganho", "perdido"):
            prazo = None
        c.execute("""update funil_etapas
                        set rotulo = coalesce(nullif(%s,''), rotulo),
                            prazo_min = %s, gatilho = %s,
                            -- ligar sem escolher evento não liga nada: a etapa ficaria
                            -- "ativa" apontando pro vazio e o motor rodaria em falso.
                            -- O ::text é pro Postgres saber o tipo do parâmetro solto.
                            gatilho_ativo = (%s and %s::text is not null)
                      where id=%s and conta_id=%s""",
                  (rot, prazo, gat, ativo, gat, eid, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True, "gatilho_ativo": bool(ativo and gat)})



def _dur(minutos: float) -> str:
    """Duração pra humano. 12 minutos é '12 min', não '0,2 h' — e a mediana desta
    equipe É 12 minutos, então arredondar pra hora apagaria justamente o número que
    mostra que os vendedores são rápidos."""
    m = int(round(minutos or 0))
    if m < 60:
        return f"{m} min"
    if m < 1440:
        return f"{m // 60}h{m % 60:02d}"
    return f"{m // 1440}d {(m % 1440) // 60}h"


@router.get("/painel/prospeccao/regua/ritmo", response_class=HTMLResponse)
def regua_ritmo(request: Request, dias: int = 21):
    """O ritmo real da equipe, medido. É esta tela que escolhe os prazos da régua —
    ela existe pra que ninguém precise chutar, inclusive eu."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "O ritmo da equipe é visão de dono/gestor."
        return RedirectResponse("/painel/prospeccao", status_code=303)
    dias = max(7, min(int(dias or 21), 90))
    with get_pool().connection() as c:
        d = _fr.medir(c, ctx["conta_id"], dias)
        cfg = _fr.config(c, ctx["conta_id"])
        rot = {e["chave"]: e["rotulo"] for e in _fr.etapas(c, ctx["conta_id"])}
        c.commit()
    # a barra vai até o P90 do mais lento — comparar vendedor com vendedor só faz
    # sentido na mesma régua
    teto = max([v["p90"] for v in d["vendedores"]] or [1]) or 1
    for v in d["vendedores"]:
        v["p50_rot"], v["p90_rot"] = _dur(v["p50"]), _dur(v["p90"])
        v["larg50"] = f"{min(100, v['p50'] / teto * 100):.1f}%"
        v["larg90"] = f"{min(100, v['p90'] / teto * 100):.1f}%"
    for e in d["etapas"]:
        e["rotulo"] = rot.get(e["chave"], e["chave"])
        e["dur"] = _dur(e["horas"] * 60)
    maior = max([n for _r, n in d["cortes"]] or [1]) or 1
    cortes = [{"rotulo": r, "n": n, "dia": f"{n / max(dias, 1):.1f}".replace(".", ","),
               "larg": f"{(n / maior * 100):.0f}%"} for r, n in d["cortes"]]
    return _render("prospeccao_ritmo", request, titulo="O ritmo real",
                   secao_ativa="prospeccao", nav_ativo="regua", gerencia=True,
                   d=d, cfg=cfg, cortes=cortes, dias=dias, dur=_dur,
                   aviso=request.session.pop("prosp_aviso", None))




_CANAL_META_LEAD = {"whatsapp": ("💬", "WhatsApp"), "email": ("✉️", "E-mail"),
                     "email2": ("✉️", "E-mail"), "instagram": ("📷", "Instagram"),
                     "messenger": ("💬", "Messenger")}


def _timeline_lead(pool, alvo_id: int, limite: int | None = None):
    """Histórico de atividades do lead, mais recente primeiro. Usado na ficha
    completa (sem limite) e no resumo do balão do funil (últimas 2)."""
    sql = """select a.tipo, a.resultado, a.descricao, a.agendado_para, a.criado_em, m.nome
               from prospeccao_atividades a
               left join membros m on m.id = a.membro_id
              where a.prospeccao_id=%s order by a.criado_em desc"""
    params: list = [alvo_id]
    if limite:
        sql += " limit %s"
        params.append(limite)
    with pool.connection() as c:
        ativs = c.execute(sql, tuple(params)).fetchall()
    timeline = []
    for (t, rr, d, ag, cr, nome) in ativs:
        cor = "#3ee0a6" if rr in _RES_VERDE else "var(--ambar)" if rr in _RES_AMBAR else "#7a7a7a"
        timeline.append({"tipo_rot": TIPO_ROT.get(t, t), "resultado_rot": RESULTADO_ROT.get(rr or "", ""),
                         "descricao": d, "agendado_para": ag, "criado_em": cr, "quem": nome, "cor": cor})
    return timeline


def _canais_contato_lead(pool, alvo_id: int):
    """Canais por onde o lead se comunicou (das conversas/mensagens) — as
    'caixinhas' no topo da ficha e no resumo do balão do funil. Ordenado por
    quem respondeu primeiro; os só-enviados (sem resposta) vêm depois."""
    with pool.connection() as c:
        canais_ct = c.execute(
            """select cv.canal,
                      count(*) filter (where m.direcao='in')             as ins,
                      min(m.criado_em) filter (where m.direcao='in')     as primeiro_in,
                      max(m.criado_em)                                    as ultimo
                 from conversas cv join mensagens m on m.conversa_id=cv.id
                where cv.prospeccao_id=%s
                group by cv.canal""", (alvo_id,)).fetchall()
    canais_contato = []
    for (canal, ins, primeiro_in, ultimo) in canais_ct:
        ic, lb = _CANAL_META_LEAD.get(canal, ("•", (canal or "canal").title()))
        canais_contato.append({"canal": canal, "ic": ic, "label": lb, "ins": ins or 0,
                                "respondeu": (ins or 0) > 0, "primeiro_in": primeiro_in, "ultimo": ultimo})
    canais_contato.sort(key=lambda x: (not x["respondeu"], x["primeiro_in"] or x["ultimo"]))
    return canais_contato


@router.get("/painel/prospeccao/{alvo_id}/resumo")
def prospeccao_resumo(request: Request, alvo_id: int):
    """Resumo enxuto do lead pro balão do funil — só o que ajuda a decidir a
    próxima ação (contato, valor, situação, últimas atividades). Edição de
    cadastro, IA de primeiro contato, decisor e orçamento ficam só na ficha
    completa, atrás do link 'Ver ficha completa'."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    canais_contato = _canais_contato_lead(pool, alvo_id)
    atividades = _timeline_lead(pool, alvo_id, limite=2)
    temp_pill = TEMP_PILL.get(alvo["temperatura"]) or ("", "")
    return JSONResponse({
        "ok": True,
        "empresa": alvo["empresa"], "temperatura": alvo["temperatura"],
        "temp_cor": TEMP_COR.get(alvo["temperatura"]), "temp_pill": list(temp_pill),
        "segmento": alvo["segmento"], "cidade": alvo["cidade"], "uf": alvo["uf"],
        "vendedor_nome": alvo["vendedor_nome"], "status": alvo["status"],
        "contato": alvo["contato"], "cargo": alvo["cargo"],
        "telefone": alvo["telefone"], "whatsapp": alvo["whatsapp"], "email": alvo["email"],
        "instagram": alvo["instagram"], "site_url": alvo["site_url"], "site_dominio": alvo["site_dominio"],
        "obs": alvo["obs"], "valor_fmt": brl(alvo["valor"]) if alvo["valor"] else "",
        "valor_edit": (f"{alvo['valor'] / 100:.2f}".replace(".", ",")) if alvo["valor"] else "",
        "doc_fmt": alvo["doc_fmt"], "doc_rot": alvo["doc_rot"],
        "tel_link": alvo["tel_link"], "zap_link": alvo["zap_link"],
        "insta_url": alvo["insta_url"], "maps_url": alvo["maps_url"],
        "canais_contato": [{"ic": x["ic"], "label": x["label"], "respondeu": x["respondeu"]}
                            for x in canais_contato],
        "atividades": [{"tipo_rot": x["tipo_rot"], "resultado_rot": x["resultado_rot"],
                         "descricao": x["descricao"], "cor": x["cor"],
                         "quando": x["criado_em"].strftime("%d/%m %H:%M") if x["criado_em"] else ""}
                        for x in atividades],
    })


@router.post("/painel/prospeccao/{alvo_id}/editar-rapido")
def prospeccao_editar_rapido(request: Request, alvo_id: int, contato: str = Form(""),
                             cargo: str = Form(""), telefone: str = Form(""), whatsapp: str = Form(""),
                             email: str = Form(""), instagram: str = Form(""),
                             site_url: str = Form(""), valor: str = Form(""), obs: str = Form("")):
    """Edição rápida pelo balão do funil — só os campos de contato/valor, sem
    tocar documento/tipo/segmento/cidade/uf/sócio/regime/porte (esses ficam só
    na ficha completa, que tem a verificação própria de CNPJ/CPF). Por isso é
    uma rota PRÓPRIA e não reaproveita `/editar`: aquela faz um UPDATE de TODOS
    os campos de uma vez — chamá-la só com o subconjunto do balão apagaria os
    campos que o balão nem mostra."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    site_link = (site_url or "").strip()
    if site_link and "://" not in site_link:
        site_link = "https://" + site_link
    with pool.connection() as c:
        c.execute(
            """update prospeccao set contato=%s, cargo=%s, telefone=%s, whatsapp=%s,
                   email=%s, instagram=%s, site_url=%s, valor_estimado_centavos=%s,
                   obs=%s, atualizado_em=now()
                 where id=%s and conta_id=%s""",
            (contato.strip() or None, cargo.strip() or None, telefone.strip() or None,
             whatsapp.strip() or None, email.strip().lower() or None, instagram.strip() or None,
             site_link or None, _reais_para_centavos(valor), obs.strip() or None,
             alvo_id, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True})


@router.get("/painel/prospeccao/{alvo_id}", response_class=HTMLResponse)
def prospeccao_ficha(request: Request, alvo_id: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    timeline = _timeline_lead(pool, alvo_id)
    canais_contato = _canais_contato_lead(pool, alvo_id)
    # quem o lead usou pra falar (respondeu) primeiro é o [0] depois de ordenado —
    # é o mesmo item que vira "entrou por X" no topo do histórico.
    origem_ch = None
    if canais_contato and canais_contato[0]["respondeu"]:
        ch0 = canais_contato[0]
        origem_ch = {"ic": ch0["ic"], "label": ch0["label"], "em": ch0["primeiro_in"]}
    vends = _vendedores(pool, ctx["conta_id"]) if ctx["gerencia"] else []
    with pool.connection() as c:
        status_ficha = [(e["chave"], e["rotulo"]) for e in _etapas(c, ctx["conta_id"])]
        # o mesmo número atendido pelo outro chip. O aviso está no card do funil, mas o
        # card some assim que o vendedor abre a ficha (é a ficha que abre na gaveta) —
        # e é aqui, com o telefone na mão pra ligar, que saber disso muda o que ele faz.
        gemeo = _gemeos_de_outro_chip(c, ctx["conta_id"], [alvo_id]).get(alvo_id)
    return _render("prospeccao_ficha", request, titulo=alvo["empresa"], secao_ativa="prospeccao",
                   gemeo=gemeo, gemeo_aviso=_aviso_gemeo(gemeo),
                   gemeo_abre=_gemeo_abre(gemeo, ctx),
                   canais_contato=canais_contato, origem_ch=origem_ch,
                   a=alvo, timeline=timeline, status=status_ficha, temperaturas=TEMPERATURAS,
                   tipos=TIPOS, resultados=RESULTADOS, temp_cor=TEMP_COR, temp_pill=TEMP_PILL,
                   gerencia=ctx["gerencia"], pode_atribuir=ctx["pode_atribuir"], vendedores=vends,
                   tem_cnpja=fontes.tem_chave_cnpja(), tem_ia=_tem_ia(),
                   tem_credify=_tem_credify(),
                   wa_template=_prospec_convite.template_configurado(),
                   wa_numeros=_numeros_candidatos(alvo),
                   embed=request.query_params.get("embed") == "1",
                   aviso=request.session.pop("prosp_aviso", None))


@router.post("/painel/prospeccao/{alvo_id}/editar")
def prospeccao_editar(request: Request, alvo_id: int, contato: str = Form(""),
                      cargo: str = Form(""), telefone: str = Form(""), whatsapp: str = Form(""),
                      email: str = Form(""), cnpj: str = Form(""), cpf: str = Form(""),
                      documento: str = Form(""), tipo: str = Form(""), segmento: str = Form(""),
                      cidade: str = Form(""), uf: str = Form(""), valor: str = Form(""),
                      socio: str = Form(""), regime_tributario: str = Form(""),
                      porte: str = Form(""), instagram: str = Form(""),
                      empresa: str = Form(""),
                      tem_site: str = Form(""), site_url: str = Form(""), obs: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    tipo_lead, cnpj_limpo, cpf_limpo, erro_doc = _doc_lead(tipo, cnpj, cpf, documento)
    if erro_doc:
        request.session["prosp_aviso"] = erro_doc
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    if tipo_lead == "pf":
        socio = regime_tributario = porte = ""     # não existe quadro societário de pessoa
    # o nome é editável aqui porque em PF ele É o lead (não dá pra corrigir "Joana
    # Ribeito" só pela captação); vazio mantém o que está lá — nunca apaga.
    nome_novo = (empresa or "").strip() or alvo["empresa"]
    site_link = (site_url or "").strip()
    if site_link and "://" not in site_link:
        site_link = "https://" + site_link
    site_link = site_link or None
    # se preencheu o link, o lead tem site (mesmo que não tenha marcado o rádio)
    site = True if (tem_site == "1" or site_link) else False if tem_site == "0" else None
    try:
        with pool.connection() as c:
            c.execute(
                """update prospeccao set contato=%s, cargo=%s, telefone=%s, whatsapp=%s,
                       email=%s, cnpj=%s, cpf=%s, tipo=%s, empresa=%s, segmento=%s, cidade=%s, uf=%s,
                       valor_estimado_centavos=%s, socio=%s, regime_tributario=%s, porte=%s,
                       instagram=%s, tem_site=%s, site_url=%s, obs=%s, atualizado_em=now()
                     where id=%s and conta_id=%s""",
                (contato.strip() or None, cargo.strip() or None, telefone.strip() or None,
                 whatsapp.strip() or None, email.strip().lower() or None, cnpj_limpo, cpf_limpo,
                 tipo_lead, nome_novo, segmento.strip() or None, cidade.strip() or None,
                 (uf or "").strip()[:2].upper() or None,
                 _reais_para_centavos(valor), socio.strip() or None, regime_tributario.strip() or None,
                 porte.strip() or None, instagram.strip() or None, site, site_link, obs.strip() or None,
                 alvo_id, ctx["conta_id"]))
            c.commit()
    except UniqueViolation:
        doc, campo = (cpf_limpo, "cpf") if cpf_limpo else (cnpj_limpo, "cnpj")
        info = _lead_duplicado_info(pool, ctx["conta_id"], doc, nome_novo, campo)
        request.session["prosp_aviso"] = info["msg"]
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    request.session["prosp_aviso"] = "Dados atualizados."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/enriquecer")
def prospeccao_enriquecer(request: Request, alvo_id: int):
    """Puxa sócio/regime/porte do CNPJ na BrasilAPI (grátis). Só preenche campos vazios."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    if not alvo["cnpj"]:
        request.session["prosp_aviso"] = "Sem CNPJ pra consultar. Preencha o CNPJ e tente de novo."
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    res = fontes.enriquecer_cnpj(alvo["cnpj"])
    if not res.get("ok"):
        erros = {"cnpj_invalido": "CNPJ inválido.", "rede": "Não consegui falar com a Receita agora.",
                 "http_404": "CNPJ não encontrado na base."}
        request.session["prosp_aviso"] = erros.get(res.get("erro"), "Não consegui enriquecer agora.")
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    d = res["dados"]
    # só preenche o que estiver vazio (não sobrescreve o que o vendedor já pôs);
    # o pacote rico (situação, abertura, capital, endereço, IE…) vai no jsonb receita.
    with pool.connection() as c:
        c.execute(
            """update prospeccao set
                   socio=coalesce(socio,%s), regime_tributario=coalesce(regime_tributario,%s),
                   porte=coalesce(porte,%s), telefone=coalesce(telefone,%s),
                   email=coalesce(email,%s), segmento=coalesce(segmento,%s),
                   cidade=coalesce(cidade,%s), uf=coalesce(uf,%s),
                   receita=%s::jsonb, atualizado_em=now()
                 where id=%s and conta_id=%s""",
            (d.get("socio"), d.get("regime_tributario"), d.get("porte"), d.get("telefone"),
             d.get("email"), d.get("segmento"), d.get("cidade"), d.get("uf"),
             json.dumps(_receita_extras(d)), alvo_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Dados da Receita preenchidos ✓"
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/buscar-cnpj")
def prospeccao_buscar_cnpj(request: Request, alvo_id: int):
    """Acha CNPJs pelo nome+cidade do lead (CNPJá). Devolve candidatos pra escolher."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    res = fontes.buscar_cnpj_por_nome(alvo["empresa"], alvo["cidade"] or "", alvo["uf"] or "")
    from urllib.parse import quote
    termo = " ".join(x for x in (alvo["empresa"], alvo["cidade"], "cnpj") if x)
    res["web"] = "https://www.google.com/search?q=" + quote(termo)
    return JSONResponse(res)


@router.post("/painel/prospeccao/{alvo_id}/aplicar-cnpj")
def prospeccao_aplicar_cnpj(request: Request, alvo_id: int, cnpj: str = Form(...)):
    """Grava o CNPJ escolhido e já enriquece (colunas + jsonb receita)."""
    ctx, redir = _acesso(request)
    ajax = request.headers.get("X-Requested-With") == "fetch"
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401) if ajax else redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403) if ajax \
            else RedirectResponse("/painel/prospeccao", status_code=303)
    limpo = "".join(ch for ch in (cnpj or "") if ch.isdigit())
    if len(limpo) != 14:
        if ajax:
            return JSONResponse({"ok": False, "erro": "CNPJ inválido."})
        request.session["prosp_aviso"] = "CNPJ inválido."
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    res = fontes.enriquecer_cnpj(limpo)
    try:
        with pool.connection() as c:
            if res.get("ok"):
                d = res["dados"]
                # identidade (sócio/regime/porte/segmento) + receita: SOBRESCREVE — assim
                # "trocar CNPJ" corrige de fato. Contato/local: coalesce (Google costuma
                # ser o certo do lead).
                c.execute(
                    """update prospeccao set cnpj=%s,
                           socio=%s, regime_tributario=%s, porte=%s, segmento=%s,
                           telefone=coalesce(telefone,%s), email=coalesce(email,%s),
                           cidade=coalesce(cidade,%s), uf=coalesce(uf,%s),
                           receita=%s::jsonb, atualizado_em=now()
                         where id=%s and conta_id=%s""",
                    (limpo, d.get("socio"), d.get("regime_tributario"), d.get("porte"),
                     d.get("segmento"), d.get("telefone"), d.get("email"), d.get("cidade"),
                     d.get("uf"), json.dumps(_receita_extras(d)), alvo_id, ctx["conta_id"]))
                msg = "CNPJ vinculado e dados da Receita preenchidos ✓"
            else:
                c.execute("update prospeccao set cnpj=%s, atualizado_em=now() where id=%s and conta_id=%s",
                          (limpo, alvo_id, ctx["conta_id"]))
                msg = "CNPJ salvo (não consegui enriquecer agora — use ↻)."
            c.commit()
    except UniqueViolation:
        # Outro alvo da mesma conta já tem esse CNPJ (constraint uq_prospeccao_conta_cnpj).
        info = _lead_duplicado_info(pool, ctx["conta_id"], limpo, alvo["empresa"])
        msg = "Esse CNPJ já está em outro alvo: " + info["msg"]
        if ajax:
            return JSONResponse({"ok": False, "erro": msg})
        request.session["prosp_aviso"] = msg
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    if ajax:
        return JSONResponse({"ok": True, "msg": msg})
    request.session["prosp_aviso"] = msg
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/limpar-cnpj")
def prospeccao_limpar_cnpj(request: Request, alvo_id: int):
    """Desfaz um CNPJ escolhido errado: zera cnpj + receita + identidade (sócio/
    regime/porte). Contato/telefone/cidade/uf ficam (dados do lead)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    with pool.connection() as c:
        c.execute(
            """update prospeccao set cnpj=null, receita=null, socio=null,
                   regime_tributario=null, porte=null, atualizado_em=now()
                 where id=%s and conta_id=%s""", (alvo_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "CNPJ removido. Busque de novo se quiser."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/contato")
def prospeccao_contato(request: Request, alvo_id: int, tipo: str = Form(...),
                       resultado: str = Form(""), descricao: str = Form(""),
                       proximo: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    if tipo not in TIPO_OK:
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    res = resultado if resultado in RESULTADO_OK else None
    prox = _data_para_ts(proximo)
    with pool.connection() as c:
        c.execute(
            """insert into prospeccao_atividades (prospeccao_id, membro_id, tipo,
                 resultado, descricao, agendado_para) values (%s,%s,%s,%s,%s,%s)""",
            (alvo_id, ctx["membro_id"], tipo, res, (descricao or "").strip(), prox))
        novo_status = "contatado" if alvo["status"] == "novo" else alvo["status"]
        c.execute(
            """update prospeccao set ultimo_contato_em=now(),
                   proximo_contato_em=coalesce(%s, proximo_contato_em), status=%s,
                   atualizado_em=now() where id=%s and conta_id=%s""",
            (prox, novo_status, alvo_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Contato registrado."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/temperatura")
def prospeccao_temperatura(request: Request, alvo_id: int, temperatura: str = Form(...)):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if temperatura not in TEMP_OK:
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    with pool.connection() as c:
        c.execute("update prospeccao set temperatura=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (temperatura, alvo_id, ctx["conta_id"]))
        c.commit()
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/atribuir")
def prospeccao_atribuir(request: Request, alvo_id: int, vendedor_id: str = Form("")):
    """Atende a ficha (form + redirect) e o chat da Comunicação (fetch + JSON). Uma
    rota só: o escopo e a regra de quem pode atribuir são os mesmos nos dois."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    ajax = _eh_ajax(request)
    if not ctx["pode_atribuir"]:
        if ajax:
            return JSONResponse({"ok": False, "erro": "Só o dono atribui."}, status_code=403)
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    vend = _vendedor_destino(ctx, vendedor_id, get_pool(), ctx["conta_id"])
    with get_pool().connection() as c:
        c.execute("update prospeccao set vendedor_id=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (vend, alvo_id, ctx["conta_id"]))
        # a conversa acompanha o lead: sem isso o inbox segue dizendo "sem
        # responsável" (ou o nome antigo) pra quem acabou de ser trocado.
        c.execute("""update conversas set responsavel_membro_id=%s
                      where conta_id=%s and prospeccao_id=%s""",
                  (vend, ctx["conta_id"], alvo_id))
        c.commit()
    if ajax:
        return JSONResponse({"ok": True, "vendedor_id": vend,
                             "vendedor": _nome_vendedor(get_pool(), ctx["conta_id"], vend) or ""})
    request.session["prosp_aviso"] = "Alvo atribuído." if vend else "Alvo sem responsável."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


# ---------------------------------------------------------------- gerar orçamento (Etapa 4)
@router.post("/painel/prospeccao/{alvo_id}/orcamento")
def prospeccao_orcamento(request: Request, alvo_id: int):
    """Converte o lead num orçamento (módulo Serviços) e leva o vendedor pra editar.
    Reaproveita a tabela orcamentos: cliente/empresa são texto, não precisa cadastrar
    cliente antes. Grava o vínculo em prospeccao.orcamento_id e avança pra 'proposta'."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    if not ctx["conta"][14]:  # vende_servico — orçamento é do módulo Serviços
        request.session["prosp_aviso"] = "Orçamento é do módulo Serviços — ative Serviços na empresa pra usar."
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    oid = alvo.get("orcamento_id")
    if not oid:
        from web.painel_servicos import _garantir_tabela
        from finance import vendas
        criador = str(ctx["membro_id"]) if ctx["membro_id"] else "dono"
        with pool.connection() as c:
            _garantir_tabela(c)
            row = c.execute(
                """insert into orcamentos (conta_id, cliente, empresa, cnpj, segmento,
                     whatsapp, email, telefone, cidade, uf, site, cargo, socio,
                     criado_por, token, status, modo)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'rascunho',%s) returning id""",
                (ctx["conta_id"], alvo["contato"], alvo["empresa"], alvo["cnpj"], alvo["segmento"],
                 alvo["whatsapp"] or alvo["telefone"], alvo["email"], alvo["telefone"],
                 alvo["cidade"], alvo["uf"], alvo["site_url"], alvo["cargo"], alvo["socio"],
                 criador, secrets.token_urlsafe(16),
                 vendas.modo_do_orcamento(pool, ctx["conta_id"]))).fetchone()
            oid = row[0]
            novo_status = alvo["status"] if alvo["status"] in ("ganho", "perdido") else "proposta"
            c.execute("update prospeccao set orcamento_id=%s, status=%s, atualizado_em=now() "
                      "where id=%s and conta_id=%s", (oid, novo_status, alvo_id, ctx["conta_id"]))
            try:
                from finance import funil_regua as _fr
                with c.transaction():    # savepoint: registrar não pode derrubar o orçamento
                    _fr.registrar_movimento(c, ctx["conta_id"], alvo_id, alvo["status"], novo_status,
                                            "orcamento", ctx["membro_id"])
            except Exception:  # noqa: BLE001
                pass
            c.commit()
    return RedirectResponse(f"/painel/servicos?abrir={oid}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/status")
async def prospeccao_status(request: Request, alvo_id: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    form = await request.form()
    status = (form.get("status") or request.query_params.get("status") or "").strip()
    pool = get_pool()
    with pool.connection() as c:
        chaves = {e["chave"] for e in _etapas(c, ctx["conta_id"])}
    if status not in chaves:
        return JSONResponse({"ok": False, "erro": "status"}, status_code=400)
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    # Mudar a fase no funil implica que é um lead sendo trabalhado: se ainda estava
    # na base, promove pro funil (estagio='lead') mantendo a fase escolhida — senão
    # ele sumiria (base não aparece no funil). Quem já é lead só troca de coluna.
    with pool.connection() as c:
        c.execute("""update prospeccao set status=%s, estagio='lead', atualizado_em=now()
                       where id=%s and conta_id=%s""",
                  (status, alvo_id, ctx["conta_id"]))
        # O histórico é o que o banco nunca teve: sem ele ninguém consegue dizer
        # quanto um lead ficou em cada coluna. Grava desde já, com a régua toda
        # desligada — anotar o que a pessoa acabou de fazer não é automação. E é
        # esta linha 'manual' que a trava 3 da régua respeita depois.
        # SAVEPOINT, não enfeite: sem ele um erro no registro aborta a transação e
        # o commit abaixo vira ROLLBACK calado — o card volta pra coluna antiga e a
        # tela diz que deu certo. Mesmo motivo do savepoint da distribuição.
        try:
            from finance import funil_regua as _fr
            with c.transaction():
                _fr.registrar_movimento(c, ctx["conta_id"], alvo_id, alvo["status"], status,
                                        "manual", ctx["membro_id"])
        except Exception:  # noqa: BLE001 — histórico é acessório; mover o card é o pedido
            import logging
            logging.getLogger("funil.historico").warning(
                "movimento do lead %s não registrado", alvo_id, exc_info=True)
        c.commit()
    return JSONResponse({"ok": True, "status": status, "estagio": "lead"})


# ---------------------------------------------------------------- etapas do funil (editar)
# Só o dono/gestor edita a estrutura do funil (é uma configuração da empresa). O vendedor
# usa o funil normalmente. 'novo'/'ganho'/'perdido' (fixa=true) só renomeiam.
@router.post("/painel/prospeccao/etapas/nova")
def prospeccao_etapa_nova(request: Request, rotulo: str = Form(""), fase: str = Form("venda")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "Só o dono/gestor edita as etapas do funil."
        return RedirectResponse("/painel/prospeccao", status_code=303)
    rot = (rotulo or "").strip()[:40] or "Nova etapa"
    fase = fase if fase in ("venda", "pos") else "venda"   # 'fechamento' é só das fixas
    with get_pool().connection() as c:
        _etapas(c, ctx["conta_id"])  # garante o seed antes de mexer
        existentes = {r[0] for r in c.execute(
            "select chave from funil_etapas where conta_id=%s", (ctx["conta_id"],)).fetchall()}
        base = _slug_modelo(rot).replace("-", "_") or "etapa"
        chave, i = base, 2
        while chave in existentes:
            chave, i = f"{base}_{i}", i + 1
        mx = c.execute("select coalesce(max(ordem),0) from funil_etapas where conta_id=%s and ordem<%s",
                       (ctx["conta_id"], _ORDEM_GANHO)).fetchone()[0]
        ordem = min(mx + 10, _ORDEM_GANHO - 1)
        # Etapa de PÓS-VENDA entra depois do fechamento — o que era impossível até a
        # migração 177. Numa empresa de eventos o evento acontece DEPOIS de o sinal
        # ser pago, e prender essa coluna no meio da venda é errado na origem. Quem
        # continua contando como venda ganha é a `fase`, não a ordem (chaves_fechadas).
        if fase == "pos":
            mxp = c.execute("select coalesce(max(ordem),%s) from funil_etapas where conta_id=%s",
                            (_ORDEM_PERDIDO, ctx["conta_id"])).fetchone()[0]
            ordem = max(mxp + 10, _ORDEM_PERDIDO + 10)
        c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa, fase)
                     values (%s,%s,%s,%s,false,%s)""", (ctx["conta_id"], chave, rot, ordem, fase))
        c.commit()
    request.session["prosp_aviso"] = f"Etapa “{rot}” adicionada ✓"
    return RedirectResponse("/painel/prospeccao", status_code=303)


@router.post("/painel/prospeccao/etapas/{eid}/renomear")
def prospeccao_etapa_renomear(request: Request, eid: int, rotulo: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse("/painel/prospeccao", status_code=303)
    rot = (rotulo or "").strip()[:40]
    if rot:
        with get_pool().connection() as c:
            c.execute("update funil_etapas set rotulo=%s where id=%s and conta_id=%s",
                      (rot, eid, ctx["conta_id"]))
            c.commit()
        request.session["prosp_aviso"] = "Etapa renomeada ✓"
    return RedirectResponse("/painel/prospeccao", status_code=303)


@router.post("/painel/prospeccao/etapas/{eid}/remover")
def prospeccao_etapa_remover(request: Request, eid: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse("/painel/prospeccao", status_code=303)
    with get_pool().connection() as c:
        r = c.execute("select chave, fixa from funil_etapas where id=%s and conta_id=%s",
                      (eid, ctx["conta_id"])).fetchone()
        if not r:
            request.session["prosp_aviso"] = "Etapa não encontrada."
        elif r[1]:
            request.session["prosp_aviso"] = "Essa etapa é fixa (entrada/resultado) — não dá pra remover."
        else:
            n = c.execute("select count(*) from prospeccao where conta_id=%s and status=%s",
                          (ctx["conta_id"], r[0])).fetchone()[0]
            if n:
                request.session["prosp_aviso"] = f"Tem {n} lead(s) nessa etapa — mova-os antes de remover."
            else:
                c.execute("delete from funil_etapas where id=%s and conta_id=%s", (eid, ctx["conta_id"]))
                c.commit()
                request.session["prosp_aviso"] = "Etapa removida ✓"
    return RedirectResponse("/painel/prospeccao", status_code=303)


@router.post("/painel/prospeccao/etapas/{eid}/mover")
def prospeccao_etapa_mover(request: Request, eid: int, dir: str = Form("")):
    """Troca a ordem com a etapa vizinha do miolo (◀/▶). Fixas não reordenam."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"] or dir not in ("esq", "dir"):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    with get_pool().connection() as c:
        meio = [e for e in _etapas(c, ctx["conta_id"]) if not e["fixa"]]
        idx = next((i for i, e in enumerate(meio) if e["id"] == eid), None)
        if idx is not None:
            j = idx - 1 if dir == "esq" else idx + 1
            if 0 <= j < len(meio):
                a, b = meio[idx], meio[j]
                c.execute("update funil_etapas set ordem=%s where id=%s and conta_id=%s",
                          (b["ordem"], a["id"], ctx["conta_id"]))
                c.execute("update funil_etapas set ordem=%s where id=%s and conta_id=%s",
                          (a["ordem"], b["id"], ctx["conta_id"]))
                c.commit()
    return RedirectResponse("/painel/prospeccao", status_code=303)


# ================================================================ 1º CONTATO (IA)
def _ctx_servicos(pool, conta_id: int) -> str:
    """Catálogo de serviços da empresa, pra a IA saber o que oferecer."""
    try:
        itens = scat.listar(pool, conta_id)
    except Exception:  # noqa: BLE001
        itens = []
    return "\n".join(f"- {s.get('nome','')}: {s.get('descricao','') or ''}".strip()
                     for s in (itens or [])[:20])


def _draft_ia(pool, conta, alvo: dict, canal: str, remetente: str) -> dict:
    """Gera o rascunho da 1ª abordagem via Claude. canal: 'email' | 'whatsapp'.
    Retorna {'assunto','corpo'} (email) ou {'texto'} (whatsapp), ou {'erro'}."""
    from core.brain import Brain
    empresa_nome = (conta[2] or "nossa empresa").strip()
    catalogo = _ctx_servicos(pool, conta[0])
    site = alvo.get("site_url") or ""
    lead = "\n".join(x for x in [
        f"Empresa: {alvo.get('empresa') or ''}",
        f"Contato: {alvo.get('contato') or ''}" if alvo.get("contato") else "",
        f"Segmento: {alvo.get('segmento') or ''}" if alvo.get("segmento") else "",
        f"Cidade: {alvo.get('cidade') or ''}{('/' + alvo['uf']) if alvo.get('uf') else ''}" if alvo.get("cidade") else "",
        f"Site: {site}" if site else "",
    ] if x)
    if canal == "whatsapp":
        formato = '{"texto":"..."}'
        instr = ("uma mensagem de WhatsApp de PRIMEIRO contato: bem curta (3 a 5 linhas), "
                 "informal e humana, 1 pergunta/CTA leve pra puxar conversa, sem links longos, "
                 "pode usar no máximo 1 emoji.")
    else:
        formato = '{"assunto":"...","corpo":"..."}'
        instr = ("um e-mail de PRIMEIRO contato: assunto curto e o corpo com 5 a 8 linhas, "
                 "tom consultivo (não spam), conecte com o segmento/realidade do lead, 1 CTA "
                 "(sugerir uma conversa rápida). Assine como " + remetente + " (" + empresa_nome + ").")
    system = ("Você é um SDR de pré-vendas experiente. Escreve abordagens de prospecção "
              "que soam humanas e relevantes, em português do Brasil. NUNCA invente dados "
              "(preço, números, promessas). Responda SEMPRE só com JSON válido, sem markdown.")
    prompt = (f"MINHA EMPRESA: {empresa_nome}\n"
              f"SERVIÇOS QUE OFEREÇO:\n{catalogo or '(catálogo não informado)'}\n\n"
              f"LEAD QUE VOU ABORDAR:\n{lead}\n\n"
              f"Tarefa: escreva {instr}\n"
              f"Responda APENAS com JSON: {formato}")
    try:
        resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": prompt}])
        txt = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", None) == "text").strip()
        txt = re.sub(r"^```json|^```|```$", "", txt).strip()
        return json.loads(txt)
    except Exception as e:  # noqa: BLE001
        return {"erro": str(e)[:120]}


def _conversa_id(c, conta_id, alvo_id, canal):
    """Acha (ou cria) a conversa daquele lead+canal e devolve o id — base do inbox omnichannel."""
    r = c.execute("select id from conversas where conta_id=%s and prospeccao_id=%s and canal=%s",
                  (conta_id, alvo_id, canal)).fetchone()
    if r:
        return r[0]
    r = c.execute(
        """insert into conversas (conta_id, prospeccao_id, canal, status, ultima_msg_em)
           values (%s,%s,%s,'aberta',now()) returning id""",
        (conta_id, alvo_id, canal)).fetchone()
    return r[0]


def _add_msg(c, conversa_id, canal, direcao, autor, texto, membro_id=None, provider_sid=None):
    """Grava uma mensagem numa conversa JÁ conhecida (não re-deriva por lead) e
    atualiza o topo. Use quando já se tem o conversa_id (ex: responder no inbox)."""
    c.execute(
        """insert into mensagens (conversa_id, canal, direcao, autor, membro_id, texto, provider_sid)
           values (%s,%s,%s,%s,%s,%s,%s)""",
        (conversa_id, canal, direcao, autor, membro_id, (texto or "")[:8000], provider_sid))
    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conversa_id,))
    return conversa_id


def _registrar_msg(c, conta_id, alvo_id, canal, direcao, autor, texto, membro_id=None, provider_sid=None):
    """Grava uma mensagem na conversa DO LEAD (cria a conversa se preciso). Pra
    conversa sem lead, use _add_msg com o conversa_id."""
    conv = _conversa_id(c, conta_id, alvo_id, canal)
    return _add_msg(c, conv, canal, direcao, autor, texto, membro_id, provider_sid)


def _reg_atividade(c, alvo_id, conta_id, membro_id, tipo, descricao, status_atual):
    """Registra a abordagem na timeline, no inbox (conversas/mensagens) e avança 'novo'→'contatado'."""
    c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo,
                   resultado, descricao) values (%s,%s,%s,%s,%s)""",
              (alvo_id, membro_id, tipo, None, (descricao or "")[:4000]))
    if tipo in ("email", "whatsapp"):
        _registrar_msg(c, conta_id, alvo_id, tipo, "out", "humano", descricao, membro_id)
    novo = "contatado" if status_atual == "novo" else status_atual
    c.execute("""update prospeccao set ultimo_contato_em=now(), status=%s,
                   atualizado_em=now() where id=%s and conta_id=%s""",
              (novo, alvo_id, conta_id))
    if novo != status_atual:
        try:
            from finance import funil_regua as _fr
            with c.transaction():        # savepoint: registrar não pode derrubar a atividade
                _fr.registrar_movimento(c, conta_id, alvo_id, status_atual, novo, "atividade", membro_id)
        except Exception:  # noqa: BLE001
            pass


@router.post("/painel/prospeccao/{alvo_id}/mensagem-ia")
def prospeccao_mensagem_ia(request: Request, alvo_id: int, canal: str = Form("email")):
    """Gera o rascunho da 1ª abordagem (e-mail ou WhatsApp) via IA — não envia."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not _tem_ia():
        return JSONResponse({"ok": False, "erro": "sem_ia"})
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    canal = "whatsapp" if canal == "whatsapp" else "email"
    nome_rem, _email_rem = _membro_contato(pool, ctx["conta_id"], ctx["membro_id"])
    d = _draft_ia(pool, ctx["conta"], alvo, canal, nome_rem or "Equipe")
    if d.get("erro"):
        return JSONResponse({"ok": False, "erro": d["erro"]})
    if canal == "whatsapp":
        texto = (d.get("texto") or "").strip()
        return JSONResponse({"ok": True, "canal": "whatsapp", "texto": texto,
                             "link": _zap_link_texto(alvo["whatsapp"] or alvo["telefone"], texto)})
    return JSONResponse({"ok": True, "canal": "email",
                         "assunto": (d.get("assunto") or "").strip(),
                         "corpo": (d.get("corpo") or "").strip()})


@router.post("/painel/prospeccao/{alvo_id}/enviar-email")
def prospeccao_enviar_email(request: Request, alvo_id: int, assunto: str = Form(...),
                            corpo: str = Form(...)):
    """Envia o e-mail (revisado pelo vendedor) pro lead e registra na timeline."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    destino = (alvo.get("email") or "").strip()
    if not destino:
        return JSONResponse({"ok": False, "erro": "sem_email"})
    assunto = (assunto or "").strip() or f"Contato · {ctx['conta'][2] or ''}".strip()
    corpo = (corpo or "").strip()
    if not corpo:
        return JSONResponse({"ok": False, "erro": "sem_corpo"})
    from finance import email_inbound as _ein
    remetente = _ein.remetente_conta(pool, ctx["conta_id"])
    if not remetente:
        return JSONResponse({"ok": False, "erro": "E-mail não configurado (configure a caixa da empresa na aba Canais)."})
    nome_rem, email_rem = _membro_contato(pool, ctx["conta_id"], ctx["membro_id"])
    html = "<div style=\"font-family:var(--body);font-size:15px;line-height:1.6;color:#222\">" \
           + "".join(f"<p style=\"margin:0 0 12px\">{_html_escape(par)}</p>"
                     for par in corpo.split("\n\n")) + "</div>"
    ok = _ein.enviar_conta(pool, ctx["conta_id"], destino, assunto, html, texto_alt=corpo,
                           reply_to=email_rem or None, from_nome=(ctx["conta"][2] or None))
    if not ok:
        return JSONResponse({"ok": False, "erro": "envio_falhou"})
    with pool.connection() as c:
        _reg_atividade(c, alvo_id, ctx["conta_id"], ctx["membro_id"], "email",
                       f"De {remetente} · Para {destino} · {assunto}\n\n{corpo}", alvo["status"])
        c.commit()
    return JSONResponse({"ok": True})


@router.post("/painel/prospeccao/{alvo_id}/excluir")
def prospeccao_excluir(request: Request, alvo_id: int):
    """Exclui um lead. A conversa/e-mail dele vira órfã (FK SET NULL) e continua no
    inbox — nada de e-mail perdido; só sai do funil."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    with pool.connection() as c:
        c.execute("delete from prospeccao where id=%s and conta_id=%s", (alvo_id, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- enriquecimento de canais

def _enriquecer_lead(pool, conta_id, alvo_id) -> dict:
    """Raspa o site do lead → e-mail/Instagram/WhatsApp; preenche só o que está vazio
    (não sobrescreve o que o usuário já tem) e marca email_ok/enriquecido_em."""
    from finance import enriquecimento as enr
    with pool.connection() as c:
        r = c.execute(
            "select site_url, telefone, email, whatsapp, instagram, cnpj from prospeccao where id=%s and conta_id=%s",
            (alvo_id, conta_id)).fetchone()
        if not r:
            return {"ok": False}
        site, tel, email_at, wa_at, ig_at, cnpj_at = r
        f = enr.enriquecer(site or "", tel or "", email_at or "")
        novo_email = email_at or f["email"]
        novo_ig = ig_at or f["instagram"]
        novo_wa = wa_at or f["whatsapp"]
        novo_cnpj = (cnpj_at or "").strip() or (f.get("cnpj") or "")
        c.execute(
            """update prospeccao set email=%s, instagram=%s, whatsapp=%s, email_ok=%s,
                 cnpj=coalesce(nullif(%s,''), cnpj), enriquecido_em=now(), atualizado_em=now()
               where id=%s and conta_id=%s""",
            (novo_email, novo_ig, novo_wa, f["email_ok"], novo_cnpj, alvo_id, conta_id))
        c.commit()
    return {"ok": True, "email": novo_email, "email_ok": bool(f["email_ok"]),
            "instagram": novo_ig, "whatsapp": novo_wa, "cnpj": novo_cnpj or None}


def _enriquecer_lote_bg(pool, conta_id, ids):
    for aid in ids:
        try:
            _enriquecer_lead(pool, conta_id, aid)
        except Exception:  # noqa: BLE001
            pass


def _enriquecer_decisor_um(pool, conta_id, aid) -> bool:
    """Acha o decisor de UM lead em CASCATA (CNPJ → sócio; senão telefone → titular)
    e salva. Devolve True se achou. Pula quem já tem decisor (não paga de novo)."""
    from finance import credify as cf
    with pool.connection() as c:
        row = c.execute(
            "select cnpj, decisor_em, whatsapp, telefone from prospeccao where id=%s and conta_id=%s",
            (aid, conta_id)).fetchone()
    if not row or row[1] is not None:
        return False
    cnpj, _dec, wa, tel = row
    r = cf.decisor_por_lead(cnpj or "", [t for t in (wa, tel) if t])
    nome = r.get("decisor_nome")
    if not nome:
        return False
    tels = r.get("telefones") or []
    with pool.connection() as c:
        c.execute(
            """update prospeccao set decisor_nome=%s, decisor_cargo=%s, decisor_telefone=%s,
                 decisor_whatsapp=%s, decisor_telefones=%s::jsonb, decisor_em=now(), atualizado_em=now()
               where id=%s and conta_id=%s""",
            (nome, r.get("decisor_qualificacao"), r.get("decisor_telefone"),
             bool(r.get("decisor_whatsapp")), json.dumps(tels), aid, conta_id))
        c.commit()
    return True


def _buscar_aplicar_cnpj_um(pool, conta_id, alvo_id, nome, cidade, uf):
    """Acha o CNPJ do lead por nome+cidade (CNPJá) e aplica DE UMA VEZ só quando o
    resultado é inequívoco (1 candidato só — já vem filtrado por UF/cidade). Devolve
    (status, itens): status 'achou' | 'ambiguo' (2+ candidatos, itens tem os candidatos
    pra escolher) | 'sem' (nenhum, itens vazio)."""
    res = fontes.buscar_cnpj_por_nome(nome or "", cidade or "", uf or "")
    itens = res.get("itens") or []
    if len(itens) != 1:
        return ("ambiguo", itens[:5]) if len(itens) > 1 else ("sem", [])
    cnpj = itens[0]["cnpj"]
    enr = fontes.enriquecer_cnpj(cnpj)
    with pool.connection() as c:
        if enr.get("ok"):
            d = enr["dados"]
            c.execute(
                """update prospeccao set cnpj=%s, socio=%s, regime_tributario=%s, porte=%s, segmento=%s,
                     telefone=coalesce(telefone,%s), email=coalesce(email,%s),
                     cidade=coalesce(cidade,%s), uf=coalesce(uf,%s), receita=%s::jsonb, atualizado_em=now()
                   where id=%s and conta_id=%s""",
                (cnpj, d.get("socio"), d.get("regime_tributario"), d.get("porte"), d.get("segmento"),
                 d.get("telefone"), d.get("email"), d.get("cidade"), d.get("uf"),
                 json.dumps(_receita_extras(d)), alvo_id, conta_id))
        else:
            c.execute("update prospeccao set cnpj=%s, atualizado_em=now() where id=%s and conta_id=%s",
                      (cnpj, alvo_id, conta_id))
        c.commit()
    return ("achou", [])


@router.post("/painel/prospeccao/base/qualificar")
async def prospeccao_base_enriquecer(request: Request):
    """Qualifica os leads MARCADOS na Base, SÍNCRONO e com resumo do que achou:
    - tipo='canais': raspa o site → e-mail/Instagram/WhatsApp (grátis).
    - tipo='cnpj': acha o CNPJ por nome+cidade na CNPJá (paga, só gestão) — só aplica
      quando o candidato é único; achando mais de um ou nenhum, devolve os dados
      (candidatos / link de busca na web) pro painel da Base resolver.
    - tipo='decisor': acha o dono via Credify pelo CNPJ (paga, só gestão, pula quem já tem).
    Lê o form manual (fetch/urlencoded com 'ids' repetido)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    form = await request.form()
    tipo = form.get("tipo", "canais")
    pids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    if not pids:
        return JSONResponse({"ok": False, "erro": "Marque ao menos um contato."})
    conta_id = ctx["conta_id"]
    pool = get_pool()
    CAP = 20                               # teto por rodada (não travar o request)
    resto = max(0, len(pids) - CAP)
    pids = pids[:CAP]

    if tipo == "cnpj":
        if not ctx["gere_campanha"]:
            return JSONResponse({"ok": False, "erro": "Peça ao dono pra liberar campanhas pra você."})
        if not fontes.tem_chave_cnpja():
            return JSONResponse({"ok": False, "erro": "Busca por nome precisa da CNPJá (CNPJA_TOKEN no "
                                 "Render). Sem ela só dá pra colar o CNPJ direto na Ficha do lead."})
        with pool.connection() as c:
            # ESCOPO: o vendedor liberado gasta consulta paga só nos leads DELE.
            # Sem isto ele marcaria a base inteira da empresa (tem "marcar todos"
            # no cabeçalho da tabela) e a fatura seria da conta. Mesma trava que o
            # caminho grátis já fazia lá embaixo.
            _esc = "" if ctx["gerencia"] else " and vendedor_id=%s"
            _ex = () if ctx["gerencia"] else (ctx["membro_id"],)
            sel = c.execute(
                "select id, empresa, cidade, uf, obs from prospeccao where conta_id=%s and id = any(%s)"
                " and coalesce(nullif(trim(empresa),''),'') <> '' and coalesce(cnpj,'')=''" + _esc,
                (conta_id, pids) + _ex).fetchall()
        _INICIO = time.monotonic()
        achou, ambiguo, sem = 0, 0, 0
        processados = 0
        ambiguos = []
        sem_leads = []
        for pid, nome, cidade_l, uf_l, obs_l in sel:
            if time.monotonic() - _INICIO > 45:
                break
            processados += 1
            # endereço do Maps (obs) é o que dá pra bater com o endereço de cada
            # candidato; sem obs, cai pra cidade/uf mesmo (igual a Ficha faz).
            endereco_lead = obs_l or " / ".join(x for x in (cidade_l, uf_l) if x)
            try:
                r, itens = _buscar_aplicar_cnpj_um(pool, conta_id, pid, nome, cidade_l, uf_l)
            except Exception:  # noqa: BLE001
                r, itens = "sem", []
            termo = " ".join(x for x in (nome, cidade_l, "cnpj") if x)
            web = "https://www.google.com/search?q=" + quote(termo)
            if r == "achou":
                achou += 1
            elif r == "ambiguo":
                ambiguo += 1
                ambiguos.append({"id": pid, "empresa": nome, "endereco": endereco_lead, "itens": itens,
                                 "web": web})
            else:
                sem += 1
                sem_leads.append({"id": pid, "empresa": nome, "endereco": endereco_lead, "web": web})
        resto += len(sel) - processados
        return JSONResponse({"ok": True, "tipo": "cnpj", "n": processados, "achou": achou,
                             "ambiguo": ambiguo, "sem": sem, "resto": resto, "ambiguos": ambiguos,
                             "sem_leads": sem_leads})

    if tipo == "decisor":
        if not ctx["gere_campanha"]:
            return JSONResponse({"ok": False, "erro": "Peça ao dono pra liberar campanhas pra você."})
        from finance import credify as cf
        if not cf.tem_credenciais():
            return JSONResponse({"ok": False, "erro": "Credify não configurada (CREDIFY_CLIENT_ID/SECRET no Render)."})
        with pool.connection() as c:
            # mesmo escopo do CNPJ acima: consulta paga só nos leads do vendedor
            _esc = "" if ctx["gerencia"] else " and vendedor_id=%s"
            _ex = () if ctx["gerencia"] else (ctx["membro_id"],)
            sel = [r[0] for r in c.execute(
                "select id from prospeccao where conta_id=%s and id = any(%s) and decisor_em is null"
                " and (length(regexp_replace(coalesce(cnpj,''),'\\D','','g'))=14"
                "      or coalesce(nullif(trim(whatsapp),''), nullif(trim(telefone),'')) is not null)" + _esc,
                (conta_id, pids) + _ex).fetchall()]
        achou = 0
        for pid in sel:
            try:
                if _enriquecer_decisor_um(pool, conta_id, pid):
                    achou += 1
            except Exception:  # noqa: BLE001
                pass
        return JSONResponse({"ok": True, "tipo": "decisor", "n": len(sel), "achou": achou,
                             "sem": len(sel) - achou, "sem_cnpj": len(pids) - len(sel), "resto": resto})

    # canais (grátis) — escopo do vendedor se não for gestão
    q = "select id from prospeccao where conta_id=%s and id = any(%s) and coalesce(site_url,'')<>''"
    params = [conta_id, pids]
    if not ctx["gerencia"]:
        q += " and vendedor_id=%s"
        params.append(ctx["membro_id"])
    with pool.connection() as c:
        sel = [r[0] for r in c.execute(q, tuple(params)).fetchall()]
    # orçamento de tempo: cada lead pode levar até ~18s (3 páginas × 6s de timeout) —
    # num lote de 20 isso passaria fácil de 5min e derrubaria o request no meio (é o
    # jeito mais provável de "quase nada voltou" num lote grande). Corta em ~45s e
    # devolve parcial honesto — sem isso, um site lento no meio do lote apagava o
    # resultado de todo mundo depois dele.
    _INICIO = time.monotonic()
    _ORCAMENTO_S = 45
    com_email, com_wa, com_cnpj, sem = 0, 0, 0, 0
    processados = 0
    for pid in sel:
        if time.monotonic() - _INICIO > _ORCAMENTO_S:
            break
        try:
            r = _enriquecer_lead(pool, conta_id, pid)
        except Exception:  # noqa: BLE001
            r = {}
        processados += 1
        e = bool(r.get("email") and r.get("email_ok"))
        w = bool(r.get("whatsapp"))
        com_email += 1 if e else 0
        com_wa += 1 if w else 0
        com_cnpj += 1 if r.get("cnpj") else 0
        sem += 0 if (e or w or r.get("cnpj")) else 1
    resto += len(sel) - processados
    return JSONResponse({"ok": True, "tipo": "canais", "n": processados, "com_email": com_email,
                         "com_wa": com_wa, "com_cnpj": com_cnpj, "sem": sem,
                         "sem_site": len(pids) - len(sel), "resto": resto})


@router.post("/painel/prospeccao/{alvo_id}/enriquecer-canais")
def prospeccao_enriquecer_canais(request: Request, alvo_id: int):
    """Verifica os canais de UM lead (raspa o site, valida e-mail)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    try:
        return JSONResponse(_enriquecer_lead(pool, ctx["conta_id"], alvo_id))
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": "Falha ao verificar canais."})


@router.post("/painel/prospeccao/{alvo_id}/decisor-credify")
def prospeccao_decisor_credify(request: Request, alvo_id: int):
    """Descobre o DECISOR (sócio-administrador) do lead via Credify, pelo CNPJ:
    nome + cargo (+ telefone/WhatsApp se a conta tiver a consulta liberada). Salva
    em colunas dedicadas do lead. Consulta PAGA + dado de pessoa (LGPD) — ação
    deliberada por lead, só dono/gestor ou o dono do lead."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    from finance import credify as cf
    if not cf.tem_credenciais():
        return JSONResponse({"ok": False, "erro": "Credify não configurada (CREDIFY_CLIENT_ID/SECRET no Render)."})
    fones = [t for t in (alvo.get("whatsapp"), alvo.get("telefone")) if t]
    if not ((alvo.get("cnpj") and len(_so_digitos(alvo["cnpj"])) == 14) or fones):
        return JSONResponse({"ok": False, "erro": "Preencha o CNPJ ou um telefone do lead primeiro."})
    try:
        r = cf.decisor_por_lead(alvo.get("cnpj") or "", fones)   # cascata: CNPJ → telefone
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": "Falha ao consultar a Credify."})
    nome = r.get("decisor_nome")
    if not nome:
        if r.get("permissao"):
            msg = "A consulta de Quadro Societário não está liberada na sua conta Credify."
        elif r.get("motivo"):
            msg = "Credify: " + str(r["motivo"]) + "."
        else:
            msg = "Não achei o dono — nem pelo CNPJ (quadro societário) nem pelo telefone (titular)."
        return JSONResponse({"ok": False, "erro": msg})
    tels = r.get("telefones") or []
    with pool.connection() as c:
        c.execute(
            """update prospeccao set decisor_nome=%s, decisor_cargo=%s, decisor_telefone=%s,
                 decisor_whatsapp=%s, decisor_telefones=%s::jsonb, decisor_em=now(), atualizado_em=now()
               where id=%s and conta_id=%s""",
            (nome, r.get("decisor_qualificacao"), r.get("decisor_telefone"),
             bool(r.get("decisor_whatsapp")), json.dumps(tels), alvo_id, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True, "nome": nome, "cargo": r.get("decisor_qualificacao"),
                         "telefone": r.get("decisor_telefone"), "origem": r.get("origem", "cnpj"),
                         "whatsapp": bool(r.get("decisor_whatsapp")),
                         "n_telefones": len(tels),
                         "sem_telefone": not tels})


@router.post("/painel/prospeccao/identificar-numero")
def prospeccao_identificar_numero(request: Request, numero: str = Form("")):
    """Telefone reverso (Credify 'PF Telefone'): número -> em que NOME está cadastrado.
    Consulta paga + dado de pessoa (LGPD) — ação deliberada. Não persiste o CPF."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    from finance import credify as cf
    if not cf.tem_credenciais():
        return JSONResponse({"ok": False, "erro": "Credify não configurada no ambiente."})
    dig = _so_digitos(numero)
    if len(dig) < 10:
        return JSONResponse({"ok": False, "erro": "Informe DDD + número (ex.: 86981885930)."})
    try:
        r = cf.titular_por_telefone("", dig)
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": "Falha ao consultar a Credify."})
    if not r.get("ok"):
        e = (r.get("erro") or "").lower()
        if "permiss" in e:
            msg = "A consulta de telefone reverso (PF Telefone) ainda não está liberada na sua conta Credify."
        elif "idconsulta" in e:
            msg = "Falta o IdConsulta da PF Telefone (CREDIFY_ID_TELREV) — avise o suporte."
        elif "não encontrado" in e or "sem_titular" in e:
            msg = "Nenhum titular encontrado pra esse número na base."
        else:
            msg = "Credify: " + (r.get("erro") or "não consegui")
        return JSONResponse({"ok": False, "erro": msg})
    def _mask(c):
        c = _so_digitos(c or "")
        return f"***.{c[3:6]}.***-{c[9:]}" if len(c) == 11 else ""
    def _cep(c):
        c = _so_digitos(c or "")
        return f"{c[:5]}-{c[5:]}" if len(c) == 8 else (c or "")
    def _endereco(t):
        # monta o endereço completo a partir das partes (fallback pro ENDERECO cru)
        rua = " ".join(x for x in [(t.get("tplogradouro") or "").strip(),
                                   (t.get("logradouro") or "").strip()] if x)
        p = [x for x in [rua, (t.get("numero") or "").strip(), (t.get("complemento") or "").strip()] if x]
        linha1 = ", ".join(p)
        return linha1 or (t.get("endereco") or "")
    tit, vistos = [], set()
    for t in (r.get("titulares") or []):
        nome = (t.get("nome") or "").strip()
        chave = (nome.upper(), _so_digitos(t.get("cpf")))
        if not nome or chave in vistos:      # dedup: mesmo titular repetido
            continue
        vistos.add(chave)
        tit.append({"nome": nome, "cpf_mask": _mask(t.get("cpf")),
                    "endereco": _endereco(t), "bairro": (t.get("bairro") or "").strip(),
                    "cidade": (t.get("cidade") or "").strip(), "uf": (t.get("uf") or "").strip(),
                    "cep": _cep(t.get("cep"))})
    return JSONResponse({"ok": True, "titulares": tit})


@router.post("/painel/prospeccao/enriquecer-lote")
def prospeccao_enriquecer_lote(request: Request, background_tasks: BackgroundTasks):
    """Verifica canais de vários leads (que têm site e ainda não foram verificados),
    em background. Devolve quantos entraram na fila."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    where = ["conta_id=%s", "coalesce(site_url,'')<>''", "enriquecido_em is null"]
    params = [ctx["conta_id"]]
    if not ctx["gerencia"]:
        where.append("vendedor_id=%s")
        params.append(ctx["membro_id"])
    with get_pool().connection() as c:
        ids = [r[0] for r in c.execute(
            f"select id from prospeccao where {' and '.join(where)} order by criado_em desc limit 80",
            tuple(params)).fetchall()]
    if ids:
        import threading
        threading.Thread(target=_enriquecer_lote_bg, args=(get_pool(), ctx["conta_id"], ids),
                         daemon=True).start()
    return JSONResponse({"ok": True, "n": len(ids)})


@router.post("/painel/prospeccao/{alvo_id}/convidar-zaq")
def prospeccao_convidar_zaq(request: Request, alvo_id: int):
    """Manda pro lead um e-mail convidando a CRIAR conta no Zaq (link pro /cadastro
    pré-preenchido). Reusa o cadastro público — sem token/senha novos."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    destino = (alvo.get("email") or "").strip()
    if not destino:
        return JSONResponse({"ok": False, "erro": "Lead sem e-mail — cadastre o e-mail antes."})
    if not remetente_configurado():
        return JSONResponse({"ok": False, "erro": "E-mail não configurado no ambiente."})
    from urllib.parse import urlencode
    from finance.email_sender import _app_url, enviar_convite_zaq
    q = urlencode({k: v for k, v in {
        "nome": alvo.get("empresa") or "",
        "email": destino,
        "whatsapp": alvo.get("whatsapp") or alvo.get("telefone") or "",
    }.items() if v})
    link = _app_url() + "/cadastro" + (("?" + q) if q else "")
    _nome_rem, email_rem = _membro_contato(pool, ctx["conta_id"], ctx["membro_id"])
    ok = enviar_convite_zaq(destino, alvo.get("empresa"), link,
                            from_nome=(ctx["conta"][2] or None), reply_to=email_rem or None)
    if not ok:
        return JSONResponse({"ok": False, "erro": "Não consegui enviar o convite (confira o SMTP)."})
    remetente = remetente_configurado() or ""
    with pool.connection() as c:
        _reg_atividade(c, alvo_id, ctx["conta_id"], ctx["membro_id"], "email",
                       f"De {remetente} · Para {destino} · Convite pro Zaq\n\n"
                       f"Convite pra criar conta no Zaq: {link}", alvo["status"])
        c.commit()
    return JSONResponse({"ok": True})


@router.post("/painel/prospeccao/{alvo_id}/registrar-whatsapp")
def prospeccao_registrar_whatsapp(request: Request, alvo_id: int, texto: str = Form("")):
    """Registra na timeline que o WhatsApp de 1º contato foi disparado (o envio é
    no app do vendedor via wa.me — aqui só marcamos o histórico)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    with pool.connection() as c:
        _reg_atividade(c, alvo_id, ctx["conta_id"], ctx["membro_id"], "whatsapp",
                       "WhatsApp de 1º contato" + (f"\n\n{texto.strip()}" if texto.strip() else ""),
                       alvo["status"])
        c.commit()
    return JSONResponse({"ok": True})


@router.post("/painel/prospeccao/{alvo_id}/enviar-whatsapp")
def prospeccao_enviar_whatsapp(request: Request, alvo_id: int, texto: str = Form(...),
                               numero: str = Form("")):
    """Envia o WhatsApp de 1º contato PELO CANAL da empresa (sem abrir wa.me) e
    registra na timeline. Retorna erro amigável se o canal não estiver pronto."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    texto = (texto or "").strip()
    if not texto:
        return JSONResponse({"ok": False, "erro": "sem_texto"})
    numero = (numero or alvo.get("whatsapp") or alvo.get("telefone") or "").strip()
    if not numero:
        return JSONResponse({"ok": False, "erro": "sem_numero"})
    from finance import whatsapp_out
    with pool.connection() as c:
        res = whatsapp_out.enviar(c, ctx["conta_id"], numero, texto)
        if not res.get("ok"):
            erros = {
                "nao_configurado": "WhatsApp não conectado — configure na aba Canais.",
                "sem_numero_empresa": "Configure o WhatsApp desta empresa na aba Canais.",
                "numero_invalido": "Número do lead inválido.",
                "qr_indisponivel": "A conexão por QR ainda não está ligada.",
                "desconectado": "O WhatsApp está reconectando (normal por ~1 minuto após uma "
                                "atualização do sistema). Espere alguns segundos e envie de novo — "
                                "NÃO clique em Desconectar nem escaneie QR.",
            }
            return JSONResponse({"ok": False, "erro": res.get("erro"),
                                 "msg": erros.get(res.get("erro"),
                                        "Não consegui enviar (janela de 24h fechada? 1º contato pode exigir template aprovado).")})
        _reg_atividade(c, alvo_id, ctx["conta_id"], ctx["membro_id"], "whatsapp",
                       f"WhatsApp de 1º contato (enviado pelo sistema)\n\n{texto}", alvo["status"])
        c.commit()
    return JSONResponse({"ok": True})


@router.post("/painel/prospeccao/{alvo_id}/enviar-convite-wa")
def prospeccao_enviar_convite_wa(request: Request, alvo_id: int, numero: str = Form("")):
    """Dispara o TEMPLATE aprovado de 1º contato (WhatsApp frio, fora da janela de
    24h) pelo número da empresa e registra na timeline."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    from finance import prospec_convite
    res = prospec_convite.enviar_convite(pool, ctx["conta_id"], alvo_id,
                                         numero=(numero or "").strip() or None)
    if not res.get("ok"):
        msgs = {"sem_template": "Template ainda não configurado (falta o SID aprovado no Twilio).",
                "sem_numero": "Lead sem número de WhatsApp/telefone.",
                "sem_numero_empresa": "Configure o WhatsApp desta empresa na aba Canais.",
                "provedor_sem_template": "Template só funciona no provedor Twilio.",
                "numero_invalido": "Número do lead inválido."}
        return JSONResponse({"ok": False, "erro": res.get("erro"),
                             "msg": msgs.get(res.get("erro"), "Não consegui enviar o convite.")})
    with pool.connection() as c:
        _reg_atividade(c, alvo_id, ctx["conta_id"], ctx["membro_id"], "whatsapp",
                       "Convite de 1º contato enviado (template aprovado, WhatsApp)", alvo["status"])
        # registra como mensagem de saída (aparece no inbox e recebe status de entrega)
        sid_msg = res.get("sid")
        if sid_msg:
            rem = _so_digitos(alvo.get("whatsapp") or alvo.get("telefone") or numero or "")
            conv = c.execute("""select id from conversas where conta_id=%s and canal='whatsapp'
                                  and prospeccao_id=%s order by ultima_msg_em desc limit 1""",
                             (ctx["conta_id"], alvo_id)).fetchone()
            conv_id = conv[0] if conv else c.execute(
                """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, status, agente_ativo)
                   values (%s,%s,'whatsapp',%s,'aberta',false) returning id""",
                (ctx["conta_id"], alvo_id, rem or None)).fetchone()[0]
            c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid, status)
                         values (%s,'whatsapp','out','bot','📨 Convite de 1º contato (template aprovado)',%s,'enviado')""",
                      (conv_id, sid_msg))
            c.execute("update conversas set ultima_msg_em=now() where id=%s", (conv_id,))
        c.commit()
    return JSONResponse({"ok": True})


def _html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


# ---------------------------------------------------------------- helpers locais
def _pack(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode()


def _unpack(s: str):
    try:
        return json.loads(base64.urlsafe_b64decode((s or "").encode()).decode())
    except Exception:  # noqa: BLE001
        return None


def _reais_para_centavos(v: str) -> int:
    s = (v or "").strip()
    if not s:
        return 0
    s = s.replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except (ValueError, TypeError):
        return 0


def _data_para_ts(v: str):
    s = (v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ================================================================ CSS + TEMPLATES
_CSS = """<style>
.pw{width:100%;max-width:1240px;margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.pw h2.tt{margin:0;font-size:1.35rem}
.pbtn{width:auto;margin:0;padding:.5rem .9rem;border-radius:9px;font-size:.86rem;font-weight:600;
  background:var(--verde);color:var(--sobre-verde);border:0;cursor:pointer;display:inline-flex;align-items:center;gap:.4rem;text-decoration:none}
.pbtn:hover{background:var(--verde-hover)}
.pbtn.ghost{background:transparent;color:var(--txt-mut);border:1px solid var(--borda)}
.pbtn.ghost:hover{color:var(--txt);border-color:var(--verde)}
.pbtn.novo{background:transparent;color:var(--verde-claro);border:1px solid var(--neon-borda)}
.pbtn.novo:hover{background:#132420}
.pbtn[disabled]{opacity:.45;cursor:not-allowed}
.tpill{display:inline-flex;align-items:center;padding:.12rem .55rem;border-radius:999px;font-size:.72rem;font-weight:600;line-height:1.4}
.spill{display:inline-flex;align-items:center;padding:.14rem .6rem;border-radius:999px;font-size:.74rem;
  background:var(--card-2);border:1px solid var(--borda);color:var(--txt)}
.tdot{width:11px;height:11px;border-radius:50%;flex-shrink:0;display:inline-block}
.fld{width:100%;padding:.55rem .7rem;border-radius:8px;border:1px solid #333;background:var(--bg);color:var(--txt);font-family:inherit;font-size:.9rem}
.lbl{display:block;color:var(--txt-mut);font-size:.72rem;margin-bottom:.15rem}
/* olhinho de "ver senha salva" — width/margin explícitos porque o CSS global do
   painel manda button{width:100%;margin-top:...} e ele esticaria a linha toda */
.olho{width:auto;margin:0;flex:none;padding:.5rem .6rem;border-radius:8px;cursor:pointer;
  background:var(--card-2);border:1px solid var(--borda);color:var(--txt);font-size:.95rem;line-height:1}
.olho:hover{border-color:var(--verde-claro)}
.egrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.55rem}
.egrid .full{grid-column:1/-1}
/* ---- kanban: mobile-first (abas), vira grid no desktop ---- */
.kbtabs{display:flex;gap:.3rem;overflow-x:auto;margin-top:.9rem;padding-bottom:.35rem;-webkit-overflow-scrolling:touch}
.kbtab{width:auto;margin:0;white-space:nowrap;padding:.4rem .7rem;border-radius:999px;font-size:.8rem;cursor:pointer;
  background:transparent;border:1px solid var(--borda);color:var(--txt-mut);display:inline-flex;align-items:center;gap:.35rem}
.kbtab.on{background:var(--verde);border-color:var(--verde);color:var(--sobre-verde);font-weight:600}
.kbtab .c{background:rgba(0,0,0,.25);border-radius:999px;padding:0 .4rem;font-size:.72rem}
.kbrow{display:block;margin-top:.5rem}
.kbcol{display:none;flex-direction:column;background:var(--bg);border:1px solid var(--borda);border-radius:14px;padding:.7rem;min-height:220px}
.kbcol.show{display:flex}
.kbcol.dragover{border-color:var(--verde);box-shadow:0 0 0 1px var(--verde) inset}
.kbcol h4{margin:0 0 .55rem;font-size:.85rem;display:flex;align-items:center;justify-content:space-between}
.kbcnt{background:var(--card-2);border:1px solid var(--borda);border-radius:999px;padding:.02rem .5rem;font-size:.72rem;color:var(--txt-mut)}
.kbdrop{flex:1;display:flex;flex-direction:column;gap:.5rem}
.kbempty{border:1px dashed var(--borda);border-radius:10px;color:var(--txt-mut);font-size:.76rem;text-align:center;padding:1.1rem .5rem;opacity:.55}
.kbcard{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.6rem .65rem;cursor:pointer;transition:border-color .15s,transform .1s}
.kbcard:hover{border-color:var(--verde)}
.kbcard:active{transform:scale(.98)}
.kbcard .emp{font-size:.88rem;font-weight:600;line-height:1.2;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kbcard .sub{color:var(--txt-mut);font-size:.74rem;margin-top:.22rem}
.kbcard .ft{display:flex;align-items:center;justify-content:space-between;gap:.3rem;margin-top:.42rem;flex-wrap:wrap}
/* selo de campanha de origem — só aparece quando o lead veio de uma campanha
   (campanha_alvos) e/ou dá pra saber o chip que recebeu (mais comum: tráfego
   pago apontando direto pro número, sem passar por campanha_alvos nenhuma).
   O "📱 apelido" só entra quando a conta tem 2+ chips (senão "de qual chip"
   não diz nada a ninguém). box-sizing:border-box é obrigatório: sem ele,
   padding+border somam POR CIMA do max-width:100% (que já é a largura
   inteira do card), e o selo vaza pra fora — medido com Playwright: borda
   direita ~8px além da borda do card.
   Trunca em até 2 LINHAS (-webkit-line-clamp), não 1 com "…": um card de
   150px corta o texto tão cedo que "📣 Camp…" não identifica campanha
   nenhuma — a informação inteira do selo é justamente essa. */
.kbcard .camp{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;
  overflow:hidden;overflow-wrap:break-word;background:var(--neon-fundo);
  border:1px solid var(--neon-borda);border-radius:8px;padding:.22rem .5rem;
  font-size:.7rem;line-height:1.3;color:var(--verde-claro);margin-top:.34rem;
  max-width:100%;box-sizing:border-box}
/* nowrap só no NOME DO CHIP: sem isso, um apelido curto ("CP Zarb") ainda
   quebrava no meio ("CP" numa linha, "Zarb" na outra) — o clamp de 2 linhas
   do .camp é pro texto da campanha, que pode ser grande; o chip é sempre
   curto e não devia quebrar sozinho. */
.kbcard .camp .chip{opacity:.72;font-weight:600;white-space:nowrap}
@media(min-width:900px){
  .kbtabs{display:none}
  /* Uma coluna por ETAPA, e as etapas são configuráveis desde a régua: o
     repeat(6,...) fixo jogava a 7ª pra uma segunda linha no desktop — que é o que
     já acontece hoje em quem criou uma etapa a mais. auto-fit + minmax mantém a
     linha única e faz o quadro rolar quando não couber, como a barra de abas. */
  .kbrow{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(150px,1fr);
    gap:.55rem;overflow-x:auto;scrollbar-width:thin}
  /* esfumado na borda direita quando tem coluna cortada — sem isso a última
     coluna (ex.: "Perdido") só some na borda da tela, sem nenhum aviso de que
     dá pra arrastar pro lado; .transborda é ligado/desligado por JS
     (kbCheckScroll) só quando sobra conteúdo além do que já apareceu. */
  .kbrow.transborda{mask-image:linear-gradient(to right,#000 calc(100% - 40px),transparent 100%);
    -webkit-mask-image:linear-gradient(to right,#000 calc(100% - 40px),transparent 100%)}
  .kbcol{display:flex !important;min-height:180px}
}
/* ---- ficha ---- */
.fgrid{display:grid;grid-template-columns:1.05fr 1fr;gap:1rem;margin-top:1rem;align-items:start}
@media(max-width:820px){.fgrid{grid-template-columns:1fr}}
.fsec{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:1rem 1.1rem}
.fsec+.fsec{margin-top:1rem}
.fsec .sh{display:flex;align-items:center;justify-content:space-between;margin-bottom:.5rem;gap:.5rem}
.fsec .sh b{font-size:.9rem}
.drow{display:grid;grid-template-columns:1.4rem 96px 1fr;gap:.55rem;align-items:baseline;padding:.5rem 0;border-top:1px solid var(--borda);font-size:.86rem}
.drow:first-of-type{border-top:0}
.drow .ic{color:var(--txt-mut);text-align:center}
.drow .lb{color:var(--txt-mut);font-size:.78rem}
.drow>span:last-child{min-width:0;overflow-wrap:anywhere}
.badge{display:inline-block;padding:.02rem .4rem;border-radius:6px;font-size:.68rem;font-weight:600;background:#123a26;color:#5fe0a5;border:1px solid #1d5c3c;margin-left:.35rem}
.tl{position:relative;padding:.15rem 0 .8rem 1rem;border-left:2px solid var(--borda);margin-left:.25rem}
.tl:last-child{padding-bottom:.1rem}
.tl .dt{position:absolute;left:-7px;top:.28rem;width:12px;height:12px;border-radius:50%;border:2px solid var(--card)}
.rcpills{display:flex;flex-wrap:wrap;gap:.4rem;margin:.2rem 0 .6rem}
.rcpill{width:auto;margin:0;padding:.35rem .75rem;border-radius:999px;font-size:.8rem;cursor:pointer;background:transparent;border:1px solid var(--borda);color:var(--txt-mut)}
.rcpill.on{background:var(--verde);border-color:var(--verde);color:var(--sobre-verde);font-weight:600}
.chipin{display:inline-flex;align-items:center;gap:.4rem;border:1px solid var(--borda);border-radius:999px;padding:.3rem .7rem;color:var(--txt-mut);font-size:.8rem;background:var(--bg)}
.chipin input{border:0;background:transparent;color:var(--txt);padding:0;width:auto;font-size:.82rem}
/* ---- captação ---- */
.cabas{display:flex;gap:.3rem;background:var(--bg);border:1px solid var(--borda);border-radius:11px;padding:4px;margin:.2rem 0 1rem}
.caba{width:auto;margin:0;flex:1;text-align:center;padding:.5rem .6rem;border-radius:8px;font-size:.85rem;cursor:pointer;background:transparent;border:0;color:var(--txt-mut);text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:.35rem}
.caba.on{background:var(--verde);color:var(--sobre-verde);font-weight:600}
.rlist{border:1px solid var(--borda);border-radius:12px;overflow:hidden;margin-top:.5rem}
.rrow{display:flex;align-items:center;gap:.7rem;padding:.6rem .8rem;border-top:1px solid var(--borda)}
.rrow:first-child{border-top:0}
.dupb{font-size:.68rem;font-weight:700;padding:.05rem .45rem;border-radius:999px;color:var(--ambar);border:1px solid var(--ambar-borda);background:#2a2113;white-space:nowrap}
.rrow input[type=checkbox]{width:auto;margin:0;flex-shrink:0;width:18px;height:18px;accent-color:var(--verde)}
.toggle{position:relative;width:44px;height:24px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0;position:absolute}
.tgl{position:absolute;inset:0;background:#333;border-radius:999px;transition:.2s;cursor:pointer}
.tgl:before{content:'';position:absolute;left:3px;top:3px;width:18px;height:18px;background:#fff;border-radius:50%;transition:.2s}
.toggle input:checked+.tgl{background:var(--verde)}
.toggle input:checked+.tgl:before{transform:translateX(20px)}
/* ---- mesmo número, outro chip (aviso cruzado) ----
   Âmbar, e não vermelho: não é erro. Ter dois leads é o desenho — cada chip responde
   pelo seu número. O que o aviso pede é cuidado, não conserto. */
.gemeo-faixa{margin-top:.8rem;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;
  font-size:.84rem;line-height:1.4;color:var(--ambar);background:#2a2113;
  border:1px solid var(--ambar-borda);border-radius:10px;padding:.55rem .75rem}
.gemeo-faixa a{color:var(--ambar);text-decoration:underline;white-space:nowrap}
.gemeo-faixa .mut{color:var(--txt-mut)}
</style>"""

# ---- barra de navegação do módulo + agilidade no front (prefetch no hover + barra
# de progresso). O <script> entra no _CSS, então TODA tela de prospecção ganha a
# navegação instantânea sem recarregar sensação de lentidão; o back segue rendrizando.
_NAV_ASSETS = """<style>
/* NUNCA quebra linha. Era `flex-wrap:wrap`, e com 8 abas (996px medidos) a última
   descia pra segunda linha assim que o viewport caía abaixo de ~1030px — zoom de 125%
   numa tela de 1280, janela não maximizada, tablet. Rolagem lateral em vez de quebra:
   funciona em qualquer largura e a próxima aba que entrar não quebra nada de novo.

   DUAS CAIXAS, e não uma: as abas rolam dentro de `.pnav-rol`, e o ⚙️ fica FORA dela.
   Numa caixa só, duas coisas quebravam — o esfumado da borda direita apagaria o próprio
   ⚙️ (que mora encostado ali), e a engrenagem sairia de vista junto com as abas quando a
   pessoa rolasse. Configuração tem que estar sempre alcançável. */
.pnavbar{display:flex;gap:.4rem;flex-wrap:nowrap;align-items:center;margin:.2rem 0 1.1rem}
.pnav-rol{display:flex;gap:.4rem;flex-wrap:nowrap;align-items:center;min-width:0;
  overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch}
.pnav-rol::-webkit-scrollbar{display:none}
.pnav{flex:none}
.pnav{display:inline-flex;align-items:center;gap:.35rem;font:inherit;font-size:.84rem;font-weight:600;padding:.45rem .8rem;border-radius:9px;border:1px solid var(--borda);color:var(--txt);background:transparent;text-decoration:none;white-space:nowrap;cursor:pointer;line-height:1;box-sizing:border-box;width:auto;margin:0;-webkit-appearance:none;appearance:none;vertical-align:middle;height:auto}
.pnav:hover{border-color:var(--verde);color:#fff}
.pnav.on{color:var(--sobre-verde);background:var(--verde);border-color:var(--verde)}
/* Captar Lead: botão de AÇÃO no cabeçalho do Funil, não aba de navegação. Verde
   cheio porque é o call-to-action da tela — abrir o painel de captação. */
.cap-btn{display:inline-flex;align-items:center;gap:.4rem;font:inherit;font-size:.84rem;
  font-weight:700;padding:.5rem .9rem;border-radius:9px;border:0;cursor:pointer;
  background:var(--verde);color:var(--sobre-verde);white-space:nowrap;flex:none}
.cap-btn:hover{background:var(--verde2,var(--verde))}
/* o ⚙️ encostado à direita, separado das abas de trabalho e fora da rolagem */
.pnav.cfg{margin-left:auto;padding:.45rem .55rem;flex:none}
#pnavprog{position:fixed;top:0;left:0;height:3px;width:0;background:var(--verde);box-shadow:0 0 8px var(--verde);z-index:99999;transition:width .3s ease;opacity:0}
#pnavprog.go{opacity:1}
/* cercar área no mapa (Captar leads → Google Maps) — acordeão retraído por padrão */
.mapacc{border:1px solid var(--borda);border-radius:10px;margin:.6rem 0;overflow:hidden;background:var(--bg)}
.mapacc-hd{display:flex;align-items:center;gap:.55rem;padding:.6rem .75rem;cursor:pointer;user-select:none}
.mapacc-hd:hover{background:rgba(255,255,255,.02)}
.mapacc-ic{font-size:1rem;flex:0 0 auto}
.mapacc-tt{font-size:.86rem;font-weight:600;flex:1}
.mapacc-sub{font-size:.72rem;color:var(--txt-mut);font-weight:400;display:block;margin-top:1px}
.mapacc-caret{color:var(--txt-mut);transition:transform .18s;flex:0 0 auto}
.mapacc.open .mapacc-caret{transform:rotate(180deg)}
.mapacc-body{max-height:0;overflow:hidden;transition:max-height .22s ease}
.mapacc.open .mapacc-body{max-height:480px}
.mapacc-in{padding:0 .75rem .8rem}
.mapcard{position:relative;height:340px;border-radius:9px;overflow:hidden;border:1px solid var(--borda);background:var(--card-2)}
.mapcard-busca{position:absolute;top:8px;left:8px;right:8px;z-index:5;background:var(--card);border:1px solid var(--borda);
  border-radius:8px;color:var(--txt);padding:.42rem .6rem;font-size:.82rem;font-family:inherit;width:calc(100% - 16px)}
.mapcard-busca:focus{outline:0;border-color:var(--verde)}
.radiusbar{display:flex;align-items:center;gap:.6rem;margin-top:.6rem;font-size:.8rem;color:var(--txt-mut)}
.radiusbar input[type=range]{flex:1;accent-color:var(--verde)}
.radiusbar b{color:var(--txt);font-variant-numeric:tabular-nums;min-width:48px;text-align:right}
@media(min-width:768px){
  .mapcard{height:480px}
  .mapacc.open .mapacc-body{max-height:620px}
}
.mapa-usando{display:none;margin-top:.5rem;font-size:.74rem;color:var(--verde-claro);align-items:center;gap:5px}
.mapa-usando.on{display:flex}
</style><script>(function(){
 if(window.__pnav)return; window.__pnav=1;
 var bar=document.createElement('div'); bar.id='pnavprog';
 function mount(){ if(document.body && !document.getElementById('pnavprog')) document.body.appendChild(bar); }
 if(document.body)mount(); else document.addEventListener('DOMContentLoaded',mount);
 var seen={};
 function pre(u){ if(!u||seen[u]||u.indexOf(location.origin)!==0)return; seen[u]=1;
   var l=document.createElement('link'); l.rel='prefetch'; l.href=u; document.head.appendChild(l); }
 function near(t){ return (t && t.closest)?t.closest('a[href^=\\"/painel/prospeccao\\"]'):null; }
 document.addEventListener('mouseover',function(e){var a=near(e.target);if(a)pre(a.href);},{passive:true});
 document.addEventListener('touchstart',function(e){var a=near(e.target);if(a)pre(a.href);},{passive:true});
 document.addEventListener('click',function(e){var a=near(e.target);
   if(a && a.getAttribute('href') && !e.metaKey && !e.ctrlKey && !e.shiftKey && a.target!=='_blank'){ bar.className='go'; bar.style.width='82%'; }},true);
 window.addEventListener('pageshow',function(){ bar.className=''; bar.style.width='0'; });
})();</script>"""
# PF x PJ do lead, compartilhado pelos três formulários que criam/editam lead (Base,
# funil e Ficha). As pílulas trocam rótulo e documento e escondem o que só existe em
# empresa (contato/cargo/sócio/regime/porte e o botão da Receita). Quem manda de fato
# é o TAMANHO do documento — 11 dígitos vira pessoa física sozinho, mesma regra que o
# servidor aplica em _doc_lead (finance/validadoc), pra tela e banco nunca discordarem.
_TIPO_JS = """<script>
function leadTipo(tipo,el){
  var f=(el&&el.closest)?el.closest('[data-tipo-form]'):null; if(!f)return;
  var pj=tipo!=='pf';
  var h=f.querySelector('input[name=tipo]'); if(h)h.value=pj?'pj':'pf';
  f.querySelectorAll('[data-tipo-pill]').forEach(function(b){
    b.classList.toggle('on',b.getAttribute('data-tipo-pill')===(pj?'pj':'pf'));});
  f.querySelectorAll('[data-pj],[data-pf]').forEach(function(n){
    var t=n.getAttribute(pj?'data-pj':'data-pf'); if(t===null)return;
    if(n.tagName==='INPUT'){n.placeholder=t;}else{n.textContent=t;}});
  f.querySelectorAll('[data-so-pj]').forEach(function(n){n.style.display=pj?'':'none';});
}
function leadDoc(el){
  var n=(el.value||'').replace(/\\D/g,'').length;
  if(n===11)leadTipo('pf',el); else if(n===14)leadTipo('pj',el);
}
</script>"""
_CSS = _CSS + _NAV_ASSETS + _TIPO_JS


def _navbar(active):
    """Barra de navegação do módulo, na ordem da história. `active` marca a aba atual.

    FONTE ÚNICA. A tela do Funil (_KANBAN_TPL) já teve uma cópia desta barra escrita à
    mão, e as duas divergiram: quando a aba "Quem atacar" nasceu, entrou aqui e ninguém
    lembrou da cópia — quem estava no Funil não tinha como chegar nela. Se precisar de
    uma aba nova, é aqui e só aqui.

    "Captar Lead" NÃO é aba: ele apontava pro próprio Funil com o painel de captação
    aberto (?captar=1), então virou botão no cabeçalho do Funil, onde ele age. O link
    ?captar=1 continua funcionando pra quem vem de fora ou tem o atalho salvo.
    """
    tabs = [("base", "📇 Base", "/painel/prospeccao/base", False),
            ("campanhas", "📣 Campanhas", "/painel/prospeccao/campanhas", True),
            ("radar", "🎯 Quem atacar", "/painel/prospeccao/radar", True),
            ("ia-insta", "✨ IA Insta", "/painel/prospeccao/ia-insta", False),
            ("comunicacao", "💬 Comunicação", "/painel/prospeccao/comunicacao", False),
            ("funil", "🔥 Funil", "/painel/prospeccao", False),
            ("regua", "⏱️ Régua", "/painel/prospeccao/regua", False),
            ("canais", "⚙️", "/painel/prospeccao/comunicacao?aba=canais", False)]
    # as abas dentro da caixa que rola; o ⚙️ sai dela (ver o CSS do .pnav-rol)
    out = ['<nav class="pnavbar" aria-label="Prospecção">', '<div class="pnav-rol">']
    for key, label, href, gated in tabs:
        # .cfg empurra o ⚙️ pra direita (margin-left:auto) e some com o rótulo: quem
        # configura canal vai lá uma vez; quem trabalha usa as outras seis todo dia.
        extra = " cfg" if key == "canais" else ""
        # ativo dinâmico: a var de render `nav_ativo` sobrepõe o default estático
        # `active` (ex.: na Comunicação, Canais fica ativo quando aba=canais).
        cond = "{% if (nav_ativo|default('" + active + "')) == '" + key + "' %} on{% endif %}"
        titulo = ' title="Canais" aria-label="Canais"' if key == "canais" else ""
        a = ('<a class="pnav' + cond + extra + '"' + titulo + ' href="' + href + '">'
             + label + '</a>')
        if gated:  # Campanhas: gestão vê tudo; vendedor vê as em que é responsável
            a = "{% if gerencia or caps.vendas %}" + a + "{% endif %}"
        if key == "canais":       # fecha a caixa que rola ANTES da engrenagem
            out.append("</div>")
        out.append(a)
    out.append("</nav>")
    return "\n".join(out)


# ---- Captação dentro da Base (Maps/CNPJ/CSV/Manual). Espelha o painel do Funil
# (#captar do kanban, que segue sendo a fonte de verdade); aqui é a cópia da Base,
# com o Google Maps aberto por padrão e recarregando a lista após adicionar.
_CAPTURA_PANEL_HTML = """
<div class="cabas">
  <button type="button" class="caba@@GOOGLE_ON@@" data-tab="google" onclick="capTab('google')">📍 Google Maps</button>
  <button type="button" class="caba@@CNPJ_ON@@" data-tab="manual" onclick="capTab('manual')">🏢 CNPJ / ✏️ Manual</button>
  <button type="button" class="caba@@CSV_ON@@" data-tab="csv" onclick="capTab('csv')">📄 CSV</button>
  {% if gere_campanha %}<button type="button" class="caba" data-tab="explorium" onclick="capTab('explorium')">🔮 Explorium</button>{% endif %}
</div>

<div class="captab" data-tab="google"@@GOOGLE_HIDE@@>
  {% if not tem_places %}
  <div class="mut" style="font-size:.84rem;line-height:1.6">📍 Pra buscar no Google Maps falta a chave. No Render (openclaw-web → Environment) adicione <code style="background:var(--bg);padding:.1rem .35rem;border-radius:5px;border:1px solid var(--borda)">GOOGLE_PLACES_API_KEY</code> (Places API New, billing ativo).</div>
  {% else %}
  <form id="cap-google" action="/painel/prospeccao/captar/buscar" method="post" onsubmit="return capBuscar(event)">
    <div class="egrid">
      <div><label class="lbl">Segmento</label><input class="fld" name="segmento" required placeholder="Ex: pet shop"></div>
      <div><label class="lbl">Cidade</label><input class="fld" name="cidade" id="cap-g-cidade" placeholder="Ex: Teresina - PI"></div>
    </div>

    <div class="mapacc" id="mapacc">
      <div class="mapacc-hd" onclick="mapaToggle()">
        <span class="mapacc-ic">🗺️</span>
        <span class="mapacc-tt">Cercar uma área no mapa
          <span class="mapacc-sub">Opcional — desenhe a região em vez de digitar bairro/rua</span>
        </span>
        <span class="mapacc-caret">▾</span>
      </div>
      <div class="mapacc-body">
        <div class="mapacc-in">
          {% if not tem_maps_js %}
          <div class="mut" style="font-size:.8rem;line-height:1.6">🗺️ Pra desenhar a área falta uma chave (separada da de busca). No Render, adicione <code style="background:var(--bg);padding:.1rem .35rem;border-radius:5px;border:1px solid var(--borda)">GOOGLE_MAPS_JS_API_KEY</code> — Maps JavaScript API, com a chave <b>restrita por domínio</b> no Google Cloud (ela roda no navegador).</div>
          {% else %}
          <div class="mapcard">
            <input type="text" class="mapcard-busca" id="cercaBusca" placeholder="🔍 Endereço ou bairro pra centralizar…">
            <div id="cercaMap" style="position:absolute;inset:0"></div>
          </div>
          <div class="radiusbar">
            <span>Raio</span>
            <input type="range" id="cercaRaio" min="0.5" max="15" step="0.5" value="3">
            <b id="cercaRaioLabel">3.0 km</b>
          </div>
          <input type="hidden" name="lat" id="cercaLat"><input type="hidden" name="lng" id="cercaLng">
          <input type="hidden" name="raio_km" id="cercaRaioKm" value="3">
          {% endif %}
        </div>
      </div>
    </div>
    <div class="mapa-usando" id="mapaUsando">🗺️ <span>Usando a área desenhada acima — bairro/rua ficam de lado enquanto isso</span></div>

    <div class="lbl" style="margin-top:.4rem;color:var(--verde-claro)">📍 Refinar por região <span style="font-weight:400;color:var(--txt-mut)">— opcional, pra buscar numa área específica</span></div>
    <div class="egrid" style="margin-top:.25rem">
      <div><label class="lbl">Bairro</label><input class="fld" name="bairro" id="cap-g-bairro" placeholder="Ex: Jardim Renascença"></div>
      <div><label class="lbl">Rua</label><input class="fld" name="rua" id="cap-g-rua" placeholder="Ex: Av. Nossa Sra. de Fátima"></div>
    </div>
    <div class="mut" style="font-size:.76rem;margin-top:.3rem">Bairro filtra a vizinhança toda. Rua afunila bastante (poucos resultados) — use pra mira fina.</div>
    <label class="rrow" style="border:1px solid var(--borda);border-radius:10px;margin-top:.6rem;cursor:pointer">
      <span class="toggle"><input type="checkbox" name="esconder_redes" value="1" checked><span class="tgl"></span></span>
      <span style="font-size:.88rem">Esconder redes grandes (Petz, Drogasil…)</span>
    </label>
    {% if pode_atribuir %}<div style="max-width:280px;margin-top:.6rem"><label class="lbl">Atribuir a</label><select class="fld" id="cap-g-vend" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}
    <button class="pbtn" style="margin-top:.8rem" id="cap-g-btn">🔍 Buscar</button>
  </form>
  <div id="cap-res" style="margin-top:.9rem"></div>
  {% endif %}
</div>

<div class="captab" data-tab="manual"@@CNPJ_HIDE@@>
  <form id="cap-manual" data-tipo-form action="/painel/prospeccao/novo" method="post" onsubmit="return capManual(event)">
    <input type="hidden" name="voltar" value="@@VOLTAR@@">
    <input type="hidden" name="receita">
    <input type="hidden" name="tipo" value="pj">
    <div class="rcpills">
      <button type="button" class="rcpill on" data-tipo-pill="pj" onclick="leadTipo('pj',this)">🏢 Pessoa Jurídica</button>
      <button type="button" class="rcpill" data-tipo-pill="pf" onclick="leadTipo('pf',this)">🧑 Pessoa Física</button>
    </div>
    <div style="display:flex;gap:.5rem;align-items:end;background:var(--bg);border:1px solid var(--borda);border-radius:10px;padding:.7rem;margin-bottom:.8rem;flex-wrap:wrap">
      <div style="flex:1;min-width:200px"><label class="lbl" data-pj="🔎 CNPJ — puxa tudo da Receita" data-pf="🪪 CPF (opcional)">🔎 CNPJ — puxa tudo da Receita</label><input class="fld" name="documento" inputmode="numeric" data-pj="digite o CNPJ (só números) e clique buscar" data-pf="000.000.000-00" placeholder="digite o CNPJ (só números) e clique buscar" oninput="leadDoc(this)"></div>
      <button type="button" class="pbtn" data-so-pj onclick="capCnpj()" style="white-space:nowrap">↓ Buscar Receita</button>
    </div>
    <div class="egrid">
      <div class="full"><label class="lbl" data-pj="Empresa *" data-pf="Nome completo *">Empresa *</label><input class="fld" name="empresa" required data-pj="Nome da empresa" data-pf="Nome completo" placeholder="Nome da empresa"></div>
      <div data-so-pj><label class="lbl">Contato</label><input class="fld" name="contato"></div>
      <div data-so-pj><label class="lbl">Cargo</label><input class="fld" name="cargo" placeholder="Cargo do contato"></div>
      <div><label class="lbl">Telefone</label><input class="fld" name="telefone"></div>
      <div><label class="lbl">WhatsApp</label><input class="fld" name="whatsapp"></div>
      <div><label class="lbl">E-mail</label><input class="fld" name="email" inputmode="email"></div>
      <div><label class="lbl">Segmento</label><input class="fld" name="segmento" placeholder="Ex: pet shop"></div>
      <div><label class="lbl">Cidade</label><input class="fld" name="cidade"></div>
      <div><label class="lbl">UF</label><input class="fld" name="uf" maxlength="2" style="text-transform:uppercase"></div>
      <div><label class="lbl">Temperatura</label><select class="fld" name="temperatura">{% for v,l in temperaturas_all %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select></div>
      {% if pode_atribuir %}<div><label class="lbl">Vendedor</label><select class="fld" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}
      <div class="full"><label class="lbl">Observações</label><input class="fld" name="obs"></div>
      <div class="full"><button class="pbtn" style="margin:.3rem 0 0">＋ Adicionar à base</button></div>
    </div>
  </form>
</div>

<div class="captab" data-tab="csv"@@CSV_HIDE@@>
  <form id="cap-csv" action="/painel/prospeccao/captar/csv" method="post" enctype="multipart/form-data" onsubmit="return capCsv(event)">
    <label class="lbl">Arquivo CSV</label>
    <input class="fld" type="file" name="arquivo" accept=".csv,text/csv" required>
    <div class="mut" style="font-size:.8rem;margin-top:.5rem">1ª linha = cabeçalho. Colunas: <b>empresa</b>, telefone, whatsapp, cidade, uf, segmento, contato, email, cnpj. Separador , ou ;.</div>
    {% if pode_atribuir %}<div style="max-width:280px;margin-top:.6rem"><label class="lbl">Atribuir a</label><select class="fld" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}
    <button class="pbtn" style="margin-top:.8rem">Importar CSV</button>
  </form>
</div>

{% if gerencia %}
<div class="captab" data-tab="explorium" style="display:none">
  <div class="mut" style="font-size:.8rem;margin-bottom:.7rem">Busca empresas de <b>médio/grande porte</b> na Explorium (Vibe) e importa <b>já com o decisor + contato</b>. <b>Estimar é grátis</b>; importar consome crédito.</div>
  <div class="egrid">
    <div><label class="lbl">Categoria / segmento</label><input class="fld" id="ex-cat" placeholder="ex: food production, software company"></div>
    <div><label class="lbl">Taxonomia</label><select class="fld" id="ex-cattipo"><option value="google">Google</option><option value="linkedin">LinkedIn</option></select></div>
    <div><label class="lbl">País (código)</label><input class="fld" id="ex-pais" value="br"></div>
    <div><label class="lbl">Regiões (opcional)</label><input class="fld" id="ex-reg" placeholder="BR-PE,BR-CE,BR-BA…"></div>
  </div>
  <label class="lbl" style="margin-top:.7rem">Tamanho (funcionários)</label>
  <div style="display:flex;gap:.4rem;flex-wrap:wrap">
    {% for s in ['1-10','11-50','51-200','201-500','501-1000','1001-5000'] %}<label style="font-size:.8rem;border:1px solid var(--borda);border-radius:8px;padding:.25rem .55rem;cursor:pointer;color:var(--txt-mut)"><input type="checkbox" class="ex-size" value="{{ s }}" style="width:auto;vertical-align:middle;accent-color:var(--verde)"> {{ s }}</label>{% endfor %}
  </div>
  <label class="lbl" style="margin-top:.6rem">Cargo do decisor</label>
  <div style="display:flex;gap:.4rem;flex-wrap:wrap">
    {% for cg,rot in [('owner','Dono'),('founder','Fundador'),('cxo','C-level'),('partner','Sócio'),('director','Diretor')] %}<label style="font-size:.8rem;border:1px solid var(--borda);border-radius:8px;padding:.25rem .55rem;cursor:pointer;color:var(--txt-mut)"><input type="checkbox" class="ex-cargo" value="{{ cg }}" {% if cg in ['owner','founder','cxo','partner'] %}checked{% endif %} style="width:auto;vertical-align:middle;accent-color:var(--verde)"> {{ rot }}</label>{% endfor %}
  </div>
  <div style="display:flex;gap:.5rem;align-items:center;margin-top:.8rem;flex-wrap:wrap">
    <button type="button" class="pbtn ghost" onclick="exEstimar()">📊 Estimar (grátis)</button>
    <span id="ex-total" class="mut" style="font-size:.85rem"></span>
    <span style="flex:1"></span>
    <label class="lbl" style="margin:0">Importar</label><input class="fld" id="ex-qtd" value="5" style="width:70px" inputmode="numeric">
    <button type="button" class="pbtn" onclick="exImportar()">🔮 Importar (créditos)</button>
  </div>
</div>
{% endif %}
"""


def _captura_panel(default_tab, voltar):
    """Painel de captação parametrizado (aba padrão + pra onde volta o form manual)."""
    p = _CAPTURA_PANEL_HTML
    for t in ("google", "cnpj", "csv"):
        p = p.replace(f"@@{t.upper()}_ON@@", " on" if t == default_tab else "")
        p = p.replace(f"@@{t.upper()}_HIDE@@", "" if t == default_tab else ' style="display:none"')
    return p.replace("@@VOLTAR@@", voltar)


# JS da captação da Base (recarrega a lista após adicionar; sem addCard do kanban).
_CAPTURA_JS = """<script>
function capTab(t){document.querySelectorAll('.caba').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-tab')===t);});document.querySelectorAll('.captab').forEach(function(d){d.style.display=(d.getAttribute('data-tab')===t)?'block':'none';});}
function capFetch(url,fd){return fetch(url,{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){return r.json();});}
function _capReload(msg){capToast(msg||'Adicionado à base ✓');setTimeout(function(){location.reload();},700);}
function capManual(ev){ev.preventDefault();var f=ev.target;capFetch('/painel/prospeccao/novo',new FormData(f)).then(function(d){if(!d.ok){capToast(d.erro||'Erro',d.link_url?{url:d.link_url,label:d.link_label}:null);return;}f.reset();_capReload(d.msg||'Lead adicionado à base ✓');}).catch(function(){capToast('Falha de rede');});return false;}
function capCnpj(){var f=document.getElementById('cap-manual');var cnpj=f.querySelector('[name=documento]').value.replace(/\\D/g,'');if(cnpj.length!==14){capToast('Pra puxar da Receita, o CNPJ precisa ter 14 dígitos');return;}
  capToast('Consultando Receita…');
  fetch('/painel/prospeccao/cnpj?cnpj='+cnpj,{headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){capToast('CNPJ não encontrado ('+(d.erro||'')+')');return;}var x=d.dados;
    function put(n,v,forca){var el=f.querySelector('[name='+n+']');if(el&&v&&(forca||!el.value))el.value=v;}
    put('empresa',x.nome_fantasia||x.razao_social,false);put('segmento',x.segmento,true);put('cidade',x.cidade,true);put('uf',x.uf,true);
    put('telefone',x.telefone,true);put('email',x.email,true);
    var rc=f.querySelector('[name=receita]');if(rc){try{rc.value=JSON.stringify(x);}catch(e){}}
    capToast('Dados da Receita preenchidos ✓');
  }).catch(function(){capToast('Falha de rede');});}
function capCsv(ev){ev.preventDefault();capFetch('/painel/prospeccao/captar/csv',new FormData(ev.target)).then(function(d){if(!d.ok){capToast('Erro no CSV');return;}_capReload(d.msg||'Importado ✓');}).catch(function(){capToast('Falha de rede');});return false;}
function capBuscar(ev){ev.preventDefault();var f=ev.target;var btn=document.getElementById('cap-g-btn');if(btn){btn.disabled=true;btn.textContent='Buscando…';}
  capFetch('/painel/prospeccao/captar/buscar',new FormData(f)).then(function(d){if(btn){btn.disabled=false;btn.textContent='🔍 Buscar';}var box=document.getElementById('cap-res');
    if(!d.ok){box.innerHTML='<div class="mut" style="color:var(--ambar)">Não consegui buscar ('+(d.erro||'?')+'). Confira a chave/billing e tente de novo.</div>';return;}
    if(!d.itens.length){box.innerHTML='<div class="mut">Nada encontrado'+(d.n_redes?(' ('+d.n_redes+' rede(s) oculta(s))'):'')+'. Tente outro termo/cidade.</div>';return;}
    var TP={quente:'#f0917f',morno:'#e0b25a',frio:'#7bb8e6'};
    var h='<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem"><div class="mut" style="font-size:.82rem">'+d.itens.length+' encontrado(s)'+(d.n_redes?(' · '+d.n_redes+' oculta(s)'):'')+'</div><label class="mut" style="font-size:.8rem;cursor:pointer"><input type="checkbox" onclick="capAll(this)" style="width:auto;vertical-align:middle;accent-color:var(--verde)"> marcar todos</label></div><div class="rlist" id="cap-list">';
    d.itens.forEach(function(it){var loc=(it.cidade?(' · '+jsEsc(it.cidade)+(it.uf?('/'+jsEsc(it.uf)):'')):'');var dupB=it.dup_campanha?(' <span class="dupb">🚫 já em campanha: '+jsEsc(it.dup_campanha)+'</span>'):(it.dup?' <span class="dupb">⚠️ já na base</span>':'');h+='<label class="rrow" style="cursor:pointer"><input type="checkbox" name="itens" value="'+it.pack+'"><span style="flex:1"><span style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap"><b style="font-size:.88rem">'+jsEsc(it.empresa)+'</b>'+dupB+'</span><span class="mut" style="font-size:.76rem">'+(it.segmento?(jsEsc(it.segmento)+' · '):'')+(it.telefone?jsEsc(it.telefone):'')+(it.rating?(' · nota '+it.rating):'')+(it.tem_site?'':' · sem site')+loc+'</span></span><span class="tpill" style="background:transparent;border:1px solid '+(TP[it.temperatura]||'#7bb8e6')+';color:'+(TP[it.temperatura]||'#7bb8e6')+'">'+it.temperatura+'</span></label>';});
    h+='</div><div style="margin-top:.8rem"><button type="button" class="pbtn" onclick="capImport()">＋ Adicionar selecionados à base</button></div>';box.innerHTML=h;
  }).catch(function(){if(btn){btn.disabled=false;btn.textContent='🔍 Buscar';}capToast('Falha de rede');});return false;}
/* ---------------- cercar área no mapa (Google Maps JS API, lazy-carregada) ---------------- */
var GOOGLE_MAPS_JS_KEY = {{ maps_js_key|tojson }};
var _cercaCarregado = false, _cercaMap = null, _cercaCircle = null, _cercaMarker = null;
function mapaToggle(){
  var acc = document.getElementById('mapacc'); if(!acc) return;
  var abrindo = !acc.classList.contains('open');
  acc.classList.toggle('open');
  var bairro = document.getElementById('cap-g-bairro'), rua = document.getElementById('cap-g-rua');
  if(bairro) bairro.disabled = abrindo; if(rua) rua.disabled = abrindo;
  var nota = document.getElementById('mapaUsando'); if(nota) nota.classList.toggle('on', abrindo);
  if(abrindo && !_cercaCarregado && GOOGLE_MAPS_JS_KEY){
    _cercaCarregado = true;
    var s = document.createElement('script');
    s.src = 'https://maps.googleapis.com/maps/api/js?key=' + encodeURIComponent(GOOGLE_MAPS_JS_KEY) + '&libraries=places&callback=cercaMapaInit';
    document.head.appendChild(s);
  }
}
function cercaSync(){
  var c = _cercaCircle.getCenter(), r = _cercaCircle.getRadius(), km = r/1000;
  document.getElementById('cercaLat').value = c.lat();
  document.getElementById('cercaLng').value = c.lng();
  document.getElementById('cercaRaioKm').value = km.toFixed(2);
  document.getElementById('cercaRaio').value = km;
  document.getElementById('cercaRaioLabel').textContent = km.toFixed(1) + ' km';
}
function cercaMapaInit(){
  var partida = {lat: -5.0892, lng: -42.8019};   // Teresina - PI, só ponto de partida
  _cercaMap = new google.maps.Map(document.getElementById('cercaMap'), {
    center: partida, zoom: 13, disableDefaultUI: true, zoomControl: true, fullscreenControl: false});
  _cercaMarker = new google.maps.Marker({position: partida, map: _cercaMap, draggable: true});
  _cercaCircle = new google.maps.Circle({
    map: _cercaMap, center: partida, radius: 3000, editable: true, draggable: false,
    fillColor: 'var(--verde)', fillOpacity: .14, strokeColor: 'var(--verde)', strokeWeight: 2});
  _cercaCircle.bindTo('center', _cercaMarker, 'position');
  _cercaMarker.addListener('drag', cercaSync);
  google.maps.event.addListener(_cercaCircle, 'radius_changed', cercaSync);
  google.maps.event.addListener(_cercaCircle, 'center_changed', cercaSync);
  document.getElementById('cercaRaio').addEventListener('input', function(){
    _cercaCircle.setRadius(parseFloat(this.value) * 1000);
  });
  var buscaInput = document.getElementById('cercaBusca');
  var autocomplete = new google.maps.places.Autocomplete(buscaInput, {fields: ['geometry']});
  autocomplete.addListener('place_changed', function(){
    var place = autocomplete.getPlace();
    if(!place.geometry || !place.geometry.location) return;
    _cercaMap.panTo(place.geometry.location);
    _cercaMap.setZoom(14);
    _cercaMarker.setPosition(place.geometry.location);
    cercaSync();
  });
  cercaSync();
}
function capAll(el){document.querySelectorAll('#cap-list input[name=itens]').forEach(function(c){c.checked=el.checked;});}
function capImport(){var packs=[];document.querySelectorAll('#cap-list input[name=itens]:checked').forEach(function(c){packs.push(c.value);});if(!packs.length){capToast('Marque ao menos um');return;}
  var fd=new FormData();packs.forEach(function(p){fd.append('itens',p);});var vs=document.getElementById('cap-g-vend');if(vs)fd.append('vendedor_id',vs.value);
  capFetch('/painel/prospeccao/captar/importar',fd).then(function(d){if(!d.ok){capToast('Erro ao importar');return;}_capReload((d.msg||'Adicionados')+' ✓');}).catch(function(){capToast('Falha de rede');});}
function jsEsc(s){return (s||'').replace(/[&<>"]/g,function(c){return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c];});}
function capToast(msg,link){var t=document.getElementById('cap-toast');if(!t){t=document.createElement('div');t.id='cap-toast';t.style.cssText='position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--card);border:1px solid var(--verde);color:var(--verde-claro);padding:.6rem 1rem;border-radius:10px;z-index:200;font-size:.85rem;box-shadow:0 6px 20px rgba(0,0,0,.4);transition:opacity .4s;display:flex;align-items:center;gap:.7rem';document.body.appendChild(t);}
  t.textContent='';var span=document.createElement('span');span.textContent=msg;t.appendChild(span);
  if(link&&link.url){var a=document.createElement('a');a.href=link.url;a.textContent=link.label||'Ver ›';a.style.cssText='color:#fff;font-weight:700;text-decoration:underline;white-space:nowrap;flex-shrink:0';t.appendChild(a);}
  t.style.opacity='1';clearTimeout(window._captoastT);window._captoastT=setTimeout(function(){t.style.opacity='0';},link&&link.url?7000:2600);}
function _exForm(){var b=new URLSearchParams();var cat=document.getElementById('ex-cat');if(cat&&cat.value.trim())b.append('categoria',cat.value.trim());var ct=document.getElementById('ex-cattipo');if(ct)b.append('cat_tipo',ct.value);var p=document.getElementById('ex-pais');b.append('pais',(p&&p.value.trim())||'br');var rg=document.getElementById('ex-reg');if(rg&&rg.value.trim())b.append('regioes',rg.value.trim());document.querySelectorAll('.ex-size:checked').forEach(function(c){b.append('tamanho',c.value);});document.querySelectorAll('.ex-cargo:checked').forEach(function(c){b.append('cargo',c.value);});return b;}
function exEstimar(){var b=_exForm();capToast('Estimando…');fetch('/painel/prospeccao/base/explorium-estimar',{method:'POST',headers:{'X-Requested-With':'fetch'},body:b}).then(function(r){return r.json();}).then(function(d){var el=document.getElementById('ex-total');if(!d.ok){if(el)el.textContent='';alert('⚠️ Explorium: '+(d.erro||'erro'));return;}if(el)el.innerHTML='<b style="color:var(--verde-claro)">'+(d.total||0)+'</b> empresas no filtro';capToast((d.total||0)+' empresas no filtro');}).catch(function(){capToast('Falha de rede.');});}
function exImportar(){var q=document.getElementById('ex-qtd');var qtd=(q&&parseInt(q.value,10))||5;if(qtd>10)qtd=10;if(!confirm('Importar '+qtd+' empresa(s) da Explorium com decisor + contato?\\nConsome crédito (fetch + enrich por lead).'))return;var b=_exForm();b.append('qtd',qtd);capToast('Importando da Explorium… (alguns segundos)');fetch('/painel/prospeccao/base/explorium-importar',{method:'POST',headers:{'X-Requested-With':'fetch'},body:b}).then(function(r){return r.json();}).then(function(d){if(!d.ok){alert('⚠️ Explorium: '+(d.erro||'erro'));return;}alert('🔮 Explorium: '+(d.n||0)+' lead(s) importado(s) com decisor'+(d.ja_tinha?(' · '+d.ja_tinha+' já existiam'):'')+'.\\n(de '+(d.empresas||0)+' empresas · '+(d.prospects||0)+' decisores achados)');location.reload();}).catch(function(){capToast('Falha de rede.');});}
</script>"""


_BASE_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<style>
 .bt-tiles{display:grid;grid-template-columns:repeat(5,1fr);gap:.6rem;margin:.9rem 0}
 .bt-tile{border:1px solid var(--borda);border-radius:12px;background:var(--card);padding:.7rem .8rem}
 .bt-tile .v{font-size:1.4rem;font-weight:800;font-variant-numeric:tabular-nums}
 .bt-tile .l{font-size:.72rem;color:var(--mut);margin-top:.1rem}
 .bt-tile.k .v{color:var(--verde-claro)}
 .bt-wrap{overflow-x:auto;border:1px solid var(--borda);border-radius:12px}
 .bt-tbl{width:100%;border-collapse:collapse;font-size:.86rem;min-width:720px}
 .bt-tbl th{text-align:left;font-size:.66rem;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);padding:.5rem .7rem;border-bottom:1px solid var(--borda)}
 .bt-tbl td{padding:.55rem .7rem;border-bottom:1px solid var(--borda);vertical-align:middle}
 .bt-tbl tr:last-child td{border-bottom:0}
 .bt-chip{font-size:.68rem;padding:.1rem .45rem;border-radius:999px;border:1px solid var(--borda);color:var(--mut);white-space:nowrap}
 .bt-dup{font-size:.62rem;font-weight:700;padding:.05rem .4rem;border-radius:999px;color:var(--ambar);border:1px solid var(--ambar-borda);background:#2a2113;white-space:nowrap}
 .hist-warn{border:1px solid var(--ambar-borda);background:#2a2113;color:var(--ambar);border-radius:10px;padding:.55rem .8rem;font-size:.84rem;margin:.2rem 0 .7rem}
 .hist{border:1px solid var(--borda);border-radius:12px;background:var(--card);padding:.6rem .8rem}
 .hist details{border-top:1px solid var(--borda)}.hist details:first-child{border-top:0}
 .hist summary{list-style:none;cursor:pointer;padding:.5rem .1rem;display:flex;align-items:center;gap:.5rem;font-size:.86rem}
 .hist summary::-webkit-details-marker{display:none}
 .hist summary .cnt{margin-left:auto;color:var(--mut);font-size:.78rem}
 .hist .camp{padding:.3rem 0 .3rem 1rem}
 .hist .camp summary{font-size:.83rem;padding:.35rem .1rem}
 .hist .emps{padding:.1rem 0 .5rem 1.6rem;font-size:.8rem;color:var(--mut);line-height:1.7}
 @media(max-width:820px){.bt-tiles{grid-template-columns:repeat(2,1fr)}}
 .capcard{border:1px solid var(--borda);border-radius:14px;background:var(--card);padding:.85rem 1rem;margin:.2rem 0 1.2rem}
 .capttl{font-weight:700;font-size:1rem;display:flex;align-items:center;gap:.45rem;margin-bottom:.75rem}
 .capcard .cabas{margin-bottom:.85rem}
</style>
<div class="pw">
""" + _navbar('base') + """
  <div style="display:flex;align-items:flex-start;gap:.6rem;flex-wrap:wrap">
    <div style="flex:1;min-width:170px">
      <h2 class="tt">📇 Base de captação</h2>
      <div class="mut" style="font-size:.82rem;margin-top:.15rem">Capte e gerencie a matéria-prima das campanhas — vira <b style="color:var(--verde-claro)">lead</b> quando topa no WhatsApp ou responde o e-mail.</div>
    </div>
  </div>

  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <div class="capcard">
    <div class="capttl">➕ Adicionar leads à base</div>
""" + _captura_panel('google', '/painel/prospeccao/base') + """
  </div>
""" + _CAPTURA_JS + """

  <div class="bt-tiles">
    <div class="bt-tile"><div class="v">{{ metr.na_base }}</div><div class="l">Na base</div></div>
    <div class="bt-tile"><div class="v">{{ metr.com_wpp }}</div><div class="l">Com WhatsApp</div></div>
    <div class="bt-tile"><div class="v">{{ metr.com_mail }}</div><div class="l">Com e-mail</div></div>
    <div class="bt-tile"><div class="v">{{ metr.em_camp }}</div><div class="l">Em campanha</div></div>
    <div class="bt-tile k"><div class="v">{{ metr.virou }}</div><div class="l">Viraram lead 🔥</div></div>
  </div>

  <form method="get" action="/painel/prospeccao/base" style="display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:.7rem">
    <input class="fld" name="q" value="{{ q }}" placeholder="Buscar empresa…" style="min-width:180px">
    <input class="fld" name="segmento" value="{{ segmento }}" placeholder="Segmento" style="width:150px">
    <input class="fld" name="cidade" value="{{ cidade }}" placeholder="Cidade" style="width:130px">
    <button class="pbtn ghost">Filtrar</button>
  </form>

  <div id="cnpj-resolver" style="display:none;margin-bottom:.8rem"></div>

  <form method="post" action="/painel/prospeccao/base/promover">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem;gap:.5rem;flex-wrap:wrap">
      <div class="mut" style="font-size:.8rem"><b style="color:var(--txt)" id="base-sel-n">0</b> marcado(s) · {{ leads|length }} na página{% if leads|length>=300 %} (máx 300){% endif %}</div>
      <div style="display:flex;gap:.4rem;align-items:center;flex-wrap:wrap">
        <button type="button" class="pbtn ghost" onclick="baseEnriquecer('canais')" title="Raspa o site dos marcados e acha e-mail / Instagram / WhatsApp (grátis)">🔎 Enriquecer canais</button>
        {% if gere_campanha %}<button type="button" class="pbtn ghost" onclick="baseMarcarSemCnpj()" title="Marca só os leads desta página que ainda não têm CNPJ — pra rodar o Buscar CNPJ só neles">🔎 Marcar sem CNPJ</button>{% endif %}
        {% if gere_campanha %}<button type="button" class="pbtn ghost" onclick="baseEnriquecer('cnpj')" title="Acha o CNPJ dos marcados sem CNPJ ainda, por nome + cidade (CNPJá) — consulta paga. Achando mais de um candidato, mostra pra você escolher aqui mesmo">🏢 Buscar CNPJ</button>{% endif %}
        {% if gere_campanha %}<button type="button" class="pbtn ghost" onclick="baseEnriquecer('decisor')" title="Acha o dono dos marcados na Credify: por CNPJ (sócio) ou pelo telefone do Google (titular/dono) — consulta paga">🎯 Buscar decisor</button>{% endif %}
        {% if gere_campanha %}<button type="button" class="pbtn ghost" onclick="baseExplorium()" title="Teste de conexão com a Explorium (Vibe) no lead marcado">🔮 Explorium (teste)</button>{% endif %}
        <span style="width:1px;height:24px;background:var(--borda);margin:0 .15rem"></span>
        {% if gere_campanha %}
        {% if ver_camp %}
        <button class="pbtn ghost" formaction="/painel/prospeccao/base/tirar-campanha" onclick="return baseTirarCheck()" style="color:var(--ambar);border-color:var(--ambar-borda)" title="Tira os marcados de qualquer campanha — voltam livres pra Base">🔓 Tirar da campanha</button>
        {% else %}
        <select name="campanha_id" class="fld" style="max-width:220px;width:auto" onchange="baseCampSel(this)">
          <option value="">📣 Jogar na campanha…</option>
          {% for c in campanhas %}<option value="{{ c.id }}">{{ c.nome }} · {{ c.status_rot }}</option>{% endfor %}
          <option value="__nova__">➕ Nova campanha…</option>
        </select>
        <input name="novo_nome" id="base-novo-nome" class="fld" placeholder="Nome da nova campanha" maxlength="120" style="display:none;max-width:200px;width:auto">
        <button class="pbtn" formaction="/painel/prospeccao/base/add-campanha" onclick="return baseJogarCheck()" title="Joga os marcados na campanha escolhida">Jogar →</button>
        {% endif %}
        {% endif %}
        <button class="pbtn ghost" name="only" value="">⬆︎ Promover a lead</button>
      </div>
    </div>
    {% if metr.n_dup %}
    <div class="hist-warn">⚠️ <b>{{ metr.n_dup }}</b> empresa(s) aparecem <b>duplicadas</b> na base — revise e exclua a sobra com o 🗑.</div>
    {% endif %}
    <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin:.2rem 0 .6rem">
      {% if metr.em_camp %}
      {% if ver_camp %}
      <a href="/painel/prospeccao/base?{% if q %}q={{ q|urlencode }}&{% endif %}{% if segmento %}segmento={{ segmento|urlencode }}&{% endif %}{% if cidade %}cidade={{ cidade|urlencode }}&{% endif %}ver_camp=0" style="font-size:.8rem;color:var(--verde-claro);text-decoration:none">📣 <b style="color:var(--txt)">{{ metr.em_camp }}</b> já em campanha — ‹ esconder de novo</a>
      {% else %}
      <a href="/painel/prospeccao/base?{% if q %}q={{ q|urlencode }}&{% endif %}{% if segmento %}segmento={{ segmento|urlencode }}&{% endif %}{% if cidade %}cidade={{ cidade|urlencode }}&{% endif %}ver_camp=1" style="font-size:.8rem;color:var(--verde-claro);text-decoration:none">📣 <b style="color:var(--txt)">{{ metr.em_camp }}</b> já em campanha (fora da lista) — ver quais ›</a>
      {% endif %}
      {% endif %}
      <button type="button" class="pbtn ghost" onclick="baseHistorico(this)" style="font-size:.8rem;padding:.35rem .7rem">📅 Histórico de envios</button>
    </div>
    <div id="historico" class="hist" style="display:none;margin:.2rem 0 .8rem"></div>
    <div class="bt-wrap">
      <table class="bt-tbl">
        <thead><tr>
          <th style="width:26px"><input type="checkbox" onclick="var s=this.checked;document.querySelectorAll('.bt-ck').forEach(function(c){c.checked=s});baseUpd()"></th>
          <th>Empresa</th><th>Contato</th><th>Campanha</th><th>Toques</th><th>Última atividade</th><th></th>
        </tr></thead>
        <tbody>
        {% for l in leads %}
          <tr id="bt-row-{{ l.id }}">
            <td><input class="bt-ck" type="checkbox" name="ids" value="{{ l.id }}"{% if not l.cnpj %} data-sem-cnpj="1"{% endif %}{% if l.campanha and not ver_camp %} disabled title="Já está na campanha {{ l.campanha }}"{% endif %}></td>
            <td id="bt-empresa-{{ l.id }}"><b>{{ l.empresa }}</b>{% if l.dup %} <span class="bt-dup" title="Empresa aparece mais de uma vez na base">⚠️ dup</span>{% endif %}<div class="mut" style="font-size:.76rem">{{ l.segmento or '—' }}{% if l.cidade %} · {{ l.cidade }}{% if l.uf %}/{{ l.uf }}{% endif %}{% endif %}</div>{% if l.cnpj %}<div class="mut bt-cnpj-tag" style="font-size:.72rem">🏢 {{ l.cnpj }}</div>{% endif %}</td>
            <td style="font-size:.76rem;line-height:1.5;min-width:190px">
              {% if l.whats %}<div>💬 {{ l.whats }}</div>{% endif %}
              {% if l.email_v %}<div>✉️ {{ l.email_v }}</div>{% endif %}
              {% if l.insta %}<div class="mut">📷 {{ l.insta }}</div>{% endif %}
              {% if l.tem_decisor %}<div style="color:var(--verde-claro)">🎯 {{ l.dec_nome or 'Decisor' }}
                {% if l.dec_tels %}
                  {% for t in l.dec_tels_visiveis %}<label style="display:flex;align-items:center;gap:.3rem;margin-top:.1rem;cursor:pointer" title="Marcado = esse número vai quando jogar pra campanha">
                    <input type="checkbox" name="tel_{{ l.id }}" value="{{ t.formatado }}"{% if t.melhor %} checked{% endif %} style="width:auto;margin:0;accent-color:var(--verde)">
                    {{ t.formatado }}{% if t.provavel %} ⭐{% endif %}{% if t.whatsapp %} 💬{% endif %}{% if t.tipo_rot %} <span class="mut">· {{ t.tipo_rot }}</span>{% endif %}</label>{% endfor %}
                  {% if l.dec_tels_ocultos %}
                  <div class="mut" style="font-size:.72rem;margin-top:.15rem;cursor:pointer;text-decoration:underline" onclick="this.nextElementSibling.style.display='block';this.style.display='none'">+{{ l.dec_tels_ocultos|length }} número(s) sem WhatsApp confirmado ›</div>
                  <div style="display:none">
                    {% for t in l.dec_tels_ocultos %}<label style="display:flex;align-items:center;gap:.3rem;margin-top:.1rem;cursor:pointer">
                      <input type="checkbox" name="tel_{{ l.id }}" value="{{ t.formatado }}"{% if t.melhor %} checked{% endif %} style="width:auto;margin:0;accent-color:var(--verde)">
                      {{ t.formatado }}{% if t.provavel %} ⭐{% endif %}{% if t.tipo_rot %} <span class="mut">· {{ t.tipo_rot }}</span>{% endif %}</label>{% endfor %}
                  </div>
                  {% endif %}
                {% elif l.dec_tel %} · {{ l.dec_tel }}
                {% else %} · <span class="mut">(sem telefone na Credify)</span>{% endif %}
              </div>{% endif %}
              {% if not l.whats and not l.email_v and not l.insta %}<span class="mut">{% if l.verificado %}✓ verificado · sem dados no site{% else %}—{% endif %}</span>{% endif %}
            </td>
            <td>{% if l.campanha %}<span class="bt-chip">{{ l.campanha }}</span>{% else %}<span class="mut">—</span>{% endif %}</td>
            <td class="mut" style="font-variant-numeric:tabular-nums;white-space:nowrap">💬 {{ l.toque_wa }} · ✉️ {{ l.toque_mail }}</td>
            <td class="mut" style="font-size:.78rem;white-space:nowrap">{{ l.ult or '—' }}</td>
            <td style="white-space:nowrap"><button class="pbtn ghost" name="only" value="{{ l.id }}" style="padding:.2rem .5rem;font-size:.76rem" title="Promover a lead">⬆︎</button>
              <button type="button" class="pbtn ghost" onclick="baseExcluir({{ l.id }},this)" style="padding:.2rem .5rem;font-size:.76rem;color:var(--coral);border-color:#5c2a27" title="Excluir lead da base">🗑</button></td>
          </tr>
        {% else %}
          <tr><td colspan="7" class="mut" style="text-align:center;padding:1.5rem">Nada na base ainda. Use o <b style="color:var(--verde-claro)">➕ Adicionar leads à base</b> acima ↑ pra começar.</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </form>
</div>
<script>
function baseCampSel(s){var i=document.getElementById('base-novo-nome');if(!i)return;var nova=(s.value==='__nova__');i.style.display=nova?'':'none';if(nova){i.focus();}}
function baseChecked(){var a=[];document.querySelectorAll('.bt-ck:checked').forEach(function(c){a.push(c.value);});return a;}
function baseExcluir(id,btn){
  if(!confirm('Excluir este lead da base? Sai também de qualquer campanha e não dá pra desfazer.'))return;
  fetch('/painel/prospeccao/'+id+'/excluir',{method:'POST',headers:{'X-Requested-With':'fetch'},body:new FormData()})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert('Não consegui excluir ('+(d.erro||'?')+').');return;}
      var tr=btn.closest('tr');if(tr)tr.remove();
    }).catch(function(){alert('Falha de rede.');});
}
function baseHistorico(btn){
  var p=document.getElementById('historico');
  if(p.style.display!=='none'){p.style.display='none';return;}
  p.style.display='block';
  if(p.getAttribute('data-loaded')==='1')return;
  p.innerHTML='<div class="mut" style="font-size:.82rem">Carregando…</div>';
  fetch('/painel/prospeccao/base/historico').then(function(r){return r.json();}).then(function(d){
    if(!d.ok){p.innerHTML='<div class="mut" style="font-size:.82rem">Não consegui carregar.</div>';return;}
    if(!d.dias.length){p.innerHTML='<div class="mut" style="font-size:.82rem">Nenhum envio a campanha ainda.</div>';return;}
    var h='';
    d.dias.forEach(function(dia){
      h+='<details><summary>📅 <b>'+jsEsc(dia.dia)+'</b> <span class="cnt">'+dia.total+' lead(s) · '+dia.campanhas.length+' campanha(s)</span></summary>';
      dia.campanhas.forEach(function(cg){
        h+='<details class="camp"><summary>📣 '+jsEsc(cg.nome)+' <span class="cnt">'+cg.n+'</span></summary><div class="emps">'+cg.empresas.map(jsEsc).join(' · ')+'</div></details>';
      });
      h+='</details>';
    });
    p.innerHTML=h;p.setAttribute('data-loaded','1');
  }).catch(function(){p.innerHTML='<div class="mut" style="font-size:.82rem">Falha de rede.</div>';});
}
function baseExplorium(){
  var ids=baseChecked();
  if(!ids.length){alert('Marque um lead pra testar o Explorium.');return;}
  var body=new URLSearchParams();body.append('ids',ids[0]);
  capToast('Consultando Explorium…');
  fetch('/painel/prospeccao/base/explorium',{method:'POST',headers:{'X-Requested-With':'fetch'},body:body}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){alert('⚠️ Explorium: '+(d.erro||'erro'));return;}
    alert('🔮 Explorium — '+(d.empresa||'')+' ('+(d.dominio||'sem domínio')+')\\n\\n'+JSON.stringify(d.resposta,null,2).slice(0,1600));
  }).catch(function(){capToast('Falha de rede.');});
}
function baseEnriquecer(tipo){
  var ids=baseChecked();
  if(!ids.length){alert('Marque ao menos um contato pra enriquecer.');return;}
  if(tipo==='decisor' && !confirm('Buscar o decisor de '+ids.length+' lead(s) na Credify?\\nÉ consulta paga — usa o CNPJ (sócio) OU o telefone (titular/dono) e pula quem já tem decisor.'))return;
  if(tipo==='cnpj' && !confirm('Buscar o CNPJ de '+ids.length+' lead(s) sem CNPJ ainda, por nome + cidade (CNPJá)?\\nÉ consulta paga — aplica sozinho quando acha 1 candidato só; achando mais de um ou nenhum, mostra aqui mesmo pra você resolver.'))return;
  var body=new URLSearchParams();ids.forEach(function(i){body.append('ids',i);});body.append('tipo',tipo);
  capToast(tipo==='decisor'?'Buscando decisores… (alguns segundos)':tipo==='cnpj'?'Buscando CNPJ… (alguns segundos)':'Verificando os sites… (alguns segundos)');
  fetch('/painel/prospeccao/base/qualificar',{method:'POST',headers:{'X-Requested-With':'fetch'},body:body}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){alert('⚠️ '+(d.erro||'Não consegui rodar.'));return;}
    var msg;
    if(tipo==='decisor'){
      if(!d.n){alert('Nenhum lead marcado é elegível pra decisor.'+(d.sem_cnpj?('\\n'+d.sem_cnpj+' sem CNPJ nem telefone.'):'')+'\\n(Também pula quem já tem decisor.)');return;}
      msg='🎯 '+d.n+' consultado(s) · '+d.achou+' decisor(es) encontrado(s)'+(d.sem?(' · '+d.sem+' sem quadro/telefone na Credify'):'');
    }else if(tipo==='cnpj'){
      if(!d.n){alert('Nenhum lead marcado precisa de CNPJ (já têm ou estão sem nome).');return;}
      msg='🏢 '+d.n+' consultado(s) · '+d.achou+' CNPJ encontrado(s) e aplicado(s)'+(d.ambiguo?(' · '+d.ambiguo+' com mais de 1 candidato — escolha abaixo'):'')+(d.sem?(' · '+d.sem+' não achou'):'')+'\\n(CNPJ achado destrava o 🎯 Buscar decisor)';
    }else{
      if(!d.n){alert('Nenhum lead marcado tem site pra raspar'+(d.sem_site?(' ('+d.sem_site+' sem site)'):'')+'.');return;}
      msg='🔎 '+d.n+' site(s) verificado(s) · '+d.com_email+' com e-mail · '+d.com_wa+' com WhatsApp · '+d.com_cnpj+' com CNPJ'+(d.sem?(' · '+d.sem+' sem nada'):'')+'\\n(CNPJ achado destrava o 🎯 Buscar decisor)';
    }
    if(d.resto)msg+='\\n(processei os primeiros '+(d.n)+'; marque menos ou repita pros demais)';
    alert(msg);
    // sempre recarrega — os que deram "achou" já foram gravados e só aparecem
    // na lista depois do reload; os ambíguos/não-achados (se tiver) reabrem o
    // painel sozinhos, restaurados do sessionStorage.
    if(tipo==='cnpj' && ((d.ambiguos && d.ambiguos.length) || (d.sem_leads && d.sem_leads.length))){
      try{sessionStorage.setItem('cnpjPendentes',JSON.stringify({ambiguos:d.ambiguos||[],sem_leads:d.sem_leads||[]}));}catch(e){}
    }
    location.reload();
  }).catch(function(){capToast('Falha de rede — tente de novo.');});
}
function cnpjResolverAbrir(leads,semLeads){
  var box=document.getElementById('cnpj-resolver');
  leads=leads||[];semLeads=semLeads||[];
  var h='';
  if(leads.length){
    h+='<div class="fsec" style="padding:.9rem"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.4rem">'
      +'<b style="font-size:.9rem">🏢 Escolha o CNPJ certo (encontrou mais de 1 candidato)</b>'
      +'<button type="button" class="pbtn ghost" style="padding:.25rem .6rem;font-size:.75rem;margin:0" onclick="this.closest(\\'.fsec\\').style.display=\\'none\\'">fechar</button></div>';
    leads.forEach(function(lead){
      h+='<div id="cnpj-res-lead-'+lead.id+'" style="margin-top:.6rem;padding-top:.6rem;border-top:1px solid var(--borda)">'
        +'<div class="mut" style="font-size:.8rem;margin-bottom:.2rem">📇 <b style="color:var(--txt)">'+jsEsc(lead.empresa||('lead #'+lead.id))+'</b> — qual é o CNPJ certo?</div>'
        +(lead.endereco?('<div class="mut" style="font-size:.76rem;margin-bottom:.3rem">📍 Endereço do Maps: <b>'+jsEsc(lead.endereco)+'</b> — confira qual candidato bate:</div>'):'')
        +'<div class="rlist">';
    lead.itens.forEach(function(it){
      var loc=(it.cidade?(jsEsc(it.cidade)+(it.uf?('/'+it.uf):'')):'');
      h+='<div class="rrow" style="gap:.5rem;align-items:flex-start"><span style="flex:1"><b style="font-size:.85rem">'+jsEsc(it.razao_social||it.nome_fantasia||it.cnpj)+'</b>'
        +'<span class="mut" style="font-size:.74rem"> · '+it.cnpj+(it.situacao?(' · '+jsEsc(it.situacao)):'')+'</span>'
        +(it.endereco?('<span class="mut" style="display:block;font-size:.74rem">📍 '+jsEsc(it.endereco)+(loc?(' · '+loc):'')+'</span>'):(loc?('<span class="mut" style="display:block;font-size:.74rem">📍 '+loc+'</span>'):''))
        +(it.socio?('<span class="mut" style="display:block;font-size:.74rem">🧑‍💼 '+jsEsc(it.socio)+'</span>'):'')
        +'</span><button type="button" class="pbtn" style="padding:.3rem .7rem;font-size:.78rem;margin:0;flex-shrink:0" onclick="cnpjResolverUsar('+lead.id+',\\''+it.cnpj+'\\',this)">usar</button></div>';
    });
    h+='</div>'+(lead.web?('<div style="margin-top:.4rem"><a class="mut" style="font-size:.76rem" target="_blank" rel="noopener" href="'+lead.web+'">nenhuma bate? buscar na web →</a></div>'):'')
      +'<div style="display:flex;gap:.4rem;align-items:center;margin-top:.5rem;padding-top:.5rem;border-top:1px dashed var(--borda)">'
      +'<span class="mut" style="font-size:.76rem;white-space:nowrap">Já tem o CNPJ?</span>'
      +'<input class="fld" id="cnpj-manual-'+lead.id+'" placeholder="cole aqui" style="flex:1;padding:.4rem .55rem">'
      +'<button type="button" class="pbtn" style="padding:.35rem .8rem;font-size:.78rem;margin:0" onclick="cnpjResolverColar('+lead.id+',\\'cnpj-manual-'+lead.id+'\\',this)">usar</button></div>'
      +'</div>';
    });
    h+='</div>';
  }
  if(semLeads.length){
    h+='<div class="fsec" style="padding:.9rem'+(leads.length?';margin-top:1rem':'')+'">'
      +'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.4rem">'
      +'<b style="font-size:.9rem">🔍 Não encontrado ('+semLeads.length+')</b>'
      +'<button type="button" class="pbtn ghost" style="padding:.25rem .6rem;font-size:.75rem;margin:0" onclick="this.closest(\\'.fsec\\').style.display=\\'none\\'">fechar</button></div>'
      +'<div class="mut" style="font-size:.78rem;margin-bottom:.7rem">A CNPJá não achou nenhum candidato pra esses — geralmente uma busca no Google resolve (o CNPJ costuma aparecer direto no resultado ou na ficha do Google Meu Negócio).</div>'
      +'<div style="display:flex;flex-direction:column;gap:.6rem">';
    semLeads.forEach(function(lead){
      h+='<div id="cnpj-sem-lead-'+lead.id+'" style="border:1px dashed var(--borda);border-radius:12px;padding:.7rem .8rem">'
        +'<div style="display:flex;align-items:center;justify-content:space-between;gap:.8rem;flex-wrap:wrap">'
        +'<div style="min-width:0"><div style="font-size:.85rem;font-weight:600">'+jsEsc(lead.empresa||('lead #'+lead.id))+'</div>'
        +(lead.endereco?('<div class="mut" style="font-size:.76rem;margin-top:.2rem">📍 Endereço do Maps: '+jsEsc(lead.endereco)+'</div>'):'')+'</div>'
        +(lead.web?('<a href="'+lead.web+'" target="_blank" rel="noopener" style="font-size:.8rem;font-weight:600;color:var(--verde-claro);text-decoration:none;display:inline-flex;align-items:center;gap:.3rem;white-space:nowrap;flex-shrink:0;padding:.4rem .7rem;border:1px solid var(--neon-borda);border-radius:8px">🔎 buscar na web ›</a>'):'')
        +'</div>'
        +'<div style="display:flex;gap:.4rem;align-items:center;margin-top:.5rem;padding-top:.5rem;border-top:1px dashed var(--borda)">'
        +'<span class="mut" style="font-size:.76rem;white-space:nowrap">Já tem o CNPJ?</span>'
        +'<input class="fld" id="cnpj-manual-'+lead.id+'" placeholder="cole aqui" style="flex:1;padding:.4rem .55rem">'
        +'<button type="button" class="pbtn" style="padding:.35rem .8rem;font-size:.78rem;margin:0" onclick="cnpjResolverColar('+lead.id+',\\'cnpj-manual-'+lead.id+'\\',this)">usar</button></div>'
        +'</div>';
    });
    h+='</div></div>';
  }
  box.innerHTML=h;
  box.style.display=h?'block':'none';
  if(h)box.scrollIntoView({behavior:'smooth',block:'start'});
}
function cnpjResolverUsar(id,cnpj,btn){
  btn.disabled=true;btn.textContent='...';
  var body=new URLSearchParams();body.append('cnpj',cnpj);
  fetch('/painel/prospeccao/'+id+'/aplicar-cnpj',{method:'POST',headers:{'X-Requested-With':'fetch'},body:body}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){alert('⚠️ '+(d.erro||'Não consegui aplicar.'));btn.disabled=false;btn.textContent='usar';return;}
    var card=document.getElementById('cnpj-res-lead-'+id)||document.getElementById('cnpj-sem-lead-'+id);
    if(card)card.outerHTML='<div class="ok" style="margin-top:.6rem">✓ '+jsEsc(d.msg||'CNPJ aplicado')+'</div>';
    // atualiza a linha na tabela agora — sem isso o CNPJ já tá salvo no banco mas
    // some da vista até dar reload (que só acontece quando zera o painel inteiro).
    var row=document.getElementById('bt-row-'+id);
    if(row){
      var cel=document.getElementById('bt-empresa-'+id);
      if(cel && !cel.querySelector('.bt-cnpj-tag')){
        var tag=document.createElement('div');
        tag.className='mut bt-cnpj-tag';tag.style.fontSize='.72rem';
        tag.textContent='🏢 '+cnpj.replace(/\\D/g,'');
        cel.appendChild(tag);
      }
      var ck=row.querySelector('.bt-ck');
      if(ck)ck.removeAttribute('data-sem-cnpj');
      row.style.transition='background .6s';row.style.background='rgba(93,202,165,.14)';
      setTimeout(function(){row.style.background='';},1400);
    }
    var box=document.getElementById('cnpj-resolver');
    // só recarrega quando não sobrar NENHUM ambíguo pendente e não tiver bloco de
    // "não encontrado" (esse fica exposto até o usuário sair da página — reload
    // apagaria os links de busca, e ele não é recarregado do banco).
    if(box && !box.querySelector('[id^="cnpj-res-lead-"]') && !box.querySelector('[id^="cnpj-sem-lead-"]')){
      setTimeout(function(){location.reload();},900);
    }
  }).catch(function(){alert('Falha de rede — tente de novo.');btn.disabled=false;btn.textContent='usar';});
}
function cnpjResolverColar(id,inputId,btn){
  var v=(document.getElementById(inputId).value||'').replace(/\\D/g,'');
  if(v.length!==14){alert('CNPJ precisa ter 14 dígitos.');return;}
  cnpjResolverUsar(id,v,btn);
}
(function(){
  // depois do reload que mostra os "achou", reabre o painel sozinho pros que
  // ainda ficaram pendentes (ambíguos pra escolher / não achados pra buscar na
  // web), guardados antes de recarregar.
  try{
    var pend=sessionStorage.getItem('cnpjPendentes');
    if(pend){
      sessionStorage.removeItem('cnpjPendentes');
      var d=JSON.parse(pend);
      if(d&&((d.ambiguos&&d.ambiguos.length)||(d.sem_leads&&d.sem_leads.length)))cnpjResolverAbrir(d.ambiguos,d.sem_leads);
    }
  }catch(e){}
})();
function baseMarcados(){return document.querySelectorAll('.bt-ck:checked').length;}
function baseUpd(){var n=document.getElementById('base-sel-n');if(n)n.textContent=baseMarcados();}
function baseMarcarSemCnpj(){
  var n=0;
  document.querySelectorAll('.bt-ck').forEach(function(c){
    var semCnpj=c.hasAttribute('data-sem-cnpj')&&!c.disabled;
    c.checked=semCnpj;
    if(semCnpj)n++;
  });
  baseUpd();
  if(!n)alert('Nenhum lead sem CNPJ nesta página (ou já tá tudo em campanha).');
}
document.addEventListener('change',function(e){if(e.target&&e.target.classList&&e.target.classList.contains('bt-ck'))baseUpd();});
function baseJogarCheck(){
  if(baseMarcados()===0){alert('Marque ao menos um contato na lista pra jogar na campanha.');return false;}
  var s=document.querySelector('select[name=campanha_id]');if(!s)return true;
  if(!s.value){alert('Escolha uma campanha (ou crie uma nova) no seletor.');s.focus();return false;}
  if(s.value==='__nova__'){var i=document.getElementById('base-novo-nome');if(i&&!i.value.trim()){alert('Dê um nome pra nova campanha.');i.focus();return false;}}
  return true;
}
function baseTirarCheck(){
  var n=baseMarcados();
  if(n===0){alert('Marque ao menos um contato na lista pra tirar da campanha.');return false;}
  return confirm('Tirar '+n+' contato(s) da campanha? Eles voltam livres pra Base, prontos pra enriquecer e reenviar.');
}
</script>
{% endblock %}"""


_KANBAN_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<div class="pw">
""" + _navbar('funil') + """
  <div style="display:flex;align-items:flex-start;gap:.6rem;flex-wrap:wrap">
    <div style="flex:1;min-width:170px">
      <h2 class="tt">Prospecção</h2>
      <div class="mut" style="font-size:.82rem;margin-top:.15rem">{% if conta %}<b style="color:var(--verde-claro)">🏢 {{ conta[2] }}</b> · {% endif %}<span id="kb-total-n">{{ total_alvos }}</span> alvo(s){% if total_valor %} · pipeline {{ brl(total_valor) }}{% endif %}{% if n_contextos and n_contextos > 1 %} · <a href="/trocar" style="color:var(--verde-claro)">trocar empresa ⇄</a>{% endif %}</div>
    </div>
    {# Captar Lead saiu da barra de abas e virou botão AQUI: ele nunca navegou pra lugar
       nenhum — abre o painel de captação logo abaixo, nesta mesma tela. Como aba ele
       ocupava 129px da barra apontando pro próprio Funil; como botão fica em evidência,
       ao lado do título, e a barra ganhou o espaço de volta. #}
    <button type="button" class="cap-btn" onclick="capToggle()">🎯 Captar Lead</button>
  </div>

  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <!-- painel de captação inline (abre pra baixo, sem sair da página) -->
  <div id="captar" class="fsec" style="display:none;margin-top:1rem">
    <div class="cabas">
      <button type="button" class="caba on" data-tab="manual" onclick="capTab('manual')">✏️ Manual</button>
      <button type="button" class="caba" data-tab="csv" onclick="capTab('csv')">📄 CSV</button>
      <button type="button" class="caba" data-tab="google" onclick="capTab('google')">📍 Google Maps</button>
    </div>

    <div class="captab" data-tab="manual">
      <form id="cap-manual" data-tipo-form action="/painel/prospeccao/novo" method="post" onsubmit="return capManual(event)">
        <input type="hidden" name="voltar" value="/painel/prospeccao">
        <!-- cadastrou aqui dentro do funil = o lead ENTRA no funil (estagio=lead).
             Sem isto ele nascia na Base e o card sumia no primeiro refresh. -->
        <input type="hidden" name="destino" value="funil">
        <input type="hidden" name="receita">
        <input type="hidden" name="tipo" value="pj">
        <div class="rcpills">
          <button type="button" class="rcpill on" data-tipo-pill="pj" onclick="leadTipo('pj',this)">🏢 Pessoa Jurídica</button>
          <button type="button" class="rcpill" data-tipo-pill="pf" onclick="leadTipo('pf',this)">🧑 Pessoa Física</button>
        </div>
        <div style="display:flex;gap:.5rem;align-items:end;background:var(--bg);border:1px solid var(--borda);border-radius:10px;padding:.7rem;margin-bottom:.8rem;flex-wrap:wrap">
          <div style="flex:1;min-width:200px"><label class="lbl" data-pj="🔎 CNPJ — puxa tudo da Receita" data-pf="🪪 CPF (opcional)">🔎 CNPJ — puxa tudo da Receita</label><input class="fld" name="documento" inputmode="numeric" data-pj="digite o CNPJ (só números) e clique buscar" data-pf="000.000.000-00" placeholder="digite o CNPJ (só números) e clique buscar" oninput="leadDoc(this)"></div>
          <button type="button" class="pbtn" data-so-pj onclick="capCnpj()" style="white-space:nowrap">↓ Buscar Receita</button>
        </div>
        <div class="egrid">
          <div class="full"><label class="lbl" data-pj="Empresa *" data-pf="Nome completo *">Empresa *</label><input class="fld" name="empresa" required data-pj="Nome da empresa" data-pf="Nome completo" placeholder="Nome da empresa"></div>
          <div data-so-pj><label class="lbl">Contato</label><input class="fld" name="contato"></div>
          <div data-so-pj><label class="lbl">Cargo</label><input class="fld" name="cargo" placeholder="Cargo do contato"></div>
          <div><label class="lbl">Telefone</label><input class="fld" name="telefone"></div>
          <div><label class="lbl">WhatsApp</label><input class="fld" name="whatsapp"></div>
          <div><label class="lbl">E-mail</label><input class="fld" name="email" inputmode="email"></div>
          <div><label class="lbl">Segmento</label><input class="fld" name="segmento" placeholder="Ex: pet shop"></div>
          <div><label class="lbl">Cidade</label><input class="fld" name="cidade"></div>
          <div><label class="lbl">UF</label><input class="fld" name="uf" maxlength="2" style="text-transform:uppercase"></div>
          <div data-so-pj><label class="lbl">Sócio</label><input class="fld" name="socio"></div>
          <div data-so-pj><label class="lbl">Regime</label><input class="fld" name="regime_tributario"></div>
          <div data-so-pj><label class="lbl">Porte</label><input class="fld" name="porte"></div>
          <div><label class="lbl">Instagram</label><input class="fld" name="instagram" placeholder="@perfil"></div>
          <div><label class="lbl">Site (link)</label><input class="fld" name="site_url" inputmode="url" placeholder="https://…"></div>
          <div><label class="lbl">Valor (R$)</label><input class="fld" name="valor" inputmode="decimal" placeholder="0,00"></div>
          <div><label class="lbl">Temperatura</label><select class="fld" name="temperatura">{% for v,l in temperaturas_all %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select></div>
          {% if pode_atribuir %}<div><label class="lbl">Vendedor</label><select class="fld" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}
          <div class="full"><label class="lbl">Observações</label><input class="fld" name="obs"></div>
          <div class="full"><button class="pbtn" style="margin:.3rem 0 0">Adicionar ao funil</button>
            <div class="mut" style="font-size:.78rem;margin-top:.4rem">Entra direto no funil, na primeira etapa. Pra alimentar a Base (campanhas em massa), use 📇 Base.</div></div>
        </div>
      </form>
    </div>

    <div class="captab" data-tab="csv" style="display:none">
      <form id="cap-csv" action="/painel/prospeccao/captar/csv" method="post" enctype="multipart/form-data" onsubmit="return capCsv(event)">
        <label class="lbl">Arquivo CSV</label>
        <input class="fld" type="file" name="arquivo" accept=".csv,text/csv" required>
        <div class="mut" style="font-size:.8rem;margin-top:.5rem">1ª linha = cabeçalho. Colunas: <b>empresa</b>, telefone, whatsapp, cidade, uf, segmento, contato, email, cnpj. Separador , ou ;.</div>
        {% if pode_atribuir %}<div style="max-width:280px;margin-top:.6rem"><label class="lbl">Atribuir a</label><select class="fld" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}
        <button class="pbtn" style="margin-top:.8rem">Importar CSV</button>
      </form>
    </div>

    <div class="captab" data-tab="google" style="display:none">
      {% if not tem_places %}
      <div class="mut" style="font-size:.84rem;line-height:1.6">📍 Pra buscar no Google Maps falta a chave. No Render (openclaw-web → Environment) adicione <code style="background:var(--bg);padding:.1rem .35rem;border-radius:5px;border:1px solid var(--borda)">GOOGLE_PLACES_API_KEY</code> (Places API New, billing ativo).</div>
      {% else %}
      <form id="cap-google" action="/painel/prospeccao/captar/buscar" method="post" onsubmit="return capBuscar(event)">
        <div class="egrid">
          <div><label class="lbl">Segmento</label><input class="fld" name="segmento" required placeholder="Ex: pet shop"></div>
          <div><label class="lbl">Cidade</label><input class="fld" name="cidade" id="cap-g-cidade" placeholder="Ex: Teresina - PI"></div>
        </div>

        <div class="mapacc" id="mapacc">
          <div class="mapacc-hd" onclick="mapaToggle()">
            <span class="mapacc-ic">🗺️</span>
            <span class="mapacc-tt">Cercar uma área no mapa
              <span class="mapacc-sub">Opcional — desenhe a região em vez de digitar bairro/rua</span>
            </span>
            <span class="mapacc-caret">▾</span>
          </div>
          <div class="mapacc-body">
            <div class="mapacc-in">
              {% if not tem_maps_js %}
              <div class="mut" style="font-size:.8rem;line-height:1.6">🗺️ Pra desenhar a área falta uma chave (separada da de busca). No Render, adicione <code style="background:var(--bg);padding:.1rem .35rem;border-radius:5px;border:1px solid var(--borda)">GOOGLE_MAPS_JS_API_KEY</code> — Maps JavaScript API, com a chave <b>restrita por domínio</b> no Google Cloud (ela roda no navegador).</div>
              {% else %}
              <div class="mapcard">
                <input type="text" class="mapcard-busca" id="cercaBusca" placeholder="🔍 Endereço ou bairro pra centralizar…">
                <div id="cercaMap" style="position:absolute;inset:0"></div>
              </div>
              <div class="radiusbar">
                <span>Raio</span>
                <input type="range" id="cercaRaio" min="0.5" max="15" step="0.5" value="3">
                <b id="cercaRaioLabel">3.0 km</b>
              </div>
              <input type="hidden" name="lat" id="cercaLat"><input type="hidden" name="lng" id="cercaLng">
              <input type="hidden" name="raio_km" id="cercaRaioKm" value="3">
              {% endif %}
            </div>
          </div>
        </div>
        <div class="mapa-usando" id="mapaUsando">🗺️ <span>Usando a área desenhada acima — bairro/rua ficam de lado enquanto isso</span></div>

        <div class="lbl" style="margin-top:.4rem;color:var(--txt-mut)">📍 Refinar por região <span style="font-weight:400">— opcional, pra buscar numa área específica</span></div>
        <div class="egrid" style="margin-top:.25rem">
          <div><label class="lbl">Bairro</label><input class="fld" name="bairro" id="cap-g-bairro" placeholder="Ex: Jardim Renascença"></div>
          <div><label class="lbl">Rua</label><input class="fld" name="rua" id="cap-g-rua" placeholder="Ex: Av. Nossa Sra. de Fátima"></div>
        </div>
        <div class="mut" style="font-size:.76rem;margin-top:.3rem">Bairro filtra a vizinhança toda. Rua afunila bastante (poucos resultados) — use pra mira fina.</div>
        <label class="rrow" style="border:1px solid var(--borda);border-radius:10px;margin-top:.6rem;cursor:pointer">
          <span class="toggle"><input type="checkbox" name="esconder_redes" value="1" checked><span class="tgl"></span></span>
          <span style="font-size:.88rem">Esconder redes grandes (Petz, Drogasil…)</span>
        </label>
        {% if pode_atribuir %}<div style="max-width:280px;margin-top:.6rem"><label class="lbl">Atribuir a</label><select class="fld" id="cap-g-vend" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}
        <button class="pbtn" style="margin-top:.8rem" id="cap-g-btn">Buscar</button>
      </form>
      <div id="cap-res" style="margin-top:.9rem"></div>
      {% endif %}
    </div>
  </div>

  {% if gerencia %}
  <form method="get" action="/painel/prospeccao" style="margin-top:.8rem;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap">
    <span class="mut" style="font-size:.8rem">Vendedor:</span>
    <select name="vendedor" onchange="this.form.submit()" style="width:auto;padding:.4rem .6rem">
      <option value="" {% if not filtro_vend %}selected{% endif %}>Todos</option>
      <option value="nao" {% if filtro_vend=='nao' %}selected{% endif %}>Sem responsável</option>
      {% for v in vendedores %}<option value="{{ v.id }}" {% if filtro_vend==(v.id|string) %}selected{% endif %}>{{ v.nome }}</option>{% endfor %}
    </select>
  </form>
  {% endif %}

  {% if gerencia %}
  <style>
  .etcfg{border:1px solid var(--borda);border-radius:12px;background:var(--card);margin:.2rem 0 1rem}
  .etcfg>summary{cursor:pointer;padding:.7rem .9rem;font-weight:600;font-size:.9rem;list-style:none;
    display:flex;align-items:center;gap:.5rem;user-select:none}
  .etcfg>summary::-webkit-details-marker{display:none}
  .etcfg>summary::after{content:'▾';margin-left:auto;color:var(--txt-mut);transition:transform .18s}
  .etcfg[open]>summary::after{transform:rotate(180deg)}
  .etbody{padding:0 .9rem .9rem;border-top:1px solid var(--borda)}
  .ethint{color:var(--txt-mut);font-size:.8rem;margin:.7rem 0 .8rem}
  .etlist{display:flex;flex-direction:column;gap:.4rem}
  .etrow{display:flex;align-items:center;gap:.35rem;flex-wrap:wrap}
  .etrow .lock,.etrow .grip{width:1.1rem;text-align:center;color:var(--txt-mut);flex-shrink:0}
  .etin{flex:1;min-width:130px;padding:.4rem .55rem;border-radius:8px;border:1px solid #333;
    background:var(--bg);color:var(--txt);font-family:inherit;font-size:.86rem}
  .etin:focus{outline:none;border-color:var(--verde)}
  .etn{font-size:.72rem;color:var(--txt-mut);white-space:nowrap;font-variant-numeric:tabular-nums;min-width:52px}
  .etb{border:1px solid var(--borda);background:var(--card-2);color:var(--txt);border-radius:7px;
    width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;
    font-size:.85rem;line-height:1;flex-shrink:0}
  .etb:hover:not(:disabled){border-color:var(--verde)}
  .etb.del:hover:not(:disabled){border-color:var(--coral);color:var(--coral)}
  .etb:disabled{opacity:.32;cursor:not-allowed}
  .etadd{display:flex;gap:.4rem;margin-top:.8rem;flex-wrap:wrap}
  .etadd .etin{min-width:160px}
  </style>
  <details class="etcfg">
    <summary>⚙️ Editar etapas do funil</summary>
    <div class="etbody">
      <p class="ethint">Renomeie no campo e clique ✓. Reordene com ◀ ▶. O ✕ remove — só quando a etapa
        estiver <b>sem leads</b>. 🔒 = etapa fixa (entrada/resultado): pode renomear, mas não remover.</p>
      <div class="etlist">
        {% for e in etapas %}
        <form method="post" class="etrow">
          {% if e.fixa %}<span class="lock" title="Etapa fixa (entrada/resultado)">🔒</span>{% else %}<span class="grip">⠿</span>{% endif %}
          <input class="etin" name="rotulo" value="{{ e.rotulo }}" maxlength="40" aria-label="Nome da etapa">
          <span class="etn">{{ e.n }} lead{{ '' if e.n == 1 else 's' }}</span>
          <button class="etb" formaction="/painel/prospeccao/etapas/{{ e.id }}/renomear" title="Salvar nome">✓</button>
          <button class="etb" formaction="/painel/prospeccao/etapas/{{ e.id }}/mover" name="dir" value="esq" {% if e.fixa %}disabled{% endif %} title="Mover pra esquerda">◀</button>
          <button class="etb" formaction="/painel/prospeccao/etapas/{{ e.id }}/mover" name="dir" value="dir" {% if e.fixa %}disabled{% endif %} title="Mover pra direita">▶</button>
          <button class="etb del" formaction="/painel/prospeccao/etapas/{{ e.id }}/remover"
                  {% if e.fixa or e.n > 0 %}disabled{% endif %}
                  title="{% if e.fixa %}Etapa fixa — não remove{% elif e.n > 0 %}Mova os leads primeiro{% else %}Remover etapa{% endif %}"
                  onclick="return confirm('Remover a etapa “{{ e.rotulo }}”?')">✕</button>
        </form>
        {% endfor %}
      </div>
      <form method="post" action="/painel/prospeccao/etapas/nova" class="etadd">
        <input class="etin" name="rotulo" placeholder="Nova etapa (ex.: Reunião marcada)" maxlength="40">
        <button class="pbtn">＋ Adicionar etapa</button>
      </form>
    </div>
  </details>
  {% endif %}

  <!-- abas de status (só mobile) -->
  <div class="kbtabs" id="kbtabs">
    {% for s, rot in status %}<button type="button" class="kbtab" data-tab="{{ s }}" onclick="kbTab('{{ s }}')">{{ rot }} <span class="c">{{ colunas[s]|length }}</span></button>{% endfor %}
  </div>

  <div class="kbrow" id="kbrow">
    {% for s, rot in status %}
    <div class="kbcol" data-status="{{ s }}" ondragover="kbOver(event)" ondragleave="kbLeave(event)" ondrop="kbDrop(event,'{{ s }}')">
      <h4><span>{{ rot }}</span><span class="kbcnt">{{ colunas[s]|length }}</span></h4>
      <div class="kbdrop">
        {% for c in colunas[s] %}
        <div class="kbcard" draggable="true" data-id="{{ c.id }}" ondragstart="kbDrag(event,{{ c.id }})" ondragend="kbEnd(event)"
             onclick="if(!window._kbMoved)kbAbrirLead(event,{{ c.id }},this)">
          <div style="display:flex;align-items:center;gap:.4rem"><span class="tdot" title="{{ c.temperatura }}" style="background:{{ temp_cor[c.temperatura] }}"></span><span class="emp">{{ c.empresa }}</span><button type="button" class="kbx" style="flex:none" title="Excluir lead" onclick="kbExcluir(event,{{ c.id }})">✕</button></div>
          {% if c.segmento or c.cidade %}<div class="sub">{% if c.segmento %}{{ c.segmento }}{% endif %}{% if c.cidade %} · {{ c.cidade }}{% if c.uf %}/{{ c.uf }}{% endif %}{% endif %}</div>{% endif %}
          {% if c.campanha or c.chip_apelido %}<div class="camp">{% if c.campanha %}📣 {{ c.campanha }}{% endif %}{% if c.chip_apelido %}<span class="chip">{% if c.campanha %} · {% endif %}📱 {{ c.chip_apelido }}</span>{% endif %}</div>{% endif %}
          {% if c.tem_whatsapp or c.tem_email or c.tem_instagram or c.enriquecido %}<div class="kbch">{% if c.tem_whatsapp %}{% if c.conv_whatsapp %}<button type="button" class="kbb" onclick="kbAbrirChat(event,{{ c.conv_whatsapp }},'conversas',this)" title="Abrir a conversa de WhatsApp">💬</button>{% else %}<span title="WhatsApp">💬</span>{% endif %}{% endif %}{% if c.tem_email %}{% if c.conv_email %}<button type="button" class="kbb" onclick="kbAbrirChat(event,{{ c.conv_email }},'emails',this)" title="Abrir a conversa de e-mail">✉️</button>{% else %}<span title="E-mail">✉️</span>{% endif %}{% endif %}{% if c.tem_instagram %}{% if c.conv_instagram %}<button type="button" class="kbb" onclick="kbAbrirChat(event,{{ c.conv_instagram }},'conversas',this)" title="Abrir a conversa de Instagram">📸</button>{% else %}<span title="Instagram">📸</span>{% endif %}{% endif %}{% if c.enriquecido and not (c.tem_whatsapp or c.tem_email or c.tem_instagram) %}<span class="mut" title="Verificado, sem canal encontrado">— sem canal</span>{% endif %}</div>{% endif %}
          <div class="ft">{% if c.valor %}<span style="font-size:.76rem;color:var(--verde-claro)">{{ brl(c.valor) }}</span>{% else %}<span></span>{% endif %}{% if c.proximo %}<span class="mut" style="font-size:.72rem">📅 {{ c.proximo.strftime('%d/%m') }}</span>{% endif %}</div>
          {% if pode_atribuir %}<select class="kbvend" onclick="event.stopPropagation()" onchange="kbAtribuirVendedor(this,{{ c.id }})" data-prev="{{ c.vendedor_id or '' }}">
            <option value=""{% if not c.vendedor_id %} selected{% endif %}>— sem responsável —</option>
            {% for v in vendedores %}<option value="{{ v.id }}"{% if c.vendedor_id==v.id %} selected{% endif %}>👤 {{ v.nome }}</option>{% endfor %}
          </select>{% elif gerencia and c.vendedor %}<div class="mut" style="font-size:.72rem;margin-top:.28rem">👤 {{ c.vendedor }}</div>{% endif %}
          {# mesmo telefone, outro chip: são dois leads de propósito (cada chip responde
             pelo seu número), mas quem olha o funil precisa saber — senão dois
             vendedores negociam com a mesma pessoa, cada um com um preço. #}
          {% if c.gemeo %}<div class="kbgem" onclick="event.stopPropagation()">⚠️ {{ c.gemeo }}{% if c.gemeo_lead %} <a href="/painel/prospeccao/{{ c.gemeo_lead }}">Abrir →</a>{% endif %}</div>{% endif %}
        </div>
        {% else %}<div class="kbempty">vazio</div>{% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
</div>

<style>
.kbgem{margin-top:.4rem;font-size:.7rem;line-height:1.35;color:#e0b45f;background:rgba(224,180,95,.10);
  border:1px solid rgba(224,180,95,.32);border-radius:8px;padding:.3rem .42rem;cursor:default}
.kbgem a{color:#e0b45f;text-decoration:underline;white-space:nowrap}
.kbch{display:flex;gap:.35rem;margin-top:.3rem;font-size:.82rem;align-items:center}
.kbch .mut{font-size:.7rem}
/* o selo vira BOTÃO só quando existe conversa de verdade (não só telefone/e-mail
   cadastrado): halo verde sutil + cursor de clique é a única diferença visual —
   quem nunca conversou continua com o span apagado de sempre, sem clique. */
.kbch .kbb{background:var(--neon-fundo);border:1px solid var(--neon-borda);border-radius:7px;
  padding:.04rem .3rem;line-height:1.3;cursor:pointer;font-size:1em;width:auto;margin:0}
.kbch .kbb:hover{border-color:var(--verde)}
/* o balão do chat — SÓ mensagens, nada do resto do hub de Comunicação. Nasce em
   fixed (calculado em JS a partir do botão), então o CSS aqui não precisa
   posicionar nada em relação ao card — só desenhar o balão em si. */
.chatpop{position:fixed;z-index:90;width:336px;max-width:calc(100vw - 16px);max-height:70vh;
  background:var(--card);border:1px solid var(--borda);border-radius:14px;overflow:hidden;
  box-shadow:0 18px 46px rgba(0,0,0,.5);display:flex;flex-direction:column}
/* ✕ de fechar — fora do fluxo do cabeçalho, cravado no canto (não empurrado por
   flex): o título nunca o empurra pra baixo/quebra de linha, e fica no mesmo
   lugar sempre, em qualquer balão (chat ou resumo do lead). `margin:0` vence o
   `button{width:100%;margin-top:1.4rem}` global (pros botões de formulário de
   login/cadastro) — sem isso o botão herdava 1.4rem de margem e nascia ~22px
   mais abaixo do canto. Mesma causa da busca da Comunicação (✕ que esmagava
   o campo) — dessa vez pegou ANTES de virar bug visível. */
.pop-close{position:absolute;top:.5rem;right:.5rem;z-index:2;width:24px;height:24px;
  margin:0;display:flex;align-items:center;justify-content:center;padding:0;
  background:var(--card-2);border:1px solid var(--borda);border-radius:50%;
  color:var(--txt-mut);cursor:pointer;font-size:.72rem;line-height:1}
.pop-close:hover{color:var(--txt);border-color:var(--coral);background:rgba(224,87,79,.14)}
.cp-h{display:flex;align-items:center;gap:.5rem;padding:.55rem 2.1rem .55rem .7rem;border-bottom:1px solid var(--borda);flex:none}
.cp-h .av{width:26px;height:26px;border-radius:8px;background:#13251d;color:var(--verde-claro);
  display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.68rem;flex:none}
.cp-h b{font-size:.85rem}
.cp-h small{display:block;color:var(--txt-mut);font-size:.68rem}
.cp-mais{display:block;text-align:center;padding:.42rem;font-size:.72rem;color:var(--txt-mut);
  border-top:1px solid var(--borda);text-decoration:none;flex:none}
.cp-mais:hover{color:var(--verde-claro)}
.cx-empty{padding:1.6rem 1rem;text-align:center;color:var(--txt-mut);font-size:.84rem}
.cx-msgs{flex:1;overflow-y:auto;padding:.7rem;display:flex;flex-direction:column;gap:.45rem;min-height:120px}
.cx-m{max-width:85%;align-self:flex-end;background:#123028;border:1px solid #1d5741;border-radius:12px;
  border-bottom-right-radius:4px;padding:.42rem .6rem;font-size:.82rem;line-height:1.42}
.cx-m .meta{display:block;color:var(--txt-mut);font-size:.64rem;margin-top:.25rem;text-align:right}
.cx-m.cin{align-self:flex-start;background:var(--card-2);border-color:var(--borda)}
.cx-m.cbot{align-self:flex-start;background:#1c1428;border-color:#4a3163}
.cx-comp{border-top:1px solid var(--borda);padding:.5rem .6rem;display:flex;gap:.4rem;align-items:flex-end;flex:none}
.cx-comp textarea{flex:1;resize:none;background:var(--bg);border:1px solid var(--borda);color:var(--txt);
  border-radius:9px;padding:.4rem .55rem;font:inherit;font-size:.8rem;height:2.1rem;margin:0;width:auto}
.cx-comp button{background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;
  padding:0 .8rem;font-weight:600;cursor:pointer;font-size:.8rem;width:auto;margin:0;flex:none}
.cx-stub{border-top:1px solid var(--borda);padding:.55rem .7rem;color:var(--txt-mut);font-size:.76rem;flex:none}
.cx-stub .lbl2{display:inline-block;font-size:.6rem;padding:.04rem .38rem;border-radius:999px;
  background:#241634;color:#c9a3e0;border:1px solid #4a3163;margin-left:.3rem}
/* width:auto + margin:0 vencem o `button{width:100%;margin-top:1.4rem}` global
   (mesma causa raiz já corrigida no ✕ da busca e no ✕ de fechar os balões):
   sem isso o ✕ de excluir nascia ~22px mais abaixo da linha do nome, flutuando
   solto perto do rodapé do card em vez de ficar ao lado do nome. */
.kbx{width:auto;margin:0;background:none;border:0;color:#6b6b6b;cursor:pointer;font-size:.82rem;line-height:1;padding:.1rem .25rem;border-radius:6px;opacity:.55}
.kbx:hover{opacity:1;color:var(--coral);background:rgba(224,87,79,.12)}
/* trocar/atribuir vendedor direto no card — só o dono vê (mesma regra de
   pode_atribuir da ficha completa); quem só tem gerência continua vendo o
   nome como texto, igual sempre foi. */
.kbvend{width:100%;margin-top:.3rem;background:var(--bg);border:1px solid var(--borda);
  color:var(--txt-mut);border-radius:6px;padding:.2rem .35rem;font-size:.72rem;font-family:inherit;
  cursor:pointer}
.kbvend:hover{border-color:var(--neon-borda);color:var(--txt)}
.kbvend:focus{outline:none;border-color:var(--verde)}
/* o balão do LEAD — resumo pra decidir a próxima ação (contato, valor, situação,
   últimas atividades). Mesma engenharia do balão de chat: nasce fixed, medido
   do próprio card, sem carregar a ficha inteira num iframe. Edição de cadastro,
   IA, decisor e orçamento continuam só na ficha completa (link no rodapé). */
.leadpop{position:fixed;z-index:90;width:378px;max-width:calc(100vw - 16px);max-height:70vh;
  background:var(--card);border:1px solid var(--borda);border-radius:14px;overflow:hidden;
  box-shadow:0 18px 46px rgba(0,0,0,.5);display:flex;flex-direction:column}
.lp-h{padding:.75rem 2.1rem .65rem .85rem;border-bottom:1px solid var(--borda);flex:none}
.lp-h .top{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap}
.lp-h h3{font-size:1rem;margin:0}
.lp-h .sub{color:var(--txt-mut);font-size:.78rem;margin-top:.2rem}
.lp-canais{display:flex;gap:.35rem;flex-wrap:wrap;margin-top:.5rem}
.lp-canal{display:inline-flex;align-items:center;gap:.25rem;font-size:.7rem;padding:.14rem .5rem;border-radius:999px;
  border:1px solid var(--verde);background:rgba(62,224,166,.10);color:var(--verde-claro)}
.lp-acoes{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;padding:.65rem .85rem;border-bottom:1px solid var(--borda);flex:none}
.lp-ab{background:var(--neon-fundo);border:1px solid var(--neon-borda);border-radius:8px;padding:.32rem .65rem;
  font-size:.78rem;color:var(--txt);cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:.3rem}
.lp-ab:hover{border-color:var(--verde)}
.lp-status{margin-left:auto}
.lp-status select{background:var(--bg);border:1px solid var(--borda);color:var(--txt);border-radius:999px;
  padding:.28rem .6rem;font-size:.76rem;width:auto;margin:0}
.lp-body{padding:.7rem .85rem;overflow-y:auto;flex:1}
.lp-sh{display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem}
.lp-sh b{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut)}
/* margin:0 0 0 auto (não só margin-left:auto) — sem zerar o topo/baixo, o
   `button{margin-top:1.4rem}` global vazava e empurrava o botão ~22px pra
   baixo dentro de ".lp-sh" (mesma causa do ✕ de excluir e dos ✕ dos balões). */
.lp-edit-btn{margin:0 0 0 auto;background:none;border:1px solid var(--borda);color:var(--txt-mut);border-radius:7px;
  padding:.15rem .5rem;font-size:.7rem;cursor:pointer;width:auto}
.lp-edit-btn:hover{color:var(--txt);border-color:var(--verde)}
.lp-grid{display:grid;grid-template-columns:1fr 1fr;gap:.5rem .8rem;font-size:.8rem}
.lp-grid .k{color:var(--txt-mut);font-size:.68rem;text-transform:uppercase;letter-spacing:.04em}
.lp-grid .v{margin-top:.1rem;color:var(--txt)}
.lp-grid .full{grid-column:1 / -1}
.lp-sec2{margin-top:.9rem}
.lp-ativ{display:flex;gap:.5rem;padding:.4rem 0;border-top:1px solid var(--borda);font-size:.8rem}
.lp-ativ:first-child{border-top:0}
.lp-ativ .dot2{width:7px;height:7px;border-radius:50%;margin-top:.4rem;flex-shrink:0}
.lp-ativ .qd{color:var(--txt-mut);font-size:.72rem}
.lp-mais{display:block;text-align:center;padding:.55rem;font-size:.76rem;color:var(--txt-mut);
  border-top:1px solid var(--borda);text-decoration:none;flex:none}
.lp-mais:hover{color:var(--verde-claro)}
.lp-ed-grid{display:grid;grid-template-columns:1fr 1fr;gap:.55rem .7rem}
.lp-ed-grid .full{grid-column:1 / -1}
.lp-ed-grid label{display:block;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em;color:var(--txt-mut);margin-bottom:.2rem}
.lp-ed-grid input,.lp-ed-grid textarea{width:100%;background:var(--bg);border:1px solid var(--borda);color:var(--txt);
  border-radius:8px;padding:.35rem .55rem;font:inherit;font-size:.8rem;box-sizing:border-box;margin:0}
.lp-ed-grid textarea{resize:vertical;min-height:2.2rem}
.lp-ed-acoes{display:flex;gap:.5rem;margin-top:.8rem}
.lp-ed-acoes button{width:auto;margin:0;font-size:.8rem;cursor:pointer}
.lp-ed-salvar{background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;padding:.4rem .9rem;font-weight:600}
.lp-ed-cancelar{background:none;border:1px solid var(--borda);color:var(--txt-mut);border-radius:8px;padding:.4rem .9rem}
</style>

<script>
var _KB_STATUS={{ status|tojson }};
// SÓ AS MENSAGENS — não a tela de Comunicação inteira. O selo do canal só chama
// isto quando já existe conversa (ver conv_whatsapp/email/instagram no card).
// `event.stopPropagation()` evita abrir a ficha JUNTO (o card inteiro tem
// onclick pra isso), mesmo padrão do ✕ de excluir.
//
// Nasce em `position:fixed`, calculado do próprio botão — não em `position:
// absolute` dentro do card. O kanban é `.kbrow{overflow-x:auto}` no desktop, e
// overflow-x diferente de visible faz overflow-y virar auto por baixo dos panos
// (regra do CSS, não bug); um popover absoluto vazando pra baixo do card seria
// cortado pela própria coluna que rola. Fixed escapa disso de vez.
var _chatPop=null;
function kbFecharChat(){
  if(_chatPop){_chatPop.remove();_chatPop=null;}
  document.removeEventListener('click',_chatPopFora,true);
  document.removeEventListener('keydown',_chatPopEsc,true);
  window.removeEventListener('scroll',_chatPopRolou,true);
}
function _chatPopFora(e){if(_chatPop&&!_chatPop.contains(e.target))kbFecharChat();}
function _chatPopEsc(e){if(e.key==='Escape')kbFecharChat();}
// Rolar a página SOME com o balão, em vez de deixá-lo pra trás flutuando longe
// do card que ele veio. É `fixed` (não anda com a rolagem por design — foge do
// corte de overflow-x:auto da coluna), então "ficar pendurado no lugar errado"
// era pior que só fechar: fechado, o próximo clique abre de novo já no lugar
// certo. `capture:true` pega rolagem de QUALQUER contêiner (a coluna do
// kanban rola sozinha), não só da janela — scroll não borbulha por padrão.
//
// MAS: a lista de mensagens rola SOZINHA assim que abre, pra mostrar a
// última (`box.scrollTop=box.scrollHeight`, logo abaixo) — e rolar dentro dela
// pra ler o histórico é uso normal do balão, não um "saiu daqui". Sem o
// `contains` abaixo, o balão se fechava sozinho no instante em que as
// mensagens chegavam: o auto-scroll interno disparava este mesmo listener.
function _chatPopRolou(e){if(_chatPop&&_chatPop.contains(e.target))return;kbFecharChat();}
function cxEscK(s){var d=document.createElement('div');d.textContent=(s==null?'':s);return d.innerHTML;}
// Igual ao cxMsgsHtml do hub, só que enxuto: sem selo de entrega, sem cabeçalho
// de campanha — o balão é pra LER a conversa, não pra operar ela.
function kbMsgsHtml(d){
  if(!d.msgs||!d.msgs.length)return '<div class="cx-empty">Sem mensagens.</div>';
  var h='';
  d.msgs.forEach(function(m){
    var cls=(m.direcao==='in')?'cx-m cin':((m.autor==='bot')?'cx-m cbot':'cx-m');
    var corpo=cxEscK(m.corpo||m.cabecalho||'').replace(/\\n/g,'<br>');
    h+='<div class="'+cls+'">'+corpo+'<span class="meta">'+cxEscK(m.quem||'')+' · '+cxEscK(m.quando||'')+'</span></div>';
  });
  return h;
}
function kbAbrirChat(ev,convId,aba,btn){
  ev.stopPropagation();
  kbFecharChat();
  kbFecharLead();   // só um balão por vez — chat e resumo do lead se excluem
  var card=btn.closest('.kbcard');
  var nm=card?(card.querySelector('.emp')||{}).textContent:'';
  var r=btn.getBoundingClientRect();
  var pop=document.createElement('div');
  pop.className='chatpop';
  // Cabe na tela SEMPRE — é o bug que motivou isto: nascendo fixo em
  // `top:botão+6` sem olhar o que sobrava embaixo, um card perto do fim da
  // janela abria o balão parcialmente fora da tela. E como é `position:fixed`,
  // rolar a PÁGINA não revela o que ficou cortado (fixed não se move com a
  // rolagem) — por fora parecia "trava e não deixa ver as mensagens". Aqui
  // mede o espaço disponível pra cima e pra baixo do botão e escolhe o lado
  // com mais folga, com a altura do balão presa a esse espaço (a lista de
  // mensagens rola por dentro, `.cx-msgs{overflow-y:auto}` — o balão inteiro
  // continua sempre visível de ponta a ponta).
  var MARG=8, GAP=6, LARG=336;
  var abaixo=window.innerHeight-r.bottom-GAP-MARG, acima=r.top-GAP-MARG;
  if(abaixo>=220||abaixo>=acima){
    pop.style.top=(r.bottom+GAP)+'px';
    pop.style.maxHeight=Math.max(160,Math.min(460,abaixo))+'px';
  }else{
    pop.style.bottom=(window.innerHeight-r.top+GAP)+'px';
    pop.style.maxHeight=Math.max(160,Math.min(460,acima))+'px';
  }
  pop.style.left=Math.max(MARG,Math.min(r.left,window.innerWidth-LARG-MARG))+'px';
  pop.innerHTML='<button type="button" class="pop-close" title="Fechar" onclick="kbFecharChat()">✕</button>'
    +'<div class="cp-h"><span class="av">'+cxEscK((nm||'?').trim().slice(0,2).toUpperCase())+'</span>'
    +'<div><b>'+cxEscK(nm||'Conversa')+'</b><small>'+cxEscK(btn.title||'')+'</small></div></div>'
    +'<div class="cx-msgs" id="cp-msgs"><div class="cx-empty">Carregando…</div></div>'
    +'<div id="cp-comp"></div>'
    +'<a class="cp-mais" target="_blank" href="/painel/prospeccao/comunicacao?aba='+aba+'&abrir='+convId+'">Ver conversa completa ↗</a>';
  document.body.appendChild(pop);
  _chatPop=pop;
  // no mesmo clique que abriu, senão o próprio clique do botão já contaria como
  // "fora" e fecharia o balão antes de ele aparecer.
  setTimeout(function(){document.addEventListener('click',_chatPopFora,true);document.addEventListener('keydown',_chatPopEsc,true);
    window.addEventListener('scroll',_chatPopRolou,true);},0);
  fetch('/painel/prospeccao/comunicacao/thread/'+convId).then(function(r){return r.json();}).then(function(d){
    if(_chatPop!==pop)return;   // o popover foi trocado/fechado antes da resposta chegar
    var box=pop.querySelector('#cp-msgs');
    box.innerHTML=d.ok?kbMsgsHtml(d):'<div class="cx-empty">Não consegui abrir.</div>';
    box.scrollTop=box.scrollHeight;
    var comp=pop.querySelector('#cp-comp');
    if(d.pode_responder){
      comp.innerHTML='<div class="cx-comp"><textarea id="cp-input" rows="1" placeholder="Escreva uma resposta…"'
        +' onkeydown="if(event.key===\\'Enter\\'&&!event.shiftKey){event.preventDefault();kbResponderChat('+convId+');}"></textarea>'
        +'<button type="button" onclick="kbResponderChat('+convId+')">Enviar</button></div>';
    }else if(d.ok){
      comp.innerHTML='<div class="cx-stub">Responder por aqui <span class="lbl2">em breve</span></div>';
    }
  }).catch(function(){
    if(_chatPop===pop)pop.querySelector('#cp-msgs').innerHTML='<div class="cx-empty">Falha de rede.</div>';
  });
}
function kbResponderChat(convId){
  if(!_chatPop)return;
  var ta=_chatPop.querySelector('#cp-input');
  var texto=(ta.value||'').trim();
  if(!texto)return;
  ta.disabled=true;
  var body=new URLSearchParams();body.append('conversa_id',convId);body.append('texto',texto);
  fetch('/painel/prospeccao/comunicacao/responder',{method:'POST',body:body}).then(function(r){return r.json();}).then(function(d){
    if(!_chatPop)return;
    ta.disabled=false;
    if(!d.ok){alert(d.erro||'Não consegui enviar.');return;}
    ta.value='';
    fetch('/painel/prospeccao/comunicacao/thread/'+convId).then(function(r){return r.json();}).then(function(d2){
      if(!_chatPop)return;
      var box=_chatPop.querySelector('#cp-msgs');
      if(box&&d2.ok){box.innerHTML=kbMsgsHtml(d2);box.scrollTop=box.scrollHeight;}
    });
  }).catch(function(){if(ta)ta.disabled=false;alert('Erro de conexão.');});
}
// O balão do LEAD — resumo pra decidir a próxima ação (ligar, chamar no
// WhatsApp, mudar a situação, ver o que aconteceu por último). Antes o clique
// no card abria uma gaveta de 1080px com a ficha INTEIRA num iframe (edição de
// cadastro, IA de primeiro contato, decisor Credify, orçamento) — pesado pra
// só decidir o que fazer agora. Isso fica só na ficha completa, atrás do link
// "Ver ficha completa". Mesmo mecanismo de posicionamento/fechar do balão de
// chat (ver comentário em kbAbrirChat) — duplicado de propósito, não
// compartilhado: são popovers independentes, cada um fecha só o que é seu.
var _leadPop=null;
function kbFecharLead(){
  if(_leadPop){_leadPop.remove();_leadPop=null;}
  document.removeEventListener('click',_leadPopFora,true);
  document.removeEventListener('keydown',_leadPopEsc,true);
  window.removeEventListener('scroll',_leadPopRolou,true);
}
function _leadPopFora(e){if(_leadPop&&!_leadPop.contains(e.target))kbFecharLead();}
function _leadPopEsc(e){if(e.key==='Escape')kbFecharLead();}
function _leadPopRolou(e){if(_leadPop&&_leadPop.contains(e.target))return;kbFecharLead();}
function kbAbrirLead(ev,id,cardEl){
  if(ev)ev.stopPropagation();
  kbFecharChat();
  kbFecharLead();
  var r=cardEl.getBoundingClientRect();
  var pop=document.createElement('div');
  pop.className='leadpop';
  var MARG=8, GAP=6, LARG=378;
  var abaixo=window.innerHeight-r.bottom-GAP-MARG, acima=r.top-GAP-MARG;
  if(abaixo>=260||abaixo>=acima){
    pop.style.top=(r.bottom+GAP)+'px';
    pop.style.maxHeight=Math.max(200,Math.min(560,abaixo))+'px';
  }else{
    pop.style.bottom=(window.innerHeight-r.top+GAP)+'px';
    pop.style.maxHeight=Math.max(200,Math.min(560,acima))+'px';
  }
  pop.style.left=Math.max(MARG,Math.min(r.left,window.innerWidth-LARG-MARG))+'px';
  pop.innerHTML='<button type="button" class="pop-close" title="Fechar" onclick="kbFecharLead()">✕</button><div class="cx-empty">Carregando…</div>';
  document.body.appendChild(pop);
  _leadPop=pop;
  setTimeout(function(){document.addEventListener('click',_leadPopFora,true);document.addEventListener('keydown',_leadPopEsc,true);
    window.addEventListener('scroll',_leadPopRolou,true);},0);
  fetch('/painel/prospeccao/'+id+'/resumo').then(function(r){return r.json();}).then(function(d){
    if(_leadPop!==pop)return;
    if(!d.ok){pop.innerHTML='<div class="cx-empty">Não consegui abrir.</div>';return;}
    pop._d=d;
    pop.innerHTML=kbLeadHtml(d,id);
  }).catch(function(){if(_leadPop===pop)pop.innerHTML='<div class="cx-empty">Falha de rede.</div>';});
}
function kbLeadHtml(d,id){
  var h='<button type="button" class="pop-close" title="Fechar" onclick="kbFecharLead()">✕</button>'
    +'<div class="lp-h"><div class="top">'
    +'<span class="tdot" style="width:12px;height:12px;background:'+cxEscK(d.temp_cor||'#7a7a7a')+'"></span>'
    +'<h3>'+cxEscK(d.empresa||'Lead')+'</h3>';
  if(d.temperatura)h+='<span class="tpill" style="background:'+cxEscK(d.temp_pill[0])+';color:'+cxEscK(d.temp_pill[1])+'">'+cxEscK(d.temperatura)+'</span>';
  h+='</div>';
  var sub=[d.segmento,(d.cidade?(d.cidade+(d.uf?('/'+d.uf):'')):'')].filter(Boolean).join(' · ');
  if(d.vendedor_nome)sub+=(sub?' · ':'')+'👤 '+d.vendedor_nome;
  if(sub)h+='<div class="sub">'+cxEscK(sub)+'</div>';
  if(d.canais_contato&&d.canais_contato.length){
    h+='<div class="lp-canais">';
    d.canais_contato.forEach(function(ch){h+='<span class="lp-canal">'+cxEscK(ch.ic)+' '+cxEscK(ch.label)+(ch.respondeu?' ✓':'')+'</span>';});
    h+='</div>';
  }
  h+='</div><div class="lp-acoes">';
  if(d.tel_link)h+='<a class="lp-ab" href="'+cxEscK(d.tel_link)+'">📞 Ligar</a>';
  if(d.zap_link)h+='<a class="lp-ab" href="'+cxEscK(d.zap_link)+'" target="_blank" rel="noopener">💬 WhatsApp</a>';
  if(d.insta_url)h+='<a class="lp-ab" href="'+cxEscK(d.insta_url)+'" target="_blank" rel="noopener">📷 Instagram</a>';
  if(d.maps_url)h+='<a class="lp-ab" href="'+cxEscK(d.maps_url)+'" target="_blank" rel="noopener">🗺️ Mapa</a>';
  h+='<div class="lp-status"><select onchange="kbLeadStatus(this,'+id+')" data-prev="'+cxEscK(d.status||'')+'">';
  (_KB_STATUS||[]).forEach(function(s){h+='<option value="'+cxEscK(s[0])+'"'+(s[0]===d.status?' selected':'')+'>'+cxEscK(s[1])+'</option>';});
  h+='</select></div></div><div class="lp-body">'
    +'<div id="lp-view">'+kbLeadDadosHtml(d)+kbLeadHistHtml(d)+'</div>'
    +'<div id="lp-edit" style="display:none">'+kbLeadEditHtml(d,id)+'</div>'
    +'</div>'
    +'<a class="lp-mais" target="_blank" href="/painel/prospeccao/'+id+'">Ver ficha completa ↗</a>';
  return h;
}
// "Dados" no resumo do balão: os mesmos campos que a seção "Dados" da ficha
// completa mostra, MENOS o que é enriquecimento automático (sócio, regime,
// porte, Receita) — aquilo não é algo que se corrige rápido, fica só na ficha.
function kbLeadDadosHtml(d){
  var h='<div class="lp-sh"><b>Dados</b><button type="button" class="lp-edit-btn" onclick="kbLeadEditar()">✎ Editar</button></div><div class="lp-grid">';
  if(d.contato)h+='<div><div class="k">Contato</div><div class="v">'+cxEscK(d.contato)+(d.cargo?(' · '+cxEscK(d.cargo)):'')+'</div></div>';
  if(d.doc_fmt)h+='<div><div class="k">'+cxEscK(d.doc_rot||'Documento')+'</div><div class="v">'+cxEscK(d.doc_fmt)+'</div></div>';
  if(d.telefone)h+='<div><div class="k">Telefone</div><div class="v">'+cxEscK(d.telefone)+'</div></div>';
  if(d.whatsapp)h+='<div><div class="k">WhatsApp</div><div class="v">'+cxEscK(d.whatsapp)+'</div></div>';
  if(d.email)h+='<div><div class="k">E-mail</div><div class="v">'+cxEscK(d.email)+'</div></div>';
  if(d.instagram)h+='<div><div class="k">Instagram</div><div class="v">'+cxEscK(d.instagram)+'</div></div>';
  if(d.site_url)h+='<div><div class="k">Site</div><div class="v"><a href="'+cxEscK(d.site_url)+'" target="_blank" rel="noopener" style="color:var(--verde-claro)">'+cxEscK(d.site_dominio||d.site_url)+'</a></div></div>';
  if(d.valor_fmt)h+='<div><div class="k">Valor estimado</div><div class="v">'+cxEscK(d.valor_fmt)+'</div></div>';
  if(d.obs)h+='<div class="full"><div class="k">Observação</div><div class="v">'+cxEscK(d.obs)+'</div></div>';
  return h+'</div>';
}
function kbLeadHistHtml(d){
  if(!d.atividades||!d.atividades.length)return '';
  var h='<div class="lp-sec2"><div class="lp-sh"><b>Histórico</b></div>';
  d.atividades.forEach(function(a){
    h+='<div class="lp-ativ"><span class="dot2" style="background:'+cxEscK(a.cor||'#7a7a7a')+'"></span><div>'
      +'<div>'+cxEscK(a.tipo_rot||'')+(a.resultado_rot?' — '+cxEscK(a.resultado_rot):'')+(a.descricao?': '+cxEscK(a.descricao):'')+'</div>'
      +'<div class="qd">'+cxEscK(a.quando||'')+'</div></div></div>';
  });
  return h+'</div>';
}
function _lpVal(v){return cxEscK(v==null?'':v);}
function kbLeadEditHtml(d,id){
  return '<div class="lp-sh"><b>Editando os dados</b></div><div class="lp-ed-grid">'
    +'<div><label>Contato</label><input id="lp-ed-contato" value="'+_lpVal(d.contato)+'"></div>'
    +'<div><label>Cargo</label><input id="lp-ed-cargo" value="'+_lpVal(d.cargo)+'"></div>'
    +'<div><label>Telefone</label><input id="lp-ed-telefone" value="'+_lpVal(d.telefone)+'"></div>'
    +'<div><label>WhatsApp</label><input id="lp-ed-whatsapp" value="'+_lpVal(d.whatsapp)+'"></div>'
    +'<div><label>E-mail</label><input id="lp-ed-email" value="'+_lpVal(d.email)+'"></div>'
    +'<div><label>Instagram</label><input id="lp-ed-instagram" value="'+_lpVal(d.instagram)+'"></div>'
    +'<div class="full"><label>Site</label><input id="lp-ed-site" value="'+_lpVal(d.site_url)+'"></div>'
    +'<div class="full"><label>Valor estimado</label><input id="lp-ed-valor" value="'+_lpVal(d.valor_edit)+'"></div>'
    +'<div class="full"><label>Observação</label><textarea id="lp-ed-obs">'+_lpVal(d.obs)+'</textarea></div>'
    +'</div><div class="lp-ed-acoes">'
    +'<button type="button" class="lp-ed-salvar" onclick="kbLeadSalvar('+id+')">Salvar</button>'
    +'<button type="button" class="lp-ed-cancelar" onclick="kbLeadCancelarEdicao()">Cancelar</button></div>';
}
function kbLeadEditar(){
  var v=document.getElementById('lp-view'), e=document.getElementById('lp-edit');
  if(v)v.style.display='none';
  if(e)e.style.display='block';
}
function kbLeadCancelarEdicao(){
  var v=document.getElementById('lp-view'), e=document.getElementById('lp-edit');
  if(e)e.style.display='none';
  if(v)v.style.display='block';
}
// Rota PRÓPRIA (não a /editar da ficha completa — ver comentário na rota):
// só os campos que o balão mostra. Depois de salvar, busca o /resumo de novo
// e redesenha (mesma função que abriu o balão), voltando pro modo leitura já
// com o dado novo.
function kbLeadSalvar(id){
  var btn=_leadPop&&_leadPop.querySelector('.lp-ed-salvar');
  if(btn){btn.disabled=true;btn.textContent='Salvando…';}
  var body=new URLSearchParams();
  body.append('contato',document.getElementById('lp-ed-contato').value);
  body.append('cargo',document.getElementById('lp-ed-cargo').value);
  body.append('telefone',document.getElementById('lp-ed-telefone').value);
  body.append('whatsapp',document.getElementById('lp-ed-whatsapp').value);
  body.append('email',document.getElementById('lp-ed-email').value);
  body.append('instagram',document.getElementById('lp-ed-instagram').value);
  body.append('site_url',document.getElementById('lp-ed-site').value);
  body.append('valor',document.getElementById('lp-ed-valor').value);
  body.append('obs',document.getElementById('lp-ed-obs').value);
  fetch('/painel/prospeccao/'+id+'/editar-rapido',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json();}).then(function(d){
      if(!_leadPop)return;
      if(!d.ok){if(btn){btn.disabled=false;btn.textContent='Salvar';}alert('Não consegui salvar.');return;}
      return fetch('/painel/prospeccao/'+id+'/resumo').then(function(r){return r.json();}).then(function(d2){
        if(!_leadPop||!d2.ok)return;
        _leadPop._d=d2;
        _leadPop.innerHTML=kbLeadHtml(d2,id);
      });
    }).catch(function(){if(btn){btn.disabled=false;btn.textContent='Salvar';}alert('Falha de rede.');});
}
// A mesma rota da ficha completa (fichaStatus lá dentro) — só troca a situação
// e, se deu certo, move o card pra coluna nova no board por trás (mesma
// varredura de contagem do drag-and-drop) e fecha o balão: o resultado
// (card na coluna nova) já fica visível sem o balão flutuando desalinhado.
function kbLeadStatus(sel,id){
  var novo=sel.value, prev=sel.getAttribute('data-prev')||'';
  var body=new URLSearchParams();body.append('status',novo);
  fetch('/painel/prospeccao/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert('Não consegui mudar a situação.');sel.value=prev;return;}
      var card=document.querySelector('.kbcard[data-id="'+id+'"]');
      var colNova=document.querySelector('.kbcol[data-status="'+novo+'"] .kbdrop');
      if(card&&colNova){var vazio=colNova.querySelector('.kbempty');if(vazio)vazio.remove();colNova.appendChild(card);}
      _kbAposMoverStatus(d);
      kbFecharLead();
    }).catch(function(){alert('Falha de rede.');sel.value=prev;});
}
function kbTab(s){document.querySelectorAll('.kbcol').forEach(function(c){c.classList.toggle('show',c.getAttribute('data-status')===s);});
  document.querySelectorAll('.kbtab').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-tab')===s);});}
(function(){var cols=document.querySelectorAll('#kbrow .kbcol');var alvo='novo';
  for(var i=0;i<cols.length;i++){if(cols[i].querySelectorAll('.kbcard').length){alvo=cols[i].getAttribute('data-status');break;}}
  kbTab(alvo);})();
window._kbMoved=false;
function kbDrag(ev,id){ev.dataTransfer.setData('text/plain',id);ev.dataTransfer.effectAllowed='move';window._kbDragEl=ev.currentTarget;setTimeout(function(){ev.currentTarget.style.opacity='.35';},0);}
function kbEnd(ev){ev.currentTarget.style.opacity='';setTimeout(function(){window._kbMoved=false;},60);}
function kbOver(ev){ev.preventDefault();ev.currentTarget.classList.add('dragover');}
function kbLeave(ev){ev.currentTarget.classList.remove('dragover');}
// Contagens das colunas + placeholder "vazio" depois que um card muda de status —
// usado tanto pelo drag-and-drop (kbDrop) quanto pela troca de situação no balão
// resumo do lead (kbLeadStatus), pra não duplicar a mesma varredura duas vezes.
function _kbAposMoverStatus(d){
  if(!d.ok){location.reload();return;}
  document.querySelectorAll('.kbcol').forEach(function(col){var n=col.querySelectorAll('.kbcard').length;
    var chip=col.querySelector('.kbcnt');if(chip)chip.textContent=n;
    var tabc=document.querySelector('.kbtab[data-tab="'+col.getAttribute('data-status')+'"] .c');if(tabc)tabc.textContent=n;
    var dp=col.querySelector('.kbdrop');if(n===0&&!dp.querySelector('.kbempty')){var e=document.createElement('div');e.className='kbempty';e.textContent='vazio';dp.appendChild(e);}});
}
function kbDrop(ev,status){ev.preventDefault();ev.currentTarget.classList.remove('dragover');
  var id=ev.dataTransfer.getData('text/plain');var card=window._kbDragEl;if(!id||!card)return;window._kbMoved=true;
  var drop=ev.currentTarget.querySelector('.kbdrop');var emp=drop.querySelector('.kbempty');if(emp)emp.remove();drop.appendChild(card);
  var body=new URLSearchParams();body.append('status',status);
  fetch('/painel/prospeccao/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json();}).then(_kbAposMoverStatus).catch(function(){location.reload();});}

// ---- captação inline (sem reload) ----
var TEMPCOR={frio:'#5b9bd5',morno:'var(--ambar)',quente:'var(--coral)'};
function jsEsc(s){return (s||'').replace(/[&<>"]/g,function(c){return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c];});}
function jsBrl(c){c=c||0;var s=(c/100).toFixed(2).split('.');var i=s[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g,'.');return 'R$ '+i+','+s[1];}
function cardGo(ev,id,el){if(!window._kbMoved)kbAbrirLead(ev,id,el);}
function enrqLote(){var b=document.getElementById('enrq-btn'),m=document.getElementById('enrq-msg');if(!b)return;b.disabled=true;var t=b.textContent;b.textContent='Verificando…';m.textContent='';
  fetch('/painel/prospeccao/enriquecer-lote',{method:'POST',headers:{'X-Requested-With':'fetch'},body:new FormData()}).then(function(r){return r.json();}).then(function(d){b.disabled=false;b.textContent=t;
    if(!d.ok){m.textContent=d.erro||'Não consegui.';return;}
    m.textContent=d.n?('Verificando '+d.n+' lead(s) em 2º plano — recarregue em ~1 min pra ver os canais.'):'Nada pra verificar (leads sem site ou já verificados).';}).catch(function(){b.disabled=false;b.textContent=t;m.textContent='Falha de rede.';});}
function kbExcluir(ev,id){ev.stopPropagation();ev.preventDefault();
  if(!confirm('Excluir este lead? A conversa/e-mail dele continua no inbox — só sai do funil.'))return;
  fetch('/painel/prospeccao/'+id+'/excluir',{method:'POST',headers:{'X-Requested-With':'fetch'},body:new FormData()})
    .then(function(r){return r.json();}).then(function(d){if(!d.ok){alert(d.erro||'Não consegui excluir.');return;}
      var card=document.querySelector('.kbcard[data-id="'+id+'"]');if(card&&card.parentNode)card.parentNode.removeChild(card);
      updCounts();}).catch(function(){alert('Falha de rede.');});}
function updCounts(){var tot=0;document.querySelectorAll('.kbcol').forEach(function(col){var n=col.querySelectorAll('.kbcard').length;tot+=n;var chip=col.querySelector('.kbcnt');if(chip)chip.textContent=n;var tc=document.querySelector('.kbtab[data-tab="'+col.getAttribute('data-status')+'"] .c');if(tc)tc.textContent=n;});var tn=document.getElementById('kb-total-n');if(tn)tn.textContent=tot;}
// esfumado da borda direita do quadro (.transborda) — só liga quando sobra
// coluna pra rolar E ainda não chegou no fim; sem isso a última coluna
// (ex.: "Perdido") corta na borda da tela sem nenhum aviso de que dá pra
// arrastar. Roda no load, no scroll/resize, e depois de inserir card novo.
function kbCheckScroll(){var el=document.getElementById('kbrow');if(!el)return;
  var temMais=(el.scrollWidth-el.clientWidth-el.scrollLeft)>4;
  el.classList.toggle('transborda',temMais);}
(function(){var el=document.getElementById('kbrow');if(!el)return;
  el.addEventListener('scroll',kbCheckScroll);
  window.addEventListener('resize',kbCheckScroll);
  kbCheckScroll();})();
// Mesma rota que a ficha completa já usa pra atribuir — só chamada direto do
// card, sem sair do funil. Guarda o valor anterior em data-prev pra voltar
// se der erro (o próprio <select> já mudou visualmente antes do fetch responder).
function kbAtribuirVendedor(sel,id){
  var novo=sel.value, prev=sel.getAttribute('data-prev')||'';
  var fd=new FormData();fd.append('vendedor_id',novo);
  fetch('/painel/prospeccao/'+id+'/atribuir',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert(d.erro||'Não consegui trocar o vendedor.');sel.value=prev;return;}
      sel.setAttribute('data-prev',novo);
    }).catch(function(){alert('Falha de rede.');sel.value=prev;});
}
function addCard(l){var col=document.querySelector('.kbcol[data-status="novo"]');if(!col)return;var drop=col.querySelector('.kbdrop');var e=drop.querySelector('.kbempty');if(e)e.remove();
  var cor=TEMPCOR[l.temperatura]||'#5b9bd5';
  var sub=(l.segmento||l.cidade)?('<div class="sub">'+(l.segmento?jsEsc(l.segmento):'')+(l.cidade?(' · '+jsEsc(l.cidade)+(l.uf?('/'+jsEsc(l.uf)):'')):'')+'</div>'):'';
  var camp=(l.campanha||l.chip_apelido)?('<div class="camp">'+(l.campanha?('📣 '+jsEsc(l.campanha)):'')
    +(l.chip_apelido?('<span class="chip">'+(l.campanha?' · ':'')+'📱 '+jsEsc(l.chip_apelido)+'</span>'):'')+'</div>'):'';
  var ft='<div class="ft">'+(l.valor?('<span style="font-size:.76rem;color:var(--verde-claro)">'+jsBrl(l.valor)+'</span>'):'<span></span>')+'<span></span></div>';
  var vd=l.vendedor?('<div class="mut" style="font-size:.72rem;margin-top:.28rem">👤 '+jsEsc(l.vendedor)+'</div>'):'';
  var html='<div class="kbcard" draggable="true" data-id="'+l.id+'" ondragstart="kbDrag(event,'+l.id+')" ondragend="kbEnd(event)" onclick="cardGo(event,'+l.id+',this)"><div style="display:flex;align-items:center;gap:.4rem"><span class="tdot" style="background:'+cor+'"></span><span class="emp">'+jsEsc(l.empresa)+'</span><button type="button" class="kbx" style="flex:none" title="Excluir lead" onclick="kbExcluir(event,'+l.id+')">✕</button></div>'+sub+camp+ft+vd+'</div>';
  drop.insertAdjacentHTML('afterbegin',html);updCounts();}
function capToggle(){var e=document.getElementById('captar');var vis=e.style.display!=='none';e.style.display=vis?'none':'block';if(!vis){var i=e.querySelector('.captab[data-tab=manual] input[name=empresa]');if(i)i.focus();e.scrollIntoView({behavior:'smooth',block:'nearest'});}}
function capTab(t){document.querySelectorAll('#captar .caba').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-tab')===t);});document.querySelectorAll('#captar .captab').forEach(function(d){d.style.display=(d.getAttribute('data-tab')===t)?'block':'none';});}
function capFetch(url,fd){return fetch(url,{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){return r.json();});}
function capManual(ev){ev.preventDefault();var f=ev.target;capFetch('/painel/prospeccao/novo',new FormData(f)).then(function(d){if(!d.ok){capToast(d.erro||'Erro',d.link_url?{url:d.link_url,label:d.link_label}:null);return;}if(d.estagio==='lead')addCard(d.lead);f.reset();capToast(d.msg||'Lead adicionado ao funil');}).catch(function(){capToast('Falha de rede');});return false;}
function capCnpj(){var f=document.getElementById('cap-manual');var cnpj=f.querySelector('[name=documento]').value.replace(/\\D/g,'');if(cnpj.length!==14){capToast('Pra puxar da Receita, o CNPJ precisa ter 14 dígitos');return;}
  capToast('Consultando Receita…');
  fetch('/painel/prospeccao/cnpj?cnpj='+cnpj,{headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){capToast('CNPJ não encontrado ('+(d.erro||'')+')');return;}var x=d.dados;
    function put(n,v,forca){var el=f.querySelector('[name='+n+']');if(el&&v&&(forca||!el.value))el.value=v;}
    put('empresa',x.nome_fantasia||x.razao_social,false);put('segmento',x.segmento,true);put('cidade',x.cidade,true);put('uf',x.uf,true);
    put('telefone',x.telefone,true);put('email',x.email,true);put('socio',x.socio,true);put('regime_tributario',x.regime_tributario,true);put('porte',x.porte,true);
    var rc=f.querySelector('[name=receita]');if(rc){try{rc.value=JSON.stringify(x);}catch(e){}}
    capToast('Dados da Receita preenchidos ✓');
  }).catch(function(){capToast('Falha de rede');});}
function capCsv(ev){ev.preventDefault();capFetch('/painel/prospeccao/captar/csv',new FormData(ev.target)).then(function(d){if(!d.ok){capToast('Erro no CSV');return;}capToast(d.msg||'Importado');setTimeout(function(){location.reload();},800);}).catch(function(){capToast('Falha de rede');});return false;}
function capBuscar(ev){ev.preventDefault();var f=ev.target;var btn=document.getElementById('cap-g-btn');if(btn){btn.disabled=true;btn.textContent='Buscando…';}
  capFetch('/painel/prospeccao/captar/buscar',new FormData(f)).then(function(d){if(btn){btn.disabled=false;btn.textContent='Buscar';}var box=document.getElementById('cap-res');
    if(!d.ok){box.innerHTML='<div class="mut" style="color:var(--ambar)">Não consegui buscar ('+(d.erro||'?')+'). Confira a chave/billing e tente de novo.</div>';return;}
    if(!d.itens.length){box.innerHTML='<div class="mut">Nada encontrado'+(d.n_redes?(' ('+d.n_redes+' rede(s) oculta(s))'):'')+'. Tente outro termo/cidade.</div>';return;}
    var TP={quente:'#f0917f',morno:'#e0b25a',frio:'#7bb8e6'};
    var h='<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem"><div class="mut" style="font-size:.82rem">'+d.itens.length+' encontrado(s)'+(d.n_redes?(' · '+d.n_redes+' oculta(s)'):'')+'</div><label class="mut" style="font-size:.8rem;cursor:pointer"><input type="checkbox" onclick="capAll(this)" style="width:auto;vertical-align:middle;accent-color:var(--verde)"> marcar todos</label></div><div class="rlist" id="cap-list">';
    d.itens.forEach(function(it){var loc=(it.cidade?(' · '+jsEsc(it.cidade)+(it.uf?('/'+jsEsc(it.uf)):'')):'');var dupB=it.dup_campanha?(' <span class="dupb">🚫 já em campanha: '+jsEsc(it.dup_campanha)+'</span>'):(it.dup?' <span class="dupb">⚠️ já na base</span>':'');h+='<label class="rrow" style="cursor:pointer"><input type="checkbox" name="itens" value="'+it.pack+'"><span style="flex:1"><span style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap"><span class="tdot" style="background:'+(TEMPCOR[it.temperatura]||'#5b9bd5')+'"></span><b style="font-size:.88rem">'+jsEsc(it.empresa)+'</b>'+(it.aberto===false?' <span style=\\'color:var(--coral);font-size:.7rem\\'>(fechado)</span>':'')+dupB+'</span><span class="mut" style="font-size:.76rem">'+(it.segmento?(jsEsc(it.segmento)+' · '):'')+(it.telefone?jsEsc(it.telefone):'')+(it.rating?(' · nota '+it.rating):'')+(it.tem_site?'':' · <span style=\\'color:var(--coral)\\'>sem site</span>')+loc+'</span></span><span class="tpill" style="background:transparent;border:1px solid '+(TP[it.temperatura]||'#7bb8e6')+';color:'+(TP[it.temperatura]||'#7bb8e6')+'">'+it.temperatura+'</span></label>';});
    h+='</div><div style="margin-top:.8rem"><button type="button" class="pbtn" onclick="capImport()">Adicionar selecionados</button></div>';box.innerHTML=h;
  }).catch(function(){if(btn){btn.disabled=false;btn.textContent='Buscar';}capToast('Falha de rede');});return false;}
/* ---------------- cercar área no mapa (Google Maps JS API, lazy-carregada) ---------------- */
var GOOGLE_MAPS_JS_KEY = {{ maps_js_key|tojson }};
var _cercaCarregado = false, _cercaMap = null, _cercaCircle = null, _cercaMarker = null;
function mapaToggle(){
  var acc = document.getElementById('mapacc'); if(!acc) return;
  var abrindo = !acc.classList.contains('open');
  acc.classList.toggle('open');
  var bairro = document.getElementById('cap-g-bairro'), rua = document.getElementById('cap-g-rua');
  if(bairro) bairro.disabled = abrindo; if(rua) rua.disabled = abrindo;
  var nota = document.getElementById('mapaUsando'); if(nota) nota.classList.toggle('on', abrindo);
  if(abrindo && !_cercaCarregado && GOOGLE_MAPS_JS_KEY){
    _cercaCarregado = true;
    var s = document.createElement('script');
    s.src = 'https://maps.googleapis.com/maps/api/js?key=' + encodeURIComponent(GOOGLE_MAPS_JS_KEY) + '&libraries=places&callback=cercaMapaInit';
    document.head.appendChild(s);
  }
}
function cercaSync(){
  var c = _cercaCircle.getCenter(), r = _cercaCircle.getRadius(), km = r/1000;
  document.getElementById('cercaLat').value = c.lat();
  document.getElementById('cercaLng').value = c.lng();
  document.getElementById('cercaRaioKm').value = km.toFixed(2);
  document.getElementById('cercaRaio').value = km;
  document.getElementById('cercaRaioLabel').textContent = km.toFixed(1) + ' km';
}
function cercaMapaInit(){
  var partida = {lat: -5.0892, lng: -42.8019};   // Teresina - PI, só ponto de partida
  _cercaMap = new google.maps.Map(document.getElementById('cercaMap'), {
    center: partida, zoom: 13, disableDefaultUI: true, zoomControl: true, fullscreenControl: false});
  _cercaMarker = new google.maps.Marker({position: partida, map: _cercaMap, draggable: true});
  _cercaCircle = new google.maps.Circle({
    map: _cercaMap, center: partida, radius: 3000, editable: true, draggable: false,
    fillColor: 'var(--verde)', fillOpacity: .14, strokeColor: 'var(--verde)', strokeWeight: 2});
  _cercaCircle.bindTo('center', _cercaMarker, 'position');
  _cercaMarker.addListener('drag', cercaSync);
  google.maps.event.addListener(_cercaCircle, 'radius_changed', cercaSync);
  google.maps.event.addListener(_cercaCircle, 'center_changed', cercaSync);
  document.getElementById('cercaRaio').addEventListener('input', function(){
    _cercaCircle.setRadius(parseFloat(this.value) * 1000);
  });
  var buscaInput = document.getElementById('cercaBusca');
  var autocomplete = new google.maps.places.Autocomplete(buscaInput, {fields: ['geometry']});
  autocomplete.addListener('place_changed', function(){
    var place = autocomplete.getPlace();
    if(!place.geometry || !place.geometry.location) return;
    _cercaMap.panTo(place.geometry.location);
    _cercaMap.setZoom(14);
    _cercaMarker.setPosition(place.geometry.location);
    cercaSync();
  });
  cercaSync();
}
function capAll(el){document.querySelectorAll('#cap-list input[name=itens]').forEach(function(c){c.checked=el.checked;});}
function capImport(){var packs=[];document.querySelectorAll('#cap-list input[name=itens]:checked').forEach(function(c){packs.push(c.value);});if(!packs.length){capToast('Marque ao menos um');return;}
  var fd=new FormData();packs.forEach(function(p){fd.append('itens',p);});var vs=document.getElementById('cap-g-vend');if(vs)fd.append('vendedor_id',vs.value);
  capFetch('/painel/prospeccao/captar/importar',fd).then(function(d){if(!d.ok){capToast('Erro ao importar');return;}(d.leads||[]).forEach(addCard);capToast(d.msg||'Adicionados');document.getElementById('cap-res').innerHTML='';var gf=document.getElementById('cap-google');if(gf)gf.reset();}).catch(function(){capToast('Falha de rede');});}
function capToast(msg,link){var t=document.getElementById('cap-toast');if(!t){t=document.createElement('div');t.id='cap-toast';t.style.cssText='position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--card);border:1px solid var(--verde);color:var(--verde-claro);padding:.6rem 1rem;border-radius:10px;z-index:200;font-size:.85rem;box-shadow:0 6px 20px rgba(0,0,0,.4);transition:opacity .4s;display:flex;align-items:center;gap:.7rem';document.body.appendChild(t);}
  t.textContent='';var span=document.createElement('span');span.textContent=msg;t.appendChild(span);
  if(link&&link.url){var a=document.createElement('a');a.href=link.url;a.textContent=link.label||'Ver ›';a.style.cssText='color:#fff;font-weight:700;text-decoration:underline;white-space:nowrap;flex-shrink:0';t.appendChild(a);}
  t.style.opacity='1';clearTimeout(window._captoastT);window._captoastT=setTimeout(function(){t.style.opacity='0';},link&&link.url?7000:2600);}
// vindo de "Captar Lead" de outra página (?captar=1) → já abre o painel embutido
if(location.search.indexOf('captar=1')>=0){try{capToggle();}catch(e){}}
</script>
{% endblock %}"""

_CAPTAR_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<div class="pw" style="max-width:760px">
  <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
    <a href="/painel/prospeccao" class="mut" style="text-decoration:none;font-size:.85rem">‹ Prospecção</a>
    <span style="flex:1"></span>
  </div>
  <h2 class="tt" style="margin-top:.3rem">Captar leads</h2>
  {% if aviso %}<div class="ok" style="margin-top:.6rem">{{ aviso }}</div>{% endif %}

  <div class="cabas">
    <a class="caba {% if aba=='manual' %}on{% endif %}" href="/painel/prospeccao/captar?aba=manual">✏️ Manual</a>
    <a class="caba {% if aba=='csv' %}on{% endif %}" href="/painel/prospeccao/captar?aba=csv">📄 CSV</a>
    <a class="caba {% if aba=='google' %}on{% endif %}" href="/painel/prospeccao/captar?aba=google">📍 Google Maps</a>
  </div>

  {% macro vendsel() %}{% if pode_atribuir %}<div><label class="lbl">Atribuir a</label>
    <select class="fld" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}{% endmacro %}

  {% if aba=='manual' %}
  <div class="fsec">
    <form method="post" action="/painel/prospeccao/novo" class="egrid" data-tipo-form>
      <input type="hidden" name="voltar" value="/painel/prospeccao/captar">
      <input type="hidden" name="tipo" value="pj">
      <div class="full rcpills" style="margin:0 0 .2rem">
        <button type="button" class="rcpill on" data-tipo-pill="pj" onclick="leadTipo('pj',this)">🏢 Pessoa Jurídica</button>
        <button type="button" class="rcpill" data-tipo-pill="pf" onclick="leadTipo('pf',this)">🧑 Pessoa Física</button>
      </div>
      <div class="full"><label class="lbl" data-pj="Empresa *" data-pf="Nome completo *">Empresa *</label><input class="fld" name="empresa" required data-pj="Nome da empresa" data-pf="Nome completo" placeholder="Nome da empresa"></div>
      <div><label class="lbl">Segmento</label><input class="fld" name="segmento" placeholder="Ex: pet shop"></div>
      <div><label class="lbl">Cidade</label><input class="fld" name="cidade"></div>
      <div><label class="lbl">UF</label><input class="fld" name="uf" maxlength="2" style="text-transform:uppercase"></div>
      <div data-so-pj><label class="lbl">Contato</label><input class="fld" name="contato"></div>
      <div><label class="lbl">Telefone</label><input class="fld" name="telefone"></div>
      <div><label class="lbl">WhatsApp</label><input class="fld" name="whatsapp"></div>
      <div><label class="lbl" data-pj="CNPJ" data-pf="CPF">CNPJ</label><input class="fld" name="documento" inputmode="numeric" data-pj="00.000.000/0000-00" data-pf="000.000.000-00" placeholder="00.000.000/0000-00" oninput="leadDoc(this)"></div>
      <div><label class="lbl">Temperatura</label><select class="fld" name="temperatura">{% for v,l in temperaturas_all %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select></div>
      {{ vendsel() }}
      <div class="full"><button class="pbtn" style="margin:.3rem 0 0">Adicionar lead</button></div>
    </form>
  </div>

  {% elif aba=='csv' %}
  <div class="fsec">
    <form method="post" action="/painel/prospeccao/captar/csv" enctype="multipart/form-data">
      <label class="lbl">Arquivo CSV</label>
      <input class="fld" type="file" name="arquivo" accept=".csv,text/csv" required>
      <div class="mut" style="font-size:.8rem;margin-top:.5rem">Use a 1ª linha como cabeçalho. Colunas reconhecidas:
        <b>empresa</b> (ou nome), telefone, whatsapp, cidade, uf, segmento, contato, email, cnpj. Separador vírgula ou ponto-e-vírgula.</div>
      {% if pode_atribuir %}<div style="margin-top:.6rem;max-width:280px">{{ vendsel() }}</div>{% endif %}
      <button class="pbtn" style="margin-top:.8rem">Importar CSV</button>
    </form>
  </div>

  {% else %}
  <div class="fsec">
    {% if not tem_places %}
    <div style="padding:.4rem 0">
      <b style="font-size:.9rem">📍 Buscar no Google Maps</b>
      <div class="mut" style="font-size:.84rem;margin-top:.5rem;line-height:1.6">
        Pra puxar comércio real (nome, telefone, nota, se tem site) falta configurar a chave da API.
        No Render, em <b>openclaw-web → Environment</b>, adicione <code style="background:var(--bg);padding:.1rem .35rem;border-radius:5px;border:1px solid var(--borda)">GOOGLE_PLACES_API_KEY</code>
        (Places API New, billing ativo no Google Cloud). Assim que salvar e o serviço reiniciar, a busca funciona aqui.
      </div>
    </div>
    {% else %}
    <form method="post" action="/painel/prospeccao/captar/buscar">
      <div class="egrid">
        <div><label class="lbl">Segmento</label><input class="fld" name="segmento" required placeholder="Ex: pet shop" value="{{ busca.segmento or '' }}"></div>
        <div><label class="lbl">Cidade</label><input class="fld" name="cidade" placeholder="Ex: Teresina - PI" value="{{ busca.cidade or '' }}"></div>
      </div>
      <div class="lbl" style="margin-top:.7rem;color:var(--txt-mut)">📍 Refinar por região <span style="font-weight:400">— opcional, pra buscar numa área específica</span></div>
      <div class="egrid" style="margin-top:.25rem">
        <div><label class="lbl">Bairro</label><input class="fld" name="bairro" placeholder="Ex: Jardim Renascença" value="{{ busca.bairro or '' }}"></div>
        <div><label class="lbl">Rua</label><input class="fld" name="rua" placeholder="Ex: Av. Nossa Sra. de Fátima" value="{{ busca.rua or '' }}"></div>
      </div>
      <div class="mut" style="font-size:.76rem;margin-top:.3rem">Bairro filtra a vizinhança toda. Rua afunila bastante (poucos resultados) — use pra mira fina.</div>
      <label class="rrow" style="border:1px solid var(--borda);border-radius:10px;margin-top:.6rem;cursor:pointer">
        <span class="toggle"><input type="checkbox" name="esconder_redes" value="1" {% if busca.esconder %}checked{% endif %}><span class="tgl"></span></span>
        <span style="font-size:.88rem">Esconder redes grandes (Petz, Drogasil…)</span>
      </label>
      <button class="pbtn" style="margin-top:.8rem">Buscar</button>
    </form>

    {% if resultados is not none %}
      {% if not busca.ok %}
        <div class="mut" style="margin-top:.9rem;color:var(--ambar)">Não consegui buscar agora ({{ busca.erro }}). Confira a chave/billing e tente de novo.</div>
      {% elif not resultados %}
        <div class="mut" style="margin-top:.9rem">Nada encontrado{% if busca.esconder and busca.n_redes %} (escondi {{ busca.n_redes }} rede(s) grande(s)){% endif %}. Tente outro termo/cidade.</div>
      {% else %}
      <form method="post" action="/painel/prospeccao/captar/importar" style="margin-top:.9rem">
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem">
          <div class="mut" style="font-size:.82rem">{{ resultados|length }} encontrado(s){% if busca.esconder and busca.n_redes %} · {{ busca.n_redes }} rede(s) oculta(s){% endif %}</div>
          <label class="mut" style="font-size:.8rem;cursor:pointer"><input type="checkbox" onclick="captAll(this)" style="width:auto;vertical-align:middle;accent-color:var(--verde)"> marcar todos</label>
        </div>
        <div class="rlist">
          {% for it in resultados %}
          <label class="rrow" style="cursor:pointer">
            <input type="checkbox" name="itens" value="{{ it.pack }}">
            <span style="flex:1">
              <span style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap"><span class="tdot" style="background:{{ temp_cor[it.temperatura] }}"></span><b style="font-size:.88rem">{{ it.empresa }}</b>{% if it.dup_campanha %}<span class="dupb">🚫 já em campanha: {{ it.dup_campanha }}</span>{% elif it.dup %}<span class="dupb">⚠️ já na base</span>{% endif %}</span>
              <span class="mut" style="font-size:.76rem">{% if it.segmento %}{{ it.segmento }} · {% endif %}{% if it.telefone %}{{ it.telefone }}{% endif %}{% if it.rating %} · nota {{ it.rating }}{% endif %}{% if not it.tem_site %} · <span style="color:var(--coral)">sem site</span>{% endif %}{% if it.cidade %} · {{ it.cidade }}{% if it.uf %}/{{ it.uf }}{% endif %}{% endif %}</span>
            </span>
            {% set tp = {'quente':'#f0917f','morno':'#e0b25a','frio':'#7bb8e6'} %}
            <span class="tpill" style="background:transparent;border:1px solid {{ tp[it.temperatura] }};color:{{ tp[it.temperatura] }}">{{ it.temperatura }}</span>
          </label>
          {% endfor %}
        </div>
        <div style="display:flex;align-items:end;gap:.6rem;flex-wrap:wrap;margin-top:.8rem">
          {% if pode_atribuir %}<div style="min-width:200px">{{ vendsel() }}</div>{% endif %}
          <button class="pbtn">Adicionar selecionados</button>
        </div>
      </form>
      {% endif %}
    {% endif %}
    {% endif %}
  </div>
  {% endif %}
</div>
<script>
function captAll(el){document.querySelectorAll('input[name=itens]').forEach(function(c){c.checked=el.checked;});}
</script>
{% endblock %}"""

_FICHA_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<div class="pw" style="max-width:920px">
  <a href="/painel/prospeccao" class="mut" style="text-decoration:none;font-size:.85rem">‹ Prospecção</a>

  <div class="fsec" style="margin-top:.5rem">
    <div style="display:flex;align-items:flex-start;gap:.6rem;flex-wrap:wrap">
      <div style="flex:1;min-width:200px">
        <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
          <span class="tdot" style="width:13px;height:13px;background:{{ temp_cor[a.temperatura] }}"></span>
          <h2 class="tt">{{ a.empresa }}</h2>
          {% set tp = temp_pill[a.temperatura] %}<span class="tpill" style="background:{{ tp[0] }};color:{{ tp[1] }}">{{ a.temperatura }}</span>
          {% if a.eh_pf %}<span class="tpill" style="background:#20172a;color:#c9a3e0" title="Pessoa física">PF</span>{% endif %}
        </div>
        <div class="mut" style="font-size:.82rem;margin-top:.25rem">{% if a.segmento %}{{ a.segmento }}{% endif %}{% if a.cidade %}{% if a.segmento %} · {% endif %}{{ a.cidade }}{% if a.uf %}/{{ a.uf }}{% endif %}{% endif %}{% if a.vendedor_nome %} · 👤 {{ a.vendedor_nome }}{% endif %}</div>
        {% if canais_contato %}<div style="display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.5rem">
          {% for ch in canais_contato %}<span title="{% if ch.respondeu %}{{ ch.ins }} mensagem(ns) recebida(s){% if ch.primeiro_in %} · 1ª em {{ ch.primeiro_in.strftime('%d/%m/%Y') }}{% endif %}{% else %}só enviado — ainda sem resposta{% endif %}" style="display:inline-flex;align-items:center;gap:.3rem;font-size:.76rem;padding:.2rem .55rem;border-radius:999px;border:1px solid {% if ch.respondeu %}var(--verde){% else %}var(--borda){% endif %};background:{% if ch.respondeu %}rgba(62,224,166,.10){% else %}transparent{% endif %};color:{% if ch.respondeu %}var(--verde-claro){% else %}var(--mut){% endif %}">{{ ch.ic }} {{ ch.label }}{% if ch.respondeu %} ✓{% endif %}</span>{% endfor %}
        </div>{% endif %}
      </div>
      <select onchange="fichaStatus(this,{{ a.id }})" data-prev="{{ a.status }}" class="spill" style="width:auto;padding:.25rem .6rem;border-radius:999px" title="Mudar a situação no funil">
        {% for s,rot in status %}<option value="{{ s }}" {% if s==a.status %}selected{% endif %}>{{ rot }}</option>{% endfor %}
      </select>
    </div>
    <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-top:.8rem">
      {% if a.tel_link %}<a class="pbtn ghost" href="{{ a.tel_link }}">📞 Ligar</a>{% endif %}
      {% if a.zap_link %}<a class="pbtn ghost" href="{{ a.zap_link }}" target="_blank" rel="noopener">💬 WhatsApp</a>{% endif %}
      {% if a.insta_url %}<a class="pbtn ghost" href="{{ a.insta_url }}" target="_blank" rel="noopener" title="Abre o perfil pra você mandar a DM na mão (Instagram não permite DM automática/fria)">📷 Abrir Instagram</a>{% endif %}
      {% if a.maps_url %}<a class="pbtn ghost" href="{{ a.maps_url }}" target="_blank" rel="noopener">🗺️ Mapa</a>{% endif %}
      {% if a.site_url %}<a class="pbtn ghost" href="{{ a.site_url }}" target="_blank" rel="noopener">🌐 Site</a>{% endif %}
      <span style="flex:1"></span>
      {% if not vende_servico %}<button type="button" class="pbtn" disabled title="Disponível pra empresas que vendem serviço">📄 Gerar orçamento</button>
      {% elif a.orcamento_id %}<a class="pbtn" href="/painel/servicos?abrir={{ a.orcamento_id }}">📄 Ver orçamento</a>
      {% else %}<form method="post" action="/painel/prospeccao/{{ a.id }}/orcamento" style="margin:0"><button class="pbtn">📄 Gerar orçamento</button></form>{% endif %}
      {% if a.site_url %}<button type="button" class="pbtn ghost" id="enrqf-btn" onclick="enrqLead({{ a.id }})" title="Raspa o site e descobre e-mail, Instagram e WhatsApp">🔎 Verificar canais</button>{% endif %}
      {% if a.email %}<button type="button" class="pbtn ghost" id="cvz-btn" onclick="convidarZaq({{ a.id }})" title="Manda um e-mail com link pro cliente criar a conta no Zaq">🎟️ Convidar pro Zaq</button>{% endif %}
    </div>
    <div class="mut" id="enrqf-msg" style="font-size:.82rem;margin-top:.5rem"></div>
    {# mesmo telefone, outro chip: dois leads de propósito, um por número — mas quem
       vai ligar precisa saber que a outra campanha já está falando com essa pessoa. #}
    {% if gemeo_aviso %}<div class="gemeo-faixa">⚠️ {{ gemeo_aviso }}
      {% if gemeo.nome %}<span class="mut">({{ gemeo.nome }})</span>{% endif %}
      {% if gemeo_abre %}<a href="/painel/prospeccao/{{ gemeo.lead_id }}">Abrir o outro lead →</a>{% endif %}</div>{% endif %}
    {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
    <script>
    function fichaStatus(sel,id){
      var prev=sel.getAttribute('data-prev')||'';
      var body=new URLSearchParams();body.append('status',sel.value);
      fetch('/painel/prospeccao/'+id+'/status',{method:'POST',headers:{'X-Requested-With':'fetch','Content-Type':'application/x-www-form-urlencoded'},body:body})
        .then(function(r){return r.json();}).then(function(d){
          if(!d.ok){alert('Não consegui mudar a situação ('+(d.erro||'?')+').');if(prev)sel.value=prev;return;}
          sel.setAttribute('data-prev',sel.value);
        }).catch(function(){alert('Falha de rede.');if(prev)sel.value=prev;});
    }
    function convidarZaq(id){var b=document.getElementById('cvz-btn');if(b){b.disabled=true;b.textContent='Enviando…';}
      fetch('/painel/prospeccao/'+id+'/convidar-zaq',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
        if(!d.ok){if(b){b.disabled=false;b.textContent='🎟️ Convidar pro Zaq';}alert(d.erro||'Não consegui enviar.');return;}
        if(b){b.textContent='✓ Convite enviado';}}).catch(function(){if(b){b.disabled=false;b.textContent='🎟️ Convidar pro Zaq';}alert('Falha de rede.');});}
    function identificarNumero(){var b=document.getElementById('idn-btn'),m=document.getElementById('idn-msg'),n=document.getElementById('idn-num');
      var num=(n&&n.value||'').trim();if(!num){if(m){m.textContent='Digite o número com DDD.';m.style.color='var(--ambar)';}return;}
      if(b){b.disabled=true;var t=b.textContent;b.textContent='Consultando…';}if(m){m.textContent='Consultando o titular na Credify…';m.style.color='';}
      var fd=new FormData();fd.append('numero',num);
      fetch('/painel/prospeccao/identificar-numero',{method:'POST',body:fd,headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
        if(b){b.disabled=false;b.textContent=t;}
        if(!d.ok){if(m){m.textContent=d.erro||'Não consegui.';m.style.color='var(--ambar)';}return;}
        if(!d.titulares||!d.titulares.length){if(m){m.textContent='Nenhum titular encontrado.';m.style.color='var(--ambar)';}return;}
        function esc(s){var e=document.createElement('div');e.textContent=s==null?'':s;return e.innerHTML;}
        function ln(rot,val){return val?('<div style=\\'display:flex;gap:.5rem;font-size:.8rem;padding:.1rem 0\\'><span style=\\'color:var(--mut);min-width:74px\\'>'+rot+'</span><b>'+esc(val)+'</b></div>'):'';}
        var h=d.titulares.map(function(t){
          var loc=[(t.cidade||''),(t.uf||'')].filter(Boolean).join('/');
          return '<div style=\\'border:1px solid var(--borda);border-radius:9px;padding:.5rem .65rem;margin-top:.4rem;background:var(--bg)\\'>'
            +'<div style=\\'font-weight:700;color:var(--verde-claro);margin-bottom:.15rem\\'>'+esc(t.nome||'—')+'</div>'
            +ln('CPF',t.cpf_mask)+ln('Endereço',t.endereco)+ln('Bairro',t.bairro)
            +ln('Cidade/UF',loc)+ln('CEP',t.cep)+'</div>';}).join('');
        if(m){m.innerHTML=h;m.style.color='';}}).catch(function(){if(b){b.disabled=false;b.textContent=t;}if(m){m.textContent='Falha de rede.';m.style.color='var(--ambar)';}});}
    function buscarDecisor(id){var b=document.getElementById('dec-btn'),m=document.getElementById('dec-msg');if(b){b.disabled=true;var t=b.textContent;b.textContent='Consultando…';}if(m){m.textContent='Consultando o quadro societário na Credify…';m.style.color='';}
      fetch('/painel/prospeccao/'+id+'/decisor-credify',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){if(b){b.disabled=false;b.textContent=t;}
        if(!d.ok){if(m){m.textContent=d.erro||'Não consegui.';m.style.color='var(--ambar)';}return;}
        var msg='Decisor: '+d.nome+(d.cargo?(' ('+d.cargo+')'):'');
        msg+=d.n_telefones?(' · '+d.n_telefones+' telefone(s)'):' · telefone não liberado na sua conta Credify';
        if(m){m.textContent=msg+' — recarregando…';m.style.color='var(--verde-claro)';}
        setTimeout(function(){location.reload();},1200);}).catch(function(){if(b){b.disabled=false;b.textContent=t;}if(m){m.textContent='Falha de rede.';m.style.color='var(--ambar)';}});}
    function enrqLead(id){var b=document.getElementById('enrqf-btn'),m=document.getElementById('enrqf-msg');if(b){b.disabled=true;b.textContent='Verificando…';}if(m)m.textContent='Raspando o site…';
      fetch('/painel/prospeccao/'+id+'/enriquecer-canais',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){if(b){b.disabled=false;b.textContent='🔎 Verificar canais';}
        if(!d.ok){if(m)m.textContent=d.erro||'Não consegui.';return;}
        var p=[];if(d.whatsapp)p.push('💬 '+d.whatsapp);if(d.email)p.push('✉️ '+d.email+(d.email_ok?' ✓':' (não validou)'));if(d.instagram)p.push('📸 '+d.instagram);
        if(m)m.textContent=p.length?('Achei: '+p.join('  ·  ')+' — recarregando…'):'Não achei canais no site.';
        if(p.length)setTimeout(function(){location.reload();},1400);}).catch(function(){if(b){b.disabled=false;b.textContent='🔎 Verificar canais';}if(m)m.textContent='Falha de rede.';});}
    </script>
  </div>

  <div class="fgrid">
    <div>
      {% if tem_ia and (a.email or a.whatsapp or a.telefone) %}
      <div class="fsec">
        <div class="sh"><b>✨ Primeiro contato</b><span class="mut" style="font-size:.74rem;font-weight:400">IA escreve · você revisa e envia</span></div>
        <div style="display:flex;gap:.5rem;flex-wrap:wrap">
          {% if a.email %}<button type="button" class="pbtn ghost" id="ia-btn-email" onclick="iaMsg('email')">✉️ E-mail com IA</button>{% endif %}
          {% if a.whatsapp or a.telefone %}<button type="button" class="pbtn ghost" id="ia-btn-wpp" onclick="iaMsg('whatsapp')">💬 WhatsApp com IA</button>{% endif %}
          {% if wa_template and (a.whatsapp or a.telefone) %}<button type="button" class="pbtn ghost" id="wa-tpl-btn" onclick="enviarConviteWa()" title="Mensagem aprovada — funciona mesmo no 1º contato frio (fora da janela de 24h)">📨 Convite WhatsApp</button>{% endif %}
        </div>
        {% if wa_numeros %}
        <div id="wa-num-wrap" style="margin-top:.6rem;max-width:420px">
          <label class="lbl" style="font-size:.74rem">📱 Enviar para (número já captado)</label>
          <select class="fld" id="wa-numero">
            {% for n in wa_numeros %}<option value="{{ n.numero }}">{{ n.label }} — {{ n.numero }}</option>{% endfor %}
          </select>
          <div class="mut" style="font-size:.72rem;margin-top:.25rem">Ordenados do mais provável/quente (⭐ decisor) pra baixo. Usado nos envios de WhatsApp acima.</div>
        </div>
        {% endif %}
        <div id="ia-box" style="display:none;margin-top:.7rem"></div>
      </div>
      {% endif %}
      <div class="fsec">
        <div class="sh"><b>Dados</b>
          <div style="display:flex;gap:.4rem">
            {% set _end_lead = (a.receita.endereco if a.receita else None) or a.obs or ((a.cidade or '') ~ ('/' ~ a.uf if a.uf else '')) %}
            {# Receita, CNPJá e decisor (quadro societário) só existem pra empresa — em
               pessoa física esses botões não teriam o que consultar. #}
            {% if not a.eh_pf %}
            {% if a.cnpj %}<form method="post" action="/painel/prospeccao/{{ a.id }}/enriquecer" style="margin:0"><button class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" title="Puxar dados da Receita (CNPJá/BrasilAPI)">↻ atualizar</button></form>
              {% if tem_cnpja %}<button type="button" class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" data-endereco="{{ _end_lead }}" onclick="acharCnpj({{ a.id }},this)" title="Buscar outro CNPJ (trocar)">🔎 trocar</button>{% endif %}
              <form method="post" action="/painel/prospeccao/{{ a.id }}/limpar-cnpj" style="margin:0" onsubmit="return confirm('Remover o CNPJ e os dados da Receita deste lead?')"><button class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" title="Remover o CNPJ (escolhido errado)">🗑 limpar</button></form>
            {% elif tem_cnpja %}<button type="button" class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" data-endereco="{{ _end_lead }}" onclick="acharCnpj({{ a.id }},this)" title="Achar o CNPJ por nome+cidade (CNPJá)">🔎 achar CNPJ</button>
            {% else %}<a class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" target="_blank" rel="noopener" title="Achar o CNPJ na web (nome + cidade)" href="https://www.google.com/search?q={{ (a.empresa ~ ' ' ~ (a.cidade or '') ~ ' cnpj')|urlencode }}">🔎 achar CNPJ</a>{% endif %}
            {% if a.cnpj and tem_credify %}<button type="button" class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" id="dec-btn" onclick="buscarDecisor({{ a.id }})" title="Descobre o sócio-administrador (decisor) pelo CNPJ via Credify — consulta paga">🕵️ {% if a.decisor_nome %}Atualizar decisor{% else %}Buscar decisor{% endif %}</button>{% endif %}
            {% endif %}
            <button type="button" class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" onclick="prospToggle('edit-dados')">editar</button>
          </div>
        </div>
        <div class="mut" id="dec-msg" style="font-size:.8rem;margin:.1rem 0"></div>
        <div id="cnpj-cands" style="margin:.2rem 0"></div>
        {% if a.decisor_nome %}
        <div class="drow" style="align-items:flex-start">
          <span class="ic">🕵️</span><span class="lb">Decisor</span>
          <span style="flex:1"><b>{{ a.decisor_nome }}</b>{% if a.decisor_cargo %} · <span class="mut">{{ a.decisor_cargo }}</span>{% endif %}<span class="badge" style="margin-left:.3rem">Credify</span>
            {% if a.decisor_telefones %}
              {% for t in a.decisor_telefones %}
              <div style="margin-top:.3rem;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
                <span style="color:var(--verde-claro){% if t.provavel %};font-weight:700{% endif %}">{{ t.formatado }}</span>
                {% if t.provavel %}<span class="badge" style="background:#2a2410;border-color:#5a4a1e;color:#e0b25a" title="Número que a Credify indica como o mais provável do decisor">⭐ mais provável</span>{% endif %}
                {% if t.tipo_rot %}<span class="mut" style="font-size:.74rem">{{ t.tipo_rot }}</span>{% endif %}
                {% if t.whatsapp %}<span class="badge" style="background:var(--neon-fundo);border-color:var(--neon-borda);color:var(--verde)">WhatsApp</span>{% endif %}
                {% if t.tel_link %}<a href="{{ t.tel_link }}" style="color:var(--txt-mut);font-size:.78rem">ligar</a>{% endif %}
                {% if t.zap_link %}<a href="{{ t.zap_link }}" target="_blank" rel="noopener" style="color:var(--verde);font-size:.78rem">abrir WhatsApp</a>{% endif %}
              </div>
              {% endfor %}
            {% elif a.decisor_telefone %}<br><span style="color:var(--verde-claro)">{{ a.decisor_telefone }}</span>
              {% if a.decisor_tel_link %} · <a href="{{ a.decisor_tel_link }}" style="color:var(--verde-claro)">ligar</a>{% endif %}
              {% if a.decisor_zap %} · <a href="{{ a.decisor_zap }}" target="_blank" rel="noopener" style="color:var(--verde)">WhatsApp</a>{% endif %}
            {% else %}<br><span class="mut" style="font-size:.78rem">telefone indisponível (consulta de telefone não liberada na Credify)</span>{% endif %}
          </span>
        </div>
        {% endif %}
        {% if tem_credify %}
        <div class="drow" style="align-items:flex-start">
          <span class="ic">🔎</span><span class="lb">Identificar nº</span>
          <span style="flex:1">
            <div style="display:flex;gap:.4rem;flex-wrap:wrap;align-items:center">
              <input class="fld" id="idn-num" inputmode="tel" placeholder="DDD+número (86981885930)" style="flex:1;min-width:150px;max-width:240px">
              <button type="button" class="pbtn ghost" id="idn-btn" style="padding:.35rem .7rem;font-size:.78rem" onclick="identificarNumero()">Consultar titular</button>
            </div>
            <div class="mut" id="idn-msg" style="font-size:.78rem;margin-top:.3rem">Descobre em que nome um telefone está cadastrado (Credify · consulta paga).</div>
          </span>
        </div>
        {% endif %}
        {% if a.contato %}<div class="drow"><span class="ic">👤</span><span class="lb">Contato</span><span>{{ a.contato }}{% if a.cargo %} · {{ a.cargo }}{% endif %}</span></div>{% endif %}
        {% if a.doc %}<div class="drow"><span class="ic">{{ '🪪' if a.eh_pf else '🏢' }}</span><span class="lb">{{ a.doc_rot }}</span><span>{{ a.doc_fmt }}</span></div>{% endif %}
        {% if a.socio %}<div class="drow"><span class="ic">🧑‍💼</span><span class="lb">Sócio</span><span>{{ a.socio }}</span></div>{% endif %}
        {% if a.regime_tributario or a.porte %}<div class="drow"><span class="ic">📑</span><span class="lb">Regime</span><span>{{ a.regime_tributario or '—' }}{% if a.porte %} · porte {{ a.porte }}{% endif %}</span></div>{% endif %}
        {% if a.telefone %}<div class="drow"><span class="ic">📞</span><span class="lb">Telefone</span><span>{{ a.telefone }}</span></div>{% endif %}
        {% if a.whatsapp %}<div class="drow"><span class="ic">💬</span><span class="lb">WhatsApp</span><span>{{ a.whatsapp }}<span class="badge">Business?</span></span></div>{% endif %}
        {% if a.email %}<div class="drow"><span class="ic">✉️</span><span class="lb">E-mail</span><span>{{ a.email }}</span></div>{% endif %}
        {% if a.instagram %}<div class="drow"><span class="ic">📷</span><span class="lb">Instagram</span><span>{{ a.instagram }}</span></div>{% endif %}
        {% if a.site_url %}<div class="drow"><span class="ic">🌐</span><span class="lb">Site</span><span><a href="{{ a.site_url }}" target="_blank" rel="noopener" style="color:var(--verde-claro)">{{ a.site_dominio or a.site_url }}</a> · <span class="mut" style="font-size:.78rem">ver página</span></span></div>{% elif a.tem_site is not none %}<div class="drow"><span class="ic">🌐</span><span class="lb">Site</span><span>{% if a.tem_site %}tem site{% else %}<span style="color:var(--coral)">não tem</span>{% endif %}</span></div>{% endif %}
        {% if a.valor %}<div class="drow"><span class="ic">💰</span><span class="lb">Valor est.</span><span style="color:var(--verde-claro)">{{ brl(a.valor) }}</span></div>{% endif %}
        {% if a.proximo_contato_em %}<div class="drow"><span class="ic">📅</span><span class="lb">Próximo</span><span style="color:var(--verde-claro)">{{ a.proximo_contato_em.strftime('%d/%m/%Y') }}</span></div>{% endif %}
        {% if a.obs %}<div class="drow"><span class="ic">📝</span><span class="lb">Obs</span><span>{{ a.obs }}</span></div>{% endif %}
        {% if a.receita %}
        <div style="margin-top:.6rem;border-top:1px solid var(--borda);padding-top:.5rem">
          <div class="lb" style="text-transform:uppercase;letter-spacing:.03em;margin-bottom:.2rem">🧾 Receita Federal{% if a.receita.fonte %} · <span style="opacity:.7">{{ a.receita.fonte }}</span>{% endif %}</div>
          {% if a.receita.nome_fantasia %}<div class="drow"><span class="ic">🏷️</span><span class="lb">Fantasia</span><span>{{ a.receita.nome_fantasia }}</span></div>{% endif %}
          {% if a.receita.situacao %}<div class="drow"><span class="ic">📌</span><span class="lb">Situação</span><span>{{ a.receita.situacao }}</span></div>{% endif %}
          {% if a.receita.abertura %}<div class="drow"><span class="ic">📆</span><span class="lb">Abertura</span><span>{{ a.receita.abertura }}</span></div>{% endif %}
          {% if a.receita.capital_social %}<div class="drow"><span class="ic">🏦</span><span class="lb">Capital</span><span>{{ a.receita.capital_social }}</span></div>{% endif %}
          {% if a.receita.natureza %}<div class="drow"><span class="ic">⚖️</span><span class="lb">Natureza</span><span>{{ a.receita.natureza }}</span></div>{% endif %}
          {% if a.receita.inscricao_estadual %}<div class="drow"><span class="ic">🧾</span><span class="lb">Insc. Est.</span><span>{{ a.receita.inscricao_estadual }}</span></div>{% endif %}
          {% if a.receita.endereco %}<div class="drow"><span class="ic">📍</span><span class="lb">Endereço</span><span>{{ a.receita.endereco }}</span></div>{% endif %}
          {% if a.receita.atividades_secundarias %}<div class="drow"><span class="ic">🔧</span><span class="lb">Outras ativ.</span><span>{{ a.receita.atividades_secundarias|join(' · ') }}</span></div>{% endif %}
        </div>
        {% endif %}
        {% if not (a.contato or a.doc or a.socio or a.telefone or a.whatsapp or a.email or a.instagram or a.valor) %}
          <div class="mut" style="font-size:.82rem">Sem dados ainda. Clique em <b>editar</b> pra preencher{% if not a.eh_pf %} — ou preencha o CNPJ e use <b>↻ atualizar</b> pra puxar da Receita{% endif %}.</div>{% endif %}

        <form id="edit-dados" data-tipo-form method="post" action="/painel/prospeccao/{{ a.id }}/editar" style="display:none;margin-top:.8rem;border-top:1px solid var(--borda);padding-top:.8rem">
          <input type="hidden" name="tipo" value="{{ a.tipo or 'pj' }}">
          <div class="rcpills">
            <button type="button" class="rcpill {% if not a.eh_pf %}on{% endif %}" data-tipo-pill="pj" onclick="leadTipo('pj',this)">🏢 Pessoa Jurídica</button>
            <button type="button" class="rcpill {% if a.eh_pf %}on{% endif %}" data-tipo-pill="pf" onclick="leadTipo('pf',this)">🧑 Pessoa Física</button>
          </div>
          <div class="egrid">
            <div class="full"><label class="lbl" data-pj="Empresa" data-pf="Nome completo">{{ 'Nome completo' if a.eh_pf else 'Empresa' }}</label><input class="fld" name="empresa" value="{{ a.empresa or '' }}"></div>
            <div class="full"><label class="lbl" data-pj="CNPJ — cole aqui e puxe tudo da Receita" data-pf="CPF">{{ 'CPF' if a.eh_pf else 'CNPJ — cole aqui e puxe tudo da Receita' }}</label><div style="display:flex;gap:.3rem"><input class="fld" name="documento" inputmode="numeric" data-pj="00.000.000/0000-00" data-pf="000.000.000-00" placeholder="{{ '000.000.000-00' if a.eh_pf else '00.000.000/0000-00' }}" value="{{ a.doc_fmt }}" oninput="leadDoc(this)"><button type="button" class="pbtn ghost" data-so-pj style="padding:.5rem .6rem;white-space:nowrap{% if a.eh_pf %};display:none{% endif %}" onclick="fichaCnpj()" title="Preencher tudo pela Receita">↓ Receita</button></div></div>
            <div data-so-pj{% if a.eh_pf %} style="display:none"{% endif %}><label class="lbl">Contato</label><input class="fld" name="contato" value="{{ a.contato or '' }}"></div>
            <div data-so-pj{% if a.eh_pf %} style="display:none"{% endif %}><label class="lbl">Cargo</label><input class="fld" name="cargo" value="{{ a.cargo or '' }}"></div>
            <div><label class="lbl">Telefone</label><input class="fld" name="telefone" value="{{ a.telefone or '' }}"></div>
            <div><label class="lbl">WhatsApp</label><input class="fld" name="whatsapp" value="{{ a.whatsapp or '' }}"></div>
            <div><label class="lbl">E-mail</label><input class="fld" name="email" value="{{ a.email or '' }}"></div>
            <div><label class="lbl">Segmento</label><input class="fld" name="segmento" value="{{ a.segmento or '' }}"></div>
            <div><label class="lbl">Cidade</label><input class="fld" name="cidade" value="{{ a.cidade or '' }}"></div>
            <div><label class="lbl">UF</label><input class="fld" name="uf" maxlength="2" value="{{ a.uf or '' }}"></div>
            <div data-so-pj{% if a.eh_pf %} style="display:none"{% endif %}><label class="lbl">Sócio</label><input class="fld" name="socio" value="{{ a.socio or '' }}"></div>
            <div data-so-pj{% if a.eh_pf %} style="display:none"{% endif %}><label class="lbl">Regime</label><input class="fld" name="regime_tributario" value="{{ a.regime_tributario or '' }}"></div>
            <div data-so-pj{% if a.eh_pf %} style="display:none"{% endif %}><label class="lbl">Porte</label><input class="fld" name="porte" value="{{ a.porte or '' }}"></div>
            <div><label class="lbl">Instagram</label><input class="fld" name="instagram" value="{{ a.instagram or '' }}"></div>
            <div><label class="lbl">Valor est. (R$)</label><input class="fld" name="valor" inputmode="decimal" value="{{ (a.valor/100)|n2 if a.valor else '' }}"></div>
            <div><label class="lbl">Tem site?</label><select class="fld" name="tem_site">
              <option value="" {% if a.tem_site is none %}selected{% endif %}>—</option>
              <option value="1" {% if a.tem_site is true %}selected{% endif %}>Tem</option>
              <option value="0" {% if a.tem_site is false %}selected{% endif %}>Não tem</option></select></div>
            <div class="full"><label class="lbl">Site (link)</label><input class="fld" name="site_url" inputmode="url" placeholder="https://…" value="{{ a.site_url or '' }}"></div>
            <div class="full"><label class="lbl">Observações</label><input class="fld" name="obs" value="{{ a.obs or '' }}"></div>
          </div>
          <div style="display:flex;gap:.5rem;margin-top:.7rem"><button class="pbtn" style="margin:0">Salvar dados</button><button type="button" class="pbtn ghost" onclick="prospToggle('edit-dados')">Cancelar</button></div>
        </form>
      </div>

      <div class="fsec">
        <div class="sh"><b>Temperatura</b></div>
        <form method="post" action="/painel/prospeccao/{{ a.id }}/temperatura" style="margin:0">
          <div class="rcpills">{% for t,rot in temperaturas %}<button type="submit" name="temperatura" value="{{ t }}" class="rcpill {% if t==a.temperatura %}on{% endif %}" style="{% if t==a.temperatura %}background:{{ temp_cor[t] }};border-color:{{ temp_cor[t] }}{% endif %}">{{ rot }}</button>{% endfor %}</div>
        </form>
        {% if pode_atribuir %}
        <form method="post" action="/painel/prospeccao/{{ a.id }}/atribuir" style="margin-top:.6rem">
          <label class="lbl">Vendedor responsável</label>
          <select class="fld" name="vendedor_id" onchange="this.form.submit()"><option value="">— sem responsável —</option>{% for v in vendedores %}<option value="{{ v.id }}" {% if v.id==a.vendedor_id %}selected{% endif %}>{{ v.nome }}</option>{% endfor %}</select>
        </form>{% endif %}
      </div>
    </div>

    <div>
      <div class="fsec">
        <div class="sh"><b>Registrar contato</b></div>
        <form method="post" action="/painel/prospeccao/{{ a.id }}/contato" style="margin:0">
          <div class="rcpills" id="rc-pills">{% for t,rot in tipos %}<button type="button" class="rcpill {% if loop.first %}on{% endif %}" data-tipo="{{ t }}" onclick="rcPick(this)">{{ rot }}</button>{% endfor %}</div>
          <input type="hidden" name="tipo" id="rc-tipo" value="ligacao">
          <textarea class="fld" name="descricao" rows="2" placeholder="O que rolou nesse contato?"></textarea>
          <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-top:.6rem">
            <select class="fld" name="resultado" style="width:auto">{% for r,rot in resultados %}<option value="{{ r }}">{{ rot if r else 'Resultado…' }}</option>{% endfor %}</select>
            <label class="chipin">📅 Próximo <input type="date" name="proximo"></label>
            <span style="flex:1"></span>
            <button class="pbtn" style="margin:0">✓ Salvar contato</button>
          </div>
        </form>
      </div>

      <div class="fsec">
        <div class="sh"><b>Histórico</b></div>
        {% if origem_ch %}<div class="tl"><span class="dt" style="background:#3ee0a6"></span>
          <div style="font-size:.86rem"><b>Entrou por {{ origem_ch.ic }} {{ origem_ch.label }}</b> <span class="mut">— 1º contato do lead</span></div>
          <div class="mut" style="font-size:.72rem;margin-top:.2rem">{{ origem_ch.em.strftime('%d/%m/%Y %H:%M') if origem_ch.em else '' }}</div>
        </div>{% endif %}
        {% if not timeline and not origem_ch %}<p class="mut" style="margin:.2rem 0 0">Nenhum contato registrado ainda.</p>{% endif %}
        {% for ev in timeline %}
        <div class="tl"><span class="dt" style="background:{{ ev.cor }}"></span>
          <div style="font-size:.86rem"><b>{{ ev.tipo_rot }}</b>{% if ev.resultado_rot %} — <span class="mut">{{ ev.resultado_rot }}</span>{% endif %}</div>
          {% if ev.descricao %}<div style="font-size:.83rem;margin-top:.12rem">{{ ev.descricao }}</div>{% endif %}
          <div class="mut" style="font-size:.72rem;margin-top:.2rem">{{ ev.criado_em.strftime('%d/%m/%Y %H:%M') if ev.criado_em else '' }}{% if ev.quem %} · {{ ev.quem }}{% endif %}{% if ev.agendado_para %} · próximo {{ ev.agendado_para.strftime('%d/%m') }}{% endif %}</div>
        </div>{% endfor %}
      </div>
    </div>
  </div>
</div>
<script>
function prospToggle(id){var e=document.getElementById(id);e.style.display=(e.style.display==='none')?'block':'none';}
function rcPick(el){var box=document.getElementById('rc-pills');box.querySelectorAll('.rcpill').forEach(function(b){b.classList.remove('on');});el.classList.add('on');document.getElementById('rc-tipo').value=el.getAttribute('data-tipo');}
function fToast(msg){var t=document.getElementById('f-toast');if(!t){t=document.createElement('div');t.id='f-toast';t.style.cssText='position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--card);border:1px solid var(--verde);color:var(--verde-claro);padding:.6rem 1rem;border-radius:10px;z-index:200;font-size:.85rem;box-shadow:0 6px 20px rgba(0,0,0,.4);transition:opacity .4s';document.body.appendChild(t);}t.textContent=msg;t.style.opacity='1';clearTimeout(window._ft);window._ft=setTimeout(function(){t.style.opacity='0';},2600);}
function cnpjManualBox(id){
  // caixa pra colar o CNPJ achado por fora (Google) e aplicar direto — mesma
  // rota do "usar" (grava + puxa a Receita; o backend valida os 14 dígitos).
  // Sem precisar abrir "editar".
  return '<form method="post" action="/painel/prospeccao/'+id+'/aplicar-cnpj" '
    +'style="display:flex;gap:.3rem;align-items:center;margin-top:.5rem;border-top:1px dashed var(--borda);padding-top:.5rem">'
    +'<span class="mut" style="font-size:.76rem;white-space:nowrap">Já tem o CNPJ?</span>'
    +'<input class="fld" name="cnpj" inputmode="numeric" placeholder="cole aqui" style="flex:1;padding:.4rem .55rem">'
    +'<button class="pbtn" style="padding:.35rem .8rem;font-size:.78rem;margin:0">usar</button></form>';
}
function acharCnpj(id,btn){var box=document.getElementById('cnpj-cands');var endLead=(btn&&btn.getAttribute('data-endereco'))||'';box.innerHTML='<div class="mut" style="font-size:.8rem">Procurando CNPJ…</div>';
  fetch('/painel/prospeccao/'+id+'/buscar-cnpj',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
    var webBtn=d.web?('<a class="pbtn" style="padding:.35rem .8rem;font-size:.8rem;margin-top:.4rem;display:inline-flex" target="_blank" rel="noopener" href="'+d.web+'">🔎 buscar na web</a>'):'';
    var manual=cnpjManualBox(id);
    if(!d.ok){box.innerHTML='<div class="mut" style="font-size:.8rem;color:var(--ambar)">Não achei ('+(d.erro||'?')+').</div>'+webBtn+manual;return;}
    if(!d.itens||!d.itens.length){box.innerHTML='<div class="mut" style="font-size:.8rem">Nenhum CNPJ nessa cidade pra esse nome.</div>'+webBtn+manual;return;}
    var h='';
    if(endLead){h+='<div class="mut" style="font-size:.76rem;margin:.1rem 0 .3rem">📍 Endereço do lead: <b>'+jsEsc(endLead)+'</b> — escolha o que bate:</div>';}
    else{h+='<div class="lb" style="margin:.2rem 0">Escolha a empresa certa (confira o endereço):</div>';}
    h+='<div class="rlist">';
    d.itens.forEach(function(it){
      var loc=(it.cidade?(jsEsc(it.cidade)+(it.uf?('/'+it.uf):'')):'');
      h+='<form method="post" action="/painel/prospeccao/'+id+'/aplicar-cnpj" class="rrow" style="cursor:pointer;gap:.5rem"><input type="hidden" name="cnpj" value="'+it.cnpj+'">'
        +'<span style="flex:1"><b style="font-size:.85rem">'+jsEsc(it.razao_social||it.nome_fantasia||it.cnpj)+'</b>'
        +'<span class="mut" style="font-size:.74rem"> · '+it.cnpj+(it.situacao?(' · '+jsEsc(it.situacao)):'')+'</span>'
        +(it.endereco?('<span class="mut" style="display:block;font-size:.74rem">📍 '+jsEsc(it.endereco)+(loc?(' · '+loc):'')+'</span>'):(loc?('<span class="mut" style="display:block;font-size:.74rem">📍 '+loc+'</span>'):''))
        +(it.socio?('<span class="mut" style="display:block;font-size:.74rem">🧑‍💼 '+jsEsc(it.socio)+'</span>'):'')
        +'</span><button class="pbtn" style="padding:.3rem .7rem;font-size:.78rem;margin:0">usar</button></form>';
    });
    h+='</div>';
    if(d.web){h+='<div style="margin-top:.4rem"><a class="mut" style="font-size:.76rem" target="_blank" rel="noopener" href="'+d.web+'">nenhuma bate? buscar na web →</a></div>';}
    h+=manual;
    box.innerHTML=h;
  }).catch(function(){box.innerHTML='<div class="mut" style="font-size:.8rem;color:var(--ambar)">Falha de rede.</div>'+cnpjManualBox(id);});}
function jsEsc(s){return (s||'').replace(/[&<>"]/g,function(c){return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c];});}
function fichaCnpj(){var f=document.getElementById('edit-dados');var cnpj=f.querySelector('[name=documento]').value.replace(/\\D/g,'');if(cnpj.length!==14){fToast('Pra puxar da Receita, o CNPJ precisa ter 14 dígitos');return;}
  fToast('Consultando Receita…');
  fetch('/painel/prospeccao/cnpj?cnpj='+cnpj,{headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){fToast('CNPJ não encontrado ('+(d.erro||'')+')');return;}var x=d.dados;
    function put(n,v){var el=f.querySelector('[name='+n+']');if(el&&v)el.value=v;}
    // o nome só entra se estiver vazio — quem já batizou o lead não perde o nome dele
    var e=f.querySelector('[name=empresa]');
    if(e&&!e.value&&(x.nome_fantasia||x.razao_social))e.value=x.nome_fantasia||x.razao_social;
    put('segmento',x.segmento);put('cidade',x.cidade);put('uf',x.uf);put('telefone',x.telefone);
    put('email',x.email);put('socio',x.socio);put('regime_tributario',x.regime_tributario);put('porte',x.porte);
    fToast('Preenchido pela Receita ✓ confira e salve');
  }).catch(function(){fToast('Falha de rede');});}
function iaMsg(canal){var box=document.getElementById('ia-box');var eb=document.getElementById('ia-btn-email'),wb=document.getElementById('ia-btn-wpp');
  if(eb)eb.disabled=true;if(wb)wb.disabled=true;box.style.display='block';box.innerHTML='<div class="mut" style="font-size:.82rem">✨ Gerando com IA…</div>';
  var fd=new FormData();fd.append('canal',canal);
  fetch('/painel/prospeccao/{{ a.id }}/mensagem-ia',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){return r.json();}).then(function(d){
    if(eb)eb.disabled=false;if(wb)wb.disabled=false;
    if(!d.ok){box.innerHTML='<div class="mut" style="color:var(--ambar);font-size:.82rem">'+(d.erro==='sem_ia'?'IA não configurada (falta a chave da IA).':'Não consegui gerar ('+(d.erro||'?')+').')+'</div>';return;}
    if(d.canal==='whatsapp'){
      box.innerHTML='<label class="lbl">Mensagem de WhatsApp</label><textarea class="fld" id="ia-texto" rows="5"></textarea>'
        +'<div style="display:flex;gap:.5rem;margin-top:.5rem;flex-wrap:wrap"><button type="button" class="pbtn" id="ia-wa-btn" onclick="iaWhats()">💬 Enviar pelo sistema</button>'
        +'<button type="button" class="pbtn ghost" onclick="iaMsg(&quot;whatsapp&quot;)">↻ gerar outra</button></div>'
        +'<div class="mut" style="font-size:.74rem;margin-top:.35rem">Envia pelo seu canal de WhatsApp conectado e registra no histórico — sem abrir outra página. (Use o botão “WhatsApp” lá em cima para abrir o WhatsApp externo.)</div>';
      document.getElementById('ia-texto').value=d.texto||'';box.setAttribute('data-link',d.link||'');
    }else{
      box.innerHTML='<label class="lbl">Assunto</label><input class="fld" id="ia-assunto">'
        +'<label class="lbl" style="margin-top:.5rem">Mensagem</label><textarea class="fld" id="ia-corpo" rows="8"></textarea>'
        +'<div style="display:flex;gap:.5rem;margin-top:.5rem;flex-wrap:wrap"><button type="button" class="pbtn" onclick="iaSendEmail()">✉️ Enviar e-mail</button>'
        +'<button type="button" class="pbtn ghost" onclick="iaMsg(&quot;email&quot;)">↻ gerar outro</button></div>'
        +'<div class="mut" style="font-size:.74rem;margin-top:.35rem">Envia pra {{ a.email }} · resposta volta pro seu e-mail · registra no histórico.</div>';
      document.getElementById('ia-assunto').value=d.assunto||'';document.getElementById('ia-corpo').value=d.corpo||'';
    }
  }).catch(function(){if(eb)eb.disabled=false;if(wb)wb.disabled=false;box.innerHTML='<div class="mut" style="color:var(--ambar)">Falha de rede.</div>';});}
function iaSendEmail(){var a=document.getElementById('ia-assunto').value,c=document.getElementById('ia-corpo').value;if(!c.trim()){fToast('Escreva a mensagem');return;}
  fToast('Enviando…');var fd=new FormData();fd.append('assunto',a);fd.append('corpo',c);
  fetch('/painel/prospeccao/{{ a.id }}/enviar-email',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){fToast(d.erro==='envio_falhou'?'Não consegui enviar (confira a config de e-mail).':d.erro==='sem_email'?'Lead sem e-mail.':'Erro ao enviar');return;}
    fToast('E-mail enviado ✓');setTimeout(function(){location.reload();},900);}).catch(function(){fToast('Falha de rede');});}
function iaWhats(){var t=(document.getElementById('ia-texto')||{}).value||'';if(!t.trim()){fToast('Escreva a mensagem');return;}
  var b=document.getElementById('ia-wa-btn');if(b){b.disabled=true;b.textContent='Enviando…';}
  var ns=document.getElementById('wa-numero');
  var fd=new FormData();fd.append('texto',t);if(ns&&ns.value)fd.append('numero',ns.value);
  fetch('/painel/prospeccao/{{ a.id }}/enviar-whatsapp',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){return r.json();}).then(function(d){
    if(b){b.disabled=false;b.textContent='💬 Enviar pelo sistema';}
    if(!d.ok){fToast(d.msg||'Não consegui enviar');return;}
    fToast('Mensagem enviada ✓');setTimeout(function(){location.reload();},900);
  }).catch(function(){if(b){b.disabled=false;b.textContent='💬 Enviar pelo sistema';}fToast('Falha de rede');});}
function enviarConviteWa(){var b=document.getElementById('wa-tpl-btn');
  var ns=document.getElementById('wa-numero');var alvo=ns&&ns.value?ns.value:'este lead';
  if(!confirm('Enviar o convite de 1º contato por WhatsApp (mensagem aprovada) para '+alvo+'?'))return;
  if(b){b.disabled=true;b.textContent='Enviando…';}
  var fd=new FormData();if(ns&&ns.value)fd.append('numero',ns.value);
  fetch('/painel/prospeccao/{{ a.id }}/enviar-convite-wa',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){return r.json();}).then(function(d){
    if(b){b.disabled=false;b.textContent='📨 Convite WhatsApp';}
    if(!d.ok){fToast(d.msg||'Não consegui enviar');return;}
    fToast('Convite enviado ✓');setTimeout(function(){location.reload();},900);
  }).catch(function(){if(b){b.disabled=false;b.textContent='📨 Convite WhatsApp';}fToast('Falha de rede');});}
</script>
{% endblock %}"""

_COMUNICACAO_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<style>
.cx-wrap{max-width:1180px;margin:0 auto;padding:0 .3rem}
.cx-head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap}
.cx-head code{background:var(--card);border:1px solid var(--borda);border-radius:6px;padding:.12rem .45rem;color:var(--verde-claro);font-size:.82rem}
.cx-chip-faixa{font-size:.82rem;color:var(--txt-mut);margin-top:.25rem;display:flex;
  align-items:center;gap:.4rem;flex-wrap:wrap}
.cx-chip-faixa b{color:var(--txt);font-weight:600}
.cx-chip-faixa a{color:var(--verde-claro)}
.cx-chip-faixa .pt{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.cx-filtros{display:flex;gap:.5rem;flex-wrap:wrap;margin:.8rem 0}
/* busca da caixa: ocupa a sobra da linha e encolhe até 180px antes de quebrar */
.cx-busca{display:flex;align-items:center;gap:.35rem;background:var(--card-2);
  border:1px solid var(--borda);border-radius:9px;padding:.2rem .5rem;flex:1 1 180px;
  min-width:150px;max-width:340px}
.cx-busca:focus-within{border-color:var(--neon);box-shadow:0 0 0 2px var(--neon-fundo)}
.cx-lupa{color:var(--txt-mut);font-size:.95rem;line-height:1;flex:none}
.cx-busca-in{flex:1;min-width:0;background:none;border:0;outline:none;color:var(--txt);
  font-family:inherit;font-size:.82rem;padding:.24rem 0}
.cx-busca-in::placeholder{color:var(--txt-mut)}
.cx-busca-in::-webkit-search-cancel-button{display:none}
/* width:auto + flex:none derrubam o `button{width:100%}` global (pensado pros
   botões de formulário de login/cadastro) — sem isso, assim que o ✕ aparece
   (ao digitar algo), ele vira flex-basis:100% da barra de busca e espreme o
   próprio campo de texto até ~0px: some a caixa inteira, mesmo com o valor
   certo por dentro (a busca funciona, só não dá pra VER o que foi digitado). */
.cx-busca-x{background:none;border:0;color:var(--txt-mut);cursor:pointer;font-size:.85rem;
  line-height:1;padding:.1rem .15rem;width:auto;flex:none}
.cx-busca-x:hover{color:var(--txt)}
/* o pedaço que casou com o termo, na linha da conversa */
.cx-conv mark{background:var(--neon-fundo);color:var(--txt);border-radius:2px;padding:0 1px}
.cx-num{font-size:.62rem;font-family:ui-monospace,monospace;letter-spacing:.03em;
  padding:0 5px;border-radius:4px;border:1px solid var(--borda);color:var(--azul);
  line-height:1.6;white-space:nowrap}
/* 2 colunas (lista + conversa) enquanto nada está aberto; vira 3 (com contexto)
   só ao abrir uma conversa — evita os 2 quadros vazios e dá mais respiro. */
.cx-grid{display:grid;grid-template-columns:minmax(280px,330px) minmax(0,1fr);gap:.8rem;align-items:start}
.cx-grid.open{grid-template-columns:minmax(280px,330px) minmax(0,1fr) 300px}
.cx-ctx{display:none}
.cx-grid.open .cx-ctx{display:block}
.cx-list{border:1px solid var(--borda);border-radius:12px;background:var(--card);overflow:hidden;max-height:72vh;overflow-y:auto}
.cx-conv{display:flex;gap:.6rem;width:100%;text-align:left;background:none;border:0;border-bottom:1px solid var(--borda);padding:.7rem .75rem;cursor:pointer;color:var(--txt)}
.cx-conv:hover{background:#141416}
.cx-conv.on{background:#12271f}
/* etiqueta de chip: só nasce quando a empresa tem dois. Verde no principal,
   âmbar no segundo — as mesmas cores das abas de Canais. */
.cx-chip{font-size:.68rem;padding:1px 7px;border-radius:100px;border:1px solid;
  margin-left:.3rem;white-space:nowrap}
.cx-chip.um{color:var(--neon);border-color:#2A5A3E}
.cx-chip.dois{color:var(--ambar);border-color:#57491D}
.cx-conv .av{width:36px;height:36px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.78rem;background:#1d3a30;color:var(--verde-claro)}
.cx-conv .mid{flex:1;min-width:0}
.cx-conv .nm{display:flex;justify-content:space-between;gap:.4rem;align-items:baseline}
.cx-conv .nm b{font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cx-conv .nm .t{color:var(--txt-mut);font-size:.7rem;white-space:nowrap}
.cx-conv .pre{color:var(--txt-mut);font-size:.78rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:.15rem}
/* o dono do lead em linha própria: das três posições testadas, foi a única que não
   trunca o nome nem desalinha quando a coluna fica estreita. */
/* `flex` (nível de bloco), e não `inline-flex`: os filhos do .mid são spans inline,
   então em inline-flex o dono grudava no fim da prévia e a quebra virava sorteio do
   tamanho do texto — exatamente o defeito da opção A que a gente descartou. Bloco
   força a linha própria; o width:fit-content mantém o hover do tamanho do texto. */
.cx-conv .cx-dono{display:flex;width:fit-content;align-items:center;gap:.2rem;
  font-size:.72rem;color:var(--txt-mut);margin-top:.15rem;border-radius:6px;
  padding:.05rem .2rem}
.cx-conv .cx-dono.vazio{color:var(--ambar)}
/* tracejado = campo editável. Vale parado, sem depender de :hover — que no celular
   não existe, e é lá que esta caixa mais é olhada. */
.cx-conv .cx-dono.clicavel{cursor:pointer;border:1px dashed var(--borda);padding:.05rem .35rem}
.cx-conv .cx-dono.clicavel:hover{background:var(--card-2);color:var(--txt);border-color:var(--txt-mut)}
.cx-dmenu{position:fixed;z-index:70;background:var(--card);border:1px solid var(--borda);
  border-radius:10px;padding:.3rem;min-width:190px;box-shadow:0 10px 30px rgba(0,0,0,.6)}
.cx-dmenu .cab{font-family:var(--mono);font-size:.62rem;color:var(--txt-mut);
  text-transform:uppercase;letter-spacing:.08em;padding:.3rem .5rem}
.cx-dmenu .op{padding:.35rem .5rem;border-radius:7px;font-size:.82rem;cursor:pointer}
.cx-dmenu .op:hover{background:var(--neon-fundo);color:var(--neon)}
.cx-dmenu .op.sem{color:var(--txt-mut);border-top:1px solid var(--borda);margin-top:.2rem}
.cx-dmenu .op.atual{background:var(--neon-fundo);color:var(--neon)}
/* faixa dos leads sem dono */
.cx-orf{display:flex;align-items:center;gap:.55rem;background:var(--ambar-fundo);
  border:1px solid var(--ambar-borda);border-radius:10px;padding:.55rem .75rem;
  margin:.2rem 0 .6rem;font-size:.84rem;color:var(--txt-mut);flex-wrap:wrap}
.cx-orf b{color:var(--ambar);font-weight:600}
.cx-orf .pt{width:8px;height:8px;border-radius:50%;background:var(--ambar);flex-shrink:0}
.cx-orf .lk{color:var(--verde-claro);margin-left:auto;text-decoration:none}
.cx-orf .lk:hover{text-decoration:underline}
.cx-orf .bts{display:flex;gap:.4rem;margin-left:auto;flex-wrap:wrap;align-items:center}
.cx-orf .fld{width:auto;padding:.3rem .5rem;font-size:.8rem}
.cx-orf .pbtn{padding:.35rem .7rem;font-size:.8rem}
.cx-cn{font-size:.66rem;padding:.05rem .4rem;border-radius:999px;border:1px solid;margin-top:.25rem;display:inline-block}
.cn-mail{color:var(--azul,#5b9bd5);border-color:#2f4a63;background:#14212e}
.cn-wpp{color:var(--verde);border-color:var(--neon-borda);background:var(--neon-fundo)}
.cx-thread,.cx-ctx{border:1px solid var(--borda);border-radius:12px;background:var(--card);min-height:40vh}
.cx-thread{display:flex;flex-direction:column;max-height:72vh}
.cx-empty{padding:2.4rem 1rem;text-align:center;color:var(--txt-mut);font-size:.9rem}
.cx-trunc{padding:.35rem 0;text-align:center;color:var(--txt-mut);font-size:.72rem}
.cx-th{display:flex;align-items:center;gap:.6rem;padding:.7rem .85rem;border-bottom:1px solid var(--borda)}
/* aviso de mesmo número no outro chip — colado embaixo do cabeçalho, antes das
   mensagens: é o que a pessoa lê ANTES de escrever, que é o ponto. */
.cx-gemeo{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;font-size:.78rem;
  line-height:1.4;color:var(--ambar);background:#2a2113;border-bottom:1px solid var(--ambar-borda);
  padding:.45rem .85rem}
.cx-gemeo a{color:var(--ambar);text-decoration:underline;white-space:nowrap}
.cx-th b{font-size:.92rem}
.cx-th small{display:block;color:var(--txt-mut);font-size:.75rem}
.cx-msgs{flex:1;overflow-y:auto;padding:.9rem;display:flex;flex-direction:column;gap:.6rem}
.cx-day{align-self:center;color:#6c6c68;font-size:.72rem}
.cx-m{max-width:82%;align-self:flex-end;background:#123028;border:1px solid #1d5741;border-radius:12px;border-bottom-right-radius:4px;padding:.5rem .7rem;font-size:.86rem;line-height:1.45}
.cx-m .cab{color:var(--txt-mut);font-size:.72rem;margin-bottom:.25rem}
.cx-m .meta{display:block;color:var(--txt-mut);font-size:.68rem;margin-top:.3rem;text-align:right}
.cx-comp{border-top:1px solid var(--borda);padding:.6rem .7rem;display:flex;gap:.5rem;align-items:flex-end;background:#101011}
.cx-comp textarea{flex:1;resize:none;background:var(--bg);border:1px solid var(--borda);color:var(--txt);border-radius:9px;padding:.5rem .6rem;font:inherit;font-size:.86rem}
.cx-stub{border-top:1px solid var(--borda);padding:.7rem .85rem;background:#101011;color:var(--txt-mut);font-size:.8rem}
.cx-stub .lbl2{display:inline-block;font-size:.62rem;padding:.05rem .4rem;border-radius:999px;background:#241634;color:#c9a3e0;border:1px solid #4a3163;margin-left:.3rem}
.cx-ctx{padding:.85rem}
.cx-sec{margin-bottom:.9rem}
.cx-sec h4{margin:0 0 .4rem;font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;color:var(--txt-mut)}
.cx-kv{display:flex;justify-content:space-between;gap:.5rem;font-size:.82rem;padding:.18rem 0}
.cx-kv span{color:var(--txt-mut)}
/* confirmação do "Levar para o lead" */
.vl-fundo{position:fixed;inset:0;z-index:60;background:rgba(8,8,9,.72);display:flex;align-items:center;
  justify-content:center;padding:1rem;overflow:auto}
.vl-cx{width:100%;max-width:420px;background:var(--card);border:1px solid var(--borda);border-radius:14px;
  box-shadow:0 18px 50px rgba(0,0,0,.55)}
.vl-cab{padding:.9rem 1rem .2rem;display:flex;flex-direction:column;gap:.2rem}
.vl-cab b{font-size:1rem}
.vl-cab .mut{font-size:.78rem;color:var(--txt-mut)}
.vl-corpo{padding:.9rem 1rem;display:flex;flex-direction:column;gap:.75rem}
.vl-2{display:grid;gap:.75rem;grid-template-columns:1fr}
@media(min-width:460px){.vl-2{grid-template-columns:1fr 1fr}}
.vl-fonte{margin-top:.3rem;font-size:.7rem;color:var(--verde-claro)}
.vl-dup{margin:0 1rem;padding:.55rem .7rem;border-radius:9px;font-size:.76rem;color:var(--ambar);
  border:1px solid var(--ambar-borda);background:#2a2113}
.vl-dup a{color:var(--ambar)}
.vl-pe{display:flex;gap:.5rem;justify-content:flex-end;padding:.8rem 1rem;border-top:1px solid var(--borda);
  background:var(--card-2);border-radius:0 0 13px 13px;margin-top:.9rem}
.cn-msg{color:#4a9cff;border-color:#274a73;background:#0f1e30}
.cn-ig{color:#f083b0;border-color:#5c2946;background:#2a1420}
.cx-tabs{display:flex;gap:.2rem;border-bottom:1px solid var(--borda);margin:.7rem 0 0;overflow-x:auto}
.cx-tab{padding:.6rem .85rem;font-size:.88rem;color:var(--txt-mut);border-bottom:2px solid transparent;white-space:nowrap;text-decoration:none}
.cx-tab:hover{color:var(--txt)}
.cx-tab.on{color:var(--verde-claro);border-bottom-color:var(--verde)}
.cx-m.cin{align-self:flex-start;background:var(--card-2);border-color:var(--borda)}
.cx-m.cbot{background:#1c1428;border-color:#4a3163}
.cx-m .who{display:block;font-size:.64rem;color:#c9a3e0;margin-bottom:.15rem}
.cx-msgs{scroll-behavior:smooth}
.cx-undot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--verde);margin-left:.3rem;vertical-align:middle}
.cx-conv .av{transition:none}
.cx-tbl{margin-top:.8rem;border:1px solid var(--borda);border-radius:12px;overflow:hidden;background:var(--card)}
.cx-tbl table{width:100%;border-collapse:collapse;font-size:.86rem}
.cx-tbl th{text-align:left;font-weight:600;color:var(--txt-mut);font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;padding:.6rem .8rem;background:var(--card-2);border-bottom:1px solid var(--borda)}
.cx-tbl td{padding:.6rem .8rem;border-top:1px solid var(--borda);vertical-align:top}
.cx-tbl tr:hover td{background:#191b1a}
.cx-cc{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:.8rem}
.cx-card{border:1px solid var(--borda);border-radius:14px;background:var(--card);padding:1rem}
.cx-card h3{margin:0 0 .4rem;font-size:.95rem}
.cx-stat{display:inline-flex;align-items:center;gap:.35rem;font-size:.76rem;padding:.15rem .5rem;border-radius:999px;border:1px solid;margin-left:.4rem}
.st-on{color:var(--verde-claro);border-color:#1d5741;background:#0f231b}
.st-off{color:var(--ambar);border-color:var(--ambar-borda);background:#241d10}
.cx-env{font-family:var(--mono);font-size:.76rem;background:var(--bg);border:1px solid var(--borda);border-radius:8px;padding:.5rem .6rem;color:var(--txt-mut);margin-top:.5rem}
.cx-env b{color:var(--verde-claro)}
.waseg{display:grid;grid-template-columns:repeat(var(--waseg-n,3),1fr);gap:.25rem;margin:.3rem 0 .75rem;background:var(--bg);border:1px solid var(--borda);border-radius:10px;padding:.25rem}
.waseg label{font-size:.75rem;border-radius:7px;padding:.42rem .25rem;cursor:pointer;color:var(--txt-mut);text-align:center;line-height:1.15;display:flex;align-items:center;justify-content:center;transition:background .12s,color .12s}
.waseg label:hover{color:var(--txt)}
.waseg label.on{color:var(--sobre-verde);background:var(--verde);font-weight:600}
.waseg label.dois.on{background:var(--ambar);color:#1B1405}
.waseg label input{display:none}
.waprov{display:none}
.waprov .lbl{margin-top:.5rem}
.sw{position:relative;display:inline-block;width:42px;height:24px;flex-shrink:0}
.sw input{opacity:0;width:0;height:0}
.sw span{position:absolute;inset:0;background:#333;border-radius:999px;cursor:pointer;transition:.15s}
.sw span::before{content:"";position:absolute;left:3px;top:3px;width:18px;height:18px;background:#eee;border-radius:50%;transition:.15s}
.sw input:checked+span{background:var(--verde)}
.sw input:checked+span::before{transform:translateX(18px);background:#04140d}
.agrow{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.55rem 0;border-top:1px solid var(--borda)}
.agrow:first-of-type{border-top:0}
.agrow .lab b{font-size:.88rem}.agrow .lab div{color:var(--txt-mut);font-size:.76rem;margin-top:.1rem}
.aggrid{display:grid;grid-template-columns:1fr 1fr;gap:.7rem;margin-top:.3rem}
.agfield label{display:block;font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;color:var(--txt-mut);margin-bottom:.25rem}
.agfaq{border:1px solid var(--borda);border-radius:10px;padding:.55rem .65rem;margin-bottom:.45rem;background:var(--bg);display:flex;gap:.6rem;align-items:flex-start}
.agfaq .q b{font-size:.85rem}.agfaq .q p{margin:.15rem 0 0;color:var(--txt-mut);font-size:.8rem}
.tag-new{font-size:.6rem;padding:.05rem .4rem;border-radius:999px;background:#241634;color:#c9a3e0;border:1px solid #4a3163;margin-left:.35rem}
@media(max-width:1024px){.cx-grid,.cx-grid.open{grid-template-columns:1fr}.cx-ctx{order:3}.cx-cc{grid-template-columns:1fr}.aggrid{grid-template-columns:1fr}}
</style>
<div class="pw">
{% set nav_ativo = 'canais' if aba == 'canais' else 'comunicacao' %}
""" + _navbar('comunicacao') + """
  <div class="cx-head">
    <div>
      <h2 class="tt">📨 Comunicação</h2>
      <div class="mut" style="font-size:.82rem;margin-top:.15rem">Enviando e-mails como {% if remetente %}<code>{{ remetente }}</code> · respostas voltam pro e-mail de quem enviou{% else %}<span style="color:var(--coral)">(e-mail ainda não configurado)</span>{% endif %}</div>
      {# de qual número sai o WhatsApp. O dado já existia (a credencial do QR guarda
         o aparelho conectado) e não aparecia em tela nenhuma — quem dispara campanha
         não sabia de onde ia sair. #}
      <div class="cx-chip-faixa">
        {% if chip.estado == 'conectado' %}
          <span class="pt" style="background:{{ 'var(--ambar)' if chip.sem_receber else 'var(--neon)' }}"></span>Enviando WhatsApp pelo chip
          {% if chip.nome %}<b>{{ chip.nome }}</b> · {% endif %}<code>{{ chip.numero }}</code>
          · <span style="color:var(--neon)">conectado</span>
          {# "conectado" é o que o serviço acha da SESSÃO; isto aqui é o que a caixa
             viu de verdade. Os dois discordando é exatamente o sintoma da sessão que
             emudece sem cair — e era o que ninguém tinha como perceber. #}
          {% if chip.sem_receber %}· <span style="color:var(--ambar)"><b>sem receber {{ chip.sem_receber }}</b></span>
          · <a href="/painel/prospeccao/comunicacao?aba=canais">conferir o chip</a>
          {# "conectado + sem receber" é o estado que engana: ninguém desconfia de um
             chip verde. Dizer o TESTE aqui vale mais que o alerta — mandar mensagem
             de outro celular resolve a dúvida em 10 segundos. O dono também leva este
             aviso no Telegram/e-mail — ver finance/wa_silencio.py.

             Este texto dizia "reconecte o chip em Canais — NÃO use o Desconectar",
             porque desconectar apagaria o histórico. Isso deixou de ser verdade no
             PR #404, que tirou a limpeza de dentro do /sair (o log de lá hoje diz
             "sair: sessão encerrada (histórico preservado)", e apagar virou duas
             portas explícitas: o botão "Apagar histórico" e a faxina dos 30 dias).

             O aviso obsoleto era pior que inútil: quando a sessão emudece de verdade
             — o chip sai da lista de dispositivos da conta e não recebe nem o eco das
             próprias mensagens — parear de novo é a ÚNICA saída, e a tela mandava não
             fazer justamente isso. Aconteceu com a conta 35 em 22/08: quatro horas
             calada, religamento forçado e restart do processo sem resolver. #}
          <div style="font-size:.72rem;color:var(--txt-mut);margin-top:.2rem">
            Teste: mande uma mensagem de <b>outro celular</b> pro número. Se ela não
            aparecer aqui, reconecte o chip em Canais. Se depois de reconectar ela
            ainda não aparecer, pareie de novo lendo o QR — o histórico de conversas
            <b>não</b> se perde nisso.
          </div>{% endif %}
        {% elif chip.estado == 'sincronizando' %}
          <span class="pt" style="background:var(--ambar)"></span>Chip
          {% if chip.nome %}<b>{{ chip.nome }}</b> · {% endif %}<code>{{ chip.numero }}</code>
          · <span style="color:var(--ambar)">importando as conversas…</span>
        {% elif chip.estado == 'caido' %}
          <span class="pt" style="background:var(--coral)"></span>Chip
          {% if chip.nome %}<b>{{ chip.nome }}</b> · {% endif %}<code>{{ chip.numero }}</code>
          · <span style="color:var(--coral)">desconectado</span> —
          <a href="/painel/prospeccao/comunicacao?aba=canais">reconectar</a>
        {% else %}
          <span class="pt" style="background:var(--coral)"></span>
          <span style="color:var(--coral)">Nenhum chip de WhatsApp conectado</span> —
          <a href="/painel/prospeccao/comunicacao?aba=canais">conectar</a>
        {% endif %}
      </div>

      {# SEGUNDA LINHA — o chip 2, no mesmo formato e com os mesmos estados. Só
         aparece pra empresa que está no QR: no Twilio/Cloud não existe segundo chip,
         e a linha viraria ruído prometendo o que não dá pra fazer. #}
      {% if chip.provedor == 'qr' %}
      <div class="cx-chip-faixa">
        {% if chip2 and chip2.estado == 'conectado' %}
          <span class="pt" style="background:{{ 'var(--ambar)' if chip2.sem_receber else 'var(--neon)' }}"></span>Enviando WhatsApp pelo chip
          {% if chip2.nome %}<b>{{ chip2.nome }}</b> · {% endif %}<code>{{ chip2.numero }}</code>
          · <span style="color:var(--neon)">conectado</span>
          {% if chip2.sem_receber %}· <span style="color:var(--ambar)"><b>sem receber {{ chip2.sem_receber }}</b></span>
          · <a href="/painel/prospeccao/comunicacao?aba=canais">conferir o chip</a>{% endif %}
        {% elif chip2 %}
          <span class="pt" style="background:var(--coral)"></span>Chip
          {% if chip2.nome %}<b>{{ chip2.nome }}</b>{% endif %}{% if chip2.numero %} · <code>{{ chip2.numero }}</code>{% endif %}
          · <span style="color:var(--coral)">{{ 'desconectado' if chip2.numero else 'ainda sem parear' }}</span> —
          <a href="/painel/prospeccao/comunicacao?aba=canais">{{ 'reconectar' if chip2.numero else 'ler o QR' }}</a>
        {% else %}
          {# discreto de propósito: não ter chip 2 é o normal, não é problema #}
          <span class="pt" style="background:var(--borda)"></span>
          <span style="color:var(--txt-mut)">Segundo chip não conectado</span> ·
          <a href="/painel/prospeccao/comunicacao?aba=canais">conectar</a>
        {% endif %}
      </div>
      {% endif %}
    </div>
  </div>

  <div class="cx-tabs">
    {% for k,rot in [('conversas','💬 Conversas'),('emails','✉️ E-mails'),('agente','🤖 Agente IA')] %}
    <a class="cx-tab {% if aba==k %}on{% endif %}" href="/painel/prospeccao/comunicacao?aba={{ k }}">{{ rot }}</a>{% endfor %}
  </div>

  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  {% if aba=='conversas' %}
  <form class="cx-filtros" method="get" action="/painel/prospeccao/comunicacao">
    <input type="hidden" name="aba" value="conversas">
    <select class="fld" name="canal" style="width:auto" onchange="this.form.submit()">
      <option value="" {% if not canal %}selected{% endif %}>Todos os mensageiros</option>
      <option value="whatsapp" {% if canal=='whatsapp' %}selected{% endif %}>💬 WhatsApp</option>
      <option value="messenger" {% if canal=='messenger' %}selected{% endif %}>🔵 Messenger</option>
      <option value="instagram" {% if canal=='instagram' %}selected{% endif %}>📸 Instagram</option>
    </select>
    {% if gerencia %}<select class="fld" name="vendedor" style="width:auto" onchange="this.form.submit()">
      <option value="">Todos os vendedores</option>
      {# o filtro que faltava: ver de uma vez os leads que estão sem dono #}
      {% if pode_atribuir %}<option value="sem" {% if filtro_vend=='sem' %}selected{% endif %}>— sem responsável —</option>{% endif %}
      {% for v in vendedores %}<option value="{{ v.id }}" {% if filtro_vend==(v.id|string) %}selected{% endif %}>{{ v.nome }}</option>{% endfor %}
    </select>{% endif %}
    {# A BUSCA. Fica DENTRO do form pra herdar a linha (e pra o Enter ainda funcionar
       com o JS desligado), mas quem manda é o JS: o Enter é barrado e a lista se
       refaz pela mesma rota do polling, sem recarregar a página. #}
    <span class="cx-busca">
      <span class="cx-lupa">⌕</span>
      <input class="cx-busca-in" type="search" name="q" id="cx-busca" autocomplete="off"
             placeholder="Buscar por nome ou número" value="{{ busca or '' }}"
             oninput="cxBuscaDigitou()"
             onkeydown="if(event.key==='Enter'){event.preventDefault();cxBuscaJa();}">
      <button type="button" class="cx-busca-x" id="cx-busca-x" title="Limpar"
              onclick="cxBuscaLimpar()" style="display:none">✕</button>
    </span>
    <span class="mut" id="cx-conta" style="align-self:center;font-size:.8rem;white-space:nowrap">{{ convs|length }}{% if total_convs and total_convs > convs|length %} de {{ total_convs }}{% endif %} conversa(s)</span>
  </form>

  <!-- Importação em andamento. Sem isso o vendedor abria o painel no meio do sync,
       via 2 conversas e concluía que "só veio isso" — foi exatamente o que
       aconteceu. Barra indeterminada de propósito: a % que o WhatsApp manda
       reinicia a cada bloco, então mostrar número seria mentira; o que é honesto
       (e útil) é o total de conversas já importadas, que sobe na frente dele. -->
  <div id="cx-sync-aviso" style="display:none;margin:.2rem 0 .6rem;padding:.5rem .7rem;
       border:1px solid var(--neon-borda);background:var(--neon-fundo);border-radius:9px;font-size:.82rem">
    <span id="cx-sync-txt">📥 Importando conversas do WhatsApp…</span>
    <div style="height:5px;border-radius:4px;background:var(--bg);overflow:hidden;margin-top:.4rem">
      <div id="cx-sync-bar" style="height:100%;width:25%;background:var(--verde);
           animation:cxsync 1.1s ease-in-out infinite alternate"></div>
    </div>
  </div>
  <style>@keyframes cxsync{from{margin-left:0}to{margin-left:75%}}</style>

  {# Leads sem dono: a faixa conta e leva pro filtro; com o filtro ligado, ela vira
     as ações em lote. Preenchida pelo JS (cxSemDono) porque o número muda a cada
     poll e a cada atribuição, sem recarregar a página. #}
  <div id="cx-sem-dono"></div>

  <div class="cx-grid" id="cx-grid">
    <div class="cx-list" id="cx-list">
      {% for c in convs %}
      <button type="button" class="cx-conv" id="cxc-{{ c.id }}" onclick="cxOpen(this,{{ c.id }})">
        <span class="av">{{ (c.empresa[:2]|upper) if c.empresa else '?' }}</span>
        <span class="mid">
          <span class="nm"><b>{{ c.empresa }}</b><span class="t">{{ c.quando.strftime('%d/%m') if c.quando else '' }}</span></span>
          <span class="pre">{{ c.quem }}: {{ c.preview }}</span>
          {% set cnc = {'whatsapp':'cn-wpp','email':'cn-mail','messenger':'cn-msg','instagram':'cn-ig'} %}
          <span class="cx-cn {{ cnc.get(c.canal,'cn-mail') }}">{{ c.canal_rot }}{% if c.n > 1 %} · {{ c.n }}{% endif %}</span>{% if c.chip_rot %}<span class="cx-chip {{ 'dois' if c.chip_id else 'um' }}">{{ c.chip_rot }}</span>{% endif %}
        </span>
      </button>
      {% else %}
      <div class="cx-empty">Nenhuma comunicação ainda.<br><span style="font-size:.82rem">Envie um e-mail ou WhatsApp de 1º contato pela ficha de um lead — aparece aqui.</span></div>
      {% endfor %}
    </div>

    <div class="cx-thread" id="cx-thread">
      <div class="cx-empty">← Escolha uma conversa pra ver as mensagens.</div>
    </div>

    <div class="cx-ctx" id="cx-ctx">
      <div class="cx-empty" style="padding:1.4rem 1rem">Selecione uma conversa.</div>
    </div>
  </div>

  {% elif aba=='emails' %}
  <div style="display:flex;align-items:center;gap:.6rem;margin:.2rem 0 .7rem;flex-wrap:wrap">
    <button class="pbtn ghost" id="esync-btn" type="button" onclick="emailSync()">🔄 Sincronizar recebidos</button>
    <span class="mut" id="esync-msg" style="font-size:.82rem"></span>
    <span style="flex:1"></span>
    <span class="mut" style="font-size:.8rem">{{ convs|length }} conversa(s) de e-mail</span>
  </div>
  <div class="cx-grid" id="cx-grid">
    <div class="cx-list" id="cx-list">
      {% for c in convs %}
      <button type="button" class="cx-conv" id="cxc-{{ c.id }}" onclick="cxOpen(this,{{ c.id }})">
        <span class="av">{{ (c.empresa[:2]|upper) if c.empresa else '?' }}</span>
        <span class="mid">
          <span class="nm"><b>{{ c.empresa }}</b><span class="t">{{ c.quando.strftime('%d/%m') if c.quando else '' }}</span></span>
          <span class="pre">{{ c.quem }}: {{ c.preview }}</span>
          <span class="cx-cn cn-mail">✉️ E-mail{% if c.n > 1 %} · {{ c.n }}{% endif %}</span>
        </span>
      </button>
      {% else %}
      <div class="cx-empty">Nenhum e-mail ainda.<br><span style="font-size:.82rem">Os recebidos aparecem aqui após “Sincronizar recebidos”. Configure a caixa na aba <b>Canais</b>.</span></div>
      {% endfor %}
    </div>
    <div class="cx-thread" id="cx-thread"><div class="cx-empty">← Escolha um e-mail pra ler e responder.</div></div>
    <div class="cx-ctx" id="cx-ctx"><div class="cx-empty" style="padding:1.4rem 1rem">Selecione um e-mail.</div></div>
  </div>
  <script>
  function emailSync(){var b=document.getElementById('esync-btn'),m=document.getElementById('esync-msg');if(!b)return;b.disabled=true;var t=b.textContent;b.textContent='Sincronizando…';m.textContent='';
    fetch('/painel/prospeccao/comunicacao/email-sync',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){b.disabled=false;b.textContent=t;
      if(!d.ok){m.textContent=d.erro||'Não consegui.';return;}
      if(d.novos){m.textContent='+'+d.novos+' novo(s) — recarregando…';setTimeout(function(){location.reload();},900);}else{m.textContent=d.detalhe||'Nenhum e-mail novo.';}}).catch(function(){b.disabled=false;b.textContent=t;m.textContent='Falha de rede.';});}
  </script>

  {% elif aba=='agente' %}
  {% if not gerencia %}
  <div class="cx-card" style="margin-top:.8rem"><p class="mut" style="margin:0">Só o dono/gestor configura o Agente.</p></div>
  {% else %}
  {% if not tem_ia %}<div class="cx-card" style="margin-top:.8rem;border-color:var(--ambar-borda)"><p class="mut" style="margin:0;color:var(--ambar)">⚠️ A IA ainda não está configurada no ambiente (falta a chave). O agente só responde depois disso — mas você já pode deixar tudo configurado aqui.</p></div>{% endif %}
  <form method="post" action="/painel/prospeccao/comunicacao/agente-config" class="cx-cc" style="align-items:start">
    <div>
      <div class="cx-card">
        <div style="display:flex;align-items:center;gap:.7rem">
          <div style="font-size:1.6rem">🤖</div>
          <div style="flex:1"><b style="font-size:1rem">Agente de Atendimento</b><div class="mut" style="font-size:.8rem">Responde os leads, qualifica e te passa quando precisa.</div></div>
          <label class="sw"><input type="checkbox" name="ativo" {% if ag_cfg.ativo %}checked{% endif %}><span></span></label>
        </div>
      </div>
      <div class="cx-card">
        <h3>⚙️ Comportamento</h3>
        <div class="agfield" style="margin:.5rem 0"><label>Confiança mínima pra responder sozinho: <b style="color:#c9a3e0" id="lim-v">{{ ag_cfg.limiar_confianca }}%</b></label>
          <input type="range" name="limiar_confianca" min="50" max="95" value="{{ ag_cfg.limiar_confianca }}" style="width:100%;accent-color:#7b4fb0" oninput="document.getElementById('lim-v').textContent=this.value+'%'"></div>
        <div class="aggrid">
          <div class="agfield"><label>Responder em</label><select class="fld" name="horario"><option value="comercial" {% if ag_cfg.horario=='comercial' %}selected{% endif %}>Horário comercial</option><option value="24h" {% if ag_cfg.horario=='24h' %}selected{% endif %}>24 horas</option></select></div>
          <div class="agfield"><label>Tom</label><select class="fld" name="tom"><option value="informal" {% if ag_cfg.tom=='informal' %}selected{% endif %}>Informal</option><option value="formal" {% if ag_cfg.tom=='formal' %}selected{% endif %}>Formal</option></select></div>
          <div class="agfield"><label>Máx. respostas do bot antes de te chamar</label><input class="fld" name="max_trocas" inputmode="numeric" value="{{ ag_cfg.max_trocas }}"><small class="mut" style="font-size:.72rem">12 ou mais = praticamente sempre ativo (não passa pro humano por quantidade)</small></div>
          <div class="agfield"><label>Escalar para</label><select class="fld" name="escalar_para"><option value="dono_lead" {% if ag_cfg.escalar_para=='dono_lead' %}selected{% endif %}>Dono do lead</option><option value="plantao" {% if ag_cfg.escalar_para=='plantao' %}selected{% endif %}>Vendedor de plantão</option></select></div>
        </div>
      </div>
      <div class="cx-card">
        <h3>✅ O que ele faz sozinho</h3>
        <div class="agrow"><div class="lab"><b>Responder dúvidas frequentes</b><div>Usa a base de conhecimento ao lado</div></div><label class="sw"><input type="checkbox" name="pode_responder" {% if ag_cfg.pode_responder %}checked{% endif %}><span></span></label></div>
        <div class="agrow"><div class="lab"><b>Qualificar o lead</b><div>Mede interesse e ajusta a temperatura</div></div><label class="sw"><input type="checkbox" name="pode_qualificar" {% if ag_cfg.pode_qualificar %}checked{% endif %}><span></span></label></div>
        <div class="agrow"><div class="lab"><b>Agendar follow-up</b></div><label class="sw"><input type="checkbox" name="pode_agendar" {% if ag_cfg.pode_agendar %}checked{% endif %}><span></span></label></div>
        <div class="agrow"><div class="lab"><b>Gerar orçamento prévio quando o cliente pedir<span class="tag-new">novo</span></b><div>Monta rascunho com serviços + preço e manda o link</div></div><label class="sw"><input type="checkbox" name="pode_orcamento" {% if ag_cfg.pode_orcamento %}checked{% endif %}><span></span></label></div>
        <div class="agrow"><div class="lab"><b>Oferecer orçamento proativamente</b><div>Sem o cliente pedir</div></div><label class="sw"><input type="checkbox" name="orcamento_proativo" {% if ag_cfg.orcamento_proativo %}checked{% endif %}><span></span></label></div>
      </div>
      <div style="display:flex;justify-content:flex-end"><button class="pbtn">Salvar configuração</button></div>
    </div>
    <div>
      <div class="cx-card">
        <h3>🙋 Quando ele te passa (handoff)</h3>
        <div class="mut" style="font-size:.84rem;line-height:1.9">✓ O cliente pede pra falar com uma pessoa<br>✓ Sentimento negativo / reclamação<br>✓ Confiança abaixo do limiar<br>✓ Negociação de preço / fechamento<br>✓ Assunto fora do escopo dos serviços</div>
      </div>
    </div>
  </form>

  <style>
  .distrow{display:flex;align-items:center;gap:.55rem;padding:.5rem .6rem;border:1px solid var(--borda);border-radius:9px;background:var(--bg);cursor:default}
  .distrow .grip{color:var(--txt-mut);cursor:grab;font-size:.9rem}
  .distrow input[type=checkbox]{width:18px;height:18px;accent-color:var(--verde);flex-shrink:0}
  .distrow .dnm{flex:1;font-size:.88rem}
  .distnote{font-size:.82rem;color:var(--txt-mut);background:#1a1226;border:1px solid #3a2b52;border-radius:10px;padding:.6rem .75rem;margin:.7rem 0;line-height:1.6}
  .distnote b{color:#c9a3e0}
  .distalerta{font-size:.84rem;color:#f7d9a8;background:#2a1d0c;border:1px solid #6b4d17;
    border-radius:10px;padding:.65rem .8rem;margin:.7rem 0;line-height:1.6}
  .distalerta b{color:#ffc86b}
  /* --- aviso de lead por WhatsApp (migração 185) --- */
  .zapdobra{border-left:2px solid var(--verde);margin:.2rem 0 0 .15rem;padding:.1rem 0 .1rem 1rem}
  .zapdobra[hidden]{display:none}
  .zapchips{display:flex;flex-direction:column;gap:.45rem}
  .zapchip{display:flex;align-items:center;gap:.6rem;padding:.55rem .7rem;border:1px solid var(--borda);
    border-radius:10px;background:var(--bg);cursor:pointer}
  .zapchip:has(input:checked){border-color:var(--verde);background:rgba(37,211,102,.07)}
  .zapchip input[type=radio]{accent-color:var(--verde);flex-shrink:0}
  .zapchip .nm{flex:1;min-width:0}
  .zapchip .nm b{display:block;font-size:.86rem}
  .zapchip .nm span{font-size:.74rem;color:var(--txt-mut)}
  .zappill{font-size:.66rem;font-weight:600;padding:.14rem .45rem;border-radius:99px;
    border:1px solid;white-space:nowrap}
  .zappill.ok{color:var(--verde);border-color:#1E4A3A;background:#10241A}
  .zappill.off{color:var(--coral);border-color:#5A2B2B;background:#241313}
  .zapvars{display:flex;flex-wrap:wrap;gap:.3rem;margin-bottom:.4rem}
  .zapvar{font-family:var(--mono,monospace);font-size:.72rem;padding:.18rem .42rem;border-radius:6px;
    border:1px dashed var(--borda);background:var(--bg);color:var(--verde);cursor:pointer}
  .zapvar:hover{border-color:var(--verde)}
  </style>
  <div class="cx-card">
    <form method="post" action="/painel/prospeccao/comunicacao/distribuicao">
      <div style="display:flex;align-items:center;gap:.7rem">
        <div style="font-size:1.6rem">🎯</div>
        <div style="flex:1"><b style="font-size:1rem">Distribuição de leads<span class="tag-new">novo</span></b>
          <div class="mut" style="font-size:.8rem">Reparte os leads novos entre a equipe, por ordem de fila (rodízio).</div></div>
        <label class="sw"><input type="checkbox" name="ativo" {% if dist_cfg and dist_cfg.ativo %}checked{% endif %}><span></span></label>
      </div>
      {# fila montada + chave desligada é o estado que não avisa e não reparte: o
         formulário grava as duas coisas juntas, então dá pra sair daqui achando que
         ligou. Enquanto estiver assim, todo lead novo nasce sem dono. #}
      {% if dist_cfg and not dist_cfg.ativo and dist_membros | selectattr('na_fila') | list %}
      <div class="distalerta">⚠️ <b>A fila está montada, mas a distribuição está desligada.</b>
        Enquanto o interruptor aí em cima estiver apagado, nenhum lead novo é repartido —
        todos entram <b>sem dono</b>. Ligue e salve pra valer.</div>
      {% endif %}
      <div class="distnote">🤖 O agente dá o 1º toque e qualifica. O <b>vendedor da vez</b> é avisado, observa e
        <b>assume quando quiser</b>. Vale pra toda entrada nova que cai no chip da empresa — anúncio (tráfego pago),
        resposta de campanha e contato orgânico.</div>
      <div class="agrow"><div class="lab"><b>Avisar por e-mail e push</b><div>Chega na hora no app do vendedor, com link direto pra ficha do lead</div></div>
        <label class="sw"><input type="checkbox" name="avisar" {% if not dist_cfg or dist_cfg.avisar %}checked{% endif %}><span></span></label></div>

      {# WhatsApp em interruptor PRÓPRIO. Antes o `avisar` governava e-mail, push e zap
         de uma vez, e quem queria push sem tocar o celular do vendedor não tinha saída. #}
      <div class="agrow"><div class="lab"><b>Avisar no WhatsApp <span class="tag-new">novo</span></b>
          <div>Uma mensagem no número que o vendedor tem cadastrado na aba <b>Equipe</b></div></div>
        <label class="sw"><input type="checkbox" name="aviso_zap" id="zapsw"
          {% if dist_cfg and dist_cfg.aviso_zap %}checked{% endif %}><span></span></label></div>

      <div id="zapbox" class="zapdobra" {% if not (dist_cfg and dist_cfg.aviso_zap) %}hidden{% endif %}>
        {% if dist_qr and dist_chips | length > 1 %}
        <div class="agfield">
          <label>Enviar por qual chip</label>
          <div class="zapchips">
            {% for ch in dist_chips %}
            <label class="zapchip">
              <input type="radio" name="aviso_zap_chip_id" value="{{ '' if ch.principal else ch.id }}"
                {% if (ch.principal and not dist_cfg.aviso_zap_chip_id) or (not ch.principal and dist_cfg.aviso_zap_chip_id == ch.id) %}checked{% endif %}>
              <span class="nm"><b>{{ ch.rotulo }}{% if ch.principal %} · principal{% endif %}</b>
                <span>{{ ch.numero or 'sem número pareado' }}</span></span>
              <span class="zappill {{ 'ok' if ch.ativo else 'off' }}">{{ 'conectado' if ch.ativo else 'fora do ar' }}</span>
            </label>
            {% endfor %}
          </div>
          <small class="mut" style="font-size:.72rem">O chip da campanha costuma ser a melhor escolha: o principal é o que fala com cliente o dia inteiro.</small>
        </div>
        {% endif %}
        {% if dist_qr %}
        <div class="agfield" style="margin-top:.7rem">
          <label>Mensagem</label>
          <div class="zapvars">
            {% for v in ['{lead}','{vendedor}','{empresa}','{link}','{primeira_mensagem}'] %}
            <button type="button" class="zapvar" data-v="{{ v }}">{{ v }}</button>
            {% endfor %}
          </div>
          <textarea class="fld" name="aviso_zap_texto" id="zaptexto" rows="6">{{ dist_cfg.aviso_zap_texto if dist_cfg else '' }}</textarea>
          <small class="mut" style="font-size:.72rem">Toque numa variável pra inserir onde o cursor estiver. <b>*asterisco*</b> vira negrito no WhatsApp. A mensagem do cliente entra cortada em 120 caracteres.</small>
        </div>
        <div class="distnote" style="margin-top:.6rem">Sem template e sem janela de 24h — o chip por QR entrega a qualquer hora. Se o chip escolhido cair, o vendedor <b>continua</b> recebendo por e-mail e push, e o lead continua sendo distribuído. No máximo um WhatsApp a cada 2 minutos por vendedor.</div>
        {# Twilio/Meta não têm chip pra escolher nem aceitam texto livre fora das 24h:
           lá o template é obrigatório, e é a única coisa que faz sentido pedir. #}
        {% else %}
        <div class="agfield" style="margin-top:.7rem">
          <label>Template do aviso <span style="font-weight:400;color:var(--txt-mut)">(Content SID “HX…” do Twilio ou o nome do template aprovado na Meta)</span></label>
          <input class="fld" name="aviso_template_sid" value="{{ dist_cfg.aviso_template_sid if dist_cfg else '' }}" placeholder="HX… ou nome_do_template">
          <small class="mut" style="font-size:.72rem">Seu WhatsApp é Twilio/Meta, então a janela de 24h obriga template — a variável {{ '{{1}}' }} é a empresa do lead. Num chip por QR isto não seria necessário.</small>
        </div>
        {% endif %}
      </div>
      {% if dist_qr %}<input type="hidden" name="aviso_template_sid" value="{{ dist_cfg.aviso_template_sid if dist_cfg else '' }}">{% endif %}
      <div class="agfield" style="margin-top:.7rem"><label>Vendedores na fila — marque quem participa e arraste ⠿ pra ordenar</label></div>
      <div id="distfila" style="display:flex;flex-direction:column;gap:.4rem;margin-top:.3rem">
        {% for m in dist_membros %}
        <div class="distrow" draggable="true">
          <span class="grip" title="Arraste pra reordenar">⠿</span>
          <label style="display:flex;align-items:center;gap:.55rem;flex:1;cursor:pointer">
            <input type="checkbox" name="vend" value="{{ m.id }}" {% if m.na_fila %}checked{% endif %}>
            <span class="dnm">{{ m.nome }}{% if not m.whatsapp %} <span class="mut" style="font-size:.72rem">· sem WhatsApp (só e-mail)</span>{% endif %}</span>
          </label>
        </div>
        {% else %}
        <p class="mut" style="font-size:.82rem;margin:.2rem 0">Convide vendedores na aba <b>Equipe</b> pra montar a fila.</p>
        {% endfor %}
      </div>
      <div style="display:flex;justify-content:flex-end;margin-top:.8rem"><button class="pbtn">Salvar distribuição</button></div>
    </form>
  </div>
  <script>
  (function(){
    // dobra do aviso por WhatsApp: some quando o interruptor apaga, mas os campos
    // continuam no formulário — quem desliga e liga de novo não perde o texto.
    var sw=document.getElementById('zapsw'),cx=document.getElementById('zapbox');
    if(sw&&cx)sw.addEventListener('change',function(){cx.hidden=!sw.checked;});
    // inserir variável ONDE O CURSOR ESTÁ, não no fim: quem clica com o cursor no
    // meio da frase espera que caia ali.
    var ta=document.getElementById('zaptexto');
    document.querySelectorAll('.zapvar').forEach(function(b){
      b.addEventListener('click',function(){
        if(!ta)return; var v=b.dataset.v, i=ta.selectionStart||0, f=ta.selectionEnd||0;
        ta.value=ta.value.slice(0,i)+v+ta.value.slice(f);
        ta.focus(); ta.selectionStart=ta.selectionEnd=i+v.length;
      });
    });
  })();
  (function(){
    var box=document.getElementById('distfila'); if(!box) return; var drag=null;
    box.querySelectorAll('.distrow').forEach(function(row){
      row.addEventListener('dragstart',function(){drag=row;row.style.opacity=.4;});
      row.addEventListener('dragend',function(){row.style.opacity=1;drag=null;});
      row.addEventListener('dragover',function(e){e.preventDefault();
        if(!drag||drag===row)return; var r=row.getBoundingClientRect();
        box.insertBefore(drag,(e.clientY-r.top)/r.height<.5?row:row.nextSibling);});
    });
  })();
  </script>

  <div class="cx-card">
    <h3>🧠 Treinar o agente</h3>
    <p class="mut" style="font-size:.82rem;margin:.1rem 0 .6rem">Quanto melhor a base, melhor ele responde. Já funciona antes mesmo do WhatsApp.</p>
    <form method="post" action="/painel/prospeccao/comunicacao/agente-instrucoes">
      <div class="agfield"><label>Sobre a empresa / instruções gerais</label>
        <textarea class="fld" name="texto" rows="3" placeholder="Ex: Somos a X, ajudamos comércios de Teresina a... Nunca prometa prazo/desconto sem confirmar com o vendedor.">{{ ag_conhec.instrucoes }}</textarea></div>
      <div style="display:flex;justify-content:flex-end;margin-top:.4rem"><button class="pbtn ghost">Salvar instruções</button></div>
    </form>
    <div style="border-top:1px solid var(--borda);margin:.8rem 0;padding-top:.7rem">
      <label class="agfield" style="display:block;margin-bottom:.4rem"><span style="font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;color:var(--txt-mut)">Perguntas & respostas</span></label>
      {% for q in ag_conhec.faqs %}
      <div class="agfaq"><div class="q" style="flex:1"><b>{{ q.pergunta }}</b><p>{{ q.resposta }}</p></div>
        <form method="post" action="/painel/prospeccao/comunicacao/agente-faq" style="margin:0"><input type="hidden" name="excluir" value="{{ q.id }}"><button class="pbtn ghost" style="padding:.3rem .55rem;font-size:.76rem" title="Remover">🗑</button></form></div>
      {% else %}<p class="mut" style="font-size:.82rem">Nenhuma pergunta cadastrada ainda.</p>{% endfor %}
      <form method="post" action="/painel/prospeccao/comunicacao/agente-faq" style="margin-top:.5rem">
        <div class="aggrid">
          <div class="agfield"><label>Pergunta</label><input class="fld" name="pergunta" placeholder="Quanto custa?"></div>
          <div class="agfield"><label>Resposta</label><input class="fld" name="resposta" placeholder="A partir de R$149/mês. Posso montar um orçamento?"></div>
        </div>
        <div style="display:flex;justify-content:flex-end;margin-top:.4rem"><button class="pbtn">+ Adicionar pergunta</button></div>
      </form>
    </div>
  </div>

  <div class="cx-card">
    <h3>📇 Perfil do 1º contato (WhatsApp)</h3>
    <p class="mut" style="font-size:.82rem;margin:.1rem 0 .6rem">Personaliza o convite frio e as respostas dos botões. Quem toca <b>“Quero te conhecer”</b> recebe seu Instagram; quem toca <b>“Quero o material”</b> recebe o material da campanha.</p>
    <form method="post" action="/painel/prospeccao/comunicacao/prospec-perfil" enctype="multipart/form-data">
      <div class="aggrid">
        <div class="agfield"><label>Seu Instagram</label><input class="fld" name="prospec_instagram" value="{{ perfil.instagram }}" placeholder="@seuperfil"><small class="mut" style="font-size:.72rem">@ ou link — é a referência que o lead recebe pra te conhecer.</small></div>
        <div class="agfield"><label>Seu cargo</label><input class="fld" name="prospec_cargo" value="{{ perfil.cargo }}" placeholder="CEO"><small class="mut" style="font-size:.72rem">Aparece no convite: “Aqui é o Fulano, {cargo} da Empresa…”.</small></div>
      </div>
      <div class="agfield" style="margin-top:.6rem">
        <label>Material padrão</label>
        <small class="mut" style="font-size:.72rem;display:block;margin-bottom:.3rem">Enviado no “Quero o material” quando o lead <b>não está em campanha</b>. Dentro de campanha, vale o material da campanha.</small>
        <input type="hidden" name="prospec_material_tipo" id="pm-tipo" value="{{ perfil.material_tipo }}">
        <div class="pmtabs">
          <button type="button" class="pmtab {% if perfil.material_tipo=='link' %}on{% endif %}" onclick="pmtab(this,'link')">🔗 Link</button>
          <button type="button" class="pmtab {% if perfil.material_tipo=='pdf' %}on{% endif %}" onclick="pmtab(this,'pdf')">📄 PDF</button>
          <button type="button" class="pmtab {% if perfil.material_tipo=='video' %}on{% endif %}" onclick="pmtab(this,'video')">🎬 Vídeo</button>
          <button type="button" class="pmtab {% if perfil.material_tipo=='foto' %}on{% endif %}" onclick="pmtab(this,'foto')">🖼 Foto</button>
        </div>
        <div class="pmpane {% if perfil.material_tipo=='link' %}on{% endif %}" data-pm="link"><input class="fld" name="material_link" value="{% if perfil.material_tipo=='link' %}{{ perfil.material }}{% endif %}" placeholder="https://sua-apresentacao.com · site, página, proposta…"></div>
        <div class="pmpane {% if perfil.material_tipo=='video' %}on{% endif %}" data-pm="video"><input class="fld" name="material_video" value="{% if perfil.material_tipo=='video' %}{{ perfil.material }}{% endif %}" placeholder="Link do YouTube, Loom ou Google Drive"></div>
        <div class="pmpane {% if perfil.material_tipo=='pdf' %}on{% endif %}" data-pm="pdf">
          {% if perfil.material_tipo=='pdf' and perfil.material %}<div class="pmfile">📄 <a href="{{ perfil.material }}" target="_blank" rel="noopener" style="color:var(--verde-claro);text-decoration:none">material atual (PDF)</a><span class="mut" style="margin-left:auto;font-size:.74rem">enviar outro ↓</span></div>{% endif %}
          <label class="pmdrop">📄 Escolher o PDF <b>(clique aqui)</b><div style="font-size:.72rem;margin-top:.2rem">até 10 MB</div><input type="file" name="material_pdf" accept="application/pdf" hidden onchange="pmfile(this)"></label>
          <div class="pmfile pmpick" style="display:none"></div>
        </div>
        <div class="pmpane {% if perfil.material_tipo=='foto' %}on{% endif %}" data-pm="foto">
          {% if perfil.material_tipo=='foto' and perfil.material %}<img src="{{ perfil.material }}" alt="material" style="max-height:120px;max-width:100%;border-radius:8px;border:1px solid var(--borda);margin-bottom:.4rem">{% endif %}
          <label class="pmdrop">🖼 Escolher a imagem <b>(clique aqui)</b><div style="font-size:.72rem;margin-top:.2rem">JPG/PNG · até 6 MB</div><input type="file" name="material_foto" accept="image/*" hidden onchange="pmfile(this)"></label>
          <div class="pmfile pmpick" style="display:none"></div>
        </div>
      </div>
      <div style="display:flex;justify-content:flex-end;margin-top:.4rem"><button class="pbtn">Salvar perfil</button></div>
    </form>
  </div>
  <style>
    .pmtabs{display:flex;gap:.4rem;flex-wrap:wrap;margin:.15rem 0 .55rem}
    .pmtab{display:inline-flex;align-items:center;gap:.35rem;padding:.4rem .7rem;border-radius:999px;font-size:.8rem;font-weight:600;background:transparent;border:1px solid var(--borda);color:var(--mut);cursor:pointer}
    .pmtab:hover{color:var(--txt);border-color:var(--verde)}
    .pmtab.on{background:var(--verde);border-color:var(--verde);color:var(--sobre-verde)}
    .pmpane{display:none}.pmpane.on{display:block}
    .pmdrop{display:block;border:1px dashed var(--borda);border-radius:10px;background:var(--bg);padding:1rem;text-align:center;color:var(--mut);font-size:.85rem;cursor:pointer}
    .pmdrop:hover{border-color:var(--verde);color:var(--txt)}.pmdrop b{color:var(--verde-claro)}
    .pmfile{display:flex;align-items:center;gap:.55rem;border:1px solid var(--neon-borda);background:var(--neon-fundo);border-radius:10px;padding:.5rem .7rem;margin-top:.5rem;font-size:.84rem}
  </style>
  <script>
    function pmtab(btn,tipo){
      var A=btn.parentNode.querySelectorAll('.pmtab');
      for(var i=0;i<A.length;i++)A[i].classList.remove('on');
      btn.classList.add('on');
      document.getElementById('pm-tipo').value=tipo;
      var P=document.querySelectorAll('.pmpane');
      for(var j=0;j<P.length;j++)P[j].classList.toggle('on',P[j].getAttribute('data-pm')===tipo);
    }
    function pmfile(inp){
      var f=inp.files&&inp.files[0]; if(!f)return;
      var box=inp.closest('.pmpane').querySelector('.pmpick');
      if(box){var mb=(f.size/1048576).toFixed(1);box.style.display='flex';box.innerHTML='📎 <b>'+f.name+'</b> <span class="mut" style="margin-left:auto;font-size:.75rem">'+mb+' MB</span>';}
    }
  </script>
  {% endif %}

  {% else %}
  <p class="mut" style="margin:.8rem 0 0">Todos os canais num lugar só. As credenciais ficam no ambiente (Render); aqui você vê o status e conecta cada um quando o acesso libera.</p>
  <div class="cx-cc">
    <div class="cx-card">
      <h3>✉️ E-mail <span class="cx-stat {{ 'st-on' if canais.email else 'st-off' }}">● {{ 'Enviando' if canais.email else 'A configurar' }}</span></h3>
      <div class="cx-kv"><span>Principal (De:)</span><b>{{ canais.email_ident or remetente or '—' }}</b></div>
      <div class="cx-kv"><span>Secundária</span><b>{% if canais.email2_ident %}<span style="color:var(--verde-claro)">✓ {{ canais.email2_ident }}</span>{% else %}—{% endif %}</b></div>
      <div class="mut" style="font-size:.72rem">Cada campanha escolhe de qual caixa sai. As respostas de <b>ambas</b> caem no inbox.</div>
      {% if canais.email_ident %}{% if '@gmail.com' in (canais.email_ident|lower) %}
      <div style="font-size:.75rem;color:var(--ambar);margin-top:.35rem;border:1px solid var(--ambar-borda);background:#2a2113;border-radius:8px;padding:.45rem .6rem">⚠️ A principal é <b>Gmail pessoal</b>. Pra prospecção o ideal é o <b>e-mail do domínio</b> (Workspace): entrega melhor e sem o limite baixo do Gmail. Troque a caixa principal pelo domínio.</div>
      {% else %}
      <div style="font-size:.75rem;color:var(--verde-claro);margin-top:.35rem">✓ Principal em <b>domínio próprio</b> — ótimo pra entrega (confirme SPF/DKIM/DMARC no DNS).</div>
      {% endif %}{% endif %}
      {% if gerencia %}
      <form method="post" action="/painel/prospeccao/comunicacao/email-config" style="margin-top:.6rem">
        <input type="hidden" name="slot" value="principal">
        <label class="lbl">Caixa PRINCIPAL <span style="color:var(--mut);font-weight:400">— recomendo o e-mail do domínio</span></label>
        <input class="fld" name="endereco" type="email" placeholder="voce@seudominio.com" value="{{ canais.email_ident }}" style="margin-bottom:.35rem">
        <div style="display:flex;gap:.3rem;align-items:center;margin-bottom:.35rem">
          <input class="fld" name="senha" type="password" placeholder="senha de app (deixe vazio p/ manter)" autocomplete="new-password" style="margin:0">
          <button type="button" class="olho" title="ver o que está salvo" onclick="olhoSegredo(this,'email')">👁</button>
        </div>
        <div class="olho-msg mut" style="font-size:.74rem;margin-bottom:.35rem"></div>
        <input class="fld" name="host" placeholder="imap.gmail.com (padrão)" style="margin-bottom:.4rem">
        <div style="display:flex;gap:.4rem">
          <button class="pbtn">Salvar</button>
          <button type="button" class="pbtn ghost" id="etest-principal-btn" onclick="emailTestar('principal')" style="white-space:nowrap">Testar</button>
        </div>
        <div class="mut" id="etest-principal-msg" style="font-size:.78rem;margin-top:.4rem"></div>
      </form>
      <div style="margin-top:.7rem;border-top:1px solid var(--borda);padding-top:.6rem">
        {% if canais.email2_ident %}
        <div class="mut" style="font-size:.8rem">2ª caixa: <b style="color:var(--verde-claro)">✓ {{ canais.email2_ident }}</b> · <a href="javascript:void(0)" onclick="toggleEmail2()" style="color:var(--verde-claro)">editar / trocar</a></div>
        {% else %}
        <a href="javascript:void(0)" onclick="toggleEmail2()" style="color:var(--verde-claro);font-size:.82rem">＋ Adicionar 2ª caixa de e-mail (opcional)</a>
        {% endif %}
        <form id="email2-wrap" method="post" action="/painel/prospeccao/comunicacao/email-config" style="display:none;margin-top:.55rem">
          <input type="hidden" name="slot" value="secundario">
          <label class="lbl">Caixa SECUNDÁRIA <span style="color:var(--mut);font-weight:400">— ex.: seu Gmail</span></label>
          <input class="fld" name="endereco" type="email" placeholder="voce@gmail.com" value="{{ canais.email2_ident }}" style="margin-bottom:.35rem">
          <div style="display:flex;gap:.3rem;align-items:center;margin-bottom:.35rem">
            <input class="fld" name="senha" type="password" placeholder="senha de app (deixe vazio p/ manter)" autocomplete="new-password" style="margin:0">
            <button type="button" class="olho" title="ver o que está salvo" onclick="olhoSegredo(this,'email2')">👁</button>
          </div>
          <div class="olho-msg mut" style="font-size:.74rem;margin-bottom:.35rem"></div>
          <input class="fld" name="host" placeholder="imap.gmail.com (padrão)" style="margin-bottom:.4rem">
          <div style="display:flex;gap:.4rem">
            <button class="pbtn">Salvar</button>
            <button type="button" class="pbtn ghost" id="etest-secundario-btn" onclick="emailTestar('secundario')" style="white-space:nowrap">Testar</button>
          </div>
          <div class="mut" id="etest-secundario-msg" style="font-size:.78rem;margin-top:.4rem"></div>
        </form>
      </div>
      <div class="mut" style="margin-top:.4rem;font-size:.76rem">Gmail/Workspace: gere uma <b>senha de app</b> em myaccount.google.com/apppasswords e ligue o IMAP. A senha fica só nesta empresa.</div>
      <script>
      function toggleEmail2(){var w=document.getElementById('email2-wrap');if(w)w.style.display=(w.style.display==='none'||!w.style.display)?'block':'none';}
      function emailTestar(slot){var b=document.getElementById('etest-'+slot+'-btn'),m=document.getElementById('etest-'+slot+'-msg');if(!b)return;b.disabled=true;var t=b.textContent;b.textContent='Testando…';m.textContent='';m.style.color='';
        var body=new URLSearchParams();body.append('slot',slot);
        fetch('/painel/prospeccao/comunicacao/email-testar',{method:'POST',headers:{'X-Requested-With':'fetch','Content-Type':'application/x-www-form-urlencoded'},body:body}).then(function(r){return r.json();}).then(function(d){b.disabled=false;b.textContent=t;
          m.textContent=d.msg||d.erro||'—';m.style.color=d.ok?'var(--verde-claro)':'var(--ambar)';}).catch(function(){b.disabled=false;b.textContent=t;m.textContent='Falha de rede.';m.style.color='var(--ambar)';});}
      </script>
      <script>
      // Olhinho dos campos de senha/token. O valor salvo NÃO vem no HTML — só é
      // buscado no clique, e some do campo quando esconde de novo (nada de
      // segredo pendurado no DOM depois de fechar).
      function olhoSegredo(btn, campo){
        var wrap = btn.parentElement;
        var inp = wrap.querySelector('input');
        if(!inp) return;
        // o aviso é o irmão logo depois do par input+botão (não busca no form
        // inteiro: com 2 caixas de e-mail acharia sempre o da primeira)
        var prox = wrap.nextElementSibling;
        var aviso = (prox && prox.classList.contains('olho-msg')) ? prox : null;
        if(inp.type === 'text'){                       // já revelado -> esconde e limpa
          inp.type = 'password'; inp.value = ''; btn.textContent = '👁';
          btn.title = 'ver o que está salvo';
          if(aviso) aviso.textContent = '';
          return;
        }
        var original = btn.textContent; btn.textContent = '…'; btn.disabled = true;
        var body = new URLSearchParams(); body.append('campo', campo);
        fetch('/painel/prospeccao/comunicacao/revelar-segredo',
              {method:'POST', headers:{'X-Requested-With':'fetch',
               'Content-Type':'application/x-www-form-urlencoded'}, body:body})
          .then(function(r){ return r.json(); })
          .then(function(d){
            btn.disabled = false;
            if(!d.ok){ btn.textContent = original;
              if(aviso){ aviso.textContent = d.erro || 'Não consegui mostrar.';
                         aviso.style.color = 'var(--ambar)'; }
              return; }
            inp.type = 'text'; inp.value = d.valor; btn.textContent = '🙈';
            btn.title = 'esconder';
            inp.focus(); inp.select();                 // pronto pra copiar
            if(aviso){ aviso.textContent = 'Cuidado: senha à mostra. Clique no 🙈 pra esconder.';
                       aviso.style.color = 'var(--mut)'; }
          })
          .catch(function(){ btn.disabled = false; btn.textContent = original;
            if(aviso){ aviso.textContent = 'Falha de rede.'; aviso.style.color = 'var(--ambar)'; } });
      }
      </script>
      {% else %}<div class="mut" style="margin-top:.4rem;font-size:.8rem">SMTP (Google Workspace). Prospecção fria ✓</div>{% endif %}
    </div>
    <div class="cx-card">
      <h3>💬 WhatsApp <span class="cx-stat {{ 'st-on' if canais.whatsapp else 'st-off' }}">● {{ ('Serviço de QR ligado — veja o status da sessão abaixo' if canais.wa_provedor == 'qr' else 'Conectado') if canais.whatsapp else 'A configurar' }}</span></h3>
      {# A "última recebida" saiu daqui e desceu pra dentro de cada aba de chip: no
         nível da empresa ela não diz em QUAL número a mensagem entrou, e essa é
         justamente a pergunta de quem desconfia que um dos dois parou. #}
      {% if gerencia %}
      <div class="mut" style="font-size:.8rem;margin-bottom:.1rem">Como este cliente conecta o WhatsApp:</div>
      {# Uma aba por chip. A pílula sempre foi "escolha um jeito de conectar e veja o
         painel dele" — dois chips são dois painéis, e cabem no mesmo mecanismo. Com
         um chip só (todas as contas hoje) a quarta aba não existe e o rótulo é
         "QR Code", igualzinho ao de antes. #}
      {% set tem2 = canais.chips|length > 1 %}
      {# a 4ª aba existe SEMPRE: é por ela que se cria o chip 2. Sem chip criado
         ela mostra só o campo de apelido e o botão — nada de sessão, nada de QR. #}
      <div class="waseg" style="--waseg-n:4">
        <label class="{{ 'on' if canais.wa_provedor not in ['cloud','qr'] else '' }}"><input type="radio" name="wa_prov_sel" value="twilio" {{ 'checked' if canais.wa_provedor not in ['cloud','qr'] else '' }} onchange="waProv('twilio')">Twilio</label>
        <label class="{{ 'on' if canais.wa_provedor=='cloud' else '' }}"><input type="radio" name="wa_prov_sel" value="cloud" {{ 'checked' if canais.wa_provedor=='cloud' else '' }} onchange="waProv('cloud')">Número próprio</label>
        <label class="{{ 'on' if canais.wa_provedor=='qr' else '' }}"><input type="radio" name="wa_prov_sel" value="qr" {{ 'checked' if canais.wa_provedor=='qr' else '' }} onchange="waProv('qr')">{{ 'QR Code 1' if tem2 else 'QR Code' }}</label>
        <label class="dois"><input type="radio" name="wa_prov_sel" value="qr2" onchange="waProv('qr2')">{{ 'QR Code 2' if tem2 else '+ 2º chip' }}</label>
      </div>

      <div id="wa-twilio" class="waprov">
        <div style="font-weight:600;font-size:.85rem;margin-bottom:.15rem">Via Twilio (BSP)</div>
        <div class="mut" style="font-size:.78rem">Credenciais globais no Render:</div>
        <div class="cx-env"><b>TWILIO_ACCOUNT_SID</b>=•••••<br><b>TWILIO_AUTH_TOKEN</b>=•••••</div>
        <form method="post" action="/painel/prospeccao/comunicacao/canal-numero" style="margin-top:.5rem">
          <input type="hidden" name="canal" value="whatsapp"><input type="hidden" name="provedor" value="twilio">
          <label class="lbl">Número desta empresa</label>
          <div style="display:flex;gap:.4rem">
            <input class="fld" name="numero" inputmode="tel" placeholder="+17602847678" value="{{ canais.numeros.get('whatsapp','') if canais.wa_provedor!='cloud' else '' }}">
            <button class="pbtn" style="white-space:nowrap">Salvar</button>
          </div>
        </form>
        <div class="mut" style="margin-top:.4rem;font-size:.78rem">No painel Twilio, aponte o webhook pra <code>/webhooks/twilio</code>.</div>
      </div>

      <div id="wa-cloud" class="waprov">
        <div style="font-weight:600;font-size:.85rem;margin-bottom:.15rem">Cloud API oficial da Meta</div>
        <div class="mut" style="font-size:.78rem">Número <b>próprio</b> do cliente, direto na Meta — sem Twilio, grátis e sem risco de ban. Usa o mesmo app da Meta (env) + webhook <code>/webhooks/meta</code>.</div>
        <div style="font-size:.72rem;color:var(--txt-mut);background:#181a17;border:1px solid var(--borda);border-radius:8px;padding:.45rem .6rem;margin:.5rem 0">⚠️ Ao registrar o número na Cloud API ele <b>sai do app do celular</b> e passa a ser operado pelo sistema. Requer conta Meta Business + verificar o número.</div>
        <form method="post" action="/painel/prospeccao/comunicacao/canal-numero" style="margin-top:.2rem">
          <input type="hidden" name="canal" value="whatsapp"><input type="hidden" name="provedor" value="cloud">
          <label class="lbl">Número (opcional, só p/ exibir)</label>
          <input class="fld" name="numero" inputmode="tel" placeholder="+5586999999999" value="{{ canais.numeros.get('whatsapp','') if canais.wa_provedor=='cloud' else '' }}" style="margin-bottom:.35rem">
          <label class="lbl">Phone Number ID (Cloud API)</label>
          <input class="fld" name="wa_phone_id" placeholder="ex.: 123456789012345" value="{{ canais.wa_phone_id if canais.wa_provedor=='cloud' else '' }}" style="margin-bottom:.35rem">
          <label class="lbl">Access Token</label>
          <div style="display:flex;gap:.3rem;align-items:center;margin-bottom:.4rem">
            <input class="fld" name="token" type="password" placeholder="token da Cloud API {% if canais.tokens_set.get('whatsapp') and canais.wa_provedor=='cloud' %}(salvo — vazio mantém){% endif %}" autocomplete="new-password" style="margin:0">
            <button type="button" class="olho" title="ver o que está salvo" onclick="olhoSegredo(this,'whatsapp')">👁</button>
          </div>
          <div class="olho-msg mut" style="font-size:.74rem;margin-bottom:.4rem"></div>
          <button class="pbtn">Conectar número próprio</button>
        </form>
        {% if canais.wa_provedor=='cloud' and canais.wa_phone_set %}
        <div style="border:1px solid var(--borda);border-radius:9px;padding:.55rem .6rem;margin-top:.6rem;background:var(--bg)">
          <div class="lbl" style="margin:0 0 .35rem">Testar envio</div>
          <div style="display:flex;gap:.4rem;flex-wrap:wrap;align-items:center">
            <input class="fld" id="watest-num" inputmode="tel" placeholder="Seu nº (+55...)" style="flex:1;min-width:150px">
            <button type="button" class="pbtn ghost" id="watest-btn" onclick="waTestar()" style="white-space:nowrap">📤 Enviar teste</button>
          </div>
          <div class="mut" id="watest-msg" style="font-size:.78rem;margin-top:.35rem"></div>
        </div>
        <script>
        function waTestar(){var b=document.getElementById('watest-btn'),m=document.getElementById('watest-msg'),n=document.getElementById('watest-num');if(!b)return;
          var num=(n&&n.value||'').trim();b.disabled=true;var t=b.textContent;b.textContent='Enviando…';m.textContent='';m.style.color='';
          var fd=new FormData();fd.append('numero',num);
          fetch('/painel/prospeccao/comunicacao/whatsapp-testar',{method:'POST',body:fd,headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
            b.disabled=false;b.textContent=t;m.textContent=d.msg||d.erro||'—';m.style.color=d.ok?'var(--verde-claro)':'var(--ambar)';}).catch(function(){b.disabled=false;b.textContent=t;m.textContent='Falha de rede.';m.style.color='var(--ambar)';});}
        </script>
        {% endif %}
        <div class="mut" style="margin-top:.4rem;font-size:.78rem">Pega o <b>Phone Number ID</b> e o <b>token</b> em developers.facebook.com → seu app → WhatsApp → API Setup. Assine <code>messages</code>.</div>
      </div>

      <div id="wa-qr" class="waprov">
        {% set chip1 = (canais.chips[0] if canais.chips else None) %}
        <div style="font-weight:600;font-size:.85rem;margin-bottom:.15rem">QR Code (tipo WhatsApp Web)</div>
        {# desceu do topo da aba: agora diz de QUAL chip é a última mensagem #}
        {% if chip1 %}
        <div class="mut" style="font-size:.8rem;margin-bottom:.35rem">📥 Última recebida neste chip:
          {% if chip1.ultima %}<b style="color:var(--verde-claro)">{{ chip1.ultima }}</b>{% if chip1.sem_receber %}
          <span style="color:var(--ambar)">({{ chip1.sem_receber }} — se o cliente está mandando mensagem, reconecte abaixo)</span>{% endif %}
          {% else %}<span style="color:var(--mut)">nenhuma ainda</span>{% endif %}</div>
        {% endif %}
        <div style="font-size:.78rem;color:var(--ambar);background:#2a2113;border:1px solid var(--ambar-borda);border-radius:8px;padding:.55rem .7rem">
          Usa o número <b>como está</b>, sem migrar nada. Mas: <b>viola os termos</b> do WhatsApp (risco de banimento) e depende de um serviço à parte sempre-ligado.
        </div>
        <div style="display:flex;gap:.4rem;margin-top:.6rem;flex-wrap:wrap">
          <!-- nasce "Verificando…" de propósito. Nascer "📱 Gerar QR" era afirmar
               "não há sessão" antes de ter perguntado — e numa empresa conectada a
               tela ficava assim até alguém trocar de aba. Quem responde é o qrShow;
               se a consulta falhar, o qrPoll devolve o botão ao estado usável. -->
          <button type="button" class="pbtn" id="qr-btn" onclick="qrIniciar()" disabled>Verificando…</button>
          <button type="button" class="pbtn ghost" id="qr-sair" onclick="qrSair()" style="display:none">Desconectar</button>
          <!-- só aparece com a sessão fora do ar E SEM CREDENCIAL: com a sessão de pé
               (ou com a conta apenas estacionada pelo disjuntor, que volta sozinha) o
               celular ressincroniza e reescreveria parte do que foi apagado. -->
          <button type="button" class="pbtn ghost" id="qr-apagar" onclick="qrApagar()"
                  style="display:none;border-color:var(--ambar-borda);color:var(--ambar)">🗑️ Apagar histórico</button>
        </div>
        <div class="mut" id="qr-retencao" style="font-size:.75rem;margin-top:.45rem;display:none">
          Desconectar <b>não apaga</b> as conversas — elas ficam aqui e voltam a
          funcionar quando você reconectar. Depois de <b>30 dias</b> desconectado,
          o sistema apaga o histórico deste WhatsApp automaticamente.
        </div>
        <div style="font-size:.73rem;color:var(--txt-mut);margin-top:.4rem">
          Caiu ou está reconectando? <b>Espere</b> — o sistema religa sozinho. O
          <b>Desconectar</b> apaga a sessão e exige QR novo; depois dele a conta fica
          um tempo conectada <b>sem receber</b>. Use só pra trocar de número.
        </div>
        <!-- ═══ O RELÓGIO DO QR ══════════════════════════════════════════════
             O código não vive pra sempre e a tela nunca disse isso. Medido na
             fonte do Baileys (Socket/socket.js:464 e :478): o PRIMEIRO código
             dura 60s, cada um dos seguintes dura 20s. Quando os `ref` do lote
             acabam, o Baileys derruba a conexão com 'QR refs attempts ended'
             (408) — e o nosso handler de close religa em 2,5s e pega um LOTE
             NOVO. Ou seja: não expira pra sempre, mas some da tela por alguns
             segundos, e era isso que parecia defeito.

             Referência real: a conta 23 pareou em 26/08 às 11:17 e levou 42s —
             passou com 18 de folga, no primeiro código. Quem não estiver com o
             celular já aberto em Aparelhos conectados não passa.

             Classes com prefixo `zqr-` e compartilhadas pelos DOIS chips; os ids
             continuam separados, como manda a regra do cartão do chip 2. -->
        <style>
          .zqr-relogio{display:flex;align-items:center;gap:.7rem;margin-top:.6rem}
          .zqr-anel{width:52px;height:52px;border-radius:50%;flex:0 0 auto;display:grid;
            place-items:center;position:relative;
            background:conic-gradient(var(--zqr-cor,var(--verde)) calc(var(--zqr-p,1)*360deg),var(--borda) 0)}
          .zqr-anel::after{content:"";position:absolute;inset:5px;border-radius:50%;background:var(--card)}
          .zqr-anel b{position:relative;z-index:1;font-size:1rem;font-weight:600;
            color:var(--zqr-cor,var(--verde));font-variant-numeric:tabular-nums}
          .zqr-txt b{display:block;font-size:.85rem;color:var(--txt)}
          .zqr-txt span{font-size:.76rem;color:var(--txt-mut)}
          .zqr-passos{margin:.55rem 0 0;padding-left:1.15rem;font-size:.78rem;
            color:var(--txt-mut);line-height:1.75}
          .zqr-passos b{color:var(--txt)}
          .zqr-fim{border-radius:8px;padding:.55rem .7rem;font-size:.78rem;line-height:1.5;
            margin-top:.6rem;border:1px solid var(--ambar-borda);background:var(--ambar-fundo);color:#F2D08A}
          @media(prefers-reduced-motion:reduce){.zqr-anel{transition:none}}
        </style>
        <div id="qr-box" style="margin-top:.6rem;text-align:center;display:none">
          <img id="qr-img" alt="QR do WhatsApp" style="width:220px;max-width:100%;border-radius:10px;background:#fff;padding:.4rem">
        </div>
        <div id="qr-relogio" class="zqr-relogio" style="display:none">
          <div id="qr-anel" class="zqr-anel"><b id="qr-seg">60</b></div>
          <div class="zqr-txt">
            <b id="qr-rel-tit">Escaneie agora</b>
            <span id="qr-rel-det">Este código vale por mais 60 segundos</span>
          </div>
        </div>
        <ol id="qr-passos" class="zqr-passos" style="display:none">
          <li>No celular, abra o <b>WhatsApp</b></li>
          <li>Toque em <b>Aparelhos conectados</b></li>
          <li>Toque em <b>Conectar aparelho</b> e aponte para esta tela</li>
        </ol>
        <div id="qr-fim" class="zqr-fim" style="display:none">
          O tempo deste código acabou e estamos buscando um novo — <b>nada foi perdido</b>.
          Deixe o WhatsApp aberto em <b>Aparelhos conectados</b> que ele aparece em instantes.
        </div>
        <div id="qr-sync" style="margin-top:.6rem;display:none">
          <div style="font-size:.78rem;margin-bottom:.3rem">📥 Sincronizando conversas dos últimos 30 dias… <b id="qr-sync-pct">0%</b></div>
          <div style="height:8px;border-radius:5px;background:var(--bg);overflow:hidden;border:1px solid var(--borda)">
            <div id="qr-sync-bar" style="height:100%;width:0%;background:var(--verde);transition:width .4s"></div>
          </div>
        </div>
        <div class="mut" id="qr-msg" style="font-size:.78rem;margin-top:.45rem"></div>

        <!-- ═══ APELIDO DO CHIP 1 e o CHIP 2 ═══════════════════════════════════
             Tudo daqui até o fim do bloco é ADITIVO: nenhuma linha acima foi
             tocada. O chip 1 continua com os mesmos ids, as mesmas funções e o
             mesmo polling de sempre — o cartão do chip 2 tem os seus (`c2-*`) e
             fala com as mesmas rotas passando `chip=<id>`. Duas sessões, dois
             cartões, dois pollings; um não alcança o outro nem por engano.       -->
        <div style="margin-top:.7rem;border-top:1px solid var(--borda);padding-top:.7rem">
          <label class="lbl" style="font-size:.72rem">Apelido deste chip</label>
          <div style="display:flex;gap:.4rem">
            <input class="fld" id="c1-apelido" maxlength="60" placeholder="ex.: Agência Alfa"
                   value="{{ (canais.chips[0].apelido if canais.chips else '') }}">
            <button type="button" class="pbtn ghost" style="white-space:nowrap"
                    onclick="chipApelido('', 'c1-apelido')">Salvar</button>
          </div>
          <div class="mut" style="font-size:.72rem;margin-top:.3rem">
            É este nome que aparece no inbox e que o relatório agrupa. Não muda o
            nome da empresa em contrato, cobrança ou e-mail.
          </div>
        </div>

        <!-- APARELHOS LIGADOS. Nasce escondido e só o JS mostra: afirmar "nenhum
             aparelho" antes de ter perguntado seria pior que não dizer nada. -->
<div id="wa-aps" style="margin-top:.7rem;border-top:1px solid var(--borda);padding-top:.7rem">
          <div style="font-weight:600;font-size:.82rem;margin-bottom:.35rem">Quem ainda responde por fora</div>
          <div id="wa-aps-n" style="display:flex;gap:.4rem;flex-wrap:wrap;font-size:.78rem"></div>
          <!-- SOB DEMANDA, NUNCA EM INTERVALO. Perguntar os aparelhos é uma consulta
               ao WhatsApp; repetir isso sozinho a cada poucos segundos é tráfego de
               robô num cliente não oficial — o caminho curto pro número ser banido,
               que é a pior forma de derrubar a conexão. Quem pede é o dedo. -->
          <button type="button" class="pbtn ghost sm" id="wa-aps-btn" onclick="apsPuxa()"
                  style="margin-top:.5rem;font-size:.76rem">Conferir aparelhos ligados</button>
          <div class="mut" id="wa-aps-dica" style="font-size:.74rem;margin-top:.45rem"></div>
        </div>
        <script>
        // Dois números de fontes DIFERENTES: os aparelhos dizem que a porta
        // existe (e dependem do WhatsApp responder); a saída por fora sai do
        // banco e diz se alguém passa por ela. Um cobre o outro.
        // Os ids entram por parâmetro pra mesma função servir os dois chips. O
        // PADRÃO é o do chip 1, então as chamadas que já existiam continuam iguais.
        function apsPinta(j,idN,idD){
          var cx=document.getElementById(idN?idN.replace(/-n$/,''):'wa-aps'),
              n=document.getElementById(idN||'wa-aps-n'),
              d=document.getElementById(idD||'wa-aps-dica');
          if(!cx||!n||!d)return;
          if(!j||!j.ok){cx.style.display='none';return;}
          var a=j.aparelhos,f=j.fora||{},chips=[];
          if(a){
            chips.push('<span class="chip">📱 celular do dono</span>');
            chips.push('<span class="chip" style="border-color:var(--verde);color:var(--verde)">⚡ Zaq</span>');
            if(a.outros>0)
              chips.push('<span class="chip" style="border-color:var(--ambar-borda);color:var(--ambar)">⚠️ '
                +a.outros+' outro'+(a.outros>1?'s':'')+'</span>');
          }
          if(f.total>0){
            var cor=f.pct>=50?'var(--ambar)':'var(--txt-mut)';
            chips.push('<span class="chip" style="color:'+cor+'">'+f.pct+'% saiu por fora em '+f.dias+' dias</span>');
          }
          n.innerHTML=chips.join('');
          if(a && a.outros>0){
            d.innerHTML='Cada "outro" é um WhatsApp Web ou aparelho ligado neste número — '
              +'o que sai por ali chega aqui <b>sem o nome de quem falou</b>. '
              +'Desligar é no celular dono da conta: <b>Aparelhos conectados</b>.';
          } else if(a){
            d.textContent='Só o celular do dono e o Zaq. É o estado que você quer.';
          } else {
            d.textContent='Não consegui perguntar ao WhatsApp agora (sessão fora do ar).';
          }
          cx.style.display='block';
        }
        var _apsOcupado=false;
        function apsPuxa(){
          if(_apsOcupado)return;                       // clique nervoso não vira rajada
          _apsOcupado=true;
          var b=document.getElementById('wa-aps-btn');
          if(b){b.disabled=true;b.textContent='Perguntando…';}
          fetch('/painel/prospeccao/comunicacao/whatsapp-aparelhos?perguntar=1')
            .then(function(r){return r.json();}).then(apsPinta)
            .catch(function(){})
            .then(function(){_apsOcupado=false;
              if(b){b.disabled=false;b.textContent='Conferir aparelhos ligados';}});
        }
        // a saída por fora vem do BANCO: não toca no WhatsApp, então pode carregar
        // junto com a página e ficar sempre à vista
        document.addEventListener('DOMContentLoaded',function(){
          fetch('/painel/prospeccao/comunicacao/whatsapp-aparelhos')
            .then(function(r){return r.json();}).then(apsPinta).catch(function(){});
        });
        var _qrTimer=null;
        // ═══ RELÓGIO DO QR — chip 1 ════════════════════════════════════════════
        // Os tempos são os do Baileys (Socket/socket.js:464 e :478): 60s no
        // primeiro código do lote, 20s em cada um dos seguintes.
        //
        // A conta do tempo é do LADO DE CÁ porque o serviço não manda quando o
        // código nasceu — e por isso ela é conservadora: o polling é de 3s, então
        // a gente pode descobrir um código novo até 3s depois de ele existir.
        // Descontar esses 3s faz o número na tela ser um piso, não uma promessa.
        // Dizer 57 quando são 60 é chato; dizer 60 quando são 57 é mentir pro
        // cliente bem na hora em que ele está com o celular na mão.
        var QR_1O=60, QR_SEG=20, QR_ATRASO=3;
        var _qrSrc=null, _qrN=0, _qrResta=0, _qrTick=null, _qrVao=false;
        function qrRelPinta(){
          var anel=document.getElementById('qr-anel'), seg=document.getElementById('qr-seg'),
              tit=document.getElementById('qr-rel-tit'), det=document.getElementById('qr-rel-det');
          if(!anel)return;
          var total=(_qrN<=1?QR_1O:QR_SEG), r=Math.max(0,_qrResta);
          anel.style.setProperty('--zqr-p', total?r/total:0);
          anel.style.setProperty('--zqr-cor', r<=10?'var(--ambar)':'var(--verde)');
          if(seg)seg.textContent=r;
          if(!tit||!det)return;
          if(r<=0){tit.textContent='Buscando um código novo';det.textContent='Aguarde alguns segundos';}
          else if(r<=10){tit.textContent='Vai trocar de código';
            det.textContent='Um novo aparece em '+r+' s — pode esperar';}
          else if(_qrN<=1){tit.textContent='Escaneie agora';
            det.textContent='Este código vale por mais '+r+' segundos';}
          else{tit.textContent='Código renovado';
            det.textContent='O anterior venceu — aponte para este ('+r+' s)';}
        }
        // Um código novo entrou na tela: reinicia a conta. `primeiro` distingue os
        // 60s iniciais dos 20s dos seguintes.
        function qrRelNovo(){
          _qrResta=Math.max(1,(_qrN<=1?QR_1O:QR_SEG)-QR_ATRASO);
          if(_qrTick)clearInterval(_qrTick);
          _qrTick=setInterval(function(){_qrResta--;qrRelPinta();
            if(_qrResta<=0){qrFimMostra(true);}},1000);
          qrFimMostra(false); qrRelPinta();
        }
        function qrFimMostra(v){var f=document.getElementById('qr-fim');if(f)f.style.display=v?'block':'none';}
        function qrRelPara(){
          if(_qrTick){clearInterval(_qrTick);_qrTick=null;}
          _qrSrc=null; _qrN=0; _qrVao=false;
          var r=document.getElementById('qr-relogio'), p=document.getElementById('qr-passos');
          if(r)r.style.display='none'; if(p)p.style.display='none'; qrFimMostra(false);
        }
        // O vão entre um lote de códigos e o seguinte. Fica aqui porque o estado
        // que o serviço reporta nesse intervalo NÃO é 'aguardando_qr': quando os
        // `ref` acabam o Baileys derruba com 408, o handler de close marca
        // 'reconectando' e só 2,5s depois nasce o socket com o lote novo. Tratar
        // isso como "não é hora do relógio" desmontaria a tela bem no vão — que é
        // exatamente o sumiço calado que este bloco existe pra consertar.
        function qrVaoEntra(){
          if(_qrTick){clearInterval(_qrTick);_qrTick=null;}
          _qrSrc=null;      // o código do lote velho não vale mais
          _qrN=0;           // o próximo é o PRIMEIRO do lote novo: 60s, não 20s
          _qrVao=true;
          var r=document.getElementById('qr-relogio'), p=document.getElementById('qr-passos');
          if(r)r.style.display='none'; if(p)p.style.display='none';
          qrFimMostra(true);
        }
        // Chamada de dentro do qrShow, com a resposta do serviço já na mão.
        function qrRelogio(d){
          var rel=document.getElementById('qr-relogio'), pas=document.getElementById('qr-passos');
          if(!d){qrRelPara();return;}
          if(d.status==='reconectando'&&(_qrN>0||_qrVao)){qrVaoEntra();return;}
          if(d.status!=='aguardando_qr'){qrRelPara();return;}
          if(d.qr){
            // o serviço manda o QR como data-URL; mudou a string = código novo
            if(d.qr!==_qrSrc){_qrVao=false;_qrSrc=d.qr;_qrN++;
              if(rel)rel.style.display='flex'; if(pas)pas.style.display='block';
              qrRelNovo();}
          }else{
            // 'aguardando_qr' sem imagem também é vão — o socket subiu e o código
            // ainda não chegou
            qrVaoEntra();
          }
        }
        function qrShow(d){var box=document.getElementById('qr-box'),img=document.getElementById('qr-img'),
            msg=document.getElementById('qr-msg'),sair=document.getElementById('qr-sair'),btn=document.getElementById('qr-btn'),
            sync=document.getElementById('qr-sync'),syncBar=document.getElementById('qr-sync-bar'),syncPct=document.getElementById('qr-sync-pct');
          // SEM STATUS NÃO SE CONCLUI NADA. O /iniciar devolve `status` nulo quando o
          // serviço de QR não responde, e seguir daqui trataria isso como
          // desconectado: escondia "Desconectar", mostrava "Gerar QR" e — pior —
          // liberava o "Apagar histórico" numa empresa que talvez esteja conectada.
          if(!d||!d.status){qrIndefinido();return;}
          var conectado=d.status==='conectado';
          // conectado tira o QR da tela na hora — nada de deixar a imagem velha
          // parada aí sem dizer nada (era exatamente essa a queixa).
          if(d.qr&&!conectado){img.src=d.qr;box.style.display='block';}else{box.style.display='none';}
          qrRelogio(d);   // relógio, passo a passo e o aviso do vão entre lotes
          if(sync)sync.style.display=(conectado&&d.sincronizando)?'block':'none';
          if(syncBar)syncBar.style.width=(d.sync_progress||0)+'%';
          if(syncPct)syncPct.textContent=(d.sync_progress||0)+'%';
          if(msg)msg.textContent=(conectado&&d.sincronizando)?'':(d.msg||'');
          // QUEM MANDA AQUI É A CREDENCIAL, NÃO O STATUS DA SESSÃO.
          //
          // Os dois botões liam `status==='desconectado'`, e funcionou enquanto
          // 'desconectado' significava na prática "não tem credencial". O disjuntor da
          // guerra de sessão (#520) criou um estado que não existia: a conta é
          // ESTACIONADA de propósito — fica 'desconectado' e continua PAREADA, e o vigia
          // a retoma sozinho em minutos. Isso quebrava os dois lados:
          //   • "Desconectar" (que APAGA a credencial) sumia justo em quem tem credencial
          //     pra apagar — a conta 35 ficou sem saída nenhuma pela tela;
          //   • "Apagar histórico" APARECIA numa conta que vai voltar sozinha, que é
          //     exatamente o resultado pela metade que a barreira existe pra impedir
          //     (o celular ressincroniza e reescreve parte do que foi apagado).
          // `pareada` responde a pergunta certa, e os dois voltam a ser exclusivos.
          //
          // null = o serviço não soube dizer (banco fora do ar, ou versão antiga que
          // ainda não manda o campo): cai no comportamento antigo em vez de apostar.
          var pareada=(d.pareada===undefined||d.pareada===null)?(d.status!=='desconectado'):!!d.pareada;
          if(sair)sair.style.display=pareada?'inline-flex':'none';
          var apg=document.getElementById('qr-apagar'),ret=document.getElementById('qr-retencao');
          // continua exigindo sessão fora do ar — com ela de pé o celular sincroniza e
          // reescreve o que foi apagado — E AGORA também credencial nenhuma.
          var podeApagar=(d.status==='desconectado')&&!pareada;
          if(apg)apg.style.display=podeApagar?'inline-flex':'none';
          if(ret)ret.style.display=podeApagar?'block':'none';
          // chegou resposta: desarma o cão de guarda, o botão sai do "Verificando…"
          // e volta a ser clicável
          if(_qrEspera){clearTimeout(_qrEspera);_qrEspera=null;}
          // CONECTADO NÃO É "RECONECTAR". O botão dizia isso e não reconectava nada:
          // o /iniciar devolve a sessão viva sem tocar nela (services/wa-qr:1696), e
          // o painel nunca manda {forcar:true}. Prometer uma ação que não acontece é
          // pior que não ter botão — quem clica acha que consertou.
          //
          // CONTINUA CLICÁVEL de propósito. Desabilitar tiraria a única saída de quem
          // está "conectado" e mudo — o caso que a Doce Mell já viveu —, e este
          // arquivo já pagou por botão travado: é por isso que existe o cão de guarda
          // do "Verificando…". Clicar aqui reconsulta e, se a sessão tiver morrido por
          // baixo, religa. Só o texto e o peso mudam.
          if(btn){btn.textContent=conectado?'✓ Conectado':'📱 Gerar QR';
            btn.classList.toggle('ghost',conectado);
            btn.title=conectado?'A sessão está de pé. Clique só se desconfiar do status — verifica sem derrubar nada.':'';
            btn.disabled=false;}
          if(msg)msg.style.color=conectado?'var(--verde-claro)':'';
          // só para de perguntar quando realmente não tem mais nada mudando:
          // desconectado, ou conectado E já terminou de sincronizar o histórico.
          if(d.status==='desconectado'||(conectado&&!d.sincronizando)){if(_qrTimer){clearInterval(_qrTimer);_qrTimer=null;}}}
        // A consulta falhou (rede, serviço fora): NÃO dá pra dizer "desconectado" —
        // isso é justamente a mentira que o "Verificando…" existe pra evitar. Libera
        // o botão pro usuário poder agir e diz que não deu pra checar.
        function qrIndefinido(){var btn=document.getElementById('qr-btn'),m=document.getElementById('qr-msg');
          if(_qrEspera){clearTimeout(_qrEspera);_qrEspera=null;}
          if(btn&&btn.disabled){btn.textContent='📱 Gerar QR';btn.classList.remove('ghost');btn.disabled=false;}
          if(m&&!m.textContent){m.textContent='Não deu pra checar a sessão agora — pode gerar o QR.';m.style.color='var(--ambar)';}}
        // CÃO DE GUARDA do "Verificando…". A consulta pode não FALHAR nem RESPONDER:
        // o /iniciar de uma conta caída tenta religar antes de devolver, e o timeout
        // do servidor pro serviço de QR é 20s (finance/whatsapp_qr.py). Sem prazo aqui,
        // um botão desabilitado esperando essa resposta deixava a tela sem saída —
        // pior que o estado anterior, em que ele era sempre clicável.
        var _qrEspera=null;
        function qrEsperando(){if(_qrEspera)clearTimeout(_qrEspera);
          _qrEspera=setTimeout(qrIndefinido,7000);}

        // ═══ CHIP 2 ═══════════════════════════════════════════════════════════
        // Funções PRÓPRIAS, ids próprios, timer próprio. Reaproveitar as do chip 1
        // com um parâmetro pareceria mais limpo e seria pior: o chip 1 está no ar em
        // três contas, e qualquer engano no parâmetro faria o cartão de um mexer na
        // sessão do outro. Duplicar cem linhas custa menos que essa chance.
        var _c2Timer=null;
        function c2Chip(){var el=document.getElementById('c2-card');return el?el.dataset.chip:'';}
        // ═══ RELÓGIO DO QR — chip 2 ════════════════════════════════════════════
        // Cópia deliberada do chip 1, pelo mesmo motivo que o resto deste cartão é
        // copiado: um parâmetro compartilhado erraria de chip e mexeria na sessão
        // do outro. As constantes QR_1O/QR_SEG/QR_ATRASO são as mesmas, do chip 1.
        var _c2Src=null, _c2N=0, _c2Resta=0, _c2Tick=null, _c2Vao=false;
        function c2RelPinta(){
          var anel=document.getElementById('c2-anel'), seg=document.getElementById('c2-seg'),
              tit=document.getElementById('c2-rel-tit'), det=document.getElementById('c2-rel-det');
          if(!anel)return;
          var total=(_c2N<=1?QR_1O:QR_SEG), r=Math.max(0,_c2Resta);
          anel.style.setProperty('--zqr-p', total?r/total:0);
          anel.style.setProperty('--zqr-cor', r<=10?'var(--ambar)':'var(--verde)');
          if(seg)seg.textContent=r;
          if(!tit||!det)return;
          if(r<=0){tit.textContent='Buscando um código novo';det.textContent='Aguarde alguns segundos';}
          else if(r<=10){tit.textContent='Vai trocar de código';
            det.textContent='Um novo aparece em '+r+' s — pode esperar';}
          else if(_c2N<=1){tit.textContent='Escaneie agora';
            det.textContent='Este código vale por mais '+r+' segundos';}
          else{tit.textContent='Código renovado';
            det.textContent='O anterior venceu — aponte para este ('+r+' s)';}
        }
        function c2RelNovo(){
          _c2Resta=Math.max(1,(_c2N<=1?QR_1O:QR_SEG)-QR_ATRASO);
          if(_c2Tick)clearInterval(_c2Tick);
          _c2Tick=setInterval(function(){_c2Resta--;c2RelPinta();
            if(_c2Resta<=0){c2FimMostra(true);}},1000);
          c2FimMostra(false); c2RelPinta();
        }
        function c2FimMostra(v){var f=document.getElementById('c2-fim');if(f)f.style.display=v?'block':'none';}
        function c2RelPara(){
          if(_c2Tick){clearInterval(_c2Tick);_c2Tick=null;}
          _c2Src=null; _c2N=0; _c2Vao=false;
          var r=document.getElementById('c2-relogio'), p=document.getElementById('c2-passos');
          if(r)r.style.display='none'; if(p)p.style.display='none'; c2FimMostra(false);
        }
        // mesmo vão do chip 1: entre lotes o serviço reporta 'reconectando', e
        // desmontar a tela ali deixaria o cliente sem explicação bem no sumiço
        function c2VaoEntra(){
          if(_c2Tick){clearInterval(_c2Tick);_c2Tick=null;}
          _c2Src=null; _c2N=0; _c2Vao=true;
          var r=document.getElementById('c2-relogio'), p=document.getElementById('c2-passos');
          if(r)r.style.display='none'; if(p)p.style.display='none';
          c2FimMostra(true);
        }
        function c2Relogio(d){
          var rel=document.getElementById('c2-relogio'), pas=document.getElementById('c2-passos');
          if(!d){c2RelPara();return;}
          if(d.status==='reconectando'&&(_c2N>0||_c2Vao)){c2VaoEntra();return;}
          if(d.status!=='aguardando_qr'){c2RelPara();return;}
          if(d.qr){ if(d.qr!==_c2Src){_c2Vao=false;_c2Src=d.qr;_c2N++;
            if(rel)rel.style.display='flex'; if(pas)pas.style.display='block';
            c2RelNovo();} }
          else{ c2VaoEntra(); }
        }
        function c2Show(d){
          var box=document.getElementById('c2-box'),img=document.getElementById('c2-img'),
              msg=document.getElementById('c2-msg'),sair=document.getElementById('c2-sair'),
              btn=document.getElementById('c2-btn'),st=document.getElementById('c2-st');
          if(!d||!d.status){if(btn){btn.textContent='📱 Gerar QR';btn.disabled=false;}
            if(st){st.textContent='não deu pra checar';st.style.color='var(--ambar)';}return;}
          var conectado=d.status==='conectado';
          if(d.qr&&!conectado){img.src=d.qr;box.style.display='block';}else{box.style.display='none';}
          c2Relogio(d);   // mesmo relógio do chip 1, ids próprios
          if(msg)msg.textContent=d.msg||'';
          if(st){st.textContent=conectado?'✅ Conectado':(d.status==='aguardando_qr'?'Aguardando QR':d.status);
                 st.style.color=conectado?'var(--verde-claro)':'var(--ambar)';}
          // mesma regra do chip 1: quem manda é a CREDENCIAL, não o status. Uma conta
          // estacionada pelo disjuntor fica 'desconectado' e pareada, e é justo nela que
          // o botão precisa aparecer. `null` cai no comportamento antigo.
          var c2Pareada=(d.pareada===undefined||d.pareada===null)?(d.status!=='desconectado'):!!d.pareada;
          if(sair)sair.style.display=c2Pareada?'inline-flex':'none';
          // mesma correção que o chip 1 recebeu: conectado não é "Reconectar". O
          // /iniciar devolve a sessão viva sem tocar nela, então o botão prometia uma
          // ação que não acontece. Continua clicável — é a saída de quem está
          // "conectado" e mudo.
          if(btn){btn.textContent=conectado?'✓ Conectado':'📱 Gerar QR';
            btn.classList.toggle('ghost',conectado);
            btn.title=conectado?'A sessão está de pé. Clique só se desconfiar do status — verifica sem derrubar nada.':'';
            btn.disabled=false;}
          if(d.status==='desconectado'||conectado){if(_c2Timer){clearInterval(_c2Timer);_c2Timer=null;}}}
        function c2Poll(){fetch('/painel/prospeccao/comunicacao/whatsapp-qr-status?chip='+encodeURIComponent(c2Chip()))
          .then(function(r){return r.json();}).then(c2Show).catch(function(){c2Show(null);});}
        function c2Iniciar(){var btn=document.getElementById('c2-btn'),msg=document.getElementById('c2-msg');
          btn.disabled=true;btn.textContent='Gerando…';if(msg)msg.textContent='';
          c2RelPara();   // lote novo: o próximo QR volta a valer 60s, não 20s
          var fd=new FormData();fd.append('chip',c2Chip());
          fetch('/painel/prospeccao/comunicacao/whatsapp-qr-iniciar',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
            .then(function(r){return r.json();}).then(function(d){
              c2Show(d);
              if(!d.ok&&msg){msg.textContent=d.msg||d.erro||'Falha.';msg.style.color='var(--ambar)';return;}
              if(_c2Timer)clearInterval(_c2Timer);_c2Timer=setInterval(c2Poll,3000);})
            .catch(function(){btn.disabled=false;btn.textContent='📱 Gerar QR';
              if(msg){msg.textContent='Falha de rede.';msg.style.color='var(--ambar)';}});}
        // Mesmo aviso do chip 1, e pelo mesmo motivo: este botão APAGA a credencial.
        // A diferença é que aqui ele apaga a DESTE chip — o chip 1 não sente nada.
        function c2Sair(){if(!confirm('⚠️ Isto NÃO é só desconectar.\\n\\n'
          + 'Apaga a credencial e as chaves deste chip 2. Pra voltar, alguém vai '
          + 'precisar escanear um QR novo — e depois disso ele fica CONECTADO MAS SEM '
          + 'RECEBER por um bom tempo, enquanto as chaves se refazem.\\n\\n'
          + 'O chip 1 não é afetado.\\n\\n'
          + 'Se você só quer RECONECTAR, não use: o sistema reconecta sozinho.\\n\\n'
          + 'Apagar a sessão do chip 2 mesmo assim?'))return;
          // mesma segunda pergunta do chip 1 — ver o comentário lá: a primeira é
          // sobre consequência, esta é sobre prontidão, e o QR dura ~1min
          if(!confirm('📱 O celular do chip 2 está com você agora?\\n\\n'
            + 'Assim que desconectar, o QR aparece e o primeiro código dura cerca de '
            + '1 minuto. Se o celular não estiver por perto, este chip fica sem '
            + 'WhatsApp até alguém escanear.\\n\\n'
            + 'Deixe o WhatsApp já aberto em Aparelhos conectados › Conectar aparelho.\\n\\n'
            + 'Pode desconectar agora?'))return;
          var fd=new FormData();fd.append('chip',c2Chip());
          fetch('/painel/prospeccao/comunicacao/whatsapp-qr-sair',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
            .then(function(r){return r.json();}).then(function(d){
              var m=document.getElementById('c2-msg');
              if(d&&d.ok===false){if(m){m.textContent='Não deu pra desconectar ('+(d.erro||'falha')+').';m.style.color='var(--ambar)';}return;}
              if(_c2Timer){clearInterval(_c2Timer);_c2Timer=null;}
              c2Show({status:'desconectado',msg:'Desconectado.'});})
            .catch(function(){var m=document.getElementById('c2-msg');
              if(m){m.textContent='Falha de rede ao desconectar.';m.style.color='var(--ambar)';}});}
        function chipNovo(){
          var i=document.getElementById('c2-novo-nome'),m=document.getElementById('c2-novo-msg');
          var nome=(i&&i.value||'').trim();
          if(!nome){if(m){m.textContent='Dê um apelido pro chip.';m.style.color='var(--ambar)';}return;}
          var fd=new FormData();fd.append('apelido',nome);
          fetch('/painel/prospeccao/comunicacao/chip-novo',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
            .then(function(r){return r.json();}).then(function(d){
              if(!d||!d.ok){if(m){m.textContent=(d&&(d.msg||d.erro))||'Falha.';m.style.color='var(--ambar)';}return;}
              // recarrega pra tela nascer com o cartão do chip 2 — é uma vez só, e
              // evita duplicar em JS o bloco que o template já sabe desenhar
              location.reload();})
            .catch(function(){if(m){m.textContent='Falha de rede.';m.style.color='var(--ambar)';}});}
        function chipApelido(chip,campo){
          var i=document.getElementById(campo);if(!i)return;
          var fd=new FormData();fd.append('chip',chip||'');fd.append('apelido',i.value||'');
          var antes=i.style.borderColor;
          fetch('/painel/prospeccao/comunicacao/chip-apelido',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
            .then(function(r){return r.json();}).then(function(d){
              i.style.borderColor=(d&&d.ok)?'var(--verde)':'var(--ambar)';
              setTimeout(function(){i.style.borderColor=antes;},1400);})
            .catch(function(){i.style.borderColor='var(--ambar)';
              setTimeout(function(){i.style.borderColor=antes;},1400);});}
        // NÃO consulta no load: a aba do chip 2 nasce escondida, e perguntar o
        // estado de uma sessão que ninguém está olhando é tráfego à toa no serviço.
        // Quem dispara é o `waProv('qr2')`, quando a aba abre.
        // Aparelhos do chip 2 — mesma regra do chip 1: SOB DEMANDA, nunca em
        // intervalo. Perguntar é uma consulta ao WhatsApp, e repetir sozinho é
        // tráfego de robô num cliente não oficial.
        var _aps2Ocupado=false;
        function apsPuxa2(){
          if(_aps2Ocupado)return;
          _aps2Ocupado=true;
          var b=document.getElementById('c2-aps-btn');
          if(b){b.disabled=true;b.textContent='Perguntando…';}
          fetch('/painel/prospeccao/comunicacao/whatsapp-aparelhos?perguntar=1&chip='+encodeURIComponent(c2Chip()))
            .then(function(r){return r.json();}).then(function(j){apsPinta(j,'c2-aps-n','c2-aps-dica');})
            .catch(function(){})
            .then(function(){_aps2Ocupado=false;
              if(b){b.disabled=false;b.textContent='Conferir aparelhos ligados';}});
        }
        function qrPoll(){qrEsperando();
          fetch('/painel/prospeccao/comunicacao/whatsapp-qr-status').then(function(r){return r.json();})
            .then(qrShow).catch(qrIndefinido);}
        function qrIniciar(){var btn=document.getElementById('qr-btn'),msg=document.getElementById('qr-msg');
          btn.disabled=true;var t=btn.textContent;btn.textContent='Gerando…';if(msg)msg.textContent='';
          // pedido novo = lote novo de códigos: o próximo QR volta a ser o "primeiro"
          // (60s), senão ele herdaria os 20s do fim da tentativa anterior
          qrRelPara();
          fetch('/painel/prospeccao/comunicacao/whatsapp-qr-iniciar',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
            btn.disabled=false;btn.textContent=t;qrShow(d);
            if(!d.ok&&msg){msg.textContent=d.msg||d.erro||'Falha.';msg.style.color='var(--ambar)';return;}
            if(_qrTimer)clearInterval(_qrTimer);_qrTimer=setInterval(qrPoll,3000);
          }).catch(function(){btn.disabled=false;btn.textContent=t;if(msg){msg.textContent='Falha de rede.';msg.style.color='var(--ambar)';}});}
        // O texto é longo de propósito. "Desconectar o WhatsApp por QR desta empresa?"
        // fazia parecer um liga/desliga, e não é: o botão APAGA a credencial e o cofre
        // de chaves (wa_qr_auth inteira). Medido na conta 35 em 17/08 — um clique às
        // 15:07, QR novo às 15:13, e a conta passou as 3 horas seguintes CONECTADA e
        // sem receber uma mensagem, porque as chaves do Signal se refazem conversa por
        // conversa (8 sessões reconstruídas em 3h, contra 85 e 467 das contas sadias).
        // Quem clicou queria só reconectar — e reconectar o sistema já faz sozinho.
        function qrSair(){if(!confirm('⚠️ Isto NÃO é só desconectar.\\n\\n'
          + 'Este botão APAGA a credencial e as chaves de criptografia desta empresa. '
          + 'Para voltar, alguém vai precisar escanear um QR novo — e depois disso o '
          + 'WhatsApp fica CONECTADO MAS SEM RECEBER por um bom tempo, enquanto as '
          + 'chaves se refazem conversa por conversa. Já aconteceu de uma conta passar '
          + '3 horas assim depois de um clique aqui.\\n\\n'
          + 'Se você só quer RECONECTAR, não use este botão: o sistema reconecta sozinho.\\n\\n'
          + 'Use só para trocar o número desta empresa, ou se o suporte pediu.\\n\\n'
          + 'Apagar a sessão mesmo assim?'))return;
          // SEGUNDA PERGUNTA, curta e separada de propósito.
          //
          // A de cima é sobre CONSEQUÊNCIA (apaga credencial e chaves). Esta é sobre
          // PRONTIDÃO, que é outra decisão — e é a que determina se a empresa fica
          // fora do ar. Quem clica ali já aceitou o custo; o que ninguém pergunta é
          // se a pessoa do celular está por perto AGORA.
          //
          // O prazo não é chute: o primeiro QR do lote dura 60s (Baileys,
          // Socket/socket.js:464) e os seguintes 20s. Em 26/08 o dono da conta 23
          // pareou em 42s COM O CELULAR JÁ NA MÃO — quem tiver que ir buscar o
          // aparelho não passa, e a conta fica fora do ar até alguém escanear.
          //
          // Curta E depois da longa: no fim de um parágrafo grande, uma linha curta
          // ainda é lida. Enfiada no meio daquele texto, ela sumiria.
          if(!confirm('📱 O celular está com você agora?\\n\\n'
            + 'Assim que desconectar, o QR aparece e o primeiro código dura cerca de '
            + '1 minuto. Se o celular não estiver por perto, a empresa fica sem '
            + 'WhatsApp até alguém escanear.\\n\\n'
            + 'Deixe o WhatsApp já aberto em Aparelhos conectados › Conectar aparelho.\\n\\n'
            + 'Pode desconectar agora?'))return;
          fetch('/painel/prospeccao/comunicacao/whatsapp-qr-sair',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
            // só declara desconectado se REALMENTE desconectou — senão o usuário ia
            // escanear um QR novo achando que a sessão antiga tinha caído
            if(d&&d.ok===false){var m=document.getElementById('qr-msg');
              if(m){m.textContent='Não deu pra desconectar ('+(d.erro||'falha')+'). Tente de novo.';m.style.color='var(--ambar)';}
              return;}
            if(_qrTimer){clearInterval(_qrTimer);_qrTimer=null;}qrShow({status:'desconectado',msg:'Desconectado.'});})
            .catch(function(){var m=document.getElementById('qr-msg');
              if(m){m.textContent='Falha de rede ao desconectar. Tente de novo.';m.style.color='var(--ambar)';}});}
        // Apagar é irreversível, então a confirmação diz NÚMEROS REAIS (buscados
        // agora) em vez de um "tem certeza?" genérico. Quem lê "2.394 mensagens de
        // 14/07 a 17/08" decide de verdade; quem lê "tem certeza?" só clica em OK.
        function qrApagar(){
          var m=document.getElementById('qr-msg');
          fetch('/painel/prospeccao/comunicacao/historico-resumo').then(function(r){return r.json();}).then(function(d){
            if(!d||!d.ok){if(m){m.textContent='Não deu pra ler o histórico. Tente de novo.';m.style.color='var(--ambar)';}return;}
            if(!d.mensagens&&!d.conversas&&!d.contatos){
              if(m){m.textContent='Não há histórico de WhatsApp pra apagar.';m.style.color='';}return;}
            var per=[];
            if(d.conversas)per.push(d.conversas+(d.conversas===1?' conversa':' conversas'));
            if(d.mensagens)per.push(d.mensagens.toLocaleString('pt-BR')+(d.mensagens===1?' mensagem':' mensagens'));
            if(d.contatos)per.push(d.contatos+' contatos da agenda do celular');
            // BARRA-N DUPLA nas quebras de linha abaixo. Este template é uma string
            // Python comum (não raw), então uma barra-n simples viraria newline
            // LITERAL dentro da string JS de aspas simples — SyntaxError que mata o
            // bloco <script> INTEIRO, levando qrShow/qrPoll/qrIniciar/qrSair com
            // ele. Vale pros comentários também: um destes aqui já se partiu no
            // meio e o resto da frase virou código. Ver tests/test_painel_js_sintaxe.
            var txt='APAGAR O HISTÓRICO DE WHATSAPP\\n\\nVai apagar '+per.join(', ')
              +(d.de?('\\n\\nConversas de '+d.de+' a '+d.ate):'')
              +'\\n\\nIsso NÃO tem como desfazer.\\n\\nOs leads e os orçamentos continuam —'
              +' só somem as conversas. Os contatos da agenda voltam sozinhos no próximo'
              +' pareamento.\\n\\nApagar mesmo assim?';
            if(!confirm(txt))return;
            var b=document.getElementById('qr-apagar');if(b){b.disabled=true;b.textContent='Apagando…';}
            fetch('/painel/prospeccao/comunicacao/historico-apagar',{method:'POST',headers:{'X-Requested-With':'fetch'}})
              .then(function(r){return r.json();}).then(function(res){
                if(b){b.disabled=false;b.textContent='🗑️ Apagar histórico';}
                if(!res||!res.ok){if(m){m.textContent=res&&res.erro?res.erro:'Não deu pra apagar.';m.style.color='var(--ambar)';}return;}
                if(m){m.textContent='Histórico apagado: '+res.mensagens+' mensagens e '+res.conversas+' conversas.';
                      m.style.color='var(--verde-claro)';}
                if(b)b.style.display='none';
              }).catch(function(){if(b){b.disabled=false;b.textContent='🗑️ Apagar histórico';}
                if(m){m.textContent='Falha de rede ao apagar.';m.style.color='var(--ambar)';}});
          }).catch(function(){if(m){m.textContent='Falha de rede.';m.style.color='var(--ambar)';}});}
        // Ao abrir a página, tenta reconectar sozinho em vez de só checar o status —
        // o serviço Node reinicia a cada deploy (perde a sessão da memória, mas as
        // credenciais continuam salvas), e sem isso o usuário via "Desconectado" e
        // achava que precisava escanear um QR novo, quando na real bastava reconectar
        // com o que já tinha salvo. iniciarSessao só gera QR de verdade quando não
        // existe credencial válida — reconectar não tem custo/risco de rodar à toa.
        function qrAutoReconectar(){
          // com prazo: este POST é o que mais demora (religa antes de responder), e
          // era ele que deixava o "Verificando…" preso na abertura da tela.
          qrEsperando();
          fetch('/painel/prospeccao/comunicacao/whatsapp-qr-iniciar',{method:'POST',headers:{'X-Requested-With':'fetch'}})
            .then(function(r){return r.json();}).then(function(d){qrShow(d);})
            .catch(function(){qrPoll();})
            .then(function(){if(_qrTimer)clearInterval(_qrTimer);_qrTimer=setInterval(qrPoll,3000);});
        }
        {% if canais.wa_provedor=='qr' %}qrAutoReconectar();{% endif %}
        </script>
      </div>

      {# ═══ ABA DO CHIP 2 ═══════════════════════════════════════════════════
         Irmã da aba do chip 1, não apêndice dela: mesmo painel, mesma ordem,
         mesmos botões. Ids próprios (`c2-*`) e polling próprio — o cartão de um
         não alcança a sessão do outro nem por engano. Só existe pra empresa que
         criou o chip; nas 22 contas de hoje esta div nem é renderizada.        #}
      <div id="wa-qr2" class="waprov">
        {% set chip2 = (canais.chips[1] if canais.chips|length > 1 else None) %}
        {% if chip2 %}
        <div id="c2-card" data-chip="{{ chip2.id }}">
          <div style="font-weight:600;font-size:.85rem;margin-bottom:.15rem">QR Code · chip 2</div>
          <div style="display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;margin-bottom:.3rem">
            <span id="c2-num" style="font-family:ui-monospace,monospace;font-size:.82rem;color:{{ 'var(--txt)' if chip2.numero else 'var(--txt-mut)' }}">{{ chip2.numero or 'nenhum número pareado' }}</span>
            <span id="c2-st" style="font-size:.78rem;margin-left:auto;color:var(--ambar)">Verificando…</span>
          </div>
          <div class="mut" style="font-size:.8rem;margin-bottom:.35rem">📥 Última recebida neste chip:
            {% if chip2.ultima %}<b style="color:var(--verde-claro)">{{ chip2.ultima }}</b>{% if chip2.sem_receber %}
            <span style="color:var(--ambar)">({{ chip2.sem_receber }} — se o cliente está mandando mensagem, reconecte abaixo)</span>{% endif %}
            {% else %}<span style="color:var(--mut)">nenhuma ainda</span>{% endif %}</div>

          <div style="display:flex;gap:.4rem;margin-top:.5rem;flex-wrap:wrap">
            <button type="button" class="pbtn" id="c2-btn" onclick="c2Iniciar()" disabled>Verificando…</button>
            <button type="button" class="pbtn ghost" id="c2-sair" onclick="c2Sair()" style="display:none">Desconectar</button>
          </div>
          <div style="font-size:.73rem;color:var(--txt-mut);margin-top:.4rem">
            Caiu ou está reconectando? <b>Espere</b> — o sistema religa sozinho. O
            <b>Desconectar</b> apaga a sessão <b>deste chip</b> e exige QR novo; o chip 1
            não é afetado.
          </div>
          <div id="c2-box" style="margin-top:.6rem;text-align:center;display:none">
            <img id="c2-img" alt="QR do chip 2" style="width:220px;max-width:100%;border-radius:10px;background:#fff;padding:.4rem">
          </div>
          <!-- mesmo relógio do chip 1, ids próprios (as classes zqr-* são compartilhadas) -->
          <div id="c2-relogio" class="zqr-relogio" style="display:none">
            <div id="c2-anel" class="zqr-anel"><b id="c2-seg">60</b></div>
            <div class="zqr-txt">
              <b id="c2-rel-tit">Escaneie agora</b>
              <span id="c2-rel-det">Este código vale por mais 60 segundos</span>
            </div>
          </div>
          <ol id="c2-passos" class="zqr-passos" style="display:none">
            <li>No celular, abra o <b>WhatsApp</b></li>
            <li>Toque em <b>Aparelhos conectados</b></li>
            <li>Toque em <b>Conectar aparelho</b> e aponte para esta tela</li>
          </ol>
          <div id="c2-fim" class="zqr-fim" style="display:none">
            O tempo deste código acabou e estamos buscando um novo — <b>nada foi perdido</b>.
            Deixe o WhatsApp aberto em <b>Aparelhos conectados</b> que ele aparece em instantes.
          </div>
          <div class="mut" id="c2-msg" style="font-size:.78rem;margin-top:.45rem"></div>

          <div style="margin-top:.7rem;border-top:1px solid var(--borda);padding-top:.7rem">
            <label class="lbl" style="font-size:.72rem">Apelido deste chip</label>
            <div style="display:flex;gap:.4rem">
              <input class="fld" id="c2-apelido" maxlength="60" placeholder="ex.: Agência Beta" value="{{ chip2.apelido }}">
              <button type="button" class="pbtn ghost" style="white-space:nowrap"
                      onclick="chipApelido(document.getElementById('c2-card').dataset.chip,'c2-apelido')">Salvar</button>
            </div>
            <div class="mut" style="font-size:.72rem;margin-top:.3rem">
              É este nome que aparece no inbox e que o relatório agrupa.
            </div>
          </div>

          <div id="c2-aps" style="margin-top:.7rem;border-top:1px solid var(--borda);padding-top:.7rem">
            <div style="font-weight:600;font-size:.82rem;margin-bottom:.35rem">Quem ainda responde por fora <span class="mut" style="font-weight:400">(neste chip)</span></div>
            <div id="c2-aps-n" style="display:flex;gap:.4rem;flex-wrap:wrap;font-size:.78rem"></div>
            <button type="button" class="pbtn ghost sm" id="c2-aps-btn" onclick="apsPuxa2()"
                    style="margin-top:.5rem;font-size:.76rem">Conferir aparelhos ligados</button>
            <div class="mut" id="c2-aps-dica" style="font-size:.74rem;margin-top:.45rem"></div>
          </div>
        </div>
        {% else %}
        <div style="font-weight:600;font-size:.85rem;margin-bottom:.15rem">Segundo chip</div>
        <div class="mut" style="font-size:.78rem;margin-bottom:.5rem">
          Conecta outro aparelho nesta mesma empresa. Conexão independente —
          <b>não mexe na do chip 1</b> — e os leads caem no mesmo funil, com etiqueta
          no inbox.
        </div>
        <div style="display:flex;gap:.4rem;flex-wrap:wrap">
          <input class="fld" id="c2-novo-nome" maxlength="60" placeholder="Apelido (ex.: Agência Beta)">
          <button type="button" class="pbtn ghost" style="white-space:nowrap" onclick="chipNovo()">Criar chip 2</button>
        </div>
        <div class="mut" id="c2-novo-msg" style="font-size:.76rem;margin-top:.35rem"></div>
        {% endif %}
      </div>

      <!-- templates da agenda: só valem na API oficial (no QR não existe template) -->
      <div id="wa-tmpl-agenda" style="margin-top:.9rem;padding-top:.8rem;border-top:1px dashed var(--borda)">
        <div style="font-weight:600;font-size:.85rem">📅 Templates da Agenda <span class="mut" style="font-weight:400">(opcional)</span></div>
        <div class="mut" style="font-size:.76rem;margin-top:.15rem">Para o convite e o aviso da reunião saírem <b>sozinhos</b> fora da janela de 24h. Sem eles, o convite continua indo pelo link manual.</div>
        <form method="post" action="/painel/prospeccao/comunicacao/canal-templates" style="margin-top:.5rem">
          <label class="lbl">Convite de reunião</label>
          <input class="fld" name="tmpl_convite_sid" value="{{ canais.tmpl_convite }}" placeholder="HX… ou nome_do_template" spellcheck="false" style="font-family:ui-monospace,monospace">
          <div class="mut" style="font-size:.74rem;margin-top:.2rem">{% if canais.tmpl_convite %}<span style="color:var(--verde-claro)">● pronto pra disparo</span>{% else %}<span style="color:#e0a33e">● defina o SID pra ligar o disparo automático</span>{% endif %}</div>
          <label class="lbl" style="margin-top:.5rem">Aviso antes da reunião</label>
          <input class="fld" name="tmpl_lembrete_sid" value="{{ canais.tmpl_lembrete }}" placeholder="HX… ou nome_do_template" spellcheck="false" style="font-family:ui-monospace,monospace">
          <div class="mut" style="font-size:.74rem;margin-top:.2rem">{% if canais.tmpl_lembrete %}<span style="color:var(--verde-claro)">● pronto pra disparo</span>{% else %}<span style="color:#e0a33e">● sem ele, só avisa quem respondeu no WhatsApp nas últimas 24h</span>{% endif %}</div>
          <div class="mut" style="font-size:.72rem;margin-top:.45rem">Gere com <code>scripts/criar_template_lembrete.py</code> e cole o código aqui. Deixe vazio pra limpar.</div>
          <button class="pbtn" type="submit" style="margin-top:.5rem">Salvar templates</button>
        </form>
      </div>
      <script>
      function waProv(v){['twilio','cloud','qr','qr2'].forEach(function(k){var e=document.getElementById('wa-'+k);if(e)e.style.display=(k===v)?'block':'none';});
        document.querySelectorAll('.waseg label').forEach(function(l){var r=l.querySelector('input');l.classList.toggle('on',!!r&&r.value===v);});
        // no QR não existe template aprovado — esconde pra não prometer o que não funciona
        var t=document.getElementById('wa-tmpl-agenda');if(t)t.style.display=(v==='qr'||v==='qr2')?'none':'block';
        // a aba do chip 2 pergunta o estado DELE ao abrir, pelo mesmo motivo do chip 1:
        // o HTML estático tem a cara de sessão desconectada
        if(v==='qr2'&&document.getElementById('c2-card'))c2Poll();
        // MOSTROU O BLOCO, PERGUNTA O ESTADO. Sem isto o bloco aparecia com o HTML
        // estático — "Gerar QR" visível e "Desconectar" escondido —, que é a cara de
        // sessão desconectada, mesmo com a sessão de pé. O polling só era disparado
        // quando o provedor SALVO era 'qr', então bastava clicar no rádio "QR Code"
        // (ou o auto-reconectar do load falhar) pra tela mentir sobre a conexão.
        //
        // `qrPoll` e não `qrAutoReconectar`: o /whatsapp-qr-iniciar faz
        // `on conflict do update set provedor='qr', ativo=true` — clicar num rádio
        // pra CONFERIR não pode migrar a empresa pro QR por efeito colateral. Ver o
        // estado é leitura; mudar de provedor é o botão Salvar.
        if(v==='qr'&&typeof qrPoll==='function')qrPoll();}
      waProv('{{ canais.wa_provedor if canais.wa_provedor in ['twilio','cloud','qr'] else 'twilio' }}');
      </script>
      {% else %}
      <div class="mut" style="margin-top:.4rem">Provedor: <b>{{ 'Cloud API (número próprio)' if canais.wa_provedor=='cloud' else 'Twilio' }}</b> · Número: <b>{{ canais.numeros.get('whatsapp','—') }}</b></div>
      {% endif %}
    </div>
    <div class="cx-card">
      <h3>🔵 Messenger <span class="cx-stat {{ 'st-on' if canais.messenger else 'st-off' }}">● {{ 'Conectado' if canais.messenger else ('⚠ Token inválido — reconecte' if canais.msg_tok_ruim else ('Falta Página/token' if canais.meta else 'Falta app (env)')) }}</span></h3>
      <div class="mut" style="font-size:.8rem">Via Meta direto. App da Meta (env global) + Página do Facebook. 📥 responde na janela de 24h.</div>
      <div class="mut" style="font-size:.8rem;margin-top:.25rem">📥 Última recebida: {% if canais.ult_in.get('messenger') %}<b style="color:var(--verde-claro)">{{ canais.ult_in['messenger'] }}</b>{% else %}<span style="color:var(--mut)">nenhuma ainda</span>{% endif %}</div>
      <div class="cx-env"><b>META_APP_SECRET</b>=•••••<br><b>META_VERIFY_TOKEN</b>=•••••</div>
      {% if gerencia %}
      <form method="post" action="/painel/prospeccao/comunicacao/canal-numero" style="margin-top:.6rem">
        <input type="hidden" name="canal" value="messenger">
        <label class="lbl">Page ID (Facebook) + Page Access Token</label>
        <input class="fld" name="numero" placeholder="Page ID" value="{{ canais.numeros.get('messenger','') }}" style="margin-bottom:.35rem">
        <div style="display:flex;gap:.3rem;align-items:center;margin-bottom:.4rem">
          <input class="fld" name="token" type="password" placeholder="Page Access Token {% if canais.tokens_set.get('messenger') %}(salvo — vazio mantém){% endif %}" autocomplete="new-password" style="margin:0">
          <button type="button" class="olho" title="ver o que está salvo" onclick="olhoSegredo(this,'messenger')">👁</button>
        </div>
        <div class="olho-msg mut" style="font-size:.74rem;margin-bottom:.4rem"></div>
        <button class="pbtn">Salvar</button>
      </form>
      <button type="button" class="pbtn ghost" style="margin-top:.5rem;font-size:.82rem" onclick="detectarFB(this)" title="O servidor descobre a Página do Facebook (id + token da Página) a partir do token salvo e inscreve ela no webhook 'messages'">🔎 Detectar página e ativar recebimento</button>
      <div class="mut" id="detfb-msg" style="font-size:.8rem;margin-top:.35rem"></div>
      <script>
      function detectarFB(b){var m=document.getElementById('detfb-msg');b.disabled=true;var t=b.textContent;b.textContent='Detectando…';m.textContent='';m.style.color='';
        fetch('/painel/prospeccao/comunicacao/detectar-fb',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){b.disabled=false;b.textContent=t;
          if(!d.ok){m.style.color='var(--ambar)';m.textContent='⚠ '+(d.erro||'Não consegui.');return;}
          if(d.assinado){m.style.color='var(--verde-claro)';m.textContent='✓ Página "'+(d.nome||'?')+'" (ID '+d.page_id+') detectada e INSCRITA no webhook.'+(d.varias?' (usei a 1ª — você tem mais de uma Página)':'')+' Recarregando…';}
          else{m.style.color='var(--ambar)';m.textContent='Página "'+(d.nome||'?')+'" (ID '+d.page_id+') salva, mas a inscrição falhou: '+(d.assinar_erro||'?');}
          setTimeout(function(){location.reload();},2400);}).catch(function(){b.disabled=false;b.textContent=t;m.style.color='var(--ambar)';m.textContent='Falha de rede.';});}
      </script>{% endif %}
      <div class="mut" style="margin-top:.4rem;font-size:.8rem">No app da Meta, aponte o webhook pra <code>/webhooks/meta</code> (verify token = META_VERIFY_TOKEN) e assine <code>messages</code>.</div>
    </div>
    <div class="cx-card">
      <h3>📸 Instagram <span class="cx-stat {{ 'st-on' if canais.instagram else 'st-off' }}">● {{ 'Conectado' if canais.instagram else ('⚠ Token inválido — reconecte' if canais.ig_tok_ruim else ('Falta conta/token' if canais.meta else 'Falta app (env)')) }}</span></h3>
      <div class="mut" style="font-size:.8rem">Via Meta direto (mesmo webhook). Conta IG Profissional ligada à Página. 📥 responde na janela de 24h.</div>
      <div class="mut" style="font-size:.8rem;margin-top:.25rem">📥 Última recebida: {% if canais.ult_in.get('instagram') %}<b style="color:var(--verde-claro)">{{ canais.ult_in['instagram'] }}</b>{% else %}<span style="color:var(--mut)">nenhuma ainda</span>{% endif %}</div>
      {% if gerencia %}
      <form method="post" action="/painel/prospeccao/comunicacao/canal-numero" style="margin-top:.6rem">
        <input type="hidden" name="canal" value="instagram">
        <label class="lbl">IG Account ID + Page Access Token</label>
        <input class="fld" name="numero" placeholder="IG Account ID" value="{{ canais.numeros.get('instagram','') }}" style="margin-bottom:.35rem">
        <div style="display:flex;gap:.3rem;align-items:center;margin-bottom:.4rem">
          <input class="fld" name="token" type="password" placeholder="Page Access Token {% if canais.tokens_set.get('instagram') %}(salvo — vazio mantém){% endif %}" autocomplete="new-password" style="margin:0">
          <button type="button" class="olho" title="ver o que está salvo" onclick="olhoSegredo(this,'instagram')">👁</button>
        </div>
        <div class="olho-msg mut" style="font-size:.74rem;margin-bottom:.4rem"></div>
        <button class="pbtn">Salvar</button>
      </form>
      <button type="button" class="pbtn ghost" style="margin-top:.5rem;font-size:.82rem" onclick="detectarIG(this)" title="O servidor descobre o ID da conta e inscreve ela no webhook 'messages' (subscribed_apps) — necessário pra DM real chegar">🔎 Detectar conta e ativar recebimento</button>
      <div class="mut" id="detig-msg" style="font-size:.8rem;margin-top:.35rem"></div>
      <script>
      function detectarIG(b){var m=document.getElementById('detig-msg');b.disabled=true;var t=b.textContent;b.textContent='Detectando…';m.textContent='';m.style.color='';
        fetch('/painel/prospeccao/comunicacao/detectar-ig',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){b.disabled=false;b.textContent=t;
          if(!d.ok){m.style.color='var(--ambar)';m.textContent='⚠ '+(d.erro||'Não consegui.');return;}
          if(d.assinado){m.style.color='var(--verde-claro)';m.textContent='✓ Conta @'+(d.username||'?')+' (ID '+d.user_id+') detectada e INSCRITA no webhook'+(d.token_longo?' · token de 60 dias ✓':'')+'. Recarregando…';}
          else{m.style.color='var(--ambar)';m.textContent='Conta @'+(d.username||'?')+' salva, mas a inscrição falhou: '+(d.assinar_erro||'?')+'. (token pode ter expirado)';}
          setTimeout(function(){location.reload();},2200);}).catch(function(){b.disabled=false;b.textContent=t;m.style.color='var(--ambar)';m.textContent='Falha de rede.';});}
      </script>{% endif %}
      <div class="mut" style="margin-top:.4rem;font-size:.8rem">Webhook: <code>/webhooks/meta</code> · assine <code>messages</code> no produto Instagram do app.</div>
    </div>
  </div>
  {% endif %}
</div>
<script>
var _cxConv=null,_cxSig='',_cxAg=null,_cxPr=null,_cxTimer=null,_cxSeen={},_cxList={},_cxListHtml=null;
// Mensagens ESCRITAS mas ainda não confirmadas pelo servidor. Ficam à parte das
// mensagens reais porque o thread é redesenhado do zero a cada poll (4s) — sem
// esta lista, o balão recém-escrito sumia no primeiro redesenho e só voltava
// quando o envio terminasse, que é justamente o que fazia o chat "travar".
var _cxPend=[],_cxPendSeq=0;
var _cxEscopo='{{ escopo }}';   // 'email' (aba E-mails) ou 'msg' (aba Conversas)
// quem pode trocar o responsável direto na lista, e por quem trocar. Mesma regra da
// ficha do lead — a lista não pode ser mais permissiva que ela.
var _cxPodeAtrib={{ 'true' if pode_atribuir else 'false' }};
var _cxVends=[{% for v in vendedores %}{id:{{ v.id }},nome:{{ v.nome|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}];
function cxEsc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
// Balões do que ainda está saindo. Redesenhados junto com o thread, então
// sobrevivem ao poll — e o usuário nunca vê a própria mensagem piscar.
function cxPendHtml(){
  var h='';
  _cxPend.forEach(function(p){
    // pendente de OUTRA conversa não aparece aqui (continua na fila, e volta a
    // aparecer se o usuário reabrir aquela conversa)
    if(p.conv!==_cxConv)return;
    var meta=p.erro
      ? '<span style="color:var(--coral)">✕ não saiu</span> · <a href="#" data-pid="'+p.id+'" onclick="cxReenviar(this.dataset.pid);return false;">tentar de novo</a>'
      : 'enviando…';
    h+='<div class="cx-m cx-pend" id="'+p.id+'" style="opacity:'+(p.erro?'1':'.55')+'">'
      +cxEsc(p.texto).replace(/\\n/g,'<br>')+'<span class="meta">'+meta+'</span></div>';
  });
  return h;
}
function cxMsgsHtml(d){
  if(!d.msgs.length)return '<div class="cx-empty">Sem mensagens.</div>'+cxPendHtml();
  // classe PRÓPRIA, nunca cx-empty: cxResponder limpa o thread inteiro quando
  // acha um .cx-empty (o placeholder "Sem mensagens"), então usar essa classe
  // aqui fazia o thread sumir e "voltar com o histórico" a cada envio.
  var h=d.truncado?'<div class="cx-trunc">Mostrando as últimas 100 mensagens</div>':'';
  d.msgs.forEach(function(m){
    var cls=(m.direcao==='in')?'cx-m cin':((m.autor==='bot')?'cx-m cbot':'cx-m');
    var cab=m.cabecalho?('<div class="cab">'+cxEsc(m.cabecalho)+'</div>'):'';
    var corpo=cxEsc(m.corpo||m.cabecalho).replace(/\\n/g,'<br>');
    h+='<div class="'+cls+'">'+cab+corpo+'<span class="meta">'+cxEsc(m.quem)+' · '+cxEsc(m.quando)+cxTick(m)+'</span></div>';
  });
  return h+cxPendHtml();
}
// selo de entrega só nas mensagens que SAÍRAM (WhatsApp): ✓ enviado · ✓✓ entregue · 👀 lido · ⚠ erro
function cxTick(m){
  if(m.direcao!=='out'||m.canal!=='whatsapp')return '';
  var s={enviado:' · ✓',entregue:' · ✓✓',lido:' · <span style="color:#4aa3ff">👀 lido</span>',erro:' · <span style="color:var(--coral)">⚠ falhou</span>'}[m.status];
  if(!s)return '';
  // "falhou" sozinho não diz o que fazer. O motivo vem do provedor e agora é
  // guardado junto da mensagem — quase sempre é a janela de 24h, e saber disso
  // é a diferença entre reenviar em vão e pedir pro cliente responder.
  if(m.status==='erro'&&m.erro)s+=' <span style="color:var(--txt-mut)">— '+cxEsc(m.erro)+'</span>';
  return s;
}
// JSON.stringify em vez de join('|'): o corpo da última mensagem entra na
// assinatura, e um '|' digitado pelo cliente quebrava o split lá no poll —
// o painel entrava em loop de recarga total (com "Carregando…" e o scroll
// pulando) a cada 4 segundos.
function cxSig(d){var u=d.msgs.length?d.msgs[d.msgs.length-1]:null;
  return JSON.stringify([d.msgs.length,u&&u.corpo,u&&u.status,d.agente_ativo?1:0,d.pode_responder?1:0]);}
function cxScroll(force){var b=document.getElementById('cx-msgs');if(!b)return;
  if(force||b.scrollHeight-b.scrollTop-b.clientHeight<80)b.scrollTop=b.scrollHeight;}
function cxOpen(el,id){
  _cxConv=id;if(el&&_cxList[id])_cxSeen[id]=_cxList[id].ult_msg_id||0;
  var g=document.getElementById('cx-grid');if(g)g.classList.add('open');
  var th=document.getElementById('cx-thread'),cx=document.getElementById('cx-ctx');
  document.querySelectorAll('.cx-conv').forEach(function(x){x.classList.remove('on');});
  var lb=document.getElementById('cxc-'+id);if(lb){lb.classList.add('on');var dt=lb.querySelector('.cx-undot');if(dt)dt.remove();}
  th.innerHTML='<div class="cx-empty">Carregando…</div>';
  fetch('/painel/prospeccao/comunicacao/thread/'+id).then(function(r){return r.json();}).then(function(d){
    if(_cxConv!==id)return;
    if(!d.ok){th.innerHTML='<div class="cx-empty">Não consegui abrir.</div>';return;}
    var L=d.lead;_cxSig=cxSig(d);_cxAg=d.agente_ativo?1:0;_cxPr=d.pode_responder?1:0;
    var rodape=d.pode_responder
      ?'<div class="cx-comp"><textarea id="cx-reply" rows="2" placeholder="Escreva uma resposta…" onkeydown="if(event.key===\\'Enter\\'&&!event.shiftKey){event.preventDefault();cxResponder('+d.conversa_id+');}"></textarea><button class="pbtn" id="cx-send" onclick="cxResponder('+d.conversa_id+')">Enviar</button></div>'
      :'<div class="cx-stub">Responder por aqui<span class="lbl2">em breve</span> — disponível quando o canal estiver conectado (aba <b>Canais</b>).</div>';
    var agBtn=(d.agente_ativo
      ?'<button class="pbtn ghost" style="padding:.35rem .7rem;font-size:.78rem;border-color:#4a3163;color:#c9a3e0" onclick="cxAgente('+d.conversa_id+',0)" title="Assumir você (desliga o agente)">🙋 Assumir</button>'
      :'<button class="pbtn ghost" style="padding:.35rem .7rem;font-size:.78rem" onclick="cxAgente('+d.conversa_id+',1)" title="Devolver ao agente">🤖 Ativar agente</button>');
    th.innerHTML=''
      // o dono do lead no cabeçalho: quem atende pelo inbox precisa saber de quem é
      // a conversa sem abrir o painel do lado nem a ficha
      +'<div class="cx-th"><div><b>'+cxEsc(L.empresa)+'</b><small>'+cxEsc(L.canal_rot||'')+(L.cidade?(' · '+cxEsc(L.cidade)+(L.uf?'/'+cxEsc(L.uf):'')):'')+(L.status_rot?(' · '+cxEsc(L.status_rot)):'')+(L.id?(L.vendedor?(' · 👤 '+cxEsc(L.vendedor)):' · <span style=\\'color:#e0b45f\\'>👤 sem responsável</span>'):'')+(d.agente_ativo?' · <span style=\\'color:#c9a3e0\\'>🤖 no automático</span>':'')+'</small></div>'
      +'<span style="flex:1"></span>'+agBtn+(L.id?(' <a class="pbtn ghost" style="padding:.35rem .7rem;font-size:.78rem" href="/painel/prospeccao/'+L.id+'">Abrir ficha</a>'):(' <button class="pbtn" style="padding:.35rem .7rem;font-size:.78rem" onclick="cxVirarLead('+d.conversa_id+')" title="Criar um lead a partir deste contato">➕ Levar para o lead</button>'))+'</div>'
      // mesmo número, outro chip: as duas conversas são separadas de propósito (cada
      // chip responde pelo seu número). O aviso é pra quem digita aqui não repetir —
      // ou contradizer — o que a outra campanha já combinou com a mesma pessoa.
      +(d.gemeo?('<div class="cx-gemeo">⚠️ '+cxEsc(d.gemeo.texto)+(d.gemeo.conversa_id?(' <a href="#" onclick="cxOpen(null,'+d.gemeo.conversa_id+');return false">Ver conversa →</a>'):'')+'</div>'):'')
      +'<div class="cx-msgs" id="cx-msgs">'+cxMsgsHtml(d)+'</div>'+rodape;
    cxScroll(true);
    var kv=function(k,v){return v?('<div class="cx-kv"><span>'+k+'</span><b>'+cxEsc(v)+'</b></div>'):'';};
    // Responsável: quem pode atribuir troca aqui mesmo; quem não pode continua só
    // lendo. Sem dono, mostra "— sem responsável —" em vez de sumir a linha (era o
    // kv() acima: valor vazio não renderizava, e o lead órfão parecia não ter campo).
    var resp;
    if(d.pode_atribuir){
      var ops='<option value="">— sem responsável —</option>';
      (d.vendedores||[]).forEach(function(v){
        ops+='<option value="'+v.id+'"'+(v.id===L.vendedor_id?' selected':'')+'>'+cxEsc(v.nome)+'</option>';});
      resp='<div class="cx-kv" style="align-items:center"><span>Responsável</span>'
          +'<select class="fld" id="cx-resp" style="width:auto;padding:.25rem .4rem;font-size:.8rem"'
          +' data-antes="'+(L.vendedor_id||'')+'"'
          +' onchange="cxAtribuir('+L.id+',this)">'+ops+'</select></div>';
    }else{
      // "— sem responsável —" só faz sentido onde EXISTE lead pra ter responsável.
      // Sem o L.id, a linha convivia na mesma tela com "este contato ainda não é um
      // lead" logo abaixo — e parecia um campo que devia ser editável e não era.
      resp=L.id?kv('Responsável',L.vendedor||'— sem responsável —'):'';
    }
    cx.innerHTML=''
      +'<div class="cx-sec"><h4>Lead</h4>'+kv('Empresa',L.empresa)+kv('Segmento',L.segmento)+kv('Cidade',(L.cidade||'')+(L.uf?'/'+L.uf:''))+kv('WhatsApp',L.whatsapp)+kv('E-mail',L.email)+resp+kv('Status',L.status_rot)+'</div>'
      +(L.id?('<div class="cx-sec"><a class="pbtn" style="width:100%;text-align:center" href="/painel/prospeccao/'+L.id+'">Abrir ficha do lead</a></div>'):('<div class="cx-sec"><button class="pbtn" style="width:100%" onclick="cxVirarLead('+d.conversa_id+')">➕ Levar para o lead</button><div class="mut" style="font-size:.74rem;margin-top:.4rem">Este contato ainda não é um lead. Crie o lead quando fizer sentido.</div></div>'));
  }).catch(function(){th.innerHTML='<div class="cx-empty">Falha de rede.</div>';});
}
function cxPollThread(){
  if(!_cxConv)return;var id=_cxConv;
  fetch('/painel/prospeccao/comunicacao/thread/'+id).then(function(r){return r.json();}).then(function(d){
    if(!d.ok||_cxConv!==id)return;
    // recarga TOTAL do painel só quando muda algo estrutural (agente ligado/
    // desligado ou o canal parou/voltou a poder responder) — nunca por causa do
    // conteúdo das mensagens; isso era o que fazia o chat "pular" o tempo todo.
    if((d.agente_ativo?1:0)!==_cxAg||(d.pode_responder?1:0)!==_cxPr){cxOpen(document.getElementById('cxc-'+id),id);return;}
    var sig=cxSig(d);if(sig===_cxSig)return;_cxSig=sig;
    var b=document.getElementById('cx-msgs');
    if(b){
      // se o usuário está acompanhando o rodapé, seguimos a conversa; se ele subiu
      // pra ler o histórico, preservamos a posição (innerHTML zera o scroll senão).
      var perto=(b.scrollHeight-b.scrollTop-b.clientHeight)<120;var st=b.scrollTop;
      b.innerHTML=cxMsgsHtml(d);
      b.scrollTop=perto?b.scrollHeight:st;
    }
  }).catch(function(){});
}
function cxParams(){var q=new URLSearchParams(location.search);return 'canal='+(q.get('canal')||'')+'&vendedor='+(q.get('vendedor')||'')+'&escopo='+encodeURIComponent(_cxEscopo||'msg')+'&q='+encodeURIComponent(cxTermo());}
// ------------------------------------------------------------------ a busca
//
// O termo mora no CAMPO, não na URL: a busca não recarrega a página, então guardar
// na URL obrigaria a reescrever o histórico do navegador a cada tecla. Quem lê o
// termo é o cxParams, que já é chamado pelo polling de 4 em 4 segundos — é isso que
// mantém o resultado da busca VIVO enquanto a pessoa lê.
var _cxBuscaT=null;
function cxTermo(){var el=document.getElementById('cx-busca');return el?(el.value||'').trim():'';}
// 300ms depois da ÚLTIMA tecla. Uma consulta por tecla seriam sete pra escrever
// "jacque", e a lista piscaria no meio da palavra.
function cxBuscaDigitou(){
  var x=document.getElementById('cx-busca-x');if(x)x.style.display=cxTermo()?'':'none';
  clearTimeout(_cxBuscaT);_cxBuscaT=setTimeout(cxBuscaJa,300);
}
function cxBuscaJa(){clearTimeout(_cxBuscaT);_cxListHtml='';cxPollList();}
// O X nasce visível quando a página veio com termo (recarregou com ?q=)
document.addEventListener('DOMContentLoaded',function(){if(cxTermo())cxBuscaDigitou();});
function cxBuscaLimpar(){
  var el=document.getElementById('cx-busca');if(el){el.value='';el.focus();}
  cxBuscaDigitou();cxBuscaJa();
}
// Realça o pedaço que casou. Sem isso, numa lista de nomes parecidos ninguém sabe
// por que aquela linha apareceu. Compara sem acento e sem caixa — do mesmo jeito que
// o servidor comparou (ver _dobrar) —, mas recorta e devolve o texto ORIGINAL: o
// nome tem que continuar aparecendo como a pessoa escreveu.
var _CX_ACC='áàâãäéèêëíìîïóòôõöúùûüçñ',_CX_SEM='aaaaaeeeeiiiiooooouuuucn';
function cxDobra(t){t=(t||'').toLowerCase();var o='';
  for(var i=0;i<t.length;i++){var j=_CX_ACC.indexOf(t[i]);o+=(j<0?t[i]:_CX_SEM[j]);}
  return o;}
function cxRealce(txt,termo){
  txt=txt||'';if(!termo)return cxEsc(txt);
  var i=cxDobra(txt).indexOf(cxDobra(termo));
  if(i<0)return cxEsc(txt);
  return cxEsc(txt.substring(0,i))+'<mark>'+cxEsc(txt.substring(i,i+termo.length))
    +'</mark>'+cxEsc(txt.substring(i+termo.length));
}
// O número só vira etiqueta quando a busca FOI por número — fora disso seria a
// mesma informação repetida em cem linhas. A regra dos 4 dígitos é a mesma do
// servidor (_MIN_DIGITOS): abaixo disso "ana 2" continua sendo busca de nome.
function cxNumEtiqueta(c,termo){
  var d=(termo||'').replace(/[^0-9]/g,'');
  if(d.length<4||!c.numero)return '';
  return '<span class="cx-num">'+cxRealce(c.numero,d)+'</span>';
}
function cxListItem(c){
  var cnc={whatsapp:'cn-wpp',email:'cn-mail',messenger:'cn-msg',instagram:'cn-ig'}[c.canal]||'cn-mail';
  var unread=(c.id!==_cxConv)&&(c.ult_autor==='lead'||c.ult_autor==='bot')&&((c.ult_msg_id||0)>(_cxSeen[c.id]||0));
  var av=(c.empresa||'?').substring(0,2).toUpperCase();
  var termo=cxTermo();
  return '<button type="button" class="cx-conv'+(c.id===_cxConv?' on':'')+'" id="cxc-'+c.id+'" onclick="cxOpen(this,'+c.id+')">'
    +'<span class="av">'+cxEsc(av)+'</span><span class="mid">'
    +'<span class="nm"><b>'+cxRealce(c.empresa,termo)+'</b><span class="t">'+cxEsc(c.quando||'')+(unread?' <span class="cx-undot"></span>':'')+'</span></span>'
    +'<span class="pre">'+cxEsc(c.quem)+': '+cxEsc(c.preview)+'</span>'
    +cxDonoLinha(c)
    +'<span class="cx-cn '+cnc+'">'+cxEsc(c.canal_rot)+(c.n>1?(' · '+c.n):'')+'</span>'
    +(c.chip_rot?('<span class="cx-chip '+(c.chip_id?'dois':'um')+'">'+cxEsc(c.chip_rot)+'</span>'):'')
    +cxNumEtiqueta(c,termo)
    +'</span></button>';
}
// O dono do lead, em linha própria. Só pra conversa que JÁ é lead: a maior parte da
// caixa é contato que ainda não virou lead, e marcar todas de "sem responsável" seria
// alarme falso na maioria das linhas.
function cxDonoLinha(c){
  if(!c.eh_lead)return '';
  var txt=c.dono?('👤 '+cxEsc(c.dono)):'👤 sem responsável';
  var cls='cx-dono'+(c.dono?'':' vazio')+(_cxPodeAtrib?' clicavel':'');
  var acao=_cxPodeAtrib
    ?' onclick="cxDonoMenu(event,'+c.lead_id+','+(c.dono_id||0)+',this)" title="Trocar o responsável"'
    :'';
  // sem seta: um ▾ de .6rem vira um pontinho colado no texto na coluna estreita. Quem
  // pode trocar vê o chip com borda tracejada (cara de campo editável), que funciona
  // parado — e portanto no celular, onde não existe hover.
  return '<span class="'+cls+'"'+acao+'>'+txt+'</span>';
}
// Popover de trocar o responsável. Um <select> por linha seria mais simples, mas com
// 100 conversas na tela são 100 selects — e o clique deles roubaria o de abrir a
// conversa. Aqui só existe um menu por vez, criado no clique.
function cxDonoFechar(){var m=document.getElementById('cx-dmenu');if(m)m.parentNode.removeChild(m);
  document.removeEventListener('click',cxDonoFora,true);}
function cxDonoFora(e){var m=document.getElementById('cx-dmenu');if(m&&!m.contains(e.target))cxDonoFechar();}
function cxDonoMenu(ev,leadId,donoId,el){
  ev.stopPropagation();ev.preventDefault();   // não abrir a conversa junto
  cxDonoFechar();
  var r=el.getBoundingClientRect();
  var m=document.createElement('div');
  m.id='cx-dmenu';m.className='cx-dmenu';
  m.style.left=Math.round(r.left)+'px';
  m.style.top=Math.round(r.bottom+6)+'px';
  var h='<div class="cab">Responsável</div>';
  _cxVends.forEach(function(v){
    var atual=(v.id===donoId);
    h+='<div class="op'+(atual?' atual':'')+'" onclick="cxDonoEscolher('+leadId+','+v.id+')">'
      +cxEsc(v.nome)+(atual?' ✓':'')+'</div>';});
  h+='<div class="op sem'+(donoId?'':' atual')+'" onclick="cxDonoEscolher('+leadId+',0)">'
    +'— sem responsável —'+(donoId?'':' ✓')+'</div>';
  m.innerHTML=h;
  document.body.appendChild(m);
  setTimeout(function(){document.addEventListener('click',cxDonoFora,true);},0);
}
function cxDonoEscolher(leadId,vendId){
  cxDonoFechar();
  var fd=new FormData();fd.append('vendedor_id',vendId?String(vendId):'');
  fetch('/painel/prospeccao/'+leadId+'/atribuir',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert(d.erro||'Não consegui trocar o responsável.');return;}
      cxPollList();                       // a lista se redesenha com o dono novo
      if(_cxConv)cxOpen(document.getElementById('cxc-'+_cxConv),_cxConv);
    }).catch(function(){alert('Falha de rede.');});
}
// A faixa dos leads sem dono. Dois estados: fora do filtro ela CONTA e leva pra lá;
// dentro do filtro ela AGE. Some quando não há órfão — nada de faixa permanente
// dizendo "0", que vira ruído que ninguém mais lê.
var _cxSemDonoN=-1;
function cxSemDono(n){
  var box=document.getElementById('cx-sem-dono');if(!box)return;
  var filtrado=(new URLSearchParams(location.search)).get('vendedor')==='sem';
  if(n===_cxSemDonoN&&box.getAttribute('data-f')===String(filtrado))return;  // não repinta à toa
  _cxSemDonoN=n;box.setAttribute('data-f',String(filtrado));
  if(!n||!_cxPodeAtrib){box.innerHTML='';return;}
  var plural=n>1?'s':'';
  if(!filtrado){
    box.innerHTML='<div class="cx-orf"><span class="pt"></span>'
      +'<b>'+n+' lead'+plural+' sem responsável</b>'
      +'<a class="lk" href="'+cxUrlSemDono()+'">ver só eles →</a></div>';
    return;
  }
  var ops=_cxVends.map(function(v){
    return '<option value="'+v.id+'">'+cxEsc(v.nome)+'</option>';}).join('');
  box.innerHTML='<div class="cx-orf"><span class="pt"></span>'
    +'<b>'+n+' lead'+plural+' sem responsável</b>'
    +'<div class="bts">'
    +'<select class="fld" id="cx-lote-v"><option value="">atribuir todos a…</option>'+ops+'</select>'
    +'<button class="pbtn ghost" id="cx-lote-um">Atribuir</button>'
    +'<button class="pbtn" id="cx-lote-rod">⚡ Distribuir pelo rodízio</button>'
    +'</div></div>';
  document.getElementById('cx-lote-um').onclick=function(){
    var v=document.getElementById('cx-lote-v').value;
    if(!v){alert('Escolha o vendedor.');return;}
    cxLote({vendedor_id:v},this);
  };
  document.getElementById('cx-lote-rod').onclick=function(){cxLote({rodizio:'1'},this);};
}
function cxUrlSemDono(){
  var q=new URLSearchParams(location.search);q.set('vendedor','sem');
  return '/painel/prospeccao/comunicacao?'+q.toString();
}
function cxLote(campos,bt){
  var rot=bt.textContent;bt.disabled=true;bt.textContent='Atribuindo…';
  var fd=new FormData();fd.append('escopo',_cxEscopo||'msg');
  Object.keys(campos).forEach(function(k){fd.append(k,campos[k]);});
  fetch('/painel/prospeccao/comunicacao/atribuir-lote',
        {method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      bt.disabled=false;bt.textContent=rot;
      if(!d.ok){alert(d.erro||'Não consegui atribuir.');return;}
      // o aviso do rodízio desligado é o que impede a conta de acumular órfãos de
      // novo — vale interromper pra ler.
      if(d.aviso)alert(d.aviso);
      _cxSemDonoN=-1;cxPollList();
    }).catch(function(){bt.disabled=false;bt.textContent=rot;alert('Falha de rede.');});
}
function cxPollList(){
  var box=document.getElementById('cx-list');if(!box)return;
  fetch('/painel/prospeccao/comunicacao/lista?'+cxParams()).then(function(r){return r.json();}).then(function(d){
    if(!d.ok)return;
    // aviso de importação: mostra o total já importado subindo, que é o que
    // realmente responde "ainda está vindo mais?"
    var av=document.getElementById('cx-sync-aviso'),tx=document.getElementById('cx-sync-txt');
    if(av){av.style.display=d.sincronizando?'block':'none';
      if(d.sincronizando&&tx)tx.textContent='📥 Importando conversas do WhatsApp… '
        +d.convs.length+' até agora. Pode ir usando, elas vão aparecendo sozinhas.';}
    cxSemDono(d.sem_dono||0);
    var h='';var novo={};var termo=cxTermo();
    d.convs.forEach(function(c){novo[c.id]=c;h+=cxListItem(c);});
    // A CONTAGEM diz de quantas. "100 conversa(s)" é verdade e esconde o que
    // importa: que existem outras fora do alcance da tela.
    var ct=document.getElementById('cx-conta');
    if(ct){var t=d.total||d.convs.length;
      ct.textContent=d.convs.length+((t>d.convs.length)?(' de '+t):'')+' conversa(s)'
        +(termo?(' · buscando'):'');}
    if(!h){
      if(termo){
        // Dizer ONDE procurou. "Nada encontrado" sozinho deixa a dúvida de se a
        // busca chegou a rodar — e o número prova que rodou na caixa inteira.
        h='<div class="cx-empty">Nenhuma conversa com <b>&ldquo;'+cxEsc(termo)+'&rdquo;</b>.'
          +'<br><span style="font-size:.82rem">Procurei no nome do lead, no nome do perfil '
          +'do WhatsApp e no número — nas <b>'+(d.total||0)+'</b> conversas desta caixa.</span>';
        // Busca e filtro se somam, e é aí que some resultado sem explicação: o vazio
        // conta quantos existem fora do filtro de canal e oferece o caminho.
        if(d.fora_do_filtro>0)h+='<br><a href="#" style="font-size:.82rem" '
          +'onclick="cxTodosOsCanais();return false">Buscar em todos os mensageiros</a>'
          +'<span style="font-size:.82rem"> — tem '+d.fora_do_filtro+' lá.</span>';
        h+='</div>';
      }else{h='<div class="cx-empty">Nenhuma conversa ainda.</div>';}
    }
    // sem isso, todo poll (4s) trocava o innerHTML inteiro mesmo sem nada mudar,
    // e sempre zerava o scroll pro topo — na sincronização de histórico (lista
    // reordenando toda hora) isso parecia a tela "subindo e descendo" sozinha.
    if(h===_cxListHtml)return;
    _cxListHtml=h;_cxList=novo;
    var perto=box.scrollTop<40;var st=box.scrollTop;
    box.innerHTML=h;
    box.scrollTop=perto?0:st;
  }).catch(function(){});
}
// Tirar o filtro de canal SEM perder o termo. O filtro mora na URL (é um form GET
// de sempre) e o termo mora no campo — então recarrega com o canal vazio e leva o
// termo no `q`, que a rota da página sabe ler.
function cxTodosOsCanais(){
  var q=new URLSearchParams(location.search);q.delete('canal');
  q.set('aba','conversas');q.set('q',cxTermo());
  location.search=q.toString();
}
function cxAgente(convId,on){
  var fd=new FormData();fd.append('conversa_id',convId);fd.append('ativar',on?'1':'0');
  fetch('/painel/prospeccao/comunicacao/agente-conversa',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){if(!d.ok){alert(d.erro||'Não consegui.');return;}cxOpen(document.getElementById('cxc-'+convId),convId);}).catch(function(){alert('Falha de rede.');});
}
// Trocar o responsável sem sair da conversa. Mesma rota da ficha do lead
// (/atribuir), que responde JSON quando vem por fetch — o guard de quem pode
// atribuir é o mesmo nos dois lugares.
function cxAtribuir(leadId,sel){
  if(!leadId)return;
  var antes=sel.getAttribute('data-antes')||'';
  sel.disabled=true;
  var fd=new FormData();fd.append('vendedor_id',sel.value);
  fetch('/painel/prospeccao/'+leadId+'/atribuir',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      sel.disabled=false;
      if(!d.ok){sel.value=antes;alert(d.erro||'Não consegui trocar o responsável.');return;}
      sel.setAttribute('data-antes',sel.value);
      // recarrega a conversa pro cabeçalho e a lista mostrarem o dono novo
      if(_cxConv)cxOpen(document.getElementById('cxc-'+_cxConv),_cxConv);
    }).catch(function(){sel.disabled=false;sel.value=antes;alert('Falha de rede.');});
}
// "Levar para o lead" agora CONFIRMA antes de criar. Sem isso o clique gravava na
// hora com o número cru no lugar do nome — e o funil enchia de lead chamado "5586…"
// mesmo com o nome do contato guardado no banco (agenda do celular / perfil do
// WhatsApp / remetente do e-mail). O modal abre com tudo preenchido: só conferir.
var _cxVlTemp='morno',_cxVlTipo='pf';
function cxVlFechar(){var m=document.getElementById('cx-vl');if(m)m.parentNode.removeChild(m);
  document.removeEventListener('keydown',cxVlEsc);}
function cxVirarLead(convId){
  fetch('/painel/prospeccao/comunicacao/virar-lead/'+convId,{headers:{'X-Requested-With':'fetch'}})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert(d.erro||'Não consegui.');return;}
      if(d.ja_lead){location.href='/painel/prospeccao/'+d.lead_id;return;}
      cxVlAbrir(convId,d);
    }).catch(function(){alert('Falha de rede.');});
}
function cxVlAbrir(convId,d){
  cxVlFechar();
  _cxVlTemp='morno';_cxVlTipo=(d.tipo==='pj')?'pj':'pf';
  var FONTES={agenda:'📇 veio da agenda do seu celular',perfil:'💬 nome do perfil no WhatsApp',
              email:'✉️ nome do remetente do e-mail'};
  var fonte=d.nome?(FONTES[d.nome_fonte]||''):'Não achei o nome em lugar nenhum — escreva como ele deve aparecer no funil.';
  var vend='';
  if(d.pode_atribuir){
    // deixar em branco não é a mesma coisa nos dois casos: com o rodízio ligado a
    // fila escolhe; desligado o lead fica sem dono e ninguém é avisado.
    var ops='<option value="">'+(d.rodizio?'— o rodízio escolhe —':'— sem responsável —')+'</option>';
    d.vendedores.forEach(function(v){ops+='<option value="'+v.id+'">'+cxEsc(v.nome)+'</option>';});
    var dica=d.rodizio?'':'<small class="mut" style="font-size:.72rem">O rodízio está desligado — sem escolher aqui, o lead fica sem dono.</small>';
    vend='<div><label class="lbl" for="vl-vend">Responsável</label><select class="fld" id="vl-vend">'+ops+'</select>'+dica+'</div>';
  }
  var dup=d.duplicado?('<div class="vl-dup">⚠️ Já existe um lead com esse telefone: <b>'+cxEsc(d.duplicado.empresa)
        +'</b>. <a href="/painel/prospeccao/'+d.duplicado.id+'">Abrir o lead existente</a> em vez de criar outro?</div>'):'';
  var temps=['frio','morno','quente'].map(function(t){
    return '<button type="button" class="rcpill'+(t==='morno'?' on':'')+'" data-vlt="'+t+'" onclick="cxVlTemp(this)">'+t+'</button>';}).join('');
  var m=document.createElement('div');
  m.id='cx-vl';m.className='vl-fundo';m.setAttribute('role','dialog');m.setAttribute('aria-modal','true');
  m.onclick=function(e){if(e.target===m)cxVlFechar();};
  m.innerHTML='<div class="vl-cx">'
    +'<div class="vl-cab"><b>Levar para o lead</b><span class="mut">Confira o nome e o telefone. É assim que ele vai aparecer no funil.</span></div>'
    +'<div class="vl-corpo">'
    +'<div><span class="lbl">Tipo</span><div class="rcpills" style="margin:0">'
      +'<button type="button" class="rcpill'+(_cxVlTipo==='pj'?' on':'')+'" data-vltp="pj" onclick="cxVlTipo(this)">🏢 Empresa</button>'
      +'<button type="button" class="rcpill'+(_cxVlTipo==='pf'?' on':'')+'" data-vltp="pf" onclick="cxVlTipo(this)">🧑 Pessoa física</button></div></div>'
    +'<div><label class="lbl" for="vl-nome">Nome do contato</label><input class="fld" id="vl-nome" value="'+cxEsc(d.nome||'')+'" placeholder="Como ele aparece no funil">'
      +(fonte?('<div class="vl-fonte">'+cxEsc(fonte)+'</div>'):'')+'</div>'
    +'<div id="vl-emp-box"'+(_cxVlTipo==='pf'?' style="display:none"':'')+'><label class="lbl" for="vl-emp">Empresa <span class="mut">(opcional)</span></label>'
      +'<input class="fld" id="vl-emp" placeholder="Mesmo nome do contato"></div>'
    +'<div class="vl-2"><div><label class="lbl" for="vl-tel">WhatsApp</label><input class="fld" id="vl-tel" value="'+cxEsc(d.telefone||'')+'"></div>'
      +'<div><label class="lbl" for="vl-mail">E-mail</label><input class="fld" id="vl-mail" value="'+cxEsc(d.email||'')+'" inputmode="email"></div></div>'
    +vend
    +'<div><span class="lbl">Temperatura</span><div class="rcpills" style="margin:0">'+temps+'</div></div>'
    +'</div>'+dup
    +'<div class="vl-pe"><button type="button" class="pbtn ghost" onclick="cxVlFechar()">Cancelar</button>'
    +'<button type="button" class="pbtn" id="vl-ok" onclick="cxVlCriar('+convId+')">Criar lead</button></div></div>';
  document.body.appendChild(m);
  var n=document.getElementById('vl-nome');if(n){n.focus();if(d.nome)n.select();}
  document.addEventListener('keydown',cxVlEsc);
}
function cxVlEsc(e){if(e.key==='Escape')cxVlFechar();}
function cxVlTemp(b){_cxVlTemp=b.getAttribute('data-vlt');
  b.parentNode.querySelectorAll('.rcpill').forEach(function(x){x.classList.remove('on');});b.classList.add('on');}
function cxVlTipo(b){_cxVlTipo=b.getAttribute('data-vltp');
  b.parentNode.querySelectorAll('.rcpill').forEach(function(x){x.classList.remove('on');});b.classList.add('on');
  var e=document.getElementById('vl-emp-box');if(e)e.style.display=(_cxVlTipo==='pj')?'':'none';}
function cxVlCriar(convId){
  var nome=(document.getElementById('vl-nome').value||'').trim();
  if(!nome){alert('Escreva o nome — é ele que vai aparecer no funil.');document.getElementById('vl-nome').focus();return;}
  var b=document.getElementById('vl-ok');b.disabled=true;b.textContent='Criando…';
  var vd=document.getElementById('vl-vend');
  var fd=new FormData();
  fd.append('conversa_id',convId);fd.append('nome',nome);fd.append('tipo',_cxVlTipo);
  fd.append('empresa',(document.getElementById('vl-emp').value||'').trim());
  fd.append('telefone',(document.getElementById('vl-tel').value||'').trim());
  fd.append('email',(document.getElementById('vl-mail').value||'').trim());
  fd.append('temperatura',_cxVlTemp);fd.append('vendedor_id',vd?vd.value:'');
  fetch('/painel/prospeccao/comunicacao/virar-lead',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){b.disabled=false;b.textContent='Criar lead';alert(d.erro||'Não consegui.');return;}
      location.href='/painel/prospeccao/'+d.lead_id;})
    .catch(function(){b.disabled=false;b.textContent='Criar lead';alert('Falha de rede.');});
}
// Envio NÃO BLOQUEIA. A mensagem aparece na hora, o campo fica livre pra
// escrever a próxima e o envio corre por trás. Antes o botão ficava desabilitado
// até a resposta voltar — e /enviar leva até 12s quando precisa religar a
// sessão, então o chat parecia travado bem no meio da conversa. Falha também não
// interrompe mais: em vez de um alert() por cima da tela, o próprio balão fica
// marcado com "não saiu" e um link pra tentar de novo, sem perder o texto.
function cxResponder(convId){
  var ta=document.getElementById('cx-reply');if(!ta)return;var t=ta.value.trim();if(!t)return;
  ta.value='';ta.focus();
  var pid='cxp'+(++_cxPendSeq);
  _cxPend.push({id:pid,texto:t,conv:convId,erro:false});
  cxRenderPend();
  cxEnviarPend(pid);
}
function cxReenviar(pid){
  var p=null;_cxPend.forEach(function(x){if(x.id===pid)p=x;});
  if(!p)return;p.erro=false;cxRenderPend();cxEnviarPend(pid);
}
function cxEnviarPend(pid){
  var p=null;_cxPend.forEach(function(x){if(x.id===pid)p=x;});
  if(!p)return;
  var fd=new FormData();fd.append('conversa_id',p.conv);fd.append('texto',p.texto);
  fetch('/painel/prospeccao/comunicacao/responder',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      if(d&&d.ok){
        // some da lista de pendentes; o balão de verdade vem no próximo desenho
        _cxPend=_cxPend.filter(function(x){return x.id!==pid;});
        _cxSig='';cxPollThread();cxPollList();
      }else{cxPendFalhou(pid,d&&d.erro);}
    }).catch(function(){cxPendFalhou(pid,'falha de rede');});
}
function cxPendFalhou(pid,motivo){
  _cxPend.forEach(function(x){if(x.id===pid){x.erro=true;x.motivo=motivo||'';}});
  cxRenderPend();
}
// Redesenha só os pendentes, sem tocar nas mensagens já confirmadas.
function cxRenderPend(){
  var b=document.getElementById('cx-msgs');if(!b)return;
  var velhos=b.querySelectorAll('.cx-pend');
  for(var i=0;i<velhos.length;i++)velhos[i].remove();
  // o placeholder "Sem mensagens" sai só quando há o que mostrar no lugar
  if(_cxPend.length){var vazio=b.querySelector('.cx-empty');if(vazio)vazio.remove();}
  b.insertAdjacentHTML('beforeend',cxPendHtml());
  cxScroll(true);
}
function cxPoll(){cxPollList();cxPollThread();}
(function(){var box=document.getElementById('cx-list');if(box){document.querySelectorAll('#cx-list .cx-conv').forEach(function(b){var id=parseInt(b.id.replace('cxc-',''));_cxList[id]={id:id,ult_msg_id:0};});
  // roda já na abertura: esperar 4s pro primeiro poll significa abrir a página no
  // meio de uma importação e não ver aviso nenhum logo de cara.
  cxPollList();
  // deep-link ?abrir=<conversa_id>: o drawer do funil (kbAbrirChat) manda pra cá
  // já sabendo qual conversa quer — abre direto, sem o clique extra na lista.
  // cxOpen tolera 'el' nulo (só pula a marcação de lida da lista, que ainda nem
  // carregou de verdade nesse instante).
  var _abrirQ={{ (abrir or '')|tojson }};
  if(_abrirQ)cxOpen(null,parseInt(_abrirQ,10));
  _cxTimer=setInterval(cxPoll,4000);
  // o navegador congela o timer quando a aba fica em segundo plano — ao voltar o
  // foco/visibilidade, atualiza na hora pra conversa subir e mostrar as msgs novas.
  document.addEventListener('visibilitychange',function(){if(!document.hidden)cxPoll();});
  window.addEventListener('focus',cxPoll);}})();
</script>
{% endblock %}"""

_CPILL_CSS = """<style>
/* base de botão/campo — as páginas de campanha não herdam _CSS (só o <button>/<input>
   genérico do template base), então .pbtn/.fld/.lbl/.pw precisam existir aqui */
.pw{width:100%;max-width:1240px;margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.pbtn{width:auto;margin:0;padding:.5rem .9rem;border-radius:9px;font-size:.86rem;font-weight:600;
  background:var(--verde);color:var(--sobre-verde);border:0;cursor:pointer;display:inline-flex;align-items:center;gap:.4rem;text-decoration:none}
.pbtn:hover{background:var(--verde-hover)}
.pbtn.ghost{background:transparent;color:var(--txt-mut);border:1px solid var(--borda)}
.pbtn.ghost:hover{color:var(--txt);border-color:var(--verde)}
.pbtn[disabled]{opacity:.45;cursor:not-allowed}
.fld{width:100%;padding:.55rem .7rem;border-radius:8px;border:1px solid #333;background:var(--bg);color:var(--txt);font-family:inherit;font-size:.9rem}
.lbl{display:block;color:var(--txt-mut);font-size:.72rem;margin-bottom:.15rem}
.cpill{font-size:.68rem;font-weight:700;padding:.14rem .5rem;border-radius:999px;border:1px solid var(--borda);white-space:nowrap}
.cpill.ativa{color:var(--verde);border-color:var(--neon-borda);background:var(--neon-fundo)}
.cpill.pausada{color:var(--ambar);border-color:var(--ambar-borda);background:#2a2113}
.cpill.rascunho,.cpill.concluida{color:#8a938a}
.cpill.ia{color:#c9a3e0;border-color:#4a3163;background:#1c1327}
.cpstep{display:flex;gap:.7rem;align-items:center;padding:.55rem 0;border-top:1px solid var(--borda)}
.cpstep:first-of-type{border-top:0}
.cpday{width:54px;text-align:center;flex-shrink:0}.cpday b{font-size:1.05rem}
/* pipeline */
.cppipe{display:flex;gap:.5rem;background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:.6rem;margin:1rem 0;overflow-x:auto}
.cppstep{flex:1;min-width:140px;border:1px solid var(--borda);border-radius:9px;padding:.55rem .65rem;position:relative;background:var(--bg)}
.cppstep h5{margin:.2rem 0 .05rem;font-size:.82rem}.cppstep p{margin:0;font-size:.72rem;color:var(--mut)}
.cppstep.on{border-color:var(--neon-borda);background:var(--neon-fundo)}
.cppstep .arw{position:absolute;right:-.6rem;top:50%;transform:translateY(-50%);color:var(--mut);z-index:2}
/* barra de progresso do card */
.cpbar{height:6px;border-radius:999px;background:#1e1f22;margin-top:.5rem;overflow:hidden;display:flex}
.cpbar i{display:block;height:100%}.cpbar .e{background:#5b9bd5}.cpbar .r{background:var(--verde)}
/* botão excluir no card da lista */
.cpx{width:32px;height:32px;padding:0;border-radius:8px;background:transparent;border:1px solid var(--borda);color:var(--mut);cursor:pointer;font-size:.9rem;flex-shrink:0;line-height:1}
.cpx:hover{color:var(--coral);border-color:#5c2a27;background:#231414}
/* explicação do fluxo */
.cphelp{border:1px solid var(--borda);border-radius:11px;background:var(--card);padding:.8rem .95rem;margin-top:.5rem}
.cphelp ol{margin:.4rem 0 0;padding-left:1.15rem;font-size:.82rem;color:var(--mut);line-height:1.7}
.cphelp ol b{color:var(--txt)}
/* resumo 3 colunas */
.cpsum{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.7rem;margin-top:1rem}
@media(max-width:760px){.cpsum{grid-template-columns:1fr}}
.cpsum .box{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.75rem .85rem}
.cpsum .box h5{margin:0 0 .4rem;font-size:.82rem}
.cpkv{display:flex;justify-content:space-between;gap:.5rem;font-size:.82rem;padding:.15rem 0}
.cpkv span{color:var(--mut)}
/* prévia do e-mail */
.mailp{background:#0e0f11;border:1px solid var(--borda);border-radius:11px;overflow:hidden;margin-top:.7rem}
.mailp .h{padding:.6rem .85rem;border-bottom:1px solid var(--borda);font-size:.78rem;color:var(--mut)}
.mailp .h b{color:var(--txt)}
.mailp .b{padding:.9rem 1rem;font-size:.9rem;line-height:1.6;white-space:pre-wrap}
.mailp .wa{display:inline-block;margin-top:.5rem;color:var(--verde);border:1px solid var(--neon-borda);background:var(--neon-fundo);border-radius:8px;padding:.3rem .65rem;font-size:.82rem;text-decoration:none}
.mailp .f{padding:.55rem .85rem;border-top:1px solid var(--borda);font-size:.68rem;color:var(--mut)}</style>""" + _NAV_ASSETS

_CAMPANHAS_TPL = """{% extends "base" %}{% block conteudo %}""" + _CPILL_CSS + """
<style>
.cwrap{max-width:1000px;margin:0 auto}
/* guia "como criar e configurar" */
.guia{border:1px solid var(--neon-borda);border-radius:12px;background:linear-gradient(180deg,var(--neon-fundo),transparent 60%);padding:.9rem 1rem;margin:1rem 0}
.guia>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:.5rem;font-weight:700;font-size:.92rem}
.guia>summary::-webkit-details-marker{display:none}
.guia>summary .chev{margin-left:auto;color:var(--mut);transition:transform .2s}
.guia[open]>summary .chev{transform:rotate(90deg)}
.guia .steps{list-style:none;margin:.8rem 0 0;padding:0;display:grid;gap:.5rem}
@media(min-width:760px){.guia .steps{grid-template-columns:1fr 1fr}}
.guia .steps li{display:flex;gap:.6rem;align-items:flex-start;background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:.55rem .65rem}
.guia .steps .gn{width:22px;height:22px;flex-shrink:0;border-radius:50%;display:grid;place-items:center;font-size:.74rem;font-weight:700;background:var(--neon-fundo);border:1px solid var(--neon-borda);color:var(--verde)}
.guia .steps .gt{font-size:.83rem;line-height:1.4}
.guia .steps .gt b{color:var(--txt)}.guia .steps .gt span{color:var(--mut)}
/* card de campanha */
.ccard{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:.9rem 1rem;display:flex;gap:.9rem;align-items:flex-start}
.ccard .body{flex:1;min-width:0}
.ccard .top{display:flex;align-items:center;gap:.5rem;min-width:0}
.ccard .nome{flex:1;min-width:0;font-weight:700;color:inherit;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ccard .nome:hover{color:var(--verde-claro)}
.ccard .badge{flex-shrink:0;font-size:.64rem;font-weight:700;border-radius:999px;padding:.05rem .4rem}
.ccard .badge.mail{color:#6fb0e6;border:1px solid #2f4a63;background:#11212e}
.ccard .badge.wa{color:var(--verde);border:1px solid #1e5c39;background:#0e2418}
.ckpis{display:flex;flex-wrap:wrap;gap:.35rem .55rem;margin-top:.5rem;font-size:.8rem;color:var(--mut)}
.ckpis .kv{display:inline-flex;align-items:baseline;gap:.28rem;white-space:nowrap}
.ckpis .kv b{color:var(--txt);font-variant-numeric:tabular-nums}
.ckpis .kv.g b{color:var(--verde-claro)}
.ckpis .sep{color:#3a3a3c}
/* motorzinho: gauge circular de "hoje" + pulso de atividade */
.motor{width:64px;flex-shrink:0;display:flex;flex-direction:column;align-items:center;gap:.3rem;text-align:center}
.gauge{position:relative;width:52px;height:52px}
.gauge svg{transform:rotate(-90deg)}
.gauge circle{fill:none;stroke-width:5}
.gauge .trilho{stroke:var(--borda)}
.gauge .prog{stroke:var(--verde-claro);stroke-linecap:round;transition:stroke-dashoffset .4s ease}
.gauge.pausada .prog{stroke:#5a5c5e}
.gauge .val{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  font-size:.72rem;font-weight:750;font-variant-numeric:tabular-nums;color:var(--txt)}
.motor .pulso{display:inline-flex;align-items:center;gap:.3rem;font-size:.66rem;color:var(--verde-claro);font-weight:600}
.motor .dot{width:6px;height:6px;border-radius:50%;background:var(--verde-claro);animation:pulso 1.6s ease-in-out infinite}
@media(prefers-reduced-motion:reduce){.motor .dot{animation:none}}
@keyframes pulso{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.72)}}
.motor .parada{font-size:.66rem;color:var(--mut)}
.motor .erro{font-size:.66rem;color:var(--coral);font-weight:700}
.motor .quando{font-size:.62rem;color:var(--mut);margin-top:.1rem}
/* cabeçalho + botão "+ Criar" + modal */
.pagehead{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap}
.hdrbtn{width:auto;margin:0;padding:.55rem 1rem;border-radius:9px;font-size:.86rem;font-weight:700;
  background:var(--verde);color:var(--sobre-verde);border:0;cursor:pointer;display:inline-flex;align-items:center;gap:.4rem;flex-shrink:0}
.hdrbtn:hover{background:var(--verde-hover)}
.ovl{position:fixed;inset:0;background:rgba(6,7,7,.6);backdrop-filter:blur(2px);display:none;align-items:center;justify-content:center;z-index:50;padding:1rem}
.ovl.on{display:flex}
.mcard{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:1.2rem 1.3rem;width:100%;max-width:380px;
  box-shadow:0 20px 50px rgba(0,0,0,.5)}
.mcard h3{margin:0 0 .2rem;font-size:1.02rem}
.mcard p{margin:0 0 .9rem;font-size:.8rem;color:var(--mut)}
.mcard .row{display:flex;gap:.5rem;margin-top:1rem}
.mcard .row .pbtn.ghost{flex:0 0 auto}
.mcard .row .pbtn.principal{flex:1;justify-content:center}
/* faixa de gastos + custo por canal */
.gastos{border:1px solid var(--borda);border-radius:12px;background:var(--card);padding:.7rem .9rem;margin:1rem 0}
.gastos .gh{display:flex;align-items:center;gap:.5rem;font-size:.72rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--mut);margin-bottom:.55rem}
.gastos .gg{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.6rem}
.gastos .gi{background:var(--bg);border:1px solid var(--borda);border-radius:10px;padding:.5rem .65rem}
.gastos .gi .k{font-size:.68rem;color:var(--mut)}
.gastos .gi .v{font-size:1.2rem;font-weight:800;letter-spacing:-.02em;margin-top:.1rem;font-variant-numeric:tabular-nums}
.gastos .gi .v.free{color:var(--verde-claro)} .gastos .gi .v.warn{color:var(--ambar)}
.gastos .gi .f{font-size:.64rem;color:var(--mut);margin-top:.05rem}
/* KPI que abre a lista de quem está por trás dele */
.gastos .gi.abre{cursor:pointer} .gastos .gi.abre:hover{border-color:var(--verde)}
.gastos .gi.abre.on{border-color:var(--verde);background:var(--neon-fundo)}
.gastos .gi .lup{opacity:.5;font-size:.62rem}
.kpi-painel{display:none;margin-top:.7rem;border:1px solid var(--borda);border-radius:11px;background:var(--bg-2)}
.kpi-painel.on{display:block}
.kv-top{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;padding:.55rem .7rem;border-bottom:1px solid var(--borda);font-size:.78rem}
.kv-vazio{padding:1.3rem;text-align:center;color:var(--mut);font-size:.85rem}
.kv-wrap{overflow-x:auto}
.kv-tab{width:100%;border-collapse:collapse;font-size:.79rem;min-width:820px}
.kv-tab th{text-align:left;color:var(--mut);font-weight:600;font-size:.69rem;padding:.4rem .55rem;border-bottom:1px solid var(--borda);white-space:nowrap}
.kv-tab td{padding:.45rem .55rem;border-bottom:1px solid var(--borda);vertical-align:top}
.kv-tab tr:last-child td{border-bottom:0}
.kv-teste{background:rgba(224,163,46,.06)} .kv-ruido{opacity:.5}
.kv-sub{font-size:.7rem;color:var(--mut);margin-top:.1rem}
.kv-det{min-width:210px;font-size:.75rem;color:var(--mut)}
.kv-alerta{color:var(--coral)}
.kv-nw{white-space:nowrap}
.kv-tag,.kv-tmp,.kv-flag{font-size:.68rem;border-radius:999px;padding:.05rem .42rem;border:1px solid var(--borda);white-space:nowrap;color:var(--mut)}
.kv-tmp.quente{color:var(--coral);border-color:var(--coral-borda);background:var(--coral-fundo)}
.kv-tmp.morno{color:var(--ambar);border-color:var(--ambar-borda);background:var(--ambar-fundo)}
.kv-flag.t{color:var(--ambar);border-color:var(--ambar-borda);background:var(--ambar-fundo)}
.kv-flag.a{color:var(--coral);border-color:var(--coral-borda);background:var(--coral-fundo)}
.kv-bt{font-size:.72rem;text-decoration:none;border:1px solid var(--borda);border-radius:7px;padding:.12rem .4rem;color:var(--txt)}
.kv-bt.zap{border-color:#1e5c39;color:var(--verde)}
.subline{margin-top:.4rem;font-size:.78rem;color:var(--mut)}
.subline b{color:var(--txt);font-variant-numeric:tabular-nums} .subline .g{color:var(--verde-claro)}
.chan{display:flex;flex-wrap:wrap;align-items:center;gap:.3rem .5rem;margin-top:.42rem;font-size:.79rem;color:var(--mut)}
.chan .cl{font-size:.72rem;font-weight:700;color:var(--txt);min-width:86px;display:inline-flex;align-items:center;gap:.3rem}
.chan .kv{display:inline-flex;align-items:baseline;gap:.26rem;white-space:nowrap}
.chan .kv b{color:var(--txt);font-variant-numeric:tabular-nums}
.chan .kv.g b{color:var(--verde-claro)} .chan .kv.err b{color:var(--coral)}
.chan .sep{color:#3a3a3c}
.pbar{position:relative;height:6px;border-radius:999px;background:var(--bg);border:1px solid var(--borda);overflow:hidden;margin-top:.3rem}
.pbar i{position:absolute;left:0;top:0;height:100%;border-radius:999px;transition:width .5s ease}
.pbar.mail .e{background:#3f6f9e}
.pbar .r{background:var(--verde-claro)}
.tbar{display:flex;align-items:center;gap:.5rem;margin-top:.32rem}
.tbar .bar{flex:1;height:7px;border-radius:999px;background:var(--bg);border:1px solid var(--borda);overflow:hidden}
.tbar .bar i{display:block;height:100%;border-radius:999px;transition:width .5s ease}
.tbar .lbl{font-size:.7rem;color:var(--mut);white-space:nowrap;font-variant-numeric:tabular-nums}
.tbar .lbl b{color:var(--txt)}
/* sinal de vida do motor (thread de fundo) — mesma escala do resto do cartão */
.motorstat{margin-top:.42rem;font-size:.73rem;display:flex;align-items:center;gap:.35rem;color:var(--mut)}
.motorstat .ic{font-size:.68rem;line-height:1}
.motorstat.ok{color:var(--verde-claro)}
.motorstat.lento{color:var(--ambar)}
.motorstat.parado{color:var(--coral);font-weight:600}
.motorstat a{color:inherit;text-decoration:underline;text-decoration-color:currentColor;opacity:.85}
.motorstat a:hover{opacity:1}
.fok{background:var(--verde)} .famar{background:var(--ambar)} .fcoral{background:var(--coral)}
.calert{margin-left:auto;font-size:.66rem;font-weight:700;padding:.08rem .45rem;border-radius:999px}
.calert.amar{color:var(--ambar);border:1px solid var(--ambar-borda);background:#2a2113}
.calert.coral{color:var(--coral);border:1px solid #5c2a27;background:#2a1513}
.semteto{margin-top:.34rem;font-size:.73rem;color:var(--mut)}
.semteto b{color:var(--txt)} .semteto a{color:var(--verde-claro);text-decoration:none}
</style>
<div class="pw">
""" + _navbar('campanhas') + """
  <div class="pagehead">
    <div>
      <h2 class="tt">📣 Campanhas</h2>
      <div class="mut" style="font-size:.85rem">Prospecção fria multicanal · <b style="color:var(--verde-claro)">{{ elegiveis }}</b> lead(s) com e-mail ou WhatsApp prontos pra abordar</div>
    </div>
    {% if gere_campanha %}<button class="hdrbtn" type="button" onclick="document.getElementById('ovlCriar').classList.add('on');document.getElementById('nomeCampanha').focus()">＋ Criar</button>{% endif %}
  </div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <div class="cppipe">
    <div class="cppstep"><div>📇</div><h5>Base</h5><p>captados</p><span class="arw">›</span></div>
    <div class="cppstep on"><div>📣</div><h5>Dispara</h5><p>💬 zap + ✉️ e-mail</p><span class="arw">›</span></div>
    <div class="cppstep"><div>💬</div><h5>Resposta → inbox</h5><p>agente assume</p><span class="arw">›</span></div>
    <div class="cppstep"><div>🔥</div><h5>Vira lead</h5><p>entra no funil</p></div>
  </div>

  <details class="guia">
    <summary>📖 Como criar e configurar uma campanha <span class="chev">›</span></summary>
    <ol class="steps">
      <li><span class="gn">1</span><span class="gt"><b>Criar</b> <span>— dê um nome (ex.: <code>Dentistas · Teresina</code>) e clique <b>Criar campanha</b>. Nasce como Rascunho.</span></span></li>
      <li><span class="gn">2</span><span class="gt"><b>Configurar</b> <span>— envios/dia, ligue o 💬 WhatsApp pro decisor e escolha o <b>material</b> (link, PDF, vídeo ou foto).</span></span></li>
      <li><span class="gn">3</span><span class="gt"><b>Público</b> <span>— filtre a base por segmento/cidade e adicione quem tem e-mail ou WhatsApp.</span></span></li>
      <li><span class="gn">4</span><span class="gt"><b>Sequência</b> <span>— monte os passos (<code>D+0</code>, <code>D+3</code>…) com 🤖 IA ou template.</span></span></li>
      <li><span class="gn">5</span><span class="gt"><b>Prévia</b> <span>— confira como o e-mail chega ao lead antes de disparar.</span></span></li>
      <li><span class="gn">6</span><span class="gt"><b>Ativar</b> <span>— dispara sozinho, <b>para</b> em quem responde e quem topa vira lead 🔥 no funil.</span></span></li>
    </ol>
  </details>

  {% if camps %}
  <div class="gastos" id="gastos">
    <div class="gh">💰 Gastos das campanhas</div>
    <div class="gg">
      <div class="gi"><div class="k">💬 Gasto WhatsApp</div><div class="v" data-t="tot_gasto">{{ totais.gasto_fmt }}</div><div class="f">só marketing é cobrado</div></div>
      <div class="gi"><div class="k">💬 Mensagens WhatsApp</div><div class="v" data-t="tot_msgs">{{ totais.msgs }}</div><div class="f">enviadas</div></div>
      <div class="gi"><div class="k">✉️ E-mails</div><div class="v free" data-t="tot_emails">{{ totais.emails }}</div><div class="f">grátis · não custa</div></div>
      <div class="gi"><div class="k">Teto total</div><div class="v" data-t="tot_teto">{{ totais.teto_fmt }}</div></div>
      <div class="gi"><div class="k">Perto do limite</div><div class="v warn" data-t="tot_perto">{{ totais.perto }}</div></div>
      <div class="gi"><div class="k">Custo médio/lead</div><div class="v" data-t="tot_cpl">{{ totais.custo_lead_fmt }}</div></div>
      <div class="gi abre" onclick="kpiAbre('sem_interesse',this)"><div class="k">🙅 Sem interesse agora <span class="lup">🔍</span></div><div class="v warn" data-t="tot_sem_interesse">{{ totais.sem_interesse }}</div><div class="f">clicaram "Agora não" no WhatsApp</div></div>
      <div class="gi abre" onclick="kpiAbre('quer_conhecer',this)"><div class="k">👋 Quero te conhecer <span class="lup">🔍</span></div><div class="v free" data-t="tot_quer_conhecer">{{ totais.quer_conhecer }}</div><div class="f">clicaram no WhatsApp</div></div>
      <div class="gi abre" onclick="kpiAbre('quer_material',this)"><div class="k">📎 Quero o material <span class="lup">🔍</span></div><div class="v free" data-t="tot_quer_material">{{ totais.quer_material }}</div><div class="f">clicaram no WhatsApp</div></div>
    </div>
    <div class="gh" style="margin-top:.9rem">📡 Sinais que os três de cima não pegam</div>
    <div class="gg">
      <div class="gi abre" onclick="kpiAbre('interesse_email',this)"><div class="k">📧 Tenho interesse <span class="lup">🔍</span></div><div class="v free" data-t="tot_int_email">{{ totais.interesse_email }}</div><div class="f">clicaram no <b>e-mail</b></div></div>
      <div class="gi abre" onclick="kpiAbre('baixou_material',this)"><div class="k">📥 Baixou o material <span class="lup">🔍</span></div><div class="v free" data-t="tot_baixou">{{ totais.baixou_material }}</div><div class="f">abriu o PDF/link</div></div>
      <div class="gi abre" onclick="kpiAbre('conversou',this)"><div class="k">💬 Conversou no chat <span class="lup">🔍</span></div><div class="v" data-t="tot_conversou">{{ totais.conversou }}</div><div class="f">escreveram alguma coisa</div></div>
      <div class="gi abre" onclick="kpiAbre('sem_humano',this)"><div class="k">⚠️ Sem resposta humana <span class="lup">🔍</span></div><div class="v warn" data-t="tot_sem_humano">{{ totais.sem_humano }}</div><div class="f">desses, só o bot falou</div></div>
    </div>
    <div id="kpi-painel" class="kpi-painel"><div id="kpi-corpo"></div></div>
  </div>
  {% endif %}
  {% if elegiveis == 0 %}<div class="mut" style="margin-top:.5rem;font-size:.85rem;border:1px solid var(--borda);border-radius:10px;padding:.7rem .9rem">Nenhum lead com e-mail ou WhatsApp ainda. Capte leads (Google Maps traz o telefone) pra começar.</div>{% endif %}

  <div style="display:flex;flex-direction:column;gap:.6rem;margin-top:1rem">
    {% for c in camps %}
    <div class="ccard" data-camp="{{ c.id }}">
      <div class="motor">
        <div class="gauge{% if c.status != 'ativa' %} pausada{% endif %}">
          <svg width="52" height="52"><circle class="trilho" cx="26" cy="26" r="21"></circle>
            <circle class="prog" cx="26" cy="26" r="21" stroke-dasharray="131.9" stroke-dashoffset="{{ c.hoje_offset if c.status == 'ativa' else 131.9 }}"></circle></svg>
          <div class="val">{{ (c.hoje ~ '/' ~ c.limite) if c.status == 'ativa' else '—' }}</div>
        </div>
        {% if c.status == 'ativa' %}
          <div class="pulso"><span class="dot"></span>ativa</div>
          {% if c.proximo %}<div class="quando" title="Horário do próximo disparo agendado">⏰ {{ c.proximo }}</div>{% endif %}
        {% else %}<div class="parada">{{ c.status_curto }}</div>{% endif %}
      </div>
      <div class="body">
        <div class="top">
          <a class="nome" href="/painel/prospeccao/campanhas/{{ c.id }}" title="{{ c.nome }}">{{ c.nome }}</a>
          <span class="badge mail" title="E-mail">✉️</span>
          {% if c.wa %}<span class="badge wa" title="WhatsApp">💬</span>{% endif %}
          <span class="cpill {{ c.status }}">{{ c.status_rot }}</span>
          {% if c.alerta == 'coral' %}<span class="calert coral" data-t="alerta">⛔ teto atingido</span>{% elif c.alerta == 'amar' %}<span class="calert amar" data-t="alerta">⚠ {{ c.pct }}% do teto</span>{% else %}<span class="calert amar" data-t="alerta" style="display:none"></span>{% endif %}
          {% if gerencia %}<form method="post" action="/painel/prospeccao/campanhas/{{ c.id }}/excluir" style="margin:0" onsubmit="return confirm('Excluir “{{ c.nome }}”? Os leads voltam pro funil.')">
            <button class="cpx" title="Excluir campanha">🗑</button>
          </form>{% endif %}
        </div>
        <div class="subline"><b>{{ c.n }}</b> {{ 'lead' if c.n == 1 else 'leads' }} · <b class="g" data-t="virou">{{ c.virou }}</b> virou lead 🔥 · limite <b>{{ c.limite }}</b>/dia{% if gerencia and c.responsavel %} · 👤 {{ c.responsavel }}{% endif %}</div>
        <div class="chan">
          <span class="cl">💬 WhatsApp</span>
          <span class="kv"><b data-t="wa_env">{{ c.wa_env }}</b> enviadas</span><span class="sep">·</span>
          <span class="kv g"><b data-t="wa_resp">{{ c.wa_resp }}</b> respostas</span><span class="sep">·</span>
          {% if c.wa_err %}<span class="kv err"><b data-t="wa_err">{{ c.wa_err }}</b> não chegou ⚠</span>{% else %}<span class="kv"><b data-t="wa_err">0</b> falhas</span>{% endif %}<span class="sep">·</span>
          <span class="kv"><b data-t="gasto">{{ c.gasto_fmt }}</b> 💰</span>
        </div>
        {% if c.teto %}
        <div class="tbar"><div class="bar"><i class="{{ 'fcoral' if c.alerta=='coral' else 'famar' if c.alerta=='amar' else 'fok' }}" data-w="teto" style="width:{{ c.pct }}%"></i></div>
          <div class="lbl"><b data-t="gasto2">{{ c.gasto_fmt }}</b> / {{ c.teto_fmt }} · <span data-t="pct">{{ c.pct }}</span>%</div></div>
        {% else %}
        <div class="semteto">Sem teto · <b>custo previsto</b>: <b>{{ c.previsto_fmt }}</b> · <a href="/painel/prospeccao/campanhas/{{ c.id }}">definir teto ›</a></div>
        {% endif %}
        <div class="chan">
          <span class="cl">✉️ E-mail</span>
          <span class="kv"><b data-t="email_env">{{ c.email.env }}</b> enviados</span><span class="sep">·</span>
          <span class="kv"><b data-t="email_abriu">{{ c.email.abriu }}</b> abriram</span><span class="sep">·</span>
          <span class="kv g"><b data-t="email_resp">{{ c.email.resp }}</b> respostas</span><span class="sep">·</span>
          {% if c.email.volta %}<span class="kv err"><b data-t="email_volta">{{ c.email.volta }}</b> voltaram ↩</span>{% else %}<span class="kv"><b data-t="email_volta">0</b> voltaram ↩</span>{% endif %}<span class="sep">·</span>
          <span class="kv" style="color:var(--verde-claro)">grátis</span>
        </div>
        <div class="pbar mail"><i class="e" data-w="email_fill" style="width:{{ c.email.pct }}%"></i><i class="r" data-w="email_resp_fill" style="width:{{ c.email.pct_resp }}%"></i></div>
        <div class="motorstat {{ totais.motor.estado }}" data-t="motor" title="{% if totais.motor.estado == 'parado' %}Reinicie o serviço openclaw-web no Render{% endif %}"><span class="ic">{{ totais.motor.ic }}</span> {{ totais.motor.texto }}</div>
      </div>
    </div>
    {% else %}<div class="mut" style="text-align:center;padding:2rem">Nenhuma campanha ainda — clique em <b>＋ Criar</b> acima.</div>{% endfor %}
  </div>
</div>

<div class="ovl" id="ovlCriar" onclick="if(event.target===this)this.classList.remove('on')">
  <div class="mcard">
    <h3>＋ Nova campanha</h3>
    <p>Dá pra renomear e configurar tudo depois.</p>
    <form method="post" action="/painel/prospeccao/campanhas/nova">
      <label class="lbl" for="nomeCampanha">Nome da campanha</label>
      <input class="fld" id="nomeCampanha" name="nome" placeholder="ex.: Dentistas · Teresina" maxlength="120">
      <div class="row">
        <button class="pbtn ghost" type="button" onclick="document.getElementById('ovlCriar').classList.remove('on')">Cancelar</button>
        <button class="pbtn principal" type="submit">Criar campanha</button>
      </div>
    </form>
  </div>
</div>
<script>
document.addEventListener('keydown', function(e){
  if(e.key === 'Escape'){ var o=document.getElementById('ovlCriar'); if(o) o.classList.remove('on'); }
});
/* Abre/fecha a lista de quem está por trás de um KPI. Carrega sob demanda: a
   consulta varre as mensagens da conta inteira e não pode pesar no load de quem
   só quer ver os números.
   FICA NO TOPO, fora do IIFE abaixo, porque é chamada por `onclick=` inline no
   HTML — e onclick só enxerga o escopo global. Dentro do IIFE ela existe, mas o
   clique morre num ReferenceError e a tela não faz nada. Mesma convenção de
   capToggle/secToggle/clUpd. */
var kpiAtual=null;
function kpiAbre(sinal, el){
  var painel=document.getElementById('kpi-painel'), corpo=document.getElementById('kpi-corpo');
  if(!painel||!corpo) return;
  document.querySelectorAll('.gastos .gi.abre').forEach(function(n){n.classList.remove('on');});
  if(kpiAtual===sinal){ kpiAtual=null; painel.classList.remove('on'); return; }
  kpiAtual=sinal; el.classList.add('on'); painel.classList.add('on');
  corpo.innerHTML='<div class="kv-vazio">Carregando…</div>';
  fetch('/painel/prospeccao/campanhas/kpi/'+encodeURIComponent(sinal))
    .then(function(r){ return r.ok ? r.text() : Promise.reject(r.status); })
    .then(function(h){ if(kpiAtual===sinal) corpo.innerHTML=h; })
    .catch(function(){ corpo.innerHTML='<div class="kv-vazio">Não consegui carregar a lista.</div>'; });
}
/* tempo real: pergunta as métricas a cada 10s e repinta números/barras sem refresh.
   pausa sozinho quando a aba está oculta (Page Visibility) e retoma ao voltar. */
(function(){
  function paint(c){
    var el=document.querySelector('.ccard[data-camp="'+c.id+'"]'); if(!el)return;
    var T=function(f,v){var n=el.querySelector('[data-t="'+f+'"]'); if(n&&v!=null)n.textContent=v;};
    var W=function(f,v){var n=el.querySelector('[data-w="'+f+'"]'); if(n&&v!=null)n.style.width=v+'%';};
    T('virou',c.virou);
    T('wa_env',c.wa_env); T('wa_resp',c.wa_resp); T('wa_err',c.wa_err); T('gasto',c.gasto_fmt); T('gasto2',c.gasto_fmt);
    T('email_env',c.email.env); T('email_abriu',c.email.abriu); T('email_resp',c.email.resp); T('email_volta',c.email.volta);
    W('email_fill',c.email.pct); W('email_resp_fill',c.email.pct_resp);
    if(c.teto){ W('teto',c.pct); T('pct',c.pct); }
    var al=el.querySelector('[data-t="alerta"]');
    if(al){
      if(c.alerta==='coral'){al.style.display='';al.className='calert coral';al.textContent='⛔ teto atingido';}
      else if(c.alerta==='amar'){al.style.display='';al.className='calert amar';al.textContent='⚠ '+c.pct+'% do teto';}
      else{al.style.display='none';}
    }
  }
  function paintTot(t){
    var s=function(f,v){var n=document.querySelector('.gastos [data-t="'+f+'"]'); if(n&&v!=null)n.textContent=v;};
    s('tot_gasto',t.gasto_fmt); s('tot_msgs',t.msgs); s('tot_emails',t.emails);
    s('tot_teto',t.teto_fmt); s('tot_perto',t.perto); s('tot_cpl',t.custo_lead_fmt);
    s('tot_sem_interesse',t.sem_interesse);
    s('tot_quer_conhecer',t.quer_conhecer); s('tot_quer_material',t.quer_material);
    s('tot_int_email',t.interesse_email); s('tot_baixou',t.baixou_material);
    s('tot_conversou',t.conversou); s('tot_sem_humano',t.sem_humano);
    if(t.motor){
      document.querySelectorAll('.motorstat').forEach(function(el){
        el.className='motorstat '+t.motor.estado;
        el.title=(t.motor.estado==='parado')?'Reinicie o serviço openclaw-web no Render':'';
        el.textContent=t.motor.ic+' '+t.motor.texto;
      });
    }
  }
  var timer=null;
  function tick(){
    if(document.hidden)return;
    fetch('/painel/prospeccao/campanhas/metricas',{headers:{'Accept':'application/json'}})
      .then(function(r){return r.ok?r.json():null;})
      .then(function(d){ if(d&&d.ok){ (d.camps||[]).forEach(paint); if(d.totais)paintTot(d.totais); } })
      .catch(function(){});
  }
  function start(){ if(timer)return; timer=setInterval(tick,10000); }
  document.addEventListener('visibilitychange',function(){
    if(document.hidden){ if(timer){clearInterval(timer);timer=null;} } else { tick(); start(); }
  });
  start();
})();
</script>
{% endblock %}"""

_CAMPANHA_TPL = """{% extends "base" %}{% block conteudo %}""" + _CPILL_CSS + """
<style>
.cdwrap{width:100%;max-width:1120px;margin:0 auto;box-sizing:border-box}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap}
.head h1{margin:0;font-size:1.4rem;display:flex;align-items:center;gap:.55rem;flex-wrap:wrap;min-width:0}
.head h1 .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
.head .sub{font-size:.85rem;color:var(--mut);margin-top:.25rem}
.head .acts{display:flex;gap:.45rem;flex-wrap:wrap;flex-shrink:0}
.pbtn.sm{padding:.36rem .6rem;font-size:.8rem}
/* stepper — clica e abre a seção correspondente */
.flow{display:flex;gap:.4rem;margin:1rem 0 1.3rem;overflow-x:auto;padding-bottom:.3rem;-webkit-overflow-scrolling:touch}
.step{flex:1;min-width:132px;display:flex;gap:.55rem;align-items:center;padding:.6rem .7rem;background:var(--card);border:1px solid var(--borda);border-radius:11px;text-decoration:none;color:var(--txt);cursor:pointer}
.step .n{width:22px;height:22px;flex-shrink:0;border-radius:50%;display:grid;place-items:center;font-size:.74rem;font-weight:700;background:var(--card-2);border:1px solid var(--borda);color:var(--mut)}
.step>.lbl2{display:flex;flex-direction:column;min-width:0}
.step .tt2{display:block;font-size:.82rem;font-weight:600;line-height:1.2}
.step .st{display:block;font-size:.68rem;color:var(--mut);margin-top:.05rem}
.step.done .n{background:var(--neon-fundo);border-color:var(--neon-borda);color:var(--verde)}
.step.todo .st{color:var(--ambar)}
/* ===== seção colapsável ===== */
.secs{display:flex;flex-direction:column;gap:.85rem}
.sec{background:var(--card);border:1px solid var(--borda);border-radius:13px;overflow:hidden}
.sumrow{display:flex;align-items:center;gap:.8rem;padding:.85rem 1.05rem;cursor:pointer;user-select:none;background:none;border:0;width:100%;text-align:left;font:inherit;color:inherit}
.sumrow:hover{background:var(--card-2)}
.sumrow .idx{width:26px;height:26px;flex-shrink:0;border-radius:8px;display:grid;place-items:center;font-size:.82rem;font-weight:750;background:var(--card-2);border:1px solid var(--borda);color:var(--verde-claro)}
.sec.done .sumrow .idx{background:var(--neon-fundo);border-color:var(--neon-borda);color:var(--verde)}
.sumrow h3{margin:0;font-size:.95rem;flex-shrink:0;font-weight:700}
.chips{display:flex;gap:.4rem;flex-wrap:wrap;flex:1;min-width:0;align-items:center}
.chip{font-size:.76rem;color:var(--mut);white-space:nowrap;padding:.18rem .55rem;border-radius:999px;border:1px solid var(--borda);background:var(--bg);display:inline-flex;align-items:center;gap:.3rem}
.chip b{color:var(--txt);font-weight:600;font-variant-numeric:tabular-nums}
.chip.warn{color:var(--ambar);border-color:var(--ambar-borda);background:#2a2113}
.chip.on{color:var(--verde-claro);border-color:var(--neon-borda);background:var(--neon-fundo)}
.caret{flex-shrink:0;width:30px;height:30px;border-radius:8px;border:1px solid var(--borda);display:grid;place-items:center;color:var(--mut);transition:transform .18s ease,background .15s ease}
.sumrow:hover .caret{border-color:var(--verde);color:var(--txt)}
.sec.open .caret{transform:rotate(180deg);background:var(--card-2)}
/* grid-template-rows 0fr→1fr anima até a altura real do conteúdo, sem teto —
   max-height fixo (jeito antigo) cortava campanhas com muitos leads/histórico
   aberto e não dava pra rolar até o fim. IMPORTANTE: o padding/borda tem que
   ficar no .bodypad DE DENTRO, não no .bodyin que encolhe — padding não
   encolhe com min-height:0 (é sempre somado à caixa), então ficava um
   resto de ~18px (1.1rem) vazando com o texto por trás mesmo fechado. */
.secbody{display:grid;grid-template-rows:0fr;transition:grid-template-rows .25s ease}
.sec.open .secbody{grid-template-rows:1fr}
.bodyin{min-height:0;overflow:hidden}
.bodypad{padding:0 1.05rem 1.1rem;border-top:1px solid var(--borda)}
.sec .desc{font-size:.8rem;color:var(--mut);margin:.8rem 0 .8rem}
.row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:flex-end}
.divi{border-top:1px solid var(--borda);margin:.85rem 0 .8rem}
.chk{display:flex;gap:.45rem;align-items:center;font-size:.85rem;cursor:pointer}
.chk input{width:16px;height:16px;accent-color:var(--verde)}
.foot{display:flex;justify-content:flex-end;gap:.5rem;margin-top:.9rem;flex-wrap:wrap}
/* material selector */
.mtabs{display:flex;gap:.4rem;flex-wrap:wrap;margin:.8rem 0 .6rem}
.mtab{display:inline-flex;align-items:center;gap:.35rem;padding:.4rem .7rem;border-radius:999px;font-size:.8rem;font-weight:600;background:transparent;border:1px solid var(--borda);color:var(--mut);cursor:pointer;flex:0 0 auto}
.mtab:hover{color:var(--txt);border-color:var(--verde)}
.mtab.on{background:var(--verde);border-color:var(--verde);color:var(--sobre-verde)}
.mpane{display:none}.mpane.on{display:block}
.drop{display:block;border:1px dashed var(--borda);border-radius:10px;background:var(--bg);padding:1rem;text-align:center;color:var(--mut);font-size:.85rem;cursor:pointer}
.drop:hover{border-color:var(--verde);color:var(--txt)}.drop b{color:var(--verde-claro)}
.mfile{display:flex;align-items:center;gap:.55rem;border:1px solid var(--neon-borda);background:var(--neon-fundo);border-radius:10px;padding:.5rem .7rem;margin-top:.5rem;font-size:.84rem}
.mhint{font-size:.74rem;color:var(--mut);margin-top:.5rem}
/* prévia + checklist */
.mailp{background:#0e0f11;border:1px solid var(--borda);border-radius:11px;overflow:hidden;margin-top:.8rem}
.mailp .h{padding:.55rem .8rem;border-bottom:1px solid var(--borda);font-size:.76rem;color:var(--mut);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mailp .h b{color:var(--txt)}
.mailp .b{padding:.85rem .95rem;font-size:.88rem;line-height:1.6;white-space:pre-wrap}
.mailp .f{padding:.5rem .8rem;border-top:1px solid var(--borda);font-size:.68rem;color:var(--mut)}
.ck{display:flex;align-items:center;gap:.5rem;font-size:.83rem;padding:.22rem 0}
.ck .dot{width:16px;height:16px;border-radius:50%;flex-shrink:0;display:grid;place-items:center;font-size:.68rem}
.ck.good .dot{background:var(--neon-fundo);border:1px solid var(--neon-borda);color:var(--verde)}
.ck.miss{color:var(--mut)}.ck.miss .dot{background:#2a2113;border:1px solid var(--ambar-borda);color:var(--ambar)}
/* passo */
.passo{border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;background:var(--bg)}
.passo+.passo{margin-top:.55rem}
.passo .prow{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
.passo .dtag{font-size:.72rem;color:var(--mut);display:flex;align-items:center;gap:.3rem}
.passo .dtag .fld{width:60px;text-align:center;padding:.35rem}
.passo textarea.fld{resize:vertical}
/* painel "Números não tentados" — a reserva de telefones que sobrou nos alvos parados */
.resv{display:flex;flex-direction:column;gap:.35rem}
.resv-l{border:1px solid var(--borda);border-radius:9px;background:var(--bg);overflow:hidden}
.resv-h{display:grid;grid-template-columns:20px 1fr auto;gap:.6rem;align-items:center;padding:.5rem .65rem;cursor:pointer}
.resv-h:hover{background:var(--card-2)}
.resv-h input{margin:0;accent-color:var(--verde);width:15px;height:15px}
.resv-e{min-width:0}
.resv-e b{display:block;font-size:.86rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.resv-n{font-family:var(--mono);font-size:.8rem;font-weight:700;color:var(--azul);white-space:nowrap;font-variant-numeric:tabular-nums}
.resv-n span{font-family:var(--fonte);font-weight:400;color:var(--mut);font-size:.72rem}
.resv-b{display:none;padding:0 .65rem .55rem 2.05rem;border-top:1px solid var(--borda)}
.resv-l.open .resv-b{display:block}
.resv-f{margin:.45rem 0 .3rem;padding-left:1.1rem;font-family:var(--mono);font-size:.82rem;color:var(--txt);font-variant-numeric:tabular-nums}
.resv-f li{margin:.1rem 0}
/* link "voltar" (breadcrumb) acima do título */
.voltar{display:inline-block;color:var(--txt-mut,#8a938a);text-decoration:none;font-size:.82rem;margin-bottom:.35rem}
.voltar:hover{color:var(--verde-claro)}
/* desempenho */
.kpigrp{margin-top:.9rem}
.kpigrp:first-child{margin-top:.8rem}
.kpihead{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);font-weight:700;margin:0 0 .35rem}
.cpstats4{display:grid;grid-template-columns:repeat(4,1fr);gap:.6rem}
.cpstats5{display:grid;grid-template-columns:repeat(5,1fr);gap:.6rem}
.cpstats6{display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem}
@media(max-width:680px){.cpstats5{grid-template-columns:repeat(3,1fr)}}
@media(max-width:560px){.cpstats4{grid-template-columns:repeat(2,1fr)}.cpstats5,.cpstats6{grid-template-columns:repeat(2,1fr)}}
.histrow>td{background:var(--bg)}
.histbox{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;padding:.7rem .4rem}
@media(max-width:600px){.histbox{grid-template-columns:1fr}}
.histcol h5{margin:0 0 .4rem;font-size:.78rem;color:var(--txt)}
.histev{display:flex;gap:.6rem;font-size:.8rem;padding:.25rem 0;border-bottom:1px solid var(--borda)}
.histev .qd{color:var(--mut);white-space:nowrap;min-width:82px}
.cpstat{background:var(--bg);border:1px solid var(--borda);border-radius:11px;padding:.7rem .8rem;min-width:0}
.cpstat .n{font-size:1.4rem;font-weight:750;letter-spacing:-.02em;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cpstat .l{font-size:.7rem;color:var(--mut);margin-top:.1rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cpstat.g .n{color:var(--verde)}.cpstat.r .n{color:var(--coral)}
.cpstat.novo{border-color:var(--neon-borda)}.cpstat.novo .l{color:var(--verde-claro)}
/* Por que a mensagem não chegou NAQUELE lead. Fica visível na linha, não só no
   hover: é por este motivo que o dono decide recolocar o lead na fila, e cada
   tentativa é uma mensagem de marketing cobrada — decisão que custa dinheiro não
   pode depender de passar o mouse (e no celular não existe hover).
   `white-space:normal` porque a célula é nowrap; sem isso a frase esticaria a
   coluna e jogaria a tabela inteira no scroll horizontal. */
.wa-why{font-size:.72rem;line-height:1.25;color:var(--coral);opacity:.85;
        white-space:normal;max-width:22ch;margin-top:.1rem}
.tbl-wrap{overflow-x:auto;border-radius:12px;border:1px solid var(--borda);margin-top:.8rem}
.tbl-wrap table{width:100%;border-collapse:collapse;font-size:.84rem;min-width:460px}
.tbl-wrap thead th{text-align:left;color:var(--mut);font-weight:500;padding:.55rem .9rem;background:var(--card-2)}
.tbl-wrap tbody td{padding:.55rem .9rem;border-top:1px solid var(--borda)}
.apill{font-size:.68rem;font-weight:600;padding:.1rem .45rem;border-radius:999px;border:1px solid var(--borda);white-space:nowrap}
.apill.respondeu,.apill.concluido{color:var(--verde);border-color:var(--neon-borda);background:var(--neon-fundo)}
.apill.enviado{color:#5b9bd5;border-color:#2f4a63;background:#14212e}
.apill.descadastrou,.apill.erro{color:var(--ambar);border-color:var(--ambar-borda);background:#2a2113}
.apill.fila{color:#8a938a}
</style>
{% set mt = camp.material_tipo or 'link' %}
{% set micon = {'link':'🔗','pdf':'📄','video':'🎬','foto':'🖼'} %}
{% set ns = namespace(modelo_nome='') %}
{% for m in modelos %}{% if m.codigo==camp.modelo_codigo %}{% set ns.modelo_nome = m.nome %}{% endif %}{% endfor %}
<div class="cdwrap">
  <div class="head">
    <div style="min-width:0">
      <a class="voltar" href="/painel/prospeccao/campanhas">‹ Voltar para Campanhas</a>
      <h1><span class="nm">{{ camp.nome }}</span> <span class="cpill {{ camp.status }}">{{ camp.status_rot }}</span></h1>
      <div class="sub"><b style="color:var(--txt)">{{ na_camp }}</b> lead(s) na campanha · limite <b style="color:var(--txt)">{{ camp.limite }}</b>/dia · ✉️ e-mail{% if camp.wa_ativo %} + 💬 WhatsApp{% endif %}{% if responsavel_nome %} · 👤 <b style="color:var(--txt)">{{ responsavel_nome }}</b>{% endif %}</div>
    </div>
    <div class="acts">
      {% if pode_atribuir %}<form method="post" action="/painel/prospeccao/campanhas/{{ camp.id }}/responsavel" style="margin:0;display:flex;align-items:center;gap:.35rem">
        <label class="lbl" style="margin:0">👤 Responsável</label>
        <select class="fld" name="vendedor_id" onchange="this.form.submit()" style="width:auto;padding:.36rem .5rem;font-size:.82rem">
          <option value="">— livre —</option>
          {% for v in vendedores %}<option value="{{ v.id }}" {% if camp.responsavel_id==v.id %}selected{% endif %}>{{ v.nome }}</option>{% endfor %}
        </select>
      </form>{% endif %}
      {% if camp.status != 'ativa' %}<form method="post" action="/painel/prospeccao/campanhas/{{ camp.id }}/status" style="margin:0"><input type="hidden" name="status" value="ativa"><button class="pbtn" {% if not na_camp %}disabled title="Adicione leads antes de ativar"{% endif %}>▶ Ativar</button></form>
      {% else %}<form method="post" action="/painel/prospeccao/campanhas/{{ camp.id }}/status" style="margin:0"><input type="hidden" name="status" value="pausada"><button class="pbtn ghost">❚❚ Pausar</button></form>{% endif %}
    </div>
  </div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <nav class="flow" aria-label="Etapas da campanha">
    <span class="step done" onclick="secOpen('s1')"><span class="n">✓</span><span class="lbl2"><span class="tt2">Configuração</span><span class="st">nome, ritmo, canais</span></span></span>
    <span class="step {% if passos %}done{% endif %}" onclick="secOpen('s2')"><span class="n">{% if passos %}✓{% else %}2{% endif %}</span><span class="lbl2"><span class="tt2">Sequência</span><span class="st">{{ passos|length }} passo(s)</span></span></span>
    <span class="step" onclick="secOpen('s3')"><span class="n">3</span><span class="lbl2"><span class="tt2">Prévia</span><span class="st">confira o e-mail</span></span></span>
    <span class="step {% if camp.status=='ativa' %}done{% else %}todo{% endif %}" onclick="secOpen('s4')"><span class="n">{% if camp.status=='ativa' %}✓{% else %}4{% endif %}</span><span class="lbl2"><span class="tt2">Ativar</span><span class="st">e acompanhar</span></span></span>
  </nav>

  <div class="secs">
    <!-- 1 · CONFIGURAÇÃO -->
    <div class="sec done" id="s1">
      <button type="button" class="sumrow" onclick="secToggle('s1')">
        <span class="idx">1</span><h3>Configuração</h3>
        <span class="chips">
          <span class="chip">Envios/dia <b>{{ camp.limite }}</b></span>
          <span class="chip {% if not camp.material %}warn{% endif %}">{{ micon[mt] }} Material: <b>{% if camp.material %}{{ mt }}{% else %}não configurado{% endif %}</b></span>
          <span class="chip {% if camp.wa_ativo and camp.wa_pronto %}on{% elif camp.wa_ativo %}warn{% endif %}">💬 WhatsApp {% if camp.wa_ativo and camp.wa_pronto %}ativo{% elif camp.wa_ativo %}não dispara{% else %}desligado{% endif %}</span>
          <span class="chip {% if camp.reengajar_ativo %}on{% endif %}">🔁 Reengajar {% if camp.reengajar_ativo %}em <b>{{ camp.reengajar_dias }}d</b>{% else %}desligado{% endif %}</span>
        </span>
        <span class="caret">▾</span>
      </button>
      <div class="secbody"><div class="bodyin"><div class="bodypad">
      <form method="post" action="/painel/prospeccao/campanhas/{{ camp.id }}/config" enctype="multipart/form-data">
        <p class="desc">O básico: nome, ritmo de envio e o material que o lead recebe.</p>
        <div class="row">
          <div style="flex:1;min-width:180px"><label class="lbl">Nome</label><input class="fld" name="nome" value="{{ camp.nome }}" maxlength="120"></div>
          <div><label class="lbl">Envios/dia</label><input class="fld" name="limite_dia" value="{{ camp.limite }}" inputmode="numeric" style="width:90px"></div>
        </div>

        <div style="margin-top:.75rem">
          <label class="lbl">✉️ Remetente <span style="color:var(--mut);font-weight:400">— de qual e-mail a campanha sai</span></label>
          {% if email_secundario %}
          <select class="fld" name="remetente_slot">
            <option value="principal" {% if camp.remetente_slot!='secundario' %}selected{% endif %}>Principal — {{ email_principal or 'e-mail da conta' }}</option>
            <option value="secundario" {% if camp.remetente_slot=='secundario' %}selected{% endif %}>Secundária — {{ email_secundario }}</option>
          </select>
          {% else %}
          <input type="hidden" name="remetente_slot" value="{{ camp.remetente_slot }}">
          <div class="mut" style="font-size:.74rem;margin-top:.2rem">Sai de <b>{{ email_principal or 'e-mail da conta' }}</b>. Configure uma <b>2ª caixa</b> em <a href="/painel/prospeccao/comunicacao?aba=canais" style="color:var(--verde-claro)">Comunicação › Canais</a> pra poder escolher aqui.</div>
          {% endif %}
        </div>

        <div style="margin-top:.75rem">
          <label class="lbl">📎 Material <span style="color:var(--mut);font-weight:400">— o que o lead recebe ao clicar “✅ Tenho interesse”</span></label>
          <input type="hidden" name="material_tipo" id="mtipo" value="{{ mt }}">
          <div class="mtabs">
            <button type="button" class="mtab {% if mt=='link' %}on{% endif %}" onclick="mtab(this,'link')">🔗 Link</button>
            <button type="button" class="mtab {% if mt=='pdf' %}on{% endif %}" onclick="mtab(this,'pdf')">📄 PDF</button>
            <button type="button" class="mtab {% if mt=='video' %}on{% endif %}" onclick="mtab(this,'video')">🎬 Vídeo</button>
            <button type="button" class="mtab {% if mt=='foto' %}on{% endif %}" onclick="mtab(this,'foto')">🖼 Foto</button>
          </div>
          <div class="mpane {% if mt=='link' %}on{% endif %}" data-m="link"><input class="fld" name="material_link" value="{% if mt=='link' %}{{ camp.material }}{% endif %}" placeholder="https://sua-apresentacao.com  ·  site, página, proposta…"></div>
          <div class="mpane {% if mt=='video' %}on{% endif %}" data-m="video"><input class="fld" name="material_video" value="{% if mt=='video' %}{{ camp.material }}{% endif %}" placeholder="Cole o link do YouTube, Loom ou Google Drive"><div class="mhint">Vídeo entra por link — mais leve pro lead abrir do que um arquivo pesado.</div></div>
          <div class="mpane {% if mt=='pdf' %}on{% endif %}" data-m="pdf">
            {% if mt=='pdf' and camp.material %}<div class="mfile">📄 <a href="{{ camp.material }}" target="_blank" rel="noopener" style="color:var(--verde-claro);text-decoration:none">material atual (PDF)</a><span class="mut" style="margin-left:auto;font-size:.74rem">enviar outro ↓</span></div>{% endif %}
            <label class="drop">📄 Escolher o PDF <b>(clique aqui)</b><div style="font-size:.72rem;margin-top:.2rem">até 10 MB</div><input type="file" name="material_pdf" accept="application/pdf" hidden onchange="mfile(this)"></label>
            <div class="mfile mfpick" style="display:none"></div>
          </div>
          <div class="mpane {% if mt=='foto' %}on{% endif %}" data-m="foto">
            {% if mt=='foto' and camp.material %}<img src="{{ camp.material }}" alt="material" style="max-height:120px;max-width:100%;border-radius:8px;border:1px solid var(--borda);margin-bottom:.4rem">{% endif %}
            <label class="drop">🖼 Escolher a imagem <b>(clique aqui)</b><div style="font-size:.72rem;margin-top:.2rem">JPG/PNG · até 6 MB</div><input type="file" name="material_foto" accept="image/*" hidden onchange="mfile(this)"></label>
            <div class="mfile mfpick" style="display:none"></div>
          </div>
          <div class="mhint">✨ O ZAQ envia sozinho na hora do interesse e o <b>agente IA assume a conversa</b> — você não precisa fazer nada.</div>
        </div>

        <div class="divi"></div>
        <label class="lbl" style="font-size:.82rem">💬 Template de WhatsApp — 1º contato frio (aprovado no Twilio)</label>
        <div class="mut" style="font-size:.74rem;margin-top:.15rem">A mensagem aprovada que fura a janela de 24h (o convite com os botões). <b>É diferente do modelo de e‑mail</b> da etapa 2 — aqui você cola o código do template do WhatsApp.</div>
        {% if camp.wa_bloqueio %}<div style="margin-top:.5rem;padding:.55rem .7rem;border:1px solid var(--ambar);border-radius:8px;background:rgba(224,163,46,.09);font-size:.78rem;line-height:1.45">⚠️ <b>O WhatsApp desta campanha não vai disparar.</b> {{ camp.wa_bloqueio }}</div>{% endif %}
        <div style="margin-top:.4rem">
          <label class="lbl">Content SID do template de WhatsApp (Twilio)</label>
          <input class="fld" name="wa_template_sid" value="{{ camp.wa_template_sid }}" placeholder="HX..." spellcheck="false" style="font-family:var(--mono)">
          <div class="mut" style="font-size:.74rem;margin-top:.2rem">Cole o SID do template aprovado <b>desta campanha</b> (começa com <code>HX</code>). Cada campanha pode ter o seu — não precisa mexer no Render. {% if camp.wa_pronto %}<span style="color:var(--verde-claro)">● pronto pra disparo frio</span>{% endif %}</div>
        </div>
        <div style="display:flex;gap:.8rem;align-items:center;flex-wrap:wrap;margin-top:.5rem">
          <label class="chk"><input type="checkbox" name="wa_ativo" value="1" {% if camp.wa_ativo %}checked{% endif %}> <span>Disparar o convite por WhatsApp junto com o e-mail</span></label>
          <div><label class="lbl">WhatsApp/dia</label><input class="fld" name="limite_wa_dia" value="{{ camp.limite_wa }}" inputmode="numeric" style="width:90px"></div>
        </div>
        <div class="mut" style="font-size:.74rem;margin-top:.3rem">Mira o <b>decisor</b> (Credify pelo CNPJ, ⭐ número dele); sem decisor, usa o melhor número captado. Lead sem número recebe só o e-mail.</div>
        <label class="chk" style="margin-top:.5rem"><input type="checkbox" name="wa_mmlite" value="1" {% if camp.wa_mmlite %}checked{% endif %}> <span>⚡ Usar <b>MM Lite</b> (entrega otimizada de marketing)</span></label>
        <div class="mut" style="font-size:.74rem;margin-top:.2rem"><b>Mesmo preço</b> do Cloud API — a MM Lite só melhora entrega/leitura no disparo em massa. Só funciona no <b>número próprio (Cloud API)</b> e com a conta habilitada em MM Lite na Meta; se não estiver, deixe desligado.</div>
        <div style="display:flex;gap:.8rem;align-items:flex-end;flex-wrap:wrap;margin-top:.6rem">
          <div><label class="lbl">💰 Teto de gasto WhatsApp (R$)</label><input class="fld" name="teto_wa" value="{{ camp.teto_wa }}" placeholder="ex.: 100,00" inputmode="decimal" style="width:160px"></div>
        </div>
        <div class="mut" style="font-size:.74rem;margin-top:.2rem">Limite de gasto do WhatsApp desta campanha. Ao alcançar, o motor <b>pausa a campanha sozinho</b> e o card acende o alerta. Vazio = sem teto (mostra só o custo previsto).</div>

        <div class="divi"></div>
        <label class="lbl" style="font-size:.82rem">🔁 Follow-up automático (reengajamento)</label>
        <div style="display:flex;gap:.8rem;align-items:center;flex-wrap:wrap;margin-top:.3rem">
          <label class="chk"><input type="checkbox" name="reengajar_ativo" value="1" {% if camp.reengajar_ativo %}checked{% endif %}> <span>Reengajar quem não respondeu</span></label>
          <div><label class="lbl">após (dias)</label><input class="fld" name="reengajar_dias" value="{{ camp.reengajar_dias }}" inputmode="numeric" style="width:80px"></div>
        </div>
        <div class="mut" style="font-size:.74rem;margin-top:.3rem">Quem recebeu a sequência e <b>não respondeu</b> em X dias leva <b>1 toque pelo outro canal</b>: WhatsApp (se ativo + número), senão um e-mail curto de reforço. Dispara <b>uma vez</b>, respeita o limite/dia e para quando o lead responde ou descadastra.</div>

        <div class="foot">
          <button type="button" class="pbtn ghost sm" onclick="secToggle('s1')">Fechar</button>
          <button class="pbtn ghost sm" formaction="/painel/prospeccao/campanhas/{{ camp.id }}/reiniciar" formmethod="post" formnovalidate style="color:#d98a2b;border-color:#5c4a27" onclick="return confirm('Reiniciar a campanha do zero?\\n\\nTodos os leads voltam pra fila no passo 0 e o acompanhamento (aberturas, status, histórico de Desempenho) é zerado. As conversas do inbox são preservadas.\\n\\nA campanha fica pausada até você clicar em Ativar — aí o motor recomeça do 1º e-mail.')">🔄 Reiniciar</button>
          <button class="pbtn ghost sm" formaction="/painel/prospeccao/campanhas/{{ camp.id }}/excluir" formmethod="post" formnovalidate style="color:var(--coral);border-color:#5c2a27" onclick="return confirm('Excluir a campanha? Os leads voltam pro funil.')">🗑 Excluir</button>
          <button class="pbtn sm">Salvar configuração</button>
        </div>
      </form>
      </div></div></div>
    </div>

    <!-- 2 · SEQUÊNCIA -->
    <div class="sec {% if passos %}done{% endif %}" id="s2">
      <button type="button" class="sumrow" onclick="secToggle('s2')">
        <span class="idx">2</span><h3>Sequência</h3>
        <span class="chips">
          <span class="chip {% if not passos %}warn{% endif %}"><b>{{ passos|length }}</b> passo(s)</span>
          {% if ns.modelo_nome %}<span class="chip">Modelo: <b>{{ ns.modelo_nome }}</b></span>{% endif %}
          {% if cadencia != '—' %}<span class="chip">{{ cadencia }}</span>{% endif %}
        </span>
        <span class="caret">▾</span>
      </button>
      <div class="secbody"><div class="bodyin"><div class="bodypad">
      <form method="post" action="/painel/prospeccao/campanhas/{{ camp.id }}/sequencia">
        <p class="desc">Os e-mails da campanha (isto <b>não</b> é o template de WhatsApp da etapa 1). <b>D+</b> = dias após o 1º e-mail (0 = primeiro). <b>🤖 IA</b> escreve único por lead; <b>Template</b> usa o texto (<code>{empresa}</code>, <code>{cidade}</code>, <code>{segmento}</code>).</p>
        <div style="display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin-bottom:.8rem;padding:.5rem .6rem;border:1px solid var(--borda);border-radius:10px;background:var(--bg)">
          <label class="lbl" style="margin:0" title="Modelo da sequência de e-mail por nicho — não confundir com o template de WhatsApp">📋 Modelo de e-mail (por nicho):</label>
          <select class="fld" id="modelo-sel" style="width:auto">
            {% for m in modelos %}<option value="{{ m.codigo }}" {% if m.codigo==camp.modelo_codigo %}selected{% endif %}>{{ m.nome }}{% if m.origem=='meu' %} (meu){% endif %}</option>{% endfor %}
          </select>
          <button type="button" class="pbtn ghost sm" onclick="aplicarModelo({{ camp.id }})">Aplicar à sequência</button>
          <span style="flex:1"></span>
          <button type="button" class="pbtn ghost sm" onclick="addPasso()">＋ Passo</button>
          <button type="button" class="pbtn ghost sm" onclick="salvarModelo({{ camp.id }})" title="Salvar a sequência atual como um modelo seu, reutilizável">💾 Salvar como modelo</button>
        </div>
        <div id="passos">
          {% for p in passos %}
          <div class="passo">
            <div class="prow">
              <span class="dtag">D+<input class="fld" name="dias" value="{{ p.dias }}" inputmode="numeric"></span>
              <select class="fld" name="usar_ia" style="width:auto"><option value="1" {% if p.ia %}selected{% endif %}>🤖 IA escreve</option><option value="0" {% if not p.ia %}selected{% endif %}>Template</option></select>
              <span style="flex:1"></span>
              <button type="button" class="pbtn ghost sm" onclick="remPasso(this)" title="Remover passo">✕</button>
            </div>
            <input class="fld" name="assunto" value="{{ p.assunto }}" placeholder="Assunto do e-mail" style="margin-top:.45rem">
            <textarea class="fld" name="corpo" rows="3" placeholder="Texto do e-mail (Template). Na opção IA, deixe vazio ou use como orientação." style="margin-top:.45rem">{{ p.corpo }}</textarea>
          </div>
          {% endfor %}
        </div>
        <div class="foot">
          <button type="button" class="pbtn ghost sm" onclick="secToggle('s2')">Fechar</button>
          <button class="pbtn sm">Salvar sequência</button>
        </div>
      </form>
      </div></div></div>
    </div>

    <!-- 3 · PRÉVIA -->
    <div class="sec {% if previa and na_camp and camp.material %}done{% endif %}" id="s3">
      <button type="button" class="sumrow" onclick="secToggle('s3')">
        <span class="idx">3</span><h3>Prévia</h3>
        <span class="chips">
          <span class="chip {% if passos %}on{% else %}warn{% endif %}">{% if passos %}✓{% else %}!{% endif %} sequência</span>
          <span class="chip {% if na_camp %}on{% else %}warn{% endif %}">{% if na_camp %}✓{% else %}!{% endif %} <b>{{ na_camp }}</b> lead(s)</span>
          <span class="chip {% if camp.material %}on{% else %}warn{% endif %}">{% if camp.material %}✓{% else %}!{% endif %} material</span>
        </span>
        <span class="caret">▾</span>
      </button>
      <div class="secbody"><div class="bodyin"><div class="bodypad">
        {% if previa %}
        <p class="desc" style="margin-bottom:.4rem">1º passo · exemplo com <b>{{ previa.empresa }}</b></p>
        <div class="mailp">
          <div class="h">De <b>{{ remetente or 'sua empresa' }}</b> · Para {{ previa.email or '—' }}</div>
          <div class="b"><b id="pv-assunto">{{ previa.assunto }}</b>
            <div style="height:.5rem"></div><span id="pv-corpo">{{ previa.corpo }}</span>
            <div style="margin:14px 0 2px"><span style="display:inline-block;padding:10px 20px;background:#16a34a;color:#fff;font-weight:bold;border-radius:8px">✅ Tenho interesse</span></div>
            <div class="mut" style="font-size:.72rem">Ao clicar, recebe o material{% if camp.material %} (já configurado){% else %} — <b>configure acima</b>{% endif %} e o agente assume.</div>
          </div>
          <div class="f">Sua empresa · descadastrar (link automático em cada envio).</div>
        </div>
        {% else %}
        <p class="desc">Sem exemplo ainda — adicione um passo na Sequência e um lead na campanha pra ver a prévia.</p>
        {% endif %}
        <div style="margin-top:1rem">
          <div class="ck {% if passos %}good{% else %}miss{% endif %}"><span class="dot">{% if passos %}✓{% else %}!{% endif %}</span> Sequência com {{ passos|length }} passo(s)</div>
          <div class="ck {% if na_camp %}good{% else %}miss{% endif %}"><span class="dot">{% if na_camp %}✓{% else %}!{% endif %}</span> {% if na_camp %}{{ na_camp }} lead(s) na campanha{% else %}Sem leads — mande da <b>Base</b> (Jogar na campanha){% endif %}</div>
          <div class="ck {% if camp.material %}good{% else %}miss{% endif %}"><span class="dot">{% if camp.material %}✓{% else %}!{% endif %}</span> {% if camp.material %}Material configurado{% else %}Material não configurado{% endif %}</div>
        </div>
        <div class="foot">
          <button type="button" class="pbtn ghost sm" onclick="secToggle('s3')">Fechar</button>
          {% if previa and previa.ia %}<button type="button" class="pbtn ghost sm" id="pia-btn" onclick="previaIA({{ camp.id }})">🤖 Gerar outro exemplo</button>{% endif %}
        </div>
      </div></div></div>
    </div>

    <!-- 4 · DESEMPENHO + LEADS -->
    <div class="sec {% if camp.status=='ativa' %}done{% endif %}" id="s4">
      <button type="button" class="sumrow" onclick="secToggle('s4')">
        <span class="idx">4</span><h3>Desempenho</h3>
        <span class="chips">
          <span class="chip">{{ metr.enviados }} enviados</span>
          <span class="chip {% if metr.responderam %}on{% endif %}">{{ metr.responderam }} responderam</span>
          <span class="chip">{{ metr.taxa }}% taxa</span>
          <span class="chip">{{ metr.fila }} na fila</span>
          {% if metr.sem_canal %}<span class="chip">{{ metr.sem_canal }} sem canal</span>{% endif %}
          {% if metr.wa_custo.tem %}<span class="chip">💰 {{ metr.wa_custo.total }}</span>{% endif %}
        </span>
        <span class="caret">▾</span>
      </button>
      <div class="secbody"><div class="bodyin"><div class="bodypad">
        {% if camp.status=='ativa' %}<div class="mut" style="font-size:.8rem;margin-top:.8rem">✅ <b style="color:var(--verde-claro)">Ativa</b> — dispara sozinho (até {{ camp.limite }}/dia) e para em quem responde ou descadastra.</div>{% else %}<div class="mut" style="font-size:.8rem;margin-top:.8rem">Clique <b>▶ Ativar</b> (no topo) pra o motor começar a disparar.</div>{% endif %}

        <div class="kpigrp"><div class="kpihead">📧 E-mail</div>
          <div class="cpstats6">
            <div class="cpstat novo"><div class="n">{{ metr.email_detectados }}</div><div class="l">Detectados</div></div>
            <div class="cpstat"><div class="n">{{ metr.enviados }}</div><div class="l">Enviados</div></div>
            <div class="cpstat"><div class="n">{{ metr.abriram }}</div><div class="l">Abriram 👁</div></div>
            <div class="cpstat"><div class="n">{{ metr.taxa_abertura }}%</div><div class="l">Taxa abertura</div></div>
            <div class="cpstat g"><div class="n">{{ metr.responderam }}</div><div class="l">Responderam</div></div>
            <div class="cpstat{% if metr.erros %} r{% endif %}"><div class="n">{{ metr.erros }}</div><div class="l">Erros</div></div>
          </div>
        </div>
        <div class="kpigrp"><div class="kpihead">💬 WhatsApp</div>
          <div class="cpstats6">
            <div class="cpstat novo"><div class="n">{{ metr.wa_detectados }}</div><div class="l">Detectados</div></div>
            <div class="cpstat"><div class="n">{{ metr.wa_enviados }}</div><div class="l">Enviados</div></div>
            <div class="cpstat"><div class="n">{{ metr.wa_entregues }}</div><div class="l">Entregues ✓✓</div></div>
            <div class="cpstat"><div class="n">{{ metr.wa_lidos }}</div><div class="l">Lidos 👀</div></div>
            <div class="cpstat"><div class="n">{{ metr.wa_taxa_leitura }}%</div><div class="l">Taxa leitura</div></div>
            <div class="cpstat{% if metr.wa_erros %} r{% endif %}"><div class="n">{{ metr.wa_erros }}</div><div class="l">Erros</div></div>
            {% if metr.wa_reserva %}<div class="cpstat novo" title="Números com WhatsApp que a base já tem, em leads que pararam, e que ainda não foram tentados"><div class="n">{{ metr.wa_reserva }}</div><div class="l">Na reserva</div></div>{% endif %}
          </div>
        </div>
        {% if metr.wa_custo.tem %}
        <div class="kpigrp"><div class="kpihead">💰 Custos — WhatsApp
          <span class="mut" style="font-size:.72rem;font-weight:400">· {% if metr.wa_custo.confirmado %}✓ confirmado pela Meta{% else %}estimado (tarifa BR){% endif %}</span></div>
          <div class="cpstats5">
            <div class="cpstat"><div class="n">{{ metr.wa_custo.cobradas }}</div><div class="l">Cobradas</div></div>
            <div class="cpstat g"><div class="n">{{ metr.wa_custo.gratis }}</div><div class="l">Grátis</div></div>
            <div class="cpstat"><div class="n" style="font-size:1.15rem">{{ metr.wa_custo.total }}</div><div class="l">Custo total</div></div>
            <div class="cpstat"><div class="n" style="font-size:1.15rem">{{ metr.wa_custo.por_lead }}</div><div class="l">Por lead</div></div>
            <div class="cpstat"><div class="n" style="font-size:1.15rem">{{ metr.wa_custo.tarifa_mkt }}</div><div class="l">Marketing/msg</div></div>
          </div>
          <div class="mut" style="font-size:.74rem;margin-top:.5rem">Só template é cobrado. Resposta do agente na janela de 24h e leads que entram por anúncio (FEP) saem grátis.</div>
          <div class="mut" style="font-size:.72rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;margin-top:.9rem">🧮 Simular um disparo</div>
          <div style="display:flex;gap:.7rem;align-items:flex-end;flex-wrap:wrap;margin-top:.35rem">
            <div><label class="lbl">Leads</label><input class="fld" id="sim-leads" value="10000" inputmode="numeric" style="width:120px" oninput="simCusto()"></div>
            <div><label class="lbl">% via anúncio (grátis)</label><input class="fld" id="sim-fep" value="0" inputmode="numeric" style="width:120px" oninput="simCusto()"></div>
            <div style="flex:1;min-width:150px">
              <div class="mut" style="font-size:.72rem">Custo estimado/mês</div>
              <div id="sim-out" style="font-size:1.35rem;font-weight:700;color:var(--verde-claro)">R$ 3.217,00</div>
            </div>
          </div>
          <div class="mut" id="sim-hint" style="font-size:.72rem;margin-top:.25rem"></div>
        </div>
        {% endif %}
        <div class="kpigrp"><div class="kpihead">📊 Geral</div>
          <div class="cpstats6">
            <div class="cpstat g"><div class="n">{{ metr.taxa }}%</div><div class="l">Taxa resposta</div></div>
            <div class="cpstat"><div class="n">{{ metr.fila }}</div><div class="l">Na fila</div></div>
            <div class="cpstat"><div class="n">{{ metr.sem_canal }}</div><div class="l">Sem canal</div></div>
            <div class="cpstat r"><div class="n">{{ metr.descadastros }}</div><div class="l">Descadastros</div></div>
            <div class="cpstat{% if metr.erros_total %} r{% endif %}"><div class="n">{{ metr.erros_total }}</div><div class="l">Erros totais</div></div>
            <div class="cpstat"><div class="n">{{ metr.hoje }}<span style="font-size:.9rem;color:var(--mut)">/{{ camp.limite }}</span></div><div class="l">Hoje</div></div>
          </div>
        </div>

        {% if reserva.leads %}
        <div class="kpihead" style="margin-top:1.1rem">Números não tentados</div>
        <div class="mut" style="font-size:.79rem;margin:.1rem 0 .5rem;max-width:64ch">Estes leads pararam no número que falhou, mas a base guarda outros com WhatsApp. O disparo tenta até <b>{{ tentativas_teto }}</b> números por lead — quem já passou disso só volta por aqui.</div>
        <div class="resv">
          {% for l in reserva.leads %}
          <div class="resv-l" id="rv{{ l.aid }}">
            <div class="resv-h" onclick="rvAb('rv{{ l.aid }}')">
              <input class="rv-ck" type="checkbox" value="{{ l.aid }}"{% if l.travado %} data-travado="1"{% endif %} onclick="event.stopPropagation()" onchange="rvUpd()"{% if l.no_teto %} disabled title="Já usou as {{ tentativas_teto }} tentativas"{% endif %}>
              <div class="resv-e"><b>{{ l.empresa }}</b><div class="mut" style="font-size:.74rem">{% if l.tentados %}tentou {{ l.tentativas }}{% if l.tentativas == 1 %} número{% else %} números{% endif %}{% if l.cod %} · erro {{ l.cod }}{% endif %}{% if l.no_teto %} · <span style="color:var(--ambar)">teto atingido</span>{% endif %}{% else %}sem tentativa registrada{% endif %}{% if l.travado and not l.no_teto %} · <span style="color:var(--azul)" title="Você escolheu {{ l.travado }} ao jogar este lead na campanha. O disparo não tenta outros sozinho — colocar na fila libera os demais.">🔒 número travado</span>{% endif %}</div></div>
              <span class="resv-n">{{ l.sobra }} <span>na reserva</span></span>
            </div>
            <div class="resv-b">
              <ol class="resv-f">{% for n in l.proximos %}<li>{{ n }}</li>{% endfor %}</ol>
              {% if l.sobra > l.proximos|length %}<div class="mut" style="font-size:.74rem">+ {{ l.sobra - l.proximos|length }} guardados depois destes</div>{% endif %}
            </div>
          </div>
          {% endfor %}
        </div>
        <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-top:.6rem">
          <span id="rv-count" class="mut" style="font-size:.78rem">Marque os leads pra devolver pra fila de disparo</span>
          <span style="flex:1"></span>
          <button type="button" class="pbtn sm" id="rv-btn" onclick="rvFila({{ camp.id }})" disabled>↻ Colocar na fila</button>
        </div>
        {% endif %}
        <div class="kpihead" style="margin-top:1.1rem">Contatos &amp; histórico</div>
        {% if leads %}
        <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-top:.3rem">
          <span id="cl-count" class="mut" style="font-size:.78rem">Marque os leads na tabela pra agir em lote (ex.: base sem riqueza de dados, sem interesse)</span>
          <span style="flex:1"></span>
          <a class="pbtn novo sm" href="/painel/prospeccao/campanhas/{{ camp.id }}/exportar-cliques" title="Baixa um CSV com 1 linha por lead: abriu e-mail, clicou 'Tenho interesse', baixou material, leu no WhatsApp, clicou 'Agora não' e descadastrou">📥 Exportar cliques (CSV)</a>
          <button type="button" class="pbtn ghost sm" id="cl-rem-btn" onclick="clRemSelecionados({{ camp.id }})" disabled>🗑 Remover selecionados</button>
        </div>
        {% endif %}
        <div class="tbl-wrap">
          <table>
            <thead><tr><th style="width:26px"><input type="checkbox" id="cl-all" onclick="clToggleAll(this)" title="Selecionar todos"></th><th>Empresa</th><th>Situação</th><th>📧 E-mail</th><th>💬 WhatsApp</th><th>Próximo/último</th><th></th></tr></thead>
            <tbody>
              {% for l in leads %}
              <tr><td><input class="cl-ck" type="checkbox" value="{{ l.pid }}" onchange="clUpd()"></td>
                <td><b>{{ l.empresa }}</b><div class="mut" style="font-size:.76rem">{% if l.email %}✉️ {{ l.email }}{% endif %}{% if l.email and l.fone %} · {% endif %}{% if l.fone %}💬 {{ l.fone }}{% endif %}{% if not l.email and not l.fone %}—{% endif %}</div></td>
                <td><span class="apill {{ l.status }}">{{ l.rot }}</span></td>
                <td class="mut" style="white-space:nowrap">D{{ l.passo }}{% if l.abriu %} · <span style="color:var(--verde-claro)" title="Abriu {{ l.abriu }}x · 1ª em {{ l.aberto }}">👁 {{ l.aberto }}{% if l.abriu > 1 %} ({{ l.abriu }}x){% endif %}</span>{% endif %}</td>
                <td class="mut" style="white-space:nowrap">{% if l.fone %}{{ l.fone }}{% if l.wa_rot %}<div style="font-size:.76rem">{{ l.wa_rot }}</div>{% endif %}{% else %}<span>{{ l.wa_rot or '—' }}</span>{% endif %}{% if l.wa_erro %}<div class="wa-why" title="{{ l.wa_erro|e }}">{{ l.wa_erro|e }}</div>{% endif %}</td>
                <td class="mut" style="white-space:nowrap">{% if l.status in ('fila','enviado') and l.prox %}⏳ {{ l.prox }}{% elif l.ult %}✓ {{ l.ult }}{% else %}—{% endif %}</td>
                <td style="text-align:right;white-space:nowrap"><button type="button" class="cpx" onclick="campHist({{ camp.id }},{{ l.pid }},this)" title="Ver histórico (data/hora por canal)">🕘</button> <button type="button" class="cpx" onclick="campRemLead(this,{{ camp.id }},{{ l.pid }})" title="Remover da campanha (o lead volta pra Base)">✕</button></td></tr>
              {% else %}<tr><td colspan="7" class="mut" style="text-align:center;padding:1.6rem">Nenhum lead ainda — mande da <b>Base</b> (marque os leads → “Jogar na campanha”).</td></tr>{% endfor %}
            </tbody>
          </table>
        </div>
      </div></div></div>
    </div>
  </div>
</div>
<script>
function secToggle(id){document.getElementById(id).classList.toggle('open');}
function secOpen(id){var el=document.getElementById(id);el.classList.add('open');el.scrollIntoView({behavior:'smooth',block:'start'});}
function simCusto(){
  var R=0.3217, L=document.getElementById('sim-leads'); if(!L)return;
  var n=parseInt((L.value||'0').replace(/\\D/g,''))||0;
  var fep=Math.max(0,Math.min(100,parseInt(document.getElementById('sim-fep').value||'0')||0));
  var pagos=Math.round(n*(1-fep/100)), custo=pagos*R;
  var fmt=function(v){return 'R$ '+v.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});};
  document.getElementById('sim-out').textContent=fmt(custo);
  document.getElementById('sim-hint').textContent=pagos.toLocaleString('pt-BR')+' cobrados · '+(n-pagos).toLocaleString('pt-BR')+' grátis (anúncio) · marketing R$0,3217/msg';
}
document.addEventListener('DOMContentLoaded',simCusto);
if(location.hash){var _h=location.hash.slice(1);document.addEventListener('DOMContentLoaded',function(){var el=document.getElementById(_h);if(el&&el.classList.contains('sec')){el.classList.add('open');el.scrollIntoView({block:'start'});}});}
function mtab(btn,tipo){
  var box=btn.parentNode; var A=box.querySelectorAll('.mtab');
  for(var i=0;i<A.length;i++)A[i].classList.remove('on');
  btn.classList.add('on');
  document.getElementById('mtipo').value=tipo;
  var P=document.querySelectorAll('.mpane');
  for(var j=0;j<P.length;j++)P[j].classList.toggle('on',P[j].getAttribute('data-m')===tipo);
}
function campRemLead(btn,camp,pid){
  if(!confirm('Remover este lead da campanha? Ele volta pra Base e pode ser reenviado depois.'))return;
  var body=new URLSearchParams();body.append('prospeccao_id',pid);
  fetch('/painel/prospeccao/campanhas/'+camp+'/remover-lead',{method:'POST',headers:{'X-Requested-With':'fetch'},body:body})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert('Não consegui remover ('+(d.erro||'?')+').');return;}
      var tr=btn.closest('tr');if(tr){var nx=tr.nextElementSibling;if(nx&&nx.classList.contains('histrow'))nx.remove();tr.remove();}
      clUpd();
    }).catch(function(){alert('Falha de rede.');});
}
function clChecked(){var a=[];document.querySelectorAll('.cl-ck:checked').forEach(function(c){a.push(c.value);});return a;}
function rvAb(id){document.getElementById(id).classList.toggle('open');}
function rvChecked(){return Array.prototype.slice.call(document.querySelectorAll('.rv-ck:checked')).map(function(c){return c.value;});}
function rvUpd(){
  var n=rvChecked().length;
  var btn=document.getElementById('rv-btn');
  if(btn){btn.disabled=!n;btn.textContent=n?'↻ Colocar na fila ('+n+')':'↻ Colocar na fila';}
  var cnt=document.getElementById('rv-count');
  if(cnt)cnt.textContent=n?n+' selecionado(s)':'Marque os leads pra devolver pra fila de disparo';
}
function rvFila(camp){
  var ids=rvChecked();
  if(!ids.length)return;
  var trav=document.querySelectorAll('.rv-ck:checked[data-travado="1"]').length;
  var aviso='Colocar '+ids.length+' lead(s) de volta na fila? Cada tentativa é uma mensagem de marketing cobrada.';
  if(trav)aviso+='\\n\\n'+trav+' deles tem número travado por você — colocar na fila libera os outros números da base.';
  if(!confirm(aviso))return;
  var body=new URLSearchParams();ids.forEach(function(id){body.append('ids',id);});
  var btn=document.getElementById('rv-btn');if(btn)btn.disabled=true;
  fetch('/painel/prospeccao/campanhas/'+camp+'/recolocar-na-fila',{method:'POST',headers:{'X-Requested-With':'fetch'},body:body})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert('Não consegui recolocar ('+(d.erro||'?')+').');rvUpd();return;}
      ids.forEach(function(id){var el=document.getElementById('rv'+id);if(el)el.remove();});
      rvUpd();
    }).catch(function(){alert('Falha de rede.');rvUpd();});
}
function clToggleAll(el){document.querySelectorAll('.cl-ck').forEach(function(c){c.checked=el.checked;});clUpd();}
function clUpd(){
  var total=document.querySelectorAll('.cl-ck').length;
  var n=clChecked().length;
  var btn=document.getElementById('cl-rem-btn');
  if(btn){btn.disabled=!n;btn.textContent=n?'🗑 Remover selecionados ('+n+')':'🗑 Remover selecionados';}
  var cnt=document.getElementById('cl-count');
  if(cnt)cnt.textContent=n?n+' selecionado(s)':'Marque os leads na tabela pra agir em lote (ex.: base sem riqueza de dados, sem interesse)';
  var all=document.getElementById('cl-all');
  if(all){all.checked=total>0&&n===total;all.indeterminate=n>0&&n<total;}
}
function clRemSelecionados(camp){
  var ids=clChecked();
  if(!ids.length)return;
  if(!confirm('Remover '+ids.length+' lead(s) da campanha? Eles voltam pra Base e podem ser reenviados depois.'))return;
  var body=new URLSearchParams();ids.forEach(function(id){body.append('ids',id);});
  var btn=document.getElementById('cl-rem-btn');if(btn)btn.disabled=true;
  fetch('/painel/prospeccao/campanhas/'+camp+'/remover-leads',{method:'POST',headers:{'X-Requested-With':'fetch'},body:body})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert('Não consegui remover ('+(d.erro||'?')+').');clUpd();return;}
      ids.forEach(function(id){
        var cb=document.querySelector('.cl-ck[value="'+id+'"]');if(!cb)return;
        var tr=cb.closest('tr');if(tr){var nx=tr.nextElementSibling;if(nx&&nx.classList.contains('histrow'))nx.remove();tr.remove();}
      });
      clUpd();
    }).catch(function(){alert('Falha de rede.');clUpd();});
}
function hEsc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
function campHist(camp,pid,btn){
  var tr=btn.closest('tr');
  var nx=tr.nextElementSibling;
  if(nx&&nx.classList.contains('histrow')){nx.remove();return;}   // toggle: fecha se já aberto
  var ncols=tr.children.length;
  var row=document.createElement('tr');row.className='histrow';
  row.innerHTML='<td colspan="'+ncols+'"><div class="mut" style="padding:.6rem;font-size:.8rem">carregando histórico…</div></td>';
  tr.parentNode.insertBefore(row,tr.nextSibling);
  fetch('/painel/prospeccao/campanhas/'+camp+'/lead/'+pid+'/historico',{headers:{'X-Requested-With':'fetch'}})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){row.firstChild.innerHTML='<div class="mut" style="padding:.6rem">não consegui carregar</div>';return;}
      function col(titulo,arr){
        var h='<div class="histcol"><h5>'+titulo+'</h5>';
        if(!arr||!arr.length){h+='<div class="mut" style="font-size:.78rem">sem eventos ainda</div>';}
        else{arr.forEach(function(e){
          h+='<div class="histev"><span class="qd">'+hEsc(e.quando)+'</span><span>'+hEsc(e.rot)
            +(e.detalhe?(' <span class="mut">· '+hEsc(e.detalhe)+'</span>'):'')+'</span></div>';});}
        return h+'</div>';
      }
      row.firstChild.innerHTML='<div class="histbox">'+col('📧 E-mail',d.email)+col('💬 WhatsApp',d.whatsapp)+'</div>';
    }).catch(function(){row.firstChild.innerHTML='<div class="mut" style="padding:.6rem">falha de rede</div>';});
}
function mfile(inp){
  var f=inp.files&&inp.files[0]; if(!f)return;
  var box=inp.closest('.mpane').querySelector('.mfpick');
  if(box){var mb=(f.size/1048576).toFixed(1);box.style.display='flex';box.innerHTML='📎 <b>'+f.name+'</b> <span class="mut" style="margin-left:auto;font-size:.75rem">'+mb+' MB</span>';}
}
function novoPasso(){return '<div class="passo"><div class="prow"><span class="dtag">D+<input class="fld" name="dias" value="7" inputmode="numeric"></span>'
  +'<select class="fld" name="usar_ia" style="width:auto"><option value="0" selected>Template</option><option value="1">🤖 IA escreve</option></select>'
  +'<span style="flex:1"></span><button type="button" class="pbtn ghost sm" onclick="remPasso(this)" title="Remover passo">✕</button></div>'
  +'<input class="fld" name="assunto" placeholder="Assunto do e-mail" style="margin-top:.45rem">'
  +'<textarea class="fld" name="corpo" rows="3" placeholder="Texto do e-mail (use {empresa}, {cidade})" style="margin-top:.45rem"></textarea></div>';}
function aplicarModelo(id){
  var sel=document.getElementById('modelo-sel');var cod=sel.value,nome=sel.options[sel.selectedIndex].text;
  if(!confirm('Aplicar o modelo "'+nome+'"? Isso substitui a sequência atual (você ainda pode editar e salvar).'))return;
  var body=new URLSearchParams();body.append('codigo',cod);
  fetch('/painel/prospeccao/campanhas/'+id+'/usar-modelo',{method:'POST',headers:{'X-Requested-With':'fetch','Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert('Não consegui aplicar ('+(d.erro||'?')+').');return;}
      location.reload();
    }).catch(function(){alert('Falha de rede.');});
}
function salvarModelo(id){
  var nome=prompt('Nome do modelo (ex.: Pet shop · meu jeito):');
  if(!nome||!nome.trim())return;
  var body=new URLSearchParams();body.append('nome',nome.trim());
  fetch('/painel/prospeccao/campanhas/'+id+'/salvar-modelo',{method:'POST',headers:{'X-Requested-With':'fetch','Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert('Não consegui salvar ('+(d.erro||'?')+').');return;}
      alert('Modelo salvo ✓ — já aparece na lista.');location.reload();
    }).catch(function(){alert('Falha de rede.');});
}
function addPasso(){document.getElementById('passos').insertAdjacentHTML('beforeend',novoPasso());}
function remPasso(b){var ps=document.querySelectorAll('#passos .passo');if(ps.length<=1){alert('Deixe ao menos 1 passo.');return;}b.closest('.passo').remove();}
function previaIA(id){var b=document.getElementById('pia-btn');if(b){b.disabled=true;b.textContent='Gerando…';}
  fetch('/painel/prospeccao/campanhas/'+id+'/previa-ia',{method:'POST',headers:{'X-Requested-With':'fetch'},body:new FormData()}).then(function(r){return r.json();}).then(function(d){if(b){b.disabled=false;b.textContent='🤖 Gerar';}
    if(!d.ok){alert(d.erro||'Não consegui.');return;}
    document.getElementById('pv-assunto').textContent=d.assunto;document.getElementById('pv-corpo').textContent=d.corpo;}).catch(function(){if(b){b.disabled=false;b.textContent='🤖 Gerar';}alert('Falha de rede.');});}
</script>
{% endblock %}"""

_env.loader.mapping["prospeccao"] = _KANBAN_TPL
_env.loader.mapping["prospeccao_base"] = _BASE_TPL
_env.loader.mapping["prospeccao_captar"] = _CAPTAR_TPL
_env.loader.mapping["prospeccao_ficha"] = _FICHA_TPL
_env.loader.mapping["prospeccao_comunicacao"] = _COMUNICACAO_TPL
# Lista de quem está por trás de um KPI. Parcial, servida sob demanda.
# ESCAPE: autoescape está DESLIGADO (DictLoader com nome sem extensão), e aqui sai
# nome de empresa e TRECHO DE MENSAGEM DO LEAD — texto externo puro. Todo dado leva
# `|e` na mão.
_KPI_LISTA_TPL = """
{% if not itens %}<div class="kv-vazio">Ninguém ainda neste sinal.</div>{% else %}
<div class="kv-top">
  <span class="mut">{{ itens|length }} lead{{ 's' if itens|length != 1 }}</span>
  {% set testes = itens|selectattr('eh_teste')|list|length %}
  {% if testes %}<span class="kv-flag t">🧪 {{ testes }} teste{{ 's' if testes != 1 }} da casa</span>{% endif %}
  {% set mudos = itens|rejectattr('resp_humana')|list|length %}
  {% if mudos %}<span class="kv-flag a">⚠️ {{ mudos }} sem resposta humana</span>{% endif %}
  <span style="flex:1"></span>
  <a class="kv-bt" href="/painel/prospeccao/campanhas/kpi/{{ sinal|e }}/csv">📥 CSV</a>
</div>
<div class="kv-wrap"><table class="kv-tab">
<thead><tr><th>Empresa</th><th>Campanha</th><th>Quando</th><th>Temp.</th><th>Vendedor</th><th>O que aconteceu</th><th></th></tr></thead>
<tbody>
{% for l in itens %}
<tr class="{% if l.eh_teste %}kv-teste{% elif l.eh_ruido %}kv-ruido{% endif %}">
  <td><b>{{ l.empresa|e or '(sem nome)' }}</b>
    {% if l.eh_teste %}<span class="kv-flag t">🧪 teste seu</span>
    {% elif l.eh_ruido %}<span class="kv-flag r">🤖 resposta automática</span>{% endif %}
    <div class="kv-sub">{% if l.segmento %}{{ l.segmento|e }}{% endif %}{% if l.cidade %} · {{ l.cidade|e }}{% endif %}</div></td>
  <td><span class="kv-tag">{{ l.campanha|e }}</span></td>
  <td class="kv-nw">{{ l.quando_br }}{% if l.vezes > 1 %}<div class="kv-sub">{{ l.vezes }}x</div>{% endif %}</td>
  <td class="kv-nw"><span class="kv-tmp {{ l.temperatura|e }}">{{ l.temperatura|e or '—' }}</span></td>
  <td class="kv-nw">👤 {{ l.vendedor|e }}</td>
  <td class="kv-det">
    {% if l.msgs_lead %}{{ l.msgs_lead }} msg{{ 's' if l.msgs_lead != 1 }} do lead ·
      {% if l.resp_humana %}{{ l.resp_humana }} resposta{{ 's' if l.resp_humana != 1 }} humana{% else %}<b class="kv-alerta">nenhuma resposta humana</b>{% endif %}{% endif %}
    {% if l.trecho %}<div class="kv-sub">“{{ l.trecho|e }}”</div>{% endif %}</td>
  <td class="kv-nw">
    {% if l.wa_link %}<a class="kv-bt zap" href="{{ l.wa_link|e }}" target="_blank" rel="noopener">💬</a>{% endif %}
    <a class="kv-bt" href="/painel/prospeccao/{{ l.id }}">ficha</a></td>
</tr>
{% endfor %}
</tbody></table></div>
{% endif %}"""

_env.loader.mapping["prospeccao_campanhas"] = _CAMPANHAS_TPL

# ATENÇÃO ao escape: este template roda com autoescape DESLIGADO (o DictLoader usa
# nomes sem extensão, e `select_autoescape()` resolve pra False nesse caso). Todo
# dado que veio do banco sai com `|e` na mão. Nome de empresa e segmento chegam de
# fonte externa (Google Maps, CNPJ, CSV importado) — sem o `|e` isso é injeção.
_RADAR_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<style>
.rwrap{max-width:1000px;margin:0 auto}
.rbaldes{display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem;margin:.9rem 0}
@media(max-width:640px){.rbaldes{grid-template-columns:repeat(2,1fr)}}
.rb{display:block;text-decoration:none;color:inherit;background:var(--card);
    border:1px solid var(--borda);border-radius:12px;padding:.6rem .7rem}
.rb.on{border-color:var(--verde);background:var(--neon-fundo)}
.rb .n{font-size:1.5rem;font-weight:750;font-variant-numeric:tabular-nums;line-height:1.1}
.rb .l{font-size:.72rem;color:var(--mut);margin-top:.15rem}
.rb.mao .n{color:var(--verde-claro)} .rb.nao .n{color:var(--coral)}
.rlead{background:var(--card);border:1px solid var(--borda);border-radius:12px;
       padding:.7rem .8rem;margin-bottom:.5rem;display:flex;gap:.7rem;align-items:flex-start}
.rlead.feito{opacity:.5}
.rlead .corpo{flex:1;min-width:0}
.rlead .emp{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rlead .meta{font-size:.76rem;color:var(--mut);margin-top:.1rem}
.rsinal{display:inline-block;font-size:.74rem;border-radius:999px;padding:.05rem .5rem;margin-top:.35rem}
.rsinal.mao{color:var(--verde-claro);border:1px solid var(--neon-borda);background:var(--neon-fundo)}
.rsinal.nao{color:var(--coral);border:1px solid var(--coral-borda);background:var(--coral-fundo)}
.rsinal.leu{color:var(--mut);border:1px solid var(--borda)}
.rsinal.frio{color:var(--text-faint);border:1px solid var(--borda)}
.racoes{display:flex;flex-direction:column;gap:.3rem;flex-shrink:0;align-items:stretch;width:170px}
.racoes .linha{display:flex;gap:.3rem}
.racoes .linha>*{flex:1;text-align:center}
/* No celular a coluna fixa de ações espremia o nome da empresa em "Mimo cani…" e
   empilhava o segmento/cidade numa tira estreita. Aqui as ações descem pra baixo
   do lead e viram uma faixa — o nome volta a caber, que é o que o vendedor lê. */
@media(max-width:640px){
  .rlead{flex-direction:column;gap:.5rem;align-items:stretch}
  /* `min-width:0` sozinho não segura filho de coluna: sem o `width:100%` o nome
     longo (nowrap+ellipsis) estica o cartão e joga a PÁGINA no scroll lateral. */
  .rlead .corpo{width:100%;max-width:100%}
  .racoes{width:auto;flex-direction:row;flex-wrap:wrap}
  .racoes>*{flex:1 1 auto}
  .racoes .linha{flex:1 1 100%}
}
.rbtn{font-size:.76rem;text-decoration:none;border-radius:8px;padding:.3rem .55rem;
      border:1px solid var(--borda);color:var(--txt);background:var(--bg);white-space:nowrap}
.rbtn.zap{border-color:#1e5c39;color:var(--verde)}
.rbtn.ok{cursor:pointer}
.rvazio{text-align:center;color:var(--mut);padding:2rem 1rem;font-size:.88rem}
</style>
<div class="pw rwrap">
""" + _navbar("radar") + """
  <h2 style="margin:.2rem 0 .1rem">🎯 Quem atacar</h2>
  <div class="mut" style="font-size:.82rem">
    Quem deu sinal nas campanhas, do mais quente pro mais frio. Atualizado a cada minuto.
    <span id="r-quando"></span>
  </div>

  <div class="rbaldes">
    <a class="rb mao{% if balde=='mao' %} on{% endif %}" href="/painel/prospeccao/radar?balde=mao{% if camp_sel %}&camp={{ camp_sel }}{% endif %}">
      <div class="n">{{ contagem.mao }}</div><div class="l">🔥 Levantou a mão</div></a>
    <a class="rb{% if balde=='leu' %} on{% endif %}" href="/painel/prospeccao/radar?balde=leu{% if camp_sel %}&camp={{ camp_sel }}{% endif %}">
      <div class="n">{{ contagem.leu }}</div><div class="l">👀 Leu, não respondeu</div></a>
    <a class="rb nao{% if balde=='nao' %} on{% endif %}" href="/painel/prospeccao/radar?balde=nao{% if camp_sel %}&camp={{ camp_sel }}{% endif %}">
      <div class="n">{{ contagem.nao }}</div><div class="l">🚫 Disse “agora não”</div></a>
    <a class="rb{% if balde=='frio' %} on{% endif %}" href="/painel/prospeccao/radar?balde=frio{% if camp_sel %}&camp={{ camp_sel }}{% endif %}">
      <div class="n">{{ contagem.frio }}</div><div class="l">💤 Sem sinal</div></a>
  </div>

  <form method="get" action="/painel/prospeccao/radar" style="display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;margin-bottom:.8rem">
    <input type="hidden" name="balde" value="{{ balde|e }}">
    <select class="fld" name="camp" onchange="this.form.submit()" style="max-width:280px">
      <option value="0">Todas as campanhas</option>
      {% for c in camps %}<option value="{{ c.id }}"{% if camp_sel == c.id %} selected{% endif %}>{{ c.nome|e }}</option>{% endfor %}
    </select>
    {% if balde %}<a class="rbtn" href="/painel/prospeccao/radar{% if camp_sel %}?camp={{ camp_sel }}{% endif %}">limpar filtro</a>{% endif %}
  </form>

  {% if balde == 'nao' %}
  <div class="mut" style="font-size:.8rem;border:1px solid var(--coral-borda);background:var(--coral-fundo);border-radius:10px;padding:.55rem .7rem;margin-bottom:.7rem">
    Estes clicaram em <b>“Agora não”</b> — recusaram. Ligar agora queima a marca. O lugar deles é uma reabordagem daqui a 60–90 dias, com outra oferta.
  </div>
  {% endif %}

  {% for l in itens %}
  <div class="rlead{% if l.falado_br %} feito{% endif %}" id="rl-{{ l.pid }}">
    <div class="corpo">
      <div class="emp">{{ l.empresa|e or '(sem nome)' }}</div>
      <div class="meta">
        {% if l.segmento %}{{ l.segmento|e }} · {% endif %}{% if l.cidade %}{{ l.cidade|e }}{% if l.uf %}/{{ l.uf|e }}{% endif %} · {% endif %}{{ l.campanha|e }}
        · 👤 {{ l.vendedor|e }}
      </div>
      <span class="rsinal {{ l.balde }}">{{ l.sinal }}{% if l.quando_br %} · {{ l.quando_br }}{% endif %}</span>
      {% if l.falado_br %}<span class="rsinal leu">✓ falado em {{ l.falado_br }}</span>{% endif %}
    </div>
    <div class="racoes">
      {% if l.wa_link %}<a class="rbtn zap" href="{{ l.wa_link|e }}" target="_blank" rel="noopener">💬 chamar no zap</a>{% endif %}
      {% if l.fone %}<a class="rbtn" href="tel:{{ l.fone|e }}">📞 {{ l.fone|e }}</a>{% endif %}
      <div class="linha">
        {% if l.email %}<a class="rbtn" href="mailto:{{ l.email|e }}" title="{{ l.email|e }}">✉️ e-mail</a>{% endif %}
        <a class="rbtn" href="/painel/prospeccao/{{ l.pid }}">ficha</a>
      </div>
      <button type="button" class="rbtn ok" onclick="radarFalei({{ l.pid }}, {{ 'true' if l.falado_br else 'false' }})">
        {% if l.falado_br %}↩ desfazer{% else %}✓ já falei{% endif %}</button>
    </div>
  </div>
  {% else %}
  <div class="rvazio">Nada aqui. {% if balde %}Tente outro balde ou limpe o filtro.{% else %}Quando um lead reagir a uma campanha, ele aparece nesta lista.{% endif %}</div>
  {% endfor %}
</div>
<script>
function radarFalei(pid, desfazer){
  var b = new URLSearchParams(); b.set('pid', pid);
  if(desfazer) b.set('desfazer','1');
  fetch('/painel/prospeccao/radar/contatado',{method:'POST',headers:{'X-Requested-With':'fetch'},body:b})
    .then(function(r){return r.json()})
    .then(function(d){ if(d.ok){ location.reload(); } else { alert('Não consegui marcar ('+(d.erro||'?')+').'); } })
    .catch(function(){ alert('Não consegui marcar.'); });
}
// "tempo real" aqui é recarregar sozinho: os webhooks escrevem na hora e o poller
// roda a cada 2min, então 60s de atraso é menos que a granularidade do dado.
// Não recarrega com a aba escondida, pra não competir com quem está atendendo.
setInterval(function(){ if(!document.hidden) location.reload(); }, 60000);
(function(){ var e=document.getElementById('r-quando');
  var h=new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
  if(e) e.textContent='· '+h; })();
</script>
{% endblock %}"""
_env.loader.mapping["prospeccao_radar"] = _RADAR_TPL
_env.loader.mapping["prospeccao_campanha"] = _CAMPANHA_TPL


_REGUA_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<style>
.rg-grp{display:flex;align-items:center;gap:.6rem;margin:1.1rem 0 .35rem}
.rg-grp b{font-family:var(--mono);font-size:.68rem;letter-spacing:.16em;text-transform:uppercase;white-space:nowrap}
.rg-grp span{flex:1;height:1px;background:var(--borda)}
.rg-r1{display:grid;grid-template-columns:12px 1fr 150px 62px;gap:.6rem;align-items:center}
.rg-uni{padding:.48rem .5rem;border-radius:8px;border:1px solid #333;background:var(--bg);color:var(--txt);font-size:.8rem;font-family:inherit}
.rg-sel{width:100%;box-sizing:border-box;padding:.42rem .6rem;border-radius:8px;border:1px solid var(--azul-borda);
  background:var(--azul-fundo);color:var(--azul);font-size:.8rem;font-family:inherit}
.rg-sel:disabled{border-color:var(--borda);background:transparent;color:var(--txt-mut)}
.rg-seg{display:inline-flex;padding:3px;background:var(--bg);border:1px solid var(--borda);border-radius:999px;gap:2px}
.rg-seg label{padding:.34rem .8rem;border-radius:999px;font-size:.78rem;color:var(--txt-mut);cursor:pointer;white-space:nowrap}
.rg-seg input{display:none}
.rg-seg input:checked+label{background:var(--azul-fundo);color:var(--azul);font-weight:600;box-shadow:inset 0 0 0 1px var(--azul-borda)}
.rg-seg input[value=off]:checked+label{background:var(--verde);color:var(--sobre-verde);box-shadow:none}
.rg-dia{padding:.35rem .7rem;border-radius:999px;font-size:.8rem;border:1px solid var(--borda);color:var(--txt-mut);cursor:pointer}
.rg-dia input{display:none}
.rg-dia.on{border-color:var(--neon-borda);background:var(--neon-fraco);color:var(--verde-claro)}
.rg-tag{display:inline-flex;align-items:center;padding:.06rem .45rem;border-radius:999px;font-size:.68rem;font-weight:600}
</style>
<div class="pw">
""" + _navbar("regua") + """
  <div style="display:flex;align-items:flex-start;gap:.6rem;flex-wrap:wrap">
    <div style="flex:1;min-width:200px">
      <h2 class="tt">Régua do funil</h2>
      <div class="mut" style="font-size:.82rem;margin-top:.15rem">{% if conta %}<b style="color:var(--verde-claro)">🏢 {{ conta[2] }}</b> · {% endif %}as etapas, o que traz o lead pra cada uma, e quanto tempo ela aguenta</div>
    </div>
  </div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <form method="post" action="/painel/prospeccao/regua/config">
  <!-- ---------------- estado ---------------- -->
  <div class="fsec" style="margin-top:1rem;border-color:var(--azul-borda)">
    <div class="sh"><b>Estado</b><span class="mut" style="font-size:.76rem">tudo construído · você decide quando cada parte age</span></div>
    {% for campo, nome, desc in [
        ('gatilhos_modo','Gatilhos das etapas','movem o card sozinhos quando o fato acontece'),
        ('cobranca_modo','Cobrança por prazo','avisa o vendedor e escala pro gestor')] %}
    <div style="display:flex;align-items:center;gap:1rem;padding:.8rem 0;border-top:1px solid var(--borda);flex-wrap:wrap">
      <div style="flex:1;min-width:240px">
        <div style="font-size:.9rem;font-weight:600">{{ nome }}</div>
        <div class="mut" style="font-size:.79rem;margin-top:.15rem">{{ desc }}</div>
      </div>
      <span class="rg-seg">
        {% for v, r in [('off','Desligado'),('observando','Observando'),('ligado','Ligado')] %}
        <input type="radio" id="{{ campo }}_{{ v }}" name="{{ campo }}" value="{{ v }}" {% if cfg[campo]==v %}checked{% endif %}>
        <label for="{{ campo }}_{{ v }}">{{ r }}</label>
        {% endfor %}
      </span>
    </div>
    {% endfor %}
    <div style="display:flex;align-items:center;gap:.6rem;padding-top:.75rem;border-top:1px solid var(--borda);flex-wrap:wrap">
      <span class="mut" style="font-size:.79rem;line-height:1.55;flex:1;min-width:280px">
        <b style="color:var(--azul)">Observando</b> roda o motor inteiro e anota o que <i>teria</i> feito — sem mover card nem avisar ninguém.
        O histórico já roda de qualquer jeito: <b class="num" style="color:var(--txt)">{{ n_mov }}</b> movimento(s) gravado(s).
      </span>
      <a class="pbtn ghost" href="/painel/prospeccao/regua/ritmo" style="white-space:nowrap">📈 Ver o ritmo real</a>
    </div>
  </div>

  <!-- ---------------- conversa ---------------- -->
  <div class="fsec" style="margin-top:.9rem">
    <div class="sh"><b>Quando a bola está com a gente</b><span class="mut" style="font-size:.76rem">lido da conversa, inclusive do celular do vendedor</span></div>
    {% for b in conv %}
    <div style="display:grid;grid-template-columns:1fr 150px;gap:.6rem;align-items:center;padding:.62rem 0;border-top:1px solid var(--borda)">
      <div style="font-size:.89rem;font-weight:600">{{ b.rotulo }}</div>
      <div style="display:flex;gap:.3rem">
        <input class="fld" style="text-align:right" name="{{ b.chave }}_n" value="{{ b.prazo_n }}">
        <select class="rg-uni" name="{{ b.chave }}_u">
          {% for u, r in unidades %}<option value="{{ u }}" {% if b.prazo_u==u %}selected{% endif %}>{{ r }}</option>{% endfor %}
        </select>
      </div>
    </div>
    {% endfor %}
  </div>

  <!-- ---------------- janela + escalonamento ---------------- -->
  <div class="fgrid" style="grid-template-columns:1.1fr 1fr">
    <div class="fsec">
      <div class="sh"><b>Janela de atendimento</b></div>
      <div style="display:flex;gap:.35rem;flex-wrap:wrap;margin:.3rem 0 .8rem">
        {% for d, r in [(1,'Seg'),(2,'Ter'),(3,'Qua'),(4,'Qui'),(5,'Sex'),(6,'Sáb'),(7,'Dom')] %}
        <label class="rg-dia {% if d in dias_on %}on{% endif %}" onclick="rgDia(this)">
          <input type="checkbox" name="dias" value="{{ d }}" {% if d in dias_on %}checked{% endif %}>{{ r }}
        </label>
        {% endfor %}
      </div>
      <div class="egrid">
        <div><label class="lbl">Abre</label><input class="fld" name="abre" value="{{ cfg.janela_abre.strftime('%H:%M') }}"></div>
        <div><label class="lbl">Fecha</label><input class="fld" name="fecha" value="{{ cfg.janela_fecha.strftime('%H:%M') }}"></div>
      </div>
      <p class="mut" style="font-size:.76rem;line-height:1.55;margin:.7rem 0 0">
        Todo prazo desta tela só corre aqui dentro. Gatilho, não: fato é fato a qualquer hora — sinal pago às 23h move o card às 23h.
      </p>
    </div>
    <div class="fsec">
      <div class="sh"><b>Escalonamento</b></div>
      <label class="lbl" style="margin-top:.3rem">Depois de quanto tempo sem toque escala pro gestor</label>
      <div style="display:flex;gap:.3rem">
        <input class="fld" style="text-align:right" name="escala_n" value="{{ (cfg.escala_min // 60) or 4 }}">
        <select class="rg-uni" name="escala_u"><option value="h" selected>horas</option><option value="d">dias</option></select>
      </div>
      <label class="lbl" style="margin-top:.7rem">Teto de avisos por vendedor / dia</label>
      <input class="fld" name="teto" value="{{ cfg.teto_avisos_dia }}">
      <p class="mut" style="font-size:.76rem;line-height:1.5;margin:.55rem 0 0">Passou do teto, vira um resumo só no fim do expediente.</p>
    </div>
  </div>

  <div style="margin-top:1rem"><button class="pbtn">Salvar régua</button></div>
  </form>

  <!-- ---------------- etapas ---------------- -->
  <div class="fsec" style="margin-top:1.1rem">
    <div class="sh"><b>As etapas do funil</b><span class="mut" style="font-size:.76rem">cada linha salva sozinha · ligue um gatilho de cada vez</span></div>
    {% set fases = [('venda','Fase · Venda','o lead ainda está sendo conquistado'),
                    ('fechamento','Fase · Fechamento','relatório e comissão contam a partir daqui'),
                    ('pos','Fase · Pós-venda','já é cliente — continua contando como fechado')] %}
    {% for fchave, ftit, fnota in fases %}
      {% set doFase = etapas | selectattr('fase','equalto',fchave) | list %}
      {% if doFase %}
      <div class="rg-grp"><b style="color:var(--txt-mut)">{{ ftit }}</b><span></span><span class="mut" style="font-size:.72rem;flex:0 0 auto">{{ fnota }}</span></div>
      {% for e in doFase %}
      <form class="rg-etapa" onsubmit="return rgSalvar(event)"
            action="/painel/prospeccao/regua/etapa/{{ e.id }}" method="post"
            style="padding:.65rem 0;border-top:1px solid var(--borda)">
        <div class="rg-r1">
          <span class="tdot" style="background:{{ '#25D366' if e.fase!='venda' else '#229ED9' }}"></span>
          <span style="display:flex;align-items:center;gap:.5rem;min-width:0">
            <input class="fld" name="rotulo" value="{{ e.rotulo }}" style="max-width:240px">
            <code class="mut" style="font-size:.68rem">{{ e.chave }}</code>
            {% if e.fixa %}<span class="rg-tag" style="background:var(--card-2);border:1px solid var(--borda);color:var(--txt-mut)">fixa</span>{% endif %}
          </span>
          <span style="display:flex;gap:.3rem">
            <input class="fld" name="prazo_n" value="{{ e.prazo_n }}" style="text-align:right;width:56px"
                   {% if e.chave in ('ganho','perdido') %}disabled placeholder="—"{% endif %}>
            <select class="rg-uni" name="prazo_u" {% if e.chave in ('ganho','perdido') %}disabled{% endif %}>
              {% for u, r in unidades %}<option value="{{ u }}" {% if e.prazo_u==u %}selected{% endif %}>{{ r }}</option>{% endfor %}
            </select>
          </span>
          <span class="num" style="text-align:right;font-size:.95rem;color:{{ 'var(--txt-mut)' if not e.n else 'var(--txt)' }}">{{ e.n }}</span>
        </div>
        <div style="display:flex;align-items:center;gap:.5rem;margin:.45rem 0 0 1.35rem;flex-wrap:wrap">
          <label class="chk" style="display:inline-flex;align-items:center;gap:.35rem;font-size:.74rem;color:var(--txt-mut);cursor:pointer">
            <input type="checkbox" name="gatilho_ativo" value="1" {% if e.gatilho_ativo %}checked{% endif %}
                   style="width:auto;margin:0;accent-color:var(--azul)">
            entra sozinho quando
          </label>
          <select class="rg-sel" name="gatilho" style="flex:1;min-width:260px">
            <option value="">— só na mão —</option>
            {% for ev, rot in eventos %}<option value="{{ ev }}" {% if e.gatilho==ev %}selected{% endif %}>{{ rot }}</option>{% endfor %}
          </select>
          <button class="pbtn ghost" style="padding:.35rem .7rem;font-size:.78rem">Salvar</button>
        </div>
      </form>
      {% endfor %}
      {% endif %}
    {% endfor %}

    <form method="post" action="/painel/prospeccao/etapas/nova" style="display:flex;gap:.5rem;align-items:center;margin-top:1rem;padding-top:.85rem;border-top:1px solid var(--borda);flex-wrap:wrap">
      <input class="fld" name="rotulo" placeholder="Nome da etapa nova" style="max-width:230px">
      <select class="rg-uni" name="fase"><option value="venda">na fase de venda</option><option value="pos">na pós-venda</option></select>
      <button class="pbtn novo">+ Nova etapa</button>
      <span class="mut" style="font-size:.78rem">só remove etapa vazia · as fixas só renomeiam</span>
    </form>
  </div>
</div>
<script>
function rgToast(msg,erro){var t=document.getElementById('rg-toast');
  if(!t){t=document.createElement('div');t.id='rg-toast';
    t.style.cssText='position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--card);border:1px solid var(--verde);color:var(--verde-claro);padding:.6rem 1rem;border-radius:10px;z-index:200;font-size:.85rem;box-shadow:0 6px 20px rgba(0,0,0,.4);transition:opacity .4s';
    document.body.appendChild(t);}
  t.style.borderColor=erro?'var(--coral)':'var(--verde)';t.style.color=erro?'var(--coral)':'var(--verde-claro)';
  t.textContent=msg;t.style.opacity='1';clearTimeout(t._t);t._t=setTimeout(function(){t.style.opacity='0';},2600);}
function rgDia(el){setTimeout(function(){el.classList.toggle('on',el.querySelector('input').checked);},0);}
function rgSalvar(ev){ev.preventDefault();var f=ev.target;
  fetch(f.action,{method:'POST',headers:{'X-Requested-With':'fetch'},body:new FormData(f)})
    .then(function(r){return r.json();}).then(function(d){
      rgToast(d.ok?'Etapa salva ✓':(d.erro||'Não consegui salvar'),!d.ok);})
    .catch(function(){rgToast('Falha de rede',true);});
  return false;}
</script>
{% endblock %}"""

_env.loader.mapping["prospeccao_regua"] = _REGUA_TPL


_RITMO_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<style>
.rt-tile{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:.95rem 1.05rem}
.rt-tile .v{font-family:var(--mono);font-weight:700;font-size:1.9rem;letter-spacing:-.03em;line-height:1.05}
.rt-tile .l{font-size:.83rem;color:var(--txt-mut);margin-top:.25rem}
.rt-tile .d{font-size:.74rem;color:var(--text-faint);margin-top:.15rem;line-height:1.45}
.rt-bar{position:relative;height:10px;border-radius:5px;background:var(--neon-fraco);overflow:hidden}
.rt-bar i{position:absolute;top:0;left:0;height:10px;border-radius:5px;background:var(--verde);display:block}
.rt-usar{padding:.28rem .6rem;border-radius:7px;font-size:.74rem;font-weight:600;border:1px solid var(--neon-borda);
  color:var(--verde-claro);background:var(--neon-fraco);cursor:pointer;white-space:nowrap}
</style>
<div class="pw">
""" + _navbar("regua") + """
  <div style="display:flex;align-items:flex-start;gap:.8rem;flex-wrap:wrap">
    <div style="flex:1;min-width:260px">
      <h2 class="tt">O ritmo real</h2>
      <div class="mut" style="font-size:.84rem;margin-top:.25rem;line-height:1.55;max-width:74ch">
        Medido nos últimos <b style="color:var(--txt)">{{ dias }}</b> dias. São estes números que
        devem escolher os prazos da régua — não o palpite de quem escreveu o código.
      </div>
    </div>
    <a class="pbtn ghost" href="/painel/prospeccao/regua">⏱️ Ajustar a régua</a>
  </div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <div class="egrid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin-top:1.1rem">
    <div class="rt-tile"><div class="v">{{ d.mensagens }}</div>
      <div class="l">mensagens de clientes</div>
      <div class="d">~{{ (d.mensagens / dias)|round(0, 'floor')|int }} por dia</div></div>
    <div class="rt-tile" style="border-color:var(--coral-borda)">
      <div class="v" style="color:var(--coral)">{{ d.mudas }}</div>
      <div class="l">nunca receberam resposta</div>
      <div class="d">some sem ninguém saber</div></div>
    <div class="rt-tile"><div class="v" style="color:var(--azul)">{{ d.saltos_simulados }}</div>
      <div class="l">cards que teriam saltado</div>
      <div class="d">gatilhos em observação</div></div>
    <div class="rt-tile"><div class="v" style="color:var(--ambar)">{{ d.avisos_simulados }}</div>
      <div class="l">cobranças que teriam saído</div>
      <div class="d">com os prazos de agora</div></div>
  </div>

  <div class="fsec" style="margin-top:.9rem">
    <div class="sh"><b>Quanto vocês demoram pra responder</b>
      <span class="mut" style="font-size:.76rem">a barra clara vai até 9 em cada 10 · a cheia é a metade</span></div>
    {% if not d.vendedores %}
      <div class="mut" style="font-size:.85rem;padding:.6rem 0">Sem mensagens no período.</div>
    {% endif %}
    {% for v in d.vendedores %}
    <div style="display:grid;grid-template-columns:190px 1fr 150px;gap:.9rem;align-items:center;padding:.7rem 0;border-top:1px solid var(--borda)">
      <div><div style="font-size:.88rem;font-weight:600">{{ v.nome }}</div>
        <div class="mut num" style="font-size:.72rem">{{ v.n }} respostas</div></div>
      <div class="rt-bar" style="width:{{ v.larg90 }}"><i style="width:{{ v.larg50 }}"></i></div>
      <div style="text-align:right;font-size:.8rem">
        <b class="num" style="color:var(--verde-claro)">{{ v.p50_rot }}</b>
        <span class="mut"> · 9/10 em {{ v.p90_rot }}</span></div>
    </div>
    {% endfor %}
  </div>

  <div class="fsec" style="margin-top:.9rem">
    <div class="sh"><b>Que prazo escolher pra “bola com você”</b>
      <span class="mut" style="font-size:.76rem">quantas viraram cobrança em {{ dias }} dias</span></div>
    <form method="post" action="/painel/prospeccao/regua/config">
      <input type="hidden" name="gatilhos_modo" value="{{ cfg.gatilhos_modo }}">
      <input type="hidden" name="cobranca_modo" value="{{ cfg.cobranca_modo }}">
      {% for c in cortes %}
      <div style="display:grid;grid-template-columns:90px 1fr 170px 96px;gap:.9rem;align-items:center;padding:.6rem 0;border-top:1px solid var(--borda)">
        <span class="num" style="font-size:.95rem">{{ c.rotulo }}</span>
        <span class="rt-bar" style="width:{{ c.larg }}"><i style="width:100%"></i></span>
        <span class="mut" style="font-size:.82rem"><b class="num" style="color:var(--txt)">{{ c.n }}</b> cobranças · {{ c.dia }}/dia</span>
        <span style="text-align:right">
          <button class="rt-usar" name="bola_nossa_n" value="{{ c.rotulo.split(' ')[0] }}">usar este</button>
        </span>
      </div>
      {% endfor %}
      <input type="hidden" name="bola_nossa_u" value="h">
    </form>
    <p class="mut" style="font-size:.79rem;line-height:1.55;margin:.85rem 0 0;max-width:84ch">
      Repare se o número muda pouco de uma linha pra outra: quando muda, a cauda é gente atrasada e o
      prazo importa; quando não muda, é gente esquecida — qualquer prazo da faixa pega o mesmo grupo, então
      vale escolher o que menos incomoda quem está trabalhando bem.
    </p>
  </div>

  <div class="fsec" style="margin-top:.9rem">
    <div class="sh"><b>Quanto tempo um lead fica em cada etapa</b>
      {% if not d.etapas %}<span style="font-size:.76rem;color:var(--ambar)">ainda não dá pra saber</span>{% endif %}</div>
    {% if not d.etapas %}
    <div style="background:var(--ambar-fundo);border:1px solid var(--ambar-borda);border-radius:11px;padding:.85rem .95rem;margin-top:.5rem">
      <p style="margin:0;color:var(--txt-mut);font-size:.85rem;line-height:1.6">
        O sistema nunca guardou quando um card muda de coluna — a pergunta não tinha resposta nem
        olhando pra trás. O histórico começou a ser gravado agora{% if d.desde %}, em
        {{ d.desde.strftime('%d/%m') }}{% endif %}; em duas semanas de uso estes números existem e
        escolhem os prazos das etapas sozinhos.
      </p>
    </div>
    {% endif %}
    {% for e in d.etapas %}
    <div style="display:grid;grid-template-columns:200px 1fr 130px;gap:.9rem;align-items:center;padding:.6rem 0;border-top:1px solid var(--borda)">
      <span style="font-size:.87rem;font-weight:600">{{ e.rotulo }}</span>
      <span class="mut num" style="font-size:.78rem">{{ e.n }} passagem(ns)</span>
      <span style="text-align:right;font-size:.82rem">metade em <b class="num" style="color:var(--txt)">{{ e.dur }}</b></span>
    </div>
    {% endfor %}
  </div>
</div>
{% endblock %}"""

_env.loader.mapping["prospeccao_ritmo"] = _RITMO_TPL
