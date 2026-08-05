"""finance/prospeccao_fontes.buscar_places: modo "cercar no mapa" (lat/lng +
raio_km). Sem banco — só monkeypatcha httpx.post e olha o corpo da requisição.
"""
import httpx

from finance import prospeccao_fontes as pf


class _FakeResp:
    status_code = 200
    def json(self):
        return {"places": []}


def test_sem_raio_usa_bias_nao_restricao(monkeypatch):
    capturado = {}
    def fake_post(url, json, headers, timeout):
        capturado["body"] = json
        return _FakeResp()
    monkeypatch.setattr(httpx, "post", fake_post)
    pf.buscar_places("pet shop", "", api_key="fake", lat=-5.09, lng=-42.80)
    body = capturado["body"]
    assert "locationBias" in body and "locationRestriction" not in body
    assert body["locationBias"]["circle"]["radius"] == 15000.0


def test_com_raio_usa_restricao_e_converte_km_pra_metros(monkeypatch):
    capturado = {}
    def fake_post(url, json, headers, timeout):
        capturado["body"] = json
        return _FakeResp()
    monkeypatch.setattr(httpx, "post", fake_post)
    pf.buscar_places("pet shop", "", api_key="fake", lat=-5.09, lng=-42.80, raio_km=2.5)
    body = capturado["body"]
    assert "locationRestriction" in body and "locationBias" not in body
    assert body["locationRestriction"]["circle"]["radius"] == 2500.0
    assert body["locationRestriction"]["circle"]["center"] == {"latitude": -5.09, "longitude": -42.80}


def test_raio_e_clampado_no_limite_do_google(monkeypatch):
    capturado = {}
    def fake_post(url, json, headers, timeout):
        capturado["body"] = json
        return _FakeResp()
    monkeypatch.setattr(httpx, "post", fake_post)
    pf.buscar_places("pet shop", "", api_key="fake", lat=-5.09, lng=-42.80, raio_km=999)
    assert capturado["body"]["locationRestriction"]["circle"]["radius"] == 50000.0


def test_sem_lat_lng_nao_manda_nenhum_dos_dois(monkeypatch):
    capturado = {}
    def fake_post(url, json, headers, timeout):
        capturado["body"] = json
        return _FakeResp()
    monkeypatch.setattr(httpx, "post", fake_post)
    pf.buscar_places("pet shop", "Teresina - PI", api_key="fake")
    body = capturado["body"]
    assert "locationBias" not in body and "locationRestriction" not in body
