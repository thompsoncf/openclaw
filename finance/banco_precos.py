"""Banco de precos PROPRIO + comparador de cesta.

O ativo que ninguem pode bloquear: cada item de cupom de mercado vira um preco
observado. Quando o cliente monta a lista, cruzamos os itens com essa base e
dizemos onde a CESTA inteira sai mais barata (destaque) e, ao abrir, item a item.

A base e' compartilhada por REGIAO (preco de mercado e' dado publico), mas nunca
expoe quem comprou - so' produto, preco, mercado, data.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta


_STOP = {"tipo", "de", "do", "da", "com", "sem", "extra", "premium", "kg", "g",
         "un", "und", "ml", "l", "pct", "cx", "pc", "lt"}


def normalizar(desc: str) -> str:
    """Reduz a descricao a um nucleo comparavel: minusculas, sem acento, sem
    unidades/ruido. 'ARROZ TIPO 1 5KG BRANCO' -> 'arroz branco'."""
    s = (desc or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    s = re.sub(r"\b\d+[.,]?\d*\s*(kg|g|ml|l|un|und|pct|cx|lt|pc)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    palavras = [p for p in s.split()
                if p not in _STOP and not p.isdigit() and len(p) > 1]
    return " ".join(palavras[:4])


def tokens(desc: str) -> set[str]:
    return set(normalizar(desc).split())


class BancoPrecos:
    def __init__(self, pool):
        self.pool = pool

    def registrar_item(self, item_id: int, descricao: str, valor_unitario_centavos: int,
                       data_compra: date, mercado: str | None = None,
                       regiao: str | None = None, gtin: str | None = None,
                       conta_id: int | None = None) -> bool:
        """Registra UM preco observado (idempotente por item_id). Ignora preco zero."""
        if not descricao or valor_unitario_centavos <= 0:
            return False
        with self.pool.connection() as c:
            c.execute(
                """insert into precos_observados
                   (descricao_norm, descricao_original, valor_unitario_centavos,
                    mercado, regiao, gtin, data_compra, conta_id, item_id)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   on conflict (item_id) do update set
                     valor_unitario_centavos = excluded.valor_unitario_centavos,
                     mercado = excluded.mercado, regiao = excluded.regiao""",
                (normalizar(descricao), descricao[:200], valor_unitario_centavos,
                 mercado, regiao, gtin, data_compra, conta_id, item_id),
            )
            c.commit()
        return True

    def importar_historico(self, regiao: str | None = None) -> int:
        """Varre os itens de cupom JA' existentes e popula o banco de precos.
        Usa o nome do mercado a partir da descricao do lancamento (heuristica).
        Retorna quantos precos foram (re)gravados."""
        with self.pool.connection() as c:
            rows = c.execute(
                """select i.id, i.descricao, i.valor_unitario_centavos, l.data,
                          l.descricao, l.conta_id
                   from itens_lancamento i
                   join lancamentos l on l.id = i.lancamento_id
                   where i.valor_unitario_centavos > 0 and l.categoria = 'Mercado'"""
            ).fetchall()
        n = 0
        for item_id, desc, vu, data, lanc_desc, conta_id in rows:
            mercado = _mercado_da_descricao(lanc_desc)
            if self.registrar_item(item_id, desc, int(vu), data,
                                    mercado=mercado, regiao=regiao, conta_id=conta_id):
                n += 1
        return n

    def precos_de(self, descricao: str, regiao: str | None = None,
                  dias: int = 90) -> list[dict]:
        """Precos recentes que casam com a descricao (por nucleo normalizado),
        agrupados por mercado (o mais recente de cada). Ordenado do mais barato."""
        nucleo = normalizar(descricao)
        if not nucleo:
            return []
        primeira = nucleo.split()[0]
        corte = date.today() - timedelta(days=dias)
        sql = """select mercado, valor_unitario_centavos, descricao_original, data_compra,
                        descricao_norm
                 from precos_observados
                 where descricao_norm like %s and data_compra >= %s"""
        params: list = [f"%{primeira}%", corte]
        if regiao:
            sql += " and (regiao = %s or regiao is null)"
            params.append(regiao)
        sql += " order by data_compra desc"
        with self.pool.connection() as c:
            rows = c.execute(sql, params).fetchall()
        alvo = tokens(descricao)
        achados = []
        for merc, vu, orig, dt, norm in rows:
            comuns = alvo & set(norm.split())
            if not alvo or len(comuns) >= max(1, len(alvo) // 2):
                achados.append({"mercado": merc or "(sem nome)", "valor_centavos": int(vu),
                                "descricao": orig, "data": dt})
        por_mercado: dict[str, dict] = {}
        for a in achados:
            por_mercado.setdefault(a["mercado"], a)
        return sorted(por_mercado.values(), key=lambda x: x["valor_centavos"])

    def comparar_cesta(self, itens: list[str], regiao: str | None = None,
                       dias: int = 90) -> dict:
        """Para uma lista de produtos, calcula quanto a CESTA sai em cada mercado.
        Retorna o ranking de mercados + detalhe item a item."""
        detalhe_itens = []
        mercados: dict[str, dict] = {}
        total_obs = 0

        for desc in itens:
            precos = self.precos_de(desc, regiao=regiao, dias=dias)
            total_obs += len(precos)
            if precos:
                melhor = precos[0]
                detalhe_itens.append({
                    "descricao": desc,
                    "melhor_mercado": melhor["mercado"],
                    "melhor_centavos": melhor["valor_centavos"],
                    "precos": precos,
                })
                for p in precos:
                    m = mercados.setdefault(p["mercado"], {"soma": 0, "cobertos": 0, "faltando": []})
                    m["soma"] += p["valor_centavos"]
                    m["cobertos"] += 1
            else:
                detalhe_itens.append({
                    "descricao": desc, "melhor_mercado": None,
                    "melhor_centavos": None, "precos": [],
                })

        nomes_itens = [d["descricao"] for d in detalhe_itens]
        for nome_merc, m in mercados.items():
            tem = set()
            for d in detalhe_itens:
                if any(p["mercado"] == nome_merc for p in d["precos"]):
                    tem.add(d["descricao"])
            m["faltando"] = [n for n in nomes_itens if n not in tem]

        ranking = [
            {"mercado": k, "total_centavos": v["soma"],
             "itens_cobertos": v["cobertos"], "itens_faltando": v["faltando"]}
            for k, v in mercados.items()
        ]
        ranking.sort(key=lambda x: (-x["itens_cobertos"], x["total_centavos"]))

        return {"mercados": ranking, "itens": detalhe_itens, "observacoes": total_obs}


def _mercado_da_descricao(lanc_desc: str | None) -> str | None:
    """Heuristica simples pra extrair o nome do mercado da descricao do
    lancamento (ex: 'Compra Carvalho', 'Mercado Assai'). Sem dado, retorna None."""
    if not lanc_desc:
        return None
    s = lanc_desc.strip()
    for ruido in ("compra", "compras", "no ", "na ", "em "):
        s = re.sub(rf"\b{ruido}\b", "", s, flags=re.IGNORECASE).strip()
    return s.title()[:60] if s else None
