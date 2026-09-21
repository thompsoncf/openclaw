"""A API de cotação que a corretora entrega pro site dela — /api/v1/cotacoes.

A PORTA DE FORA (decisão do dono, 21/09/2026: "OpenClaw expõe"). O site da
Liberal, uma landing de campanha ou um parceiro manda o risco por HTTP e recebe as
ofertas; a cotação fica na base da corretora, com origem 'api', e aparece na tela
do corretor como a fila "Cotações do site".

AUTENTICAÇÃO É CHAVE POR CONTA, NÃO SESSÃO. `Authorization: Bearer zaq_cot_…`,
emitida na própria tela de Cotações. O banco guarda só o sha256 (ver
`cotacao.criar_chave`), e é a chave que diz de qual conta é a cotação — nenhum
`conta_id` vem do corpo. Sem isso, quem tivesse a chave de uma corretora cotaria
na conta de outra só trocando um número no JSON.

O QUE ESTA API NÃO FAZ, e é decisão, não pendência: ela não escolhe oferta e não
emite proposta. Cotar é o que o site do cliente precisa; ESCOLHER é ato comercial
da corretora, com comissão e responsabilidade no meio — e ato comercial acontece
na tela de quem responde por ele, não num POST de terceiro.

LIMITE DE TAMANHO E TEMPO ficam no provedor e no FastAPI; o que esta camada
garante é que erro de risco (CPF errado, CEP faltando) volta 422 com a frase em
português, e falha de provedor volta 200 com `situacao: "falhou"` e o motivo — a
cotação existe dos dois jeitos, e o site precisa poder dizer o que houve.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db.conexao import get_pool
from finance import cotacao as ct

router = APIRouter()
_log = logging.getLogger("openclaw.api_cotacao")


def _token_do_cabecalho(request: Request) -> str:
    """O token cru, sem tocar no banco. Aceita 'Bearer x' e 'x' pelado: integrador
    que esquece o prefixo é o erro mais comum de primeira integração, e devolver
    401 por causa dele gasta uma tarde de alguém."""
    bruto = (request.headers.get("authorization") or "").strip()
    if bruto.lower().startswith("bearer "):
        bruto = bruto[7:].strip()
    if not bruto:
        bruto = (request.headers.get("x-api-key") or "").strip()
    return bruto


def _conta_do_token(token: str) -> int | None:
    """A conta dona da chave — ou None. TOCA O BANCO: nunca chame no event loop
    (ver tests/test_event_loop_nao_trava e o incidente de 22/08/2026, em que
    chamada síncrona em handler async levou a resposta de 527 ms pra ~50 s)."""
    if not (token or "").strip():
        return None
    try:
        return ct.conta_da_chave(get_pool(), token)
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra conferir a chave: %s: %s", type(e).__name__, e)
        return None


def _conta_da_requisicao(request: Request) -> int | None:
    """Versão síncrona, pros handlers `def` (que o FastAPI já roda na threadpool)."""
    return _conta_do_token(_token_do_cabecalho(request))


def _sem_chave() -> JSONResponse:
    return JSONResponse(
        {"erro": "chave de API ausente ou revogada",
         "como": "mande o cabeçalho Authorization: Bearer <chave>; a chave é emitida "
                 "no painel, em Cotações › Chaves da API"},
        status_code=401)


def _oferta_publica(o: dict) -> dict:
    """O que o site vê. A COMISSÃO NÃO VAI: é quanto a corretora ganha, e o
    segurado não vê isso em lugar nenhum do mercado — muito menos num JSON que
    qualquer um lê apertando F12 no navegador."""
    return {
        "seguradora": o["seguradora"],
        "produto": o["produto"],
        "premio_total_centavos": o["premio_total_centavos"],
        "premio_liquido_centavos": o["premio_liquido_centavos"],
        "iof_centavos": o["iof_centavos"],
        "franquia_centavos": o["franquia_centavos"],
        "parcelas": o["parcelas"],
        "coberturas": o["coberturas"],
        "validade": o["validade"].isoformat() if o.get("validade") else None,
    }


#: O que o site lê quando o provedor falha. GENÉRICO de propósito: a mensagem
#: crua nomeia o multicálculo da corretora e às vezes devolve pedaço do corpo da
#: resposta dele ("Segfy 500 em /quote: ..."). Quem precisa do detalhe é o
#: corretor, e ele o tem na tela — o visitante do site precisa saber que não deu.
ERRO_PUBLICO = "não foi possível cotar agora; tente de novo em alguns minutos"


def _cotacao_publica(cot: dict) -> dict:
    """O JSON que sai pra fora. Três coisas ficam DENTRO: a comissão (é quanto a
    corretora ganha), o nome do provedor (é fornecedor dela, não assunto de quem
    cota) e o texto cru do erro."""
    return {
        "id": cot["id"],
        "situacao": cot["situacao"],
        "situacao_txt": cot["situacao_txt"],
        "ramo": cot["ramo"],
        "erro": (ERRO_PUBLICO if cot["situacao"] == "falhou" else None),
        "criado_em": cot["criado_em"].isoformat() if cot.get("criado_em") else None,
        "ofertas": [_oferta_publica(o) for o in cot.get("ofertas") or []],
    }


def _cotar_sincrono(token: str, dados: dict) -> JSONResponse:
    """Tudo que fala com banco e com provedor. Roda na THREADPOOL, nunca no loop."""
    conta_id = _conta_do_token(token)
    if conta_id is None:
        return _sem_chave()
    try:
        risco = ct.normalizar_risco(dados, ramo=(dados.get("ramo") or "auto"))
    except ct.CotacaoErro as e:
        return JSONResponse({"erro": str(e)}, status_code=422)
    pool = get_pool()
    cid = ct.criar(pool, conta_id, risco, ramo=(dados.get("ramo") or "auto"),
                   origem="api")
    cot = ct.cotar(pool, conta_id, cid)
    return JSONResponse(_cotacao_publica(cot), status_code=201)


@router.post("/api/v1/cotacoes")
async def criar_cotacao(request: Request):
    """Recebe o risco, cota e devolve as ofertas.

    ASYNC SÓ PRA LER O CORPO; o resto vai pra `run_in_threadpool`. É a segunda das
    duas formas certas que a base já usa (a outra é o handler `def`): o psycopg
    daqui é síncrono, e uma consulta no event loop congela o worker INTEIRO — não
    só esta requisição. Cotar ainda espera um terceiro pela rede, então é
    justamente o handler em que isso doeria mais.

    Devolve 201 mesmo quando o provedor falhou: a cotação foi criada e existe na
    conta da corretora — quem falhou foi o terceiro, e `situacao`/`erro` dizem
    isso. Devolver 500 aqui faria o site apagar um lead que a corretora tem.
    """
    try:
        dados = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"erro": "corpo não é JSON"}, status_code=400)
    if not isinstance(dados, dict):
        return JSONResponse({"erro": "o corpo tem que ser um objeto JSON"}, status_code=400)
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_cotar_sincrono, _token_do_cabecalho(request), dados)


@router.get("/api/v1/cotacoes/{cotacao_id}")
def ler_cotacao(request: Request, cotacao_id: int):
    conta_id = _conta_da_requisicao(request)
    if conta_id is None:
        return _sem_chave()
    cot = ct.ler(get_pool(), conta_id, cotacao_id)
    if not cot:
        # 404 e não 403: a chave é de outra conta, e dizer "existe, mas não é sua"
        # já conta que ela existe. Pra quem tem a chave certa, é a mesma resposta.
        return JSONResponse({"erro": "cotação não encontrada"}, status_code=404)
    return JSONResponse(_cotacao_publica(cot))
