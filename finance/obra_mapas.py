"""O mapa das obras — a planta do empreendimento com os lotes riscados (migração 482).

Desenho aprovado pelo dono em 02/10/2026 (docs/mockups/obras_mapa_3d.html,
"segue as recomendações"). PR 1 de 3: a área, a planta, o editor de riscar, o
mapa 2D/3D e o perfil com a casa por camadas.

POR QUE RISCAR, E NÃO EXTRAIR: a planta real que testamos (um loteamento de
cliente, em PDF vetorial) tem os lotes em 362 segmentos de reta soltos — nenhum
retângulo fechado pra um programa agarrar. Então a planta vira FUNDO, e a
empresa risca os lotes por cima, com a ferramenta de fileira pra não ser um por
um. Cinco gestos riscam o loteamento inteiro.

A UNIDADE É 1000: todo lote é guardado com a largura do desenho valendo 1000
(x, y, larg, alt inteiros), como o mapa dos stands (web/loja_stands.py). A tela
escala pro tamanho que tiver; o banco não conhece pixel.

A PLANTA VAI PRO BUCKET PRIVADO (finance/comprovantes.py, o mesmo cano das
fotos de obra): planta de loteamento tem nome de empreendimento e desenho de
área que ainda nem foi a registro — não vira URL pública. O banco guarda o
caminho; quem entrega é a rota do painel, com sessão conferida. PDF é
convertido pra imagem na subida (pymupdf, que já está nos requirements).

O LOTE NÃO É A OBRA: o risco é desenho. Ligar o lote a uma obra é opcional
("vago" é lote sem casa; "terceiro" é o caso real do dono de outra gleba no
mesmo PDF — "essa azul e rosa não é minha"). Apagar um risco não toca na obra.
"""
from __future__ import annotations

import io
import re
import time
import uuid

from . import obras as _ob

TIPOS_PLANTA = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/jpg": "jpg",
                "image/png": "png", "image/webp": "webp"}
MAX_BYTES = 10 * 1024 * 1024
MAX_OBS = 600               # o "O que tem no arquivo" (migração 202610031642)
SITUACOES = ("meu", "vago", "terceiro")
_ALTURA_PADRAO = 520        # a proporção do mockup aprovado (1000 × 520)

_COLS_LOTE = ("id", "obra_id", "rotulo", "x", "y", "larg", "alt", "situacao")


# ── a área de obras (o empreendimento) ────────────────────────────────────
def criar(pool, conta_id: int, nome: str, cidade: str = "") -> dict:
    nome = " ".join((nome or "").split())[:60]
    if not nome:
        raise ValueError("Dê um nome — por exemplo, Santa Marina 2.")
    with pool.connection() as c:
        if c.execute("select 1 from obra_mapas where conta_id=%s and lower(nome)=lower(%s)",
                     (conta_id, nome)).fetchone():
            raise ValueError(f"Já existe a área “{nome}”.")
        mid = c.execute("""insert into obra_mapas (conta_id, nome, cidade)
                           values (%s,%s,%s) returning id""",
                        (conta_id, nome, " ".join((cidade or "").split())[:60])).fetchone()[0]
        c.commit()
    return {"id": mid, "nome": nome}


def editar(pool, conta_id: int, mapa_id: int, nome: str, cidade: str = "") -> None:
    nome = " ".join((nome or "").split())[:60]
    if not nome:
        raise ValueError("A área precisa de um nome.")
    with pool.connection() as c:
        if c.execute("""select 1 from obra_mapas where conta_id=%s and lower(nome)=lower(%s)
                          and id<>%s""", (conta_id, nome, mapa_id)).fetchone():
            raise ValueError(f"Já existe a área “{nome}”.")
        c.execute("update obra_mapas set nome=%s, cidade=%s where id=%s and conta_id=%s",
                  (nome, " ".join((cidade or "").split())[:60], mapa_id, conta_id))
        c.commit()


def apagar(pool, conta_id: int, mapa_id: int) -> None:
    """Some a área e os riscos dela (são desenho, não dado financeiro); as obras
    ligadas ficam intactas. A planta no bucket sai best-effort."""
    m = obter(pool, conta_id, mapa_id)
    if not m:
        return
    with pool.connection() as c:
        c.execute("delete from obra_mapas where id=%s and conta_id=%s", (mapa_id, conta_id))
        c.commit()
    if m["planta_caminho"]:
        from .comprovantes import apagar as _remover
        _remover(m["planta_caminho"])


def listar(pool, conta_id: int) -> list[dict]:
    try:
        with pool.connection() as c:
            if c.execute("select to_regclass('public.obra_mapas')").fetchone()[0] is None:
                return []
            rows = c.execute(
                """select m.id, m.nome, m.cidade, m.planta_caminho is not null, m.altura,
                          (select count(*) from obra_mapa_lotes l
                            where l.mapa_id = m.id and l.conta_id = m.conta_id)
                     from obra_mapas m where m.conta_id=%s
                    order by lower(m.nome), m.id""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001 — instalação sem a 482
        return []
    return [{"id": r[0], "nome": r[1], "cidade": r[2], "tem_planta": bool(r[3]),
             "altura": int(r[4]), "n_lotes": int(r[5])} for r in rows]


def obter(pool, conta_id: int, mapa_id: int) -> dict | None:
    with pool.connection() as c:
        r = c.execute("""select id, nome, cidade, planta_caminho, altura from obra_mapas
                          where id=%s and conta_id=%s""", (mapa_id, conta_id)).fetchone()
        if not r:
            return None
        try:                                # o que tem no arquivo (202610031642)
            with c.transaction():
                extra = c.execute("""select planta_obs, planta_nome from obra_mapas
                                      where id=%s and conta_id=%s""", (mapa_id, conta_id)).fetchone()
        except Exception:  # noqa: BLE001 — sem a migração
            extra = ("", "")
    return {"id": r[0], "nome": r[1], "cidade": r[2], "planta_caminho": r[3],
            "altura": int(r[4]), "tem_planta": bool(r[3]),
            "planta_obs": (extra or ("", ""))[0] or "", "planta_nome": (extra or ("", ""))[1] or ""}


def salvar_obs(pool, conta_id: int, mapa_id: int, obs: str | None) -> None:
    """O "O que tem no arquivo": o que o desenho não deixa claro (quais quadras,
    quais casas são nossas, o que é vago ou de terceiro, onde é a entrada) — quem
    risca os lotes lê isso no editor. Linhas mantidas, espaços sobrando não."""
    linhas = [" ".join(ln.split()) for ln in (obs or "").replace("\r", "").split("\n")]
    texto = "\n".join(ln for ln in linhas if ln)[:MAX_OBS]
    with pool.connection() as c:
        if c.execute("update obra_mapas set planta_obs=%s where id=%s and conta_id=%s",
                     (texto, mapa_id, conta_id)).rowcount == 0:
            raise ValueError("Área não encontrada.")
        c.commit()


# ── a planta (o fundo do mapa) ────────────────────────────────────────────
def _pdf_pra_png(conteudo: bytes, pagina: int = 1) -> tuple[bytes, int, int]:
    """A página `pagina` (1, 2, …) do PDF como PNG (bytes, largura, altura).
    ValueError se o arquivo não abrir ou a página não existir — a mensagem vai
    pra tela."""
    try:
        import pymupdf
        doc = pymupdf.open(stream=conteudo, filetype="pdf")
        try:
            if doc.needs_pass:
                raise ValueError("Esse PDF tem senha — salve uma cópia sem senha (ou imprima em PDF) "
                                 "e mande de novo.")
            n = doc.page_count
            if not 1 <= pagina <= n:
                raise ValueError(f"Esse PDF tem {n} página{'s' if n != 1 else ''} — "
                                 f"escolha a página de 1 a {n}." if n > 1 else
                                 "Esse PDF tem uma página só — deixe a página 1.")
            pag = doc[pagina - 1]
            # ~2000 px de largura: nítido no zoom do editor sem estourar o bucket
            zoom = min(4.0, max(1.0, 2000.0 / float(pag.rect.width or 600)))
            pix = pag.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            return pix.tobytes("png"), int(pix.width), int(pix.height)
        finally:
            doc.close()
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Não consegui ler esse PDF: {e}")


def _tamanho_imagem(conteudo: bytes) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(conteudo)) as img:
            return int(img.width), int(img.height)
    except Exception:  # noqa: BLE001
        raise ValueError("Não consegui abrir essa imagem.")


def guardar_planta(pool, conta_id: int, mapa_id: int, conteudo: bytes,
                   content_type: str, *, pagina: int = 1, nome_arquivo: str = "",
                   subir=None, remover=None) -> dict:
    """Sobe a planta (PDF vira PNG aqui, na página `pagina`) pro bucket privado e
    guarda o caminho, a proporção e o nome do arquivo. `subir`/`remover` são o
    cano do Storage, injetáveis nos testes; por padrão, os de
    finance/comprovantes.py."""
    m = obter(pool, conta_id, mapa_id)
    if not m:
        raise ValueError("Área não encontrada.")
    if not conteudo:
        raise ValueError("O arquivo veio vazio.")
    if len(conteudo) > MAX_BYTES:
        raise ValueError("Arquivo muito grande (máximo 10 MB).")
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct not in TIPOS_PLANTA:
        raise ValueError("Aceito a planta em PDF ou imagem (JPG, PNG, WEBP).")
    pdf = ct == "application/pdf"
    if pdf:
        conteudo, w, h = _pdf_pra_png(conteudo, pagina)
        ct = "image/png"
    else:
        w, h = _tamanho_imagem(conteudo)
        ct = "image/jpeg" if ct == "image/jpg" else ct
    altura = max(200, min(4000, int(round(1000 * h / max(1, w)))))
    if subir is None:
        from .comprovantes import subir_em as subir
    caminho = (f"mapas/{conta_id}/{mapa_id}-{int(time.time())}-"
               f"{uuid.uuid4().hex[:10]}.{TIPOS_PLANTA.get(ct, 'png')}")
    subir(caminho, conteudo, ct)
    nome = " ".join((nome_arquivo or "").replace("\\", "/").split("/")[-1].split())[:100]
    if nome and pdf and pagina > 1:
        nome += f" · página {pagina}"
    with pool.connection() as c:
        c.execute("update obra_mapas set planta_caminho=%s, altura=%s where id=%s and conta_id=%s",
                  (caminho, altura, mapa_id, conta_id))
        try:                                # o nome do arquivo (202610031642)
            with c.transaction():
                c.execute("update obra_mapas set planta_nome=%s where id=%s and conta_id=%s",
                          (nome, mapa_id, conta_id))
        except Exception:  # noqa: BLE001 — sem a migração
            pass
        c.commit()
    if m["planta_caminho"] and m["planta_caminho"] != caminho:
        if remover is None:
            from .comprovantes import apagar as remover
        remover(m["planta_caminho"])
    return {"caminho": caminho, "altura": altura}


# ── os riscos (os lotes) ──────────────────────────────────────────────────
def lotes(pool, conta_id: int, mapa_id: int) -> list[dict]:
    with pool.connection() as c:
        rows = c.execute(f"""select {', '.join(_COLS_LOTE)} from obra_mapa_lotes
                              where conta_id=%s and mapa_id=%s order by y, x, id""",
                         (conta_id, mapa_id)).fetchall()
    return [dict(zip(_COLS_LOTE, r)) for r in rows]


def _validar_lote(d: dict) -> dict:
    try:
        x, y = int(d.get("x")), int(d.get("y"))
        larg, alt = int(d.get("larg")), int(d.get("alt"))
    except (TypeError, ValueError):
        raise ValueError("Um dos riscos veio sem posição.")
    if not (0 <= x < 1000 and 0 <= y < 6000):
        raise ValueError("Tem um risco fora da planta.")
    # a divisão da fileira arredonda; o que passar da borda é aparado, não recusado
    larg, alt = min(larg, 1000 - x), min(alt, 6000 - y)
    if larg < 8 or alt < 8:
        raise ValueError("Tem um risco pequeno demais — apague e risque de novo.")
    sit = (d.get("situacao") or "meu").strip()
    if sit not in SITUACOES:
        raise ValueError("Situação de lote desconhecida.")
    obra = d.get("obra_id")
    return {"id": d.get("id"), "rotulo": " ".join(str(d.get("rotulo") or "").split())[:20],
            "x": x, "y": y, "larg": larg, "alt": alt, "situacao": sit,
            "obra_id": int(obra) if obra else None}


def salvar_lotes(pool, conta_id: int, mapa_id: int, dados: list[dict]) -> int:
    """O desenho inteiro de uma vez: upsert pelo id, e o risco que não veio some.
    Valida tudo ANTES de escrever — ou salva o desenho todo, ou nada."""
    if obter(pool, conta_id, mapa_id) is None:
        raise ValueError("Área não encontrada.")
    if len(dados) > 400:
        raise ValueError("São riscos demais pra um mapa só (máximo 400).")
    limpos = [_validar_lote(d) for d in dados]
    obra_ids = [l["obra_id"] for l in limpos if l["obra_id"]]
    if len(obra_ids) != len(set(obra_ids)):
        raise ValueError("A mesma casa está ligada a dois lotes.")
    with pool.connection() as c:
        if obra_ids:
            ok = {r[0] for r in c.execute(
                "select id from obras where conta_id=%s and id = any(%s)",
                (conta_id, obra_ids)).fetchall()}
            if set(obra_ids) - ok:
                raise ValueError("Uma das casas ligadas não é desta conta.")
            fora = c.execute("""select 1 from obra_mapa_lotes
                                 where obra_id = any(%s) and mapa_id<>%s limit 1""",
                             (obra_ids, mapa_id)).fetchone()
            if fora:
                raise ValueError("Uma dessas casas já está num lote de outra área.")
        existentes = {r[0] for r in c.execute(
            "select id from obra_mapa_lotes where conta_id=%s and mapa_id=%s",
            (conta_id, mapa_id)).fetchall()}
        ficam = set()
        for l in limpos:
            lid = l["id"] if l["id"] in existentes else None
            if lid:
                c.execute("""update obra_mapa_lotes
                                set rotulo=%s, x=%s, y=%s, larg=%s, alt=%s, situacao=%s, obra_id=%s
                              where id=%s and conta_id=%s and mapa_id=%s""",
                          (l["rotulo"], l["x"], l["y"], l["larg"], l["alt"], l["situacao"],
                           l["obra_id"], lid, conta_id, mapa_id))
            else:
                lid = c.execute("""insert into obra_mapa_lotes
                                       (conta_id, mapa_id, obra_id, rotulo, x, y, larg, alt, situacao)
                                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                                (conta_id, mapa_id, l["obra_id"], l["rotulo"], l["x"], l["y"],
                                 l["larg"], l["alt"], l["situacao"])).fetchone()[0]
            ficam.add(lid)
        sobra = existentes - ficam
        if sobra:
            c.execute("delete from obra_mapa_lotes where conta_id=%s and mapa_id=%s and id = any(%s)",
                      (conta_id, mapa_id, list(sobra)))
        # sem planta, o chão cresce pra caber o desenho; com planta, a proporção é dela
        m = c.execute("select planta_caminho from obra_mapas where id=%s", (mapa_id,)).fetchone()
        if m and not m[0]:
            alt = max([l["y"] + l["alt"] for l in limpos], default=0)
            c.execute("update obra_mapas set altura=%s where id=%s",
                      (max(300, min(6000, alt + 30)) if limpos else _ALTURA_PADRAO, mapa_id))
        c.commit()
    return len(limpos)


# ── a casa por camadas: que peça do desenho cada obra já tem ──────────────
#
# O desenho 3D tem seis peças (fundação, pilares, paredes, telhado, esquadrias,
# pintura). A obra tem as etapas DELA, com os nomes dela — então a peça acende
# pela etapa de nome correspondente quando existe, e pelo % acumulado quando
# não existe (os cortes são os pesos do padrão de casa popular do nicho).
_PECAS = (("fund", r"funda", 8), ("pil", r"estrut", 22), ("par", r"alvenar|parede", 32),
          ("telh", r"cobert|telhad", 44), ("esq", r"esquadr|janela|porta", 87),
          ("pint", r"pintur", 98))


def casa_pecas(obra: dict) -> dict:
    out = {}
    for chave, padrao, corte in _PECAS:
        das = [e for e in obra.get("etapas", []) if re.search(padrao, _ob._norm(e["nome"]))]
        out[chave] = (any(e["concluida_em"] for e in das) if das
                      else obra.get("pct", 0) >= corte)
    return out


# ── a vista do mapa (o que a tela desenha) ────────────────────────────────
def _faixa(pct: int) -> str:
    # as mesmas faixas do mapa em grade da quadra (obra_grupos.mapa)
    return ("pronta" if pct == 100 else "f3" if pct > 60 else "f2" if pct > 30
            else "f1" if pct > 0 else "f0")


def _alerta(pool, conta_id: int, o: dict) -> str:
    """O que trava a casa pronta, ou o primeiro prazo vencendo — igual ao mapa
    em grade da quadra. Sem a 353, sem alerta."""
    if o.get("tipo") != "casa":
        return ""
    try:
        from . import obra_venda as _ov
        sit = _ov.situacao_da_casa(pool, conta_id, o)
        if o["pct"] == 100 and sit["trava"] and sit["trava"]["chave"] != "creditado":
            return "trava: " + sit["trava"]["nome"].lower()
        if sit["alertas"]:
            return sit["alertas"][0]
    except Exception:  # noqa: BLE001
        pass
    return ""


def vista(pool, conta_id: int, mapa_id: int) -> dict:
    """O mapa pronto pra tela: cada risco com a obra dele resolvida (andamento,
    peças da casa, etapas e números já formatados). É o JSON que o JS desenha."""
    m = obter(pool, conta_id, mapa_id)
    if not m:
        raise ValueError("Área não encontrada.")
    # com as arquivadas: a casa entregue continua no mapa, pronta
    por_id = {o["id"]: o for o in _ob.listar_obras(pool, conta_id, incluir_arquivadas=True)}
    try:                                    # o material no perfil (migração 484)
        from . import obra_grupos as _og
        from . import obra_material as _omat
        mats = _omat.por_obra(pool, conta_id)
        # as quadras carregadas UMA vez pro alerta das irmãs de todos os lotes
        grupos = _og.por_obra(pool, conta_id)
        por_grupo: dict = {}
        for oid, g in grupos.items():
            if g["grupo_id"] and oid in por_id and por_id[oid]["status"] != "arquivada":
                por_grupo.setdefault(g["grupo_id"], []).append(por_id[oid])
    except Exception:  # noqa: BLE001
        _omat, mats, grupos, por_grupo = None, {}, {}, {}
    out = []
    for l in lotes(pool, conta_id, mapa_id):
        o = por_id.get(l["obra_id"]) if l["obra_id"] else None
        d = {"id": l["id"], "rotulo": l["rotulo"], "x": l["x"], "y": l["y"],
             "larg": l["larg"], "alt": l["alt"], "situacao": l["situacao"],
             # obra apagada: o lote volta a se portar como vago, e o perfil não quebra
             "obra_id": l["obra_id"] if o is not None else None,
             "pct": 0, "st": l["situacao"] if l["situacao"] != "meu" else "vago"}
        if o is not None:
            alerta = _alerta(pool, conta_id, o)
            feitas = sum(1 for e in o["etapas"] if e["concluida_em"])
            d.update({
                # a cor é SEMPRE o andamento; o alerta vira um selo no canto
                # (pedido do dono em 02/10: com o lote inteiro âmbar, um
                # loteamento com prazo de documento vencendo perdia a leitura)
                "pct": o["pct"], "st": _faixa(o["pct"]),
                "alerta": alerta, "obra_nome": o["nome"],
                "casa": casa_pecas(o),
                "gasto": _ob._brl(o["custos"]["total"]),
                "gasto_c": int(o["custos"]["total"] or 0),
                "previsto": (f"{o['pct_previsto']}% do previsto" if o.get("pct_previsto") is not None
                             else "sem previsto"),
                "m2": (_ob._brl(o["custo_m2"]) + "/m²") if o.get("custo_m2") else "",
                "n_etapas": f"{feitas} de {len(o['etapas'])} etapas",
                "etapas": [[e["nome"], bool(e["concluida_em"])] for e in o["etapas"]],
            })
            if _omat is not None:
                linhas = mats.get(o["id"]) or []
                # só o que TEM na obra: "0 barras" é ruído, e o saldo negativo
                # já vira o alerta de furo logo abaixo
                d["mat"] = " · ".join([f"{_omat.rotulo(r['saldo'], r['unidade'])} de {r['nome']}"
                                       for r in linhas if r["saldo"] > 0][:3])
                fur = _omat.furos(linhas)
                # o furo primeiro: uso maior que entrada é material sumindo (ou nota
                # faltando) — mais grave que gastar acima das irmãs
                d["mat_alerta"] = ((fur[0] if fur else "")
                                   or _omat.alerta_irmas(pool, conta_id, o, quadros=mats,
                                                         grupos=grupos, obras=por_grupo))
                # a tabela inteira pro perfil (entrou / usado / na obra), como na ficha
                d["mat_linhas"] = [[r["nome"], _omat.rotulo(r["entrou"], r["unidade"]),
                                    _omat._qtd(r["usado"]), _omat.rotulo(r["saldo"], r["unidade"]),
                                    bool(r["chave"]), bool(r["saldo"] < 0)] for r in linhas]
            d["fotos"] = _fotos_recentes(pool, conta_id, o["id"])
        out.append(d)
    return {"mapa": m, "lotes": out, "resumo": resumo(out)}


def _fotos_recentes(pool, conta_id: int, obra_id: int, n: int = 4) -> list[str]:
    """As últimas fotos da obra, como links da rota com sessão (o bucket é
    privado). Sem a 369, nenhuma."""
    try:
        from . import obra_fotos as _of
        return [f"/painel/obras/{obra_id}/foto/{f['id']}"
                for f in _of.listar(pool, conta_id, obra_id)[:n]]
    except Exception:  # noqa: BLE001
        return []


def resumo(lotes_vista: list[dict]) -> dict:
    """O topo da aba: casas no mapa, andamento médio, prontas, gasto e quantas
    pedem atenção (⚠️ obra, 🧱 material)."""
    casas = [l for l in lotes_vista if l.get("obra_id")]
    n = len(casas)
    return {"casas": n,
            "pct": int(round(sum(l["pct"] for l in casas) / n)) if n else 0,
            "prontas": sum(1 for l in casas if l["pct"] == 100),
            "gasto": _ob._brl(sum(l.get("gasto_c", 0) for l in casas)),
            "alerta_obra": sum(1 for l in casas if l.get("alerta")),
            "alerta_mat": sum(1 for l in casas if l.get("mat_alerta")),
            "vagos": sum(1 for l in lotes_vista if l["st"] == "vago")}


# ── o exemplo (o botão "Ver um exemplo") ──────────────────────────────────
#
# Um loteamento fictício montado NA HORA, sem gravar nada no banco (decisão do
# dono em 02/10/2026: "modo exemplo na aba"). Serve pra quem ainda não riscou a
# planta ver o que a aba entrega, e pro vendedor mostrar ao prospect. A planta
# é genérica: planta de cliente é do cliente e nunca aparece pra outra conta.

_ETAPAS_EXEMPLO = (("Preliminares e fundação", 8), ("Estrutura", 22), ("Alvenaria", 32),
                   ("Cobertura", 44), ("Instalações elétricas", 50),
                   ("Instalações hidráulicas", 56), ("Reboco e revestimento", 68),
                   ("Pisos", 78), ("Esquadrias", 87), ("Louças e metais", 92),
                   ("Pintura", 98), ("Limpeza e entrega", 100))

#: (rótulo, x, y, larg, alt, pct ou None = vago / "t" = terceiro, alerta, alerta de material)
_LOTES_EXEMPLO = (
    ("10", 350, 0, 70, 250, 100, "", ""), ("11", 420, 0, 70, 250, 87, "", ""),
    ("12", 490, 0, 70, 250, 32, "", ""), ("13", 560, 0, 70, 250, 8, "", ""),
    ("14", 630, 0, 70, 250, 0, "", ""), ("15", 700, 0, 70, 250, None, "", ""),
    ("16", 770, 0, 230, 75, 100, "", ""), ("17", 770, 75, 230, 75, 56, "", ""),
    ("18", 770, 150, 230, 75, 0, "", ""), ("19", 770, 225, 230, 75, None, "", ""),
    ("1", 0, 320, 75, 200, 100, "pronta há 12 dias esperando o habite-se", ""),
    ("2", 75, 320, 75, 200, 100, "", ""),
    ("3", 150, 320, 75, 200, 68, "",
     "Cimento CP-II 50 kg 33% acima das irmãs na mesma altura: esta casa já usou 48 sacos; a irmã usou 36."),
    ("4", 225, 320, 75, 200, 68, "", ""), ("5", 300, 320, 70, 200, 44, "", ""),
    ("6", 370, 320, 70, 200, 44, "",
     "Areia média: uso maior que entrada (2 m³ descobertos)"),
    ("7", 440, 320, 70, 200, 22, "", ""), ("8", 510, 320, 70, 200, 8, "CNO da obra atrasado", ""),
    ("9", 580, 320, 70, 200, 0, "", ""),
    ("", 650, 320, 100, 200, "t", "", ""), ("", 750, 320, 250, 200, "t", "", ""),
)


def _material_exemplo(pct: int, gastona: bool) -> list[list]:
    if pct <= 0:
        return []
    usado = round(48 if gastona else 36 * min(1, pct / 68 + .1))
    entrou = max(usado + (2 if pct < 100 else 0), 8)
    return [["Cimento CP-II 50 kg", f"{entrou} sacos", str(usado), f"{entrou - usado} sacos", True, False],
            ["Ferro 8 mm (barra 12 m)", "30 barras", "30" if pct >= 22 else "0",
             "0 barras" if pct >= 22 else "30 barras", True, False],
            ["Areia média", "6 m³", "5,5" if pct >= 32 else "0", "0,5 m³" if pct >= 32 else "6 m³", True, False],
            ["Tijolo 8 furos", "4 milheiros", "4" if pct >= 32 else "0",
             "0 milheiros" if pct >= 32 else "4 milheiros", True, False]]


def vista_exemplo() -> dict:
    """O mesmo formato de `vista`, inventado: a tela não sabe a diferença."""
    out = []
    for n, (rot, x, y, w, h, pct, alerta, mat_alerta) in enumerate(_LOTES_EXEMPLO, start=1):
        d = {"id": -n, "rotulo": rot, "x": x, "y": y, "larg": w, "alt": h,
             "situacao": "terceiro" if pct == "t" else ("vago" if pct is None else "meu"),
             "obra_id": None, "pct": 0}
        d["st"] = d["situacao"] if d["situacao"] != "meu" else "vago"
        if isinstance(pct, int):
            etapas = [{"nome": nome, "concluida_em": "x" if pct >= corte else None}
                      for nome, corte in _ETAPAS_EXEMPLO]
            fake = {"pct": pct, "etapas": etapas}
            linhas = _material_exemplo(pct, gastona=bool(mat_alerta and "irmãs" in mat_alerta))
            gasto_c = int(4_500_000 + pct * 63_000)
            d.update({
                "obra_id": -n, "pct": pct, "st": _faixa(pct), "alerta": alerta,
                "obra_nome": f"Casa {rot}", "casa": casa_pecas(fake),
                "gasto": _ob._brl(gasto_c), "gasto_c": gasto_c,
                "previsto": f"{min(99, 12 + pct)}% do previsto",
                "m2": _ob._brl(172_000 + (pct % 70) * 100) + "/m²",
                "n_etapas": f"{sum(1 for e in etapas if e['concluida_em'])} de {len(etapas)} etapas",
                "etapas": [[e["nome"], bool(e["concluida_em"])] for e in etapas],
                "mat": " · ".join(f"{l[3]} de {l[0]}" for l in linhas if not l[3].startswith("0"))[:120],
                "mat_alerta": mat_alerta, "mat_linhas": linhas, "fotos": [],
            })
        out.append(d)
    m = {"id": 0, "nome": "Residencial Exemplo", "cidade": "", "planta_caminho": None,
         "altura": 520, "tem_planta": False}
    deposito = [{"nome": "Argamassa AC-II 20 kg", "saldo": "8 sacos", "minimo": "15 sacos"}]
    return {"mapa": m, "lotes": out, "resumo": resumo(out), "deposito_baixo": deposito}

