"""As OBRAS da construtora: cada casa pra vender e cada reforma de cliente.

PR 2 de 4 do desenho aprovado pelo dono em 25/09/2026
(docs/mockups/nicho_construcao.html). A primeira conta é a PX2 Empreendimentos
(conta 33, Lago da Pedra-MA), que constrói casa popular pelo Minha Casa Minha Vida
e faz reforma. Medido na produção nesse dia: 13 despesas de obra, R$ 21.980,48,
e nenhuma dizendo de qual casa era.

AS QUATRO ESCOLHAS DESTE MÓDULO, e por quê:

1. **Cada obra tem um centro de custo.** O centro é criado junto com a obra (ou
   reaproveitado, se a conta já tinha um ativo com o mesmo nome). É o que faz a
   DRE por centro virar resultado por obra, e o agente — que já conhece os centros
   (23/09) — passar a lançar na casa certa pelo `centro_custo` de sempre.

2. **A obra nasce no painel, nunca pelo agente** (decisão 1 do dono, 25/09). A
   regra de 23/09 diz que o agente não cria nem mexe em centro de custo; como a
   obra cria um, o agente também não cria obra — ele manda o link.

3. **Dividir não quebra o lançamento.** A nota de material das três casas continua
   UM lançamento, com o valor do comprovante, e `lancamento_rateio` diz quanto é de
   cada obra. Quebrar em três faria a mesma nota mandada de novo passar pelo
   `checar_duplicata` (ele procura o valor inteiro) e faria o comprovante deixar de
   bater com a conta a pagar que ele quita. Onde há rateio, ele vale; o centro do
   próprio lançamento fica nulo.

4. **"Sem obra" é custo de obra sem obra**, não toda despesa sem centro. DAS,
   contador e sistema não são de casa nenhuma e ficariam âmbar pra sempre. Entra
   o que tem cara de obra: material (conta 3.1.03, categorias Insumos, Construcao,
   Compras) e mão de obra (3.1.04, Servicos). É o recorte que pega as 13 da PX2.

Multi-tenant sagrado: toda consulta é escopada por `conta_id`.
"""
from __future__ import annotations

import unicodedata
from datetime import date

TIPOS = ("casa", "reforma")
ROTULO_TIPO = {"casa": "Casa pra vender", "reforma": "Reforma"}
STATUS = ("em_obra", "pronta", "vendida", "entregue", "arquivada")
ROTULO_STATUS = {"em_obra": "Em obra", "pronta": "Pronta", "vendida": "Vendida",
                 "entregue": "Entregue", "arquivada": "Arquivada"}

#: O endereço que o agente manda quando pedem obra nova (ver a escolha 2).
LINK_OBRAS = "https://app.zaq-ia.com/painel/obras"

# ─────────────────────────────────────────────────────────────── as etapas
#
# (chave, nome, peso). Os pesos da CASA ficam dentro das faixas da planilha de
# construção individual da Caixa (PCI): estrutura 12–18%, pisos 8–12%, paredes
# 5–11%, esquadrias 4–13%, cobertura até 13%, revestimento interno 7–9%, fundação
# 3–7%, cada instalação 4–5%, louças 4–5%. Preliminares, pintura e limpeza entram
# pra fechar 100. A obra nova copia as etapas da ÚLTIMA obra do mesmo tipo da
# conta (`_semente`), então o ajuste que a empresa fizer na primeira vale pras
# próximas — é o "pesos editáveis" do desenho, sem tela de configuração.
ETAPAS_CASA = (
    ("preliminares_fundacao", "Preliminares e fundação", 8),
    ("estrutura", "Estrutura", 14),
    ("alvenaria", "Alvenaria", 10),
    ("cobertura", "Cobertura", 12),
    ("eletrica", "Instalações elétricas", 6),
    ("hidraulica", "Instalações hidráulicas", 6),
    ("reboco_revestimento", "Reboco e revestimento", 12),
    ("pisos", "Pisos", 10),
    ("esquadrias", "Esquadrias", 9),
    ("loucas_metais", "Louças e metais", 5),
    ("pintura", "Pintura", 6),
    ("limpeza_entrega", "Limpeza e entrega", 2),
)
# Reforma varia demais de uma pra outra pra ter pesos de referência: esta é só a
# espinha, e a primeira reforma da conta ajusta.
ETAPAS_REFORMA = (
    ("demolicao_preparo", "Demolição e preparo", 15),
    ("instalacoes", "Instalações", 20),
    ("alvenaria_reboco", "Alvenaria e reboco", 20),
    ("revestimento_piso", "Revestimento e piso", 20),
    ("pintura", "Pintura", 15),
    ("limpeza_entrega", "Limpeza e entrega", 10),
)

#: Como o encarregado fala no WhatsApp -> a chave da etapa. Casa exata de palavra
#: (depois de tirar acento e caixa), então "telhado" acha a cobertura e "pia" acha
#: louças, mas "piso" não acha "pisos" por acidente de prefixo — acha por estar aqui.
_SINONIMOS = {
    "fundacao": "preliminares_fundacao", "alicerce": "preliminares_fundacao",
    "baldrame": "preliminares_fundacao", "sapata": "preliminares_fundacao",
    "gabarito": "preliminares_fundacao", "terraplenagem": "preliminares_fundacao",
    "laje": "estrutura", "viga": "estrutura", "vigas": "estrutura", "pilar": "estrutura",
    "pilares": "estrutura", "coluna": "estrutura", "colunas": "estrutura",
    "parede": "alvenaria", "paredes": "alvenaria", "tijolo": "alvenaria",
    "telhado": "cobertura", "telha": "cobertura", "telhas": "cobertura",
    "madeiramento": "cobertura", "forro": "cobertura",
    "fiacao": "eletrica", "fio": "eletrica", "fios": "eletrica", "eletrica": "eletrica",
    "encanamento": "hidraulica", "cano": "hidraulica", "canos": "hidraulica",
    "esgoto": "hidraulica", "hidraulica": "hidraulica",
    "reboco": "reboco_revestimento", "emboco": "reboco_revestimento",
    "chapisco": "reboco_revestimento", "revestimento": "reboco_revestimento",
    "azulejo": "reboco_revestimento",
    "piso": "pisos", "ceramica": "pisos", "porcelanato": "pisos", "contrapiso": "pisos",
    "porta": "esquadrias", "portas": "esquadrias", "janela": "esquadrias",
    "janelas": "esquadrias", "esquadria": "esquadrias",
    "louca": "loucas_metais", "loucas": "loucas_metais", "vaso": "loucas_metais",
    "pia": "loucas_metais", "torneira": "loucas_metais", "chuveiro": "loucas_metais",
    "bacia": "loucas_metais",
    "pintura": "pintura", "tinta": "pintura", "pintar": "pintura",
    "limpeza": "limpeza_entrega", "entrega": "limpeza_entrega",
    "demolicao": "demolicao_preparo", "quebra": "demolicao_preparo",
    "instalacao": "instalacoes", "instalacoes": "instalacoes",
}

# ─────────────────────────────────────────────────────────────── o custo
MATERIAL, MAO_DE_OBRA, OUTROS = "material", "mao_de_obra", "outros"
ROTULO_CUSTO = {MATERIAL: "Material", MAO_DE_OBRA: "Mão de obra", OUTROS: "Outros"}
_PLANO_CUSTO = {"3.1.03": MATERIAL, "3.1.04": MAO_DE_OBRA}
# `Compras` e `Construcao` vêm do histórico: foi onde as notas de material da PX2
# caíram antes da persona do ramo (25/09). Em conta de construção, compra da
# EMPRESA é material de obra.
_CATEGORIA_CUSTO = {"Insumos": MATERIAL, "Construcao": MATERIAL, "Compras": MATERIAL,
                    "Servicos": MAO_DE_OBRA}


def tipo_de_custo(codigo_plano: str | None, categoria: str | None) -> str:
    """Material, mão de obra ou outros — a conta contábil manda; sem ela, a categoria."""
    if codigo_plano in _PLANO_CUSTO:
        return _PLANO_CUSTO[codigo_plano]
    return _CATEGORIA_CUSTO.get((categoria or "").strip(), OUTROS)


def _norm(txt: str | None) -> str:
    t = unicodedata.normalize("NFKD", (txt or "").casefold())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(t.split())


def etapas_modelo(tipo: str) -> tuple:
    return ETAPAS_REFORMA if tipo == "reforma" else ETAPAS_CASA


def _semente(c, conta_id: int, tipo: str) -> list[tuple]:
    """As etapas que a obra nova recebe: as da última obra do mesmo tipo desta
    conta, se ela tiver alguma; senão o modelo."""
    ult = c.execute(
        """select o.id from obras o
            where o.conta_id=%s and o.tipo=%s
              and exists (select 1 from obra_etapas e where e.obra_id=o.id)
            order by o.id desc limit 1""", (conta_id, tipo)).fetchone()
    if ult:
        return [(r[0], r[1], float(r[2])) for r in c.execute(
            "select chave, nome, peso from obra_etapas where obra_id=%s order by ordem, id",
            (ult[0],)).fetchall()]
    return [(ch, nm, float(p)) for ch, nm, p in etapas_modelo(tipo)]


# ─────────────────────────────────────────────────────────────── cadastro
def criar_obra(pool, conta_id: int, nome: str, tipo: str = "casa", *,
               endereco: str = "", area_m2=None, custo_previsto_centavos=None,
               valor_centavos=None, inicio_em=None, previsao_em=None,
               criado_por: int | None = None) -> dict:
    """Cria a obra, o centro de custo dela e as etapas. Levanta ValueError com a
    frase pra tela quando o nome falta ou já existe."""
    nome = " ".join((nome or "").split())
    if not nome:
        raise ValueError("A obra precisa de um nome — por exemplo, Casa 2.")
    if tipo not in TIPOS:
        raise ValueError("O tipo da obra é casa ou reforma.")
    with pool.connection() as c:
        if c.execute("select 1 from obras where conta_id=%s and lower(nome)=lower(%s)",
                     (conta_id, nome)).fetchone():
            raise ValueError(f"Já existe uma obra chamada “{nome}”.")
        # a conta pode já ter um centro com esse nome (criado na mão, antes das
        # obras): ele vira o centro da obra, com o histórico que já tem
        cc = c.execute(
            """select cc.id from centros_custo cc
                where cc.conta_id=%s and cc.ativo and lower(trim(cc.nome))=lower(%s)
                  and not exists (select 1 from obras o where o.centro_custo_id=cc.id)
                order by cc.id limit 1""", (conta_id, nome)).fetchone()
        centro_id = cc[0] if cc else c.execute(
            """insert into centros_custo (conta_id, nome, descricao)
                    values (%s, %s, %s) returning id""",
            (conta_id, nome, ROTULO_TIPO[tipo].lower())).fetchone()[0]
        etapas = _semente(c, conta_id, tipo)
        oid = c.execute(
            """insert into obras (conta_id, centro_custo_id, tipo, nome, endereco, area_m2,
                                  custo_previsto_centavos, valor_centavos, inicio_em,
                                  previsao_em, criado_por)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, centro_id, tipo, nome, (endereco or "").strip(), area_m2,
             custo_previsto_centavos, valor_centavos, inicio_em, previsao_em,
             criado_por)).fetchone()[0]
        for i, (chave, nm, peso) in enumerate(etapas):
            c.execute(
                """insert into obra_etapas (conta_id, obra_id, chave, nome, peso, ordem)
                        values (%s,%s,%s,%s,%s,%s)""",
                (conta_id, oid, chave, nm, peso, i * 10))
        c.commit()
    return obter_obra(pool, conta_id, oid)


_CAMPOS = ("nome", "tipo", "endereco", "area_m2", "custo_previsto_centavos",
           "valor_centavos", "status", "inicio_em", "previsao_em", "concluida_em", "obs")


def editar_obra(pool, conta_id: int, obra_id: int, **campos) -> dict:
    """Muda os campos que vierem. Nome novo renomeia o centro junto — é a mesma
    coisa com dois nomes, e o agente acha a obra pelo nome do centro."""
    novos = {k: v for k, v in campos.items() if k in _CAMPOS}
    if "nome" in novos:
        novos["nome"] = " ".join((novos["nome"] or "").split())
        if not novos["nome"]:
            raise ValueError("A obra precisa de um nome.")
    if "tipo" in novos and novos["tipo"] not in TIPOS:
        raise ValueError("O tipo da obra é casa ou reforma.")
    if "status" in novos and novos["status"] not in STATUS:
        raise ValueError("Situação desconhecida.")
    with pool.connection() as c:
        atual = c.execute("select centro_custo_id, status from obras where id=%s and conta_id=%s",
                          (obra_id, conta_id)).fetchone()
        if not atual:
            raise ValueError("Obra não encontrada.")
        if "nome" in novos and c.execute(
                "select 1 from obras where conta_id=%s and lower(nome)=lower(%s) and id<>%s",
                (conta_id, novos["nome"], obra_id)).fetchone():
            raise ValueError(f"Já existe uma obra chamada “{novos['nome']}”.")
        if novos:
            sets = ", ".join(f"{k}=%s" for k in novos)
            c.execute(f"update obras set {sets}, atualizado_em=now() "
                      "where id=%s and conta_id=%s", (*novos.values(), obra_id, conta_id))
        if "nome" in novos:
            c.execute("update centros_custo set nome=%s where id=%s and conta_id=%s",
                      (novos["nome"], atual[0], conta_id))
        # arquivar tira o centro dos selects (inativo), sem apagar o histórico;
        # tirar do arquivo devolve
        if novos.get("status") == "arquivada" or atual[1] == "arquivada":
            c.execute("update centros_custo set ativo=%s where id=%s and conta_id=%s",
                      (novos.get("status", atual[1]) != "arquivada", atual[0], conta_id))
        c.commit()
    return obter_obra(pool, conta_id, obra_id)


def obra_por_nome(pool, conta_id: int, ref: str | None, *,
                  incluir_arquivadas: bool = False) -> dict | None:
    """A obra pelo jeito que a pessoa escreveu: nome exato (sem acento e caixa),
    depois o único que começa ou contém o que ela disse. Ambíguo ou nada: None."""
    alvo = _norm(ref)
    if not alvo:
        return None
    obras = listar_obras(pool, conta_id, incluir_arquivadas=incluir_arquivadas,
                         com_custos=False)
    for o in obras:
        if _norm(o["nome"]) == alvo:
            return o
    for teste in (lambda n: n.startswith(alvo), lambda n: alvo in n):
        achou = [o for o in obras if teste(_norm(o["nome"]))]
        if len(achou) == 1:
            return achou[0]
    return None


# ─────────────────────────────────────────────────────────────── leitura
def _pct(etapas: list[dict]) -> int:
    total = sum(e["peso"] for e in etapas)
    feito = sum(e["peso"] for e in etapas if e["concluida_em"])
    return int(round(100 * feito / total)) if total else 0


def _etapas(c, conta_id: int, obra_ids) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {i: [] for i in obra_ids}
    if not obra_ids:
        return out
    for oid, ident, chave, nome, peso, ordem, concl in c.execute(
            """select obra_id, id, chave, nome, peso, ordem, concluida_em from obra_etapas
                where conta_id=%s and obra_id = any(%s) order by obra_id, ordem, id""",
            (conta_id, list(obra_ids))).fetchall():
        out[oid].append({"id": ident, "chave": chave, "nome": nome, "peso": float(peso),
                         "ordem": ordem, "concluida_em": concl})
    return out


#: tudo que foi lançado num centro, com a divisão aplicada: a linha de rateio
#: manda quando existe; senão, o próprio lançamento
_SQL_ALOCADO = """
    select coalesce(r.centro_custo_id, l.centro_custo_id) as centro_id,
           l.id as lancamento_id, l.tipo, l.categoria, p.codigo as plano,
           coalesce(r.valor_centavos, l.valor_centavos) as valor,
           l.valor_centavos as valor_inteiro, (r.id is not null) as dividido,
           l.data, l.descricao
      from lancamentos l
      left join lancamento_rateio r on r.lancamento_id = l.id
      left join plano_contas p on p.id = l.plano_conta_id
     where l.conta_id = %s and l.natureza is distinct from 'pessoal'
"""


def _vazio_custos() -> dict:
    return {MATERIAL: 0, MAO_DE_OBRA: 0, OUTROS: 0, "total": 0, "recebido": 0}


def _custos(c, conta_id: int, centros) -> dict[int, dict]:
    out = {cid: _vazio_custos() for cid in centros}
    if not centros:
        return out
    for centro_id, tipo, categoria, plano, valor in c.execute(
            f"""select centro_id, tipo, categoria, plano, sum(valor)
                  from ({_SQL_ALOCADO}) a
                 where centro_id = any(%s)
                 group by 1, 2, 3, 4""", (conta_id, list(centros))).fetchall():
        v = int(valor or 0)
        if tipo == "receita":
            out[centro_id]["recebido"] += v
        else:
            out[centro_id][tipo_de_custo(plano, categoria)] += v
            out[centro_id]["total"] += v
    return out


_COLS = ("id", "centro_custo_id", "tipo", "nome", "endereco", "area_m2",
         "custo_previsto_centavos", "valor_centavos", "status", "inicio_em",
         "previsao_em", "concluida_em", "obs", "criado_em")


def _completar(o: dict, etapas: list[dict], custos: dict | None) -> dict:
    o["area_m2"] = float(o["area_m2"]) if o["area_m2"] is not None else None
    o["etapas"] = etapas
    o["pct"] = _pct(etapas)
    abertas = [e for e in etapas if not e["concluida_em"]]
    o["proxima_etapa"] = abertas[0]["nome"] if abertas else None
    feitas = [e for e in etapas if e["concluida_em"]]
    o["ultima_etapa"] = max(feitas, key=lambda e: (e["concluida_em"], e["ordem"]))["nome"] \
        if feitas else None
    o["rotulo_tipo"] = ROTULO_TIPO.get(o["tipo"], o["tipo"])
    o["rotulo_status"] = ROTULO_STATUS.get(o["status"], o["status"])
    if custos is not None:
        o["custos"] = custos
        prev = o["custo_previsto_centavos"]
        o["pct_previsto"] = int(round(100 * custos["total"] / prev)) if prev else None
        o["custo_m2"] = int(round(custos["total"] / o["area_m2"])) if o["area_m2"] else None
    return o


def listar_obras(pool, conta_id: int, *, incluir_arquivadas: bool = False,
                 com_custos: bool = True) -> list[dict]:
    """As obras da conta, as abertas primeiro, com etapas, % e (se pedido) custo."""
    filtro = "" if incluir_arquivadas else " and status <> 'arquivada'"
    with pool.connection() as c:
        rows = c.execute(
            f"""select {', '.join(_COLS)} from obras where conta_id=%s{filtro}
                 order by (status = 'arquivada'), (status = 'entregue'), id""",
            (conta_id,)).fetchall()
        obras = [dict(zip(_COLS, r)) for r in rows]
        etapas = _etapas(c, conta_id, [o["id"] for o in obras])
        custos = _custos(c, conta_id, [o["centro_custo_id"] for o in obras]) \
            if com_custos else {}
    return [_completar(o, etapas.get(o["id"], []),
                       custos.get(o["centro_custo_id"]) if com_custos else None)
            for o in obras]


def obter_obra(pool, conta_id: int, obra_id: int) -> dict | None:
    """A obra inteira pra ficha: etapas, custo por tipo e os lançamentos dela."""
    with pool.connection() as c:
        r = c.execute(f"select {', '.join(_COLS)} from obras where id=%s and conta_id=%s",
                      (obra_id, conta_id)).fetchone()
        if not r:
            return None
        o = dict(zip(_COLS, r))
        etapas = _etapas(c, conta_id, [obra_id])[obra_id]
        custos = _custos(c, conta_id, [o["centro_custo_id"]])[o["centro_custo_id"]]
        lanc = c.execute(
            f"""select lancamento_id, data, descricao, tipo, categoria, plano, valor,
                       valor_inteiro, dividido
                  from ({_SQL_ALOCADO}) a
                 where centro_id = %s
                 order by data desc, lancamento_id desc""",
            (conta_id, o["centro_custo_id"])).fetchall()
    o = _completar(o, etapas, custos)
    o["lancamentos"] = [
        {"id": lid, "data": d, "descricao": desc or "", "tipo": tp, "categoria": cat,
         "tipo_custo": tipo_de_custo(pl, cat) if tp != "receita" else "receita",
         "valor_centavos": int(v or 0), "valor_inteiro_centavos": int(vi or 0),
         "dividido": bool(div)}
        for lid, d, desc, tp, cat, pl, v, vi, div in lanc]
    return o


# ─────────────────────────────────────────────────────────────── etapas
def _achar_etapa(etapas: list[dict], ref: str | None) -> dict | None:
    alvo = _norm(ref).replace("_", " ")
    if not alvo:
        return None
    for e in etapas:
        if alvo in (_norm(e["nome"]), e["chave"].replace("_", " ")):
            return e
    for palavra in alvo.split():
        chave = _SINONIMOS.get(palavra)
        achou = [e for e in etapas if e["chave"] == chave]
        if achou:
            return achou[0]
    achou = [e for e in etapas if alvo in _norm(e["nome"])]
    return achou[0] if len(achou) == 1 else None


def marcar_etapa(pool, conta_id: int, obra_id: int, etapa_ref, *,
                 concluida: bool = True, quando: date | None = None) -> dict:
    """Marca (ou desmarca) uma etapa. Com todas feitas, a obra passa a PRONTA; ao
    desmarcar uma de obra pronta, ela volta a EM OBRA. Devolve {etapa, pct, status}.
    Levanta ValueError quando não acha a obra ou a etapa."""
    o = obter_obra(pool, conta_id, obra_id)
    if not o:
        raise ValueError("Obra não encontrada.")
    e = next((x for x in o["etapas"] if x["id"] == etapa_ref), None) \
        if isinstance(etapa_ref, int) else _achar_etapa(o["etapas"], etapa_ref)
    if not e:
        nomes = ", ".join(x["nome"] for x in o["etapas"])
        raise ValueError(f"Não achei essa etapa em {o['nome']}. As etapas são: {nomes}.")
    quando = (quando or date.today()) if concluida else None
    with pool.connection() as c:
        c.execute("update obra_etapas set concluida_em=%s where id=%s and conta_id=%s",
                  (quando, e["id"], conta_id))
        etapas = _etapas(c, conta_id, [obra_id])[obra_id]
        pct = _pct(etapas)
        status = o["status"]
        if pct == 100 and status == "em_obra":
            status = "pronta"
            c.execute("update obras set status='pronta', concluida_em=%s, atualizado_em=now() "
                      "where id=%s and conta_id=%s", (quando or date.today(), obra_id, conta_id))
        elif pct < 100 and status == "pronta":
            status = "em_obra"
            c.execute("update obras set status='em_obra', concluida_em=null, atualizado_em=now() "
                      "where id=%s and conta_id=%s", (obra_id, conta_id))
        c.commit()
    return {"etapa": e["nome"], "concluida": concluida, "pct": pct, "status": status,
            "obra": o["nome"]}


def salvar_etapas(pool, conta_id: int, obra_id: int, linhas) -> list[dict]:
    """Troca a lista de etapas da obra por `linhas` [(chave|None, nome, peso)].
    Etapa que continua (mesma chave) guarda a data em que foi concluída; etapa que
    sai da lista é removida — é configuração da obra, não histórico de dinheiro."""
    limpas = []
    vistas = set()
    for chave, nome, peso in linhas:
        nome = " ".join((nome or "").split())
        if not nome:
            continue
        chave = (chave or _norm(nome).replace(" ", "_"))[:60]
        base, n = chave, 2
        while chave in vistas:
            chave, n = f"{base}_{n}", n + 1
        vistas.add(chave)
        limpas.append((chave, nome, max(0.0, float(peso or 0))))
    if not limpas:
        raise ValueError("A obra precisa de pelo menos uma etapa.")
    with pool.connection() as c:
        if not c.execute("select 1 from obras where id=%s and conta_id=%s",
                         (obra_id, conta_id)).fetchone():
            raise ValueError("Obra não encontrada.")
        c.execute("delete from obra_etapas where obra_id=%s and conta_id=%s and not (chave = any(%s))",
                  (obra_id, conta_id, [ch for ch, _n, _p in limpas]))
        for i, (chave, nome, peso) in enumerate(limpas):
            c.execute(
                """insert into obra_etapas (conta_id, obra_id, chave, nome, peso, ordem)
                        values (%s,%s,%s,%s,%s,%s)
                   on conflict (obra_id, chave) do update
                         set nome=excluded.nome, peso=excluded.peso, ordem=excluded.ordem""",
                (conta_id, obra_id, chave, nome, peso, i * 10))
        c.commit()
    return obter_obra(pool, conta_id, obra_id)["etapas"]


# ─────────────────────────────────────────────────────────────── a despesa na obra
def _lancamento(c, conta_id: int, lancamento_id: int):
    return c.execute(
        "select id, tipo, valor_centavos, natureza from lancamentos where id=%s and conta_id=%s",
        (lancamento_id, conta_id)).fetchone()


def por_na_obra(pool, conta_id: int, lancamento_id: int, obra_id: int) -> dict:
    """O lançamento inteiro vai pra UMA obra (o centro dela). Desfaz divisão antiga.
    Sem natureza definida, vira empresa: quem põe numa obra está dizendo isso."""
    with pool.connection() as c:
        lanc = _lancamento(c, conta_id, lancamento_id)
        obra = c.execute("select centro_custo_id, nome from obras where id=%s and conta_id=%s",
                         (obra_id, conta_id)).fetchone()
        if not lanc or not obra:
            raise ValueError("Não achei esse lançamento ou essa obra.")
        if lanc[3] == "pessoal":
            raise ValueError("Esse lançamento está marcado como pessoal.")
        c.execute("delete from lancamento_rateio where lancamento_id=%s and conta_id=%s",
                  (lancamento_id, conta_id))
        c.execute("""update lancamentos set centro_custo_id=%s,
                            natureza=coalesce(natureza, 'empresa')
                      where id=%s and conta_id=%s""", (obra[0], lancamento_id, conta_id))
        c.commit()
    return {"lancamento_id": lancamento_id, "obra": obra[1], "valor_centavos": int(lanc[2])}


def dividir(pool, conta_id: int, lancamento_id: int, obra_ids) -> list[dict]:
    """Divide o lançamento em partes iguais entre as obras (os centavos que sobram
    ficam na primeira). O lançamento continua inteiro — ver a escolha 3."""
    ids = list(dict.fromkeys(int(i) for i in obra_ids))
    if len(ids) < 2:
        raise ValueError("Pra dividir, preciso de pelo menos duas obras.")
    with pool.connection() as c:
        lanc = _lancamento(c, conta_id, lancamento_id)
        if not lanc:
            raise ValueError("Não achei esse lançamento.")
        if lanc[3] == "pessoal":
            raise ValueError("Esse lançamento está marcado como pessoal.")
        obras = c.execute(
            "select id, centro_custo_id, nome from obras where conta_id=%s and id = any(%s)",
            (conta_id, ids)).fetchall()
        if len(obras) != len(ids):
            raise ValueError("Uma das obras não é desta conta.")
        por_id = {o[0]: o for o in obras}
        total = int(lanc[2])
        parte, resto = divmod(total, len(ids))
        c.execute("delete from lancamento_rateio where lancamento_id=%s and conta_id=%s",
                  (lancamento_id, conta_id))
        out = []
        for n, oid in enumerate(ids):
            valor = parte + (resto if n == 0 else 0)
            c.execute("""insert into lancamento_rateio (conta_id, lancamento_id,
                                                        centro_custo_id, valor_centavos)
                              values (%s,%s,%s,%s)""",
                      (conta_id, lancamento_id, por_id[oid][1], valor))
            out.append({"obra": por_id[oid][2], "valor_centavos": valor})
        c.execute("""update lancamentos set centro_custo_id=null,
                            natureza=coalesce(natureza, 'empresa')
                      where id=%s and conta_id=%s""", (lancamento_id, conta_id))
        c.commit()
    return out


#: o que tem cara de custo de obra — ver a escolha 4
_SQL_SEM_OBRA = """
    from lancamentos l
    left join plano_contas p on p.id = l.plano_conta_id
   where l.conta_id = %s and l.tipo = 'despesa' and l.natureza = 'empresa'
     and l.centro_custo_id is null
     and not exists (select 1 from lancamento_rateio r where r.lancamento_id = l.id)
     and (p.codigo in ('3.1.03', '3.1.04')
          or l.categoria in ('Insumos', 'Construcao', 'Compras', 'Servicos'))
"""


def sem_obra(pool, conta_id: int, limite: int = 50) -> dict:
    """As despesas de obra que ainda não têm obra: {n, total_centavos, itens}."""
    with pool.connection() as c:
        n, total = c.execute(
            f"select count(*), coalesce(sum(l.valor_centavos), 0) {_SQL_SEM_OBRA}",
            (conta_id,)).fetchone()
        itens = c.execute(
            f"""select l.id, l.data, l.descricao, l.categoria, l.valor_centavos
                  {_SQL_SEM_OBRA}
                 order by l.data desc, l.id desc limit %s""", (conta_id, limite)).fetchall()
    return {"n": int(n), "total_centavos": int(total),
            "itens": [{"id": i, "data": d, "descricao": desc or "", "categoria": cat,
                       "valor_centavos": int(v)} for i, d, desc, cat, v in itens]}


# ─────────────────────────────────────────────────────────────── o agente
def _brl(centavos) -> str:
    v = int(centavos or 0) / 100
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def bloco_persona(pool, conta_id: int) -> str:
    """O pedaço do prompt da conta de construção que fala das obras DELA. Vazio
    se a tabela não existir (base anterior à 351)."""
    try:
        obras = listar_obras(pool, conta_id, com_custos=False)
        falta = sem_obra(pool, conta_id, limite=0)
    except Exception:  # noqa: BLE001 — sem a 351, sem bloco
        return ""
    if obras:
        lista = "; ".join(
            f"{o['nome']} ({o['tipo']}, {o['pct']}%"
            f"{', ' + o['rotulo_status'].lower() if o['status'] != 'em_obra' else ''})"
            for o in obras)
        linhas = [
            f"OBRAS ABERTAS desta empresa (cada uma é um centro de custo): {lista}.",
            "- Despesa de obra da EMPRESA: pergunte de qual dessas obras é. Com a "
            "resposta, passe centro_custo com o NOME EXATO da obra no lancar_despesa. "
            "Se for de mais de uma (ou de todas), registre sem centro_custo e chame "
            "dividir_entre_obras com o id do lançamento.",
            "- \"quanto já gastei na casa 2?\", \"como está a obra?\" -> consultar_obra.",
            "- \"terminou o telhado da casa 3\" -> marcar_etapa (uma chamada por etapa).",
        ]
    else:
        linhas = ["OBRAS: nenhuma cadastrada ainda."]
    linhas.append(f"- Obra NOVA você não cria: quem cadastra é a empresa, no painel, "
                  f"em Obras ({LINK_OBRAS}). Mande o link.")
    if falta["n"]:
        linhas.append(
            f"- SEM OBRA: {falta['n']} despesa(s) de obra sem obra "
            f"({_brl(falta['total_centavos'])}). Se ele perguntar, ou numa hora boa (uma "
            "vez, sem insistir), ofereça distribuir: gastos_sem_obra lista, e cada um vai "
            "com por_na_obra ou dividir_entre_obras.")
    linhas += _bloco_das_casas(pool, conta_id, obras)
    return "\n".join(linhas)


def _bloco_das_casas(pool, conta_id: int, obras: list[dict]) -> list[str]:
    """O caminho do dinheiro no prompt (finance/obra_venda.py): as ferramentas do
    papel e da venda, e o que TRAVA cada casa pronta. Casa em obra não entra — o
    que trava ela é a própria obra, e isso o agente já sabe pelas etapas. Vazio
    sem a 353."""
    casas = [o for o in obras if o["tipo"] == "casa"]
    if not casas:
        return []
    try:
        from . import obra_venda as _ov
        pend = []
        for o in casas:
            sit = _ov.situacao_da_casa(pool, conta_id, o)
            partes = []
            if o["pct"] == 100 and sit["trava"]:
                partes.append(f"trava em {sit['trava']['nome'].lower()}")
            partes += sit["alertas"][:2]
            if partes:
                pend.append(f"{o['nome']}: " + "; ".join(partes))
    except Exception:  # noqa: BLE001 — sem a 353, sem o caminho
        return []
    linhas = [
        "- PAPEL DA CASA: \"saiu o habite-se da casa 2\", \"averbou a casa 1\" -> "
        "marcar_documento. VENDA: \"assinou o contrato da casa 2\", \"registrou\", "
        "\"caiu o dinheiro\" -> andar_venda. A entrada e o repasse da Caixa viram contas "
        "a receber na assinatura. Comprador e valores da venda se cadastram na ficha da "
        "obra, no painel.",
    ]
    if pend:
        linhas.append("- PENDÊNCIAS DAS CASAS (lembre no máximo uma vez por semana, sem "
                      "insistir): " + " | ".join(pend) + ".")
    return linhas


def resumo_da_obra(o: dict) -> str:
    """Uma obra em uma mensagem de WhatsApp."""
    cu = o["custos"]
    txt = (f"{o['nome']}: {_brl(cu['total'])} gastos (material {_brl(cu[MATERIAL])} · "
           f"mão de obra {_brl(cu[MAO_DE_OBRA])} · outros {_brl(cu[OUTROS])}).")
    if o.get("custo_previsto_centavos"):
        txt += f" Previsto {_brl(o['custo_previsto_centavos'])}: {o['pct_previsto']}%."
    if o.get("custo_m2"):
        txt += f" {_brl(o['custo_m2'])}/m²."
    txt += f" Etapas: {o['pct']}%"
    if o["proxima_etapa"]:
        txt += f", a próxima é {o['proxima_etapa'].lower()}"
    txt += f". Situação: {o['rotulo_status'].lower()}."
    if cu["recebido"]:
        txt += f" Recebido nesta obra: {_brl(cu['recebido'])}."
    return txt
