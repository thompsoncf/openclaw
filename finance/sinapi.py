"""O custo médio do m² de construção por estado (SINAPI, IBGE) — migração 377.

Pra que serve: a ficha da obra mostra o custo por m² da casa (finance/obras.py,
`custo_m2`). Sem referência, o número não diz se está caro ou barato. O SINAPI é
o custo médio de construção por m² que o IBGE publica todo mês por estado — a
Caixa usa a mesma família de dados. É pra COMPARAR, não pra cobrar (desenho de
25/09/2026, seção 06): o SINAPI é um padrão residencial médio, e casa popular
pode e deve sair abaixo.

De onde vem: a API pública do SIDRA, tabela 2296, variáveis 48 (custo médio m²),
2119 (material) e 2120 (mão de obra), por UF (nível 3). Conferido em 26/09/2026:
Maranhão, agosto/2026 = R$ 1.969,07 (material 1.197,36 + mão de obra 771,71).

Como fica atualizado: `atualizar` roda no ticker do web, uma vez por dia, só pros
estados das contas de construção, e guarda em `sinapi_referencia`. A ficha lê só
o banco — nunca chama o IBGE na hora de abrir a tela.
"""
from __future__ import annotations

import logging
from datetime import date

_log = logging.getLogger("openclaw.sinapi")

_URL = "https://apisidra.ibge.gov.br/values/t/2296/n3/{cod}/v/48,2119,2120/p/last%201"
_VAR = {"48": "total_centavos", "2119": "material_centavos", "2120": "mao_de_obra_centavos"}
#: código IBGE de cada UF (nível territorial 3 do SIDRA)
UF_CODIGO = {"RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
             "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26, "AL": 27, "SE": 28,
             "BA": 29, "MG": 31, "ES": 32, "RJ": 33, "SP": 35, "PR": 41, "SC": 42, "RS": 43,
             "MS": 50, "MT": 51, "GO": 52, "DF": 53}
_MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def _centavos(v) -> int | None:
    try:
        return int(round(float(str(v).replace(",", ".")) * 100))
    except (TypeError, ValueError):
        return None


def ler_resposta(dados: list) -> dict | None:
    """A resposta do SIDRA (a 1ª linha é o cabeçalho) -> {mes, total_centavos, ...}.
    None se não veio o total."""
    out: dict = {}
    for r in (dados or [])[1:]:
        campo = _VAR.get(str(r.get("D2C")))
        mes = str(r.get("D3C") or "")
        if not campo or len(mes) != 6:
            continue
        out["mes"] = date(int(mes[:4]), int(mes[4:]), 1)
        out[campo] = _centavos(r.get("V"))
    return out if out.get("total_centavos") else None


def buscar(uf: str, get=None) -> dict | None:
    """O último mês publicado pra UF. `get` é injetável nos testes."""
    cod = UF_CODIGO.get((uf or "").upper())
    if not cod:
        return None
    if get is None:
        import httpx

        def get(url):
            r = httpx.get(url, timeout=15.0)
            r.raise_for_status()
            return r.json()
    return ler_resposta(get(_URL.format(cod=cod)))


def guardar(pool, uf: str, ref: dict) -> None:
    with pool.connection() as c:
        c.execute("""insert into sinapi_referencia (uf, mes, total_centavos, material_centavos,
                                                    mao_de_obra_centavos, buscado_em)
                     values (%s,%s,%s,%s,%s, now())
                     on conflict (uf, mes) do update set
                        total_centavos=excluded.total_centavos,
                        material_centavos=excluded.material_centavos,
                        mao_de_obra_centavos=excluded.mao_de_obra_centavos,
                        buscado_em=now()""",
                  (uf.upper(), ref["mes"], ref["total_centavos"], ref.get("material_centavos"),
                   ref.get("mao_de_obra_centavos")))
        c.commit()


def referencia(pool, uf: str | None) -> dict | None:
    """O mês mais recente guardado pra UF, pronto pra tela. None sem dado (ou sem a 377)."""
    if not uf:
        return None
    try:
        with pool.connection() as c:
            r = c.execute("""select mes, total_centavos, material_centavos, mao_de_obra_centavos
                               from sinapi_referencia where uf=%s order by mes desc limit 1""",
                          (uf.upper(),)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    return {"uf": uf.upper(), "mes": r[0], "rotulo_mes": f"{_MESES[r[0].month - 1]}/{r[0].year}",
            "total_centavos": r[1], "material_centavos": r[2], "mao_de_obra_centavos": r[3]}


def uf_da_conta(pool, conta_id: int) -> str | None:
    try:
        with pool.connection() as c:
            r = c.execute("select uf from contas where id=%s", (conta_id,)).fetchone()
        return (r[0] or "").strip().upper() or None if r else None
    except Exception:  # noqa: BLE001
        return None


def comparar(obra: dict, ref: dict | None) -> dict | None:
    """O custo por m² da obra contra a referência. Só compara o que é comparável:
    obra PRONTA pelo gasto real; obra em andamento pelo custo PREVISTO por m² (o
    gasto de meia obra contra o custo de uma obra inteira diria que está barato
    só porque não acabou). None sem área ou sem referência."""
    if not ref or not obra.get("area_m2"):
        return None
    area = float(obra["area_m2"])
    pronta = obra.get("pct") == 100 or obra.get("status") in ("pronta", "vendida", "entregue")
    if pronta and obra.get("custo_m2"):
        base, valor = "gasto", int(obra["custo_m2"])
    elif obra.get("custo_previsto_centavos"):
        base, valor = "previsto", int(round(obra["custo_previsto_centavos"] / area))
    else:
        return {"base": None, "valor": None, "pct": None}
    pct = int(round(100 * (valor - ref["total_centavos"]) / ref["total_centavos"]))
    return {"base": base, "valor": valor, "pct": pct}


def atualizar(pool, hoje: date | None = None, get=None) -> int:
    """Uma passada do ticker: pra cada UF de conta de construção cujo dado não foi
    buscado hoje, busca no IBGE e guarda. Nunca levanta. Devolve quantas UFs vieram."""
    hoje = hoje or date.today()
    try:
        with pool.connection() as c:
            if c.execute("select to_regclass('public.sinapi_referencia')").fetchone()[0] is None:
                return 0
            ufs = [r[0] for r in c.execute(
                """select distinct upper(c.uf) from contas c join nichos n on n.id = c.nicho_id
                    where n.slug = 'construcao' and coalesce(c.uf, '') <> ''
                      and not exists (select 1 from sinapi_referencia s
                                       where s.uf = upper(c.uf) and s.buscado_em::date >= %s)""",
                (hoje,)).fetchall()]
    except Exception as e:  # noqa: BLE001
        _log.info("sinapi: não deu pra listar as UFs: %s", e)
        return 0
    n = 0
    for uf in ufs:
        try:
            ref = buscar(uf, get=get)
            if ref:
                guardar(pool, uf, ref)
                n += 1
            else:
                # marca a tentativa no mês que já tem, pra não bater no IBGE a cada 2 min
                with pool.connection() as c:
                    c.execute("update sinapi_referencia set buscado_em=now() where uf=%s", (uf,))
                    c.commit()
        except Exception as e:  # noqa: BLE001
            _log.info("sinapi: %s falhou: %s", uf, e)
            try:
                with pool.connection() as c:
                    c.execute("update sinapi_referencia set buscado_em=now() where uf=%s", (uf,))
                    c.commit()
            except Exception:  # noqa: BLE001
                pass
    return n
