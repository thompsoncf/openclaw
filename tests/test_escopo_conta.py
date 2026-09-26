"""PORTÃO contra vazamento entre contas: query sem o filtro do dono.

O risco: este SaaS é multi-tenant e o isolamento entre clientes é feito à mão,
um `where conta_id=%s` por query. A RLS do banco NÃO protege (está ligada nas
120 tabelas, mas com zero policies e o papel do app furando por BYPASSRLS — ver
scripts/diag_rls.py). Então UMA query que esqueça o filtro vaza dado de um
cliente pro outro, e nada no banco segura.

Este teste lê o código-fonte e acusa SQL que toca uma tabela sensível sem citar
a coluna de dono dela.

COMO ELE FUNCIONA (e por que é assim):

1. CATRACA, não varredura. A dívida existente é grande demais pra revisar de uma
   vez. Então a BASELINE congela o que já existe e o teste só quebra quando
   aparece algo NOVO. Some um item da baseline? Tire da lista — dívida paga não
   volta (o segundo teste cobra isso).

2. MAPA VINDO DO BANCO, não deduzido. A coluna de dono não é sempre `conta_id`:
   seis colunas diferentes apontam pra contas(id), e nem todas são escopo —
   `cliente_id` e `fornecedor_id` às vezes são RELACIONAMENTO (com quem a linha
   fala), não dono. `clientes` usa `dono_id`. E há tabela de DOIS LADOS
   (assinaturas, carrinhos, cesta_semana, vinculos), onde a linha pertence ao
   cliente E ao fornecedor.
   Por isso TABELAS foi GERADO de pg_attribute no banco real, e não por regex
   nas migrações: a versão por regex classificou `clientes` como global — o tipo
   de erro que, levado pra uma policy de RLS, quebraria a tabela.

3. É HEURÍSTICA, não prova. Casa texto de SQL; não entende join cujo filtro está
   na tabela pai, nem query escopada por token único, nem filtro montado em
   variável (`cond = "conta_id = %s"`). Esses caem na baseline.
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

# Tabela -> coluna(s) que dizem de QUEM é a linha. GERADO do banco real
# (pg_attribute), não deduzido por regex — a dedução já errou antes:
# classificou `clientes` como global quando ela usa `dono_id`.
#
# Classificação aplicada:
#   tem conta_id            -> conta_id manda (cliente_id/admin_conta_id ao
#                              lado são RELACIONAMENTO, não dono)
#   cliente_id+fornecedor_id-> DOIS LADOS: aceita qualquer um dos dois
#   dono_id                 -> dono_id (conta_zaq_id junto é vínculo opcional)
#   só fornecedor_id        -> fornecedor_id (o fornecedor é o dono)
#
# FORA daqui, de propósito (27 tabelas): as sem nenhuma coluna de escopo —
# globais (planos, nichos, modulos, app_config...), a própria `contas`, e as
# FILHAS que se escopam pelo pai (itens_lancamento, carrinho_itens,
# campanha_alvos, prospeccao_atividades...). Nestas, conferir escopo exige
# seguir a FK — fora do alcance de uma heurística de texto. Fora também
# `pessoas`: conta_zaq_id ali é vínculo OPCIONAL, não dono (a identidade é
# portável entre contas de propósito).
TABELAS: dict[str, tuple[str, ...]] = {
    "aditivo_modelo": ('conta_id',),
    "agenda_config": ('conta_id',),
    "agenda_dia_conferido": ('conta_id',),
    "agenda_mensagens_log": ('conta_id',),
    "agente_config": ('conta_id',),
    "agente_conhecimento": ('conta_id',),
    "agente_visitas": ('conta_id',),
    "apolice_remetentes": ('conta_id',),
    "apolices": ('conta_id',),
    "assinaturas": ('cliente_id', 'fornecedor_id'),
    "aviso_envios": ('conta_id',),
    "campanha_modelos": ('conta_id',),
    "campanhas": ('conta_id',),
    "canais_config": ('conta_id',),
    "carrinhos": ('cliente_id', 'fornecedor_id'),
    "catalogo_produtos": ('fornecedor_id',),
    "centros_custo": ('conta_id',),
    "cesta_semana": ('cliente_id', 'fornecedor_id'),
    "cesta_tamanhos": ('fornecedor_id',),
    "clientes": ('dono_id',),
    "clientes_fusao": ('dono_id',),
    "cockpit_acesso": ('conta_id',),
    "cockpit_lembrete": ('conta_id',),
    "compras_fornecedor": ('fornecedor_id',),
    "compras_historico": ('conta_id',),
    "conta_modulos": ('conta_id',),
    "conteudo_posts": ('conta_id',),
    "contrato_aditivos": ('conta_id',),
    "contrato_modelo": ('conta_id',),
    "contratos": ('conta_id',),
    "conversas": ('conta_id',),
    "conversas_log": ('conta_id',),
    "descadastros": ('conta_id',),
    "distribuicao": ('conta_id',),
    "distribuicao_fila": ('conta_id',),
    "envio_falha": ('conta_id',),
    "estoque_mov": ('fornecedor_id',),
    "evento_convidados": ('conta_id',),
    "eventos_agenda": ('conta_id',),
    "eventos_conta": ('conta_id',),
    "folha_eventos": ('conta_id',),
    "follow_up_esteira": ('conta_id',),
    "follow_up_marcacoes": ('conta_id',),
    "fornecedor_fiscal": ('conta_id',),
    "funcionario_salarios": ('conta_id',),
    "funcionarios": ('conta_id',),
    "funil_avisos": ('conta_id',),
    "funil_etapas": ('conta_id',),
    "funil_motivos_perda": ('conta_id',),
    "funil_movimentos": ('conta_id',),
    "funil_regua": ('conta_id',),
    "funil_renovacoes": ('conta_id',),
    "funil_trava_tentativa": ('conta_id',),
    "lancamentos": ('conta_id',),
    "lead_repasse": ('conta_id',),
    "lead_resumo_ia": ('conta_id',),
    "lead_resumo_ia_uso": ('conta_id',),
    "leads": ('conta_id',),
    "lembretes_enviados": ('conta_id',),
    "lista_compras": ('conta_id',),
    "lista_espera_data": ('conta_id',),
    "membros": ('conta_id',),
    "novidade_lida": ('conta_id',),
    "novidade_semana_vista": ('conta_id',),
    "orcamento_comprovantes": ('conta_id',),
    "orcamento_envios": ('conta_id',),
    "orcamentos": ('conta_id',),
    "origem_compra": ('fornecedor_id',),
    "parcelas_cartao": ('conta_id',),
    "plano_conta_habilitada": ('conta_id',),
    "precos_observados": ('conta_id',),
    "precos_observados_historico": ('conta_id',),
    "prospeccao": ('conta_id',),
    "push_assinaturas": ('conta_id',),
    "qr_leituras": ('conta_id',),
    "raio_x_config": ('conta_id',),
    "raio_x_envios": ('conta_id',),
    "repasses": ('fornecedor_id',),
    "resumo_semanal_envio": ('conta_id',),
    "seguros_comissao": ('conta_id',),
    "servicos_catalogo": ('conta_id',),
    "suporte_acessos": ('conta_id',),
    "titulos": ('conta_id',),
    "tokens_email": ('conta_id',),
    "tokens_reset_senha": ('conta_id',),
    "uso_api": ('conta_id',),
    "uso_diario": ('conta_id',),
    "vinculos": ('cliente_id', 'fornecedor_id'),
    "wa_contatos": ('conta_id',),
    "wa_decifra_diario": ('conta_id',),
    "wa_qr_auth": ('conta_id',),
    "wa_qr_entrada_fila": ('conta_id',),
    "wa_qr_enviadas": ('conta_id',),
    "wa_qr_log": ('conta_id',),
    "wa_qr_sessao_estado": ('conta_id',),
    "wa_qr_sessao_lock": ('conta_id',),
    # o cadastro da clínica (348): tabelas novas com conta_id entram aqui na mão
    # até a próxima regeneração do mapa
    "clinica_profissionais": ('conta_id',),
    "clinica_locais": ('conta_id',),
    "clinica_profissional_tipos": ('conta_id',),
    "clinica_grade": ('conta_id',),
    "clinica_bloqueios": ('conta_id',),
    "clinica_repasses": ('conta_id',),      # 363: o agente passou pra recepção
    "clinica_vagas": ('conta_id',),         # 369: a vaga liberada
    "clinica_vaga_ofertas": ('conta_id',),  # 369: quem foi chamado pra ela
    "clinica_planos": ('conta_id',),        # 373: o plano de tratamento
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
                colunas = TABELAS.get(tabela)
                # a coluna de dono citada em QUALQUER lugar do SQL já basta:
                # aqui interessa o esquecimento total, não a forma do filtro.
                # Tabela de DOIS LADOS (assinaturas, carrinhos, cesta_semana,
                # vinculos) aceita qualquer uma das duas: a linha pertence ao
                # cliente E ao fornecedor, e a consulta legítima filtra por um
                # lado só, conforme quem está olhando.
                if colunas and not any(c in baixo for c in colunas):
                    # o arquivo (não a linha) é a chave: linha muda a cada edição
                    # e faria a baseline apodrecer sozinha.
                    achados.add(f"{rel}:{tabela}")
    return achados


# Dívida existente, congelada. NÃO acrescente item aqui sem ter olhado o SQL e
# concluído que está escopado de outro jeito (join pelo pai, token único, etc.).
#
# Quatro foram conferidos um a um quando a lista tinha 6 tabelas:
#   livro_caixa:lancamentos  -> filtro existe, mora em `cond = "conta_id = %s"`
#   empresa:funcionarios     -> idem, `where {cond}` interpolado
#   estatisticas:lancamentos -> GLOBAL de propósito (visão do dono do SaaS, só
#                               web/admin.py; categoria e contagem, sem dado de
#                               cliente)
#   portal:titulos           -> era literal partido em dois; o `ast` resolveu
# Os demais entraram SEM auditoria individual: a catraca serve pra impedir coisa
# NOVA, não pra atestar que o que já existe está certo. Revisar aos poucos e ir
# tirando daqui é exatamente o trabalho que este arquivo torna visível.
BASELINE: set[str] = {
    "backfill_fotos.py:catalogo_produtos",
    "contas/contas.py:leads",
    "contas/contas.py:membros",
    "contas/equipe.py:membros",
    "contas/suporte.py:suporte_acessos",
    "finance/aditivo.py:contrato_aditivos",
    "finance/agenda.py:agenda_mensagens_log",
    "finance/agenda.py:evento_convidados",
    "finance/agenda.py:eventos_agenda",
    "finance/agente.py:conversas",
    "finance/agente_cesta.py:catalogo_produtos",
    "finance/apolices.py:apolices",
    "finance/apolices.py:clientes",
    "finance/assinaturas.py:assinaturas",
    "finance/aviso_log.py:aviso_envios",
    "finance/aviso_log.py:membros",
    "finance/banco_precos.py:precos_observados",
    "finance/campanhas_motor.py:campanhas",
    "finance/campanhas_motor.py:conversas",
    "finance/campanhas_motor.py:prospeccao",
    "finance/carrinho.py:carrinhos",
    "finance/carrinho.py:catalogo_produtos",
    "finance/catalogo.py:catalogo_produtos",
    "finance/catalogo.py:compras_fornecedor",
    "finance/clientes.py:clientes",
    "finance/cockpit.py:cockpit_lembrete",
    "finance/cockpit.py:contratos",
    "finance/cockpit.py:conversas",
    "finance/cockpit.py:eventos_agenda",
    "finance/cockpit.py:push_assinaturas",
    "finance/comprovantes.py:orcamento_comprovantes",
    "finance/contrato.py:contratos",
    "finance/convites.py:agenda_mensagens_log",
    "finance/convites.py:evento_convidados",
    "finance/convites.py:eventos_agenda",
    "finance/dedup_clientes.py:clientes_fusao",
    "finance/email_inbound.py:conversas",
    "finance/email_inbound.py:prospeccao",
    "finance/empresa.py:clientes",
    "finance/empresa.py:funcionario_salarios",
    "finance/empresa.py:funcionarios",
    "finance/empresa.py:titulos",
    "finance/estatisticas.py:lancamentos",
    "finance/estatisticas.py:precos_observados",
    "finance/estatisticas.py:qr_leituras",
    "finance/esteira.py:follow_up_esteira",
    "finance/financeiro_forn.py:carrinhos",
    "finance/financeiro_forn.py:cesta_semana",
    "finance/follow_up.py:prospeccao",
    "finance/funil_modelo.py:funil_etapas",
    "finance/funil_modelo.py:prospeccao",
    "finance/funil_perda.py:funil_motivos_perda",
    "finance/funil_perdido.py:conversas",
    "finance/funil_regua.py:conversas",
    "finance/funil_regua.py:funil_avisos",
    "finance/funil_regua.py:funil_movimentos",
    "finance/funil_regua.py:membros",
    "finance/funil_teto.py:conversas",
    "finance/funil_teto.py:funil_movimentos",
    "finance/funil_teto.py:funil_renovacoes",
    "finance/funil_teto.py:membros",
    "finance/funil_teto.py:prospeccao",
    "finance/janela.py:assinaturas",
    "finance/janela.py:catalogo_produtos",
    "finance/janela.py:cesta_semana",
    "finance/lista_espera.py:lista_espera_data",
    "finance/livro_caixa.py:lancamentos",
    "finance/livro_caixa.py:parcelas_cartao",
    "finance/livro_caixa.py:precos_observados",
    "finance/manutencao_precos.py:precos_observados",
    "finance/montador.py:catalogo_produtos",
    "finance/observabilidade.py:conversas_log",
    "finance/pedidos.py:catalogo_produtos",
    "finance/raio_x_dono.py:orcamentos",
    "finance/tools.py:membros",
    "web/admin.py:qr_leituras",
    "web/admin_precos.py:precos_observados",
    "web/painel_conteudo.py:conteudo_posts",
    "web/painel_conteudo.py:membros",
    "web/painel_prospeccao.py:campanhas",
    "web/painel_prospeccao.py:conversas",
    "web/painel_prospeccao.py:membros",
    "web/painel_prospeccao.py:orcamentos",
    "web/painel_prospeccao.py:prospeccao",
    "web/painel_relatorios.py:clientes",
    "web/painel_relatorios.py:contratos",
    "web/painel_relatorios.py:conversas",
    "web/painel_relatorios.py:eventos_agenda",
    "web/painel_relatorios.py:membros",
    "web/painel_relatorios.py:orcamentos",
    "web/painel_relatorios.py:prospeccao",
    "web/painel_servicos.py:clientes",
    "web/painel_servicos.py:contratos",
    "web/painel_servicos.py:eventos_agenda",
    "web/painel_servicos.py:orcamentos",
    "web/portal.py:assinaturas",
    "web/portal.py:carrinhos",
    "web/portal.py:catalogo_produtos",
    "web/portal.py:clientes",
    "web/portal.py:membros",
    "web/portal.py:tokens_email",
    "web/portal.py:tokens_reset_senha",
    "web/proposta.py:orcamentos",
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
