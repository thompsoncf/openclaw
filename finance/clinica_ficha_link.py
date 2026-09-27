"""O link "complete sua ficha": a ficha que nasce no agendamento e o paciente termina no celular.

Desenho aprovado: docs/mockups/clinica_prontuario.html, seções 12 e 13 (ideias 2, 3, 6
e 8). Migração 411. Página pública: /ficha/{token} (web/ficha_publica.py).

  1. Marcou a consulta: a ficha nasce (finance/clinica_pacientes.ligar_evento).
  2. A confirmação do horário leva o link (`linha_da_mensagem`); a véspera lembra dele
     se a ficha estiver incompleta (a mesma mensagem, não uma a mais).
  3. O paciente abre com a data de nascimento e preenche: cadastro (com o CPF, que vai
     na nota), pré-consulta (finance/clinica_preconsulta.py, só o profissional lê) e os
     termos (LGPD e uso de imagem, com o texto exato que ele leu).
  4. A recepção vê na agenda e na lista o que falta (`situacoes`), e o caixa avisa do
     CPF antes de receber.

MENOR DE IDADE: o responsável preenche, assina os termos e é quem tem o CPF da nota. O
prontuário continua sendo da criança.

O link nasce DESLIGADO (`clinica_agenda_config.ficha_link`): a clínica lê os termos e liga.
"""
from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, timedelta, timezone

from finance import clinica_agenda as ca
from finance import clinica_pacientes as cpa
from finance import clinica_preconsulta as cpc

_log = logging.getLogger("clinica.ficha_link")

TENTATIVAS = 5
TRAVA_MIN = 30
MAIORIDADE = 18
COMO_CONHECEU = ("Instagram", "Indicação de alguém", "Google", "Anúncio", "Passei na frente", "Outro")
IMAGEM = (("clinico", "Autorizo as fotos só para o meu tratamento: ficam no prontuário, e só os profissionais "
                      "de saúde da clínica veem."),
          ("divulgacao", "Autorizo as fotos para o meu tratamento e também para divulgação da clínica (redes "
                         "sociais e site). Posso retirar essa autorização quando quiser, pelo WhatsApp."),
          ("nao", "Não autorizo fotos."))
VERSAO_TERMOS = "zaq-2026-09-27"


def _falta_migracao(e: Exception) -> bool:
    return cpa._falta_migracao(e)


# ------------------------------------------------------------------ liga/desliga

def ligado(c, conta_id: int) -> bool:
    try:
        with c.transaction():
            r = c.execute("select ficha_link from clinica_agenda_config where conta_id=%s", (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001 — migração 411 ainda não rodou
        return False
    return bool(r and r[0] == "ligado")


def salvar_ligado(c, conta_id: int, valor: str) -> str | None:
    if valor not in ("off", "ligado"):
        return "Opção inválida."
    if (valor == "ligado") == ligado(c, conta_id):
        return None                  # nada mudou (e a base sem a 411 não quebra a tela)
    try:
        with c.transaction():
            c.execute("""insert into clinica_agenda_config (conta_id, ficha_link) values (%s,%s)
                         on conflict (conta_id) do update set ficha_link=excluded.ficha_link, atualizado_em=now()""",
                      (conta_id, valor))
    except Exception:  # noqa: BLE001
        _log.warning("link da ficha: não deu pra salvar na conta %s", conta_id, exc_info=True)
        return "Não deu pra ligar o link da ficha agora. Tente de novo em alguns minutos."
    return None


# ------------------------------------------------------------------ o token e o link

def token(c, conta_id: int, cliente_id: int) -> str | None:
    """O token da ficha (o mesmo link sempre). Cria na primeira vez."""
    r = c.execute("select ficha_token from clientes where id=%s and dono_id=%s", (cliente_id, conta_id)).fetchone()
    if not r:
        return None
    if r[0]:
        return r[0]
    t = secrets.token_urlsafe(18)
    c.execute("update clientes set ficha_token=%s where id=%s and dono_id=%s and ficha_token is null",
              (t, cliente_id, conta_id))
    r = c.execute("select ficha_token from clientes where id=%s and dono_id=%s", (cliente_id, conta_id)).fetchone()
    return r[0] if r else None


def novo_token(c, conta_id: int, cliente_id: int) -> str | None:
    """Troca o link (mandado pro número errado, por exemplo): o antigo para de abrir."""
    c.execute("""update clientes set ficha_token=%s, ficha_tentativas=0, ficha_travada_ate=null
                  where id=%s and dono_id=%s""", (secrets.token_urlsafe(18), cliente_id, conta_id))
    return token(c, conta_id, cliente_id)


def link(tok: str) -> str:
    from finance.clinica_planos import _app_url
    return f"{_app_url()}/ficha/{tok}"


def por_token(c, tok: str) -> dict | None:
    """De quem é o token. A página é aberta sem login: o token É a chave, e daqui pra
    frente tudo roda com a conta dona (`dono_id`)."""
    if not tok or len(tok) > 64:
        return None
    try:
        with c.transaction():
            r = c.execute(
                """select k.id, k.dono_id, coalesce(p.nome, k.nome), k.aniversario, k.ficha_tentativas,
                          k.ficha_travada_ate, k.prospeccao_id, coalesce(p.celular, k.telefone)
                     from clientes k left join pessoas p on p.id = k.pessoa_id
                    where k.ficha_token=%s and k.ativo and coalesce(k.eh_cliente, true)""", (tok,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    return {"id": r[0], "conta_id": r[1], "nome": r[2] or "", "nascimento": r[3], "tentativas": r[4] or 0,
            "travada_ate": r[5], "lead": r[6], "fone": r[7] or "", "token": tok}


def ler_data(txt: str) -> date | None:
    """'1990-05-17' (o campo de data do celular) ou '17/05/1990'."""
    t = (txt or "").strip()
    try:
        if "/" in t:
            d, m, a = (int(x) for x in t.split("/"))
            return date(a, m, d)
        return date.fromisoformat(t)
    except (ValueError, TypeError):
        return None


def entrar(c, f: dict, nascimento: str, agora: datetime) -> str:
    """'ok' | 'errado' | 'travada'. Cada 5 erros travam o link: 30 minutos, depois 1h,
    2h... (a conta só zera quando a data certa entra). Tudo num UPDATE só: dez tentativas
    em paralelo contam dez, não uma."""
    if f["travada_ate"] and f["travada_ate"] > agora:
        return "travada"
    d = ler_data(nascimento)
    if d and f["nascimento"] and d == f["nascimento"]:
        r = c.execute("""update clientes set ficha_tentativas=0, ficha_travada_ate=null, ficha_aberta_em=now()
                          where id=%s and dono_id=%s and (ficha_travada_ate is null or ficha_travada_ate <= %s)
                          returning id""", (f["id"], f["conta_id"], agora)).fetchone()
        return "ok" if r else "travada"
    r = c.execute(
        """update clientes set ficha_tentativas = ficha_tentativas + 1,
                  ficha_travada_ate = case when (ficha_tentativas + 1) %% %s = 0
                      then %s + make_interval(mins => %s * power(2, least((ficha_tentativas + 1) / %s - 1, 6))::int)
                  end
            where id=%s and dono_id=%s and (ficha_travada_ate is null or ficha_travada_ate <= %s)
            returning ficha_travada_ate""",
        (TENTATIVAS, agora, TRAVA_MIN, TENTATIVAS, f["id"], f["conta_id"], agora)).fetchone()
    return "travada" if (r is None or r[0]) else "errado"


# ------------------------------------------------------------------ o que falta

def _idade(nasc: date | None, hoje: date) -> int | None:
    return cpa._idade(nasc, hoje)


def situacoes(c, conta_id: int, cliente_ids, agora: datetime) -> dict[int, dict]:
    """{cliente_id: {'pct', 'falta', 'completa', 'cpf_ok', 'cadastro_ok', 'pre_ok', 'termos_ok',
    'menor', 'alergia', 'pre_em'}} — em poucas consultas, pra agenda e a lista inteira."""
    ids = sorted({int(x) for x in cliente_ids if x})
    if not ids:
        return {}
    hoje = ca.hoje_br(agora)
    # com o link desligado, pré-consulta e termos não têm por onde chegar: não contam
    pelo_link = ligado(c, conta_id)
    rows = c.execute(
        """select k.id, coalesce(p.nome, k.nome), k.aniversario, k.cidade, p.cpf, rk.id, rp.cpf
             from clientes k
             left join pessoas p on p.id = k.pessoa_id
             left join clientes rk on rk.id = k.responsavel_id and rk.dono_id = k.dono_id and rk.ativo
             left join pessoas rp on rp.id = rk.pessoa_id
            where k.dono_id=%s and k.id = any(%s)""", (conta_id, ids)).fetchall()
    ultimo, termos = {}, {}
    try:
        with c.transaction():
            # a pré-consulta vale pra próxima consulta se veio DEPOIS da última terminar
            # (quem preenche na sala de espera responde pra consulta daquele dia)
            ultimo = dict(c.execute(
                """select cliente_id, max(coalesce(situacao_em, fim, inicio)) from eventos_agenda
                    where conta_id=%s and cliente_id = any(%s) and situacao='finalizado' group by 1""",
                (conta_id, ids)).fetchall())
            for kid, termo in c.execute(
                    """select distinct cliente_id, termo from clinica_termos_aceites
                        where conta_id=%s and cliente_id = any(%s)""", (conta_id, ids)).fetchall():
                termos.setdefault(kid, set()).add(termo)
    except Exception as e:  # noqa: BLE001
        if not _falta_migracao(e):
            raise
    pre = cpc.resumo(c, conta_id, ids)
    out = {}
    for kid, nome, nasc, cid, cpf, resp, resp_cpf in rows:
        idade = _idade(nasc, hoje)
        menor = idade is not None and idade < MAIORIDADE
        cpf_ok = bool(resp and resp_cpf) if menor else bool(cpf)
        pre_ok = cpc.respondida_desde(pre.get(kid), ultimo.get(kid))
        termos_ok = {"lgpd", "imagem"} <= termos.get(kid, set())
        itens = [("nome completo", len((nome or "").split()) >= 2), ("data de nascimento", bool(nasc))]
        if menor:
            itens.append(("responsável", bool(resp)))
        itens += [("CPF do responsável" if menor else "CPF", cpf_ok), ("cidade", bool(cid))]
        if pelo_link:
            itens += [("pré-consulta", pre_ok), ("termos", termos_ok)]
        feitos = sum(1 for _r, ok in itens if ok)
        falta = [r for r, ok in itens if not ok]
        out[kid] = {"pct": round(100 * feitos / len(itens)), "falta": falta, "completa": not falta,
                    "cpf_ok": cpf_ok, "pre_ok": pre_ok, "termos_ok": termos_ok, "menor": menor,
                    "cadastro_ok": not [r for r in falta if r not in ("pré-consulta", "termos")],
                    "alergia": bool(pre.get(kid, {}).get("alergia")),
                    "pre_em": ca.local(pre[kid]["quando"]) if kid in pre else None,
                    # a pré-consulta curta só depois de uma completa respondida
                    "retorno": bool(pre.get(kid, {}).get("tem_completa")), "pelo_link": pelo_link}
    return out


def situacao(c, conta_id: int, cliente_id: int, agora: datetime) -> dict | None:
    return situacoes(c, conta_id, [cliente_id], agora).get(cliente_id)


def falta_txt(s: dict | None) -> str:
    """'ficha completa' | 'ficha 60% · falta CPF e pré-consulta'."""
    if not s:
        return ""
    if s["completa"]:
        return "ficha completa"
    f = s["falta"]
    lista = f[0] if len(f) == 1 else ", ".join(f[:-1]) + " e " + f[-1]
    return f"ficha {s['pct']}% · falta {lista}"


def cliente_do_evento(c, conta_id: int, evento_id: int | None) -> int | None:
    if not evento_id:
        return None
    try:
        with c.transaction():
            r = c.execute("select cliente_id from eventos_agenda where id=%s and conta_id=%s",
                          (evento_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001 — migração 407 ainda não rodou
        return None
    return r[0] if r else None


def dos_eventos(c, conta_id: int, evs: list[dict], agora: datetime) -> None:
    """Põe em cada agendamento da agenda a situação da ficha do paciente (`e['ficha']`)."""
    ids = [e["id"] for e in evs]
    if not ids:
        return
    try:
        with c.transaction():
            kids = dict(c.execute("select id, cliente_id from eventos_agenda where conta_id=%s and id = any(%s)",
                                  (conta_id, ids)).fetchall())
    except Exception:  # noqa: BLE001
        return
    sit = situacoes(c, conta_id, [k for k in kids.values() if k], agora)
    for e in evs:
        e["cliente_id"] = kids.get(e["id"])
        e["ficha"] = sit.get(e["cliente_id"]) if e["cliente_id"] else None
        e["ficha_txt"] = falta_txt(e["ficha"])


def proximo_evento(c, conta_id: int, cliente_id: int, agora: datetime) -> int | None:
    try:
        with c.transaction():
            r = c.execute(
                """select id from eventos_agenda
                    where conta_id=%s and cliente_id=%s and situacao in ('agendado','confirmado')
                      and coalesce(status, '') <> 'cancelado' and inicio >= %s
                    order by inicio limit 1""", (conta_id, cliente_id, agora - timedelta(hours=3))).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return r[0] if r else None


# ------------------------------------------------------------------ a mensagem

def _de_quem(c, conta_id: int, cliente_id: int) -> tuple[str, str]:
    """('sua ficha', 'Sua ficha') ou ('a ficha de Pedro', 'A ficha de Pedro'): a mãe que
    marcou pro filho recebe no WhatsApp dela."""
    r = c.execute("""select coalesce(p.nome, k.nome), k.responsavel_id from clientes k
                      left join pessoas p on p.id = k.pessoa_id where k.id=%s and k.dono_id=%s""",
                  (cliente_id, conta_id)).fetchone()
    if r and r[1]:
        n = cpa._primeiro(r[0]).capitalize() or "paciente"
        return f"a ficha de {n}", f"A ficha de {n}"
    return "sua ficha", "Sua ficha"


def linha_da_mensagem(c, conta_id: int, ev: dict, qual: str, agora: datetime | None = None) -> str:
    """O pedaço do link que vai no fim da confirmação ('marcado') ou da véspera
    ('vespera'). Vazio quando o link está desligado, o agendamento não tem ficha ou a
    ficha já está completa."""
    try:
        if not ligado(c, conta_id):
            return ""
        # num savepoint: um erro aqui nunca deixa quebrada a transação de quem manda a
        # mensagem (o agente, a véspera, a vaga)
        with c.transaction():
            kid = cliente_do_evento(c, conta_id, ev.get("id"))
            if not kid:
                return ""
            s = situacao(c, conta_id, kid, agora or datetime.now(timezone.utc))
            if not s or s["completa"]:
                return ""
            tok = token(c, conta_id, kid)
            if not tok:
                return ""
            minus, maius = _de_quem(c, conta_id, kid)
        if qual == "vespera":
            return f"\n\n📝 {maius} ainda está pela metade: dá pra terminar pelo mesmo link 😊 {link(tok)}"
        return f"\n\n📝 Complete {minus} antes da consulta (leva 3 minutos): {link(tok)}"
    except Exception:  # noqa: BLE001 — a mensagem da agenda nunca cai por causa do link
        _log.warning("link da ficha: falhou pro agendamento %s", ev.get("id"), exc_info=True)
        return ""


def texto_manual(c, conta_id: int, cliente_id: int) -> str | None:
    """A recepção manda o link na mão, pela ficha do paciente."""
    tok = token(c, conta_id, cliente_id)
    if not tok:
        return None
    r = c.execute("select coalesce(p.nome, k.nome), k.responsavel_id from clientes k left join pessoas p "
                  "on p.id = k.pessoa_id where k.id=%s and k.dono_id=%s", (cliente_id, conta_id)).fetchone()
    minus, _m = _de_quem(c, conta_id, cliente_id)
    n = "" if (r and r[1]) else cpa._primeiro(r[0] if r else "").capitalize()
    return (f"Oi{', ' + n if n else ''}! Para agilizar o atendimento, complete {minus} "
            f"(leva 3 minutos, abre com a data de nascimento): {link(tok)}")


# ------------------------------------------------------------------ o que o paciente manda

def _limpo(v, n: int = 120) -> str:
    return " ".join(str(v or "").split())[:n]


def salvar_cadastro(pool, f: dict, form: dict, agora: datetime,
                    verificado: bool = True) -> tuple[str | None, str | None]:
    """Passo 1 do link → (erro, aviso). Nunca apaga: campo vazio não muda nada.

    SEM A DATA CONFERIDA (a ficha ainda não tinha nascimento): quem tem o link só
    PREENCHE o que está vazio — não troca o nome por outro paciente, nem o CPF, a cidade
    ou o endereço que a recepção guardou.

    CPF que já é de outra ficha não trava o paciente: o resto é salvo e a recepção
    confere na chegada (o nome do dono do CPF nunca aparece aqui)."""
    from finance import clientes as cli
    from finance import validadoc
    conta_id, kid = f["conta_id"], f["id"]
    nome = _limpo(form.get("nome"))
    if len(nome.split()) < 2:
        return "Escreva o nome completo.", None
    nasc = ler_data(form.get("nascimento") or "")
    hoje = ca.hoje_br(agora)
    if not nasc or nasc > hoje or nasc.year < 1900:
        return "Data de nascimento inválida.", None
    cidade = _limpo(form.get("cidade"), 80)
    if not cidade:
        return "Informe a cidade.", None
    menor = (_idade(nasc, hoje) or 0) < MAIORIDADE
    cpf = validadoc.so_digitos(form.get("cpf")) or None
    if cpf and not validadoc.valida_cpf(cpf):
        return "CPF inválido.", None
    resp_nome = _limpo(form.get("resp_nome"))
    resp_cpf = validadoc.so_digitos(form.get("resp_cpf")) or None
    if resp_cpf and not validadoc.valida_cpf(resp_cpf):
        return "CPF do responsável inválido.", None
    aviso = None
    with pool.connection() as c:
        cur = c.execute(
            """select k.pessoa_id, k.responsavel_id, k.aniversario, coalesce(p.nome, k.nome), p.cpf,
                      k.cidade, k.uf, k.endereco, k.cep, coalesce(p.email, k.email)
                 from clientes k left join pessoas p on p.id = k.pessoa_id
                where k.id=%s and k.dono_id=%s and k.ativo""", (kid, conta_id)).fetchone()
        if not cur:
            return "Ficha não encontrada.", None
        pessoa_id, resp_id, nasc0, nome0, cpf0, cid0, uf0, end0, cep0, email0 = cur
        if not verificado and nasc0:
            return "Confirme a data de nascimento para continuar.", None
        resp_tem_cpf = False
        if resp_id:
            r = c.execute("select p.cpf from clientes k left join pessoas p on p.id = k.pessoa_id "
                          "where k.id=%s and k.dono_id=%s", (resp_id, conta_id)).fetchone()
            resp_tem_cpf = bool(r and r[0])
        # 1) tudo conferido ANTES de gravar qualquer coisa
        if menor:
            if not resp_id and len(resp_nome.split()) < 2:
                return "Menor de idade: escreva o nome completo do responsável.", None
            if not resp_cpf and not resp_tem_cpf:
                return "Informe o CPF do responsável (vai na nota fiscal).", None
        elif not cpf and not cpf0:
            return "Informe o CPF (vai na nota fiscal).", None
        if cpf and cpf0 and (cpf == cpf0 or not verificado):
            cpf = None                                   # igual, ou não é quem provou a data
        if cpf and cpa.cpf_de_outra_ficha(c, conta_id, pessoa_id, cpf):
            cpf = None
            aviso = "Não deu pra guardar o CPF: ele já está em outra ficha. A clínica confere com você na chegada."
        if resp_cpf and resp_tem_cpf:
            resp_cpf = None                              # o CPF do responsável já guardado não muda por aqui
        # 2) o responsável: só o nome completo igual serve, e nunca outro menor
        if menor and not resp_id:
            resp_id = cpa.achar_ou_criar(c, conta_id, f["lead"], resp_nome, f["fone"], exato=True)
            r = c.execute("select aniversario from clientes where id=%s and dono_id=%s",
                          (resp_id, conta_id)).fetchone()
            if resp_id == kid or (r and r[0] and (_idade(r[0], hoje) or 0) < MAIORIDADE):
                c.rollback()
                return "O responsável precisa ser outra pessoa, maior de idade.", None
            c.execute("update clientes set responsavel_id=%s where id=%s and dono_id=%s", (resp_id, kid, conta_id))
        if resp_cpf:
            rp = c.execute("select pessoa_id from clientes where id=%s and dono_id=%s", (resp_id, conta_id)).fetchone()
            if cpa.cpf_de_outra_ficha(c, conta_id, rp[0] if rp else None, resp_cpf):
                resp_cpf = None
                aviso = ("Não deu pra guardar o CPF do responsável: ele já está em outra ficha. "
                         "A clínica confere com você na chegada.")
        c.commit()
    if verificado:
        campos = {"nome": nome, "aniversario": nasc, "cidade": cidade}
        for k, v in (("uf", _limpo(form.get("uf"), 2).upper()), ("endereco", _limpo(form.get("endereco"), 200)),
                     ("cep", _limpo(form.get("cep"), 9)), ("email", _limpo(form.get("email"), 120))):
            if v:
                campos[k] = v
    else:
        # sem a data conferida: só o que está vazio (e o nome, se for o mesmo paciente)
        campos = {"aniversario": nasc}
        if cpa._mesmo_paciente(nome0 or "", nome):
            campos["nome"] = nome
        for k, v, antes in (("cidade", cidade, cid0), ("uf", _limpo(form.get("uf"), 2).upper(), uf0),
                            ("endereco", _limpo(form.get("endereco"), 200), end0),
                            ("cep", _limpo(form.get("cep"), 9), cep0),
                            ("email", _limpo(form.get("email"), 120), email0)):
            if v and not antes:
                campos[k] = v
    if cpf:
        campos["cpf"] = cpf
    try:
        cli.atualizar_cliente(pool, conta_id, kid, **campos)
        if resp_cpf:
            cli.atualizar_cliente(pool, conta_id, resp_id, cpf=resp_cpf)
    except ValueError as e:
        return ("CPF inválido." if "CPF" in str(e) else str(e)), None
    except Exception as e:  # noqa: BLE001 — o CPF que entrou em outra ficha no meio do caminho
        if "ux_pessoas_cpf" in str(e):
            return None, "Não deu pra guardar o CPF: ele já está em outra ficha. A clínica confere com você na chegada."
        raise
    como = _limpo(form.get("como_conheceu"), 80)
    if como:
        with pool.connection() as c:
            c.execute("update clientes set como_conheceu=%s where id=%s and dono_id=%s and como_conheceu is null",
                      (como, kid, conta_id))
            c.commit()
    return None, aviso


def textos_dos_termos(empresa: str, paciente: str, menor: bool) -> dict:
    """O texto padrão do Zaq. A clínica lê antes de ligar o link."""
    de = f"os dados de {paciente}, de quem sou responsável legal," if menor else "os meus dados"
    lgpd = (f"Autorizo {empresa or 'a clínica'} a guardar e usar {de} pessoais e de saúde (cadastro, respostas "
            "da pré-consulta, prontuário, fotos clínicas e documentos) para o atendimento, para marcar e lembrar "
            "consultas pelo WhatsApp e para emitir nota fiscal e recibos. Os dados de saúde ficam no prontuário, "
            "que só os profissionais de saúde da clínica leem, e são guardados pelo prazo que as normas de saúde "
            "exigem. Posso pedir uma cópia, corrigir os dados ou tirar dúvidas pelo WhatsApp da clínica.")
    imagem = ("Fotos clínicas (antes, durante e depois do tratamento) ajudam o profissional a acompanhar o "
              "resultado. Elas ficam no prontuário, guardadas com sigilo.")
    return {"lgpd": ("Uso de dados (LGPD)", lgpd), "imagem": ("Uso de imagem", imagem)}


def salvar_termos(c, f: dict, form: dict, *, empresa: str, ip: str, user_agent: str, agora: datetime) -> str | None:
    conta_id, kid = f["conta_id"], f["id"]
    if str(form.get("lgpd") or "") != "1":
        return "Para seguir, é preciso aceitar o uso de dados."
    opcao = str(form.get("imagem") or "")
    if opcao not in dict(IMAGEM):
        return "Escolha uma opção sobre as fotos."
    quem = _limpo(form.get("nome"))
    if len(quem.split()) < 2:
        return "Escreva o nome completo de quem está aceitando."
    r = c.execute("select k.aniversario, coalesce(p.nome, k.nome) from clientes k left join pessoas p "
                  "on p.id = k.pessoa_id where k.id=%s and k.dono_id=%s", (kid, conta_id)).fetchone()
    menor = bool(r and r[0] and (_idade(r[0], ca.hoje_br(agora)) or 0) < MAIORIDADE)
    textos = textos_dos_termos(empresa, r[1] if r else "", menor)
    papel = "responsavel" if menor else "paciente"
    for termo, op in (("lgpd", None), ("imagem", opcao)):
        titulo, texto = textos[termo]
        if op:
            texto = texto + "\n\n" + dict(IMAGEM)[op]
        c.execute("""insert into clinica_termos_aceites (conta_id, cliente_id, termo, opcao, titulo, texto, versao,
                                                         aceito_por_nome, papel, ip, user_agent)
                     values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (conta_id, kid, termo, op, titulo, texto, VERSAO_TERMOS, quem, papel, (ip or "")[:60],
                   (user_agent or "")[:300]))
    return None


def termos_aceitos(c, conta_id: int, cliente_id: int) -> list[dict]:
    """O último aceite de cada termo (a ficha mostra; a recepção pode ver)."""
    try:
        with c.transaction():
            rows = c.execute(
                """select distinct on (termo) termo, opcao, titulo, aceito_por_nome, papel, aceito_em
                     from clinica_termos_aceites where conta_id=%s and cliente_id=%s
                    order by termo, aceito_em desc""", (conta_id, cliente_id)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"termo": r[0], "opcao": r[1], "opcao_txt": dict(IMAGEM).get(r[1], "") if r[1] else "",
             "titulo": r[2], "por": r[3], "papel": r[4], "quando": ca.local(r[5])} for r in rows]
