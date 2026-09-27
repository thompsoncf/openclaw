"""Os pacientes da clínica: a lista, a ficha e o paciente separado do card do WhatsApp.

Desenho aprovado: docs/mockups/clinica_prontuario.html, seções 11.1 (a lista), 11.2 (a
ficha) e 13 (o paciente separado do card, o menor com responsável, a cidade). Migração
407. Tela: /painel/clinica/pacientes (web/painel_clinica_pacientes.py).

O PACIENTE mora na ficha de clientes do Zaq (`clientes`, com a identidade — nome,
celular, CPF — em `pessoas`). O card do WhatsApp (`prospeccao`) é por onde ele chegou,
e UM número pode ter VÁRIOS pacientes: a mãe que marca pro filho gera a ficha do filho.
Por isso a ficha do paciente NÃO usa o `criar_cliente` comum, que reaproveita quem tem
o mesmo telefone: aqui só reaproveita quando, além do card ou do celular, o PRIMEIRO
NOME bate (a mesma regra que o pacote e a assinatura já usavam).

O AGENDAMENTO aponta pro paciente (`eventos_agenda.cliente_id`), ligado ao marcar
(`ligar_evento`, chamado por `clinica_agenda.agendar`).

O Zaq não lê prontuário aqui: a ficha mostra cadastro, agenda, tratamento, financeiro,
produtos e a conversa. O conteúdo clínico é outra parte (e só o profissional vê).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from finance import clinica_agenda as ca

_log = logging.getLogger("clinica.pacientes")

#: os filtros da lista (seção 11.1 do mockup), na ordem da tela
FILTROS = (("todos", "Todos"), ("novos", "Novos contatos"), ("com_horario", "Com horário"),
           ("em_tratamento", "Em tratamento"), ("retorno_vencido", "Retorno vencido"),
           ("assinantes", "Assinantes"), ("inativos", "Sem vir há 6 meses"))
INATIVO_DIAS = 180


def _falta_migracao(e: Exception) -> bool:
    s = str(e)
    return "does not exist" in s and ("prospeccao_id" in s or "cliente_id" in s or "responsavel_id" in s
                                      or "como_conheceu" in s)


def _primeiro(nome: str | None) -> str:
    from finance.clinica_pacotes import _primeiro as p
    return p(nome)


def _sem_acento(t: str | None) -> str:
    """'Lúcia' e 'lucia' são a mesma busca: a recepção digita sem acento."""
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", (t or "").lower()) if not unicodedata.combining(ch))


def _idade(nasc: date | None, hoje: date) -> int | None:
    if not nasc or nasc.year < 1900 or nasc > hoje:
        return None
    return hoje.year - nasc.year - ((hoje.month, hoje.day) < (nasc.month, nasc.day))


# ------------------------------------------------------------------ o paciente de um agendamento

def _celular(fone: str | None) -> str:
    """Só dígitos, com o 55 (o formato de `pessoas.celular` no resto do Zaq)."""
    dig = ca._digitos(fone)
    return ("55" + dig) if len(dig) in (10, 11) else dig


def _mesmo_paciente(a: str, b: str) -> bool:
    """O NOME COMPLETO bate (sem acento e sem espaço sobrando); o primeiro nome só
    basta quando um dos dois é uma palavra só ("Ana" x "Ana Clara"). Assim "Ana Clara"
    e "Ana Beatriz", irmãs no mesmo WhatsApp, são duas fichas."""
    na, nb = _sem_acento(" ".join(a.split())), _sem_acento(" ".join(b.split()))
    if na == nb:
        return True
    if len(na.split()) == 1 or len(nb.split()) == 1:
        return na.split()[:1] == nb.split()[:1]
    return False


def achar_ou_criar(c, conta_id: int, lead: int | None, nome: str, fone: str,
                   origem: str | None = None) -> int | None:
    """A ficha do paciente pra este card/celular e este NOME (`_mesmo_paciente`).
    Reaproveita a que tem o mesmo card (ou o mesmo celular) e o mesmo paciente; senão
    cria uma nova, ligada ao card. Na transação de quem chama, sem commit. Uma de cada
    vez por número: duas marcações juntas do mesmo paciente novo não viram duas fichas."""
    nome = " ".join((nome or "").split())[:120]
    if not nome:
        return None
    dig = ca._digitos(fone)
    c.execute("select pg_advisory_xact_lock(%s, hashtext(%s))",
              (771169, f"{conta_id}:{lead or ''}:{dig[-8:]}"))
    rows = c.execute(
        r"""select k.id, coalesce(p.nome, k.nome), k.prospeccao_id
              from clientes k left join pessoas p on p.id = k.pessoa_id
             where k.dono_id=%s and k.ativo and coalesce(k.eh_cliente, true)
               and (k.prospeccao_id = %s
                    or (length(%s) >= 8 and right(regexp_replace(coalesce(p.celular, k.telefone, ''), '\D', '', 'g'), 8) = %s))
             order by (k.prospeccao_id = %s) desc nulls last, k.id
             limit 30""",
        (conta_id, lead, dig, dig[-8:], lead)).fetchall()
    for kid, knome, kprosp in rows:
        if _mesmo_paciente(knome or "", nome):
            if lead and not kprosp:
                c.execute("update clientes set prospeccao_id=%s, atualizado_em=now() where id=%s and dono_id=%s",
                          (lead, kid, conta_id))
            return kid
    celular = _celular(fone) or None
    pid = c.execute("insert into pessoas (nome, celular) values (%s, %s) returning id",
                    (nome, celular)).fetchone()[0]
    return c.execute(
        """insert into clientes (dono_id, nome, telefone, pessoa_id, prospeccao_id, eh_cliente, como_conheceu)
           values (%s,%s,%s,%s,%s,true,%s) returning id""",
        (conta_id, nome, celular, pid, lead, (origem or "").strip()[:80] or None)).fetchone()[0]


def ficha_do_paciente(pool, conta_id: int, lead: int | None, nome: str, fone: str) -> int | None:
    """A mesma ficha, pra quem está fora da transação da agenda (o plano aceito, a
    mensalidade da assinatura, a venda de produto): o título e a venda caem na ficha
    DO PACIENTE, e não na primeira ficha do telefone (a da mãe). None: quem chama segue
    do jeito antigo."""
    try:
        with pool.connection() as c:
            kid = achar_ou_criar(c, conta_id, lead, nome, fone)
            c.commit()
            return kid
    except Exception as e:  # noqa: BLE001
        if not _falta_migracao(e):
            _log.warning("pacientes: ficha não resolvida (conta %s)", conta_id, exc_info=True)
        return None


def ligar_evento(c, conta_id: int, evento_id: int, lead: int | None, nome: str, fone: str,
                 origem: str | None = None, cliente_id: int | None = None) -> int | None:
    """Liga o agendamento ao paciente. `cliente_id` (a recepção marcou pela ficha) manda;
    senão, acha ou cria. Tolerante: sem a 407, o agendamento segue."""
    try:
        with c.transaction():
            kid = None
            if cliente_id and c.execute("select 1 from clientes where id=%s and dono_id=%s and ativo",
                                        (cliente_id, conta_id)).fetchone():
                kid = cliente_id
            kid = kid or achar_ou_criar(c, conta_id, lead, nome, fone, origem)
            if kid:
                c.execute("update eventos_agenda set cliente_id=%s where id=%s and conta_id=%s",
                          (kid, evento_id, conta_id))
            return kid
    except Exception as e:  # noqa: BLE001
        if not _falta_migracao(e):
            _log.warning("pacientes: agendamento %s sem paciente", evento_id, exc_info=True)
        return None


# ------------------------------------------------------------------ a lista

def _conjuntos(c, conta_id: int, hoje: date) -> dict:
    """Quem está em tratamento, é assinante ou tem retorno vencido — por (card, primeiro
    nome), a chave que pacote e assinatura usam, e por paciente no retorno."""
    out = {"pacote": set(), "assinante": set(), "retorno": set()}

    def _t(sql, args):
        try:
            with c.transaction():
                return c.execute(sql, args).fetchall()
        except Exception:  # noqa: BLE001 — sem a migração da fase, o selo não aparece
            return []
    for lead, nome in _t("""select prospeccao_id, paciente_nome from clinica_pacotes
                             where conta_id=%s and estado='ativo'""", (conta_id,)):
        out["pacote"].add((lead, _primeiro(nome)))
    for lead, nome in _t("""select prospeccao_id, paciente_nome from clinica_planos
                             where conta_id=%s and status='aceito'""", (conta_id,)):
        out["pacote"].add((lead, _primeiro(nome)))
    for lead, nome in _t("""select prospeccao_id, paciente_nome from clinica_assinantes
                             where conta_id=%s and estado='ativa'""", (conta_id,)):
        out["assinante"].add((lead, _primeiro(nome)))
    for (kid,) in _t("""select e.cliente_id from clinica_retornos r
                          join eventos_agenda e on e.id = r.evento_id and e.conta_id = r.conta_id
                         where r.conta_id=%s and r.estado='aguardando' and r.vence_em < %s
                           and e.cliente_id is not null""", (conta_id, hoje)):
        out["retorno"].add(kid)
    return out


def listar(c, conta_id: int, agora: datetime, *, filtro: str = "todos", busca: str = "",
           cidade: str = "", limite: int = 300) -> dict:
    """{'pacientes': [...], 'contagem': {filtro: n}, 'cidades': [...]}. Os pacientes com
    ficha, e os NOVOS CONTATOS: quem escreveu no WhatsApp e ainda não tem ficha nem
    atendimento (o antigo "lead")."""
    hoje = ca.hoje_br(agora)
    try:
        with c.transaction():
            rows = c.execute(
                """select k.id, coalesce(p.nome, k.nome), coalesce(p.celular, k.telefone), k.cidade, k.aniversario,
                          k.prospeccao_id, p.cpf,
                          (select max(e.inicio) from eventos_agenda e
                            where e.conta_id = k.dono_id and e.cliente_id = k.id and e.situacao = 'finalizado'),
                          (select min(e.inicio) from eventos_agenda e
                            where e.conta_id = k.dono_id and e.cliente_id = k.id
                              and e.situacao in ('agendado','confirmado') and coalesce(e.status, '') <> 'cancelado'
                              and e.inicio >= %s),
                          (select count(*) from eventos_agenda e
                            where e.conta_id = k.dono_id and e.cliente_id = k.id and e.situacao = 'finalizado'),
                          r.nome
                     from clientes k
                     left join pessoas p on p.id = k.pessoa_id
                     left join clientes rk on rk.id = k.responsavel_id and rk.dono_id = k.dono_id
                     left join pessoas r on r.id = rk.pessoa_id
                    where k.dono_id=%s and k.ativo and coalesce(k.eh_cliente, true)""",
                (agora, conta_id)).fetchall()
    except Exception as e:  # noqa: BLE001
        if _falta_migracao(e):
            return {"pacientes": [], "contagem": {k: 0 for k, _r in FILTROS}, "cidades": []}
        raise
    conj = _conjuntos(c, conta_id, hoje)
    pacientes = []
    for (kid, nome, fone, cid, nasc, lead, cpf, ultimo, proximo, n_atend, resp) in rows:
        chave = (lead, _primeiro(nome))
        pac = {"tipo": "paciente", "id": kid, "nome": nome or "Paciente", "fone": fone or "", "cidade": cid or "",
               "idade": _idade(nasc, hoje), "lead": lead, "cpf": bool(cpf),
               "ultimo": ca.local(ultimo).date() if ultimo else None,
               "proximo": ca.local(proximo) if proximo else None, "atendimentos": int(n_atend or 0),
               "responsavel": resp, "em_tratamento": chave in conj["pacote"],
               "assinante": chave in conj["assinante"], "retorno_vencido": kid in conj["retorno"]}
        pac["inativo"] = bool(pac["ultimo"] and pac["ultimo"] < hoje - timedelta(days=INATIVO_DIAS)
                              and not pac["proximo"])
        pac["novo"] = not pac["atendimentos"] and not pac["proximo"]
        pacientes.append(pac)
    # os novos contatos: o card sem ficha nenhuma
    try:
        with c.transaction():
            contatos = c.execute(
                r"""select p.id, coalesce(nullif(p.contato,''), nullif(p.empresa,''), ''),
                          coalesce(nullif(p.whatsapp,''), p.telefone, ''), coalesce(p.cidade, ''),
                          (select max(m.criado_em) from conversas cv join mensagens m on m.conversa_id = cv.id
                            where cv.prospeccao_id = p.id and cv.conta_id = p.conta_id and m.direcao = 'in')
                     from prospeccao p
                    where p.conta_id=%s and coalesce(p.status,'') <> 'perdido'
                      and coalesce(p.estagio, 'lead') = 'lead'
                      and exists (select 1 from conversas cv join mensagens m on m.conversa_id = cv.id
                                   where cv.prospeccao_id = p.id and cv.conta_id = p.conta_id and m.direcao = 'in')
                      and not exists (select 1 from clientes k where k.dono_id = p.conta_id and k.prospeccao_id = p.id)
                      and not exists (select 1 from clientes k left join pessoas pe on pe.id = k.pessoa_id
                                       where k.dono_id = p.conta_id and k.ativo
                                         and length(regexp_replace(coalesce(nullif(p.whatsapp,''), p.telefone, ''), '\D', '', 'g')) >= 8
                                         and right(regexp_replace(coalesce(pe.celular, k.telefone, ''), '\D', '', 'g'), 8)
                                           = right(regexp_replace(coalesce(nullif(p.whatsapp,''), p.telefone, ''), '\D', '', 'g'), 8))""",
                (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001 — `prospeccao.cidade` pode não existir numa base velha
        contatos = []
    for lead, nome, fone, cid, ult_msg in contatos:
        pacientes.append({"tipo": "contato", "id": None, "nome": nome or "Novo contato", "fone": fone, "cidade": cid,
                          "idade": None, "lead": lead, "cpf": False, "ultimo": None, "proximo": None,
                          "atendimentos": 0, "responsavel": None, "em_tratamento": False, "assinante": False,
                          "retorno_vencido": False, "inativo": False, "novo": True,
                          "ultima_msg": ca.local(ult_msg) if ult_msg else None,
                          "_msg_ts": ult_msg.timestamp() if ult_msg else 0.0})

    def passa(p, f):
        return {"todos": True, "novos": p["novo"], "com_horario": bool(p["proximo"]),
                "em_tratamento": p["em_tratamento"], "retorno_vencido": p["retorno_vencido"],
                "assinantes": p["assinante"], "inativos": p["inativo"]}[f]
    contagem = {k: sum(1 for p in pacientes if passa(p, k)) for k, _r in FILTROS}
    cidades = sorted({p["cidade"].strip() for p in pacientes if p["cidade"].strip()}, key=str.lower)
    f = filtro if filtro in dict(FILTROS) else "todos"
    sel = [p for p in pacientes if passa(p, f)]
    b = _sem_acento(" ".join((busca or "").split()))
    if b:
        dig = ca._digitos(b)
        sel = [p for p in sel if b in _sem_acento(p["nome"])
               or (len(dig) >= 4 and dig in ca._digitos(p["fone"]))]
    if cidade:
        sel = [p for p in sel if (p["cidade"] or "").strip().lower() == cidade.strip().lower()]
    # quem tem horário primeiro, depois quem veio por último; novos contatos pela última mensagem
    sel.sort(key=lambda p: (p["proximo"] is None, p["proximo"] or datetime.max.replace(tzinfo=timezone.utc),
                            -(p["ultimo"] or date.min).toordinal(),
                            -p.get("_msg_ts", 0.0), (p["nome"] or "").lower()))
    return {"pacientes": sel[:limite], "total": len(sel), "contagem": contagem, "cidades": cidades}


# ------------------------------------------------------------------ a ficha

def ficha(c, conta_id: int, cliente_id: int, agora: datetime) -> dict | None:
    """Tudo do paciente, em abas: cadastro, agenda, tratamento, financeiro, produtos e a
    conversa. Nada clínico."""
    hoje = ca.hoje_br(agora)
    r = c.execute(
        """select k.id, coalesce(p.nome, k.nome), coalesce(p.celular, k.telefone), coalesce(p.email, k.email),
                  k.aniversario, k.cidade, k.uf, k.endereco, k.cep, p.cpf, k.prospeccao_id, k.responsavel_id,
                  k.como_conheceu, k.criado_em, k.obs
             from clientes k left join pessoas p on p.id = k.pessoa_id
            where k.id=%s and k.dono_id=%s and k.ativo and coalesce(k.eh_cliente, true)""",
        (cliente_id, conta_id)).fetchone()
    if not r:
        return None
    (kid, nome, fone, email, nasc, cid, uf, end, cep, cpf, lead, resp_id, como, criado, obs) = r
    d = {"id": kid, "nome": nome, "fone": fone or "", "email": email or "", "nascimento": nasc,
         "idade": _idade(nasc, hoje), "cidade": cid or "", "uf": uf or "", "endereco": end or "", "cep": cep or "",
         "cpf": cpf or "", "lead": lead, "responsavel_id": resp_id, "como_conheceu": como or "",
         "desde": ca.local(criado).date() if criado else None, "obs": obs or ""}
    d["responsavel"] = None
    if resp_id:
        rr = c.execute("""select k.id, coalesce(p.nome, k.nome) from clientes k left join pessoas p on p.id = k.pessoa_id
                           where k.id=%s and k.dono_id=%s""", (resp_id, conta_id)).fetchone()
        d["responsavel"] = {"id": rr[0], "nome": rr[1]} if rr else None
    # quem mais usa o mesmo card (a mãe e os filhos)
    d["mesmo_card"] = [{"id": x[0], "nome": x[1]} for x in c.execute(
        """select k.id, coalesce(p.nome, k.nome) from clientes k left join pessoas p on p.id = k.pessoa_id
            where k.dono_id=%s and k.ativo and k.prospeccao_id=%s and k.id <> %s order by k.id""",
        (conta_id, lead, kid)).fetchall()] if lead else []
    d["agenda"] = [{"id": e[0], "quando": ca.local(e[1]), "tipo": e[2] or "Atendimento", "prof": e[3] or "",
                    "situacao": ca.SIT_D.get(e[4], e[4])}
                   for e in c.execute(
                       """select e.id, e.inicio, s.nome, pr.nome,
                                 case when e.status = 'cancelado' then 'cancelou' else e.situacao end
                            from eventos_agenda e
                            left join servicos_catalogo s on s.id = e.servico_id and s.conta_id = e.conta_id
                            left join clinica_profissionais pr on pr.id = e.profissional_id and pr.conta_id = e.conta_id
                           where e.conta_id=%s and e.cliente_id=%s order by e.inicio desc limit 50""",
                       (conta_id, kid)).fetchall()]
    d["proximo"] = next((a for a in sorted(d["agenda"], key=lambda a: a["quando"])
                         if a["quando"] >= ca.local(agora) and a["situacao"] in (ca.SIT_D.get("agendado"),
                                                                                  ca.SIT_D.get("confirmado"))), None)
    quem = _primeiro(nome)

    def _t(sql, args):
        try:
            with c.transaction():
                return c.execute(sql, args).fetchall()
        except Exception:  # noqa: BLE001
            return []
    d["planos"] = [{"id": x[0], "status": x[1], "total": x[2], "criado": ca.local(x[3]).date() if x[3] else None}
                   for x in _t("""select id, status, total_centavos, criado_em, paciente_nome from clinica_planos
                                   where conta_id=%s and prospeccao_id=%s order by id desc""", (conta_id, lead))
                   if _primeiro(x[4]) == quem] if lead else []
    d["pacotes"] = [{"id": x[0], "nome": x[1], "usadas": x[2], "total": x[3], "estado": x[4]}
                    for x in _t("""select id, nome, sessoes_usadas, sessoes_total, estado, paciente_nome
                                     from clinica_pacotes where conta_id=%s and prospeccao_id=%s order by id desc""",
                                (conta_id, lead)) if _primeiro(x[5]) == quem] if lead else []
    d["assinatura"] = next(({"plano": x[0], "desde": x[1]} for x in _t(
        """select p.nome, a.inicio, a.paciente_nome from clinica_assinantes a
             join clinica_assinatura_planos p on p.id = a.plano_id and p.conta_id = a.conta_id
            where a.conta_id=%s and a.prospeccao_id=%s and a.estado='ativa'""", (conta_id, lead))
        if _primeiro(x[2]) == quem), None) if lead else None
    d["titulos"] = [{"id": x[0], "descricao": x[1], "valor": x[2], "vencimento": x[3], "status": x[4]}
                    for x in _t("""select id, descricao, valor_centavos, vencimento, status from titulos
                                    where conta_id=%s and cliente_id=%s and tipo='receber'
                                    order by vencimento desc limit 30""", (conta_id, kid))]
    d["atrasado"] = sum(t["valor"] for t in d["titulos"] if t["status"] == "aberto" and t["vencimento"]
                        and t["vencimento"] < hoje)
    d["produtos"] = [{"nome": x[0], "quando": ca.local(x[1]).date(), "recompra": x[2]}
                     for x in _t("""select pr.nome, v.criado_em, v.recompra_em, v.paciente_nome
                                      from clinica_produto_vendas v
                                      join catalogo_produtos pr on pr.id = v.produto_id and pr.fornecedor_id = v.conta_id
                                     where v.conta_id=%s and v.prospeccao_id=%s order by v.id desc limit 20""",
                                 (conta_id, lead)) if _primeiro(x[3]) == quem] if lead else []
    conv = c.execute("select id from conversas where conta_id=%s and prospeccao_id=%s order by id desc limit 1",
                     (conta_id, lead)).fetchone() if lead else None
    d["conversa_id"] = conv[0] if conv else None
    # o que falta na ficha (seção 12 do mockup: a ficha que nasce no agendamento)
    d["falta"] = [x for x, ok in (("data de nascimento", bool(nasc)), ("CPF", bool(cpf)), ("cidade", bool(cid)))
                  if not ok]
    return d


def salvar_cadastro(pool, conta_id: int, cliente_id: int, form: dict) -> str | None:
    """Cadastro da ficha: identidade (nome, celular, e-mail, CPF) pelo módulo de clientes;
    o resto (nascimento, cidade, endereço, responsável, como conheceu) aqui."""
    from finance import clientes as cli
    nome = " ".join((form.get("nome") or "").split())
    if not nome:
        return "Informe o nome do paciente."
    nasc = None
    if (form.get("nascimento") or "").strip():
        try:
            nasc = date.fromisoformat(form["nascimento"].strip())
        except ValueError:
            return "Data de nascimento inválida."
        if nasc > date.today() or nasc.year < 1900:
            return "Data de nascimento inválida."
    resp = form.get("responsavel_id")
    resp = int(resp) if str(resp or "").isdigit() and int(resp) != cliente_id else None
    from finance import validadoc
    cpf = validadoc.so_digitos(form.get("cpf")) or None
    with pool.connection() as c:
        meu = c.execute("select pessoa_id from clientes where id=%s and dono_id=%s and ativo and coalesce(eh_cliente, true)",
                        (cliente_id, conta_id)).fetchone()
        if not meu:
            return "Paciente não encontrado."
        if resp and not c.execute("select 1 from clientes where id=%s and dono_id=%s and ativo",
                                  (resp, conta_id)).fetchone():
            return "Responsável não encontrado."
        if cpf:
            outro = c.execute(
                """select coalesce(k.nome, p.nome) from pessoas p
                     left join clientes k on k.pessoa_id = p.id and k.dono_id = %s and k.ativo
                    where p.cpf = %s and p.id <> coalesce(%s, 0) limit 1""", (conta_id, cpf, meu[0])).fetchone()
            if outro:
                return (f"Este CPF já está na ficha de {outro[0]}." if outro[0]
                        else "Este CPF já está cadastrado em outra ficha do Zaq.")
    try:
        cli.atualizar_cliente(pool, conta_id, cliente_id, nome=nome, telefone=form.get("fone") or None,
                              email=form.get("email") or None, cpf=form.get("cpf") or None,
                              aniversario=nasc, cidade=(form.get("cidade") or "").strip() or None,
                              uf=((form.get("uf") or "").strip().upper()[:2]) or None,
                              endereco=(form.get("endereco") or "").strip() or None,
                              cep=(form.get("cep") or "").strip() or None)
    except ValueError as e:
        return "CPF inválido." if "CPF" in str(e) else str(e)
    except Exception as e:  # noqa: BLE001 — o CPF que entrou em outra ficha no meio do caminho
        if "ux_pessoas_cpf" in str(e):
            return "Este CPF já está cadastrado em outra ficha do Zaq."
        raise
    with pool.connection() as c:
        c.execute("""update clientes set responsavel_id=%s, como_conheceu=%s, atualizado_em=now()
                      where id=%s and dono_id=%s""",
                  (resp, (form.get("como_conheceu") or "").strip()[:80] or None, cliente_id, conta_id))
        c.commit()
    return None


def novo(pool, conta_id: int, form: dict) -> tuple[int | None, str | None]:
    """Paciente cadastrado na mão (sem agendamento): nome e celular."""
    nome = " ".join((form.get("nome") or "").split())
    if not nome:
        return None, "Informe o nome do paciente."
    if len(ca._digitos(form.get("fone"))) < 10:
        return None, "Informe o celular com DDD."
    with pool.connection() as c:
        lead, _n, fone, erro = ca._lead_do_paciente(c, conta_id, None, nome, form.get("fone") or "")
        if erro:
            c.rollback()
            return None, erro
        kid = achar_ou_criar(c, conta_id, lead, nome, fone)
        c.commit()
    return kid, None
