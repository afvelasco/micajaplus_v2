import sys
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from flask import Flask, request, jsonify
from flask_cors import CORS
from mysql.connector import Error

# Apuntar a la carpeta servicios-python para importar common
RUTA_SERVICIOS = Path(__file__).resolve().parent.parent
sys.path.append(str(RUTA_SERVICIOS))

from common.cliente_agw import exigir_sesion_agw

app = Flask(__name__)
CORS(app)


# CONFIGURACIÓN DE BASE DE DATOS


from common.db import obtener_conexion


ROLES_PERMITIDOS_ESCRITURA = {"ALMACEN", "CONTADOR", "FACTURADOR"}




def redondear_moneda(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validar_rol_escritura(sesion_agw: dict):
    """Restringe POST y PUT a 'ALMACEN', 'CONTADOR', 'FACTURADOR'"""
    rol = sesion_agw.get("rol", "")
    if rol not in ROLES_PERMITIDOS_ESCRITURA:
        return jsonify({
            "ok": False,
            "error": f"El rol '{rol}' no tiene permisos para modificar el catálogo. Permitidos: {', '.join(ROLES_PERMITIDOS_ESCRITURA)}"
        }), 403
    return None



# ENDPOINTS MS-03 

# 1. GET /api/v1/catalogo/items - Lista productos y servicios activos con su subcuenta PUC
@app.get("/api/v1/catalogo/items")
@exigir_sesion_agw("ventas", requerir_nit=False)
def listar_items_catalogo(sesion_agw):
    conn = None
    cursor = None
    try:
        conn = obtener_conexion()
        cursor = conn.cursor(dictionary=True)
        sql = """
            SELECT
                v.codigo_item, v.nombre_pro_ser, v.descripcion_pro_ser, v.tipo,
                v.subcuenta, p.descrip_subc, v.precio_cuanto_costo,
                v.aplica_descuento, v.descuento, v.total, v.precio_venta, v.estado
            FROM venta_producto_servicio v
            LEFT JOIN puc p ON v.subcuenta = p.subcuenta
            WHERE v.estado = 'ACTIVO'
            ORDER BY v.nombre_pro_ser ASC
        """
        cursor.execute(sql)
        items = cursor.fetchall()
        return jsonify({
            "ok": True,
            "total_items": len(items),
            "items": items
        }), 200
    except Error as db_err:
        return jsonify({"ok": False, "error": f"Error SQL: {str(db_err)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# 2. GET /api/v1/catalogo/items/<codigo_item> - Consulta individual de ítem para el carrito[cite: 4, 10]
@app.get("/api/v1/catalogo/items/<codigo_item>")
@exigir_sesion_agw("ventas", requerir_nit=False)
def obtener_item_catalogo(codigo_item, sesion_agw):
    conn = None
    cursor = None
    try:
        conn = obtener_conexion()
        cursor = conn.cursor(dictionary=True)
        sql = """
            SELECT
                codigo_item, nombre_pro_ser, tipo, subcuenta,
                precio_venta, aplica_descuento, descuento
            FROM venta_producto_servicio
            WHERE codigo_item = %s AND estado = 'ACTIVO'
        """
        cursor.execute(sql, (codigo_item,))
        item = cursor.fetchone()
        if not item:
            return jsonify({
                "ok": False,
                "error": f"El ítem '{codigo_item}' no existe o se encuentra INACTIVO."
            }), 404
        return jsonify({"ok": True, "item": item}), 200
    except Error as db_err:
        return jsonify({"ok": False, "error": f"Error SQL: {str(db_err)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# 3. POST /api/v1/catalogo/items - Registra nuevo producto o servicio vinculado a su subcuenta PUC[cite: 4, 10, 11]
@app.post("/api/v1/catalogo/items")
@exigir_sesion_agw("ventas", requerir_nit=False)
def crear_item_catalogo(sesion_agw):
    error_rol = validar_rol_escritura(sesion_agw)
    if error_rol:
        return error_rol

    data = request.get_json(silent=True) or {}
    campos_obligatorios = ["codigo_item", "nombre_pro_ser", "tipo", "subcuenta", "precio_cuanto_costo", "precio_venta"]
    for campo in campos_obligatorios:
        if campo not in data or str(data[campo]).strip() == "":
            return jsonify({"ok": False, "error": f"El campo '{campo}' es obligatorio."}), 400

    codigo_item = str(data["codigo_item"]).strip()
    nombre_pro_ser = str(data["nombre_pro_ser"]).strip()
    descripcion_pro_ser = str(data.get("descripcion_pro_ser", "")).strip()
    tipo = str(data["tipo"]).strip().upper()
    subcuenta = str(data["subcuenta"]).strip()

    if tipo not in ("PRODUCTO", "SERVICIO"):
        return jsonify({"ok": False, "error": "El campo 'tipo' debe ser 'PRODUCTO' o 'SERVICIO'."}), 400
    if len(subcuenta) != 6 or not subcuenta.isdigit():
        return jsonify({"ok": False, "error": "La 'subcuenta' PUC debe tener exactamente 6 dígitos."}), 400

    try:
        precio_costo = Decimal(str(data["precio_cuanto_costo"]))
        precio_venta = Decimal(str(data["precio_venta"]))
        aplica_descuento = bool(data.get("aplica_descuento", False))
        descuento_pct = Decimal(str(data.get("descuento", "0.0000"))) if aplica_descuento else Decimal("0.0000")
    except Exception:
        return jsonify({"ok": False, "error": "Los valores de precios y descuentos deben ser numéricos válidos."}), 400

    if precio_costo < 0 or precio_venta <= 0 or descuento_pct < 0:
        return jsonify({"ok": False, "error": "Precios o porcentajes fuera de rango válido."}), 400

    if "total" in data and data["total"] is not None:
        total_final = redondear_moneda(Decimal(str(data["total"])))
    else:
        valor_desc = precio_venta * (descuento_pct / Decimal("100"))
        total_final = redondear_moneda(precio_venta - valor_desc)

    conn = None
    cursor = None
    try:
        conn = obtener_conexion()
        cursor = conn.cursor(dictionary=True)

        # Validar que la subcuenta exista y esté ACTIVA en puc[cite: 4]
        cursor.execute("SELECT subcuenta FROM puc WHERE subcuenta = %s AND estado = 'ACTIVO'", (subcuenta,))
        if not cursor.fetchone():
            return jsonify({
                "ok": False,
                "error": f"La subcuenta PUC '{subcuenta}' no existe o está INACTIVA."
            }), 400

        # Validar llave primaria codigo_item no duplicada[cite: 4]
        cursor.execute("SELECT codigo_item FROM venta_producto_servicio WHERE codigo_item = %s", (codigo_item,))
        if cursor.fetchone():
            return jsonify({
                "ok": False,
                "error": f"El código de ítem '{codigo_item}' ya existe en el catálogo."
            }), 409

        sql = """
            INSERT INTO venta_producto_servicio (
                codigo_item, nombre_pro_ser, descripcion_pro_ser, tipo, subcuenta,
                precio_cuanto_costo, aplica_descuento, descuento, total,
                precio_venta, estado, autorizado_por
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVO', %s)
        """
        cursor.execute(sql, (
            codigo_item,
            nombre_pro_ser,
            descripcion_pro_ser,
            tipo,
            subcuenta,
            redondear_moneda(precio_costo),
            1 if aplica_descuento else 0,
            descuento_pct,
            total_final,
            redondear_moneda(precio_venta),
            sesion_agw["autorizado_por"]
        ))
        conn.commit()
        return jsonify({
            "ok": True,
            "mensaje": "Ítem registrado correctamente en el catálogo.",
            "codigo_item": codigo_item,
            "subcuenta": subcuenta,
            "precio_venta": str(redondear_moneda(precio_venta)),
            "total": str(total_final)
        }), 201
    except Error as db_err:
        if conn:
            conn.rollback()
        return jsonify({"ok": False, "error": f"Error SQL al registrar ítem: {str(db_err)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# 4. PUT /api/v1/catalogo/items/<codigo_item> - Actualiza precios, descuentos o subcuenta contable[cite: 4]
@app.put("/api/v1/catalogo/items/<codigo_item>")
@exigir_sesion_agw("ventas", requerir_nit=False)
def actualizar_item_catalogo(codigo_item, sesion_agw):
    error_rol = validar_rol_escritura(sesion_agw)
    if error_rol:
        return error_rol

    data = request.get_json(silent=True) or {}
    conn = None
    cursor = None
    try:
        conn = obtener_conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM venta_producto_servicio WHERE codigo_item = %s", (codigo_item,))
        actual = cursor.fetchone()
        if not actual:
            return jsonify({"ok": False, "error": f"El ítem '{codigo_item}' no existe."}), 404

        nombre_pro_ser = str(data.get("nombre_pro_ser", actual["nombre_pro_ser"])).strip()
        descripcion_pro_ser = str(data.get("descripcion_pro_ser", actual["descripcion_pro_ser"] or "")).strip()
        tipo = str(data.get("tipo", actual["tipo"])).strip().upper()
        subcuenta = str(data.get("subcuenta", actual["subcuenta"])).strip()
        estado = str(data.get("estado", actual["estado"])).strip().upper()

        if tipo not in ("PRODUCTO", "SERVICIO"):
            return jsonify({"ok": False, "error": "El campo 'tipo' debe ser 'PRODUCTO' o 'SERVICIO'."}), 400
        if estado not in ("ACTIVO", "INACTIVO"):
            return jsonify({"ok": False, "error": "El campo 'estado' debe ser 'ACTIVO' o 'INACTIVO'."}), 400

        cursor.execute("SELECT subcuenta FROM puc WHERE subcuenta = %s AND estado = 'ACTIVO'", (subcuenta,))
        if not cursor.fetchone():
            return jsonify({"ok": False, "error": f"La subcuenta PUC '{subcuenta}' no existe o está INACTIVA."}), 400

        precio_costo = Decimal(str(data.get("precio_cuanto_costo", actual["precio_cuanto_costo"])))
        precio_venta = Decimal(str(data.get("precio_venta", actual["precio_venta"])))
        aplica_descuento = bool(data["aplica_descuento"]) if "aplica_descuento" in data else bool(actual["aplica_descuento"])
        descuento_pct = Decimal(str(data.get("descuento", actual["descuento"] or "0.0000"))) if aplica_descuento else Decimal("0.0000")

        if "total" in data and data["total"] is not None:
            total_final = redondear_moneda(Decimal(str(data["total"])))
        else:
            valor_desc = precio_venta * (descuento_pct / Decimal("100"))
            total_final = redondear_moneda(precio_venta - valor_desc)

        sql = """
            UPDATE venta_producto_servicio
            SET
                nombre_pro_ser = %s,
                descripcion_pro_ser = %s,
                tipo = %s,
                subcuenta = %s,
                precio_cuanto_costo = %s,
                aplica_descuento = %s,
                descuento = %s,
                total = %s,
                precio_venta = %s,
                estado = %s,
                autorizado_por = %s
            WHERE codigo_item = %s
        """
        cursor.execute(sql, (
            nombre_pro_ser,
            descripcion_pro_ser,
            tipo,
            subcuenta,
            redondear_moneda(precio_costo),
            1 if aplica_descuento else 0,
            descuento_pct,
            total_final,
            redondear_moneda(precio_venta),
            estado,
            sesion_agw["autorizado_por"],
            codigo_item
        ))
        conn.commit()
        return jsonify({
            "ok": True,
            "mensaje": "Ítem actualizado correctamente.",
            "codigo_item": codigo_item,
            "precio_venta": str(redondear_moneda(precio_venta)),
            "total": str(total_final),
            "subcuenta": subcuenta,
            "estado": estado
        }), 200
    except Error as db_err:
        if conn:
            conn.rollback()
        return jsonify({"ok": False, "error": f"Error SQL al actualizar ítem: {str(db_err)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# 5. GET /api/v1/puc/subcuentas - Lista y filtra las subcuentas activas de la tabla puc[cite: 4, 11]
@app.get("/api/v1/puc/subcuentas")
@exigir_sesion_agw("ventas", requerir_nit=False)
def listar_subcuentas_puc(sesion_agw):
    clase = request.args.get("clase")
    termino = request.args.get("termino", "").strip()
    patron = f"%{termino}%"

    conn = None
    cursor = None
    try:
        conn = obtener_conexion()
        cursor = conn.cursor(dictionary=True)
        sql = """
            SELECT
                clase, descrip_clase, grupo, descrip_grupo,
                cuenta, descrip_cuenta, subcuenta, descrip_subc
            FROM puc
            WHERE estado = 'ACTIVO'
            AND (subcuenta LIKE %s OR descrip_subc LIKE %s)
            AND (%s IS NULL OR clase = %s)
            ORDER BY subcuenta ASC
        """
        cursor.execute(sql, (patron, patron, clase, clase))
        subcuentas = cursor.fetchall()
        return jsonify({
            "ok": True,
            "total_subcuentas": len(subcuentas),
            "subcuentas": subcuentas
        }), 200
    except Error as db_err:
        return jsonify({"ok": False, "error": f"Error SQL: {str(db_err)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8003, debug=True)