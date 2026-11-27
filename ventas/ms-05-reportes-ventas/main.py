from flask import Flask, request, jsonify
from common.db import obtener_conexion
from common.cliente_agw import exigir_sesion_agw

app = Flask(__name__)


@app.after_request
def agregar_cabeceras_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Nit-Empresa"
    return response


# 1. Detalle Unificado de una Venta (Trazabilidad Completa en un solo JSON)
# GET /api/v1/reportes/ventas/<id_venta>/detalle-unificado
@app.route("/api/v1/reportes/ventas/<id_venta>/detalle-unificado", methods=["GET"])
@exigir_sesion_agw("ventas", requerir_nit=False)
def obtener_detalle_unificado(sesion_agw: dict, id_venta: str):
    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)

    try:
        # Consulta A: Encabezado + Cliente + Vendedor + Cuenta PUC principal + Empresa
        cursor.execute("""
            SELECT 
                v.id_venta, v.fecha, v.marca_tiempo AS fecha_hora_exacta, v.forma_pago, 
                v.medio_pago, v.fecha_vencimiento, v.anticipo_iva, v.anticipo_ica, 
                v.aplica_descuento, v.descuento AS porcentaje_descuento_general, 
                v.incremento, v.total_venta, v.autorizado_por,
                e.nit AS nit_empresa, e.razon_social AS empresa_razon_social,
                c.tipo_identificacion, c.numero_identificacion, c.nombre_cliente, 
                c.regimen_fiscal_tributario, c.direccion AS direccion_cliente, 
                c.ciudad AS ciudad_cliente, c.telefono_cliente, c.email AS email_cliente,
                u.id_usuario AS id_vendedor, u.nombre AS nombre_vendedor, u.rol AS rol_vendedor,
                p.subcuenta AS puc_subcuenta, p.descrip_subc AS puc_descripcion_subcuenta, 
                p.cuenta AS puc_cuenta, p.descrip_cuenta AS puc_descripcion_cuenta, 
                p.descrip_clase AS puc_clase
            FROM ventas v
            INNER JOIN empresas e ON v.nit_empresa = e.nit
            INNER JOIN clientes c ON v.id_cliente = c.numero_identificacion
            INNER JOIN usuarios u ON v.id_vendedor = u.id_usuario
            LEFT JOIN puc p ON v.subcuenta = p.subcuenta
            WHERE v.id_venta = %s
            LIMIT 1
        """, (id_venta,))
        encabezado = cursor.fetchone()

        if not encabezado:
            return jsonify({"ok": False, "error": f"La venta '{id_venta}' no existe."}), 404

        # Formatear Decimales y Fechas
        for campo_decimal in ("anticipo_iva", "anticipo_ica", "porcentaje_descuento_general", "incremento", "total_venta"):
            encabezado[campo_decimal] = str(encabezado[campo_decimal] or "0.00")
        encabezado["fecha"] = str(encabezado["fecha"])
        encabezado["fecha_hora_exacta"] = str(encabezado["fecha_hora_exacta"])
        encabezado["fecha_vencimiento"] = str(encabezado["fecha_vencimiento"])

        # Consulta B: Productos y servicios vendidos en la factura + su PUC individual
        cursor.execute("""
            SELECT 
                efv.id_elemento, efv.codigo_item, vps.nombre_pro_ser, vps.tipo,
                efv.cantidad_items, efv.precio_unitario, efv.descuento AS descuento_item, 
                efv.monto_subtotal, vps.subcuenta AS item_subcuenta_puc, p.descrip_subc AS item_nombre_puc
            FROM elementos_factura_venta efv
            INNER JOIN venta_producto_servicio vps ON efv.codigo_item = vps.codigo_item
            LEFT JOIN puc p ON vps.subcuenta = p.subcuenta
            WHERE efv.id_venta = %s
        """, (id_venta,))
        items = cursor.fetchall()
        for it in items:
            it["precio_unitario"] = str(it["precio_unitario"])
            it["descuento_item"] = str(it["descuento_item"])
            it["monto_subtotal"] = str(it["monto_subtotal"])

        # Consulta C: Notas contables aplicadas a la venta
        cursor.execute("""
            SELECT id_nota, tipo_nota, valor_modificacion, fecha, marca_tiempo, autorizado_por
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
            "venta": encabezado,
            "elementos_vendidos": items,
            "notas_contables": notas
        }), 200
    finally:
        cursor.close()
        conn.close()


# 2. Reporte Consolidado Gerencial (Por forma de pago, medio de pago y subcuenta PUC)
# GET /api/v1/reportes/ventas/consolidado?nit_empresa=900123456&fecha_inicio=2026-11-01&fecha_fin=2026-11-30
@app.route("/api/v1/reportes/ventas/consolidado", methods=["GET"])
@exigir_sesion_agw("ventas", requerir_nit=True)
def obtener_reporte_consolidado(sesion_agw: dict):
    fecha_inicio = request.args.get("fecha_inicio", "2026-01-01")
    fecha_fin    = request.args.get("fecha_fin", "2099-12-31")

    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 
                v.forma_pago, v.medio_pago, v.subcuenta, p.descrip_subc,
                COUNT(v.id_venta) AS cantidad_facturas,
                SUM(v.total_venta) AS total_acumulado
            FROM ventas v
            LEFT JOIN puc p ON v.subcuenta = p.subcuenta
            WHERE v.nit_empresa = %s
            AND v.fecha BETWEEN %s AND %s
            GROUP BY v.forma_pago, v.medio_pago, v.subcuenta, p.descrip_subc
            ORDER BY total_acumulado DESC
        """, (sesion_agw["nit_empresa"], fecha_inicio, fecha_fin))
        resumen = cursor.fetchall()
        for fila in resumen:
            fila["total_acumulado"] = str(fila["total_acumulado"] or "0.00")

        return jsonify({
            "ok": True,
            "nit_empresa": sesion_agw["nit_empresa"],
            "rango_fechas": {"inicio": fecha_inicio, "fin": fecha_fin},
            "consolidado": resumen
        }), 200
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8005, debug=True)