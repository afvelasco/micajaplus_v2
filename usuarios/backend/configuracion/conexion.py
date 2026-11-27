import os
import threading
from pathlib import Path
import mysql.connector
from dotenv import load_dotenv

_RUTA_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_RUTA_ENV, override=True)

_VARIABLES_REQUERIDAS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")

_contexto_request = threading.local()


def _crear_conexion_directa():
    load_dotenv(_RUTA_ENV, override=True)
    faltantes = [var for var in _VARIABLES_REQUERIDAS if os.getenv(var) is None]
    if faltantes:
        raise RuntimeError(
            f"Error critico: Faltan variables de BD en el archivo .env: {', '.join(faltantes)}"
        )

    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "").strip(),
        port=int(os.getenv("DB_PORT", "3306").strip()),
        user=os.getenv("DB_USER", "").strip(),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "").strip(),
        charset="utf8mb4",
        collation="utf8mb4_spanish_ci",
        use_unicode=True,
        connection_timeout=15,
        autocommit=False,
        buffered=True
    )


class _ConexionRequestProxy:

    def __init__(self, conexion_real):
        self._conn = conexion_real

    def cursor(self, *args, **kwargs):
        kwargs.setdefault("buffered", True)
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        pass

    def cerrar_definitivamente(self):
        if self._conn is not None and self._conn.is_connected():
            self._conn.close()


def iniciar_conexion_request() -> None:
    cerrar_conexion_request()
    conexion_real = _crear_conexion_directa()
    _contexto_request.conexion = _ConexionRequestProxy(conexion_real)


def obtener_conexion():
    conexion_actual = getattr(_contexto_request, "conexion", None)
    if conexion_actual is not None:
        return conexion_actual
    return _crear_conexion_directa()


def cerrar_conexion_request() -> None:
    conexion_actual = getattr(_contexto_request, "conexion", None)
    if conexion_actual is not None:
        try:
            conexion_actual.cerrar_definitivamente()
        finally:
            _contexto_request.conexion = None