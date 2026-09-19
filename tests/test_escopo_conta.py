"""PORTÃO contra vazamento entre contas: query sem o filtro do dono.

O risco: este SaaS é multi-tenant e o isolamento entre clientes é feito à mão,
um `where conta_id=%s` por query. A RLS do banco NÃO protege (está ligada nas
120 tabelas, mas com zero policies e o papel do app furando por BYPASSRLS — ver
scripts/diag_rls.py). Então UMA query que esqueça o filtro vaza dado de um
cliente pro outro, e nada no banco segura.

Este teste lê o código-fonte e acusa SQL que toca uma tabela sensível sem citar
a coluna de dono dela.

COMO ELE FUNCIONA (e por que é assim):

1. CATRACA, não varredura. O código tem 247 ocorrências em produção que não dá
   pra revisar de uma vez. Então a BASELINE congela o que já existe e o teste só
   quebra quando aparece algo NOVO. Some um item da baseline? Tire da lista —
   dívida paga não volta.

2. LISTA CURADA de tabelas. Não dá pra deduzir a coluna de dono: seis colunas
   diferentes apontam pra contas(id), e nem todas são escopo — `cliente_id` e
   `fornecedor_id` são RELACIONAMENTO (com quem a linha fala), não dono. E o
   nome varia: `clientes` usa `dono_id`, quase todo o resto usa `conta_id`.
   Cada entrada de TABELAS abaixo foi conferida na migração que criou a tabela.

3. É HEURÍSTICA, não prova. Casa texto de SQL; não entende join cujo filtro está
   na tabela pai, nem query escopada por token único. Esses caem na baseline.
   Ele não garante que o que passa está certo — garante que nada NOVO entra sem
   alguém olhar. Uma rede, não um muro.

Pra atualizar a baseline depois de mexer no código:
    python -m tests.test_escopo_conta --baseline
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Tabela -> coluna que diz de QUEM é a linha. Conferido na migração de cada uma.
# Comece pequeno e confiável; crescer esta lista é como o portão aperta.
TABELAS: dict[str, str] = {
    "lancamentos": "conta_id",    # schema.sql — o livro-caixa
    "titulos": "conta_id",        # 053_modulo_pj.sql — a pagar/receber
    "membros": "conta_id",        # schema.sql — quem entra na conta
    "funcionarios": "conta_id",   # 053_modulo_pj.sql — folha
    "leads": "conta_id",          # 023_leads.sql
    "clientes": "dono_id",        # 064_clientes_lojista.sql — ATENÇÃO: dono_id
}

# Só código de produção. tests/ monta cenário de propósito e scripts/ são
# ferramentas one-shot que rodam fora do fluxo do usuário.
IGNORADOS = ("tests/", "scripts/", "db/migracoes/")

_VERBO = re.compile(r"\b(select|update|delete\s+from|insert\s+into)\b", re.I)
_ALVO = re.compile(r"\b(?:from|join|update|into)\s+(?:only\s+)?(?:public\.)?(\w+)", re.I)


def _strings_de(src: str):
    """Cada string do arquivo, com a concatenação implícita JÁ JUNTA.

    Usa `ast` em vez de regex porque o SQL daqui costuma vir partido em literais
    adjacentes ("select ... from titulos " "where conta_id=%s"). Lendo os pedaços
    soltos, o filtro parece ausente e o portão acusa um falso positivo.
    """
    try:
        arvore = ast.parse(src)
    except SyntaxError:
        return
    # f-string: monta o texto e marca os pedaços internos, pra não relê-los soltos
    internos: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.JoinedStr):
            partes = []
            for v in no.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    internos.add(id(v))
                    partes.append(v.value)
                else:
                    partes.append(" ? ")   # trecho interpolado: conteúdo desconhecido
            yield "".join(partes)
    for no in ast.walk(arvore):
        if (isinstance(no, ast.Constant) and isinstance(no.value, str)
                and id(no) not in internos):
            yield no.value


def varrer() -> set[str]:
    """Devolve {"arquivo:tabela"} pra cada SQL sem o filtro de dono."""
    achados: set[str] = set()
    for py in RAIZ.rglob("*.py"):
        rel = str(py.relative_to(RAIZ)).replace("\\", "/")
        if "__pycache__" in rel or rel.startswith(IGNORADOS):
            continue
        try:
            src = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for sql in _strings_de(src):
            if not _VERBO.search(sql):
                continue
            baixo = sql.lower()
            for tabela in set(_ALVO.findall(baixo)):
                col = TABELAS.get(tabela)
                # a coluna de dono citada em QUALQUER lugar do SQL já basta:
                # aqui interessa o esquecimento total, não a forma do filtro.
                if col and col not in baixo:
                    # o arquivo (não a linha) é a chave: linha muda a cada edição
                    # e faria a baseline apodrecer sozinha.
                    achados.add(f"{rel}:{tabela}")
    return achados


# Dívida existente, congelada. NÃO acrescente item aqui sem ter olhado o SQL e
# concluído que está escopado de outro jeito (join pelo pai, token único, etc.).
#
# Destes, quatro foram conferidos um a um ao criar a lista:
#   livro_caixa:lancamentos  -> filtro existe, mora em `cond = "conta_id = %s"`
#   empresa:funcionarios     -> idem, `where {cond}` interpolado
#   estatisticas:lancamentos -> GLOBAL de propósito (visão do dono do SaaS, só
#                               web/admin.py; devolve categoria e contagem, sem
#                               dado de cliente)
#   portal:titulos           -> era literal partido em dois; o `ast` resolveu e
#                               ele saiu da lista sozinho
# Os demais entraram SEM auditoria individual: a catraca serve pra impedir coisa
# NOVA, não pra atestar que o que já existe está certo. Revisar aos poucos e ir
# tirando daqui é exatamente o trabalho que este arquivo torna visível.
BASELINE: set[str] = {
    "contas/contas.py:leads",
    "contas/contas.py:membros",
    "contas/equipe.py:membros",
    "finance/apolices.py:clientes",
    "finance/aviso_log.py:membros",
    "finance/clientes.py:clientes",
    "finance/empresa.py:clientes",
    "finance/empresa.py:funcionarios",
    "finance/empresa.py:membros",
    "finance/empresa.py:titulos",
    "finance/estatisticas.py:lancamentos",
    "finance/funil_regua.py:membros",
    "finance/funil_teto.py:membros",
    "finance/livro_caixa.py:lancamentos",
    "finance/tools.py:membros",
    "web/painel_conteudo.py:membros",
    "web/painel_prospeccao.py:membros",
    "web/painel_relatorios.py:clientes",
    "web/painel_relatorios.py:membros",
    "web/painel_servicos.py:clientes",
    "web/portal.py:clientes",
    "web/portal.py:membros",
}


def test_nenhuma_query_nova_sem_filtro_de_conta():
    """Falha quando aparece SQL NOVO tocando tabela sensível sem o dono."""
    novos = sorted(varrer() - BASELINE)
    assert not novos, (
        "SQL novo toca tabela multi-tenant sem citar a coluna de dono — isso "
        "vaza dado entre clientes.\n  " + "\n  ".join(novos)
        + "\n\nSe cada caso estiver escopado de outra forma (join pelo pai, token "
          "único), confirme lendo o SQL e acrescente à BASELINE de "
          "tests/test_escopo_conta.py."
    )


def test_baseline_nao_tem_item_morto():
    """Item que sumiu do código tem que sair da baseline — senão ela vira lixo
    e deixa de proteger (um arquivo novo poderia 'herdar' a dispensa)."""
    mortos = sorted(BASELINE - varrer())
    assert not mortos, (
        "Estes itens da BASELINE não existem mais. Remova-os:\n  "
        + "\n  ".join(mortos))


if __name__ == "__main__":
    if "--baseline" in sys.argv:
        for item in sorted(varrer()):
            print(f'    "{item}",')
    else:
        achados = varrer()
        print(f"{len(achados)} achado(s); {len(achados - BASELINE)} fora da baseline")
