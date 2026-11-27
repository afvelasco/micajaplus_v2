import hmac
import os
import smtplib
from email.message import EmailMessage
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


def _enviar_correo_recuperacion(destinatario: str, nombre: str, url_recuperacion: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()

    print("\n" + "=" * 70)
    print(f"[RECUPERACION DE CLAVE] Correo verificado: {destinatario}")
    print(f"[LINK A NUEVACONTRA]: {url_recuperacion}")
    print("=" * 70 + "\n")

    if not smtp_user or not smtp_pass:
        return True

    mensaje = EmailMessage()
    mensaje["Subject"] = "Mi Caja+ | Restablecer contraseña"
    mensaje["From"] = smtp_user
    mensaje["To"] = destinatario
    mensaje.set_content(
        f"Hola {nombre},\n\n"
        f"Has solicitado restablecer tu contraseña en Mi Caja+.\n"
        f"Haz clic en el siguiente enlace (válido por 15 minutos) para crear tu nueva contraseña:\n\n"
        f"{url_recuperacion}\n\n"
        f"Si no solicitaste este cambio, ignora este mensaje."
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


def _validar_credencial_contrasena(contrasena_ingresada: str, clave_bd: str, id_usuario: str) -> bool:
    """
    Verifica primero con el motor NIST SP 800-232 (Ascon + Braille-256).
    Si en la BD remota el usuario fue insertado manualmente en texto plano desde phpMyAdmin,
    lo valida en tiempo constante y migra automaticamente su hash a Braille-256.
    """
    print(f"[DEBUG] clave_bd len={len(clave_bd)}")
    print(f"[DEBUG] clave_bd es braille: {bool(clave_bd) and all(0x2800 <= ord(c) <= 0x28FF for c in clave_bd)}")
    print(f"[DEBUG] clave_bd primeros 60 bytes repr: {repr(clave_bd[:60])}")

    if verificar_contrasena(contrasena_ingresada, clave_bd):
        print("[DEBUG] verificar_contrasena => True (Braille-256 OK)")
        return True

    # Si la clave almacenada no esta en rango Braille-256 (ej. texto plano de prueba en BD remota)
    es_braille = bool(clave_bd) and all(0x2800 <= ord(c) <= 0x28FF for c in clave_bd)
    print(f"[DEBUG] intentando comparacion texto plano, es_braille={es_braille}")
    if not es_braille:
        # Comparar ignorando espacios laterales
        clave_bd_limpia = clave_bd.strip()
        contrasena_limpia = contrasena_ingresada.strip()
        if hmac.compare_digest(contrasena_limpia.encode("utf-8"), clave_bd_limpia.encode("utf-8")):
            print("[DEBUG] texto plano coincide => migrando hash")
            try:
                nuevo_hash = generar_hash_contrasena(contrasena_ingresada)
                actualizar_contrasena(id_usuario, nuevo_hash, f"{id_usuario} (AUTO_MIGRACION_ASCON)")
                print("[DEBUG] migracion exitosa")
            except Exception as e:
                print(f"[DEBUG] error en migracion: {e}")
            return True

    print("[DEBUG] _validar_credencial_contrasena => False")
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
            "error": f"Error al consultar usuario en la BD ({err.errno}): {err.msg}"
        }

    if not usuario_bd:
        verificar_contrasena(contrasena, "")
        return {
            "ok": False,
            "codigo": 401,
            "error": "Documento o contrasena incorrectos.",
            "redirigir_a": "index.html"
        }

    clave_almacenada = str(usuario_bd.get("contrasena") or "")
    if not _validar_credencial_contrasena(contrasena, clave_almacenada, str(usuario_bd["id_usuario"])):
        return {
            "ok": False,
            "codigo": 401,
            "error": "Documento o contrasena incorrectos.",
            "redirigir_a": "index.html"
        }

    estado_actual = str(usuario_bd.get("estado") or "").strip().upper()
    if estado_actual != "ACTIVO":
        return {
            "ok": False,
            "codigo": 403,
            "error": "Su cuenta de usuario se encuentra INACTIVA. Contacte al Contador.",
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
            "error": f"Error al consultar el correo en la BD ({err.errno}): {err.msg}"
        }

    if not usuario_bd or str(usuario_bd.get("estado", "")).strip().upper() != "ACTIVO":
        return {
            "ok": False,
            "codigo": 404,
            "error": "El correo ingresado no se encuentra registrado o la cuenta esta inactiva."
        }

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
            "error": "Error al enviar el correo de recuperacion."
        }

    return {
        "ok": True,
        "codigo": 200,
        "mensaje": "Se ha enviado un enlace a tu correo electronico para restablecer la contrasena.",
        "url_recuperacion": url_recuperacion
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