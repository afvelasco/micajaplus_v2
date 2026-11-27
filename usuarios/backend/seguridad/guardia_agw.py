from functools import wraps
from .token_identidad import verificar_token_identidad

PERMISOS_MODULOS = {
    "empresas": frozenset({
        "CONTADOR", "AUXILIAR", "TESORERO", "FACTURADOR", "ALMACEN", "VENDEDORES"
    }),
    "ventas": frozenset({
        "CONTADOR", "FACTURADOR", "VENDEDORES"
    }),
    "compras": frozenset({
        "CONTADOR", "AUXILIAR", "ALMACEN"
    }),
    "usuarios": frozenset({
        "CONTADOR"
    }),
}


def verificar_acceso_modulo(token: str | None, nombre_modulo: str) -> dict:
    modulo = nombre_modulo.strip().lower() if isinstance(nombre_modulo, str) else ""

    if modulo not in PERMISOS_MODULOS:
        return {
            "ok": False,
            "codigo": 404,
            "error": "Modulo no registrado en el AGW.",
            "redirigir_a": "/index.html"
        }

    datos_token = verificar_token_identidad(token or "", uso_esperado="SESION")
    if not datos_token:
        return {
            "ok": False,
            "codigo": 401,
            "error": "Acceso denegado: Sesion no iniciada o token expirado.",
            "redirigir_a": "/index.html"
        }

    rol_usuario = datos_token["rol"]
    if rol_usuario not in PERMISOS_MODULOS[modulo]:
        return {
            "ok": False,
            "codigo": 403,
            "error": f"El rol '{rol_usuario}' no tiene autorizacion para '{modulo}'.",
            "redirigir_a": "/empresas"
        }

    return {
        "ok": True,
        "codigo": 200,
        "id_usuario": datos_token["sub"],
        "rol": rol_usuario,
        "autorizado_por": f"{datos_token['sub']} ({rol_usuario})",
        "jti": datos_token["jti"],
        "modulo": modulo
    }


def proteger_inicio_modulo(nombre_modulo: str):
    def decorador(funcion_modulo):
        @wraps(funcion_modulo)
        def envoltura(token: str | None, *args, **kwargs):
            estado_acceso = verificar_acceso_modulo(token, nombre_modulo)
            if not estado_acceso["ok"]:
                return estado_acceso
            return funcion_modulo(sesion=estado_acceso, *args, **kwargs)
        return envoltura
    return decorador