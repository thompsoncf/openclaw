"""O JS que o NAVEGADOR recebe, nas páginas de `web/portal.py`, tem que compilar.

MESMO RISCO já fechado em tests/test_painel_js_sintaxe.py, e o motivo de existir
um arquivo igual aqui em vez de estender aquele: os templates deste módulo vivem
numa string Python COMUM também (`_CLIENTES = \"\"\"...\"\"\"`, `_EMPRESA =
\"\"\"...\"\"\"`, sem `r`) — o mesmo padrão `_TPL = \"\"\"..\"\"\"` que já
mordeu a aba Canais em 17/08: um `\\n` escrito dentro de uma string JS vira
newline LITERAL no HTML servido, corta a string no meio, e o `<script>` inteiro
morre sem avisar. `node --check` no .py fonte passa — o erro só existe depois
do render, que é o único jeito confiável de pegar.

As páginas 'clientes' e 'empresa' ganharam JS novo em 20/08 (troca do rótulo
Cliente/Fornecedor no formulário de títulos, filtro de papel na base) — é
exatamente o tipo de mudança que este teste existe pra proteger.

O contexto não é decoração: os `{% if %}` do menu lateral (caps/tem_pj/
vende_produto) e do card de Contrato/DRE (dre.mes etc.) precisam de valores DE
VERDADE, não Undefined — Undefined explode em comparação (`n_contextos > 1`),
e um contexto raso demais deixaria justamente o bloco que interessa escondido,
passando o teste sem ter olhado pra nada.
"""
from __future__ import annotations

import inspect
import re
import shutil
import subprocess

import pytest

from web import portal as pt

_CAPS = {"vendas": True, "financeiro": True, "gerir": True}
_BASE_CTX = dict(
    logado=True, papel="dono", caps=_CAPS, tem_pj=True, vende_produto=False,
    vende_servico=True, n_contextos=1, ve_novidades=False, novidades_n=0,
    embed=False, versao_app="1", tem_cesta=False,
)
_CONTA = (1, "pf", "Empresa Teste", None, "pj_padrao", "ativa", None, 50, 5,
          None, False, False, None, False, None)
_DRE = {"mes": 8, "ano": 2026, "estrutura": {"linhas": []}, "receitas_centavos": 0,
        "despesas_centavos": 0, "resultado_centavos": 0, "margem_pct": 0,
        "a_definir_n": 0, "a_definir_centavos": 0}

PAGINAS = {
    "clientes": dict(secao_ativa="clientes", clientes=[
        {"id": 1, "nome": "Ana", "tipo": "pf", "eh_cliente": True, "eh_fornecedor": False,
         "telefone": "86999999999", "documento_fmt": "111.111.111-11", "email": "",
         "aniversario": "", "cidade": "", "uf": "", "endereco": "", "cep": "", "obs": ""},
    ], total=1, busca="", papel_filtro=""),
    "empresa": dict(secao_ativa="empresa", tem_pj=True,
        clientes_lista=[{"nome": "Ana"}], fornecedores_lista=[{"nome": "Rio Poti"}],
        titulos=[], titulos_pagos=[],
        folha={"itens": [], "total_a_pagar_centavos": 0, "total_fgts_centavos": 0,
              "custo_real_total_centavos": 0},
        carteira={}, rotulo_receber="Fiado", dre=_DRE, plano_arvore=[], centros=[],
        dre_centro={"centros": [], "linhas": []}, a_classificar=[], plano_opcoes=[],
        centros_ativos=[], empresa_nome="X", empresa_doc=""),
}


def _render(nome: str) -> str:
    ctx = dict(_BASE_CTX)
    ctx.update(PAGINAS[nome])
    return pt._env.get_template(nome).render(conta=_CONTA, **ctx)


def _scripts(html: str) -> list[str]:
    return [b for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S) if b.strip()]


@pytest.mark.parametrize("pagina", sorted(PAGINAS))
def test_todo_script_da_pagina_compila(pagina, tmp_path):
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    html = _render(pagina)
    blocos = _scripts(html)
    assert blocos, f"{pagina}: nenhum <script> encontrado — o render mudou de forma?"
    erros = []
    for i, bloco in enumerate(blocos):
        alvo = tmp_path / f"{pagina}_{i}.js"
        alvo.write_text(bloco, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(alvo)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            erros.append(f"bloco {i}:\n{r.stderr.strip()[:600]}")
    assert not erros, (
        f"{pagina}: {len(erros)} bloco(s) de <script> não compilam no navegador.\n"
        + "\n\n".join(erros))


def test_a_troca_de_rotulo_do_titulo_esta_de_verdade_na_pagina():
    """Guarda do teste de sintaxe acima: se `titTipoTroca` sumir do render (um
    {% if %} errado escondendo o bloco, por exemplo), o teste de compilação
    passaria sem ter olhado pra nada — porque não haveria o que quebrar."""
    html = _render("empresa")
    js = "\n".join(_scripts(html))
    assert "titTipoTroca" in js
    assert "tit-forn-dl" in html and "tit-cli-dl" in html


def test_a_pagina_de_clientes_desenha_o_papel():
    html = _render("clientes")
    assert "eh_cliente" in html and "eh_fornecedor" in html
    assert "Clientes/Fornecedores" in html


# ---------------------------------------------------- layout: Relatórios (24/08)
# Relato em produção depois de Orçamentos/Contratos entrarem em Relatórios: com
# 8 abas (Vendas, Contas a pagar/receber, Contas pagas/recebidas, Comissão,
# Orçamentos, Contratos) o trilho de abas quebrava em 2 linhas, e o KPI "Em
# aberto" (valor tipo "10 · R$ 68.590,00", mais longo que o KPI comum do app)
# estourava a caixa em telas médias.

def test_trilho_de_abas_nunca_quebra_linha_rola_de_lado():
    fonte = inspect.getsource(pt)
    regra_abas = fonte.split(".abas{")[1].split("}")[0]
    assert "flex-wrap:nowrap" in regra_abas, (
        "com flex-wrap:wrap, muitas abas (caso de Relatórios) quebram em 2ª linha")
    assert "overflow-x:auto" in regra_abas, "sem isso não tem como rolar pra ver as abas escondidas"
    regra_aba = fonte.split(".aba{")[1].split("}")[0]
    assert "flex-shrink:0" in regra_aba, "sem isso o navegador espreme as abas em vez de rolar"


def test_aba_ativa_de_relatorios_rola_pra_ficar_visivel():
    """Com o trilho rolável, a aba ativa podia nascer fora da área visível (ex.:
    abrir em "Orçamentos" e a tela mostrar só as 6 primeiras abas, sem indicar
    onde se está) — sem isso o scroll-de-lado sozinho não bastava."""
    fonte = inspect.getsource(pt)
    assert '#rel-abas .aba.ativa' in fonte
    assert "scrollIntoView" in fonte.split("#rel-abas .aba.ativa")[1].split("</script>")[0]


def test_kpi_de_relatorio_nao_estoura_a_caixa():
    fonte = inspect.getsource(pt)
    regra_metric = fonte.split(".metric{")[1].split("}")[0]
    assert "min-width:0" in regra_metric, (
        "grid-item sem min-width:0 usa o tamanho do CONTEÚDO como mínimo — "
        "o valor mais longo do que o track do grid estoura a caixa")
    regra_metric_b = fonte.split(".metric b{")[1].split("}")[0]
    assert "overflow-wrap:break-word" in regra_metric_b or "word-break" in regra_metric_b
    # Relatórios tem valores mais longos ("10 · R$ 68.590,00") que o KPI comum
    # do app — precisa de uma fonte menor que o 1.4rem padrão do .metric b.
    assert ".rel-metricas .metric b{" in fonte


def test_linhas_da_tabela_de_relatorio_ficam_em_uma_linha_so():
    """Nome de cliente/empresa longo virando 3 linhas desalinhava a tabela
    inteira — a rolagem lateral do .rel-tbl-wrap já existe pra isso, então a
    célula não precisa (e não deve) quebrar por conta própria."""
    fonte = inspect.getsource(pt)
    regra = fonte.split(".rel-tbl-wrap table td,.rel-tbl-wrap table th{")[1].split("}")[0]
    assert "white-space:nowrap" in regra
