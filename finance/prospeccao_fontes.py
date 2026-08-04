"""Fontes de captação e enriquecimento da prospecção.

- Google Places API (New) Text Search: puxa comércio real por segmento + cidade
  (nome, endereço, telefone, se tem site, nota e nº de avaliações).
- Filtro de redes grandes (Petz, Drogasil…) pra sobrar só o comércio independente.
- Temperatura automática: comércio ativo SEM site = quente (é o alvo do ZAQ).
- Enriquecimento de CNPJ via BrasilAPI (grátis, sem chave): sócio, regime, porte.

Usa httpx (já no requirements). Tudo é tolerante a falha: sem chave ou erro de
rede devolve {"ok": False, "erro": ...} em vez de estourar exceção.
"""
from __future__ import annotations

import os
import unicodedata


def _norm(s: str) -> str:
    """minúsculo, sem acento, sem sufixo ' - UF' / '/UF' — pra comparar cidade."""
    s = (s or "").strip()
    for sep in (" - ", " – ", "/"):
        if sep in s:
            s = s.split(sep)[0]
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.strip().lower()

import httpx

# Redes grandes / franquias que NÃO são alvo (já têm sistema). Match por substring
# no nome, minúsculo e sem exigir acento perfeito.
REDES_GRANDES = (
    "petz", "cobasi", "petlove", "drogasil", "droga raia", "drogaria araujo",
    "pague menos", "pacheco", "drogaria sao paulo", "ultrafarma", "extrafarma",
    "carrefour", "assai", "atacadao", "makro", "big ", "extra ", "pao de acucar",
    "g barbosa", "carvalho", "americanas", "magazine luiza", "magalu",
    "casas bahia", "ponto frio", "renner", "riachuelo", "c&a", "havan",
    "mcdonald", "burger king", "subway", "bob's", "outback", "starbucks",
    "boticario", "o boticario", "natura", "cacau show", "kopenhagen",
    "banco do brasil", "bradesco", "itau", "santander", "caixa economica",
)


def eh_rede_grande(nome: str) -> bool:
    n = (nome or "").strip().lower()
    return any(r in n for r in REDES_GRANDES)


def temperatura_auto(tem_site) -> str:
    """Regra combinada: sem site = quente; com site = morno; desconhecido = frio."""
    if tem_site is False:
        return "quente"
    if tem_site is True:
        return "morno"
    return "frio"


def tem_chave_places() -> bool:
    return bool(os.environ.get("GOOGLE_PLACES_API_KEY"))


def buscar_places(termo: str, cidade: str, api_key: str | None = None,
                  max_resultados: int = 60, bairro: str = "", rua: str = "") -> dict:
    """Text Search (Places API New). Devolve {"ok", "itens": [...], "erro"}.

    `bairro` e `rua` são opcionais: refinam a busca por região (entram na frase da
    consulta, do mais específico pro mais geral: rua, bairro, cidade).

    Paginação: o Text Search devolve no máximo 20 por página, mas manda um
    `nextPageToken` quando há mais. A gente segue as páginas (passando o token no
    corpo) até juntar `max_resultados` (o Google entrega até ~60 no total) ou acabar
    o token. Cada página é uma requisição — o custo por página é o mesmo do field
    mask atual (faixa Pro), então 60 resultados ≈ 3 buscas.

    Cada item: empresa, endereco, telefone, place_id, tem_site, rating,
    avaliacoes, rede (bool), temperatura.
    """
    key = api_key or os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        return {"ok": False, "erro": "sem_chave", "itens": []}
    termo = (termo or "").strip()
    cidade = (cidade or "").strip()
    bairro = (bairro or "").strip()
    rua = (rua or "").strip()
    if not termo:
        return {"ok": False, "erro": "sem_termo", "itens": []}
    alvo = min(max(max_resultados, 1), 60)      # teto prático do Text Search
    url = "https://places.googleapis.com/v1/places:searchText"
    # Campos na MESMA faixa de cobrança que já usamos (telefone + nota): dá pra
    # incluir cidade/UF exatas, segmento, status e link do mapa sem subir o preço.
    # (horários/reviews subiriam pra faixa "Atmosphere", mais cara — ficam de fora.)
    # `nextPageToken` é grátis e habilita a paginação.
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": (
            "nextPageToken,"
            "places.id,places.displayName,places.formattedAddress,"
            "places.addressComponents,places.nationalPhoneNumber,"
            "places.internationalPhoneNumber,places.websiteUri,places.rating,"
            "places.userRatingCount,places.primaryTypeDisplayName,"
            "places.businessStatus,places.googleMapsUri"
        ),
    }
    # frase de busca do mais específico pro mais geral (o Text Search entende
    # linguagem natural): "pet shop em Rua X, Jardim, Teresina - PI".
    local = ", ".join(p for p in (rua, bairro, cidade) if p)
    consulta = f"{termo} em {local}" if local else termo
    base_body = {"textQuery": consulta, "languageCode": "pt-BR", "regionCode": "BR"}

    itens: list[dict] = []
    vistos: set[str] = set()
    token = ""
    for _pagina in range(4):        # 1ª + até 3 páginas de continuação (limite do provedor)
        falta = alvo - len(itens)
        if falta <= 0:
            break
        body = dict(base_body, maxResultCount=min(falta, 20))
        if token:
            body["pageToken"] = token
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=25)
            if r.status_code != 200:
                if itens:       # já tem página(s) boa(s): entrega o que juntou
                    break
                from core.falhas import avaliar_falha_provedor
                avaliar_falha_provedor(f"http_{r.status_code}: {r.text[:200]}",
                                       servico="Google Places")
                return {"ok": False, "erro": f"http_{r.status_code}", "itens": [],
                        "detalhe": r.text[:300]}
            data = r.json()
        except Exception as e:  # noqa: BLE001
            if itens:
                break
            return {"ok": False, "erro": "rede", "detalhe": str(e)[:200], "itens": []}

        for p in data.get("places", []):
            nome = (p.get("displayName") or {}).get("text") or ""
            pid = p.get("id") or ""
            if not nome or (pid and pid in vistos):
                continue
            if pid:
                vistos.add(pid)
            tem_site = bool(p.get("websiteUri"))
            cid, uf = _endereco_partes(p.get("addressComponents"))
            seg = (p.get("primaryTypeDisplayName") or {}).get("text") or ""
            status = p.get("businessStatus") or ""
            itens.append({
                "empresa": nome,
                "endereco": p.get("formattedAddress") or "",
                "telefone": p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber") or "",
                "place_id": pid,
                "tem_site": tem_site,
                "site": p.get("websiteUri") or "",
                "rating": p.get("rating"),
                "avaliacoes": p.get("userRatingCount") or 0,
                "segmento": seg,
                "cidade": cid or cidade,
                "uf": uf,
                "maps_uri": p.get("googleMapsUri") or "",
                "status": status,
                "aberto": status != "CLOSED_PERMANENTLY",
                "rede": eh_rede_grande(nome),
                "temperatura": temperatura_auto(tem_site),
            })

        token = (data.get("nextPageToken") or "").strip()
        if not token:
            break
    return {"ok": True, "itens": itens, "erro": None}


def _endereco_partes(componentes) -> tuple[str, str]:
    """Extrai (cidade, UF) dos addressComponents do Places (New)."""
    cidade, uf = "", ""
    for c in componentes or []:
        tipos = c.get("types") or []
        if not cidade and ("administrative_area_level_2" in tipos or "locality" in tipos):
            cidade = c.get("longText") or c.get("shortText") or ""
        if not uf and "administrative_area_level_1" in tipos:
            uf = (c.get("shortText") or "")[:2].upper()
    return cidade, uf


def tem_chave_cnpja() -> bool:
    return bool(os.environ.get("CNPJA_TOKEN"))


def enriquecer_cnpj(cnpj: str) -> dict:
    """Enriquece por CNPJ. Se houver CNPJA_TOKEN, usa o CNPJá (mais rico:
    Simples, sócio, porte, inscrição estadual…); senão, ou se falhar, cai na
    BrasilAPI (grátis). Devolve {"ok", "dados": {...}} ou {"ok": False, "erro"}.
    """
    if tem_chave_cnpja():
        r = enriquecer_cnpj_cnpja(cnpj)
        if r.get("ok"):
            return r
    return _enriquecer_cnpj_brasilapi(cnpj)


def enriquecer_cnpj_cnpja(cnpj: str, token: str | None = None) -> dict:
    """CNPJá comercial: GET /office/{cnpj}. Precisa de CNPJA_TOKEN (header
    Authorization). Parsing tolerante ao schema Office do CNPJá."""
    key = token or os.environ.get("CNPJA_TOKEN")
    if not key:
        return {"ok": False, "erro": "sem_chave"}
    d = "".join(c for c in (cnpj or "") if c.isdigit())
    if len(d) != 14:
        return {"ok": False, "erro": "cnpj_invalido"}
    try:
        r = httpx.get(f"https://api.cnpja.com/office/{d}",
                      params={"simples": "true", "registrations": "BR"},
                      headers={"Authorization": key}, timeout=20)
        if r.status_code != 200:
            from core.falhas import avaliar_falha_provedor
            avaliar_falha_provedor(f"http_{r.status_code}: {r.text[:200]}",
                                   servico="CNPJa")
            return {"ok": False, "erro": f"http_{r.status_code}", "detalhe": r.text[:200]}
        j = r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": "rede", "detalhe": str(e)[:200]}

    comp = j.get("company") or {}
    membros = comp.get("members") or []
    socio = None
    if membros:
        socio = ((membros[0].get("person") or {}).get("name")) or membros[0].get("name")
    if (comp.get("simei") or {}).get("optant"):
        regime = "MEI"
    elif (comp.get("simples") or {}).get("optant"):
        regime = "Simples Nacional"
    else:
        regime = None
    size = comp.get("size") or {}
    addr = j.get("address") or {}
    phones = j.get("phones") or []
    tel = None
    if phones:
        ph = phones[0]
        tel = ((ph.get("area") or "") + (ph.get("number") or "")) or None
    emails = j.get("emails") or []
    email = (emails[0].get("address") if emails else None)
    reg = j.get("registrations") or []
    ie = None
    for x in reg:
        if x.get("enabled") and x.get("number"):
            ie = f"{x.get('number')} ({x.get('state', '')})".strip()
            break
    # endereço completo montado a partir dos pedaços
    partes_end = []
    if addr.get("street"):
        partes_end.append(str(addr["street"]) + (", " + str(addr["number"]) if addr.get("number") else ""))
    if addr.get("district"):
        partes_end.append(str(addr["district"]))
    if addr.get("zip"):
        partes_end.append("CEP " + str(addr["zip"]))
    endereco = " · ".join(partes_end) or None
    secundarias = [a.get("text") for a in (j.get("sideActivities") or []) if a.get("text")][:6]
    dados = {
        "razao_social": comp.get("name") or j.get("alias"),
        "nome_fantasia": (j.get("alias") or "").strip() or None,
        "socio": socio,
        "regime_tributario": regime,
        "porte": (size.get("text") or size.get("acronym") or "").strip() or None,
        "telefone": tel,
        "email": (email or "").strip().lower() or None,
        "segmento": (j.get("mainActivity") or {}).get("text"),
        "cidade": (addr.get("city") or "").title().strip() or None,
        "uf": (addr.get("state") or "").strip().upper() or None,
        # extras ricos (vão pro jsonb `receita`, exibidos na ficha):
        "inscricao_estadual": ie,
        "situacao": (j.get("status") or {}).get("text"),
        "abertura": j.get("founded"),
        "capital_social": comp.get("equity"),
        "natureza": (comp.get("nature") or {}).get("text"),
        "endereco": endereco,
        "atividade_principal": (j.get("mainActivity") or {}).get("text"),
        "atividades_secundarias": secundarias,
        "fonte": "CNPJá",
    }
    return {"ok": True, "dados": dados}


def _limpa_termo_busca(nome: str) -> str:
    """Tira o sufixo de bairro/filial ('... - Cristo Rei') e ruído, pra o
    names.in casar o NOME da empresa, não só a 1ª palavra."""
    n = (nome or "").strip()
    for sep in (" - ", " – ", " — ", " | "):
        if sep in n:
            n = n.split(sep)[0].strip()
    return n


def buscar_cnpj_por_nome(nome: str, cidade: str = "", uf: str = "",
                         token: str | None = None, limite: int = 10) -> dict:
    """Acha CNPJs por NOME (CNPJá comercial: GET /office?names.in=...), FILTRADO
    pela UF do lead (address.state.in) e ranqueado pela cidade captada — pra os
    candidatos baterem o endereço, não trazer empresa de outro estado.
    Devolve {"ok", "itens": [{cnpj, razao_social, nome_fantasia, cidade, uf, situacao}]}."""
    key = token or os.environ.get("CNPJA_TOKEN")
    if not key:
        return {"ok": False, "erro": "sem_chave", "itens": []}
    termo = _limpa_termo_busca(nome)
    if len(termo) < 3:
        return {"ok": False, "erro": "nome_curto", "itens": []}
    uf = (uf or "").strip().upper()[:2]
    base = {"names.in": termo, "limit": str(min(max(limite, 1), 20))}
    hdr = {"Authorization": key}

    def _chama(params):
        try:
            r = httpx.get("https://api.cnpja.com/office", params=params, headers=hdr, timeout=25)
            return r.status_code, (r.json() if r.status_code == 200 else None), r.text[:200]
        except Exception as e:  # noqa: BLE001
            return None, None, str(e)[:200]

    # 1ª tentativa: com filtro de UF na API. Se o param for recusado, refaz sem ele.
    if uf:
        st, j, det = _chama(dict(base, **{"address.state.in": uf}))
        if st != 200:
            st, j, det = _chama(base)
    else:
        st, j, det = _chama(base)
    if st != 200:
        erro = "rede" if st is None else f"http_{st}"
        return {"ok": False, "erro": erro, "detalhe": det, "itens": []}

    lst = None
    for k in ("records", "data", "items", "results"):
        v = j.get(k) if isinstance(j, dict) else None
        if isinstance(v, list):
            lst = v
            break
    if lst is None:
        lst = j if isinstance(j, list) else []
    cid_lead = _norm(cidade)
    itens = []
    for o in lst:
        if not isinstance(o, dict):
            continue
        comp = o.get("company") or {}
        addr = o.get("address") or {}
        cnpj = o.get("taxId") or o.get("cnpj") or ""
        if not cnpj:
            continue
        it_uf = (addr.get("state") or "").upper()
        # filtro por UF no cliente (garante, mesmo se a API ignorou o param)
        if uf and it_uf and it_uf != uf:
            continue
        # filtro por CIDADE: só candidatos da mesma cidade do lead (bate o endereço).
        if cid_lead and _norm(addr.get("city") or "") != cid_lead:
            continue
        pe = []
        if addr.get("street"):
            pe.append(str(addr["street"]) + (", " + str(addr["number"]) if addr.get("number") else ""))
        if addr.get("district"):
            pe.append(str(addr["district"]))
        itens.append({
            "cnpj": cnpj,
            "razao_social": comp.get("name") or o.get("name") or "",
            "nome_fantasia": o.get("alias") or "",
            "cidade": (addr.get("city") or "").title(),
            "uf": it_uf,
            "endereco": " · ".join(pe),
            "situacao": (o.get("status") or {}).get("text") or "",
        })
    return {"ok": True, "itens": itens, "erro": None}


def _enriquecer_cnpj_brasilapi(cnpj: str) -> dict:
    """BrasilAPI (grátis, sem chave)."""
    d = "".join(c for c in (cnpj or "") if c.isdigit())
    if len(d) != 14:
        return {"ok": False, "erro": "cnpj_invalido"}
    try:
        r = httpx.get(f"https://brasilapi.com.br/api/cnpj/v1/{d}", timeout=20)
        if r.status_code != 200:
            return {"ok": False, "erro": f"http_{r.status_code}"}
        j = r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": "rede", "detalhe": str(e)[:200]}

    qsa = j.get("qsa") or []
    socio = None
    if qsa:
        socio = qsa[0].get("nome_socio") or qsa[0].get("nome") or None
    if j.get("opcao_pelo_mei"):
        regime = "MEI"
    elif j.get("opcao_pelo_simples"):
        regime = "Simples Nacional"
    else:
        regime = None
    tel = (j.get("ddd_telefone_1") or "").strip() or None
    pe = []
    if j.get("logradouro"):
        pe.append(str(j["logradouro"]) + (", " + str(j["numero"]) if j.get("numero") else ""))
    if j.get("bairro"):
        pe.append(str(j["bairro"]))
    if j.get("cep"):
        pe.append("CEP " + str(j["cep"]))
    sec = [a.get("descricao") for a in (j.get("cnaes_secundarios") or []) if a.get("descricao")][:6]
    dados = {
        "razao_social": j.get("razao_social") or j.get("nome_fantasia"),
        "nome_fantasia": (j.get("nome_fantasia") or "").strip() or None,
        "socio": socio,
        "regime_tributario": regime,
        "porte": (j.get("porte") or "").title().strip() or None,
        "telefone": tel,
        "email": (j.get("email") or "").strip().lower() or None,
        "segmento": j.get("cnae_fiscal_descricao") or None,
        "cidade": (j.get("municipio") or "").title().strip() or None,
        "uf": (j.get("uf") or "").strip().upper() or None,
        "inscricao_estadual": None,
        "situacao": j.get("descricao_situacao_cadastral"),
        "abertura": j.get("data_inicio_atividade"),
        "capital_social": j.get("capital_social"),
        "natureza": j.get("natureza_juridica"),
        "endereco": " · ".join(pe) or None,
        "atividade_principal": j.get("cnae_fiscal_descricao"),
        "atividades_secundarias": sec,
        "fonte": "BrasilAPI",
    }
    return {"ok": True, "dados": dados}
