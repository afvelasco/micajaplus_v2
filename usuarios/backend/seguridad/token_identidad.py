import hmac
import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from .cifrado_bytes import (
    mezclar_estado,
    expandir_clave,
    codificar_braille,
    decodificar_braille,
    _MASCARA_64
)

_RUTA_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_RUTA_ENV, override=True)

_clave_cruda = os.getenv("TOKEN_SECRET_KEY", "")
if len(_clave_cruda.encode("utf-8")) < 32:
    raise RuntimeError(
        "Error critico: TOKEN_SECRET_KEY debe tener al menos 32 bytes en el .env"
    )

_SECRETO_BYTES = _clave_cruda.encode("utf-8")


def _crear_llaves_canal(nombre_canal: bytes) -> tuple[int, int]:
    material = expandir_clave(_SECRETO_BYTES, nombre_canal, 16)
    return (
        int.from_bytes(material[:8], "little"),
        int.from_bytes(material[8:16], "little")
    )


_LLAVES_POR_USO = {
    "SESION_WEB": _crear_llaves_canal(b"MCP_SP800_232_KEY_WEB_V1"),
    "SESION_MOVIL": _crear_llaves_canal(b"MCP_SP800_232_KEY_MOBILE_V1"),
    "RECUPERACION": _crear_llaves_canal(b"MCP_SP800_232_KEY_RECOVERY_V1")
}

_PREFIJOS_CANAL = {
    "SESION_WEB": "W1",
    "SESION_MOVIL": "M1",
    "RECUPERACION": "R1"
}
_PREFIJO_A_USO = {v: k for k, v in _PREFIJOS_CANAL.items()}

_VECTOR_INICIAL_TOKEN = 0x00001000808C0001
TIEMPO_EXPIRACION = int(os.getenv("TOKEN_EXPIRACION_SEGUNDOS", 28800))

MIN_LARGO_TOKEN = 40
MAX_LARGO_TOKEN = 350

ROLES_PERMITIDOS = frozenset({
    "CONTADOR", "AUXILIAR", "TESORERO", "FACTURADOR", "ALMACEN", "VENDEDORES"
})


def _vincular_canal_estado(estado: list[int], canal_bytes: bytes) -> None:
    bloque_canal = canal_bytes.ljust(8, b"\x80")[:8]
    estado[0] ^= int.from_bytes(bloque_canal, "little")
    mezclar_estado(estado, 8)
    estado[4] ^= (1 << 63)


def _cifrar_bloque_token(uso: str, semilla_16b: bytes, texto_plano: bytes) -> tuple[bytes, bytes]:
    k0, k1 = _LLAVES_POR_USO[uso]
    n0 = int.from_bytes(semilla_16b[:8], "little")
    n1 = int.from_bytes(semilla_16b[8:16], "little")

    estado = [_VECTOR_INICIAL_TOKEN, k0, k1, n0, n1]
    mezclar_estado(estado, 12)
    estado[3] ^= k0
    estado[4] ^= k1

    _vincular_canal_estado(estado, uso.encode("ascii"))

    datos_cifrados = bytearray()
    limite = len(texto_plano) - (len(texto_plano) % 16)
    for i in range(0, limite, 16):
        p0 = int.from_bytes(texto_plano[i:i + 8], "little")
        p1 = int.from_bytes(texto_plano[i + 8:i + 16], "little")
        estado[0] ^= p0
        estado[1] ^= p1
        datos_cifrados.extend(estado[0].to_bytes(8, "little"))
        datos_cifrados.extend(estado[1].to_bytes(8, "little"))
        mezclar_estado(estado, 8)

    restante = texto_plano[limite:]
    bloque_final = restante + b"\x01" + (b"\x00" * (15 - len(restante)))
    estado[0] ^= int.from_bytes(bloque_final[:8], "little")
    estado[1] ^= int.from_bytes(bloque_final[8:16], "little")

    ultimo_cifrado = estado[0].to_bytes(8, "little") + estado[1].to_bytes(8, "little")
    datos_cifrados.extend(ultimo_cifrado[:len(restante)])

    estado[2] ^= k0
    estado[3] ^= k1
    mezclar_estado(estado, 12)
    firma_0 = (estado[3] ^ k0) & _MASCARA_64
    firma_1 = (estado[4] ^ k1) & _MASCARA_64
    firma = firma_0.to_bytes(8, "little") + firma_1.to_bytes(8, "little")

    return bytes(datos_cifrados), firma


def _descifrar_bloque_token(uso: str, semilla_16b: bytes, datos_cifrados: bytes, firma_recibida: bytes) -> bytes | None:
    k0, k1 = _LLAVES_POR_USO[uso]
    n0 = int.from_bytes(semilla_16b[:8], "little")
    n1 = int.from_bytes(semilla_16b[8:16], "little")

    estado = [_VECTOR_INICIAL_TOKEN, k0, k1, n0, n1]
    mezclar_estado(estado, 12)
    estado[3] ^= k0
    estado[4] ^= k1

    _vincular_canal_estado(estado, uso.encode("ascii"))

    texto_plano = bytearray()
    limite = len(datos_cifrados) - (len(datos_cifrados) % 16)

    for i in range(0, limite, 16):
        c0 = int.from_bytes(datos_cifrados[i:i + 8], "little")
        c1 = int.from_bytes(datos_cifrados[i + 8:i + 16], "little")
        p0 = estado[0] ^ c0
        p1 = estado[1] ^ c1
        texto_plano.extend(p0.to_bytes(8, "little"))
        texto_plano.extend(p1.to_bytes(8, "little"))
        estado[0] = c0
        estado[1] = c1
        mezclar_estado(estado, 8)

    restante = datos_cifrados[limite:]
    bytes_estado = bytearray(estado[0].to_bytes(8, "little") + estado[1].to_bytes(8, "little"))

    for idx, byte_c in enumerate(restante):
        texto_plano.append(bytes_estado[idx] ^ byte_c)
        bytes_estado[idx] = byte_c

    bytes_estado[len(restante)] ^= 0x01
    estado[0] = int.from_bytes(bytes_estado[:8], "little")
    estado[1] = int.from_bytes(bytes_estado[8:16], "little")

    estado[2] ^= k0
    estado[3] ^= k1
    mezclar_estado(estado, 12)
    firma_0 = (estado[3] ^ k0) & _MASCARA_64
    firma_1 = (estado[4] ^ k1) & _MASCARA_64
    firma_calculada = firma_0.to_bytes(8, "little") + firma_1.to_bytes(8, "little")

    if not hmac.compare_digest(firma_recibida, firma_calculada):
        return None

    return bytes(texto_plano)


def generar_token_identidad(id_usuario: str, rol: str, uso: str = "SESION_WEB") -> str:
    id_limpio = id_usuario.strip() if isinstance(id_usuario, str) else ""
    if not (1 <= len(id_limpio) <= 9):
        raise ValueError("id_usuario debe tener entre 1 y 9 caracteres (varchar(9)).")

    rol_limpio = rol.strip().upper() if isinstance(rol, str) else ""
    if rol_limpio not in ROLES_PERMITIDOS:
        raise ValueError(f"Rol no valido en la BD: {rol}")

    uso_real = "SESION_WEB" if uso == "SESION" else uso.strip().upper()
    if uso_real not in _LLAVES_POR_USO:
        raise ValueError(f"Tipo de token no valido: {uso}")

    ahora = int(time.time())
    if uso_real == "RECUPERACION":
        duracion = 900
    elif uso_real == "SESION_MOVIL":
        duracion = 43200
    else:
        duracion = TIEMPO_EXPIRACION

    carga_util = {
        "sub": id_limpio,
        "rol": rol_limpio,
        "uso": uso_real,
        "plataforma": "MOVIL" if uso_real == "SESION_MOVIL" else "WEB",
        "iat": ahora,
        "exp": ahora + duracion,
        "jti": os.urandom(6).hex()
    }

    datos_planos = json.dumps(carga_util, separators=(",", ":")).encode("utf-8")
    semilla = os.urandom(16)
    datos_cifrados, firma = _cifrar_bloque_token(uso_real, semilla, datos_planos)

    prefijo = _PREFIJOS_CANAL[uso_real]
    cuerpo_braille = codificar_braille(semilla + datos_cifrados + firma)
    return f"{prefijo}.{cuerpo_braille}"


def verificar_token_identidad(token: str, uso_esperado: str = "SESION") -> dict | None:
    if not isinstance(token, str) or not (MIN_LARGO_TOKEN <= len(token) <= MAX_LARGO_TOKEN):
        return None

    if token.count(".") != 1:
        return None

    prefijo, cuerpo_braille = token.split(".", 1)
    uso_detectado = _PREFIJO_A_USO.get(prefijo)
    if not uso_detectado:
        return None

    if uso_esperado == "SESION":
        if uso_detectado not in ("SESION_WEB", "SESION_MOVIL"):
            return None
    elif uso_detectado != uso_esperado:
        return None

    try:
        paquete = decodificar_braille(cuerpo_braille)
        if len(paquete) <= 32:
            return None

        semilla = paquete[:16]
        datos_cifrados = paquete[16:-16]
        firma_recibida = paquete[-16:]

        datos_planos = _descifrar_bloque_token(uso_detectado, semilla, datos_cifrados, firma_recibida)
        if datos_planos is None:
            return None

        datos = json.loads(datos_planos.decode("utf-8"))
        if not isinstance(datos, dict):
            return None

        ahora = int(time.time())
        exp = datos.get("exp")
        iat = datos.get("iat")
        sub = datos.get("sub")
        rol = datos.get("rol")
        uso = datos.get("uso")

        if not isinstance(exp, int) or not isinstance(iat, int):
            return None

        if ahora >= exp or iat > (ahora + 60):
            return None

        if not isinstance(sub, str) or not (1 <= len(sub) <= 9):
            return None

        if rol not in ROLES_PERMITIDOS or uso != uso_detectado:
            return None

        return datos

    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return None