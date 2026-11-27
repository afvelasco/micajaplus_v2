

from common.db import obtener_conexion
from common.cliente_agw import exigir_sesion_agw
from flask import Flask, request, jsonify
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from mysql.connector import Error as MySQLError
from common.db import obtener_conexion
from common.cliente_agw import exigir_sesion_agw
from common.id_generador import generar_consecutivo_9_chars

app = Flask(__name__)

# Enumerados exactos definidos en el SQL de mi_caja_plus_adso09
FORMAS_PAGO_VALIDAS = ("CREDITO", "CONTADO")
MEDIOS_PAGO_VALIDOS = ("TRANSFERENCIA", "PSE", "BRE-B", "EFECTIVO")
TIPOS_NOTA_VALIDOS  = ("CREDITO", "DEBITO")


@app.after_request
def agregar_cabeceras_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Nit-Empresa"
    return response


def redondear_moneda(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def redondear_tasa(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def liquidar_carrito(cursor, data: dict) -> dict:
    """Calcula subtotales por línea e impuestos/descuentos del encabezado."""
    items_entrada = data.get("items")
    if not isinstance(items_entrada, list) or len(items_entrada) == 0:
        raise ValueError("Debe incluir al menos un ítem válido en el arreglo 'items'.")

    codigos = [str(i.get("codigo_item", "")).strip() for i in items_entrada if i.get("codigo_item")]
    if len(codigos) != len(items_entrada):
        raise ValueError("Todos los elementos en 'items' deben tener 'codigo_item'.")

    placeholders = ",".join(["%s"] * len(codigos))
    cursor.execute(f"""
        SELECT codigo_item, nombre_pro_ser, precio_venta, aplica_descuento, descuento, subcuenta
        FROM venta_producto_servicio
        WHERE codigo_item IN ({placeholders}) AND estado = 'ACTIVO'
    """, tuple(codigos))
    catalogo_bd = {row["codigo_item"]: row for row in cursor.fetchall()}

    lineas_liquidadas = []
    suma_subtotales = Decimal("0.00")

    for item in items_entrada:
        cod = str(item["codigo_item"]).strip()
        if cod not in catalogo_bd:
            raise ValueError(f"El artículo '{cod}' no existe en el catálogo o está INACTIVO.")

        try:
            cantidad = int(item.get("cantidad_items", 0))
        except (ValueError, TypeError):
            raise ValueError(f"La cantidad del ítem '{cod}' debe ser un número entero.")

        if cantidad <= 0 or cantidad > 99999:
            raise ValueError(f"La cantidad del ítem '{cod}' debe estar entre 1 y 99999.")

        prod = catalogo_bd[cod]
        precio_unit = redondear_moneda(Decimal(str(prod["precio_venta"])))

        desc_crudo = item.get("descuento", prod["descuento"] if prod["aplica_descuento"] else "0.0000")
        tasa_desc_item = redondear_tasa(Decimal(str(desc_crudo or "0.0000")))

        if tasa_desc_item < Decimal("0.0000") or tasa_desc_item >= Decimal("1.0000"):
            raise ValueError(f"La tasa de descuento del ítem '{cod}' debe estar entre 0.0000 y 0.9999.")

        bruto_linea = precio_unit * Decimal(cantidad)
        valor_desc_linea = bruto_linea * tasa_desc_item
        subtotal_linea = redondear_moneda(bruto_linea - valor_desc_linea)
        suma_subtotales += subtotal_linea

        lineas_liquidadas.append({
            "codigo_item": cod,
            "nombre_pro_ser": prod["nombre_pro_ser"],
            "subcuenta_item": prod["subcuenta"],
            "precio_unitario": str(precio_unit),
            "cantidad_items": cantidad,
            "descuento": str(tasa_desc_item),
            "monto_subtotal": str(subtotal_linea)
        })

    try:
        tasa_desc_general = redondear_tasa(Decimal(str(data.get("descuento", "0.0000") or "0.0000")))
        tasa_incremento   = redondear_tasa(Decimal(str(data.get("incremento", "0.0000") or "0.0000")))
        tasa_anticipo_iva = redondear_tasa(Decimal(str(data.get("anticipo_iva", "0.0000") or "0.0000")))
        tasa_anticipo_ica = redondear_tasa(Decimal(str(data.get("anticipo_ica", "0.0000") or "0.0000")))
    except InvalidOperation:
        raise ValueError("Las tasas de impuestos, descuento o incremento tienen formato numérico inválido.")

    base_gravable = suma_subtotales - (suma_subtotales * tasa_desc_general) + (suma_subtotales * tasa_incremento)
    valor_iva = base_gravable * tasa_anticipo_iva
    valor_ica = base_gravable * tasa_anticipo_ica
    total_venta = redondear_moneda(base_gravable + valor_iva + valor_ica)

    return {
        "suma_subtotales": str(redondear_moneda(suma_subtotales)),
        "base_gravable": str(redondear_moneda(base_gravable)),
        "valor_iva": str(redondear_moneda(valor_iva)),
        "valor_ica": str(redondear_moneda(valor_ica)),
        "anticipo_iva": str(tasa_anticipo_iva),
        "anticipo_ica": str(tasa_anticipo_ica),
        "aplica_descuento": 1 if tasa_desc_general > Decimal("0.0000") else 0,
        "descuento": str(tasa_desc_general),
        "incremento": str(tasa_incremento),
        "total_venta": str(total_venta),
        "elementos": lineas_liquidadas
    }


# 1. Simulador previo en memoria
@app.route("/api/v1/ventas/calcular-totales", methods=["POST"])
@exigir_sesion_agw("ventas", requerir_nit=False)
def calcular_totales(sesion_agw: dict):
    data = request.get_json(silent=True) or {}
    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    try:
        resultado = liquidar_carrito(cursor, data)
        return jsonify({"ok": True, "liquidacion": resultado}), 200
    except ValueError as err:
        return jsonify({"ok": False, "error": str(err)}), 400
    finally:
        cursor.close()
        conn.close()


# 2. Registro definitivo de la venta (Transacción Atómica)
@app.route("/api/v1/ventas/registrar", methods=["POST"])
@exigir_sesion_agw("ventas", requerir_nit=True)
def registrar_venta(sesion_agw: dict):
    data = request.get_json(silent=True) or {}

    id_cliente = str(data.get("id_cliente", "")).strip()
    subcuenta  = str(data.get("subcuenta", "")).strip() or None
    forma_pago = str(data.get("forma_pago", "")).strip().upper()
    medio_pago = str(data.get("medio_pago", "")).strip().upper()
    fecha_venc = str(data.get("fecha_vencimiento", "")).strip()

    if not id_cliente:
        return jsonify({"ok": False, "error": "El campo 'id_cliente' es obligatorio."}), 400
    if forma_pago not in FORMAS_PAGO_VALIDAS:
        return jsonify({"ok": False, "error": f"forma_pago inválida. Use: {FORMAS_PAGO_VALIDAS}"}), 400
    if medio_pago not in MEDIOS_PAGO_VALIDOS:
        return jsonify({"ok": False, "error": f"medio_pago inválido. Use: {MEDIOS_PAGO_VALIDOS}"}), 400
    if not fecha_venc:
        return jsonify({"ok": False, "error": "El campo 'fecha_vencimiento' (YYYY-MM-DD) es obligatorio."}), 400

    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)

    try:
        conn.start_transaction()

        cursor.execute("SELECT estado FROM clientes WHERE numero_identificacion = %s LIMIT 1", (id_cliente,))
        cliente = cursor.fetchone()
        if not cliente:
            raise ValueError(f"El cliente con documento '{id_cliente}' no está registrado.")
        if cliente["estado"] != "ACTIVO":
            raise ValueError(f"El cliente '{id_cliente}' se encuentra INACTIVO y no puede ser facturado.")

        liquidacion = liquidar_carrito(cursor, data)
        id_venta = generar_consecutivo_9_chars(cursor, "ventas", "id_venta", "FV")

        cursor.execute("""
            INSERT INTO ventas (
                id_venta, nit_empresa, id_cliente, id_vendedor, subcuenta, 
                fecha, forma_pago, fecha_vencimiento, medio_pago, 
                anticipo_iva, anticipo_ica, aplica_descuento, descuento, 
                incremento, total_venta, autorizado_por
            ) VALUES (%s, %s, %s, %s, %s, CURDATE(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            id_venta,
            sesion_agw["nit_empresa"],
            id_cliente,
            sesion_agw["id_usuario"],
            subcuenta,
            forma_pago,
            fecha_venc,
            medio_pago,
            liquidacion["anticipo_iva"],
            liquidacion["anticipo_ica"],
            liquidacion["aplica_descuento"],
            liquidacion["descuento"],
            liquidacion["incremento"],
            liquidacion["total_venta"],
            sesion_agw["autorizado_por"]
        ))

        for linea in liquidacion["elementos"]:
            id_elemento = generar_consecutivo_9_chars(cursor, "elementos_factura_venta", "id_elemento", "EL")
            cursor.execute("""
                INSERT INTO elementos_factura_venta (
                    id_elemento, id_venta, codigo_item, precio_unitario, 
                    cantidad_items, monto_subtotal, descuento, autorizado_por
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                id_elemento,
                id_venta,
                linea["codigo_item"],
                linea["precio_unitario"],
                linea["cantidad_items"],
                linea["monto_subtotal"],
                linea["descuento"],
                sesion_agw["autorizado_por"]
            ))

        conn.commit()
        return jsonify({
            "ok": True,
            "mensaje": "Factura de venta registrada exitosamente.",
            "id_venta": id_venta,
            "nit_empresa": sesion_agw["nit_empresa"],
            "vendedor": sesion_agw["autorizado_por"],
            "resumen": liquidacion
        }), 201

    except ValueError as err:
        conn.rollback()
        return jsonify({"ok": False, "error": str(err)}), 400
    except MySQLError as err_sql:
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": f"Error de integridad en base de datos ({err_sql.errno}): {err_sql.msg}"
        }), 409
    finally:
        cursor.close()
        conn.close()


# 3. Listado general paginado y filtrable de ventas
@app.route("/api/v1/ventas", methods=["GET"])
@exigir_sesion_agw("ventas", requerir_nit=True)
def listar_ventas(sesion_agw: dict):
    fecha_inicio = request.args.get("fecha_inicio", "2026-01-01")
    fecha_fin    = request.args.get("fecha_fin", "2099-12-31")
    id_cliente   = request.args.get("id_cliente") or None
    id_vendedor  = request.args.get("id_vendedor") or None
    forma_pago   = request.args.get("forma_pago", "").upper() or None
    medio_pago   = request.args.get("medio_pago", "").upper() or None

    pagina = max(1, int(request.args.get("pagina", 1)))
    limite = max(1, min(100, int(request.args.get("limite", 20))))
    offset = (pagina - 1) * limite

    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 
                v.id_venta, v.fecha, v.marca_tiempo, v.id_cliente, c.nombre_cliente, 
                v.id_vendedor, u.nombre AS nombre_vendedor, v.subcuenta, 
                v.forma_pago, v.medio_pago, v.fecha_vencimiento, v.total_venta, v.autorizado_por
            FROM ventas v
            INNER JOIN clientes c ON v.id_cliente = c.numero_identificacion
            INNER JOIN usuarios u ON v.id_vendedor = u.id_usuario
            WHERE v.nit_empresa = %s
            AND v.fecha BETWEEN %s AND %s
            AND (%s IS NULL OR v.id_cliente = %s)
            AND (%s IS NULL OR v.id_vendedor = %s)
            AND (%s IS NULL OR v.forma_pago = %s)
            AND (%s IS NULL OR v.medio_pago = %s)
            ORDER BY v.marca_tiempo DESC
            LIMIT %s OFFSET %s
        """, (
            sesion_agw["nit_empresa"],
            fecha_inicio, fecha_fin,
            id_cliente, id_cliente,
            id_vendedor, id_vendedor,
            forma_pago, forma_pago,
            medio_pago, medio_pago,
            limite, offset
        ))
        ventas = cursor.fetchall()
        for v in ventas:
            v["total_venta"] = str(v["total_venta"])
            v["fecha"] = str(v["fecha"])
            v["fecha_vencimiento"] = str(v["fecha_vencimiento"])
            v["marca_tiempo"] = str(v["marca_tiempo"])

        return jsonify({
            "ok": True,
            "nit_empresa": sesion_agw["nit_empresa"],
            "pagina": pagina,
            "limite": limite,
            "datos": ventas
        }), 200
    finally:
        cursor.close()
        conn.close()


# 4. Seguimiento de cartera y vencimientos (Ventas a CREDITO)
@app.route("/api/v1/ventas/cartera-vencimientos", methods=["GET"])
@exigir_sesion_agw("ventas", requerir_nit=True)
def obtener_cartera_vencimientos(sesion_agw: dict):
    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 
                v.id_venta, v.fecha, v.fecha_vencimiento, 
                DATEDIFF(v.fecha_vencimiento, CURDATE()) AS dias_para_vencer,
                c.numero_identificacion, c.nombre_cliente, c.telefono_cliente, c.email,
                v.total_venta, v.autorizado_por
            FROM ventas v
            INNER JOIN clientes c ON v.id_cliente = c.numero_identificacion
            WHERE v.nit_empresa = %s AND v.forma_pago = 'CREDITO'
            ORDER BY v.fecha_vencimiento ASC
        """, (sesion_agw["nit_empresa"],))
        cartera = cursor.fetchall()
        for item in cartera:
            item["total_venta"] = str(item["total_venta"])
            item["fecha"] = str(item["fecha"])
            item["fecha_vencimiento"] = str(item["fecha_vencimiento"])

        return jsonify({
            "ok": True,
            "nit_empresa": sesion_agw["nit_empresa"],
            "total_facturas_credito": len(cartera),
            "cartera": cartera
        }), 200
    finally:
        cursor.close()
        conn.close()


# 5. Registrar Nota Contable (CREDITO / DEBITO)
@app.route("/api/v1/ventas/<id_venta>/notas-contables", methods=["POST"])
@exigir_sesion_agw("ventas", requerir_nit=True)
def crear_nota_contable(sesion_agw: dict, id_venta: str):
    data = request.get_json(silent=True) or {}
    tipo_nota = str(data.get("tipo_nota", "")).strip().upper()

    if tipo_nota not in TIPOS_NOTA_VALIDOS:
        return jsonify({"ok": False, "error": "El campo 'tipo_nota' debe ser 'CREDITO' o 'DEBITO'."}), 400

    try:
        valor_mod = redondear_moneda(Decimal(str(data.get("valor_modificacion", "0"))))
        if valor_mod <= Decimal("0.00"):
            raise ValueError("El 'valor_modificacion' debe ser mayor a 0.00.")
    except (InvalidOperation, ValueError) as err:
        return jsonify({"ok": False, "error": str(err)}), 400

    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id_venta, total_venta FROM ventas WHERE id_venta = %s AND nit_empresa = %s LIMIT 1",
            (id_venta, sesion_agw["nit_empresa"])
        )
        venta = cursor.fetchone()
        if not venta:
            return jsonify({"ok": False, "error": f"La venta '{id_venta}' no existe en esta empresa."}), 404

        if tipo_nota == "CREDITO" and valor_mod > Decimal(str(venta["total_venta"])):
            return jsonify({
                "ok": False,
                "error": f"La nota crédito ({valor_mod}) no puede superar el total de la venta ({venta['total_venta']})."
            }), 400

        id_nota = generar_consecutivo_9_chars(cursor, "notas_contables", "id_nota", "NC")
        cursor.execute("""
            INSERT INTO notas_contables (
                id_nota, id_venta, id_compra, tipo_nota, 
                valor_modificacion, fecha, autorizado_por
            ) VALUES (%s, %s, NULL, %s, %s, CURDATE(), %s)
        """, (
            id_nota,
            id_venta,
            tipo_nota,
            str(valor_mod),
            sesion_agw["autorizado_por"]
        ))
        conn.commit()
        return jsonify({
            "ok": True,
            "mensaje": f"Nota {tipo_nota} registrada exitosamente.",
            "id_nota": id_nota,
            "id_venta": id_venta,
            "valor_modificacion": str(valor_mod)
        }), 201
    except MySQLError as err_sql:
        conn.rollback()
        return jsonify({"ok": False, "error": f"Error SQL: {err_sql.msg}"}), 400
    finally:
        cursor.close()
        conn.close()


# 6. Listar Notas Contables de una venta específica
@app.route("/api/v1/ventas/<id_venta>/notas-contables", methods=["GET"])
@exigir_sesion_agw("ventas", requerir_nit=False)
def listar_notas_por_venta(sesion_agw: dict, id_venta: str):
    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id_nota, id_venta, tipo_nota, valor_modificacion, fecha, marca_tiempo, autorizado_por
            FROM notas_contables
            WHERE id_venta = %s
            ORDER BY marca_tiempo DESC
        """, (id_venta,))
        notas = cursor.fetchall()
        for n in notas:
            n["valor_modificacion"] = str(n["valor_modificacion"])
            n["fecha"] = str(n["fecha"])
            n["marca_tiempo"] = str(n["marca_tiempo"])

        return jsonify({
            "ok": True,
            "id_venta": id_venta,
            "notas": notas
        }), 200
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8004, debug=True)





