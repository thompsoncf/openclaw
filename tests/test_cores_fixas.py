"""TRAVA DAS CORES FIXAS (temas do Zaq, fase 2; docs/mockups/zaq_temas.html).

Cor escrita à mão numa tela (`color:#888`) não acompanha o tema: no claro ela
aparece como um borrão escuro, ou some. As cores moram em `web/tema.py` e a tela
usa a variável (`var(--text-dim)`), que troca sozinha no claro, no misto e no
automático.

Esta trava conta as cores fixas de cada arquivo de `web/` e SÓ DEIXA O NÚMERO
CAIR. Os tetos abaixo são a contagem do dia em que a trava entrou: cada fase dos
temas baixa os seus, e tela nova não ganha cor fixa.

COMO CONTA: só o que está dentro de string (comentário Python não conta) e fora
de comentário Jinja `{# #}`, onde "#490" é número de PR e não cor.

Se este teste falhou na sua mudança:
- troque a cor pelo token de `web/tema.py` (fundo: --bg/--surface/--neon-fundo...;
  texto: --text/--text-2/--text-dim; borda: --line/--line-2; aviso: --ambar,
  --coral, --azul, cada um com -fundo e -borda);
- cor que de propósito não muda com o tema (marca do WhatsApp, botão colorido com
  texto branco, documento impresso) pode ficar — e aí o teto do arquivo sobe, com
  o motivo no commit.
"""
import io
import re
import tokenize
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

_HEX = re.compile(r"(?<![&\w])#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])")
_COMENTARIO_JINJA = re.compile(r"\{#.*?#\}", re.S)

#: Não seguem o tema de quem está logado, de propósito: são a fonte das cores ou
#: documentos que o cliente final recebe, imprime e assina.
ISENTOS = {
    "tema.py",               # é onde as cores moram
    "proposta.py", "contrato_publico.py", "aditivo_publico.py",
    "recibo_publico.py", "ficha_publica.py",
}

#: O teto de cada arquivo (contagem de 03/10/2026, depois da fase 2b e das telas da clínica). Arquivo que
#: não está aqui tem teto zero.
TETO = {
    "admin.py": 43,
    "admin_precos.py": 12,
    "app.py": 5,
    "app_obra.py": 25,
    "balao_conversa.py": 8,
    "janela_lead.py": 25,
    "loja_stands.py": 203,
    "painel_aditivo.py": 30,
    "painel_agenda.py": 49,
    "painel_aparencia.py": 11,   # as miniaturas dos temas: desenham as cores de cada um
    "painel_apolices.py": 18,
    "painel_clinica_pacientes.py": 1,   # o QR do balcão, que precisa de fundo branco
    "painel_clinica_planos.py": 17,   # a página pública do plano, que o paciente abre
    "painel_cockpit.py": 55,   # fase 3: ícone e manifesto do app, mapa de stands, página offline
    "painel_conteudo.py": 17,
    "painel_deposito.py": 4,
    "painel_equipe.py": 3,   # o botão do WhatsApp
    "painel_eventos_stands.py": 40,
    "painel_follow_up.py": 12,
    "painel_obras.py": 41,
    "painel_obras_mapa.py": 98,
    "painel_origens.py": 3,
    "painel_prospeccao.py": 353,
    "painel_relatorios.py": 1,
    "painel_respostas.py": 5,
    "painel_servicos.py": 59,
    "portal.py": 803,      # 2a: a base; 2b: Empresa, Relatórios, Clientes, Novidades
    "versao.py": 1,
    "zap_fetch.py": 13,
}


#: Os pedaços de texto que contam. Até o Python 3.11 uma f-string inteira é um
#: STRING; do 3.12 em diante ela vem quebrada, e o texto fica em FSTRING_MIDDLE.
#: Sem os dois, a mesma árvore dava 279 cores no Cockpit numa máquina (3.14) e
#: 283 no CI (3.11), e o teto dependia de onde o teste rodou.
_TIPOS_DE_TEXTO = {tokenize.STRING} | (
    {tokenize.FSTRING_MIDDLE} if hasattr(tokenize, "FSTRING_MIDDLE") else set())


def cores_fixas(caminho: Path) -> int:
    """Quantas cores `#rgb`/`#rrggbb` o arquivo tem dentro de string (f-string
    inclusive), sem contar comentário Jinja."""
    n = 0
    fonte = caminho.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
        if tok.type in _TIPOS_DE_TEXTO:
            n += len(_HEX.findall(_COMENTARIO_JINJA.sub("", tok.string)))
    return n


def test_nenhum_arquivo_ganha_cor_fixa():
    passou = []
    for caminho in sorted((RAIZ / "web").glob("*.py")):
        if caminho.name in ISENTOS:
            continue
        n, teto = cores_fixas(caminho), TETO.get(caminho.name, 0)
        if n > teto:
            passou.append(f"{caminho.name}: {n} cores fixas (o teto é {teto})")
    assert not passou, (
        "Cor escrita à mão não acompanha o tema claro/misto. Use os tokens de "
        "web/tema.py (veja o topo de tests/test_cores_fixas.py):\n  " + "\n  ".join(passou))


def test_a_contagem_ignora_comentario_e_numero_de_pr():
    """'#490' num comentário Jinja é o número do PR; 'color:#490' numa string é cor."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        arq = Path(d) / "x.py"
        arq.write_text('# comentário #fff não conta\n'
                       'A = """{# desde o #490 #}<p style="color:#490">a</p>"""\n'
                       'B = "background:#1E2A23;color:#EAF2ED"\n'
                       'C = "#ic-novidades #menu-links"\n'
                       'x = 1\n'
                       'D = f"<b style=\'color:#abc\'>{x}</b>"\n', encoding="utf-8")
        # a f-string conta igual nas duas versões do Python (3.11 no CI, 3.12+ fora dele)
        assert cores_fixas(arq) == 4


def test_o_teto_so_lista_arquivo_que_existe():
    """Arquivo apagado ou renomeado não pode deixar o teto antigo pendurado: um
    arquivo novo com o mesmo nome herdaria a folga."""
    existentes = {p.name for p in (RAIZ / "web").glob("*.py")}
    assert set(TETO) <= existentes, sorted(set(TETO) - existentes)
    assert not (set(TETO) & ISENTOS)
