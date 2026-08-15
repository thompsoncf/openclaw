"""Proposta pública — o link que o vendedor manda pro cliente.

/proposta/<token>  (SEM login): o cliente vê a proposta com a marca da empresa
que vendeu, baixa em PDF (impressão A4 do navegador) e APROVA/ASSINA (nome +
aceite; registra nome, data/hora e IP — assinatura eletrônica simples).

Assinar só marca a proposta como 'aprovada' (o vendedor fecha o contrato depois,
no funil, gerando os títulos a receber). Escopo de leitura por TOKEN, não por
conta — quem tem o link vê aquela proposta e só ela.
"""
import json
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import agenda as ag, icones_servico as ics, servicos_catalogo as scat
from web.portal import _env

router = APIRouter()


def _notificar_assinatura(d: dict, assinante: str) -> None:
    """Avisa a empresa (Telegram + e-mail) que o cliente assinou. Roda em
    background e é tolerante a falha — nunca afeta a resposta pro cliente."""
    try:
        from finance import notificar
        notificar.avisar_proposta_assinada(
            get_pool(), d["conta_id"], d.get("empresa") or d.get("contato") or "cliente",
            assinante, d.get("ano1", ""), d.get("criado_por"))
    except Exception:  # noqa: BLE001
        pass


def _pos_assinatura(d: dict, assinante: str) -> None:
    """Depois que o cliente assina: reserva a data na agenda (evento) e avisa a
    empresa. Roda em background; cada parte falha sozinha, sem derrubar a outra
    nem a resposta que o cliente já recebeu."""
    try:
        _reservar_na_agenda(d)
    except Exception:  # noqa: BLE001
        pass
    _notificar_assinatura(d, assinante)


def _reservar_na_agenda(d: dict, pool=None) -> int | None:
    """Cliente aprovou o orçamento de EVENTO -> a data entra na agenda da empresa.

    É o que a folha promete ("a data é reservada"): o compromisso nasce com
    início e fim reais (festa que encerra às 24 termina 00:00 do dia seguinte —
    ver agenda.janela_evento), o local e o número de convidados. Idempotente:
    orçamento que já tem compromisso não cria outro. Sem data ou sem hora de
    início, não marca nada — a aprovação vale do mesmo jeito.
    """
    if d.get("modo") != "evento" or d.get("evento_agenda_id"):
        return None
    ev = d.get("evento") or {}
    inicio, fim = ag.janela_evento(ev.get("data"), ev.get("inicio"), ev.get("fim"))
    if not inicio:
        return None
    pool = pool or get_pool()
    cliente = (d.get("empresa") or d.get("contato") or "").strip()
    titulo = " — ".join(x for x in [(ev.get("tipo") or "Evento"), cliente] if x)
    conv = ev.get("convidados")
    descricao = " · ".join(x for x in [
        f"Orçamento {d.get('doc_num') or ''}".strip(),
        (f"{conv} convidados" if conv else ""),
        "aprovado pelo cliente",
    ] if x)
    novo = ag.criar_evento(pool, d["conta_id"], titulo, inicio, fim=fim,
                           local=(ev.get("local") or None), descricao=descricao,
                           tipo="empresa")
    with pool.connection() as c:
        c.execute("""update orcamentos set evento_agenda_id=%s
                      where id=%s and evento_agenda_id is null""", (novo["id"], d["id"]))
        c.commit()
    return novo["id"]


def _reais(v) -> str:
    return "R$ " + format(int(v or 0), ",").replace(",", ".")


def _num(centavos) -> str:
    """7.200,00 — número com centavos, sem cifrão. É como as linhas de item são
    escritas num orçamento (o R$ aparece nos totais e nas parcelas)."""
    v = int(centavos or 0) / 100
    return f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _brl(centavos) -> str:
    """R$ com centavos — total, subtotal por categoria e parcelas."""
    return "R$ " + _num(centavos)


_CONECTIVOS = {"de", "da", "do", "das", "dos", "e"}


def _titulo(v: str) -> str:
    """Endereço/bairro/cidade que vieram GRITANDO do cadastro viram Capitalizado.
    Só age quando não há uma minúscula sequer — texto misto é do jeito que a
    empresa escreveu e fica como está."""
    v = (v or "").strip()
    if not v or any(c.islower() for c in v):
        return v
    palavras = []
    for i, w in enumerate(v.split(" ")):
        b = w.lower()
        palavras.append(w if len(w) <= 1 and not w.isalpha() else
                        (b if i and b in _CONECTIVOS else b.capitalize()))
    return " ".join(palavras)


def _doc(v: str) -> str:
    """CNPJ/CPF com máscara. 14 dígitos é CNPJ, 11 é CPF; o resto sai como veio
    (documento estrangeiro, meio digitado, etc.)."""
    d = "".join(ch for ch in (v or "") if ch.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return (v or "").strip()


def _cep(v: str) -> str:
    d = "".join(ch for ch in (v or "") if ch.isdigit())
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else (v or "").strip()


def _fone(v: str) -> str:
    """(86) 98188-5930 / (86) 3221-1234. Com 55 na frente, tira o país."""
    d = "".join(ch for ch in (v or "") if ch.isdigit())
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]
    if len(d) in (10, 11):
        return f"({d[:2]}) {d[2:-4]}-{d[-4:]}"
    return (v or "").strip()


def _contatos(*valores: str) -> list[str]:
    """Os contatos do cliente, sem repetir.

    WhatsApp e telefone quase sempre são O MESMO número — o vendedor preenche os
    dois campos, e o espelho na aba Clientes guarda um deles. A folha juntava a
    lista crua e o cliente lia o próprio número duas vezes.

    A comparação é só por letra/dígito, então "(86) 98188-5930" e "86981885930"
    contam como um só; vence o primeiro da lista, que é o já mascarado.
    """
    vistos: set[str] = set()
    saida: list[str] = []
    for v in valores:
        v = (v or "").strip()
        chave = "".join(ch for ch in v if ch.isalnum()).lower()
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        saida.append(v)
    return saida


def _lista(v) -> list:
    """jsonb que pode voltar como str (bancos/drivers antigos) ou já como lista."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return v if isinstance(v, list) else []


def _dic(v) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for", "")
    if xf:
        return xf.split(",")[0].strip()[:60]
    return (request.client.host if request.client else "")[:60]


def registrar_assinatura(pool, token: str, nome: str, doc: str, ip: str) -> bool:
    """Grava a assinatura (idempotente): só assina se ainda não foi aprovada/fechada.
    Devolve True se assinou agora."""
    nome = (nome or "").strip()
    if not nome:
        return False
    with pool.connection() as c:
        r = c.execute(
            """update orcamentos
                  set aprovada_por=%s, aprovada_doc=%s, aprovada_ip=%s,
                      aprovada_em=now(), status='aprovada', atualizado_em=now()
                where token=%s and status not in ('aprovada','fechado')
              returning id""",
            (nome[:120], (doc or "").strip()[:40] or None, (ip or "")[:60], token)).fetchone()
        c.commit()
    return bool(r)


def _do_cadastro(pool, conta_id: int, cliente_id: int, congelado: tuple) -> tuple:
    """Os dados do cliente como estão HOJE na aba Clientes.

    O orçamento guarda uma cópia do que o vendedor digitou; a aba Clientes é o
    cadastro que o lojista mantém. Enquanto o orçamento não foi assinado, vale o
    cadastro — é lá que se corrige um nome errado. Campo vazio no cadastro NÃO
    apaga o que o orçamento tinha (o lojista pode não ter preenchido tudo): cada
    campo cai no valor congelado.

    Cliente arquivado ou banco fora do ar caem no congelado inteiro — a folha
    tem que abrir de qualquer jeito, é o link que está com o cliente.
    """
    from finance import clientes as cli
    try:
        c = cli.obter_cliente(pool or get_pool(), conta_id, cliente_id)
    except Exception:  # noqa: BLE001
        c = None
    if not c:
        return congelado
    empresa, contato, doc, tel, email, end, cep, cidade, uf = congelado
    novo_doc = c.get("cnpj") or c.get("cpf") or doc
    return ((c.get("nome") or empresa), contato,
            novo_doc, (c.get("telefone") or tel), (c.get("email") or email),
            (c.get("endereco") or end), (c.get("cep") or cep),
            (c.get("cidade") or cidade), (c.get("uf") or uf))


def _carregar(token: str, pool=None):
    with (pool or get_pool()).connection() as c:
        r = c.execute(
            """select o.id, o.empresa, o.cliente, o.whatsapp, o.segmento, o.escopo,
                      o.itens, o.setup_centavos, o.mensal_centavos, o.primeiro_ano_centavos,
                      o.status, o.criado_em, o.aprovada_por, o.aprovada_em, c.nome,
                      c.nome_fantasia,
                      o.conta_id, o.criado_por, c.logo_url,
                      coalesce(o.modo,'recorrente'), o.evento, o.parcelas, o.numero,
                      o.endereco, o.cep, o.cidade, o.uf, o.cnpj, o.email, o.telefone,
                      o.evento_agenda_id,
                      c.razao_social, c.documento, c.endereco, c.cep, c.bairro,
                      c.cidade, c.uf, c.telefone, c.email_empresa,
                      o.cliente_id,
                      -- vendedor: criado_por guarda o id do membro OU 'dono'
                      -- (quem abriu a conta). No segundo caso quem assina é a
                      -- própria conta — era isso que sumia da folha.
                      coalesce((select m.nome from membros m
                                 where m.id = case when o.criado_por ~ '^[0-9]+$'
                                                   then o.criado_por::bigint end),
                               case when o.criado_por = 'dono' then c.nome end)
                 from orcamentos o join contas c on c.id = o.conta_id
                where o.token=%s""", (token,)).fetchone()
    if not r:
        return None
    (oid, empresa, contato, whats, segmento, escopo, itens, setup_c, mensal_c, ano1_c,
     status, criado_em, aprov_por, aprov_em, conta_nome, conta_fantasia,
     conta_id, criado_por, logo_url,
     modo, evento, parcelas, numero, cli_end, cli_cep, cli_cidade, cli_uf, cli_doc,
     cli_email, cli_tel, agenda_id,
     em_razao, em_doc, em_end, em_cep, em_bairro, em_cidade, em_uf, em_tel, em_email,
     cliente_id, vendedor_nome) = r
    # ASSINOU, CONGELOU: enquanto o orçamento não foi aprovado, a aba Clientes é
    # quem manda — corrigiu o nome/endereço lá, reimprimiu, saiu certo, sem
    # precisar refazer a proposta. Depois de assinado fica exatamente o que o
    # cliente aprovou; erro em documento assinado se conserta emitindo outro.
    if cliente_id and (status or "") not in ("aprovada", "fechado"):
        empresa, contato, cli_doc, cli_tel, cli_email, cli_end, cli_cep, cli_cidade, cli_uf = \
            _do_cadastro(pool, conta_id, cliente_id,
                         (empresa, contato, cli_doc, cli_tel, cli_email,
                          cli_end, cli_cep, cli_cidade, cli_uf))
    itens = _lista(itens)
    evento = _dic(evento)
    criado = criado_em.date() if criado_em else date.today()
    # Evento: a validade natural é a data da festa (depois dela o orçamento não
    # serve pra nada). Fora do evento, segue os 15 dias de sempre.
    dia_evento = ag.parse_data(evento.get("data")) if evento else None
    validade = dia_evento if (modo == "evento" and dia_evento) else criado + timedelta(days=15)
    return {
        "id": oid, "empresa": empresa or "Cliente", "contato": contato or "",
        "whats": _fone(whats), "segmento": segmento or "",
        "escopo": escopo or "", "itens": itens,
        "setup": _reais((setup_c or 0) / 100), "mensal": _reais((mensal_c or 0) / 100),
        "ano1": _reais((ano1_c or 0) / 100),
        # evento: `setup_centavos` é a soma dos itens e `primeiro_ano_centavos`
        # é o total COM desconto (é o que a tela calcula e o que as parcelas
        # somam). A folha mostrava o bruto e o desconto sumia.
        "subtotal_itens": _brl(setup_c or 0),
        "total": _brl(ano1_c if (ano1_c or 0) > 0 else (setup_c or 0)),
        "desconto_pct": int((evento or {}).get("desconto") or 0),
        "desconto_valor": _brl(max(0, (setup_c or 0) - (ano1_c or 0))),
        "status": status or "rascunho",
        "criado": criado, "validade": validade,
        "aprovada_por": aprov_por or "", "aprovada_em": aprov_em,
        # Cabeçalho do orçamento de evento: o nome COMERCIAL. `contas.nome` é o
        # nome de quem abriu a conta (vira "MANOEL SOARES" no papel do cliente);
        # o nome fantasia/razão social é o que a empresa vende. Na proposta
        # recorrente nada muda — segue `contas.nome`, como sempre foi.
        "vendedor": ((conta_fantasia or em_razao or conta_nome) if modo == "evento"
                     else conta_nome) or "Proposta",
        "conta_id": conta_id, "criado_por": criado_por, "logo_url": logo_url,
        "modo": modo or "recorrente",
        "evento": evento, "dia_evento": dia_evento,
        "parcelas": _lista(parcelas), "numero": numero,
        "evento_agenda_id": agenda_id,
        # documento, CEP e telefone saem com máscara e o endereço em CAIXA ALTA
        # é capitalizado: o banco guarda o que a empresa digitou, o cliente lê
        # formatado.
        "cliente": {"doc": _doc(cli_doc), "endereco": _titulo(cli_end), "cep": _cep(cli_cep),
                    "cidade": _titulo(cli_cidade), "uf": (cli_uf or "").upper(),
                    "email": cli_email or "", "telefone": _fone(cli_tel)},
        # whatsapp e telefone costumam ser o mesmo número: a folha mostra uma vez
        "contatos": _contatos(_fone(whats), _fone(cli_tel), cli_email or ""),
        "vendedor_nome": vendedor_nome or "",
        "emitente": {"razao": em_razao or "", "doc": _doc(em_doc), "endereco": _titulo(em_end),
                     "cep": _cep(em_cep), "bairro": _titulo(em_bairro),
                     "cidade": _titulo(em_cidade), "uf": (em_uf or "").upper(),
                     "telefone": _fone(em_tel), "email": em_email or ""},
        "doc_num": ("Nº %d" % numero) if numero else
                   "PR-%s-%03d" % (criado.strftime("%Y%m"), (oid or 0) % 1000),
    }


_DIA_SEM = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

# O vocabulário (tipos de evento e de contrato) mora no catálogo de serviços —
# a folha imprime a lista toda com a escolhida em destaque, como no formulário
# de papel que a empresa usa hoje.
TIPOS_EVENTO = scat.TIPOS_EVENTO
TIPOS_CONTRATO = scat.TIPOS_CONTRATO


def _data_br(v) -> str:
    d = ag.parse_data(v)
    return d.strftime("%d/%m/%Y") if d else ""


def _linhas_evento(d: dict) -> list[dict]:
    """Itens do orçamento de evento: quantidade × valor unitário = subtotal.
    `setup` continua sendo o total da linha (é o que o funil e o fechamento
    somam), então ele manda no subtotal; qtd/unitário são a leitura humana."""
    linhas = []
    for i, it in enumerate(d["itens"], 1):
        qtd = int(it.get("qtd") or 1) or 1
        total = int(it.get("setup") or 0)
        unit = int(it.get("unitario") or 0) or (total // qtd if qtd else total)
        # ÍCONE no lugar da foto: serviço não tem embalagem pra fotografar, e a
        # foto sumia na impressão sem fundo. O ícone sai do que o vendedor fixou
        # no catálogo ou, quando não fixou, do nome/categoria do item — nunca
        # falta selo na linha.
        linhas.append({"n": i, "nome": it.get("nome") or "", "desc": it.get("desc") or "",
                       "qtd": qtd, "unit": _num(unit * 100), "subtotal": _num(total * 100),
                       "categoria": it.get("categoria") or "",
                       "icone": ics.svg(ics.escolher(it.get("nome"), it.get("categoria"),
                                                     it.get("icone")), px=24)})
    return linhas


def _subtotais(itens: list[dict]) -> list[dict]:
    """Soma por categoria (Locação de espaço, Buffet, …), na ordem em que os
    itens aparecem. Só faz sentido com mais de uma categoria — com uma só, a
    linha repetiria o total; nesse caso volta vazio e a folha não mostra o bloco."""
    soma: dict[str, int] = {}
    for it in itens:
        cat = (it.get("categoria") or "").strip()
        if not cat:
            return []          # item sem categoria: o agrupamento mentiria no total
        soma[cat] = soma.get(cat, 0) + int(it.get("setup") or 0)
    if len(soma) < 2:
        return []
    return [{"nome": k, "valor": _brl(v * 100)} for k, v in soma.items()]


@router.get("/proposta/{token}", response_class=HTMLResponse)
def proposta_publica(request: Request, token: str, erro: str = ""):
    d = _carregar(token)
    if not d:
        return HTMLResponse(_env.get_template("proposta").render(prop=None), status_code=404)
    if d["modo"] == "evento":
        linhas = _linhas_evento(d)
        d["subtotais"] = _subtotais(d["itens"])
        ev = d["evento"]
        dia = d["dia_evento"]
        d["ev"] = {
            "data": (f"{dia.strftime('%d/%m/%Y')} · {_DIA_SEM[dia.weekday()]}" if dia else ""),
            "convidados": ev.get("convidados") or "",
            "inicio": ev.get("inicio") or "", "fim": ev.get("fim") or "",
            "tipo": ev.get("tipo") or "", "local": ev.get("local") or "",
            "contratos": [str(x) for x in (ev.get("contratos") or [])],
        }
        # as listas inteiras, com a escolhida em destaque — é assim que o papel
        # que ele usa hoje mostra (todas as opções, a marcada com o X).
        d["tem_evento"] = any(str(ev.get(k) or "").strip()
                              for k in ("data", "convidados", "inicio", "fim", "tipo", "local"))
        d["tipos_evento"] = TIPOS_EVENTO
        d["contratos_todos"] = TIPOS_CONTRATO
        d["parcelas_fmt"] = [
            {"venc": _data_br(p.get("venc")), "valor": _brl(p.get("valor_centavos")),
             "forma": p.get("forma") or "", "obs": p.get("obs") or ""}
            for p in d["parcelas"] if int(p.get("valor_centavos") or 0) > 0]
    else:
        linhas = [{"nome": (it.get("nome") or ""), "desc": (it.get("desc") or ""),
                   "setup": _reais(it.get("setup")), "mensal": _reais(it.get("mensal"))}
                  for it in d["itens"]]
    _MES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
            "agosto", "setembro", "outubro", "novembro", "dezembro"]
    d["data_str"] = f"{d['criado'].day} de {_MES[d['criado'].month]} de {d['criado'].year}"
    d["validade_str"] = d["validade"].strftime("%d/%m/%Y")
    d["aprovada_str"] = d["aprovada_em"].strftime("%d/%m/%Y às %H:%M") if d["aprovada_em"] else ""
    d["assinada"] = d["status"] in ("aprovada", "fechado")
    return HTMLResponse(_env.get_template("proposta").render(prop=d, linhas=linhas,
                                                             token=token, erro=erro))


@router.post("/proposta/{token}/assinar")
def proposta_assinar(request: Request, background: BackgroundTasks, token: str,
                     nome: str = Form(""), doc: str = Form(""), aceite: str = Form("")):
    if not (nome or "").strip() or aceite != "on":
        return RedirectResponse(
            f"/proposta/{token}?erro=Preencha+seu+nome+e+marque+o+aceite.", status_code=303)
    if registrar_assinatura(get_pool(), token, nome, doc, _ip(request)):
        d = _carregar(token)
        if d:
            background.add_task(_pos_assinatura, d, nome.strip())
    return RedirectResponse(f"/proposta/{token}", status_code=303)


# ---------------------------------------------------------------- template
_PROPOSTA_TPL = r"""{% if not prop %}
<!doctype html><meta charset=utf-8><title>Proposta</title>
<div style="font-family:system-ui;max-width:520px;margin:12vh auto;text-align:center;color:#14213D">
  <h1 style="font-size:22px">Proposta não encontrada</h1>
  <p style="color:#8A8475">Esse link pode ter expirado ou estar incorreto. Peça um novo à empresa que enviou.</p>
</div>
{% else %}
<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% if prop.modo == 'evento' %}Orçamento {{ prop.doc_num }} · {{ prop.vendedor }} · {{ prop.empresa }}{% else %}Proposta · {{ prop.vendedor }}{% endif %}</title>
{% raw %}<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#14213D;background:#EEE9DD;padding:18px}
.wrapc{max-width:794px;margin:0 auto}
.bar{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.bar .who{font-size:12.5px;color:#5A6678}
.bar .dl{background:#14213D;color:#F4F1EA;border:0;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:600;cursor:pointer}
.pg{background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 20px 50px -25px rgba(20,33,61,.4)}
.hd{background:#14213D;color:#F4F1EA;padding:30px 40px;display:flex;justify-content:space-between;align-items:flex-start;gap:14px}
/* marca+dados de um lado, número do orçamento do outro, na MESMA linha: com
   logo deitada o cabeçalho quebrava e o "Nº" caía embaixo da marca. Só no
   celular é que empilha. */
.hdl{display:flex;align-items:flex-start;gap:12px;flex:1;min-width:0}
.hdl>div{min-width:0;overflow-wrap:anywhere}
.hd .mt{flex:0 0 auto;white-space:nowrap}
@media(max-width:560px){.hd{flex-wrap:wrap}
  /* no celular a marca vai EM CIMA: espremida ao lado, a coluna de texto
     ficava tão estreita que quebrava o CNPJ no meio. */
  .hdl{flex:1 1 100%;flex-wrap:wrap}
  .hdl>div{flex:1 1 100%}
  .lgo img{height:40px;max-width:130px}
  .hd .mt{flex:1 1 100%;white-space:normal}}
.hd .lg{font-size:23px;font-weight:600}.hd .lg span{color:#E0B458}
/* A logo é papel timbrado, não ícone de app: altura fixa e largura LIVRE, pra
   marca deitada (que é a maioria) não ser espremida num quadrado. Na tela o
   cabeçalho é azul-marinho, então ela ganha uma placa branca que abraça o
   formato dela — é o que salva logo escura e PNG sem fundo. No papel o
   cabeçalho já é branco: a placa sai e a marca fica solta, como num timbre. */
.lgo{background:#fff;border-radius:8px;padding:6px 8px;display:inline-flex;
  align-items:center;justify-content:center;flex:0 0 auto}
.lgo img{height:46px;width:auto;max-width:165px;object-fit:contain;display:block}
.hd .sub{font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:#9FA8BC;margin-top:5px}
.hd .mt{text-align:right;font-size:11px;color:#9FA8BC}.hd .mt b{display:block;color:#E0B458;font-size:10px;letter-spacing:.14em;text-transform:uppercase;margin-bottom:4px}
.bd{padding:28px 40px 38px}.eb{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#B8862E;font-weight:600;margin:20px 0 8px}
.cli{background:#FBFAF7;border:1px solid #ECE7DC;border-radius:9px;padding:13px 15px;font-size:14px}
p.es{font-size:13.5px;line-height:1.7;color:#3A4254}
table{width:100%;border-collapse:collapse;margin-top:4px}
th{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:#9FA8BC;background:#14213D;padding:9px 13px;text-align:left}
th.r,td.r{text-align:right;font-family:var(--mono);white-space:nowrap}
td.q{padding-left:6px}
td{padding:11px 13px;border-bottom:1px solid #F0EBE0;font-size:13.5px}
td small{display:block;color:#8A8475;font-size:11.5px;margin-top:4px;line-height:1.65;max-width:52ch}
.tot{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:22px}
.bx{border:1px solid #ECE7DC;border-radius:12px;padding:16px}.bx .l{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#8A8475;font-weight:600}.bx .v{font-family:var(--mono);font-size:21px;font-weight:600;margin-top:6px}
.fin{margin-top:12px;background:#14213D;border-radius:12px;padding:18px 20px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.fin .l{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#E0B458;font-weight:600}.fin .v{font-family:var(--mono);font-size:26px;font-weight:600;color:#FBFAF7}
.sign{margin-top:16px;background:#0e2a1f;border:1px solid #1c5c40;border-radius:12px;padding:20px 22px;color:#eafff5}
.sign h3{font-size:15px;margin-bottom:4px;color:#7EE7B8}
.sign p{font-size:12.5px;color:#a9d9c4;margin-bottom:14px;line-height:1.5}
.sign .row{display:flex;gap:10px;flex-wrap:wrap}
.sign input[type=text]{flex:1;min-width:170px;background:transparent;border:1px solid #2a6b4c;border-radius:8px;color:#fff;padding:10px 12px;font-size:14px}
.sign .ck{display:flex;align-items:flex-start;gap:8px;font-size:12.5px;color:#cdece0;margin:12px 0}
.sign .ck input{margin-top:2px}
.sign .go{background:#12b981;color:#04140d;border:0;border-radius:9px;padding:12px 22px;font-weight:700;font-size:14px;cursor:pointer}
.sign .err{background:#3a1a1a;border:1px solid #7a3a3a;color:#ffd7d7;border-radius:8px;padding:8px 12px;font-size:12.5px;margin-bottom:12px}
.ok{margin-top:16px;background:#0e2a1f;border:1px solid #1c5c40;border-radius:12px;padding:20px 22px;color:#eafff5}
.ok h3{color:#7EE7B8;font-size:16px}.ok p{font-size:13px;color:#a9d9c4;margin-top:4px}
.ft{margin-top:20px;font-size:11px;color:#8A8475;line-height:1.6}
/* ---- modo evento ---- */
.hd .emit{font-size:10px;color:#9FA8BC;line-height:1.6;margin-top:7px}
.evg{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
@media(max-width:560px){.evg{grid-template-columns:repeat(2,1fr)}}
.evb{background:#FBFAF7;border:1px solid #ECE7DC;border-radius:9px;padding:9px 11px}
.evb .l{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:#8A8475;font-weight:600}
.evb .v{font-size:15px;font-weight:700;margin-top:3px}
.evb .v small{font-weight:400;font-size:11px;color:#8A8475}
.pills{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
.pill{font-size:10.5px;font-weight:600;padding:3px 10px;border-radius:20px;border:1px solid #ECE7DC;background:#fff;color:#5A6678}
.pill.on{background:#14213D;border-color:#14213D;color:#E0B458}
.pill.on2{background:rgba(29,158,117,.12);border-color:rgba(29,158,117,.45);color:#0b7a56}
.local{font-size:12px;color:#5A6678;margin-top:9px}
td.n{width:26px;color:#A8A192;vertical-align:top}
/* a coluna do item manda na largura: as colunas de número são fixas e o que
   sobra é da descrição, senão ela vira uma tira estreita e alta. */
table.itens{table-layout:fixed}
table.itens th{font-size:8.5px}
table.itens td{font-size:11.5px;padding:9px 10px}
table.itens td small{font-size:10px;line-height:1.6;max-width:none}
table.itens td.n{color:#14213D}
.item-l{display:flex;gap:10px;align-items:flex-start}
.item-l>div{min-width:0}
/* Selo do item: ÍCONE de traço, não foto. Traço imprime (fundo não), sai igual
   em P&B e nunca falta — metade dos serviços não tem foto pra cadastrar. */
.selo{width:38px;height:38px;flex:0 0 38px;border:1px solid #E6DCC6;border-radius:8px;
  display:inline-flex;align-items:center;justify-content:center;color:#B8862E;
  background:#FDFBF6}
.cat{font-size:8px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  color:#B8862E;display:block;margin-bottom:2px}
.subs{margin-top:10px;border:1px solid #ECE7DC;border-radius:9px;overflow:hidden}
.sub-cat{display:flex;justify-content:space-between;gap:10px;font-size:11.5px;color:#5A6678;
  padding:6px 12px;border-bottom:1px solid #F5F1E8}
.sub-cat:last-child{border-bottom:0}
.sub-cat b{font-family:var(--mono);color:#14213D;white-space:nowrap}
.sub-cat.desconto,.sub-cat.desconto b{color:#0b7a56}
table.pag th{background:#FBFAF7;color:#8A8475;border-bottom:1px solid #ECE7DC}
td.q{text-align:right;font-family:var(--mono);white-space:nowrap;vertical-align:top}
.cond{background:#FBFAF7;border:1px solid #ECE7DC;border-radius:9px;padding:12px 14px;font-size:12px;color:#5A6678;line-height:1.7;white-space:pre-line}
/* ---- celular: a tabela vira LISTA ----
   Cinco colunas em 390px sobrava uns 90px pro item: cada palavra caía numa
   linha e a descrição virava uma coluna de letras. No celular cada item vira um
   bloco — nome e descrição na largura toda, e os números embaixo com o rótulo
   junto (o cabeçalho da tabela some, então o número tem que se apresentar).
   Só na TELA: no papel a folha é A4 e a tabela continua tabela. */
@media(max-width:560px){
  table.itens,table.itens tbody,table.itens tr,table.itens td{display:block;width:auto}
  table.itens tr:first-child{display:none}          /* cabeçalho da tabela */
  table.itens tr{padding:11px 0;border-bottom:1px solid #F0EBE0}
  table.itens td{border:0;padding:0}
  table.itens td.n{display:none}                    /* o número da linha não paga a largura */
  table.itens td small{max-width:none;font-size:11.5px}
  table.itens td.q{display:inline-block;margin:7px 14px 0 0;font-size:12.5px;
    font-weight:600;color:#14213D;text-align:left}
  table.itens td.q::before{content:attr(data-r) " ";font-weight:400;font-size:10px;
    letter-spacing:.06em;text-transform:uppercase;color:#8A8475}
  table.pag,table.pag tbody,table.pag tr,table.pag td{display:block;width:auto}
  table.pag tr:first-child{display:none}
  table.pag tr{padding:9px 0;border-bottom:1px solid #F0EBE0}
  table.pag td{border:0;padding:1px 0;font-size:12.5px;text-align:left}
  table.pag td:empty{display:none}
  table.pag td::before{content:attr(data-r) " ";font-size:10px;letter-spacing:.06em;
    text-transform:uppercase;color:#8A8475}
}
.assp{display:none}
@media print{
  body{background:#fff;padding:0}
  .bar,.sign,.ft{display:none!important}
  .pg{box-shadow:none;border-radius:0}
  .assp{display:block!important;margin:16px 22px 0;text-align:center;font-size:10px;color:#555}
  .assp .ln{border-top:1px solid #333;width:65%;margin:0 auto 4px}
  /* já assinado: sai o carimbo eletrônico no lugar da linha à caneta, e a
     caixa verde de "aprovado" não vira uma segunda página em branco. */
  .assp.carimbo{border-top:1px solid #ECE7DC;padding-top:8px;color:#0b7a56;margin-top:14px}
  .ok{display:none!important}
  /* ---- papel não tem fundo ----
     O navegador imprime com "gráficos de plano de fundo" DESLIGADO por padrão:
     o cabeçalho azul-marinho vira papel branco com letra clara (ilegível), o
     total some e as tarjas de cabeçalho de tabela desaparecem. Então, no papel,
     tudo o que era FUNDO vira TRAÇO + letra escura — sai igual em jato de tinta,
     laser e fotocópia P&B, com ou sem a opção ligada. */
  .hd{background:#fff!important;color:#14213D;border-bottom:2px solid #14213D;
      padding:9px 22px 8px}
  .hd .sub,.hd .emit,.hd .mt{color:#5A6678}
  .hd .lg{font-size:19px}
  .lgo{background:none!important;padding:0;border-radius:0}
  .lgo img{height:50px;max-width:150px}
  .hd .emit{font-size:9.5px;line-height:1.45;margin-top:5px}
  .hd .mt b{color:#14213D}
  .hd .mt span{color:#5A6678!important}
  th{background:#fff!important;color:#14213D;border-bottom:1.2px solid #14213D;padding:5px 9px}
  table.pag th{border-bottom:1.2px solid #14213D}
  .fin{background:#fff!important;border:1.5px solid #14213D;padding:7px 14px;margin-top:8px}
  .fin .l{color:#14213D}
  .fin .v{color:#14213D;font-size:19px}
  .pill.on{background:#fff!important;border-color:#14213D;color:#14213D}
  .pill.on2{background:#fff!important;border-color:#0b7a56;color:#0b7a56}
  .cli,.evb,.cond,.subs,.bx{background:#fff!important}
  /* aperta o respiro da tela pra caber a folha inteira em UMA página — um
     orçamento de festa que vira duas folhas é a assinatura sozinha na página 2. */
  .bd{padding:10px 22px 12px}
  .eb{margin:7px 0 3px}
  td{padding:4px 9px}
  table.itens td{font-size:10.5px;padding:4px 8px}
  table.itens td small{font-size:9px;line-height:1.42;margin-top:2px}
  .selo{width:30px;height:30px;flex:0 0 30px;background:none!important}
  .selo svg{width:20px;height:20px}
  .evb{padding:5px 9px}.evb .v{font-size:12.5px}
  .cli{padding:7px 12px;font-size:12px}
  .cond{padding:7px 12px;font-size:10px;line-height:1.5}
  .sub-cat{padding:3px 12px;font-size:10.5px}
  .pills{margin-top:5px}.pill{font-size:9.5px;padding:2px 9px}
  .local{margin-top:5px;font-size:10.5px}
  /* …e o que quebrar, quebra ENTRE blocos: item cortado no meio da página é o
     que faz um orçamento parecer amador. */
  tr,.evg,.evb,.subs,.cond,.cli{break-inside:avoid}
  @page{size:A4;margin:11mm}
}
</style>{% endraw %}
</head><body><div class="wrapc">
  <div class="bar">
    <span class="who">🔒 {{ 'Orçamento enviado' if prop.modo == 'evento' else 'Proposta enviada' }} por <b>{{ prop.vendedor }}</b> · válido até {{ prop.validade_str }}</span>
    <button class="dl" onclick="window.print()">⬇ Baixar PDF</button>
  </div>

  <div class="pg">
    {% set evento = prop.modo == 'evento' %}
    <div class="hd">
      <div class="hdl">{% if prop.logo_url %}<span class="lgo"><img src="{{ prop.logo_url }}" alt=""></span>{% endif %}<div>
        <div class="lg">{{ prop.vendedor }} <span>·</span></div>
        <div class="sub">{{ 'Orçamento de evento' if evento else 'Proposta comercial' }}</div>
        {% if evento %}<div class="emit">
          {%- if prop.emitente.razao and prop.emitente.razao != prop.vendedor %}{{ prop.emitente.razao }}{% endif %}
          {%- if prop.emitente.doc %}{% if prop.emitente.razao and prop.emitente.razao != prop.vendedor %} · {% endif %}CNPJ {{ prop.emitente.doc }}{% endif %}
          {%- if prop.emitente.endereco %}<br>{{ prop.emitente.endereco }}
            {%- if prop.emitente.bairro %} · {{ prop.emitente.bairro }}{% endif %}
            {%- if prop.emitente.cidade %} · {{ prop.emitente.cidade }}{% if prop.emitente.uf %}/{{ prop.emitente.uf }}{% endif %}{% endif %}
            {%- if prop.emitente.cep %} · CEP {{ prop.emitente.cep }}{% endif %}{% endif %}
          {%- if prop.emitente.telefone or prop.emitente.email %}<br>
            {{- prop.emitente.telefone }}{% if prop.emitente.telefone and prop.emitente.email %} · {% endif %}{{ prop.emitente.email }}{% endif %}
          {%- if prop.vendedor_nome %}<br>Vendedor: {{ prop.vendedor_nome }}{% endif %}
        </div>{% endif %}
      </div></div>
      <div class="mt"><b>{{ 'Orçamento' if evento else 'Proposta comercial' }}</b>{{ prop.doc_num }}<br>
        {%- if evento %}Emitido em {{ prop.criado.strftime('%d/%m/%Y') }}<br><span style="color:#E0B458">Válido até {{ prop.validade_str }}</span>
        {%- else %}{{ prop.data_str }}{% endif %}</div>
    </div>
    <div class="bd">
      {% if evento and prop.tem_evento %}
      <div class="eb">O evento</div>
      <div class="evg">
        <div class="evb"><div class="l">Data</div><div class="v">{{ prop.ev.data or '—' }}</div></div>
        <div class="evb"><div class="l">Convidados</div><div class="v">{{ prop.ev.convidados or '—' }}</div></div>
        <div class="evb"><div class="l">Início</div><div class="v">{{ prop.ev.inicio or '—' }}</div></div>
        <div class="evb"><div class="l">Encerramento</div><div class="v">{{ prop.ev.fim or '—' }}</div></div>
      </div>
      {# o cliente lê o que ELE contratou, não o cardápio de opções da empresa #}
      {% if prop.ev.tipo or prop.ev.contratos %}
      <div class="pills">
        {% if prop.ev.tipo %}<span class="pill on">{{ prop.ev.tipo }}</span>{% endif %}
        {% for ct in prop.ev.contratos %}<span class="pill on2">{{ ct }}</span>{% endfor %}
      </div>{% endif %}
      {% if prop.ev.local %}<div class="local">📍 {{ prop.ev.local }}</div>{% endif %}
      {% endif %}
      <div class="eb">{{ 'Dados do cliente' if evento else 'Preparada para' }}</div>
      {% if evento %}
      <div class="cli"><b>{{ prop.empresa }}</b>{% if prop.cliente.doc %} · {{ prop.cliente.doc }}{% endif %}
        {%- if prop.contato and prop.contato != prop.empresa %}<br><span style="color:#A8A192">A/C {{ prop.contato }}</span>{% endif %}
        {%- if prop.cliente.endereco or prop.cliente.cidade %}<br>{{ prop.cliente.endereco }}
          {%- if prop.cliente.cidade %}{% if prop.cliente.endereco %} · {% endif %}{{ prop.cliente.cidade }}{% if prop.cliente.uf %}/{{ prop.cliente.uf }}{% endif %}{% endif %}
          {%- if prop.cliente.cep %} · CEP {{ prop.cliente.cep }}{% endif %}{% endif %}
        {%- if prop.contatos %}<br>{{ prop.contatos|join(' · ') }}{% endif %}</div>
      {% if linhas %}
      <div class="eb">Itens do orçamento</div>
      <table class="itens"><tr><th style="width:24px">#</th><th>Item</th><th class="r" style="width:40px">Qtd</th>
                 <th class="r" style="width:76px">Vr. unit.</th><th class="r" style="width:84px">Subtotal</th></tr>
        {% for l in linhas %}<tr><td class="n">{{ l.n }}</td>
          <td><div class="item-l">
            <span class="selo">{{ l.icone|safe }}</span>
            <div style="min-width:0">
              {%- if l.categoria %}<span class="cat">{{ l.categoria }}</span>{% endif %}
              <b>{{ l.nome }}</b>{% if l.desc %}<small>{{ l.desc }}</small>{% endif %}
            </div>
          </div></td>
          {# data-r é o rótulo que o CELULAR mostra: lá a tabela vira lista e a
             linha de cabeçalho some, então cada número precisa se apresentar. #}
          <td class="q" data-r="Qtd">{{ l.qtd }}</td>
          <td class="q" data-r="Vr. unit.">{{ l.unit }}</td>
          <td class="q" data-r="Subtotal">{{ l.subtotal }}</td></tr>{% endfor %}
      </table>
      {% endif %}
      {% if prop.subtotais or prop.desconto_pct %}
      <div class="subs">
        {% for st in prop.subtotais %}<div class="sub-cat"><span>{{ st.nome }}</span><b>{{ st.valor }}</b></div>{% endfor %}
        {% if prop.desconto_pct %}
        <div class="sub-cat"><span>Subtotal dos itens</span><b>{{ prop.subtotal_itens }}</b></div>
        <div class="sub-cat desconto"><span>Desconto ({{ prop.desconto_pct }}%)</span><b>− {{ prop.desconto_valor }}</b></div>
        {% endif %}
      </div>{% endif %}
      <div class="fin"><div class="l">Total do evento</div><div class="v">{{ prop.total }}</div></div>
      {% if prop.parcelas_fmt %}
      <div class="eb">Plano de pagamento</div>
      <table class="pag"><tr><th>Vencimento</th><th class="r">Valor</th><th>Forma</th><th>Observação</th></tr>
        {% for p in prop.parcelas_fmt %}<tr><td data-r="Vencimento">{{ p.venc or '—' }}</td>
          <td class="q" data-r="Valor">{{ p.valor }}</td>
          <td data-r="Forma">{{ p.forma }}</td><td data-r="Obs">{{ p.obs }}</td></tr>{% endfor %}
      </table>
      {% endif %}
      {% if prop.escopo %}<div class="eb">Condições</div><div class="cond">{{ prop.escopo }}</div>{% endif %}
      {% else %}
      <div class="cli"><b>{{ prop.empresa }}</b>{% if prop.contato %} · {{ prop.contato }}{% endif %}{% if prop.whats %} · {{ prop.whats }}{% endif %}{% if prop.segmento %}<br><span style="color:#A8A192">Segmento: {{ prop.segmento }}</span>{% endif %}</div>
      {% if prop.escopo %}<div class="eb">Escopo da solução</div><p class="es">{{ prop.escopo }}</p>{% endif %}
      {% if linhas %}
      <div class="eb">Serviços contratados</div>
      <table><tr><th>Serviço</th><th class="r">Setup</th><th class="r">Mensal</th></tr>
        {% for l in linhas %}<tr><td><b>{{ l.nome }}</b>{% if l.desc %}<small>{{ l.desc }}</small>{% endif %}</td><td class="r">{{ l.setup }}</td><td class="r">{{ l.mensal }}</td></tr>{% endfor %}
      </table>
      {% endif %}
      <div class="tot">
        <div class="bx"><div class="l">Investimento inicial</div><div class="v">{{ prop.setup }}</div></div>
        <div class="bx"><div class="l">Mensalidade</div><div class="v">{{ prop.mensal }}</div></div>
      </div>
      <div class="fin"><div class="l">Total estimado · 1º ano</div><div class="v">{{ prop.ano1 }}</div></div>
      {% endif %}
    </div>
    {% if evento %}
      {% if prop.assinada and prop.aprovada_por %}
      <div class="assp carimbo">✓ Aprovado eletronicamente por <b>{{ prop.aprovada_por }}</b>{% if prop.aprovada_str %} em {{ prop.aprovada_str }}{% endif %} · assinatura registrada com nome, data/hora e IP</div>
      {% else %}
      <div class="assp"><div class="ln"></div>Assinatura do cliente</div>
      {% endif %}
    {% endif %}
  </div>

  {% if prop.assinada %}
  <div class="ok">
    {% if prop.aprovada_por %}
    <h3>✓ {{ 'Orçamento aprovado' if evento else 'Proposta aprovada' }}</h3>
    <p>Aprovada por <b>{{ prop.aprovada_por }}</b>{% if prop.aprovada_str %} em {{ prop.aprovada_str }}{% endif %}. A empresa foi notificada{% if evento and prop.ev.data %} e a data de <b>{{ prop.ev.data.split(' ·')[0] }}</b> está reservada{% endif %} — ela dará os próximos passos.</p>
    {% else %}
    <h3>✓ Proposta fechada</h3>
    <p>Esta proposta já foi contratada. Fale com a empresa para os próximos passos.</p>
    {% endif %}
  </div>
  {% else %}
  <form class="sign" method="post" action="/proposta/{{ token }}/assinar">
    <h3>✍️ Aprovar e assinar</h3>
    {% if evento %}
    <p>Ao aprovar, você aceita os valores e as condições acima. Fica registrado com nome, CPF, data/hora e IP{% if prop.ev.data %} — e a data de <b>{{ prop.ev.data.split(' ·')[0] }}</b> é reservada na agenda da {{ prop.vendedor }}{% endif %}.</p>
    {% if erro %}<div class="err">{{ erro }}</div>{% endif %}
    <div class="row">
      <input type="text" name="nome" placeholder="Seu nome completo" required>
      <input type="text" name="doc" placeholder="CPF">
    </div>
    {# Caixinha de aceite: o cliente marca antes de aprovar. É a prova de que
       leu — vale mais do que o clique sozinho se um dia a assinatura for
       questionada. #}
    <label class="ck"><input type="checkbox" name="aceite"> Li e concordo com os termos e valores deste orçamento.</label>
    <button class="go" type="submit">✓ Aprovar e reservar a data</button>
    {% else %}
    <p>Ao aprovar, você aceita esta proposta e autoriza o início. Fica registrado com seu nome, data/hora e IP.</p>
    {% if erro %}<div class="err">{{ erro }}</div>{% endif %}
    <div class="row">
      <input type="text" name="nome" placeholder="Seu nome completo" required>
      <input type="text" name="doc" placeholder="CPF (opcional)">
    </div>
    <label class="ck"><input type="checkbox" name="aceite"> Li e concordo com os termos e valores desta proposta.</label>
    <button class="go" type="submit">✓ Aprovar e assinar proposta</button>
    {% endif %}
  </form>
  {% endif %}

  <div class="ft">Valores em reais (BRL). Assinatura eletrônica registrada com nome, data/hora e IP — validade jurídica conforme MP 2.200-2/2001. {{ 'Orçamento válido' if evento else 'Proposta válida' }} até {{ prop.validade_str }}.</div>
</div></body></html>
{% endif %}"""

_env.loader.mapping["proposta"] = _PROPOSTA_TPL
