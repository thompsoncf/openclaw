"""O corpo do e-mail semanal — as três caras, um HTML só.

Separado de `resumo_semanal.py` de propósito: lá moram os números e a regra de
quem recebe; aqui, só como isso vira texto. Trocar uma palavra do e-mail não pode
exigir abrir o módulo que decide se ele é enviado.

O QUE MUDA ENTRE OS TRÊS (decisão do dono em 17/09/2026):

  dono ...... tudo, e a lista por vendedor COM NOME
  gestor .... a mesma semana, com o TOTAL da equipe — sem nomes
  vendedor .. só a carteira dele, sem comparação com colega nenhum

DUAS COISAS DO E-MAIL QUE NÃO SÃO ESCOLHA DE ESTILO:

* **É CLARO, e não no tema escuro da marca.** Gmail, Outlook e o app do iPhone
  reescrevem cor de fundo por conta própria no modo escuro, e um layout desenhado
  sobre #0A0F0C vira texto preto em fundo preto quando o cliente inverte só parte
  dele. Claro com contraste alto sobrevive nos dois modos.
* **Tudo em `style=` na tag.** Cliente de e-mail descarta `<style>` no topo (o
  Gmail corta o `<head>` inteiro). Feio de ler, e é o único jeito que chega.

E NADA DE TABELA LARGA: a primeira versão tinha o "por vendedor" numa tabela de 5
colunas, medida a 390px no Chromium estourava pra 443, e e-mail não rola de lado.
Cada pessoa virou um bloco que quebra sozinho.
"""
from __future__ import annotations

_FUNDO = "background:#ffffff;color:#1b211e;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.6"
_H1 = "font-size:20px;font-weight:700;color:#0f1613;margin:0 0 3px"
_SUB = "color:#6b7c73;font-size:13px;margin:0 0 18px"
_H2 = "font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:#8a9a91;margin:24px 0 8px;font-weight:700"
_CAPA = "background:#f2f7f4;border:1px solid #dbe7e0;border-radius:10px;padding:16px 17px;margin-bottom:18px"
_CTA = "display:inline-block;background:#128C4A;color:#ffffff;text-decoration:none;font-weight:600;border-radius:8px;padding:11px 18px;font-size:14px"
_PE = "margin-top:26px;padding-top:14px;border-top:1px solid #e6ebe8;font-size:12px;color:#8a9a91;line-height:1.6"


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _brl(centavos) -> str:
    v = int(centavos or 0) / 100
    return ("R$ " + f"{v:,.2f}").replace(",", "·").replace(".", ",").replace("·", ".")


def _delta(atual, anterior) -> str:
    """"+15", "−2", ou vazio quando não há base. Sem o par, um número não é
    notícia — "2 propostas" não é bom nem ruim; "2, −2" é uma queda."""
    if anterior is None or atual is None:
        return ""
    d = int(atual) - int(anterior)
    if d == 0:
        return '<span style="color:#96a59c;font-size:12px">=</span>'
    cor = "#1a8c4a" if d > 0 else "#c0392b"
    return f'<span style="color:{cor};font-size:12px">{"+" if d > 0 else "−"}{abs(d)}</span>'


def _linha_funil(rotulo: str, n, larg_pct: float, delta: str = "", cor: str = "#dcebe2") -> str:
    """Uma etapa do caminho. A barra é `width` em % dentro de uma div — não SVG,
    não `<table>`: os dois quebram em cliente de e-mail."""
    larg = max(2.0, min(100.0, larg_pct))
    return (
        '<tr><td style="padding:3px 0">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
        ' style="background:#f7faf8;border-radius:7px"><tr>'
        f'<td width="{larg:.0f}%" style="background:{cor};border-radius:7px;padding:7px 9px;font-size:13px;color:#33403a;white-space:nowrap">{_esc(rotulo)}</td>'
        '<td style="padding:7px 9px;text-align:right;white-space:nowrap">'
        f'<b style="color:#0f1613;font-size:14px">{n}</b> {delta}</td>'
        '</tr></table></td></tr>')


def _item(tag: str, texto: str, urgente: bool = False) -> str:
    cor_f, cor_t = ("#fdf4f3", "#c0392b") if urgente else ("#fdf7ec", "#9a6d10")
    return (
        '<tr><td style="padding:7px 0;border-bottom:1px solid #eef2f0;font-size:13px">'
        f'<span style="background:{cor_f};color:{cor_t};font-size:11px;padding:2px 6px;'
        f'border-radius:5px;margin-right:8px">{_esc(tag)}</span>{texto}</td></tr>')


def _bloco_vendedor(v: dict, vende_data: bool) -> str:
    fundo = "#ffffff"
    borda = "#eef2f0"
    vis = (f'<span style="margin-right:14px">visitas <b>{v["visitas"]}</b></span>'
           if vende_data else "")
    valor = (f'<span style="font-size:12px;color:#8a9a91">{_brl(v["valor"])} assinados</span>'
             if v["valor"] else '<span style="font-size:12px;color:#8a9a91">sem contrato</span>')
    return (
        f'<tr><td style="padding:0 0 6px"><table role="presentation" width="100%" cellpadding="0"'
        f' cellspacing="0" border="0" style="background:{fundo};border:1px solid {borda};border-radius:9px">'
        '<tr><td style="padding:10px 12px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="font-weight:600;color:#0f1613;font-size:14px">{_esc(v["nome"])}</td>'
        f'<td style="text-align:right">{valor}</td></tr></table>'
        '<div style="margin-top:5px;font-size:13px;color:#556">'
        f'<span style="margin-right:14px">entraram <b>{v["entraram"]}</b></span>'
        f'{vis}'
        f'<span style="margin-right:14px">fechou <b>{v["fechou"]}</b></span>'
        f'<span>carteira <b>{v["carteira"]}</b></span>'
        '</div></td></tr></table></td></tr>')


def _tabela(miolo: str) -> str:
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
            f' border="0">{miolo}</table>')


def corpo(dados: dict, tipo: str, *, nome: str = "", empresa: str = "",
          membro_id=None, base_url: str = "https://app.zaq-ia.com") -> str:
    """O HTML do e-mail. `tipo` é 'dono', 'gestor' ou 'vendedor'."""
    if tipo == "vendedor":
        return _corpo_vendedor(dados, nome=nome, membro_id=membro_id, base_url=base_url)
    p, ex = dados["placar"], dados["extras"]
    ant = dados.get("anterior") or {}
    vende_data = dados["vende_data"]

    leads = int(p.get("leads") or 0)
    assin = int(p.get("contratos") or 0)
    valor = int(p.get("contratos_valor") or 0)
    topo = max(1, leads)

    # A CAPA é uma frase, não um número: quem abre no celular lê uma linha e decide
    # se continua. E ela fala do CONTRATO, que é o que "fechar" quer dizer aqui.
    if assin:
        capa_l = f"{assin} contrato{'s' if assin != 1 else ''} assinado{'s' if assin != 1 else ''}, {_brl(valor)}."
        ant_n = int(ant.get("contratos") or 0)
        capa_p = (f"Na semana anterior {'foram' if ant_n != 1 else 'foi'} {ant_n}, "
                  f"{_brl(ant.get('contratos_valor') or 0)}." if ant else "")
        capa_bg = _CAPA
    elif dados["vazia"]:
        capa_l = "Semana sem movimento: nenhum cliente novo, nenhum contrato."
        capa_p = "O que segue abaixo é o que já está dentro e pede decisão."
        capa_bg = _CAPA
    else:
        capa_l = f"Nenhum contrato assinado. {leads} cliente{'s' if leads != 1 else ''} novo{'s' if leads != 1 else ''} na semana."
        capa_p = "O caminho abaixo mostra onde o funil estreitou."
        capa_bg = _CAPA.replace("#f2f7f4", "#fdf4f3").replace("#dbe7e0", "#f0d7d4")

    # O CAMINHO, etapa por etapa. A visita entra só onde ela existe (§6): numa
    # conta de mensalidade "visitou o espaço" não quer dizer nada.
    etapas = [("Entraram", leads, ant.get("leads"), "#dcebe2")]
    if vende_data:
        etapas.append(("Visitaram o espaço", int(p.get("visitas_ok") or 0),
                       ant.get("visitas_ok"), "#dcebe2"))
    etapas += [
        ("Receberam proposta", int(p.get("propostas") or 0), ant.get("propostas"), "#dcebe2"),
        ("Receberam contrato", int(p.get("contratos") or 0), ant.get("contratos"), "#dcebe2"),
        ("Assinaram", assin, ant.get("contratos"), "#c9e7d3"),
    ]
    if ex["sinal_pago"]:
        etapas.append(("Pagaram o sinal", ex["sinal_pago"], None, "#dcebe2"))
    funil = "".join(_linha_funil(r, n, 100.0 * (n or 0) / topo, _delta(n, a), cor)
                    for r, n, a, cor in etapas)

    # O QUE ESTÁ TRAVADO: acumulado, não da semana. É o que pede decisão hoje.
    itens = []
    if vende_data and ex["festa30_sem_contrato"]:
        itens.append(_item("agora", f'<b>{ex["festa30_sem_contrato"]} festas em menos de 30 dias</b> '
                                    "ainda sem contrato. Depois da data não tem segunda chance.", True))
    if p.get("rascunhos"):
        itens.append(_item("agora", f'<b>{int(p["rascunhos"])} propostas em rascunho</b>, nunca '
                                    "enviadas. Trabalho já feito, parado no meio do caminho.", True))
    if p.get("sem_assinar"):
        itens.append(_item("olhar", f'<b>{int(p["sem_assinar"])} contrato(s) enviado(s) e não '
                                    "assinado(s).</b>"))
    if ex["titulos_vencidos"]:
        itens.append(_item("olhar", f'<b>{ex["titulos_vencidos"]} título(s) vencido(s)</b>, '
                                    f'{_brl(ex["titulos_vencidos_valor"])}.'))
    if vende_data and ex["sem_data"]:
        itens.append(_item("olhar", f'<b>{ex["sem_data"]} leads sem data de festa.</b> Sem a data, '
                                    "ninguém sabe o que oferecer nem quando cobrar."))

    # PRÓXIMOS 7 DIAS: o que já está na agenda. É o que faz o resumo virar plano.
    prox = []
    if vende_data and ex["visitas_proximas"]:
        prox.append(_item("agenda", f'<b>{ex["visitas_proximas"]} visita(s) marcada(s)</b> ao espaço.'))
    if vende_data and ex["festas_proximas"]:
        prox.append(_item("agenda", f'<b>{ex["festas_proximas"]} festa(s) acontecem</b> nos próximos 7 dias.'))

    # POR VENDEDOR: com nome só pro dono. O gestor vê o total da equipe — foi a
    # escolha do dono, e o e-mail obedece a ela em vez de deixar no acaso de quem
    # encaminha pra quem.
    vs = ex["por_vendedor"]
    if tipo == "dono":
        equipe = "".join(_bloco_vendedor(v, vende_data) for v in vs if
                         v["entraram"] or v["fechou"] or v["carteira"])
    else:
        tot = {"nome": f"{len(vs)} vendedor{'es' if len(vs) != 1 else ''} ativo{'s' if len(vs) != 1 else ''}",
               "entraram": sum(v["entraram"] for v in vs), "visitas": sum(v["visitas"] for v in vs),
               "fechou": sum(v["fechou"] for v in vs), "valor": sum(v["valor"] for v in vs),
               "carteira": sum(v["carteira"] for v in vs)}
        equipe = _bloco_vendedor(tot, vende_data) if vs else ""

    # O rodapé segue o CADASTRO, e não o tipo: desde a 276 a versão com os nomes
    # também vai pra e-mail de texto (o campo "Seu e-mail"), que não é membro de
    # nada. Dizer "porque é dono da conta" pra quem não tem login é mentira de uma
    # linha só — e é a linha que explica como sair da lista.
    quem = ("Você recebe este resumo toda segunda porque é dono da conta."
            if tipo == "dono" and membro_id else
            "Você recebe este resumo toda segunda porque seu e-mail foi cadastrado "
            "pela empresa. Ele não dá acesso ao painel.")
    nota_ctr = ('<br>"Assinou" é contrato assinado, não etapa do funil.'
                if assin else "")

    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#eef2f0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef2f0">
<tr><td align="center" style="padding:18px 10px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
 style="max-width:600px;width:100%;{_FUNDO};border-radius:12px">
<tr><td style="padding:26px 24px">
  <div style="font-size:12px;color:#7a8a81;border-bottom:1px solid #e6ebe8;padding-bottom:10px;margin-bottom:16px">Zaq · a semana da sua empresa</div>
  <h1 style="{_H1}">A semana {_esc(empresa) and 'da ' + _esc(empresa) or 'da sua empresa'}</h1>
  <p style="{_SUB}">{_esc(dados['rotulo'])} · comparado com a semana anterior</p>

  <div style="{capa_bg}">
    <p style="font-size:16px;font-weight:600;color:#0f1613;margin:0">{_esc(capa_l)}</p>
    <p style="margin:6px 0 0;font-size:13px;color:#556">{_esc(capa_p)}</p>
  </div>

  <div style="{_H2}">O caminho da semana</div>
  {_tabela(funil)}

  {'<div style="' + _H2 + '">O que está travado</div>' + _tabela("".join(itens)) if itens else ''}
  {'<div style="' + _H2 + '">Por vendedor</div>' + _tabela(equipe) if equipe else ''}
  {'<div style="' + _H2 + '">Os próximos 7 dias</div>' + _tabela("".join(prox)) if prox else ''}

  <p style="margin-top:22px"><a href="{base_url}/painel/raio-x" style="{_CTA}">Abrir o Raio-X da semana →</a></p>

  <div style="{_PE}">{quem}
    <br><a href="{base_url}/painel/prospeccao/comunicacao?aba=agente" style="color:#128C4A">Desligar o resumo semanal</a>{nota_ctr}
  </div>
</td></tr></table></td></tr></table></body></html>"""


def _corpo_vendedor(dados: dict, *, nome: str, membro_id, base_url: str) -> str:
    """O e-mail do vendedor — o mais fácil de errar, e o mockup guarda a prova.

    A primeira versão abria com "você recebeu 23 e propôs 0", numa semana em que o
    vendedor tinha fechado R$ 12.500. Chegando toda segunda, isso é cobrança
    automática em cima de uma medição errada.

    Então ele ABRE PELO QUE A PESSOA FEZ, mostra só a carteira que ela controla e
    não traz placar de comparação nem o nome de colega nenhum. A conta "recebeu X,
    fechou Y" continua existindo — ela vai pro e-mail de quem gerencia, que é quem
    faz alguma coisa com ela.
    """
    ex = dados["extras"]
    vende_data = dados["vende_data"]
    eu = next((v for v in ex["por_vendedor"] if v["id"] == membro_id), None) or {}
    primeiro = (nome or "").split(" ")[0] or "você"

    if eu.get("fechou"):
        capa_l = f"Você fechou {eu['fechou']} contrato{'s' if eu['fechou'] != 1 else ''}, {_brl(eu['valor'])}."
        capa_p = "Abaixo, o que ainda está esperando você."
    elif eu.get("entraram"):
        capa_l = f"{eu['entraram']} clientes novos chegaram pra você na semana."
        capa_p = "Abaixo, o que já está na sua mão e pede uma resposta."
    else:
        capa_l = "Semana sem cliente novo na sua fila."
        capa_p = "Semana sem entrada é a semana de trabalhar o que já está dentro."

    kpis = [("chegaram pra você", eu.get("entraram", 0)),
            ("na sua carteira", eu.get("carteira", 0))]
    if vende_data:
        kpis.insert(1, ("visitas no espaço", eu.get("visitas", 0)))
    cels = "".join(
        f'<td width="33%" style="padding:0 4px"><table role="presentation" width="100%" border="0"'
        f' cellpadding="0" cellspacing="0" style="border:1px solid #e6ebe8;border-radius:9px">'
        f'<tr><td style="padding:10px 11px"><div style="font-size:22px;font-weight:700;color:#0f1613">{v}</div>'
        f'<div style="font-size:11px;color:#7a8a81;text-transform:uppercase;letter-spacing:.5px">{_esc(r)}</div>'
        f'</td></tr></table></td>' for r, v in kpis)

    itens = []
    if vende_data and ex["festa30_sem_contrato"]:
        itens.append(_item("agora", f'<b>{ex["festa30_sem_contrato"]} festas em menos de 30 dias</b> '
                                    "sem contrato — é onde o dinheiro está mais perto.", True))
    if ex["titulos_vencidos"]:
        itens.append(_item("olhar", f'<b>{ex["titulos_vencidos"]} título(s) vencido(s)</b> na empresa.'))
    if vende_data and ex["visitas_proximas"]:
        itens.append(_item("agenda", f'<b>{ex["visitas_proximas"]} visita(s) marcada(s)</b> nos próximos 7 dias.'))

    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#eef2f0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef2f0">
<tr><td align="center" style="padding:18px 10px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
 style="max-width:600px;width:100%;{_FUNDO};border-radius:12px">
<tr><td style="padding:26px 24px">
  <div style="font-size:12px;color:#7a8a81;border-bottom:1px solid #e6ebe8;padding-bottom:10px;margin-bottom:16px">Zaq · a sua semana</div>
  <h1 style="{_H1}">Sua semana, {_esc(primeiro)}</h1>
  <p style="{_SUB}">{_esc(dados['rotulo'])}</p>

  <div style="{_CAPA}">
    <p style="font-size:16px;font-weight:600;color:#0f1613;margin:0">{_esc(capa_l)}</p>
    <p style="margin:6px 0 0;font-size:13px;color:#556">{_esc(capa_p)}</p>
  </div>

  <div style="{_H2}">Sua semana</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{cels}</tr></table>

  {'<div style="' + _H2 + '">O que pede você</div>' + _tabela("".join(itens)) if itens else ''}

  <p style="margin-top:22px"><a href="{base_url}/cockpit" style="{_CTA}">Abrir minha Fila →</a></p>

  <div style="{_PE}">Você recebe este resumo toda segunda porque trabalha nesta conta.
    Ele fala só da sua carteira — sem comparação com os colegas.</div>
</td></tr></table></td></tr></table></body></html>"""
