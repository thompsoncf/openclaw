"""finance/prospeccao_fontes.buscar_places: modo "cercar no mapa" (lat/lng +
raio_km). Sem banco — só monkeypatcha httpx.post e olha o corpo da requisição.

A Places API (New) Text Search só aceita RETÂNGULO em locationRestriction —
mandar círculo lá é rejeitado com 400 "Unknown name circle" (reproduzido em
produção). O round-trip real é: manda um retângulo que cobre o círculo, e
filtra os resultados por distância real (_distancia_km) depois.
"""
import httpx

from finance import prospeccao_fontes as pf


class _FakeResp:
    status_code = 200
    def __init__(self, places=None):
        self._places = places or []
    def json(self):
        return {"places": self._places}


def _place(pid, nome, lat, lng):
    return {"id": pid, "displayName": {"text": nome}, "formattedAddress": "",
            "location": {"latitude": lat, "longitude": lng}}


def test_sem_raio_usa_bias_com_circulo(monkeypatch):
    """Sem raio_km, lat/lng continuam sendo só um empurrãozinho (locationBias
    aceita círculo — diferente de locationRestriction)."""
    capturado = {}
    def fake_post(url, json, headers, timeout):
        capturado["body"] = json
        return _FakeResp()
    monkeypatch.setattr(httpx, "post", fake_post)
    pf.buscar_places("pet shop", "", api_key="fake", lat=-5.09, lng=-42.80)
    body = capturado["body"]
    assert "locationBias" in body and "locationRestriction" not in body
    assert body["locationBias"]["circle"]["radius"] == 15000.0


def test_com_raio_usa_retangulo_nao_circulo(monkeypatch):
    """locationRestriction só aceita rectangle — nunca circle (é o que causava
    o 400 em produção: 'Unknown name circle' at 'location_restriction')."""
    capturado = {}
    def fake_post(url, json, headers, timeout):
        capturado["body"] = json
        return _FakeResp()
    monkeypatch.setattr(httpx, "post", fake_post)
    pf.buscar_places("pet shop", "", api_key="fake", lat=-5.09, lng=-42.80, raio_km=2.5)
    body = capturado["body"]
    assert "locationRestriction" in body and "locationBias" not in body
    rect = body["locationRestriction"]["rectangle"]
    assert "circle" not in body["locationRestriction"]
    assert rect["low"]["latitude"] < -5.09 < rect["high"]["latitude"]
    assert rect["low"]["longitude"] < -42.80 < rect["high"]["longitude"]


def test_raio_e_clampado_no_teto_do_google(monkeypatch):
    """Text Search aceita até 50km de raio de restrição — acima disso, clampa."""
    capturado = {}
    def fake_post(url, json, headers, timeout):
        capturado["body"] = json
        return _FakeResp()
    monkeypatch.setattr(httpx, "post", fake_post)
    pf.buscar_places("pet shop", "", api_key="fake", lat=-5.09, lng=-42.80, raio_km=999)
    rect_50 = pf._retangulo_ao_redor(-5.09, -42.80, 50.0)
    assert capturado["body"]["locationRestriction"]["rectangle"] == rect_50


def test_sem_lat_lng_nao_manda_nenhum_dos_dois(monkeypatch):
    capturado = {}
    def fake_post(url, json, headers, timeout):
        capturado["body"] = json
        return _FakeResp()
    monkeypatch.setattr(httpx, "post", fake_post)
    pf.buscar_places("pet shop", "Teresina - PI", api_key="fake")
    body = capturado["body"]
    assert "locationBias" not in body and "locationRestriction" not in body


def test_filtra_por_distancia_real_alem_do_retangulo(monkeypatch):
    """O retângulo é só o que a API aceita mandar — quem caiu nos "cantos" (fora
    do círculo de verdade) precisa ser descartado depois, olhando a distância."""
    centro_lat, centro_lng = -5.09, -42.80
    perto = _place("p1", "Pet Perto", centro_lat + 0.005, centro_lng)          # ~0.6km
    longe = _place("p2", "Pet Longe (canto do retângulo)", centro_lat + 0.03, centro_lng + 0.03)  # bem mais que 2km
    sem_loc = _place("p3", "Sem localização", 0, 0); del sem_loc["location"]

    def fake_post(url, json, headers, timeout):
        return _FakeResp([perto, longe, sem_loc])
    monkeypatch.setattr(httpx, "post", fake_post)

    r = pf.buscar_places("pet shop", "", api_key="fake", lat=centro_lat, lng=centro_lng, raio_km=2)
    assert r["ok"]
    nomes = [i["empresa"] for i in r["itens"]]
    assert nomes == ["Pet Perto"]


def test_distancia_km_haversine_basico():
    # ~1 grau de latitude ~= 111km
    d = pf._distancia_km(0, 0, 1, 0)
    assert 110 < d < 112
    assert pf._distancia_km(-5.09, -42.80, -5.09, -42.80) == 0
