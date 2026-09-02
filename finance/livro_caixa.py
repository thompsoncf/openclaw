"""Livro-caixa de UMA conta (tenant).

Todo metodo e' escopado por conta_id: a conta so' enxerga e mexe no que e' dela
(isolamento sagrado do multi-tenant). membro_id marca QUEM lancou (auditoria).
"""
import logging
from datetime import date, timedelta
from finance.relogio import hoje as hoje_br

from . import ofx_import as _ofx
from .models import (
    Lancamento, Tipo, centavos_para_reais, formatar_brl, normalizar_categoria,
)

_log = logging.getLogger("openclaw.precos")


def _intervalo_mes(ano: int, mes: int) -> tuple[date, date]:
    """Retorna (inicio, proximo_inicio) do mes. Usado pra filtrar por intervalo
    de data (data >= inicio and data < proximo) em vez de extract(), que NAO usa
    indice. Mesmo resultado, muito mais rapido com o indice (conta_id, data)."""
    inicio = date(ano, mes, 1)
    proximo = date(ano + 1, 1, 1) if mes == 12 else date(ano, mes + 1, 1)
    return inicio, proximo


def _somar_meses(d: date, n: int) -> date:
    """Soma n meses a uma data e devolve o 1o dia do mes resultante (competencia).
    Ex: _somar_meses(2026-03-15, 2) -> 2026-05-01. Usado pra espalhar as parcelas
    do cartao pelos meses seguintes."""
    total = d.year * 12 + (d.month - 1) + n
    ano, mes0 = divmod(total, 12)
    return date(ano, mes0 + 1, 1)


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


def _chave_dedupe_item(descricao, valor_total_centavos, codigo) -> tuple:
    """Chave de identidade de um item DENTRO de um mesmo cupom, pra nao duplicar
    quando o cupom chega em VARIAS FOTOS/lotes (o usuario reenvia uma parte, ou
    as fotos se sobrepoem). Usa codigo de barras quando ha (identidade forte),
    senao descricao normalizada - SEMPRE combinado com o valor total pago no item.

    O valor total entra na chave DE PROPOSITO: o MESMO produto pode aparecer em
    DUAS linhas distintas do cupom (ex: Sprite 1un a 3,09 e Sprite 5un a 15,45,
    mesmo EAN) - sao itens diferentes e os dois tem que ser salvos. So' colapsa
    quando codigo/descricao E total batem (isso sim e' reenvio da mesma linha).

    Nao e' uma chave global (dois cupons podem ter o mesmo item) - so' faz sentido
    comparada aos itens do MESMO lancamento. Usada so' no modo anexar."""
    from .models import normalizar_descricao

    def _txt(v) -> str:
        if v is None:
            return ""
        if isinstance(v, (bytes, bytearray, memoryview)):
            return bytes(v).decode("utf-8", "ignore")
        return str(v)

    try:
        vt = int(valor_total_centavos or 0)
    except (TypeError, ValueError):
        vt = 0
    cod = "".join(c for c in _txt(codigo) if c.isdigit())
    if len(cod) >= 8:
        return ("cod", cod, vt)
    return ("desc", normalizar_descricao(_txt(descricao)), vt)


def _cond_natureza(cond: str, params: list, natureza: str | None, col: str = "natureza"):
    """Acrescenta filtro de natureza a uma query. 'a_definir' => null;
    'pessoal'/'empresa' => igual; None/'todos' => sem filtro."""
    if natureza == "a_definir":
        cond += f" and {col} is null"
    elif natureza in ("pessoal", "empresa"):
        cond += f" and {col} = %s"; params.append(natureza)
    return cond, params


class LivroCaixa:
    def __init__(self, pool, conta_id: int, membro_id: int | None = None):
        self.pool = pool
        self.conta_id = conta_id
        self.membro_id = membro_id
        self.chave_nfce_atual = None  # webhook pode setar isso pra que tools usem

    def lancamento_por_chave(self, chave: str | None, global_: bool = False) -> dict | None:
        """Consulta se ja' existe lancamento com essa chave (NFC-e).

        Se global_=False (default): busca SÓ nesta conta.
        Se global_=True: busca em TODAS as contas (pra decidir se replica).

        Retorna {'id': int, 'data': date, 'valor': int, 'descricao': str, 'conta_id': int}
        se encontrado. Retorna None se nao existe ou se chave e' None/vazia."""
        if not chave:
            return None
        with self.pool.connection() as conn:
            if global_:
                row = conn.execute(
                    """select id, data, valor_centavos, descricao, conta_id from lancamentos
                       where chave = %s limit 1""",
                    (chave,),
                ).fetchone()
            else:
                row = conn.execute(
                    """select id, data, valor_centavos, descricao, conta_id from lancamentos
                       where conta_id = %s and chave = %s limit 1""",
                    (self.conta_id, chave),
                ).fetchone()
        if not row:
            return None
        return {"id": row[0], "data": row[1], "valor": int(row[2]), "descricao": row[3], "conta_id": row[4]}

    def replicar_cupom(self, chave: str | None) -> dict | None:
        """Replica um cupom JA' PARSEADO (com itens) pra ESTA conta, SEM chamar a
        API de visao. Copia o lancamento + os itens de qualquer conta que ja' tenha
        esse cupom (mesma chave) COM itens. NAO re-observa precos (o parse original
        ja' alimentou o banco; replicar nao pode inflar o 'visto N x'). Retorna
        {'lancamento_id','n_itens','descricao','valor'} ou None se nao houver
        cupom-fonte com itens - nesse caso o fluxo normal deve parsear."""
        if not chave:
            return None
        with self.pool.connection() as conn:
            fonte = conn.execute(
                """select l.id, l.tipo, l.valor_centavos, l.categoria, l.descricao,
                          l.data, l.pagamento
                     from lancamentos l
                    where l.chave = %s
                      and exists (select 1 from itens_lancamento il
                                  where il.lancamento_id = l.id)
                    order by l.id limit 1""",
                (chave,),
            ).fetchone()
            if not fonte:
                return None     # nenhum cupom-fonte COM itens -> deixa parsear
            src_id, tipo, valor, categoria, descricao, data, pagamento = fonte
            novo = conn.execute(
                """insert into lancamentos
                     (conta_id, membro_id, tipo, valor_centavos, categoria, descricao,
                      data, pagamento, origem, comprovante, chave)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,'replica','',%s) returning id""",
                (self.conta_id, self.membro_id, tipo, valor, categoria, descricao,
                 data, pagamento, chave),
            ).fetchone()
            novo_id = novo[0]
            # copia os itens (1 INSERT ... SELECT). SEM _observar_precos de proposito.
            conn.execute(
                """insert into itens_lancamento
                     (lancamento_id, descricao, quantidade, valor_unitario_centavos,
                      valor_total_centavos, unidade, codigo)
                   select %s, descricao, quantidade, valor_unitario_centavos,
                          valor_total_centavos, unidade, codigo
                     from itens_lancamento where lancamento_id = %s""",
                (novo_id, src_id),
            )
            n = conn.execute(
                "select count(*) from itens_lancamento where lancamento_id = %s",
                (novo_id,),
            ).fetchone()[0]
            conn.commit()
        return {"lancamento_id": novo_id, "n_itens": int(n),
                "descricao": descricao, "valor": int(valor)}


    def _lancamento_recente_igual(self, lanc: Lancamento, janela_min: int = 10) -> dict | None:
        """Procura um lancamento IDENTICO criado ha' pouco NESTA conta: mesmo tipo,
        valor EXATO, mesma data e MESMA DESCRICAO, dentro dos ultimos `janela_min`
        minutos. E' a assinatura do re-registro acidental (varios comprovantes na
        mesma rajada; o modelo, numa msg de resumo, chama lancar de novo pro mesmo
        valor). Retorna o dict do existente ou None.

        A DESCRICAO ENTROU NA REGUA EM 02/09/2026, e o motivo e' folha de prestador.
        Antes a regra era so' tipo+valor+data, ignorando a descricao de proposito
        (o agente as vezes muda o sufixo). Em 01/09 a Prime pagou dois prestadores
        de R$ 1.500 com 38 SEGUNDOS de diferenca — Pedro Yan as 17:07:30 e Thiago
        logo em seguida — e o segundo foi tratado como re-registro do primeiro. O
        dinheiro sumiu, e o agente ainda respondeu "Registrado ✅".

        Isso nao e' caso raro: os titulos da conta dizem 1a/2a QUINZENA THIAGO e
        1a/2a QUINZENA PEDRO YAN, R$ 1.500 cada, mesmo vencimento. Pagar varios
        prestadores o mesmo valor no mesmo dia E' a folha; a regra antiga chamava
        isso de duplicata.

        A comparacao e' por CONTINENCIA e nao por igualdade exata, pra nao perder o
        caso que a regra antiga protegia: "Almoco no Assai" e "Almoco no Assai Banco
        do Brasil" — um contem o outro, continua sendo duplicata. "…Pedro Yan…" e
        "…Thiago…" nao se contem, entao sao dois pagamentos.

        Descricao vazia dos dois lados cai no comportamento antigo (bloqueia): sem
        texto nao da pra distinguir, e barrar de menos duplicaria dinheiro. O que
        sobra de falso positivo — dois pagamentos diferentes com a MESMA descricao —
        deixou de ser silencioso: quem chama avisa em vez de confirmar (ver o
        `duplicado` no `adicionar` e o uso dele em finance/tools.py).
        """
        with self.pool.connection() as conn:
            row = conn.execute(
                """select id, descricao, valor_centavos, data, criado_em
                   from lancamentos
                   where conta_id = %s and tipo = %s and valor_centavos = %s
                     and data = %s
                     and criado_em >= now() - (%s || ' minutes')::interval
                   order by criado_em desc limit 1""",
                (self.conta_id, lanc.tipo.value, lanc.valor_centavos, lanc.data, janela_min),
            ).fetchone()
        if row and not descricoes_sao_a_mesma(row[1], lanc.descricao):
            return None
        if not row:
            return None
        return {"id": row[0], "descricao": row[1], "valor_centavos": int(row[2]),
                "data": row[3], "criado_em": row[4]}

    def adicionar(self, lanc: Lancamento, chave: str | None = None,
                  forcar: bool = False, janela_dedup_min: int = 10,
                  conn=None) -> Lancamento:
        # usa chave explícita, ou chave_nfce_atual se tiver sido setada pelo webhook
        chave_final = chave or self.chave_nfce_atual
        # TRAVA DE IDEMPOTENCIA (camada de dados, nao depende do modelo lembrar de
        # checar). Comprovante de Pix/TED NAO tem chave NFC-e, entao a trava por
        # chave nao o protege; aqui pegamos o re-registro acidental por tipo+valor+
        # data dentro de uma janela curta. So' vale pra lancamento SEM chave (cupom
        # tem chave e fluxo proprio de dedup). forcar=True pula a trava - pra quando
        # o usuario confirma EXPLICITAMENTE que e' uma transacao diferente de mesmo
        # valor no mesmo dia. Marca lanc.duplicado pro chamador avisar em vez de
        # confirmar (e devolve o id do lancamento que JA' existia).
        if not forcar and not chave_final:
            existente = self._lancamento_recente_igual(lanc, janela_dedup_min)
            if existente:
                _log.info("dedup: %s ja' registrado ha' pouco (id=%s, %s) - nao dupliquei",
                          formatar_brl(lanc.valor_centavos), existente["id"], lanc.data)
                lanc.id = existente["id"]
                lanc.duplicado = True
                return lanc
        # SQL do insert (reutilizado com conexão própria OU externa)
        _sql = """insert into lancamentos
                   (conta_id, membro_id, tipo, valor_centavos, categoria, descricao,
                    data, pagamento, forma_pagamento, origem, comprovante, chave,
                    natureza, plano_conta_id, centro_custo_id)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id"""
        _args = (self.conta_id, self.membro_id, lanc.tipo.value, lanc.valor_centavos,
                 lanc.categoria, lanc.descricao, lanc.data, lanc.pagamento,
                 lanc.forma_pagamento, lanc.origem, lanc.comprovante, chave_final,
                 lanc.natureza, lanc.plano_conta_id, lanc.centro_custo_id)

        # conn EXTERNO: o chamador controla a transação (NÃO commita aqui). Serve pra
        # operações que precisam ser atômicas com outros inserts (ex.: pagar_folha,
        # onde ledger + folha_eventos têm que entrar juntos ou não entrar).
        if conn is not None:
            lanc.id = conn.execute(_sql, _args).fetchone()[0]
            lanc.duplicado = False
            return lanc

        # conn PRÓPRIO: comportamento de sempre (abre, insere, commita).
        with self.pool.connection() as own:
            lanc.id = own.execute(_sql, _args).fetchone()[0]
            own.commit()
            lanc.duplicado = False
            return lanc

    def listar(self, mes: int | None = None, ano: int | None = None, limite: int = 50) -> list[Lancamento]:
        sql = ("select id, tipo, valor_centavos, categoria, descricao, data, "
               "pagamento, forma_pagamento, origem, comprovante "
               "from lancamentos where conta_id = %s")
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
                       descricao=r[4], data=r[5], pagamento=r[6], forma_pagamento=r[7],
                       origem=r[8], comprovante=r[9])
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

    # ─────────────────────────────────────────────────────────────────────
    # Cartao de credito parcelado: compra vira 1 despesa (parcela do mes) +
    # previsao das parcelas futuras (fora do saldo ate' vencerem).
    # ─────────────────────────────────────────────────────────────────────
    @staticmethod
    def _rotulo_parcela(descricao: str, num: int, total: int) -> str:
        """Anexa 'N/TOTAL' na descricao pra ficar claro no extrato. Ex:
        'Geladeira' -> 'Geladeira (1/12)'. Sem descricao, so' o marcador."""
        marca = f"({num}/{total})"
        base = (descricao or "").strip()
        return f"{base} {marca}".strip()

    def registrar_compra_parcelada(self, *, valor_total_centavos: int, parcelas: int,
                                   categoria: str, descricao: str = "",
                                   data_compra: date | None = None) -> dict:
        """Registra uma compra PARCELADA no cartao de credito (modelo fatura):
        - a 1a parcela (mes da compra) vira DESPESA agora, entra no saldo;
        - as parcelas 2..N ficam em parcelas_cartao como 'previsto' e NAO entram
          no saldo ate' a competencia chegar (viram despesa via
          materializar_parcelas_devidas).
        Divide o total em centavos sem perder resto (o resto vai na 1a parcela).
        Retorna resumo pro chamador confirmar pro usuario.
        """
        import uuid
        parcelas = max(1, int(parcelas))
        total = int(valor_total_centavos)
        if total < 0:
            raise ValueError("valor_total_centavos nao pode ser negativo")
        # hoje_br e não date.today: servidor em UTC vira o dia às 21h BRT
        data_compra = data_compra or hoje_br()
        categoria = normalizar_categoria(Tipo.DESPESA, categoria)
        base = total // parcelas
        resto = total - base * parcelas
        # resto (centavos) somado na 1a parcela: soma das parcelas == total exato
        valores = [base + (resto if i == 0 else 0) for i in range(parcelas)]
        grupo = uuid.uuid4()
        comp0 = data_compra.replace(day=1)

        # 1a parcela -> despesa agora (entra no saldo). forcar: e' intencional.
        lanc = Lancamento(
            tipo=Tipo.DESPESA, valor_centavos=valores[0], categoria=categoria,
            descricao=self._rotulo_parcela(descricao, 1, parcelas),
            data=data_compra, forma_pagamento="credito",
        )
        salvo = self.adicionar(lanc, forcar=True)

        # grava as N parcelas (1a como 'lancado', futuras como 'previsto')
        _ins = """insert into parcelas_cartao
                    (conta_id, membro_id, grupo, descricao, categoria,
                     valor_centavos, parcela_num, parcela_total, competencia,
                     status, lancamento_id)
                  values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
        with self.pool.connection() as conn:
            for i in range(parcelas):
                num = i + 1
                comp = _somar_meses(comp0, i)
                if i == 0:
                    conn.execute(_ins, (self.conta_id, self.membro_id, grupo,
                                        descricao, categoria, valores[i], num,
                                        parcelas, comp, "lancado", salvo.id))
                else:
                    conn.execute(_ins, (self.conta_id, self.membro_id, grupo,
                                        descricao, categoria, valores[i], num,
                                        parcelas, comp, "previsto", None))
            conn.commit()

        return {
            "grupo": str(grupo),
            "total_centavos": total,
            "parcelas": parcelas,
            "valor_parcela_centavos": valores[0],  # 1a (pode ter o resto)
            "valor_parcela_regular_centavos": base,
            "primeira_lancada_id": salvo.id,
            "descricao": descricao,
            "categoria": categoria,
        }

    def materializar_parcelas_devidas(self, hoje: date | None = None) -> int:
        """Promove a DESPESA toda parcela 'previsto' cuja competencia ja' chegou
        (competencia <= 1o dia do mes atual). Idempotente: so' mexe em 'previsto'.
        Roda barato a cada interacao/relatorio pra manter 'quanto gastei' honesto
        mes a mes, sem depender de agendador. Retorna quantas materializou."""
        hoje = hoje or hoje_br()
        comp_atual = hoje.replace(day=1)
        _ins = """insert into lancamentos
                    (conta_id, membro_id, tipo, valor_centavos, categoria,
                     descricao, data, pagamento, forma_pagamento, origem,
                     comprovante, chave, natureza)
                  values (%s,%s,'despesa',%s,%s,%s,%s,'','credito','parcela','',
                          null,null) returning id"""
        n = 0
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select id, membro_id, descricao, categoria, valor_centavos,
                          parcela_num, parcela_total, competencia
                     from parcelas_cartao
                    where conta_id=%s and status='previsto' and competencia <= %s
                    order by competencia, parcela_num
                    for update skip locked""",
                (self.conta_id, comp_atual),
            ).fetchall()
            for r in rows:
                pid, membro_id, desc, cat, val, num, tot, comp = r
                rot = self._rotulo_parcela(desc, num, tot)
                novo_id = conn.execute(
                    _ins, (self.conta_id, membro_id, val, cat, rot, comp),
                ).fetchone()[0]
                conn.execute(
                    "update parcelas_cartao set status='lancado', lancamento_id=%s "
                    "where id=%s and status='previsto'",
                    (novo_id, pid),
                )
                n += 1
            conn.commit()
        return n

    def previsao_cartao(self, meses: int = 6, hoje: date | None = None) -> dict:
        """Previsao da FATURA do cartao: soma das parcelas 'previsto' por mes de
        competencia, dos proximos `meses` meses (a partir do mes atual). NAO
        materializa nada - so' olha o futuro. Retorna total e pontos por mes."""
        hoje = hoje or hoje_br()
        ini = hoje.replace(day=1)
        fim = _somar_meses(ini, max(1, int(meses)))  # exclusivo
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select competencia, sum(valor_centavos), count(*)
                     from parcelas_cartao
                    where conta_id=%s and status='previsto'
                      and competencia >= %s and competencia < %s
                    group by competencia order by competencia""",
                (self.conta_id, ini, fim),
            ).fetchall()
        pontos = [{"competencia": r[0], "total_centavos": int(r[1]),
                   "parcelas": int(r[2])} for r in rows]
        total = sum(p["total_centavos"] for p in pontos)
        return {"total_centavos": total, "meses": int(meses), "pontos": pontos}

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

    def resumo_mes(self, ano: int, mes: int, membro_id: int | None = None,
                   natureza: str | None = None) -> dict:
        """Extrato do mes: saldo anterior (acumulado ate' o fim do mes passado)
        + receitas/despesas do mes = saldo ao FIM do mes selecionado.
        A conta sempre fecha: anterior + receitas - despesas = saldo.
        (opcional: de UM membro)."""
        cond = "conta_id = %s"
        base: list = [self.conta_id]
        if membro_id is not None:
            cond += " and membro_id = %s"; base.append(membro_id)
        cond, base = _cond_natureza(cond, base, natureza)
        ini, prox = _intervalo_mes(ano, mes)
        with self.pool.connection() as conn:
            anterior = conn.execute(
                f"""select coalesce(sum(case when tipo='receita' then valor_centavos else -valor_centavos end),0)
                    from lancamentos where {cond} and data < %s""",
                base + [ini]).fetchone()[0]
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
        anterior = int(anterior); rec = int(rec); desp = int(desp)
        return {"anterior": anterior, "receitas": rec, "despesas": desp,
                "saldo": anterior + rec - desp}

    def resumo_mes_quebra(self, ano: int, mes: int, membro_id: int | None = None) -> dict:
        """resumo_mes + quebra por natureza numa conexao so (2 queries com group by, no
        lugar de 4x resumo_mes). Retorna {'total':..., 'empresa':..., 'pessoal':...,
        'a_definir':...}, cada bloco = {anterior, receitas, despesas, saldo}. Equivale a
        resumo_mes() (total) e resumo_mes(natureza=X) para cada natureza."""
        cond = "conta_id = %s"
        base: list = [self.conta_id]
        if membro_id is not None:
            cond += " and membro_id = %s"; base.append(membro_id)
        ini, prox = _intervalo_mes(ano, mes)
        blocos = {n: {"anterior": 0, "receitas": 0, "despesas": 0}
                  for n in ("empresa", "pessoal", "a_definir")}
        with self.pool.connection() as conn:
            for nat, val in conn.execute(
                f"""select coalesce(natureza,'a_definir'),
                           coalesce(sum(case when tipo='receita' then valor_centavos
                                             else -valor_centavos end),0)
                    from lancamentos where {cond} and data < %s
                    group by coalesce(natureza,'a_definir')""",
                base + [ini]).fetchall():
                if nat in blocos:
                    blocos[nat]["anterior"] = int(val)
            for nat, tipo, val in conn.execute(
                f"""select coalesce(natureza,'a_definir'), tipo, coalesce(sum(valor_centavos),0)
                    from lancamentos where {cond} and data >= %s and data < %s
                    group by coalesce(natureza,'a_definir'), tipo""",
                base + [ini, prox]).fetchall():
                if nat in blocos:
                    blocos[nat]["receitas" if tipo == "receita" else "despesas"] = int(val)
        total = {"anterior": 0, "receitas": 0, "despesas": 0}
        for b in blocos.values():
            b["saldo"] = b["anterior"] + b["receitas"] - b["despesas"]
            total["anterior"] += b["anterior"]
            total["receitas"] += b["receitas"]
            total["despesas"] += b["despesas"]
        total["saldo"] = total["anterior"] + total["receitas"] - total["despesas"]
        return {"total": total, **blocos}

    def despesas_por_categoria(self, ano: int, mes: int, membro_id: int | None = None,
                               natureza: str | None = None) -> list[tuple[str, int]]:
        return self._por_categoria("despesa", ano, mes, membro_id, natureza)

    def receitas_por_categoria(self, ano: int, mes: int, membro_id: int | None = None,
                               natureza: str | None = None) -> list[tuple[str, int]]:
        return self._por_categoria("receita", ano, mes, membro_id, natureza)

    def receitas_em_dois_blocos(self, ano: int, mes: int, membro_id: int | None = None,
                                natureza: str | None = None) -> dict:
        """As receitas separadas em DUAS perguntas, que a lista unica misturava:

          operacional     -> quanto o NEGOCIO faturou (entra no resultado)
          nao_operacional -> quanto ENTROU no caixa sem ser faturamento
                             (aporte de socio, emprestimo, estorno, transferencia)

        Somar as duas coisas fazia o numero que o dono usa pra julgar o negocio
        subir e descer conforme ele precisou cobrir caixa. Em producao, 24/08/2026:
        de R$ 249 mil "de receita", R$ 3.200 eram aporte de socio espalhado por
        tres categorias — uma delas "Presentes".

        Devolve os dois totais junto porque a tela mostra os dois no cabecalho, e
        recalcular somando na view e' onde os dois numeros comecam a divergir.
        """
        from .models import receita_e_operacional
        op: list[tuple[str, int]] = []
        nao: list[tuple[str, int]] = []
        for cat, val in self._por_categoria("receita", ano, mes, membro_id, natureza):
            (op if receita_e_operacional(cat) else nao).append((cat, val))
        return {
            "operacional": op,
            "nao_operacional": nao,
            "total_operacional": sum(v for _, v in op),
            "total_nao_operacional": sum(v for _, v in nao),
        }

    def _por_categoria(self, tipo: str, ano: int, mes: int,
                       membro_id: int | None = None,
                       natureza: str | None = None) -> list[tuple[str, int]]:
        """Soma os lancamentos do tipo por categoria CANONICA (junta variacoes de
        grafia, ex Saude/Saude). Serve pra despesa e receita."""
        cond = "conta_id = %s and tipo=%s"
        params: list = [self.conta_id, tipo]
        if membro_id is not None:
            cond += " and membro_id = %s"; params.append(membro_id)
        cond, params = _cond_natureza(cond, params, natureza)
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

    def marcar_natureza(self, lancamento_id: int, natureza: str | None) -> bool:
        """Marca um lancamento como 'pessoal', 'empresa' ou None (a definir).
        Multi-tenant: so' mexe em lancamento DESTA conta."""
        nat = natureza if natureza in ("pessoal", "empresa") else None
        with self.pool.connection() as conn:
            cur = conn.execute(
                "update lancamentos set natureza = %s where id = %s and conta_id = %s",
                (nat, lancamento_id, self.conta_id))
            conn.commit()
            return cur.rowcount > 0

    def definir_plano_conta(self, lancamento_id: int,
                            plano_conta_id: int | None) -> bool:
        """Define (ou limpa, com None) a conta contábil de UM lançamento. Multi-
        tenant: só mexe em lançamento DESTA conta. A validação de que a conta do
        plano existe/está habilitada é de quem chama (o endpoint)."""
        with self.pool.connection() as conn:
            cur = conn.execute(
                "update lancamentos set plano_conta_id = %s where id = %s and conta_id = %s",
                (plano_conta_id, lancamento_id, self.conta_id))
            conn.commit()
            return cur.rowcount > 0

    def definir_centro_custo(self, lancamento_id: int,
                             centro_custo_id: int | None) -> bool:
        """Define (ou limpa, com None) o centro de custo de UM lançamento.
        Multi-tenant: só mexe em lançamento DESTA conta."""
        with self.pool.connection() as conn:
            cur = conn.execute(
                "update lancamentos set centro_custo_id = %s where id = %s and conta_id = %s",
                (centro_custo_id, lancamento_id, self.conta_id))
            conn.commit()
            return cur.rowcount > 0

    def ids_por_natureza(self, ano: int, mes: int, membro_id: int | None = None,
                         natureza: str | None = None) -> list[int]:
        """Ids dos lançamentos do mês que batem com o filtro de natureza.
        Usado pra saber 'os visíveis' (ex: os a definir) antes de classificar."""
        ini, prox = _intervalo_mes(ano, mes)
        cond = "conta_id = %s and data >= %s and data < %s"
        params: list = [self.conta_id, ini, prox]
        if membro_id is not None:
            cond += " and membro_id = %s"; params.append(membro_id)
        cond, params = _cond_natureza(cond, params, natureza)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"select id from lancamentos where {cond} order by data, id", params).fetchall()
        return [int(r[0]) for r in rows]

    def lancamentos_a_definir(self, ano: int, mes: int,
                              membro_id: int | None = None) -> list[dict]:
        """Lista dos lançamentos 'a definir' do mês (id, data, categoria, valor,
        tipo) — pro modal de classificação. Multi-tenant."""
        ini, prox = _intervalo_mes(ano, mes)
        cond = "conta_id = %s and data >= %s and data < %s and natureza is null"
        params: list = [self.conta_id, ini, prox]
        if membro_id is not None:
            cond += " and membro_id = %s"; params.append(membro_id)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select id, data, categoria, valor_centavos, tipo, descricao
                     from lancamentos where {cond} order by data, id""", params).fetchall()
        return [{"id": r[0], "data": r[1], "categoria": r[2], "valor": int(r[3]),
                 "tipo": r[4], "descricao": r[5]} for r in rows]

    def lancamentos_a_classificar(self, ano: int, mes: int,
                                  membro_id: int | None = None) -> list[dict]:
        """Lançamentos de EMPRESA do mês que ainda estão SEM conta contábil
        (plano_conta_id null) — é o que cai na linha 'A classificar' da DRE e o
        que o painel da aba Empresa lista pra resolver. Multi-tenant."""
        ini, prox = _intervalo_mes(ano, mes)
        cond = ("conta_id = %s and data >= %s and data < %s "
                "and natureza = 'empresa' and plano_conta_id is null")
        params: list = [self.conta_id, ini, prox]
        if membro_id is not None:
            cond += " and membro_id = %s"; params.append(membro_id)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select id, data, descricao, categoria, tipo, valor_centavos,
                          centro_custo_id
                     from lancamentos where {cond}
                    order by data desc, id desc""", params).fetchall()
        return [{"id": r[0], "data": r[1], "descricao": r[2], "categoria": r[3],
                 "tipo": r[4], "valor": int(r[5]), "centro_custo_id": r[6]}
                for r in rows]

    def classificar_por_categoria_do(self, lancamento_ref_id: int,
                                     plano_conta_id: int) -> int:
        """Atalho 'aplicar a todos iguais': pega a categoria e o mês do lançamento
        de referência e põe a MESMA conta contábil em todos os de EMPRESA daquele
        mês+categoria que ainda estão SEM conta. Não toca nos já classificados
        (então o próprio referência, se já tiver conta, fica de fora). Multi-tenant.
        Retorna quantos foram atualizados."""
        with self.pool.connection() as conn:
            r = conn.execute(
                "select categoria, data from lancamentos where id=%s and conta_id=%s",
                (lancamento_ref_id, self.conta_id)).fetchone()
            if not r:
                return 0
            categoria, data = r[0], r[1]
            ini, prox = _intervalo_mes(data.year, data.month)
            cur = conn.execute(
                """update lancamentos set plano_conta_id = %s
                    where conta_id=%s and data >= %s and data < %s
                      and natureza='empresa' and categoria=%s
                      and plano_conta_id is null""",
                (plano_conta_id, self.conta_id, ini, prox, categoria))
            conn.commit()
            return cur.rowcount

    def marcar_natureza_mapa(self, mapa: dict) -> int:
        """Marca vários lançamentos, cada um com sua natureza. `mapa` é
        {id: 'pessoal'|'empresa'}. Ignora valores inválidos. Multi-tenant.
        Retorna quantos foram marcados."""
        marc = 0
        with self.pool.connection() as conn:
            for lid, nat in (mapa or {}).items():
                if nat not in ("pessoal", "empresa"):
                    continue
                try:
                    lid_i = int(lid)
                except (TypeError, ValueError):
                    continue
                cur = conn.execute(
                    "update lancamentos set natureza = %s where id = %s and conta_id = %s",
                    (nat, lid_i, self.conta_id))
                marc += cur.rowcount
            conn.commit()
        return marc

    def contar_a_definir(self, ano: int | None = None, mes: int | None = None) -> int:
        """Quantos lancamentos DESTA conta estao sem natureza (a definir).
        Se ano/mes vier, conta so' o mes; senao, tudo."""
        cond = "conta_id = %s and natureza is null"
        params: list = [self.conta_id]
        if ano and mes:
            ini, prox = _intervalo_mes(ano, mes)
            cond += " and data >= %s and data < %s"; params += [ini, prox]
        with self.pool.connection() as conn:
            return int(conn.execute(
                f"select count(*) from lancamentos where {cond}", params).fetchone()[0])

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
                             tipo: str | None = None, limite: int = 50,
                             natureza: str | None = None) -> list[dict]:
        ini, prox = _intervalo_mes(ano, mes)
        cond = "l.conta_id = %s and l.data >= %s and l.data < %s"
        params: list = [self.conta_id, ini, prox]
        if membro_id is not None:
            cond += " and l.membro_id = %s"; params.append(membro_id)
        if tipo in ("despesa", "receita"):
            cond += " and l.tipo = %s"; params.append(tipo)
        cond, params = _cond_natureza(cond, params, natureza, col="l.natureza")
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select l.id, l.data, l.descricao, l.categoria, l.tipo, l.valor_centavos,
                          l.origem, coalesce(m.nome, '-') as quem, l.natureza,
                          l.forma_pagamento, l.plano_conta_id, l.centro_custo_id
                    from lancamentos l left join membros m on m.id = l.membro_id
                    where {cond} order by l.data desc, l.id desc limit %s""",
                params + [limite]).fetchall()
        return [{"id": r[0], "data": r[1], "descricao": r[2], "categoria": r[3], "tipo": r[4],
                 "valor": int(r[5]), "origem": r[6], "quem": r[7], "natureza": r[8],
                 "forma_pagamento": r[9], "plano_conta_id": r[10], "centro_custo_id": r[11]}
                for r in rows]

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

    # ─────────────────────────────────────────────────────────────────────
    # Import de extrato bancario (OFX): parsear_ofx()/sugerir_classificacao()
    # em finance/ofx_import.py nao sabem nada de banco — aqui e' onde cruzamos
    # com o que a conta ja tem (contraparte aprendida, duplicata, idempotencia
    # por chave) e devolvemos a previa pro cliente confirmar.
    # ─────────────────────────────────────────────────────────────────────
    def sugestao_por_contraparte(self, descricao: str) -> dict | None:
        """Pergunta 'da ultima vez que apareceu um lancamento com essa MESMA
        descricao (mesmo Pix recorrente, mesmo fornecedor), como o cliente
        classificou?'. So' conta lancamento que ja' tem categoria+conta contabil
        definidas (senao' estaria propagando um 'a classificar' pra outro 'a
        classificar', o que nao ajuda ninguem). Empata por mais recente.
        Retorna None se a descricao for vazia ou nao achar nada."""
        alvo = _ofx.chave_contraparte(descricao)
        if not alvo:
            return None
        # Comparação sem acento/caixa/prefixo de banco é feita em PYTHON
        # (chave_contraparte), não em SQL — o projeto não tem a extensão
        # `unaccent` habilitada, e é o mesmo espírito já usado pra casar
        # produto de cupom (finance/models.py:normalizar_descricao). O limit é
        # só uma rédea de performance pra conta muito antiga/grande.
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select descricao, categoria, plano_conta_id, data
                     from lancamentos
                    where conta_id = %s and plano_conta_id is not null
                    order by data desc
                    limit 2000""",
                (self.conta_id,),
            ).fetchall()
        contagem: dict[tuple, int] = {}
        mais_recente: dict[tuple, date] = {}
        for desc, categoria, plano_conta_id, data in rows:
            if _ofx.chave_contraparte(desc) != alvo:
                continue
            par = (categoria, plano_conta_id)
            contagem[par] = contagem.get(par, 0) + 1
            if par not in mais_recente or data > mais_recente[par]:
                mais_recente[par] = data
        if not contagem:
            return None
        melhor = max(contagem, key=lambda par: (contagem[par], mais_recente[par]))
        return {"categoria": melhor[0], "plano_conta_id": melhor[1],
                "ocorrencias": contagem[melhor]}

    def pre_visualizar_importacao_ofx(self, extrato, natureza_padrao: str | None = None) -> list[dict]:
        """Monta a previa de importacao: cada transacao do OFX vira um dict com
        a classificacao sugerida (heuristica de texto -> aprendizado por
        contraparte -> nada) e os sinais de duplicata. NAO grava nada — quem
        decide e confirma e' confirmar_importacao_ofx(), com o que o cliente
        revisou na tela. `extrato` e' o OfxExtrato de finance/ofx_import.py."""
        from . import plano_contas as pc

        chave_conta = extrato.chave_conta()
        mapa_plano = {c["codigo"]: c["id"] for c in pc.listar_plano(self.pool)}
        itens = []
        for txn in extrato.transacoes:
            chave = f"ofx:{chave_conta}:{txn.fitid}"
            ja_importado = self.lancamento_por_chave(chave) is not None

            sugestao = _ofx.sugerir_classificacao(txn)
            contraparte = None
            if not sugestao.categoria and not ja_importado:
                contraparte = self.sugestao_por_contraparte(txn.descricao)

            categoria = sugestao.categoria or (contraparte["categoria"] if contraparte else None)
            confianca = sugestao.confianca or ("alta" if contraparte else None)
            plano_conta_id = mapa_plano.get(sugestao.plano_conta_codigo) if sugestao.plano_conta_codigo else None
            if plano_conta_id is None and contraparte:
                plano_conta_id = contraparte["plano_conta_id"]
            motivo = sugestao.motivo
            if contraparte:
                motivo = f"aprendido de {contraparte['ocorrencias']} lançamento(s) anterior(es) igual(is)"

            duplicatas = [] if ja_importado else self.buscar_duplicata(txn.valor_centavos, txn.data)

            itens.append({
                "fitid": txn.fitid,
                "chave": chave,
                "data": txn.data,
                "descricao": txn.descricao,
                "tipo": txn.tipo,
                "valor_centavos": txn.valor_centavos,
                "trntype": txn.trntype,
                "categoria": categoria,
                "confianca": confianca,
                "plano_conta_id": plano_conta_id,
                "natureza": natureza_padrao,
                "motivo": motivo,
                "ja_importado": ja_importado,
                "duplicatas_provaveis": duplicatas,
                "selecionado_padrao": not ja_importado and not duplicatas,
            })
        return itens

    def confirmar_importacao_ofx(self, chave_conta: str, itens: list[dict]) -> dict:
        """Grava os itens que o cliente confirmou na revisao. `itens` e' uma
        lista de dicts já com a escolha final do cliente: fitid, tipo,
        valor_centavos, data, descricao, categoria, natureza, plano_conta_id,
        centro_custo_id. Idempotente por FITID: se a chave ja' existir (extrato
        reimportado), pula sem gravar de novo e conta em `ja_existiam`."""
        from . import plano_contas as pc
        importados, ja_existiam = 0, 0
        for it in itens:
            chave = f"ofx:{chave_conta}:{it['fitid']}"
            if self.lancamento_por_chave(chave) is not None:
                ja_existiam += 1
                continue
            plano_conta_id = pc.plano_conta_valido(self.pool, self.conta_id, it.get("plano_conta_id"))
            centro_custo_id = pc.centro_custo_valido(self.pool, self.conta_id, it.get("centro_custo_id"))
            lanc = Lancamento.criar(
                tipo=it["tipo"], valor_reais=centavos_para_reais(it["valor_centavos"]),
                categoria=it.get("categoria") or "Outros", descricao=it["descricao"],
                data=it["data"], origem="extrato",
                natureza=it.get("natureza"),
                plano_conta_id=plano_conta_id, centro_custo_id=centro_custo_id,
            )
            self.adicionar(lanc, chave=chave, forcar=True)
            importados += 1
        return {"importados": importados, "ja_existiam": ja_existiam}

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
        # loja NOVA: completa na Receita (BrasilAPI) o que faltar - inclusive
        # cidade/uf, que o agente costuma deixar embutidos no texto do endereco.
        ramo = ramo_reserva       # reserva: ramo derivado da categoria do Claude
        cnae = None
        if not nome or not endereco or not cidade or not uf:
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
        corte = hoje_br() - timedelta(days=dias)
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
                        substituir: bool = False, loja_info: dict | None = None,
                        anexar: bool = False) -> int:
        """Salva os itens de um cupom, ligados a um lancamento do PROPRIO usuario.

        Cada item: {descricao, quantidade, valor_unitario_centavos, valor_total_centavos}.
        Retorna quantos itens foram salvos (0 se o lancamento nao for do usuario).

        Protecao contra DUPLICACAO: se o lancamento ja' tiver itens, por padrao
        NAO empilha de novo (retorna -1). Com substituir=True, troca os antigos
        pelos novos. Isso evita o "cupom com itens em dobro".

        Modo ANEXAR (anexar=True): pra cupom GRANDE ou que chega em VARIAS FOTOS,
        os itens sao salvos em LOTES. Nesse modo, em vez de recusar quando ja' ha
        itens, ACRESCENTA so' os itens ainda nao salvos (dedupe por codigo ou por
        descricao+valor_total dentro do mesmo lancamento). Assim da' pra reenviar
        uma parte / mandar fotos sobrepostas sem duplicar. Retorna -2 quando o
        lote inteiro ja' estava salvo (nada novo a acrescentar).
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
            if ja and substituir:
                conn.execute("delete from itens_lancamento where lancamento_id = %s",
                             (lancamento_id,))
            elif ja and anexar:
                # lote adicional: filtra os itens que JA' estao salvos neste cupom
                existentes = conn.execute(
                    "select descricao, valor_total_centavos, codigo "
                    "from itens_lancamento where lancamento_id = %s",
                    (lancamento_id,),
                ).fetchall()
                vistos = {_chave_dedupe_item(d, vt, cod) for d, vt, cod in existentes}
                novos = [it for it in itens
                         if _chave_dedupe_item(it.get("descricao"),
                                               it.get("valor_total_centavos"),
                                               it.get("codigo")) not in vistos]
                if not novos:
                    return -2                  # lote ja' estava salvo (nada novo)
                itens = novos
            elif ja and not substituir:
                return -1                      # ja tem itens: nao duplica
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
        # CNPJ da loja: PREFERE a chave do QR (lida por maquina, precisa) sobre o
        # que o agente leu do cabecalho (visao pode trocar digito - ex: o Elizeu
        # virou 0140 em vez de 0440). So' usa o cabecalho quando NAO ha chave.
        cnpj = ""
        ch = "".join(c for c in str(getattr(self, "chave_nfce_atual", "") or "")
                     if c.isdigit())
        if len(ch) == 44:
            cnpj = ch[6:20]            # CNPJ emitente da chave (posicoes 7-20)
        if len(cnpj) != 14:            # sem chave legivel -> usa o do cabecalho
            cnpj = "".join(c for c in (li.get("cnpj") or "") if c.isdigit())
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
        # SEM loja_id (CNPJ nao identificado) -> NAO contribui pro banco coletivo.
        # Era a origem do "Carvalho" fantasma: observacao com loja_id=NULL gravava
        # 'mercado' em texto livre, que o display mostrava pra sempre. O lancamento
        # e os itens do usuario JA' foram salvos; aqui so' pulamos a contribuicao
        # pro banco de precos, que exige loja identificada (CNPJ) pra ter valor.
        if loja_id is None:
            return 0
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

    def buscar_lancamentos(self, termo: str, limite: int = 100) -> list[dict]:
        """Busca lançamentos cuja descrição casa com 'termo'.
        Retorna lista de lançamentos com os mesmos campos que lancamentos_recentes."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select l.id, l.data, l.descricao, l.categoria, l.tipo, l.valor_centavos,
                          l.origem, coalesce(m.nome, '-') as quem, l.natureza,
                          l.forma_pagamento, l.plano_conta_id, l.centro_custo_id
                    from lancamentos l left join membros m on m.id = l.membro_id
                    where l.conta_id = %s and l.descricao ilike %s
                    order by l.data desc, l.id desc limit %s""",
                (self.conta_id, f"%{termo}%", limite),
            ).fetchall()
        return [{"id": r[0], "data": r[1], "descricao": r[2], "categoria": r[3], "tipo": r[4],
                 "valor": int(r[5]), "origem": r[6], "quem": r[7], "natureza": r[8],
                 "forma_pagamento": r[9], "plano_conta_id": r[10], "centro_custo_id": r[11]}
                for r in rows]

    def buscar_itens(self, termo: str, dias: int = 60) -> tuple[list[dict], int]:
        """Busca itens cuja descricao casa com 'termo' (nos ultimos N dias).

        Retorna (lista_de_itens, total_centavos).
        """
        corte = hoje_br() - timedelta(days=dias)
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
        corte = hoje_br() - timedelta(days=dias)
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

def descricoes_sao_a_mesma(a, b) -> bool:
    """Duas descricoes falam do MESMO lancamento?

    Continencia, nao igualdade: o agente as vezes reescreve o sufixo do mesmo
    comprovante ("Almoco no Assai" vira "Almoco no Assai Banco do Brasil"), e
    exigir igualdade exata deixaria isso passar como pagamento novo — que e' o
    caso que a trava anti-duplicidade nasceu pra pegar.

    Nomes diferentes nao se contem, e e' o que separa folha de duplicata:
    "…Pedro Yan Mendes…" e "…Thiago Cesar Borges…" tem o mesmo prefixo e o mesmo
    sufixo, mas nenhuma contem a outra.

    Vazio dos dois lados: nao da pra distinguir, entao trata como a mesma —
    bloquear de menos duplicaria dinheiro, e o chamador avisa em vez de confirmar.
    """
    ta, tb = _texto_chave(a), _texto_chave(b)
    if not ta or not tb:
        return True
    return ta in tb or tb in ta


def _texto_chave(t) -> str:
    """Minusculo, sem acento e sem pontuacao — pra 'Servico' e 'Serviço' baterem."""
    import unicodedata
    bruto = unicodedata.normalize("NFD", str(t or ""))
    limpo = "".join(c for c in bruto if unicodedata.category(c) != "Mn").lower()
    return " ".join("".join(c if c.isalnum() else " " for c in limpo).split())
