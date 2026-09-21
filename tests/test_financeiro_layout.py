"""A cara da tela do Financeiro — e o que ela não pode perder pelo caminho.

POR QUE EXISTE. A tela ficou ilegível por acúmulo: a quebra por natureza em
`.68rem` (tamanho de nota de rodapé para três números que o dono usa pra
decidir), três tarjas empilhadas empurrando a página, e emoji fazendo o papel de
ícone. O desenho mudou; o CONTEÚDO e as FUNÇÕES, não. Este arquivo guarda as
duas metades disso.

A metade que mais importa é a segunda. Um restyle é feito com `replace` em
string gigante, e é assim que um `onclick` some sem ninguém ver — a tela desenha
bonita e o botão não faz nada.

O ESCOPO `.fin2` TAMBÉM É REGRA. `.metric`, `.aba` e `.barra` são de Relatórios,
Admin e mais seis telas. Engordar `.metric` lá fora pra caber aqui estragaria
todas elas de uma vez, e ninguém ligaria o estrago a esta tela.
"""
from datetime import date

from web.portal import _env


class _L(dict):
    """Linha que responde tanto a `l['x']` quanto a `l.x`, como as do banco."""
    __getattr__ = dict.get


def _lanc(i, tipo, natureza, descricao, categoria, valor):
    return _L(id=i, tipo=tipo, natureza=natureza, descricao=descricao,
              categoria=categoria, valor=valor, data=date(2026, 9, 18),
              origem="ofx", quem="MANOEL", forma_pagamento="credito",
              plano_conta_id=None, centro_custo_id=None)


def _desenha(**over) -> str:
    """A tela renderizada de verdade, com o contexto que a rota entrega.

    Conta PJ com pendências: é o estado em que a tela tem TUDO na tela — as
    pílulas de natureza, os avisos, a quebra por natureza nos cartões e a
    previsão da fatura.
    """
    itens = [_lanc(1, "despesa", "empresa", "POSTO IPIRANGA", "Combustivel", 31200),
             _lanc(2, "receita", "a_definir", "PIX JULIANA", "Outros", 240000),
             _lanc(3, "despesa", "pessoal", "MERCADO LIVRE", "Servicos", 18990)]
    ctx = dict(
        logado=True, titulo="Financeiro", secao_ativa="financeiro",
        caps={"vendas": True, "financeiro": True, "gerir": True}, papel="dono",
        n_contextos=0, versao_app="x", ve_novidades=False, conta=(3, "Prime"),
        tem_cesta=False, tem_pj=True, vende_produto=False, vende_servico=True,
        beta_gratis=True, plano_aviso=None, empresa_nome="Prime",
        resumo=_L(anterior=1840210, saldo=431755, receitas=4198000, despesas=3766245),
        categorias=[("Fornecedores", 3120480), ("Outros", 2785500)],
        maior_cat=3120480, lancamentos=itens,
        dias=[_L(data=date(2026, 9, 18), itens=itens, saldo=189810)],
        raiox={}, pessoas=[(1, "MANOEL"), (2, "THIAGO")],
        meses=[("2026-09", "setembro de 2026"), ("2026-08", "agosto de 2026")],
        mes_sel="2026-09", membro_sel=None, tipo_sel="",
        receitas_cat=[("Vendas", 1801000)], maior_rec=1801000,
        categorias_lista=["Outros"],
        receitas_nao_op=[("Outros", 2397000)], maior_rec_nao_op=2397000,
        total_rec_op=1801000, total_rec_nao_op=2397000,
        q_search="", n_resultados=0,
        natureza_sel="", sem_conta_sel=False, n_sem_conta=3, n_a_definir=14,
        quebra=_L(receitas=_L(empresa=1801000, pessoal=0, a_definir=2397000),
                  despesas=_L(empresa=3120480, pessoal=18990, a_definir=627000)),
        prev_cartao={"total_centavos": 912030, "meses": 6,
                     "pontos": [_L(competencia=date(2026, 10, 1), parcelas=3,
                                   total_centavos=521000)]},
        eh_pj=True, plano_opcoes=[], centros_custo=[],
        categorias_despesa=["Outros", "Fornecedores"],
        categorias_receita=["Vendas", "Outros"],
        canon=lambda c, t: c,
        categorias_de=lambda t: ["Outros", "Fornecedores"],
        forma_pag_label=lambda f: "credito")
    ctx.update(over)
    return _env.get_template("dash").render(**ctx)


# ---------- o que a tela NÃO pode perder ----------

def test_as_funcoes_continuam_penduradas_nos_botoes():
    """Restyle é `replace` em string gigante — é assim que um `onclick` some.

    A tela desenha bonita, o dono clica, e nada acontece."""
    html = _desenha()
    for fn in ("abrirClassificador()", "abrirImportOfx()", "marcarNat(",
               "catMudou(this)", "salvarCat(this)", "apagarLanc(this)",
               "filtrarTipo(this)", "abrirCat(this)", "abrirDep(this)"):
        assert fn in html, f"sumiu do HTML: {fn}"


def test_os_blocos_e_a_ordem_sao_os_de_sempre():
    """O dono aprovou o desenho novo porque NÃO precisa reaprender a tela."""
    html = _desenha()
    ordem = ["Ver lançamentos de", "Despesas por categoria", "Receita do negócio",
             "Previsão da fatura do cartão", "Lançamentos",
             "Raio-x do consumo por departamento"]
    onde = [html.index(t) for t in ordem]
    assert onde == sorted(onde), f"a ordem dos blocos mudou: {ordem}"


def test_os_filtros_continuam_levando_o_mes_e_a_pessoa():
    """Pílula que esquece o `mes` joga o dono pro mês corrente sem avisar."""
    html = _desenha(membro_sel=2, mes_sel="2026-08")
    for natureza in ("pessoal", "empresa", "a_definir"):
        assert f"mes=2026-08&membro=2&natureza={natureza}" in html, natureza


# ---------- a cara nova ----------

def test_o_icone_e_traco_e_desenha_de_verdade():
    """Emoji é desenhado pelo sistema de cada um: o 🏢 do iPhone do dono não é o
    do Android do vendedor. E um SVG escapado vira texto cru na tela."""
    html = _desenha()
    assert "<svg" in html
    assert "&lt;path" not in html and "&lt;svg" not in html, "o SVG saiu escapado"
    for emoji in ("👤", "🏢", "⏳"):
        assert emoji not in html, f"sobrou emoji de natureza: {emoji}"


def test_a_quebra_por_natureza_saiu_do_tamanho_de_rodape():
    """Eram três números em `.68rem` — 10,9px — que o dono usa pra decidir."""
    html = _desenha()
    assert "fin-quebra" in html
    assert "font-size:.68rem;line-height:1.85" not in html


def test_o_aviso_traz_a_acao_dentro_dele():
    html = _desenha()
    # o CSS também fala em `fin-aviso`: o que interessa aqui é a MARCAÇÃO
    assert '<div class="fin-aviso">' in html
    corte = html[html.index('<div class="fin-aviso">'):]
    assert "classificar em lote" in corte[:900], \
        "o botão da ação tem que estar DENTRO do cartão do aviso"


def test_os_alvos_de_toque_batem_o_minimo():
    """44px é o mínimo de iOS e Android — a mesma régua do PR do login."""
    css = _env.loader.mapping["dash"]
    for regra in (".fin-nat a{", ".fin-aviso .bt{", ".fin2 .aba{", ".fin-bt-ofx{"):
        i = css.index(regra)
        assert "min-height:44px" in css[i:i + 400], f"{regra} abaixo do mínimo"
    assert "min-height:48px" in css[css.index(".fin2 .fin-sel{"):][:300]


def test_o_estilo_novo_nao_vaza_pras_outras_telas():
    """`.metric`, `.aba` e `.barra` são de Relatórios, Admin e mais seis telas.

    Engordar `.metric` lá fora pra caber aqui estragaria todas de uma vez — e
    ninguém ligaria o estrago a esta tela."""
    css = _env.loader.mapping["dash"]
    for linha in css.splitlines():
        limpa = linha.strip()
        if not limpa or limpa.startswith(("/*", "*", "{%", "{#")):
            continue
        for compartilhado in (".metric", ".aba{", ".aba ", ".barra", "input[type=search]"):
            if limpa.startswith(compartilhado):
                raise AssertionError(
                    f"regra sem escopo `.fin2` vaza pras outras telas: {limpa[:80]}")
