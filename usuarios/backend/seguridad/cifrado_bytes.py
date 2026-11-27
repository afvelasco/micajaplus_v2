import hmac
import os
from pathlib import Path
from dotenv import load_dotenv

_RUTA_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_RUTA_ENV, override=True)

PEPPER_SERVIDOR = os.getenv("PEPPER_SERVIDOR", "").encode("utf-8")
if len(PEPPER_SERVIDOR) < 32:
    raise RuntimeError("Error critico: PEPPER_SERVIDOR debe tener al menos 32 bytes en el .env")

LONGITUD_SAL = int(os.getenv("LONGITUD_SAL", 16))
LONGITUD_DERIVADA = int(os.getenv("LONGITUD_DERIVADA", 64))
MAX_LARGO_CONTRASENA = int(os.getenv("MAX_LARGO_CONTRASENA", 128))

_SAL_SENUELO = b"\x00" * LONGITUD_SAL
_MASCARA_64 = 0xFFFFFFFFFFFFFFFF

# Constantes base del estandar NIST SP 800-232 (ASCON)
_CONSTANTES_BASE = (
    0xF0, 0xE1, 0xD2, 0xC3, 0xB4, 0xA5, 0x96, 0x87, 0x78, 0x69, 0x5A, 0x4B
)

# Personalizacion con el PEPPER_SERVIDOR
_MASCARA_PROPIA = int.from_bytes(PEPPER_SERVIDOR[:8], "little") ^ 0xA5A55A5AA5A55A5A
_CONSTANTES_PROPIAS = tuple(
    (c ^ PEPPER_SERVIDOR[i % len(PEPPER_SERVIDOR)]) & 0xFF
    for i, c in enumerate(_CONSTANTES_BASE)
)

_MASCARA_BRAILLE = PEPPER_SERVIDOR[0] ^ 0x5D
_BASE_BRAILLE = 0x2800


def codificar_braille(datos: bytes) -> str:
    if not datos:
        return ""
    return "".join(
        chr(_BASE_BRAILLE | ((b ^ _MASCARA_BRAILLE) & 0xFF))
        for b in datos
    )


def decodificar_braille(texto: str) -> bytes:
    if not isinstance(texto, str) or not texto:
        raise ValueError("Secuencia Braille vacia.")

    salida = bytearray()
    for simbolo in texto:
        codigo = ord(simbolo)
        if not (0x2800 <= codigo <= 0x28FF):
            raise ValueError("Simbolo fuera del rango Braille.")
        octeto = (codigo - _BASE_BRAILLE) ^ _MASCARA_BRAILLE
        salida.append(octeto & 0xFF)
    return bytes(salida)


def _rotar_derecha(valor: int, desplazamientos: int) -> int:
    return ((valor >> desplazamientos) | (valor << (64 - desplazamientos))) & _MASCARA_64


def mezclar_estado(estado: list[int], rondas: int = 12) -> None:
    x0, x1, x2, x3, x4 = estado
    inicio = 12 - rondas

    for idx in range(inicio, 12):
        x2 ^= _CONSTANTES_PROPIAS[idx]
        if idx == inicio:
            x4 ^= _MASCARA_PROPIA

        x0 ^= x4
        x4 ^= x3
        x2 ^= x1
        t0 = (x0 ^ _MASCARA_64) & x1
        t1 = (x1 ^ _MASCARA_64) & x2
        t2 = (x2 ^ _MASCARA_64) & x3
        t3 = (x3 ^ _MASCARA_64) & x4
        t4 = (x4 ^ _MASCARA_64) & x0
        x0 ^= t1
        x1 ^= t2
        x2 ^= t3
        x3 ^= t4
        x4 ^= t0
        x1 ^= x0
        x0 ^= x4
        x3 ^= x2
        x2 ^= _MASCARA_64

        x0 ^= _rotar_derecha(x0, 19) ^ _rotar_derecha(x0, 28)
        x1 ^= _rotar_derecha(x1, 61) ^ _rotar_derecha(x1, 39)
        x2 ^= _rotar_derecha(x2, 1)  ^ _rotar_derecha(x2, 6)
        x3 ^= _rotar_derecha(x3, 10) ^ _rotar_derecha(x3, 17)
        x4 ^= _rotar_derecha(x4, 7)  ^ _rotar_derecha(x4, 41)

    estado[0], estado[1], estado[2], estado[3], estado[4] = x0, x1, x2, x3, x4


def expandir_clave(mensaje: bytes, contexto: bytes, bytes_salida: int = 64) -> bytes:
    estado = [
        0x675527C2A0E8DE03 ^ _MASCARA_PROPIA,
        0x43D12D7DC0377BBC,
        0xE9901DEC426E81B5,
        0x2AB14907720780B6,
        0x8F3F1D02D432BC46 ^ _MASCARA_PROPIA
    ]
    mezclar_estado(estado, 12)

    flujo_total = (
        len(contexto).to_bytes(8, "little")
        + contexto
        + b"\x01"
        + mensaje
    )
    resto = len(flujo_total) % 8
    bloque_relleno = flujo_total + b"\x80" + (b"\x00" * ((7 - resto) % 8))

    for i in range(0, len(bloque_relleno), 8):
        palabra = int.from_bytes(bloque_relleno[i:i + 8], "little")
        estado[0] ^= palabra
        mezclar_estado(estado, 12)

    salida = bytearray()
    while len(salida) < bytes_salida:
        salida.extend(estado[0].to_bytes(8, "little"))
        mezclar_estado(estado, 12)

    return bytes(salida[:bytes_salida])


def _derivar_clave_segura(contrasena_plana: str, sal: bytes) -> bytes:
    semilla = (
        len(PEPPER_SERVIDOR).to_bytes(2, "little")
        + PEPPER_SERVIDOR
        + sal
        + contrasena_plana.encode("utf-8")
    )
    contexto = b"MCP_SP800_232_CUSTOM_KDF_V1"

    nodos = [b""] * 64
    nodos[0] = expandir_clave(semilla, contexto, 32)

    for idx in range(1, 64):
        pivote = nodos[idx - 1][0] % idx
        combinado = nodos[idx - 1] + nodos[pivote] + idx.to_bytes(2, "little")
        nodos[idx] = expandir_clave(combinado, contexto, 32)

    return expandir_clave(nodos[-1] + sal, contexto, LONGITUD_DERIVADA)


def generar_hash_contrasena(contrasena_plana: str) -> str:
    if not isinstance(contrasena_plana, str) or not (1 <= len(contrasena_plana) <= MAX_LARGO_CONTRASENA):
        raise ValueError("La contrasena debe tener entre 1 y 128 caracteres.")

    sal = os.urandom(LONGITUD_SAL)
    derivado = _derivar_clave_segura(contrasena_plana, sal)
    return codificar_braille(sal + derivado)


def verificar_contrasena(contrasena_ingresada: str, hash_almacenado: str) -> bool:
    if not isinstance(contrasena_ingresada, str) or len(contrasena_ingresada) > MAX_LARGO_CONTRASENA:
        return False

    try:
        paquete = decodificar_braille(hash_almacenado)
        if len(paquete) != (LONGITUD_SAL + LONGITUD_DERIVADA):
            _derivar_clave_segura(contrasena_ingresada, _SAL_SENUELO)
            return False

        sal = paquete[:LONGITUD_SAL]
        hash_esperado = paquete[LONGITUD_SAL:]
        hash_calculado = _derivar_clave_segura(contrasena_ingresada, sal)

        return hmac.compare_digest(hash_esperado, hash_calculado)
    except (ValueError, TypeError):
        _derivar_clave_segura(contrasena_ingresada, _SAL_SENUELO)
        return False