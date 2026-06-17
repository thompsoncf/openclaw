"""Livro-caixa de UMA conta (tenant).

Todo metodo e' escopado por conta_id: a conta so' enxerga e mexe no que e' dela
(isolamento sagrado do multi-tenant). membro_id marca QUEM lancou (auditoria).
"""
import logging
from datetime import date, timedelta

from .models import (
    Lancamento, Tipo, centavos_para_reais, formatar_brl,
)

_log = logging.getLogger("openclaw.precos")


def _intervalo_mes(ano: int, mes: int) -> tuple[date, date]:
    """Retorna (inicio, proximo_inicio) do mes. Usado pra filtrar por intervalo
    de data (data >= inicio and data < proximo) em vez de extract(), que NAO usa
    indice. Mesmo resultado, muito mais rapido com o indice (conta_id, data)."""
    inicio = date(ano, mes, 1)
    proximo = date(ano + 1, 1, 1) if mes == 12 else date(ano, mes + 1, 1)
    return inicio, proximo


# unidades vendidas por PESO/VOLUME (preco e' por kg/L) vs por UNIDADE
_UNID_PESO = {"KG", "KGS", "QUILO", "QUILOGRAMA", "G", "GR", "GRAMA", "GRAMAS"}
_UNID_VOLUME = {"L", "LT", "LTS", "LITRO", "LITROS", "ML"}


def _normalizar_unidade(u: str | None) -> str:
    """Normaliza a unidade do cupom pra uma forma curta padrao (UN, KG, L).
    Default 'UN' (a maioria dos itens e' por unidade)."""
    if not u:
        return "UN"
    t = str(u).strip().upper().replace(".", "")
    if t in _UNID_PESO:
        # padroniza grama em KG (preco/kg e' a base de comparacao)
        return "KG"
    if t in _UNID_VOLUME:
        return "L"
    if t in {"UN", "UND", "UNID", "UNIDADE", "PC", "PCT", "PCTE", "CX", "CXA",
             "DZ", "DUZIA", "FD", "FARDO", "PT", "PETE"}:
        return "UN"
    # desconhecido: mantem curto (3 chars) ou cai pra UN
    return t[:3] if t else "UN"


def _validar_item_preco(it: dict) -> tuple[int, float, int, str]:
    """CAMADA 2 (rede de seguranca do banco de ouro): a partir do que a IA leu,
    devolve (valor_unitario_centavos, quantidade, valor_total_centavos, unidade)
    COERENTES, usando a regra de ferro da NFC-e: vUnCom * qCom = vProd.

    Estrategia: a quantidade e o total sao geralmente os mais confiaveis. Se o
    unitario nao bate com total/quantidade, RECALCULA o unitario. Assim, mesmo
    se a IA confundir unitario com total (o bug da picanha), o banco grava certo.
    """
    qtd = it.get("quantidade", 1)
    try:
        qtd = float(qtd) if qtd is not None else 1.0
    except (TypeError, ValueError):
        qtd = 1.0
    if qtd <= 0:
        qtd = 1.0
    vu = int(it.get("valor_unitario_centavos", 0) or 0)
    vt = int(it.get("valor_total_centavos", 0) or 0)
    unidade = _normalizar_unidade(it.get("unidade"))

    # se nao temos total mas temos unitario+qtd, deriva total
    if vt <= 0 and vu > 0:
        vt = round(vu * qtd)
    # se nao temos unitario mas temos total+qtd, deriva unitario
    if vu <= 0 and vt > 0:
        vu = round(vt / qtd)
    # AMBOS presentes: checa coerencia (vu*qtd ~= vt). Tolerancia de 2% ou 2 cent.
    if vu > 0 and vt > 0 and qtd > 0:
        esperado = vu * qtd
        tol = max(2, esperado * 0.02)
        if abs(esperado - vt) > tol:
            # nao bate: confia em total/qtd (recalcula o unitario)
            # isso corrige o caso "IA pos o total no lugar do unitario"
            vu = round(vt / qtd)
    return int(vu), qtd, int(vt), unidade


class LivroCaixa:
    def __init__(self, pool, conta_id: int, membro_id: int | None = None):
        self.pool = pool
        self.conta_id = conta_id
        self.membro_id = membro_id
        self.chave_nfce_atual = None  # webhook pode setar isso pra que tools usem

    def lancamento_por_chave(self, chave: str | None) -> dict | None:
        """Consulta se ja' existe lancamento nesta conta com essa chave (NFC-e).
        Retorna {'id': int, 'data': date, 'valor': int, 'descricao': str} se encontrado.
        Retorna None se nao existe ou se chave e' None/vazia."""
        if not chave:
            return None
        with self.pool.connection() as conn:
            row = conn.execute(
                """select id, data, valor_centavos, descricao from lancamentos
                   where conta_id = %s and chave = %s limit 1""",
                (self.conta_id, chave),
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "data": row[1], "valor": int(row[2]), "descricao": row[3]}

    def adicionar(self, lanc: Lancamento, chave: str | None = None) -> Lancamento:
        # usa chave explícita, ou chave_nfce_atual se tiver sido setada pelo webhook
        chave_final = chave or self.chave_nfce_atual
        with self.pool.connection() as conn:
            row = conn.execute(
                """insert into lancamentos
                   (conta_id, membro_id, tipo, valor_centavos, categoria, descricao,
                    data, pagamento, origem, comprovante, chave)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (self.conta_id, self.membro_id, lanc.tipo.value, lanc.valor_centavos,
                 lanc.categoria, lanc.descricao, lanc.data, lanc.pagamento,
                 lanc.origem, lanc.comprovante, chave_final),
            ).fetchone()
            conn.commit()
            lanc.id = row[0]
            return lanc

    def listar(self, mes: int | None = None, ano: int | None = None, limite: int = 50) -> list[Lancamento]:
        sql = "select id, tipo, valor_centavos, categoria, descricao, data, pagamento, origem, comprovante from lancamentos where conta_id = %s"
        params: list = [self.conta_id]
        if ano:
            sql += " and extract(year from data) = %s"
            params.append(ano)
        if mes:
            sql += " and extract(month from data) = %s"
            params.append(mes)
        sql += " order by data desc, id desc limit %s"
        params.append(limite)
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            Lancamento(id=r[0], tipo=Tipo(r[1]), valor_centavos=r[2], categoria=r[3],
                       descricao=r[4], data=r[5], pagamento=r[6], origem=r[7], comprovante=r[8])
            for r in rows
        ]

    def saldo_centavos(self) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                """select
                     coalesce(sum(case when tipo='receita' then valor_centavos else 0 end),0)
                   - coalesce(sum(case when tipo='despesa' then valor_centavos else 0 end),0)
                   from lancamentos where conta_id = %s""",
                (self.conta_id,),
            ).fetchone()
        return int(row[0])

    def total_por_categoria(self, tipo: Tipo, mes: int | None = None, ano: int | None = None) -> dict[str, int]:
        sql = "select categoria, sum(valor_centavos) from lancamentos where conta_id = %s and tipo = %s"
        params: list = [self.conta_id, Tipo(tipo).value]
        if ano:
            sql += " and extract(year from data) = %s"
            params.append(ano)
        if mes:
            sql += " and extract(month from data) = %s"
            params.append(mes)
        sql += " group by categoria order by sum(valor_centavos) desc"
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def gastos_do_mes_centavos(self, ano: int, mes: int) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                """select coalesce(sum(valor_centavos),0) from lancamentos
                   where conta_id = %s and tipo='despesa'
                   and extract(year from data)=%s and extract(month from data)=%s""",
                (self.conta_id, ano, mes),
            ).fetchone()
        return int(row[0])

    # ---------- Dashboard do cliente (Bloco C) ----------

    def resumo_mes(self, ano: int, mes: int, membro_id: int | None = None) -> dict:
        """Saldo acumulado + receitas/despesas do mes (opcional: de UM membro)."""
        cond = "conta_id = %s"
        base: list = [self.conta_id]
        if membro_id is not None:
            cond += " and membro_id = %s"; base.append(membro_id)
        ini, prox = _intervalo_mes(ano, mes)
        with self.pool.connection() as conn:
            saldo = conn.execute(
                f"""select coalesce(sum(case when tipo='receita' then valor_centavos else -valor_centavos end),0)
                    from lancamentos where {cond}""", base).fetchone()[0]
            rec = conn.execute(
                f"""select coalesce(sum(valor_centavos),0) from lancamentos
                    where {cond} and tipo='receita'
                    and data >= %s and data < %s""",
                base + [ini, prox]).fetchone()[0]
            desp = conn.execute(
                f"""select coalesce(sum(valor_centavos),0) from lancamentos
                    where {cond} and tipo='despesa'
                    and data >= %s and data < %s""",
                base + [ini, prox]).fetchone()[0]
        return {"saldo": int(saldo), "receitas": int(rec), "despesas": int(desp)}

    def despesas_por_categoria(self, ano: int, mes: int, membro_id: int | None = None) -> list[tuple[str, int]]:
        return self._por_categoria("despesa", ano, mes, membro_id)

    def receitas_por_categoria(self, ano: int, mes: int, membro_id: int | None = None) -> list[tuple[str, int]]:
        return self._por_categoria("receita", ano, mes, membro_id)

    def _por_categoria(self, tipo: str, ano: int, mes: int,
                       membro_id: int | None = None) -> list[tuple[str, int]]:
        """Soma os lancamentos do tipo por categoria CANONICA (junta variacoes de
        grafia, ex Saude/Saude). Serve pra despesa e receita."""
        cond = "conta_id = %s and tipo=%s"
        params: list = [self.conta_id, tipo]
        if membro_id is not None:
            cond += " and membro_id = %s"; params.append(membro_id)
        ini, prox = _intervalo_mes(ano, mes)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select categoria, sum(valor_centavos) from lancamentos
                    where {cond} and data >= %s and data < %s
                    group by categoria order by sum(valor_centavos) desc""",
                params + [ini, prox]).fetchall()
        from .models import canonizar_categoria
        agg: dict[str, int] = {}
        for r in rows:
            cat = canonizar_categoria(r[0], tipo)
            agg[cat] = agg.get(cat, 0) + int(r[1])
        return sorted(agg.items(), key=lambda kv: kv[1], reverse=True)

    def mudar_categoria(self, lancamento_id: int, nova_categoria: str) -> bool:
        """Troca a categoria de UM lancamento (cliente corrigindo classificacao
        errada). Canoniza a nova categoria (so' aceita as padrao). So' mexe em
        lancamento DESTA conta (seguranca multi-tenant). Retorna True se mudou."""
        from .models import canonizar_categoria
        with self.pool.connection() as conn:
            row = conn.execute(
                "select tipo from lancamentos where id = %s and conta_id = %s",
                (lancamento_id, self.conta_id)).fetchone()
            if not row:
                return False   # nao existe ou nao e' desta conta
            tipo = row[0]
            cat = canonizar_categoria(nova_categoria, tipo)
            conn.execute(
                "update lancamentos set categoria = %s where id = %s and conta_id = %s",
                (cat, lancamento_id, self.conta_id))
            conn.commit()
        return True

    def apagar_lancamento(self, lancamento_id: int) -> bool:
        """Apaga um lancamento DESTA conta. Seguranca multi-tenant: so' apaga se
        for da propria conta. Limpa tambem os precos observados gerados por ele
        (item_id NAO tem cascade); os itens_lancamento somem por ON DELETE CASCADE.
        Retorna True se apagou, False se nao existe ou nao e' desta conta."""
        with self.pool.connection() as conn:
            dono = conn.execute(
                "select 1 from lancamentos where id = %s and conta_id = %s",
                (lancamento_id, self.conta_id)).fetchone()
            if not dono:
                return False
            # tira do banco de precos o que veio deste lancamento (item_id sem cascade)
            conn.execute(
                """delete from precos_observados where item_id in
                     (select id from itens_lancamento where lancamento_id = %s)""",
                (lancamento_id,))
            # apaga o lancamento; itens_lancamento somem por ON DELETE CASCADE
            cur = conn.execute(
                "delete from lancamentos where id = %s and conta_id = %s",
                (lancamento_id, self.conta_id))
            conn.commit()
            return cur.rowcount > 0

    def evolucao_mensal(self, meses: int = 6, membro_id: int | None = None) -> list[dict]:
        """Receitas e despesas dos ultimos N meses (pra grafico de tendencia)."""
        cond = "conta_id = %s"
        params: list = [self.conta_id]
        if membro_id is not None:
            cond += " and membro_id = %s"; params.append(membro_id)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select to_char(date_trunc('month', data), 'YYYY-MM') as mes,
                          coalesce(sum(case when tipo='receita' then valor_centavos else 0 end),0),
                          coalesce(sum(case when tipo='despesa' then valor_centavos else 0 end),0)
                    from lancamentos where {cond}
                    group by 1 order by 1 desc limit %s""",
                params + [meses]).fetchall()
        return [{"mes": r[0], "receitas": int(r[1]), "despesas": int(r[2])} for r in reversed(rows)]

    def lancamentos_recentes(self, ano: int, mes: int, membro_id: int | None = None,
                             tipo: str | None = None, limite: int = 50) -> list[dict]:
        ini, prox = _intervalo_mes(ano, mes)
        cond = "l.conta_id = %s and l.data >= %s and l.data < %s"
        params: list = [self.conta_id, ini, prox]
        if membro_id is not None:
            cond += " and l.membro_id = %s"; params.append(membro_id)
        if tipo in ("despesa", "receita"):
            cond += " and l.tipo = %s"; params.append(tipo)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select l.id, l.data, l.descricao, l.categoria, l.tipo, l.valor_centavos,
                          l.origem, coalesce(m.nome, '-') as quem
                    from lancamentos l left join membros m on m.id = l.membro_id
                    where {cond} order by l.data desc, l.id desc limit %s""",
                params + [limite]).fetchall()
        return [{"id": r[0], "data": r[1], "descricao": r[2], "categoria": r[3], "tipo": r[4],
                 "valor": int(r[5]), "origem": r[6], "quem": r[7]} for r in rows]

    def raiox_por_departamento(self, ano: int | None = None, mes: int | None = None,
                               membro_id: int | None = None,
                               dias: int | None = None) -> dict[str, list[dict]]:
        """Itens de cupom agrupados pelo DEPARTAMENTO (= categoria do lancamento).

        Filtra pela DATA do lancamento. Se ano/mes informados, usa o mes (corrige
        o bug de mostrar itens de outro mes). Senao, cai pra janela de `dias`.
        Cada item carrega a data do lancamento, pra UI dividir por dia.
        """
        cond = "l.conta_id = %s"
        params: list = [self.conta_id]
        if ano is not None and mes is not None:
            ini, prox = _intervalo_mes(ano, mes)
            cond += " and l.data >= %s and l.data < %s"
            params += [ini, prox]
        else:
            cond += " and l.data >= (now() - (%s || ' days')::interval)::date"
            params.append(dias or 90)
        if membro_id is not None:
            cond += " and l.membro_id = %s"; params.append(membro_id)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select l.categoria, i.descricao, i.valor_total_centavos, l.data
                    from itens_lancamento i join lancamentos l on l.id = i.lancamento_id
                    where {cond} order by l.categoria, l.data desc, i.valor_total_centavos desc""",
                params).fetchall()
        from .models import canonizar_categoria, DEPARTAMENTOS_RAIOX
        dep: dict[str, list[dict]] = {}
        for cat, desc, val, data in rows:
            cat_p = canonizar_categoria(cat, "despesa")
            if cat_p not in DEPARTAMENTOS_RAIOX:
                continue   # raio-x so' mostra a lista branca (Mercado, Saude, ...)
            dep.setdefault(cat_p, []).append({"descricao": desc, "valor": int(val), "data": data})
        return dep

    # ---------- Itens do cupom (raio-x do consumo) ----------

    def buscar_duplicata(self, valor_centavos: int, data) -> list[dict]:
        """Procura lancamentos iguais (mesma data e valor igual OU bem proximo)
        - sinal de cupom repetido. A tolerancia de centavos cobre pequenas
        variacoes na leitura do mesmo cupom."""
        tol = max(50, round(valor_centavos * 0.01))   # 1% ou R$0,50, o que for maior
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select id, descricao, data, criado_em, valor_centavos,
                          (select count(*) from itens_lancamento i
                           where i.lancamento_id = lancamentos.id) as qtd_itens
                   from lancamentos
                   where conta_id = %s and data = %s
                     and abs(valor_centavos - %s) <= %s
                   order by criado_em desc""",
                (self.conta_id, data, valor_centavos, tol),
            ).fetchall()
        return [{"id": r[0], "descricao": r[1], "data": r[2], "criado_em": r[3],
                 "valor_centavos": r[4], "qtd_itens": r[5]} for r in rows]

    def ultimo_lancamento_id(self) -> int | None:
        """Id do lancamento mais recente do usuario (pra anexar itens 'desse cupom')."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "select id from lancamentos where conta_id = %s order by id desc limit 1",
                (self.conta_id,),
            ).fetchone()
        return int(row[0]) if row else None

    def _achar_ou_criar_loja(self, conn, cnpj: str | None, nome: str | None,
                             endereco: str | None, cidade: str | None,
                             uf: str | None, ramo_reserva: str | None = None) -> int | None:
        """Acha a loja pelo CNPJ (identificador exato da filial) ou cria. Atualiza
        endereco/nome se vierem novos e os antigos estiverem vazios. Sem CNPJ,
        nao da' pra identificar a loja com seguranca -> retorna None."""
        cnpj = "".join(c for c in (cnpj or "") if c.isdigit())
        if len(cnpj) != 14:
            return None
        row = conn.execute("select id, endereco, nome from lojas where cnpj = %s",
                           (cnpj,)).fetchone()
        if row:
            loja_id, end_atual, nome_atual = row
            # completa dados faltantes (ex: 1a vez veio sem endereco, agora veio)
            if (endereco and not end_atual) or (nome and not nome_atual):
                conn.execute(
                    """update lojas set endereco = coalesce(nullif(%s,''), endereco),
                                        nome = coalesce(nullif(%s,''), nome)
                       where id = %s""",
                    (endereco or "", nome or "", loja_id))
            return loja_id
        # loja NOVA: se faltar nome ou endereco, tenta completar na Receita (BrasilAPI)
        ramo = ramo_reserva       # reserva: ramo derivado da categoria do Claude
        cnae = None
        if not nome or not endereco:
            try:
                from .cnpj_info import consultar_cnpj
                info = consultar_cnpj(cnpj)
                if info:
                    nome = nome or info.get("nome")
                    endereco = endereco or info.get("endereco")
                    cidade = cidade or info.get("cidade")
                    uf = uf or info.get("uf")
                    cnae = info.get("cnae")
                    # ramo do CNAE tem prioridade; senao mantem a reserva
                    ramo = info.get("ramo") or ramo
            except Exception:  # noqa: BLE001
                _log.warning("consulta CNPJ falhou para %s (cria loja sem completar)", cnpj)
        novo = conn.execute(
            """insert into lojas (cnpj, nome, endereco, cidade, uf, ramo, cnae)
               values (%s,%s,%s,%s,%s,%s,%s) returning id""",
            (cnpj, nome, endereco, cidade, uf, ramo, cnae)).fetchone()
        return novo[0] if novo else None

    def buscar_preco_observado(self, descricao: str, cidade: str | None = None,
                               dias: int = 90) -> tuple[int | None, str | None]:
        """Busca o melhor preco de um produto no BANCO DE PRECOS (multiplas fontes:
        cupom, api sefaz, etc). Prioriza: mesma cidade > mais recente. Retorna
        (centavos, fonte_texto) ou (None, None). Funciona pra qualquer conta -
        o banco de precos e' COLETIVO (o ouro), nao isolado por tenant."""
        from .models import normalizar_descricao
        from datetime import date, timedelta
        norm = normalizar_descricao(descricao)
        if not norm:
            return (None, None)
        corte = date.today() - timedelta(days=dias)
        with self.pool.connection() as conn:
            # 1) tenta na mesma cidade (regiao comeca com a cidade)
            row = None
            if cidade:
                row = conn.execute(
                    """select valor_unitario_centavos, mercado, fonte, data_compra
                       from precos_observados
                       where descricao_norm = %s and data_compra >= %s
                         and regiao ilike %s
                       order by data_compra desc limit 1""",
                    (norm, corte, f"{cidade}%"),
                ).fetchone()
            # 2) fallback: qualquer lugar, mais recente
            if not row:
                row = conn.execute(
                    """select valor_unitario_centavos, mercado, fonte, data_compra
                       from precos_observados
                       where descricao_norm = %s and data_compra >= %s
                       order by data_compra desc limit 1""",
                    (norm, corte),
                ).fetchone()
            if not row:
                return (None, None)
            centavos, mercado, fonte, data = row
            # monta texto da fonte pro cliente: "Carvalho, 11/06" ou "SEFAZ-PI"
            origem = "SEFAZ" if (fonte or "").startswith("api") else (mercado or "outro cliente")
            quando = data.strftime("%d/%m") if data else ""
            texto = f"{origem}, {quando}".strip(", ")
            return (int(centavos), texto)

    def registrar_itens(self, lancamento_id: int, itens: list[dict],
                        substituir: bool = False, loja_info: dict | None = None) -> int:
        """Salva os itens de um cupom, ligados a um lancamento do PROPRIO usuario.

        Cada item: {descricao, quantidade, valor_unitario_centavos, valor_total_centavos}.
        Retorna quantos itens foram salvos (0 se o lancamento nao for do usuario).

        Protecao contra DUPLICACAO: se o lancamento ja' tiver itens, por padrao
        NAO empilha de novo (retorna -1). Com substituir=True, troca os antigos
        pelos novos. Isso evita o "cupom com itens em dobro".
        """
        with self.pool.connection() as conn:
            dono = conn.execute(
                "select 1 from lancamentos where id = %s and conta_id = %s",
                (lancamento_id, self.conta_id),
            ).fetchone()
            if not dono:
                return 0
            ja = conn.execute(
                "select count(*) from itens_lancamento where lancamento_id = %s",
                (lancamento_id,),
            ).fetchone()[0]
            if ja and not substituir:
                return -1                      # ja tem itens: nao duplica
            if ja and substituir:
                conn.execute("delete from itens_lancamento where lancamento_id = %s",
                             (lancamento_id,))
            # Grava em LOTES (executemany): robusto pra 10 ou 3000 itens.
            # CAMADA 2: valida/corrige unitario, qtd, total e unidade de cada item.
            params = []
            for it in itens:
                vu, qtd, vt, unidade = _validar_item_preco(it)
                cod = str(it.get("codigo") or "").strip() or None
                params.append((lancamento_id, it["descricao"], qtd, vu, vt, unidade, cod))
            _INSERT_ITEM = """insert into itens_lancamento
                       (lancamento_id, descricao, quantidade,
                        valor_unitario_centavos, valor_total_centavos, unidade, codigo)
                       values (%s,%s,%s,%s,%s,%s,%s)"""
            n = 0
            try:
                # caminho rapido: tenta o lote inteiro de uma vez
                conn.cursor().executemany(_INSERT_ITEM, params)
                n = len(params)
            except Exception:  # noqa: BLE001 - um item torto NAO pode derrubar o cupom
                conn.rollback()
                _log.warning("insert de itens em lote falhou (lanc %s); tentando item a item",
                             lancamento_id)
                # item a item: salva os bons, pula (e LOGA) o que falhar
                for row in params:
                    try:
                        with conn.transaction():       # savepoint por item
                            conn.cursor().execute(_INSERT_ITEM, row)
                        n += 1
                    except Exception:  # noqa: BLE001
                        _log.exception("item ignorado (insert falhou) lanc %s: %r",
                                       lancamento_id, row)
            conn.commit()
            # alimenta o BANCO DE PRECOS (ouro): cada item com valor unitario
            # vira um preco observado, agrupado por cidade+mercado.
            try:
                self._observar_precos(conn, lancamento_id, loja_info)
            except Exception:  # noqa: BLE001
                # coleta de preco nunca quebra o registro do cupom, mas LOGA
                _log.exception("falha ao observar precos do lancamento %s", lancamento_id)
            return n

    def _observar_precos(self, conn, lancamento_id: int, loja_info: dict | None = None) -> int:
        """Grava os itens do cupom no banco coletivo de precos (precos_observados),
        pra alimentar comparacoes futuras. Agrupado por cidade (da conta) + mercado
        (descricao do lancamento). Le os itens JA' salvos (com seus ids), entao o
        indice unico idx_precos_item protege contra duplicacao. Tolerante."""
        from .models import normalizar_descricao
        from .cnpj_info import ramo_por_categoria
        info = conn.execute(
            """select l.descricao, l.data, c.cidade, l.categoria
               from lancamentos l join contas c on c.id = l.conta_id
               where l.id = %s""",
            (lancamento_id,),
        ).fetchone()
        if not info:
            return 0
        mercado, data_compra, cidade = info[0] or None, info[1], info[2] or None
        categoria = info[3] or None
        regiao = " / ".join(p for p in (cidade, mercado) if p) or None
        # identifica a LOJA pelo CNPJ (do QR/cupom) - diferencia filiais do grupo
        loja_id = None
        li = loja_info or {}
        cnpj = "".join(c for c in (li.get("cnpj") or "") if c.isdigit())
        # fallback: CNPJ embutido na chave do QR (posicoes 7-20), ja' lida na trava
        # de duplicidade. Garante a loja mesmo quando o agente nao leu o cabecalho.
        if len(cnpj) != 14:
            ch = "".join(c for c in str(getattr(self, "chave_nfce_atual", "") or "")
                         if c.isdigit())
            if len(ch) == 44:
                cnpj = ch[6:20]
        if len(cnpj) == 14:
            try:
                # extrai UF da cidade se vier "Teresina-PI"
                uf = None
                if cidade and "-" in cidade:
                    uf = cidade.rsplit("-", 1)[-1].strip().upper()[:2]
                loja_id = self._achar_ou_criar_loja(
                    conn, cnpj,
                    li.get("nome") or mercado,
                    li.get("endereco"), cidade, uf,
                    ramo_reserva=ramo_por_categoria(categoria))
            except Exception:  # noqa: BLE001
                _log.exception("falha ao achar/criar loja (cnpj=%s)", cnpj)
                loja_id = None
        # le os itens recem-salvos desse lancamento (com id, valor unitario e unidade)
        itens = conn.execute(
            """select id, descricao, valor_unitario_centavos, unidade, codigo
               from itens_lancamento where lancamento_id = %s""",
            (lancamento_id,),
        ).fetchall()
        params = []
        vistos = set()      # (descricao_norm, valor) ja' vistos NESTE lancamento
        for item_id, desc, vu, unidade, codigo in itens:
            vu = int(vu or 0)
            desc = (desc or "").strip()
            if vu <= 0 or not desc:
                continue
            norm = normalizar_descricao(desc)
            # nome de exibicao limpo (tira desconto grudado, espacos, etc)
            from .models import limpar_nome_produto
            desc_limpo = limpar_nome_produto(desc) or desc
            chave = (norm, vu)
            if chave in vistos:
                continue        # mesmo produto+preco no mesmo cupom: 1 preco basta
            vistos.add(chave)
            cod = "".join(ch for ch in (codigo or "") if ch.isdigit())
            gtin = cod if len(cod) in (8, 12, 13, 14) else None  # so' GTIN valido
            params.append((norm, desc_limpo, vu, mercado, regiao,
                           data_compra, self.conta_id, item_id, "cupom", loja_id,
                           unidade or "UN", gtin))
        if not params:
            return 0
        cur = conn.cursor()
        cur.executemany(
            """insert into precos_observados
               (descricao_norm, descricao_original, valor_unitario_centavos,
                mercado, regiao, data_compra, conta_id, item_id, fonte, loja_id,
                unidade, gtin)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (item_id) do nothing""",
            params,
        )
        conn.commit()
        return len(params)

    def buscar_itens(self, termo: str, dias: int = 60) -> tuple[list[dict], int]:
        """Busca itens cuja descricao casa com 'termo' (nos ultimos N dias).

        Retorna (lista_de_itens, total_centavos).
        """
        corte = date.today() - timedelta(days=dias)
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select i.descricao, i.quantidade, i.valor_total_centavos, l.data, i.criado_em
                   from itens_lancamento i join lancamentos l on l.id = i.lancamento_id
                   where l.conta_id = %s and i.descricao ilike %s and l.data >= %s
                   order by l.data desc, i.id desc""",
                (self.conta_id, f"%{termo}%", corte),
            ).fetchall()
        itens = [{"descricao": r[0], "quantidade": float(r[1]),
                  "valor_total_centavos": int(r[2]), "data": r[3], "criado_em": r[4]} for r in rows]
        total = sum(i["valor_total_centavos"] for i in itens)
        return itens, total

    def listar_itens(self, dias: int = 60, limite: int = 200) -> list[dict]:
        """Lista os itens dos ultimos N dias (pra perguntas por grupo, ex: 'frutas')."""
        corte = date.today() - timedelta(days=dias)
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select i.descricao, i.quantidade, i.valor_total_centavos, l.data, i.criado_em
                   from itens_lancamento i join lancamentos l on l.id = i.lancamento_id
                   where l.conta_id = %s and l.data >= %s
                   order by l.data desc, i.id desc limit %s""",
                (self.conta_id, corte, limite),
            ).fetchall()
        return [{"descricao": r[0], "quantidade": float(r[1]),
                 "valor_total_centavos": int(r[2]), "data": r[3], "criado_em": r[4]} for r in rows]
