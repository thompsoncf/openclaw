"""Banco de precos PROPRIO + comparador de cesta.

O ativo que ninguem pode bloquear: cada item de cupom de mercado vira um preco
observado. Quando o cliente monta a lista, cruzamos os itens com essa base e
dizemos onde a CESTA inteira sai mais barata (destaque) e, ao abrir, item a item.

A base e' compartilhada por REGIAO (preco de mercado e' dado publico), mas nunca
expoe quem comprou - so' produto, preco, mercado, data.

ESCALA: le' primeiro da tabela agregada precos_vigentes (mediana por
produto+loja - rapido e robusto a outlier). Se vigentes estiver vazia
(ex: manutencao ainda nao rodou), cai pro fallback nas observacoes brutas.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta


# ---------- normalizacao de descricao (casar "ARROZ T1 5KG" com "arroz") ----------

_STOP = {"tipo", "de", "do", "da", "com", "sem", "extra", "premium", "kg", "g",
         "un", "und", "ml", "l", "pct", "cx", "pc", "lt"}


def normalizar(desc: str) -> str:
    """Reduz a descricao a um nucleo comparavel: minusculas, sem acento, sem
    unidades/ruido. 'ARROZ TIPO 1 5KG BRANCO' -> 'arroz branco'."""
    s = (desc or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    # remove unidades coladas a numero: 5kg, 500g, 2l, 1.5l, 200ml
    s = re.sub(r"\b\d+[.,]?\d*\s*(kg|g|ml|l|un|und|pct|cx|lt|pc)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    # remove numeros soltos e stopwords
    palavras = [p for p in s.split()
                if p not in _STOP and not p.isdigit() and len(p) > 1]
    return " ".join(palavras[:4])  # nucleo: ate' 4 palavras significativas


def tokens(desc: str) -> set[str]:
    return set(normalizar(desc).split())


class BancoPrecos:
    def __init__(self, pool):
        self.pool = pool

    # ---------- ingestao ----------

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

    # ---------- consulta de preco de UM item ----------

    def precos_de(self, descricao: str, regiao: str | None = None,
                  dias: int = 90, so_com_endereco: bool = False) -> list[dict]:
        """Precos recentes que casam com a descricao (por nucleo normalizado),
        agrupados por mercado (o mais recente de cada). Ordenado do mais barato.

        ESCALA: le' primeiro da tabela agregada precos_vigentes (mediana por
        produto+loja - rapido e robusto a outlier). Se vigentes estiver vazia
        (ex: manutencao ainda nao rodou), cai pro fallback nas observacoes brutas.

        so_com_endereco=True: descarta precos sem loja com endereco (texto livre
        sem CNPJ) - usado pela lista de compras, onde so' vale loja que da' pra ir.
        """
        nucleo = normalizar(descricao)
        if not nucleo:
            return []
        primeira = nucleo.split()[0]  # ancora a busca na 1a palavra significativa
        # 1) tenta a tabela agregada (vigentes)
        vig = self._precos_de_vigentes(descricao, primeira, regiao)
        # 2) fallback: observacoes brutas (comportamento antigo, sempre funciona)
        res = vig if vig else self._precos_de_brutos(descricao, primeira, regiao, dias)
        if so_com_endereco:
            res = [p for p in res if p.get("endereco")]
        return res

    def _precos_de_vigentes(self, descricao: str, primeira: str,
                            regiao: str | None) -> list[dict]:
        """Le da tabela agregada precos_vigentes (mediana por produto+loja)."""
        sql = """select v.mercado, v.valor_mediana_centavos, v.descricao_exemplo,
                        v.data_mais_recente, v.descricao_norm,
                        l.endereco, l.cidade, l.uf, v.unidade, v.n_observacoes,
                        l.nome, v.loja_id
                 from precos_vigentes v
                 left join lojas l on l.id = v.loja_id
                 where v.descricao_norm like %s"""
        params: list = [f"%{primeira}%"]
        if regiao:
            sql += " and (v.regiao = %s or v.regiao is null)"
            params.append(regiao)
        try:
            with self.pool.connection() as c:
                rows = c.execute(sql, params).fetchall()
        except Exception:  # noqa: BLE001 - tabela pode nao existir ainda
            return []
        alvo = tokens(descricao)
        achados = []
        for (merc, vu, orig, dt, norm, endereco, l_cidade, l_uf, unidade, nobs,
             l_nome, loja_id) in rows:
            comuns = alvo & set(norm.split())
            if not alvo or len(comuns) >= max(1, len(alvo) // 2):
                partes_end = [p for p in [endereco,
                              f"{l_cidade}/{l_uf}" if l_cidade and l_uf else None] if p]
                # nome canonico da loja (por CNPJ) tem prioridade sobre o texto livre
                nome_loja = l_nome or merc or "(sem nome)"
                # dedup pela loja REAL (loja_id) quando existe; senao pelo texto
                chave_loja = f"id:{loja_id}" if loja_id else f"txt:{nome_loja}"
                achados.append({"mercado": nome_loja, "_chave": chave_loja,
                                "valor_centavos": int(vu),
                                "descricao": orig, "data": dt,
                                "unidade": unidade or "UN", "n_observacoes": int(nobs or 1),
                                "endereco": ", ".join(partes_end) or None})
        por_mercado: dict[str, dict] = {}
        for a in achados:
            por_mercado.setdefault(a.pop("_chave"), a)
        return sorted(por_mercado.values(), key=lambda x: x["valor_centavos"])

    def _precos_de_brutos(self, descricao: str, primeira: str,
                          regiao: str | None, dias: int) -> list[dict]:
        """Fallback: le das observacoes brutas (comportamento original)."""
        corte = date.today() - timedelta(days=dias)
        sql = """select po.mercado, po.valor_unitario_centavos, po.descricao_original,
                        po.data_compra, po.descricao_norm,
                        l.endereco, l.cidade, l.uf, po.unidade,
                        l.nome, po.loja_id
                 from precos_observados po
                 left join lojas l on l.id = po.loja_id
                 where po.descricao_norm like %s and po.data_compra >= %s"""
        params: list = [f"%{primeira}%", corte]
        if regiao:
            sql += " and (po.regiao = %s or po.regiao is null)"
            params.append(regiao)
        sql += " order by po.data_compra desc"
        with self.pool.connection() as c:
            rows = c.execute(sql, params).fetchall()
        # filtra por similaridade de tokens (evita casar 'arroz' com 'arroz doce')
        alvo = tokens(descricao)
        achados = []
        for (merc, vu, orig, dt, norm, endereco, l_cidade, l_uf, unidade,
             l_nome, loja_id) in rows:
            comuns = alvo & set(norm.split())
            if not alvo or len(comuns) >= max(1, len(alvo) // 2):
                # monta endereco legivel (rua/bairro + cidade/uf), quando houver
                partes_end = [p for p in [endereco,
                              f"{l_cidade}/{l_uf}" if l_cidade and l_uf else None] if p]
                # nome canonico da loja (por CNPJ) tem prioridade sobre o texto livre
                nome_loja = l_nome or merc or "(sem nome)"
                chave_loja = f"id:{loja_id}" if loja_id else f"txt:{nome_loja}"
                achados.append({"mercado": nome_loja, "_chave": chave_loja,
                                "valor_centavos": int(vu),
                                "descricao": orig, "data": dt,
                                "unidade": unidade or "UN",
                                "endereco": ", ".join(partes_end) or None})
        # 1 preco por loja (o mais recente, ja' que vem ordenado desc)
        por_mercado: dict[str, dict] = {}
        for a in achados:
            por_mercado.setdefault(a.pop("_chave"), a)
        return sorted(por_mercado.values(), key=lambda x: x["valor_centavos"])

    # ---------- opcoes por item (visao "menor preco" com selo de fonte) ----------
    def _coluna_fonte_existe(self) -> bool:
        """True se precos_observados tem a coluna 'fonte' (migração 012 aplicada).
        Cacheia no objeto. Antes, tudo é 'cupom'."""
        if getattr(self, "_tem_fonte", None) is not None:
            return self._tem_fonte
        try:
            with self.pool.connection() as c:
                r = c.execute(
                    "select 1 from information_schema.columns "
                    "where table_name = 'precos_observados' and column_name = 'fonte'"
                ).fetchone()
            self._tem_fonte = bool(r)
        except Exception:  # noqa: BLE001
            self._tem_fonte = False
        return self._tem_fonte

    def opcoes_item(self, descricao: str, regiao: str | None = None,
                    dias: int = 90, limite: int = 12,
                    gtin: str | None = None, produto: str | None = None,
                    tudo: bool = False) -> dict:
        """Menor preço em DOIS NÍVEIS, pra comparar PRODUTO IGUAL (nunca 1kg x 5kg):

          - SEM gtin  -> nível 'produtos': lista os produtos distintos que casam
            com o termo, cada um com NOME + faixa de preço + em quantas lojas.
          - COM gtin  -> nível 'lojas': ranqueia as lojas DAQUELE produto exato,
            do mais barato ao mais caro, com a FONTE (cupom/catálogo) pro selo.

        Retorna sempre um dict:
          {"nivel": "produtos", "termo": <str>, "ocultos": N, "opcoes": [
              {"gtin","descricao","preco_min_centavos","preco_max_centavos","n_lojas","fonte","nucleo"} ]}
          {"nivel": "lojas", "produto": <str>, "gtin": <str>, "opcoes": [
              {"mercado","fonte","preco_centavos","descricao","unidade","data","dias","endereco"} ]}
        Não toca em precos_de() nem na cesta (que continua só cupom).
        """
        import re
        import unicodedata

        def _cidade_core(s: str) -> str:
            s = (s or "").strip().lower()
            s = "".join(ch for ch in unicodedata.normalize("NFD", s)
                        if unicodedata.category(ch) != "Mn")
            s = re.sub(r"[\-/ ]+[a-z]{2}$", "", s)
            return s.strip()

        def tam(txt: str) -> set:
            t = (txt or "").lower().replace(",", ".")
            out = set()
            for val, un in re.findall(
                    r"(\d+(?:\.\d+)?)\s*(kgs?|gramas?|grs?|gr|g|ml|lts?|litros?|l)\b", t):
                v = val.rstrip("0").rstrip(".") if "." in val else val
                un = {"kgs": "kg", "gr": "g", "grs": "g", "grama": "g", "gramas": "g",
                      "lt": "l", "lts": "l", "litro": "l", "litros": "l"}.get(un, un)
                out.add(f"{v}{un}")
            return out

        corte = date.today() - timedelta(days=dias)
        col_fonte = "po.fonte" if self._coluna_fonte_existe() else "'cupom' as fonte"
        sel = f"""select po.mercado, po.valor_unitario_centavos, po.descricao_original,
                         po.data_compra, po.descricao_norm, po.unidade,
                         {col_fonte}, po.loja_id, l.nome, l.endereco, l.cidade, l.uf,
                         po.gtin
                  from precos_observados po
                  left join lojas l on l.id = po.loja_id"""

        gtin_limpo = re.sub(r"\D", "", gtin or "")
        prod_exato = (produto or "").strip()
        modo_lojas = bool(gtin_limpo or prod_exato)
        if gtin_limpo:
            sql = sel + " where po.gtin = %s and po.data_compra >= %s"
            params: list = [gtin_limpo, corte]
        elif prod_exato:
            sql = sel + " where po.descricao_norm = %s and po.data_compra >= %s"
            params = [normalizar(prod_exato), corte]
        else:
            nucleo = normalizar(descricao)
            primeira = nucleo.split()[0] if nucleo else ""
            if not primeira:
                return {"nivel": "produtos", "termo": descricao, "ocultos": 0, "opcoes": []}
            sql = sel + " where po.descricao_norm like %s and po.data_compra >= %s"
            params = [f"%{primeira}%", corte]
        if regiao:
            sql += " and (po.regiao ilike %s or po.regiao is null)"
            params.append(f"%{_cidade_core(regiao)}%")
        sql += " order by po.data_compra desc"
        with self.pool.connection() as c:
            rows = c.execute(sql, params).fetchall()

        alvo = tokens(descricao) if not modo_lojas else set()
        alvo_tam = tam(descricao) if not modo_lojas else set()
        need = max(2, (2 * len(alvo) + 2) // 3) if alvo else 0
        hoje = date.today()

        regs: list[dict] = []
        for (merc, vu, orig, dt, norm, unidade, fonte, loja_id,
             l_nome, endereco, l_cidade, l_uf, g) in rows:
            if not modo_lojas:
                if alvo_tam and not (alvo_tam & (tam(orig) | tam(norm))):
                    continue
                comuns = alvo & set((norm or "").split())
                if alvo and len(comuns) < min(need, len(alvo)):
                    continue
            fonte = fonte or "cupom"
            nome_loja = l_nome or merc or "(sem nome)"
            partes_end = [p for p in [endereco,
                          f"{l_cidade}/{l_uf}" if l_cidade and l_uf else None] if p]
            regs.append({
                "gtin": g, "descricao": orig, "norm": norm or "",
                "mercado": nome_loja, "fonte": fonte, "preco_centavos": int(vu),
                "loja_key": (f"id:{loja_id}" if loja_id else f"txt:{nome_loja}"),
                "data": dt.isoformat() if dt else None,
                "dias": (hoje - dt).days if dt else None,
                "unidade": unidade or "UN",
                "endereco": ", ".join(partes_end) or None,
            })

        # nível LOJAS (produto exato por gtin ou nome)
        if modo_lojas:
            por: dict[str, dict] = {}
            for r in regs:
                por.setdefault(f"{r['fonte']}:{r['loja_key']}", r)
            lojas = sorted(por.values(), key=lambda x: x["preco_centavos"])[:limite]
            nome_prod = lojas[0]["descricao"] if lojas else (produto or descricao)
            g_ret = gtin_limpo or (lojas[0]["gtin"] if lojas else None)
            for r in lojas:
                r.pop("norm", None); r.pop("loja_key", None); r.pop("gtin", None)
            return {"nivel": "lojas", "produto": nome_prod,
                    "gtin": g_ret, "opcoes": lojas}

        # nível PRODUTOS (agrupa por gtin OU nome normalizado)
        prod: dict[str, dict] = {}
        for r in regs:
            k = r["gtin"] or r["norm"]
            p = prod.get(k)
            if not p:
                prod[k] = {"gtin": r["gtin"], "descricao": r["descricao"],
                           "norm": r["norm"],
                           "preco_min_centavos": r["preco_centavos"],
                           "preco_max_centavos": r["preco_centavos"],
                           "lojas": {r["loja_key"]}, "fonte_min": r["fonte"]}
            else:
                if r["preco_centavos"] < p["preco_min_centavos"]:
                    p["preco_min_centavos"] = r["preco_centavos"]
                    p["fonte_min"] = r["fonte"]
                p["preco_max_centavos"] = max(p["preco_max_centavos"], r["preco_centavos"])
                p["lojas"].add(r["loja_key"])
                if len(r["descricao"]) < len(p["descricao"]):
                    p["descricao"] = r["descricao"]
        q0 = (normalizar(descricao).split() or [""])[0]

        def _e_nucleo(pnorm: str) -> bool:
            ws = (pnorm or "").split()
            return bool(ws) and ws[0] == q0

        def _relev(pnorm: str) -> int:
            ws = (pnorm or "").split()
            return ws.index(q0) if q0 in ws else 99

        todos = [{
            "gtin": p["gtin"], "descricao": p["descricao"],
            "preco_min_centavos": p["preco_min_centavos"],
            "preco_max_centavos": p["preco_max_centavos"],
            "n_lojas": len(p["lojas"]), "fonte": p["fonte_min"],
            "nucleo": _e_nucleo(p["norm"]), "_relev": _relev(p["norm"]),
        } for p in prod.values()]
        nucleo = sorted([x for x in todos if x["nucleo"]],
                        key=lambda x: (x["_relev"], x["preco_min_centavos"]))
        resto = sorted([x for x in todos if not x["nucleo"]],
                       key=lambda x: (x["_relev"], x["preco_min_centavos"]))
        if tudo or len(nucleo) < 3:
            visiveis, ocultos = nucleo + resto, 0
        else:
            visiveis, ocultos = nucleo, len(resto)
        for x in visiveis:
            x.pop("_relev", None)
        return {"nivel": "produtos", "termo": descricao,
                "ocultos": ocultos, "opcoes": visiveis[:limite]}

    def registrar_catalogo(self, registros: list[dict], regiao: str = "Teresina",
                           data_compra: date | None = None) -> int:
        """Grava preço de CATÁLOGO (referência online), fonte='catalogo'. Recebe os
        registros do ingestor (catalogo_carvalho.coletar(): descricao,
        preco_centavos, loja_nome, gtin, unidade).

        item_id tem FK pra itens_lancamento (só cupom tem item de lançamento),
        então catálogo grava com item_id NULL. Idempotência por REFRESH: apaga as
        linhas de catálogo DAQUELAS MESMAS lojas e reinsere as novas. Resultado:
          - NUNCA duplica produto (apaga antes de inserir);
          - SEMPRE atualiza o preço (reinsere o atual);
          - data_compra = hoje => sempre a ÚLTIMA data de atualização.
        Nunca toca em preço de cupom (filtra fonte='catalogo'). Sem migração.
        Devolve quantas linhas inseriu."""
        data_compra = data_compra or date.today()
        linhas = []
        mercados: set[str] = set()
        for r in registros:
            preco = r.get("preco_centavos")
            desc = (r.get("descricao") or "").strip()
            mercado = r.get("loja_nome") or r.get("mercado")
            if not desc or not preco or preco <= 0 or not mercado:
                continue
            mercados.add(mercado)
            linhas.append((normalizar(desc), desc[:200], int(preco), mercado,
                           regiao, r.get("gtin"), data_compra, r.get("unidade") or "UN"))
        if not linhas:
            return 0
        with self.pool.connection() as c:
            with c.cursor() as cur:
                # apaga SÓ o catálogo dessas lojas (cupom fica intacto)
                cur.execute(
                    "delete from precos_observados "
                    "where fonte = 'catalogo' and regiao = %s and mercado = any(%s)",
                    (regiao, list(mercados)),
                )
                cur.executemany(
                    """insert into precos_observados
                       (descricao_norm, descricao_original, valor_unitario_centavos,
                        mercado, regiao, gtin, data_compra, unidade,
                        conta_id, item_id, fonte)
                       values (%s,%s,%s,%s,%s,%s,%s,%s, null, null, 'catalogo')""",
                    linhas,
                )
            c.commit()
        return len(linhas)

    # ---------- comparador de CESTA ----------

    def comparar_cesta(self, itens: list[str], regiao: str | None = None,
                       dias: int = 90) -> dict:
        """Para uma lista de produtos, calcula quanto a CESTA sai em cada mercado.
        Retorna o ranking de mercados + detalhe item a item.

        Estrutura:
          {
            "mercados": [{"mercado","total_centavos","itens_cobertos","itens_faltando"}],
            "itens": [{"descricao","melhor_mercado","melhor_centavos","precos":[...]}],
            "observacoes": int,   # quantos precos sustentam a comparacao
          }
        """
        detalhe_itens = []
        # mercado -> {soma, cobertos, faltando[]}
        mercados: dict[str, dict] = {}
        total_obs = 0

        for desc in itens:
            precos = self.precos_de(desc, regiao=regiao, dias=dias, so_com_endereco=True)
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

        # quais itens faltam em cada mercado (nao tinha preco la')
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
        # ordena: mais itens cobertos primeiro, depois menor total
        ranking.sort(key=lambda x: (-x["itens_cobertos"], x["total_centavos"]))

        return {"mercados": ranking, "itens": detalhe_itens, "observacoes": total_obs}


def _mercado_da_descricao(lanc_desc: str | None) -> str | None:
    """Heuristica simples pra extrair o nome do mercado da descricao do
    lancamento (ex: 'Compra Carvalho', 'Mercado Assai'). Sem dado, retorna None."""
    if not lanc_desc:
        return None
    s = lanc_desc.strip()
    # remove palavras genericas comuns
    for ruido in ("compra", "compras", "mercado", "supermercado", "no ", "na ", "em "):
        s = re.sub(rf"\b{ruido}\b", "", s, flags=re.IGNORECASE).strip()
    return s.title()[:60] if s else None


def comparar_para_cidade(pool, itens: list[str], cidade: str | None) -> dict:
    """Comparador UNIFICADO: tenta a API SEFAZ (onde cobre, ex PR/PE) e cai no
    banco proprio (ex Teresina). Devolve a estrutura padrao + a chave 'fonte'
    ('sefaz' | 'proprio' | 'vazio') pra UI saber a origem.
    """
    from finance import cidades as cid

    coord = cid.coordenada(cidade)
    # 1) tenta SEFAZ se a cidade tem coordenada e fonte preferida sefaz
    if coord and cid.fonte(cidade) == "sefaz":
        try:
            from finance.sefaz_precos import SefazMenorPreco
            lat, lon, raio = coord
            r = SefazMenorPreco().comparar_cesta(itens, lat, lon, raio)
            if r["observacoes"] > 0:
                r["fonte"] = "sefaz"
                return r
        except Exception:  # noqa: BLE001
            pass
    # 2) banco proprio (regiao = cidade)
    r = BancoPrecos(pool).comparar_cesta(itens, regiao=(cidade or None))
    # se o banco proprio com regiao especifica vier vazio, tenta sem filtro de regiao
    if r["observacoes"] == 0:
        r = BancoPrecos(pool).comparar_cesta(itens, regiao=None)
    r["fonte"] = "proprio" if r["observacoes"] > 0 else "vazio"
    return r


def comparar_separado(pool, itens: list[str], cidade: str | None,
                      escolhas: dict[str, str] | None = None) -> dict:
    """Comparador separado por DEPARTAMENTO (mercado / farmacia / outro).

    `escolhas` permite AJUSTAR um item: mapa {item: termo_especifico}. O termo
    escolhido e' usado na busca no lugar do generico, dando precisao.

    Retorna {'fonte', 'grupos': {'mercado': {...}, 'farmacia': {...}}, 'observacoes'}.
    """
    escolhas = escolhas or {}
    termos = [escolhas.get(i, i) for i in itens]
    base = comparar_para_cidade(pool, termos, cidade)
    grupos: dict[str, dict] = {}
    for m in base.get("mercados", []):
        tipo = m.get("tipo") or "mercado"
        if tipo == "outro":
            tipo = "mercado"   # agrupa 'outro' com mercado (e' o caso comum)
        grupos.setdefault(tipo, {"mercados": []})["mercados"].append(m)
    # ja' vem ordenado; mantem top 3 por grupo
    for g in grupos.values():
        g["mercados"] = g["mercados"][:3]
    return {"fonte": base.get("fonte", "vazio"), "grupos": grupos,
            "observacoes": base.get("observacoes", 0), "itens": base.get("itens", [])}
