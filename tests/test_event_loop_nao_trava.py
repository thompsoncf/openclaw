"""Handler `async` não pode fazer chamada de banco SÍNCRONA no event loop.

O psycopg deste projeto é síncrono. Num handler `async def`, cada `get_pool()` /
`pool.connection()` congela o worker INTEIRO enquanto o banco não responde — não
só aquela requisição. Com os dois workers do Render, bastava os dois estarem numa
chamada dessas pra fila toda parar: em 22/08/2026 o tempo de resposta saiu de
527 ms pra ~50 s com a CPU em 0,7% (esperando, não trabalhando).

Existem duas formas certas, e as duas já aparecem na base:

  * handler `def` (sem async) — o FastAPI roda o handler inteiro na threadpool.
    É o caminho da maioria das rotas do painel;
  * handler `async def` que só lê o corpo e passa o resto pra
    `run_in_threadpool` — é o que os webhooks do wa-qr fazem, e o que o
    `webhook_wa_qr_contatos` já fazia desde que uma importação de agenda virou
    502 em série no Render.

PENDENTES é a dívida que sobrou, nomeada. A lista só pode ENCOLHER: um handler
consertado tem que sair daqui (o segundo teste falha se ficar sobrando), e um
handler novo que trave o loop não entra na lista sem alguém decidir isso de
propósito.
"""
import ast
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "web"
BLOQUEANTES = ("pool.connection()", "get_pool()")
ROTA = ("get", "post", "put", "delete", "api_route")

# (arquivo, handler) que AINDA travam o event loop. Não acrescente nada aqui sem
# um bom motivo — o certo é consertar. Ver o docstring acima pras duas formas.
PENDENTES = {
    ("app.py", "webhook_asaas"),
    ("app.py", "pesquisa_salvar"),
    ("painel_cockpit.py", "cockpit_lead_audio"),
    ("painel_prospeccao.py", "prospeccao_base_add_campanha"),
    ("painel_prospeccao.py", "prospeccao_base_explorium"),
    ("painel_prospeccao.py", "prospeccao_explorium_importar"),
    ("painel_prospeccao.py", "captar_csv"),
    ("painel_prospeccao.py", "captar_importar"),
    ("painel_prospeccao.py", "comunicacao_agente_config"),
    ("painel_prospeccao.py", "comunicacao_distribuicao"),
    ("painel_prospeccao.py", "comunicacao_prospec_perfil"),
    ("painel_prospeccao.py", "webhook_twilio"),
    ("painel_prospeccao.py", "webhook_twilio_status"),
    ("painel_prospeccao.py", "webhook_meta"),
    ("painel_prospeccao.py", "webhook_wa_qr_audio"),
    ("painel_prospeccao.py", "prospeccao_campanha_config"),
    ("painel_prospeccao.py", "regua_config"),
    ("painel_prospeccao.py", "regua_etapa"),
    ("painel_prospeccao.py", "prospeccao_status"),
    ("painel_prospeccao.py", "prospeccao_base_enriquecer"),
    ("painel_servicos.py", "painel_servicos_comprovante_subir"),
    ("portal.py", "salvar_logo_fornecedor"),
    ("portal.py", "salvar_banner_fornecedor"),
    ("portal.py", "painel_produtos_vender"),
    ("portal.py", "painel_cliente_whatsapp"),
    ("portal.py", "painel_produtos_upload_foto"),
    ("portal.py", "painel_produtos_ler_planilha"),
    ("portal.py", "painel_produtos_importar_planilha"),
    ("portal.py", "classificar_a_definir"),
    ("portal.py", "painel_lancamentos_ler_ofx"),
    ("portal.py", "painel_lancamentos_importar_ofx"),
}


def _handlers_que_travam() -> set:
    achados = set()
    for p in sorted(RAIZ.glob("*.py")):
        src = p.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                       and d.func.attr in ROTA for d in node.decorator_list):
                continue
            seg = ast.get_source_segment(src, node) or ""
            if any(m in seg for m in BLOQUEANTES):
                achados.add((p.name, node.name))
    return achados


def test_nenhum_handler_novo_trava_o_event_loop():
    novos = _handlers_que_travam() - PENDENTES
    assert not novos, (
        "handler async fazendo banco síncrono no event loop:\n  "
        + "\n  ".join(f"{a}::{b}" for a, b in sorted(novos))
        + "\n\nTire o `async` (o FastAPI joga o handler pra threadpool) ou leia só "
          "o corpo no async e mande o resto pro run_in_threadpool, como fazem os "
          "webhooks do wa-qr."
    )


def test_pendentes_nao_tem_entrada_morta():
    """Consertou? Some daqui. Sem isto a lista nunca encolheria de verdade."""
    mortas = PENDENTES - _handlers_que_travam()
    assert not mortas, (
        "estes já não travam o loop — tire de PENDENTES:\n  "
        + "\n  ".join(f"{a}::{b}" for a, b in sorted(mortas)))


@pytest.mark.parametrize("nome", [
    "webhook_wa_qr", "webhook_wa_qr_historico", "webhook_wa_qr_saida",
    "webhook_wa_qr_status", "webhook_wa_qr_deslogado",
])
def test_webhooks_do_wa_qr_passam_o_trabalho_pra_threadpool(nome):
    """Os que rodam a CADA mensagem: o wrapper async só lê o corpo."""
    from web import painel_prospeccao as pp
    src = pathlib.Path(pp.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    wrapper = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == nome)
    seg = ast.get_source_segment(src, wrapper) or ""
    assert "run_in_threadpool" in seg, f"{nome} não manda o trabalho pra threadpool"
    assert not any(m in seg for m in BLOQUEANTES), f"{nome} ainda toca o banco no loop"
    # e o worker existe, é síncrono, e é ele quem fala com o banco
    worker = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == f"_{nome}_sync"), None)
    assert worker is not None, f"falta o worker _{nome}_sync"
    assert any(m in (ast.get_source_segment(src, worker) or "") for m in BLOQUEANTES)
