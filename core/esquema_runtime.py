"""Esquema garantido em runtime: UMA VEZ POR PROCESSO, não por requisição.

Por que isto existe
-------------------
Três lugares do app criam/atualizam tabela em tempo de execução — nasceram com a
justificativa "o deploy não roda migração sozinho", que deixou de valer quando o
Render ganhou `preDeployCommand: python -m db.aplicar_migracoes`. O atalho ficou,
e virou custo fixo de cada requisição:

    web/painel_servicos.py :: _garantir_tabela          57 comandos DDL
    contas/equipe.py       :: garantir_tabela           13 comandos DDL
    finance/servicos_catalogo.py :: garantir_tabela      5 comandos DDL

Em 22/08/2026 isso deixou o painel lento pra todos os clientes ao mesmo tempo.
Dois efeitos somados:

* `ALTER TABLE` pega ACCESS EXCLUSIVE, que conflita com tudo — inclusive SELECT.
  E o pedido de lock ENFILEIRA quem chega depois: um ALTER esperando uma consulta
  terminar segura toda a fila atrás dele. `contas` e `membros` são lidas em TODA
  requisição autenticada, então travava o sistema inteiro, não só a tela dona da
  tabela.
* São dezenas de idas e voltas ao banco, em série, por página.

O volume acompanhava o uso: 426 comandos DDL registrados na hora de pico contra
~30 numa hora calma. Este módulo corta isso pra uma vez por processo.

Como usar
---------
    from core import esquema_runtime

    def garantir_tabela(pool):
        with pool.connection() as c:
            esquema_runtime.garantir(esquema_runtime.chave(c, "membros_login"),
                                     lambda: _aplicar(c))

A marca só é gravada DEPOIS que `fazer()` volta sem erro: DDL que falhou é
tentado de novo na requisição seguinte, em vez de ficar marcado como pronto.

A chave carrega a identidade do BANCO (host/porta/nome), não só o nome do
esquema. Assim um processo que fale com dois bancos — o caso dos testes — roda o
DDL uma vez em cada, e nunca pula o segundo por causa do primeiro.

Nos testes a marca é limpa antes de cada teste (fixture autouse no
tests/conftest.py), então o comportamento da suíte é o mesmo de antes.
"""
import threading

_feitos: set[str] = set()
_trava = threading.Lock()


def chave(conexao_ou_pool, nome: str) -> str:
    """Identidade do par (esquema, banco). Sem credencial: só host/porta/nome.

    Aceita conexão do psycopg (usa .info) ou pool (usa .conninfo) — os três
    chamadores têm um ou outro na mão.
    """
    info = getattr(conexao_ou_pool, "info", None)
    if info is not None and hasattr(info, "dbname"):
        alvo = f"{info.host}:{info.port}/{info.dbname}"
    else:
        alvo = _do_conninfo(getattr(conexao_ou_pool, "conninfo", "")) or "?"
    return f"{nome}@{alvo}"


def _do_conninfo(conninfo: str) -> str:
    if not conninfo:
        return ""
    try:
        from psycopg.conninfo import conninfo_to_dict
        d = conninfo_to_dict(conninfo)
        return f"{d.get('host')}:{d.get('port')}/{d.get('dbname')}"
    except Exception:  # noqa: BLE001 — chave imperfeita não pode derrubar requisição
        return ""


def garantir(chave_: str, fazer) -> bool:
    """Roda `fazer()` na primeira vez que esta chave aparece no processo.

    Devolve True se rodou agora, False se já estava feito. A trava serializa a
    primeira vez: sem ela, as requisições que chegam juntas no boot disparariam o
    mesmo DDL em paralelo — que é exatamente a rajada que queremos evitar.
    """
    if chave_ in _feitos:
        return False
    with _trava:
        if chave_ in _feitos:       # outra thread fez enquanto esperávamos
            return False
        fazer()
        _feitos.add(chave_)
        return True


def esquecer() -> None:
    """Limpa a marca. Só pros testes — em produção nada chama isto."""
    with _trava:
        _feitos.clear()


def feitos() -> set:
    """Cópia do que já foi garantido neste processo (diagnóstico e testes)."""
    return set(_feitos)
