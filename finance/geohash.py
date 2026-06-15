"""Geohash encoder em stdlib puro (sem dependencias externas).

Usado pra gerar o parametro `local` da API do Menor Preco a partir da
lat/lon de uma cidade.
"""

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"

# cidades de interesse (lat, lon do centro) -> geohash calculado on demand
CIDADES = {
    "teresina": (-5.0892, -42.8019),
    "curitiba": (-25.4284, -49.2733),  # util pra testes (foi a do exemplo)
}


def encode(lat: float, lon: float, precision: int = 9) -> str:
    """Codifica lat/lon em geohash. precision=9 e' o que a API usa."""
    lat_r = [-90.0, 90.0]
    lon_r = [-180.0, 180.0]
    geohash = []
    bits = [16, 8, 4, 2, 1]
    bit = 0
    ch = 0
    even = True
    while len(geohash) < precision:
        if even:
            mid = (lon_r[0] + lon_r[1]) / 2
            if lon > mid:
                ch |= bits[bit]
                lon_r[0] = mid
            else:
                lon_r[1] = mid
        else:
            mid = (lat_r[0] + lat_r[1]) / 2
            if lat > mid:
                ch |= bits[bit]
                lat_r[0] = mid
            else:
                lat_r[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            geohash.append(_BASE32[ch])
            bit = 0
            ch = 0
    return "".join(geohash)


def geohash_cidade(nome: str) -> str | None:
    """Retorna o geohash de uma cidade conhecida (ex: 'teresina')."""
    coords = CIDADES.get((nome or "").strip().lower())
    return encode(*coords) if coords else None
