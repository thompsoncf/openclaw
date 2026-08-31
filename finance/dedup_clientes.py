"""Juntar cadastros duplicados de cliente/fornecedor — sem perder nada.

O QUE ESTE MÓDULO NÃO FAZ: apagar. Em nenhum caminho. Juntar dois cadastros é a
operação mais perigosa desta base, porque mexe em informação de cliente — e a
regra 0 do CLAUDE.md diz que informação de cliente não se perde. Então a fusão
aqui é feita de um jeito que dá pra voltar atrás:

  * o cadastro absorvido é ARQUIVADO (`ativo=false`), igual ao botão "Arquivar"
    que já existe na ficha — a linha continua no banco, com tudo dentro;
  * títulos, lançamentos e orçamentos que apontavam pra ele passam a apontar pro
    que ficou, e os ids movidos ficam gravados em `clientes_fusao.movido`;
  * campo vazio no que fica pode ser preenchido pelo que sai — NUNCA o contrário.
    Sobrescrever é perder, e perder não pode;
  * `desfazer()` lê o que foi gravado e devolve tudo pro lugar.

DE ONDE VEIO. Depois do conserto de 29/08/2026 (salvar de novo atualiza em vez
de cunhar registro), sobraram na Prime Eventos 5 cadastros a mais, de 4 pessoas,
em dois formatos diferentes:

  * mesmo telefone, cadastro repetido — a Ana Clara três vezes em cinco minutos,
    e a Victoria com um "s" a mais no primeiro nome ("Visctoria");
  * segundo cadastro VAZIO, criado só pra marcar o papel de fornecedor — o
    Gilvan (o cliente completo é outra linha) e o Ronaldo, cujo segundo cadastro
    ainda trocou o VAZ do sobrenome por VEZ.

Repare que nenhuma régua sozinha pega os quatro: telefone pega a Ana Clara e a
Victoria; nome exato pega o Gilvan; e só a semelhança de nome pega o Ronaldo,
cujo segundo cadastro não tem telefone nem documento pra comparar. Por isso
`candidatos()` roda quatro réguas e diz qual delas apontou cada par.

QUEM DECIDE É O DONO. Este módulo nunca funde sozinho: `candidatos()` só sugere,
`previa()` mostra exatamente o que aconteceria, e `fundir()` só roda com o par
que veio da tela. Um par por vez.
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher

from finance import clientes as _cli

# Tabelas que guardam um `clientes.id`. Não é chute: são as três que aparecem em
# `information_schema` apontando pra cá (titulos e lancamentos por foreign key,
# orcamentos por coluna solta — a 152 deixou sem FK de propósito). Se um dia
# nascer uma quarta, ela entra aqui e o desfazer passa a cuidar dela junto.
_TABELAS_REF = (
    ("titulos", "cliente_id", "conta_id"),
    ("lancamentos", "cliente_id", "conta_id"),
    ("orcamentos", "cliente_id", "conta_id"),
)

# Campos da RELAÇÃO que a fusão pode preencher no vencedor, se estiverem vazios
# nele. Telefone e e-mail não estão aqui porque moram na IDENTIDADE (`pessoas`)
# e são tratados à parte, na `previa`.
_CAMPOS_CLIENTE = ("cidade", "uf", "endereco", "cep", "obs")

# Quando dois nomes são "muito parecidos". A comparação é TOKEN A TOKEN, não
# no texto inteiro, e exige: mesma quantidade de palavras, exatamente UMA
# diferente, e essa uma separada por uma letra (duas, se for palavra longa).
#
# Por que token a token. A primeira versão comparava a string inteira com
# `SequenceMatcher.ratio()`. Num teste com 2.000 cadastros sintéticos ela
# apontou 7.217 "duplicatas": "Fernanda G Oliveira" e "Fernanda H Oliveira"
# diferem numa letra, dão 0.95, e são duas pessoas. Uma letra pesa pouco no
# texto todo e muito na palavra — e é na palavra que o erro de digitação mora.
# Com a regra por token, o mesmo par cai (a palavra que difere tem 1 letra) e os
# casos reais continuam de pé: VAZ/VEZ e Victoria/Visctoria erram uma letra
# dentro de uma palavra de verdade.
#
# _MIN_NOME é um piso no nome inteiro: em nome curto não sobra contexto pra
# afirmar nada. E o que passar daqui é SUGESTÃO — falso positivo custa uma
# olhada do dono na tela de revisão, falso negativo custa um cadastro duplicado
# pra sempre. A régua está calibrada nesse sentido, e nada aqui funde sozinho.
_MIN_NOME = 12
_MIN_TOKEN = 3
_EDICAO_TOKEN_CURTO = 1     # até 5 letras: só uma diferença
_EDICAO_TOKEN_LONGO = 2

# Trava de custo: comparar nome com nome é quadrático. As buscas exatas
# (documento, telefone, nome igual) rodam em qualquer tamanho de base; a régua
# difusa só roda dentro de BLOCOS de gente que já divide o sobrenome E a
# primeira letra do nome.
#
# Por que os dois, e não só o sobrenome: numa base brasileira grande "Silva"
# sozinho junta uma fatia enorme do cadastro, o bloco estoura o teto aqui de
# baixo e a régua difusa fica desligada justamente onde ela seria útil. Somar a
# inicial reparte "Silva" em ~26 blocos e devolve a régua ao jogo. O preço é
# claro: erro de digitação NA PRIMEIRA LETRA passa batido — mas os quatro casos
# reais da Prime (VAZ/VEZ, Victoria/Visctoria) erram no meio, não no começo.
#
# Bloco maior que o teto é ignorado inteiro: travar a tela de Clientes por um
# palpite é pior do que deixar de dar o palpite.
_MAX_BLOCO = 40

# Teto da base varrida de uma vez. Medido com nomes realistas: 500 cadastros em
# 7 ms, 2.000 em 79 ms. O teto existe porque `candidatos()` também alimenta o
# aviso no topo da aba Clientes, que roda a cada abertura da tela — e esta base
# já teve um episódio de lentidão geral por trabalho extra em rota quente.
_MAX_BASE = 2000


def _norm(s: str | None) -> str:
    """Nome comparável: sem acento, sem caixa, sem espaço sobrando."""
    t = unicodedata.normalize("NFKD", (s or "").strip())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).lower()


def _edicao(a: str, b: str) -> int:
    """Quantos caracteres separam duas palavras. Letra trocada dá 1, letra a
    mais dá 1 — que é a cara de um erro de digitação."""
    return sum(max(i2 - i1, j2 - j1)
               for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b).get_opcodes()
               if tag != "equal")


def _quase_igual(na: str, nb: str) -> bool:
    """Dois nomes normalizados que diferem por um erro de digitação."""
    ta, tb = na.split(), nb.split()
    if len(ta) != len(tb) or len(na) < _MIN_NOME:
        return False
    difs = [(x, y) for x, y in zip(ta, tb) if x != y]
    if len(difs) != 1:
        return False
    x, y = difs[0]
    if min(len(x), len(y)) < _MIN_TOKEN:
        return False                     # inicial do meio não diz nada
    teto = (_EDICAO_TOKEN_CURTO if min(len(x), len(y)) <= 5
            else _EDICAO_TOKEN_LONGO)
    return _edicao(x, y) <= teto


def _doc(c: dict) -> str:
    return _cli._so_digitos(c.get("cnpj") or c.get("cpf")) or ""


def _preenchidos(c: dict) -> int:
    """Quanto cadastro tem ali dentro. Serve pra sugerir quem fica."""
    campos = ("telefone", "email", "cidade", "uf", "endereco", "cep", "obs",
              "aniversario")
    return sum(1 for k in campos if str(c.get(k) or "").strip())


# ---------------------------------------------------------------------------
# Achar os suspeitos
# ---------------------------------------------------------------------------
def _pares(lista: list[dict]) -> dict[tuple[int, int], str]:
    """Pares suspeitos -> motivo. Quatro réguas, da mais certa pra mais frouxa;
    quando duas apontam o mesmo par, fica a primeira (a mais forte)."""
    por_doc: dict[str, list[dict]] = {}
    por_tel: dict[str, list[dict]] = {}
    por_nome: dict[str, list[dict]] = {}
    por_bloco: dict[tuple, list[dict]] = {}
    normalizados = {c["id"]: _norm(c.get("nome")) for c in lista}
    for c in lista:
        d = _doc(c)
        if d:
            por_doc.setdefault(d, []).append(c)
        t = _cli._n8(c.get("telefone"))
        if t:
            por_tel.setdefault(t, []).append(c)
        n = normalizados[c["id"]]
        if n:
            por_nome.setdefault(n, []).append(c)
            por_bloco.setdefault((n.split()[-1], n[0]), []).append(c)

    achados: dict[tuple[int, int], str] = {}

    def marcar(a: dict, b: dict, motivo: str) -> None:
        if a["id"] == b["id"]:
            return
        # Documentos DIFERENTES são duas pessoas diferentes, e ponto. Nem o nome
        # idêntico nem o telefone dividido derrubam isso — pai e filho de mesmo
        # nome no mesmo celular é caso comum, e fundir os dois seria irreparável.
        da, db = _doc(a), _doc(b)
        if da and db and da != db:
            return
        chave = (min(a["id"], b["id"]), max(a["id"], b["id"]))
        achados.setdefault(chave, motivo)

    for grupo in por_doc.values():
        for i, a in enumerate(grupo):
            for b in grupo[i + 1:]:
                marcar(a, b, "mesmo CNPJ" if len(_doc(a)) == 14 else "mesmo CPF")
    for grupo in por_tel.values():
        for i, a in enumerate(grupo):
            for b in grupo[i + 1:]:
                marcar(a, b, "mesmo telefone")
    for grupo in por_nome.values():
        for i, a in enumerate(grupo):
            for b in grupo[i + 1:]:
                marcar(a, b, "mesmo nome")
    for grupo in por_bloco.values():
        if len(grupo) > _MAX_BLOCO:
            continue
        for i, a in enumerate(grupo):
            na = normalizados[a["id"]]
            for b in grupo[i + 1:]:
                if _quase_igual(na, normalizados[b["id"]]):
                    marcar(a, b, "nome muito parecido")
    return achados


def _grupos(lista: list[dict], pares: dict) -> list[list[int]]:
    """Junta os pares em grupos: Ana Clara aparece em (25,26), (25,27) e (26,27)
    e tem que sair como UM grupo de três, não três avisos."""
    pai: dict[int, int] = {}

    def raiz(x: int) -> int:
        r = x
        while pai.get(r, r) != r:
            r = pai[r]
        while pai.get(x, x) != x:            # encurta o caminho pra próxima vez
            pai[x], x = r, pai[x]
        return r

    for a, b in pares:
        ra, rb = raiz(a), raiz(b)
        if ra != rb:
            pai[max(ra, rb)] = min(ra, rb)
    junto: dict[int, list[int]] = {}
    for a, b in pares:
        junto.setdefault(raiz(a), []).extend((a, b))
    ordem = {c["id"]: i for i, c in enumerate(lista)}
    return [sorted(set(v), key=lambda i: ordem.get(i, 0)) for v in junto.values()]


def candidatos(pool, dono_id: int) -> list[dict]:
    """Grupos de cadastros que parecem ser a mesma pessoa.

    Cada grupo traz `sugerido` (quem deveria ficar — o mais completo, com
    desempate pelo que tem documento e depois pelo mais antigo) e `motivos`, com
    a régua que apontou cada um dos outros. Só sugere; não muda nada.
    """
    lista = _cli.listar_clientes(pool, dono_id, limite=_MAX_BASE)
    if len(lista) < 2:
        return []
    pares = _pares(lista)
    if not pares:
        return []
    por_id = {c["id"]: c for c in lista}
    saida = []
    for ids in _grupos(lista, pares):
        membros = [por_id[i] for i in ids if i in por_id]
        if len(membros) < 2:
            continue
        # quem fica: mais campos preenchidos > tem documento > mais antigo (id menor)
        vencedor = max(membros,
                       key=lambda c: (_preenchidos(c), 1 if _doc(c) else 0, -c["id"]))
        motivos = {}
        for outro in membros:
            if outro["id"] == vencedor["id"]:
                continue
            chave = (min(vencedor["id"], outro["id"]), max(vencedor["id"], outro["id"]))
            motivos[outro["id"]] = pares.get(chave) or "grupo"
        saida.append({"clientes": membros, "sugerido": vencedor["id"],
                      "motivos": motivos})
    saida.sort(key=lambda g: g["sugerido"])
    return saida


# ---------------------------------------------------------------------------
# O que a fusão faria
# ---------------------------------------------------------------------------
def _tabelas_presentes(conexao) -> set:
    """Quais das três tabelas de referência existem, com as colunas que a fusão
    usa. Perguntado ao catálogo em vez de descoberto no erro: dentro da fusão o
    SELECT roda na MESMA transação do resto, e um `except` com rollback ali
    desfaria a trava das duas linhas no meio do caminho — o pior lugar possível
    pra descobrir que uma tabela não existe."""
    nomes = [t for t, _, _ in _TABELAS_REF]
    linhas = conexao.execute(
        "select table_name, column_name from information_schema.columns "
        " where table_schema='public' and table_name = any(%s)",
        (nomes,)).fetchall()
    tem: dict[str, set] = {}
    for t, col in linhas:
        tem.setdefault(t, set()).add(col)
    return {t for t, col, col_conta in _TABELAS_REF
            if {col, col_conta} <= tem.get(t, set())}


def _contar_refs(conexao, dono_id: int, cliente_id: int) -> dict[str, list[int]]:
    presentes = _tabelas_presentes(conexao)
    refs: dict[str, list[int]] = {}
    for tabela, col, col_conta in _TABELAS_REF:
        if tabela not in presentes:
            continue
        linhas = conexao.execute(
            f"select id from {tabela} where {col}=%s and {col_conta}=%s",
            (cliente_id, dono_id)).fetchall()
        if linhas:
            refs[tabela] = [int(r[0]) for r in linhas]
    return refs


def previa(pool, dono_id: int, vencedor_id: int, perdedor_id: int) -> dict:
    """O que aconteceria — sem fazer. É o que a tela mostra antes de confirmar.

    Devolve {"vencedor","perdedor","campos","campos_pessoa","papeis","refs",
             "impedimento"}. Com `impedimento` preenchido a fusão está barrada e
    o botão não aparece.
    """
    v = _cli.obter_cliente(pool, dono_id, vencedor_id)
    p = _cli.obter_cliente(pool, dono_id, perdedor_id)
    base = {"vencedor": v, "perdedor": p, "campos": {}, "campos_pessoa": {},
            "papeis": [], "refs": {}}
    if v is None or p is None:
        return {**base, "impedimento": "Um dos cadastros não existe mais."}
    if vencedor_id == perdedor_id:
        return {**base, "impedimento": "São o mesmo cadastro."}

    dv, dp = _doc(v), _doc(p)
    if dv and dp and dv != dp:
        return {**base, "impedimento":
                "Os dois têm documento, e são documentos diferentes — são duas "
                "pessoas. Se um deles está errado, corrija na ficha antes."}
    if dp and not dv:
        # Barrado de propósito, e a tela oferece inverter num clique: seguir em
        # frente jogaria o único documento da dupla pra dentro do arquivado.
        return {**base, "impedimento":
                "O cadastro que sairia é o que tem documento. Inverta — quem tem "
                "CPF/CNPJ é quem deve ficar."}

    campos = {k: (p.get(k) or "").strip() for k in _CAMPOS_CLIENTE
              if str(p.get(k) or "").strip() and not str(v.get(k) or "").strip()}
    cp = {}
    if str(p.get("telefone") or "").strip() and not str(v.get("telefone") or "").strip():
        cp["celular"] = _cli._so_digitos(p["telefone"])
    if str(p.get("email") or "").strip() and not str(v.get("email") or "").strip():
        cp["email"] = p["email"].strip().lower()
    papeis = [k for k in ("eh_cliente", "eh_fornecedor")
              if p.get(k) and not v.get(k)]
    with pool.connection() as c:
        refs = _contar_refs(c, dono_id, perdedor_id)
    return {**base, "campos": campos, "campos_pessoa": cp, "papeis": papeis,
            "refs": refs, "impedimento": None}


# ---------------------------------------------------------------------------
# Fundir (e voltar atrás)
# ---------------------------------------------------------------------------
def fundir(pool, dono_id: int, vencedor_id: int, perdedor_id: int, *,
           motivo: str | None = None, feita_por: int | None = None) -> dict:
    """Funde de verdade. Levanta ValueError se houver impedimento.

    Tudo numa transação só: ou o cadastro inteiro se junta, ou nada acontece.
    Devolve a prévia realizada, acrescida de `fusao_id`.
    """
    pv = previa(pool, dono_id, vencedor_id, perdedor_id)
    if pv["impedimento"]:
        raise ValueError(pv["impedimento"])
    v, p = pv["vencedor"], pv["perdedor"]
    movido = {"refs": {}, "campos": {}, "campos_pessoa": {}, "papeis": []}

    with pool.connection() as c:
        # Trava as duas linhas: sem isso, dois cliques ao mesmo tempo no mesmo
        # par moveriam as referências duas vezes.
        travadas = c.execute(
            "select id, ativo, eh_cliente, eh_fornecedor from clientes "
            " where id in (%s,%s) and dono_id=%s for update",
            (vencedor_id, perdedor_id, dono_id)).fetchall()
        if len(travadas) != 2 or not all(t[1] for t in travadas):
            raise ValueError("Um dos cadastros mudou enquanto você olhava. "
                             "Recarregue a tela.")
        # O papel é decidido pela linha TRAVADA, não pela prévia. Se alguém
        # marcou "fornecedor" no vencedor entre a tela e o clique, esta fusão não
        # pode registrar que foi ela quem ligou — senão o desfazer apagaria a
        # marcação de outra pessoa.
        _atual = {t[0]: {"eh_cliente": t[2], "eh_fornecedor": t[3]} for t in travadas}
        papeis = [k for k in ("eh_cliente", "eh_fornecedor")
                  if _atual[perdedor_id][k] and not _atual[vencedor_id][k]]

        # Recontadas AQUI DENTRO, não na prévia: entre olhar a tela e clicar,
        # a vendedora pode ter lançado mais um título no cadastro que vai sair.
        # A prévia serve pra mostrar; quem manda na hora de mover é este SELECT.
        refs = _contar_refs(c, dono_id, perdedor_id)
        for tabela, col, col_conta in _TABELAS_REF:
            ids = refs.get(tabela)
            if not ids:
                continue
            c.execute(
                f"update {tabela} set {col}=%s "
                f"where {col}=%s and {col_conta}=%s and id = any(%s)",
                (vencedor_id, perdedor_id, dono_id, ids))
            movido["refs"][tabela] = ids

        if pv["campos"] or papeis:
            sets, vals = [], []
            for k, val in pv["campos"].items():
                # "só se ainda estiver vazio" — a prévia foi lida fora desta
                # transação, e preencher em cima de algo que apareceu no meio do
                # caminho seria sobrescrever, que é a única coisa proibida aqui.
                sets.append(f"{k} = case when coalesce({k},'')='' then %s else {k} end")
                vals.append(val)
                movido["campos"][k] = val
            for k in papeis:
                sets.append(f"{k}=true")
                movido["papeis"].append(k)
            sets.append("atualizado_em=now()")
            vals += [vencedor_id, dono_id]
            c.execute(f"update clientes set {', '.join(sets)} "
                      f"where id=%s and dono_id=%s", vals)

        if pv["campos_pessoa"] and v.get("pessoa_id"):
            sets, vals = [], []
            for k, val in pv["campos_pessoa"].items():
                sets.append(f"{k} = case when coalesce({k},'')='' then %s else {k} end")
                vals.append(val)
                movido["campos_pessoa"][k] = val
            sets.append("atualizado_em=now()")
            vals.append(v["pessoa_id"])
            c.execute(f"update pessoas set {', '.join(sets)} where id=%s", vals)
            # o cache em `clientes` acompanha (mesmo par que atualizar_cliente faz)
            if "celular" in pv["campos_pessoa"]:
                c.execute("update clientes set telefone=%s where id=%s and dono_id=%s",
                          (pv["campos_pessoa"]["celular"], vencedor_id, dono_id))
            if "email" in pv["campos_pessoa"]:
                c.execute("update clientes set email=%s where id=%s and dono_id=%s",
                          (pv["campos_pessoa"]["email"], vencedor_id, dono_id))

        # ARQUIVA — não apaga. A linha do perdedor continua inteira no banco.
        c.execute("update clientes set ativo=false, atualizado_em=now() "
                  "where id=%s and dono_id=%s", (perdedor_id, dono_id))

        row = c.execute(
            """insert into clientes_fusao
                 (dono_id, vencedor_id, perdedor_id, motivo, movido, feita_por)
               values (%s,%s,%s,%s,%s,%s) returning id""",
            (dono_id, vencedor_id, perdedor_id, motivo,
             json.dumps(movido), feita_por)).fetchone()
        c.commit()
    return {**pv, "refs": movido["refs"], "papeis": movido["papeis"],
            "fusao_id": int(row[0]), "nome": v.get("nome")}


def desfazer(pool, dono_id: int, fusao_id: int, *,
             desfeita_por: int | None = None) -> dict:
    """Volta atrás: reativa o arquivado, devolve as referências e tira do
    vencedor os campos que ESTA fusão preencheu.

    Campo que alguém editou depois fica como está — desfazer não pode virar uma
    segunda forma de perder dado. Por isso cada campo só volta a NULL se o valor
    de hoje ainda for exatamente o que a fusão escreveu.
    """
    with pool.connection() as c:
        r = c.execute(
            "select vencedor_id, perdedor_id, movido, desfeita_em "
            "from clientes_fusao where id=%s and dono_id=%s for update",
            (fusao_id, dono_id)).fetchone()
        if not r:
            raise ValueError("Fusão não encontrada.")
        if r[3] is not None:
            raise ValueError("Essa fusão já foi desfeita.")
        vencedor_id, perdedor_id = int(r[0]), int(r[1])
        movido = r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}")

        for tabela, col, col_conta in _TABELAS_REF:
            ids = (movido.get("refs") or {}).get(tabela)
            if not ids:
                continue
            c.execute(
                f"update {tabela} set {col}=%s "
                f"where {col}=%s and {col_conta}=%s and id = any(%s)",
                (perdedor_id, vencedor_id, dono_id, ids))

        sets, vals = [], []
        for k, val in (movido.get("campos") or {}).items():
            sets.append(f"{k} = case when {k}=%s then null else {k} end")
            vals.append(val)
        for k in (movido.get("papeis") or []):
            sets.append(f"{k}=false")
        if sets:
            sets.append("atualizado_em=now()")
            vals += [vencedor_id, dono_id]
            c.execute(f"update clientes set {', '.join(sets)} "
                      f"where id=%s and dono_id=%s", vals)

        cp = movido.get("campos_pessoa") or {}
        if cp:
            pid = c.execute("select pessoa_id from clientes where id=%s and dono_id=%s",
                            (vencedor_id, dono_id)).fetchone()
            if pid and pid[0]:
                sets, vals = [], []
                for k, val in cp.items():
                    sets.append(f"{k} = case when {k}=%s then null else {k} end")
                    vals.append(val)
                sets.append("atualizado_em=now()")
                vals.append(pid[0])
                c.execute(f"update pessoas set {', '.join(sets)} where id=%s", vals)
                for col, val in (("telefone", cp.get("celular")),
                                 ("email", cp.get("email"))):
                    if val:
                        c.execute(f"update clientes set {col} = case when {col}=%s "
                                  f"then null else {col} end where id=%s and dono_id=%s",
                                  (val, vencedor_id, dono_id))

        c.execute("update clientes set ativo=true, atualizado_em=now() "
                  "where id=%s and dono_id=%s", (perdedor_id, dono_id))
        c.execute("update clientes_fusao set desfeita_em=now(), desfeita_por=%s "
                  "where id=%s", (desfeita_por, fusao_id))
        c.commit()
    return {"vencedor_id": vencedor_id, "perdedor_id": perdedor_id}


def historico(pool, dono_id: int, limite: int = 20) -> list[dict]:
    """As fusões desta loja, mais recentes primeiro — pra ter onde clicar em
    desfazer. Traz o nome de cada lado inclusive quando o perdedor está
    arquivado (a ficha some da lista, o histórico não)."""
    with pool.connection() as c:
        rows = c.execute(
            """select f.id, f.vencedor_id, f.perdedor_id, f.motivo, f.criado_em,
                      f.desfeita_em,
                      coalesce(pv.nome, cv.nome), coalesce(pp.nome, cp.nome)
                 from clientes_fusao f
                 join clientes cv on cv.id = f.vencedor_id
                 join clientes cp on cp.id = f.perdedor_id
                 left join pessoas pv on pv.id = cv.pessoa_id
                 left join pessoas pp on pp.id = cp.pessoa_id
                where f.dono_id=%s order by f.id desc limit %s""",
            (dono_id, limite)).fetchall()
    return [{"id": r[0], "vencedor_id": r[1], "perdedor_id": r[2], "motivo": r[3],
             "criado_em": r[4], "desfeita_em": r[5],
             "vencedor_nome": r[6], "perdedor_nome": r[7]} for r in rows]
