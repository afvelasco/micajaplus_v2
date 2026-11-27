import hmac
import html
import math
import os
import secrets
import smtplib
import threading
import time
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote
from mysql.connector import Error as MySQLError
from seguridad import (
    generar_hash_contrasena,
    verificar_contrasena,
    generar_token_identidad,
    verificar_token_identidad,
    verificar_acceso_modulo
)
from repositorios import (
    obtener_por_id,
    obtener_por_correo,
    obtener_por_identificacion,
    actualizar_contrasena,
    listar_empresas_de_usuario,
    verificar_vinculo_activo
)

CODIGO_VIGENCIA_MINUTOS = 15
CODIGO_MAX_INTENTOS = 5
CODIGO_COOLDOWN_REENVIO_SEGUNDOS = 60

RECUPERACION_VENTANA_SEGUNDOS = 3600
RECUPERACION_MAX_POR_CORREO = 5
RECUPERACION_VENTANA_GLOBAL_SEGUNDOS = 86400
RECUPERACION_MAX_GLOBAL = 200

# Mismo texto exista o no la cuenta, para no permitir enumerar usuarios.
RESPUESTA_ENLACE_ENVIADO = "Si el correo está registrado, recibirás un enlace de recuperación."
RESPUESTA_CODIGO_ENVIADO = "Si el correo está registrado, recibirás un código de recuperación."
RESPUESTA_CODIGO_INVALIDO = "El código es incorrecto o ha expirado. Solicita uno nuevo."

# Codigos, cooldowns y solicitudes en memoria.
_ALMACEN_CODIGOS: dict[str, dict] = {}
_COOLDOWN_REENVIO: dict[str, float] = {}
_CANDADO_CODIGOS = threading.Lock()

_SOLICITUDES_CORREO: dict[str, list[float]] = {}
_SOLICITUDES_GLOBAL: list[float] = []
_CANDADO_LIMITE = threading.Lock()


def _reservar_reenvio(correo_electronico: str) -> int:
    # Segundos de espera si aun no puede pedir otro codigo; si puede, marca el envio.
    ahora = time.time()
    with _CANDADO_CODIGOS:
        vencidos = [
            clave for clave, marca in _COOLDOWN_REENVIO.items()
            if ahora - marca >= CODIGO_COOLDOWN_REENVIO_SEGUNDOS
        ]
        for clave in vencidos:
            _COOLDOWN_REENVIO.pop(clave, None)

        marca = _COOLDOWN_REENVIO.get(correo_electronico)
        if marca is not None:
            restante = marca + CODIGO_COOLDOWN_REENVIO_SEGUNDOS - ahora
            if restante > 0:
                return math.ceil(restante)

        _COOLDOWN_REENVIO[correo_electronico] = ahora
        return 0


def _liberar_reenvio(correo_electronico: str) -> None:
    # Suelta el cooldown cuando el envio falla, para no castigar un reintento.
    with _CANDADO_CODIGOS:
        _COOLDOWN_REENVIO.pop(correo_electronico, None)


def _guardar_codigo(correo_electronico: str, id_usuario: str, codigo: str) -> None:
    ahora = time.time()
    with _CANDADO_CODIGOS:
        vencidos = [clave for clave, reg in _ALMACEN_CODIGOS.items() if reg["expira"] <= ahora]
        for clave in vencidos:
            _ALMACEN_CODIGOS.pop(clave, None)

        _ALMACEN_CODIGOS[correo_electronico] = {
            "id_usuario": id_usuario,
            "codigo": codigo,
            "intentos": 0,
            "expira": ahora + (CODIGO_VIGENCIA_MINUTOS * 60)
        }


def _obtener_codigo(correo_electronico: str) -> dict | None:
    ahora = time.time()
    with _CANDADO_CODIGOS:
        registro = _ALMACEN_CODIGOS.get(correo_electronico)
        if not registro:
            return None
        if registro["expira"] <= ahora:
            _ALMACEN_CODIGOS.pop(correo_electronico, None)
            return None
        return {
            "id_usuario": registro["id_usuario"],
            "codigo": registro["codigo"],
            "intentos": registro["intentos"]
        }


def _incrementar_intentos_memoria(correo_electronico: str) -> None:
    with _CANDADO_CODIGOS:
        registro = _ALMACEN_CODIGOS.get(correo_electronico)
        if registro:
            registro["intentos"] += 1


def _descartar_codigo(correo_electronico: str) -> None:
    with _CANDADO_CODIGOS:
        _ALMACEN_CODIGOS.pop(correo_electronico, None)


def _espera_limite_solicitudes(correo_electronico: str) -> int:
    # Segundos de espera si el correo o el sistema superaron el limite; 0 si puede seguir.
    ahora = time.time()
    with _CANDADO_LIMITE:
        _SOLICITUDES_GLOBAL[:] = [
            marca for marca in _SOLICITUDES_GLOBAL
            if ahora - marca < RECUPERACION_VENTANA_GLOBAL_SEGUNDOS
        ]
        if len(_SOLICITUDES_GLOBAL) >= RECUPERACION_MAX_GLOBAL:
            espera = RECUPERACION_VENTANA_GLOBAL_SEGUNDOS - (ahora - _SOLICITUDES_GLOBAL[0])
            return max(math.ceil(espera), 1)

        historial = [
            marca for marca in _SOLICITUDES_CORREO.get(correo_electronico, [])
            if ahora - marca < RECUPERACION_VENTANA_SEGUNDOS
        ]
        _SOLICITUDES_CORREO[correo_electronico] = historial
        if len(historial) >= RECUPERACION_MAX_POR_CORREO:
            espera = RECUPERACION_VENTANA_SEGUNDOS - (ahora - historial[0])
            return max(math.ceil(espera), 1)

    return 0


def _registrar_solicitud(correo_electronico: str) -> None:
    ahora = time.time()
    with _CANDADO_LIMITE:
        _SOLICITUDES_CORREO.setdefault(correo_electronico, []).append(ahora)
        _SOLICITUDES_GLOBAL.append(ahora)


def _crear_mensaje_micajaplus(
    destinatario: str,
    asunto: str,
    texto_plano: str,
    contenido_html: str
) -> EmailMessage:
    mensaje = EmailMessage()
    smtp_user = os.getenv("SMTP_USER", "").strip()
    nombre_remitente = os.getenv("EMAIL_FROM_NAME", "Mi Caja+").strip() or "Mi Caja+"
    mensaje["Subject"] = asunto
    mensaje["From"] = Address(display_name=nombre_remitente, addr_spec=smtp_user)
    mensaje["To"] = destinatario
    mensaje.set_content(texto_plano)

    ruta_logo = Path(
        os.getenv(
            "EMAIL_LOGO_PATH",
            str(Path(__file__).resolve().parents[2] / "model" / "insumos" / "simbolo_micajaplus.png")
        )
    )
    logo = None
    try:
        logo = ruta_logo.read_bytes()
    except OSError as err:
        print(f"[AVISO CORREO]: No se pudo leer el logo {ruta_logo}: {err}")
    logo_html = (
        '<img src="cid:micajaplus-logo" width="88" height="95" '
        'alt="Mi Caja+" style="display:block;width:88px;height:auto;margin:0 auto 10px;">'
        if logo else
        '<div style="color:#111827;font-size:24px;font-weight:700;letter-spacing:-0.4px;margin:0 0 6px;">Mi Caja+</div>'
    )

    html_completo = f"""\
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(asunto)}</title>
</head>
<body style="margin:0;padding:0;background-color:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#f9fafb;">
    <tr>
      <td align="center" style="padding:40px 16px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
          style="max-width:520px;background-color:#ffffff;border:1px solid #e5e7eb;border-radius:12px;">
          <tr>
            <td align="center" style="padding:40px 40px 20px;">
              {logo_html}
              <div style="font-size:12px;letter-spacing:2px;font-weight:600;color:#6b7280;text-transform:uppercase;">MI CAJA+</div>
            </td>
          </tr>
          <tr>
            <td style="padding:10px 40px 36px;font-size:15px;line-height:1.6;color:#374151;">
              {contenido_html}
              <p style="margin:32px 0 0;padding-top:20px;border-top:1px solid #f3f4f6;font-size:13px;color:#9ca3af;">
                Si no solicitaste esta acción, puedes ignorar este correo. Su cuenta seguirá segura.
              </p>
            </td>
          </tr>
        </table>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:520px;">
          <tr>
            <td align="center" style="padding:20px 16px 0;font-size:12px;color:#9ca3af;">
              Mensaje automático de Mi Caja+. Por favor, no respondas a este correo.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    mensaje.add_alternative(html_completo, subtype="html")
    if logo:
        parte_html = mensaje.get_body(preferencelist=("html",))
        parte_html.add_related(
            logo,
            maintype="image",
            subtype="png",
            cid="<micajaplus-logo>",
            filename="simbolo_micajaplus.png",
            disposition="inline"
        )
    return mensaje


def _enviar_correo_recuperacion(destinatario: str, nombre: str, url_recuperacion: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()

    if not smtp_user or not smtp_pass:
        print("[ERROR SMTP]: Faltan SMTP_USER / SMTP_PASSWORD en el .env; no se envio el correo.")
        return False

    nombre_html = html.escape(str(nombre or ""))
    enlace_html = html.escape(url_recuperacion, quote=True)
    texto_plano = (
        f"Hola {nombre},\n\n"
        f"Has solicitado restablecer tu contraseña en Mi Caja+.\n"
        f"Abre este enlace, válido por 15 minutos, para crear una nueva contraseña:\n\n"
        f"{url_recuperacion}\n\n"
        "Si no solicitaste esta acción, ignora este correo."
    )
    contenido_html = f"""\
<h1 style="margin:0 0 14px;font-size:23px;line-height:1.3;color:#20263a;">Restablece tu contraseña</h1>
<p>Hola {nombre_html},</p>
<p>Recibimos una solicitud para crear una nueva contraseña de tu cuenta de Mi Caja+.</p>
<p style="margin:25px 0;text-align:center;">
  <a href="{enlace_html}" style="display:inline-block;padding:13px 24px;border-radius:9px;background-color:#7651d6;color:#ffffff;text-decoration:none;font-weight:700;">
    Crear nueva contraseña
  </a>
</p>
<p>Este enlace vence en <strong>15 minutos</strong>. Si el botón no funciona, copia y pega este enlace en tu navegador:</p>
<p style="word-break:break-all;font-size:13px;color:#7651d6;">{enlace_html}</p>"""
    mensaje = _crear_mensaje_micajaplus(
        destinatario,
        "Mi Caja+ | Restablece tu contraseña",
        texto_plano,
        contenido_html
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as servidor_smtp:
            servidor_smtp.starttls()
            servidor_smtp.login(smtp_user, smtp_pass)
            servidor_smtp.send_message(mensaje)
        return True
    except Exception as err:
        print(f"[ERROR SMTP]: No se pudo enviar el correo: {err}")
        return False


def _enviar_correo_codigo(destinatario: str, nombre: str, codigo: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()

    if not smtp_user or not smtp_pass:
        print("[ERROR SMTP]: Faltan SMTP_USER / SMTP_PASSWORD en el .env; no se envio el codigo.")
        return False

    texto_plano = (
        f"Hola {nombre},\n\n"
        f"Tu codigo de recuperacion de Mi Caja+ es: {codigo}\n\n"
        f"Ingresalo en la aplicacion para crear tu nueva contraseña. "
        f"El codigo es valido por {CODIGO_VIGENCIA_MINUTOS} minutos.\n\n"
        f"Si no solicitaste este cambio, ignora este mensaje."
    )
    nombre_html = html.escape(str(nombre or ""))
    codigo_html = html.escape(codigo)
    contenido_html = f"""\
<h1 style="margin:0 0 14px;font-size:23px;line-height:1.3;color:#20263a;">Código de recuperación</h1>
<p>Hola {nombre_html},</p>
<p>Usa este código para continuar con el cambio de contraseña de tu cuenta:</p>
<div style="margin:24px 0;padding:18px 12px;border:1px solid #e8e3f5;border-radius:12px;background-color:#faf9fd;text-align:center;">
  <span style="font-size:34px;line-height:1.2;font-weight:700;letter-spacing:10px;color:#7651d6;">{codigo_html}</span>
</div>
<p>El código vence en <strong>{CODIGO_VIGENCIA_MINUTOS} minutos</strong> y solo puede usarse una vez.</p>"""
    mensaje = _crear_mensaje_micajaplus(
        destinatario,
        "Mi Caja+ | Código de recuperación",
        texto_plano,
        contenido_html
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as servidor_smtp:
            servidor_smtp.starttls()
            servidor_smtp.login(smtp_user, smtp_pass)
            servidor_smtp.send_message(mensaje)
        return True
    except Exception as err:
        print(f"[ERROR SMTP]: No se pudo enviar el codigo: {err}")
        return False


def _validar_credencial_contrasena(contrasena_ingresada: str, clave_bd: str, id_usuario: str) -> bool:
    # Compara contra el hash guardado; si la clave quedo en texto plano, la deja hasheada.
    if verificar_contrasena(contrasena_ingresada, clave_bd):
        return True

    es_braille = bool(clave_bd) and all(0x2800 <= ord(c) <= 0x28FF for c in clave_bd)
    if not es_braille:
        clave_bd_limpia = clave_bd.strip()
        contrasena_limpia = contrasena_ingresada.strip()
        if hmac.compare_digest(contrasena_limpia.encode("utf-8"), clave_bd_limpia.encode("utf-8")):
            try:
                nuevo_hash = generar_hash_contrasena(contrasena_ingresada)
                actualizar_contrasena(id_usuario, nuevo_hash, f"{id_usuario} (AUTO_MIGRACION_ASCON)")
            except Exception:
                pass
            return True

    return False


def autenticar_usuario_login(
    identificacion: str,
    contrasena: str,
    plataforma: str = "WEB"
) -> dict:
    if not (isinstance(identificacion, str) and isinstance(contrasena, str)):
        return {
            "ok": False,
            "codigo": 400,
            "error": "Formato de credenciales invalido."
        }

    ident_limpia = identificacion.strip()
    if not ident_limpia or not contrasena:
        return {
            "ok": False,
            "codigo": 400,
            "error": "Debe ingresar su Cedula/NIT y contrasena."
        }

    try:
        usuario_bd = obtener_por_identificacion(ident_limpia)
    except MySQLError as err:
        print(f"[ERROR MYSQL LOGIN]: {err}")
        return {
            "ok": False,
            "codigo": 500,
            "error": "No fue posible completar la solicitud. Inténtalo nuevamente."
        }

    if not usuario_bd:
        verificar_contrasena(contrasena, "")
        return {
            "ok": False,
            "codigo": 401,
            "error": "Documento o contraseña incorrectos.",
            "redirigir_a": "index.html"
        }

    clave_almacenada = str(usuario_bd.get("contrasena") or "")
    if not _validar_credencial_contrasena(contrasena, clave_almacenada, str(usuario_bd["id_usuario"])):
        return {
            "ok": False,
            "codigo": 401,
            "error": "Documento o contraseña incorrectos.",
            "redirigir_a": "index.html"
        }

    estado_actual = str(usuario_bd.get("estado") or "").strip().upper()
    if estado_actual != "ACTIVO":
        # Mismo mensaje que credenciales invalidas: no revela que la cuenta existe.
        return {
            "ok": False,
            "codigo": 401,
            "error": "Documento o contraseña incorrectos.",
            "redirigir_a": "index.html"
        }

    canal_plataforma = str(plataforma).strip().upper() if isinstance(plataforma, str) else "WEB"
    uso_token = "SESION_MOVIL" if canal_plataforma == "MOVIL" else "SESION_WEB"

    id_usr = str(usuario_bd["id_usuario"]).strip()
    rol_usr = str(usuario_bd["rol"]).strip().upper()

    token = generar_token_identidad(
        id_usuario=id_usr,
        rol=rol_usr,
        uso=uso_token
    )
    empresas_asignadas = listar_empresas_de_usuario(
        id_usuario=id_usr,
        solo_activas=True
    )

    return {
        "ok": True,
        "codigo": 200,
        "token": token,
        "plataforma": "MOVIL" if uso_token == "SESION_MOVIL" else "WEB",
        "usuario": {
            "id_usuario": id_usr,
            "nombre": usuario_bd["nombre"],
            "rol": rol_usr,
            "correo_electronico": usuario_bd["correo_electronico"]
        },
        "empresas": empresas_asignadas,
        "redirigir_a": "/empresas"
    }


def solicitar_recuperacion_clave(correo_electronico: str, url_base_cliente: str = "") -> dict:
    if not isinstance(correo_electronico, str):
        return {"ok": False, "codigo": 400, "error": "Correo electronico invalido."}

    correo_limpio = correo_electronico.strip().lower()
    if not correo_limpio:
        return {"ok": False, "codigo": 400, "error": "Debe ingresar su correo electronico."}

    try:
        usuario_bd = obtener_por_correo(correo_limpio)
    except MySQLError as err:
        print(f"[ERROR MYSQL RECUPERAR]: {err}")
        return {
            "ok": False,
            "codigo": 500,
            "error": "No fue posible completar la solicitud. Inténtalo nuevamente."
        }

    if not usuario_bd or str(usuario_bd.get("estado", "")).strip().upper() != "ACTIVO":
        return {"ok": True, "codigo": 200, "mensaje": RESPUESTA_ENLACE_ENVIADO}

    token_recuperacion = generar_token_identidad(
        id_usuario=str(usuario_bd["id_usuario"]).strip(),
        rol=str(usuario_bd["rol"]).strip().upper(),
        uso="RECUPERACION"
    )

    token_url_seguro = quote(token_recuperacion, safe=".")
    base_url = (url_base_cliente or os.getenv("FRONTEND_BASE_URL", "")).rstrip("/")
    if base_url:
        url_recuperacion = f"{base_url}/nuevacontra.html?token={token_url_seguro}"
    else:
        url_recuperacion = f"nuevacontra.html?token={token_url_seguro}"

    enviado = _enviar_correo_recuperacion(
        destinatario=usuario_bd["correo_electronico"],
        nombre=usuario_bd["nombre"],
        url_recuperacion=url_recuperacion
    )

    if not enviado:
        return {
            "ok": False,
            "codigo": 500,
            "error": "No fue posible completar la solicitud. Inténtalo nuevamente."
        }

    return {"ok": True, "codigo": 200, "mensaje": RESPUESTA_ENLACE_ENVIADO}


def solicitar_codigo_recuperacion(correo_electronico: str) -> dict:
    """Genera un codigo de 4 digitos, lo guarda y lo envia al correo del usuario."""
    if not isinstance(correo_electronico, str):
        return {"ok": False, "codigo": 400, "error": "Correo electronico invalido."}

    correo_limpio = correo_electronico.strip().lower()
    if not correo_limpio:
        return {"ok": False, "codigo": 400, "error": "Debe ingresar su correo electronico."}

    # Cooldown y limite se aplican siempre, exista o no la cuenta,
    # para que la respuesta no delate si el correo esta registrado.
    espera_cooldown = _reservar_reenvio(correo_limpio)
    if espera_cooldown > 0:
        return {
            "ok": False,
            "codigo": 429,
            "error": f"Espera {espera_cooldown} segundos antes de solicitar otro codigo.",
            "cooldown_segundos": espera_cooldown
        }

    espera_limite = _espera_limite_solicitudes(correo_limpio)
    if espera_limite > 0:
        return {
            "ok": False,
            "codigo": 429,
            "error": "Has realizado demasiadas solicitudes de recuperación. Inténtalo de nuevo más tarde.",
            "cooldown_segundos": espera_limite
        }

    try:
        usuario_bd = obtener_por_correo(correo_limpio)
    except MySQLError as err:
        print(f"[ERROR MYSQL CODIGO]: {err}")
        _liberar_reenvio(correo_limpio)
        return {
            "ok": False,
            "codigo": 500,
            "error": "No fue posible completar la solicitud. Inténtalo nuevamente."
        }

    cuenta_valida = bool(usuario_bd) and str(usuario_bd.get("estado", "")).strip().upper() == "ACTIVO"
    codigo = ""
    if cuenta_valida:
        id_usuario = str(usuario_bd["id_usuario"]).strip()
        codigo = f"{secrets.randbelow(10000):04d}"
        _guardar_codigo(correo_limpio, id_usuario, codigo)

    _registrar_solicitud(correo_limpio)

    if cuenta_valida:
        enviado = _enviar_correo_codigo(
            destinatario=str(usuario_bd["correo_electronico"]),
            nombre=str(usuario_bd["nombre"]),
            codigo=codigo
        )
        if not enviado:
            _descartar_codigo(correo_limpio)
            _liberar_reenvio(correo_limpio)
            return {
                "ok": False,
                "codigo": 500,
                "error": "No fue posible completar la solicitud. Inténtalo nuevamente."
            }

    return {
        "ok": True,
        "codigo": 200,
        "mensaje": RESPUESTA_CODIGO_ENVIADO,
        "cooldown_segundos": CODIGO_COOLDOWN_REENVIO_SEGUNDOS
    }


def verificar_codigo_recuperacion(correo_electronico: str, codigo: str) -> dict:
    """Valida el codigo de 4 digitos y entrega el token de recuperacion."""
    if not (isinstance(correo_electronico, str) and isinstance(codigo, str)):
        return {"ok": False, "codigo": 400, "error": "Parametros invalidos."}

    correo_limpio = correo_electronico.strip().lower()
    codigo_limpio = codigo.strip()

    if not correo_limpio:
        return {"ok": False, "codigo": 400, "error": "Debe indicar el correo electronico."}
    if len(codigo_limpio) != 4 or not codigo_limpio.isdigit():
        return {"ok": False, "codigo": 400, "error": "El codigo debe tener 4 digitos."}

    registro = _obtener_codigo(correo_limpio)
    if not registro:
        return {"ok": False, "codigo": 401, "error": RESPUESTA_CODIGO_INVALIDO}

    intentos = int(registro.get("intentos") or 0)
    if intentos >= CODIGO_MAX_INTENTOS:
        _descartar_codigo(correo_limpio)
        return {"ok": False, "codigo": 401, "error": RESPUESTA_CODIGO_INVALIDO}

    if not hmac.compare_digest(codigo_limpio, str(registro["codigo"])):
        _incrementar_intentos_memoria(correo_limpio)
        return {"ok": False, "codigo": 401, "error": RESPUESTA_CODIGO_INVALIDO}

    _descartar_codigo(correo_limpio)

    try:
        usuario_bd = obtener_por_id(str(registro["id_usuario"]))
    except MySQLError as err:
        print(f"[ERROR MYSQL VERIFICAR CODIGO]: {err}")
        return {
            "ok": False,
            "codigo": 500,
            "error": "No fue posible completar la solicitud. Inténtalo nuevamente."
        }

    if not usuario_bd or str(usuario_bd.get("estado", "")).strip().upper() != "ACTIVO":
        return {
            "ok": False,
            "codigo": 403,
            "error": "La cuenta no esta disponible para el cambio de contrasena."
        }

    token_recuperacion = generar_token_identidad(
        id_usuario=str(usuario_bd["id_usuario"]).strip(),
        rol=str(usuario_bd["rol"]).strip().upper(),
        uso="RECUPERACION"
    )

    return {
        "ok": True,
        "codigo": 200,
        "mensaje": "Codigo verificado correctamente.",
        "token_recuperacion": token_recuperacion
    }


def confirmar_nueva_clave(token_recuperacion: str, nueva_contrasena: str) -> dict:
    if not (isinstance(token_recuperacion, str) and isinstance(nueva_contrasena, str)):
        return {"ok": False, "codigo": 400, "error": "Parametros invalidos."}

    datos_token = verificar_token_identidad(
        token_recuperacion,
        uso_esperado="RECUPERACION"
    )
    if not datos_token:
        return {
            "ok": False,
            "codigo": 401,
            "error": "El enlace de recuperacion es invalido o ha expirado.",
            "redirigir_a": "recuperar.html"
        }

    id_usuario = datos_token["sub"]
    usuario_bd = obtener_por_id(id_usuario)
    if not usuario_bd or str(usuario_bd.get("estado", "")).strip().upper() != "ACTIVO":
        return {
            "ok": False,
            "codigo": 403,
            "error": "Cuenta no disponible para cambio de contrasena.",
            "redirigir_a": "index.html"
        }

    try:
        nuevo_hash = generar_hash_contrasena(nueva_contrasena)
    except ValueError as err:
        return {"ok": False, "codigo": 400, "error": str(err)}

    autor = f"{id_usuario} (RECUPERACION)"
    actualizado = actualizar_contrasena(id_usuario, nuevo_hash, autor)

    if not actualizado:
        return {"ok": False, "codigo": 500, "error": "No se pudo actualizar la contrasena."}

    return {
        "ok": True,
        "codigo": 200,
        "mensaje": "Contrasena actualizada exitosamente.",
        "redirigir_a": "index.html"
    }


def autorizar_peticion_agw(token: str, modulo_destino: str, nit_empresa: str = "") -> dict:
    verificacion = verificar_acceso_modulo(token, modulo_destino)
    if not verificacion["ok"]:
        return verificacion

    id_usuario = verificacion["id_usuario"]

    usuario_bd = obtener_por_id(id_usuario)
    if not usuario_bd or str(usuario_bd.get("estado", "")).strip().upper() != "ACTIVO":
        return {
            "ok": False,
            "codigo": 401,
            "error": "El usuario ya no se encuentra ACTIVO en el sistema.",
            "redirigir_a": "index.html"
        }

    nit_limpio = nit_empresa.strip() if isinstance(nit_empresa, str) else ""
    if nit_limpio:
        vinculo = verificar_vinculo_activo(id_usuario, nit_limpio)
        if not vinculo:
            return {
                "ok": False,
                "codigo": 403,
                "error": f"No tiene asignacion ACTIVA para operar en la empresa con NIT {nit_limpio}.",
                "redirigir_a": "/empresas"
            }
        verificacion["nit_autorizado"] = nit_limpio
        verificacion["rol_en_empresa"] = vinculo["rol"]

    verificacion["nombre"] = usuario_bd["nombre"]
    return verificacion
