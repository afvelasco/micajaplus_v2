import json
import urllib.request
import urllib.error
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ==========================================
# MAPA DE MICROSERVICIOS INTERNOS
# ==========================================
SERVICIOS_INTERNOS = {
    "ms01": "http://127.0.0.1:8000",  # MS-01: Autenticación y AGW (servidor_agw.py)
    "ms02": "http://127.0.0.1:8002",  # MS-02: Clientes (PHP)
    "ms03": "http://127.0.0.1:8003",  # MS-03: Catálogo y Cuentas PUC (Flask)
    "ms04": "http://127.0.0.1:8004",  # MS-04: Ventas y Cartera (Flask)
    "ms05": "http://127.0.0.1:8005",  # MS-05: Reportes Unificados (Flask)
}


# ==========================================
# GUARDIA GLOBAL DE SOLO LECTURA (MÓVIL)
# ==========================================
@app.before_request
def bloquear_escritura_movil():
    """
    1. Bloquea cualquier petición POST, PUT, PATCH o DELETE desde el móvil
    (excepto el login móvil y peticiones preflight OPTIONS).
    2. Exige que el token pertenezca al canal móvil (prefijo M1.).
    """
    if request.method == "OPTIONS":
        return None

    if request.path == "/api/v1/movil/iniciar-sesion" and request.method == "POST":
        return None

    # Bloqueo estricto de escritura
    if request.method != "GET":
        return jsonify({
            "ok": False,
            "codigo": 403,
            "error": f"Operación '{request.method}' bloqueada. La versión móvil es estrictamente de solo lectura (GET)."
        }), 403

    # Verificación de token de plataforma móvil (prefijo M1.)
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""

    if not token.startswith("M1."):
        return jsonify({
            "ok": False,
            "codigo": 401,
            "error": "Acceso denegado: Se requiere un token de sesión móvil válido (prefijo M1.)."
        }), 401

    return None


def reenviar_peticion(url_destino: str, metodo: str = "GET", payload: dict | None = None):
    """Reenvía cabeceras (Authorization, X-Nit-Empresa) y query params al microservicio interno."""
    query_string = request.query_string.decode("utf-8")
    if query_string:
        url_destino = f"{url_destino}?{query_string}"

    cabeceras = {"Content-Type": "application/json; charset=utf-8"}
    if "Authorization" in request.headers:
        cabeceras["Authorization"] = request.headers["Authorization"]
    if "X-Nit-Empresa" in request.headers:
        cabeceras["X-Nit-Empresa"] = request.headers["X-Nit-Empresa"]

    datos_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url_destino, data=datos_bytes, headers=cabeceras, method=metodo)

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            cuerpo = resp.read().decode("utf-8")
            return jsonify(json.loads(cuerpo)), resp.status
    except urllib.error.HTTPError as err:
        try:
            cuerpo_err = json.loads(err.read().decode("utf-8"))
        except Exception:
            cuerpo_err = {"ok": False, "codigo": err.code, "error": f"Error HTTP {err.code} en servicio interno."}
        return jsonify(cuerpo_err), err.code
    except Exception as ex:
        return jsonify({
            "ok": False,
            "codigo": 503,
            "error": f"El microservicio interno no responde: {str(ex)}"
        }), 503


# ==========================================
# 1. AUTENTICACIÓN MÓVIL (CONECTADO A /agw/login DE MS-01)
# ==========================================
@app.post("/api/v1/movil/iniciar-sesion")
def login_movil():
    """
    Envía plataforma='MOVIL' al endpoint real /agw/login de servidor_agw.py (puerto 8000)
    para que emita el token cifrado con prefijo M1.
    """
    data = request.get_json(silent=True) or {}
    payload_ms01 = {
        "identificacion": str(
            data.get("identificacion")
            or data.get("credencial")
            or data.get("correo_electronico")
            or ""
        ).strip(),
        "contrasena": str(data.get("contrasena") or ""),
        "plataforma": "MOVIL"
    }
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms01']}/agw/login", "POST", payload_ms01)


@app.get("/api/v1/movil/verificar-sesion")
def verificar_sesion_movil():
    """Permite a la app móvil validar si su token M1. sigue vigente y qué rol/empresa tiene."""
    modulo = request.args.get("modulo", "ventas")
    nit = request.args.get("nit") or request.headers.get("X-Nit-Empresa", "")
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()

    return reenviar_peticion(
        f"{SERVICIOS_INTERNOS['ms01']}/agw/verificar-acceso",
        "POST",
        {"token": token, "modulo": modulo, "nit": nit}
    )


# ==========================================
# 2. CONSULTAS DE CLIENTES (MS-02 - SOLO GET)
# ==========================================
@app.get("/api/v1/movil/clientes")
def listar_clientes_movil():
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms02']}/api/v1/clientes")


@app.get("/api/v1/movil/clientes/buscar")
def buscar_cliente_movil():
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms02']}/api/v1/clientes/buscar")


@app.get("/api/v1/movil/clientes/<numero_identificacion>")
def detalle_cliente_movil(numero_identificacion):
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms02']}/api/v1/clientes/{numero_identificacion}")


# ==========================================
# 3. CONSULTAS DE CATÁLOGO Y PUC (MS-03 - SOLO GET)
# ==========================================
@app.get("/api/v1/movil/catalogo/items")
def listar_items_movil():
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms03']}/api/v1/catalogo/items")


@app.get("/api/v1/movil/catalogo/items/<codigo_item>")
def detalle_item_movil(codigo_item):
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms03']}/api/v1/catalogo/items/{codigo_item}")


@app.get("/api/v1/movil/puc/subcuentas")
def listar_puc_movil():
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms03']}/api/v1/puc/subcuentas")


# ==========================================
# 4. CONSULTAS DE VENTAS Y CARTERA (MS-04 - SOLO GET)
# ==========================================
@app.get("/api/v1/movil/ventas")
def listar_ventas_movil():
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms04']}/api/v1/ventas")


@app.get("/api/v1/movil/ventas/cartera-vencimientos")
def cartera_movil():
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms04']}/api/v1/ventas/cartera-vencimientos")


@app.get("/api/v1/movil/ventas/<id_venta>/notas-contables")
def notas_venta_movil(id_venta):
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms04']}/api/v1/ventas/{id_venta}/notas-contables")


# ==========================================
# 5. REPORTES UNIFICADOS Y CONSOLIDADO (MS-05 - SOLO GET)
# ==========================================
@app.get("/api/v1/movil/reportes/ventas/<id_venta>/detalle-unificado")
def detalle_unificado_movil(id_venta):
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms05']}/api/v1/reportes/ventas/{id_venta}/detalle-unificado")


@app.get("/api/v1/movil/reportes/ventas/consolidado")
def consolidado_movil():
    return reenviar_peticion(f"{SERVICIOS_INTERNOS['ms05']}/api/v1/reportes/ventas/consolidado")


if __name__ == "__main__":
    # Escucha en todas las interfaces (0.0.0.0) para que el celular se conecte por Wi-Fi
    app.run(host="0.0.0.0", port=8006, debug=True)