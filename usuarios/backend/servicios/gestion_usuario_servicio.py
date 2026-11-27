from mysql.connector import Error as MySQLError
from seguridad import generar_hash_contrasena, proteger_inicio_modulo
from modelos import UsuarioModelo, UsuarioEmpresaModelo
from repositorios import (
    obtener_por_id,
    obtener_por_correo,
    obtener_por_identificacion,
    crear_usuario,
    cambiar_estado_usuario,
    asignar_usuario_a_empresa,
    cambiar_estado_asignacion
)


@proteger_inicio_modulo("usuarios")
def registrar_nuevo_usuario(sesion: dict, datos: dict) -> dict:
    if not isinstance(datos, dict):
        return {"ok": False, "codigo": 400, "error": "Datos de usuario invalidos."}

    try:
        id_usuario = str(datos.get("id_usuario", "")).strip()
        correo = str(datos.get("correo_electronico", "")).strip().lower()
        identificacion = str(datos.get("identificacion", "")).strip()
        contrasena_plana = str(datos.get("contrasena", ""))

        if obtener_por_id(id_usuario):
            return {"ok": False, "codigo": 409, "error": f"El id_usuario '{id_usuario}' ya existe."}
        if obtener_por_correo(correo):
            return {"ok": False, "codigo": 409, "error": "El correo electronico ya esta registrado."}
        if obtener_por_identificacion(identificacion):
            return {"ok": False, "codigo": 409, "error": "La identificacion ya esta registrada."}

        hash_seguro = generar_hash_contrasena(contrasena_plana)

        nuevo_usuario = UsuarioModelo(
            id_usuario=id_usuario,
            nombre=str(datos.get("nombre", "")),
            identificacion=identificacion,
            correo_electronico=correo,
            contrasena=hash_seguro,
            rol=str(datos.get("rol", "")),
            estado="ACTIVO",
            autorizado_por=sesion["autorizado_por"]
        )

        crear_usuario(nuevo_usuario)
        return {
            "ok": True,
            "codigo": 201,
            "mensaje": "Usuario creado exitosamente.",
            "id_usuario": nuevo_usuario.id_usuario
        }

    except ValueError as err:
        return {"ok": False, "codigo": 400, "error": str(err)}
    except MySQLError:
        return {"ok": False, "codigo": 500, "error": "No fue posible completar la solicitud. Inténtalo nuevamente."}


@proteger_inicio_modulo("usuarios")
def vincular_usuario_empresa(sesion: dict, datos: dict) -> dict:
    if not isinstance(datos, dict):
        return {"ok": False, "codigo": 400, "error": "Datos de asignacion invalidos."}

    try:
        nueva_asignacion = UsuarioEmpresaModelo(
            id_usuario_empresa=str(datos.get("id_usuario_empresa", "")),
            id_usuario=str(datos.get("id_usuario", "")),
            nit=str(datos.get("nit", "")),
            rol=str(datos.get("rol", "")),
            estado="ACTIVO"
        )

        if not obtener_por_id(nueva_asignacion.id_usuario):
            return {"ok": False, "codigo": 404, "error": "El id_usuario que intenta vincular no existe."}

        asignar_usuario_a_empresa(nueva_asignacion)
        return {
            "ok": True,
            "codigo": 201,
            "mensaje": "Usuario vinculado a la empresa exitosamente.",
            "id_usuario_empresa": nueva_asignacion.id_usuario_empresa
        }

    except ValueError as err:
        return {"ok": False, "codigo": 400, "error": str(err)}
    except MySQLError:
        return {
            "ok": False,
            "codigo": 409,
            "error": "No fue posible completar la vinculacion. Verifica los datos e intentalo nuevamente."
        }


@proteger_inicio_modulo("usuarios")
def modificar_estado_usuario(sesion: dict, datos: dict) -> dict:
    if not isinstance(datos, dict):
        return {"ok": False, "codigo": 400, "error": "Datos invalidos."}

    try:
        id_objetivo = str(datos.get("id_usuario", "")).strip()
        nuevo_estado = str(datos.get("estado", "")).strip().upper()

        if id_objetivo == sesion["id_usuario"] and nuevo_estado == "INACTIVO":
            return {"ok": False, "codigo": 400, "error": "No puede desactivar su propia cuenta activa."}

        exito = cambiar_estado_usuario(id_objetivo, nuevo_estado, sesion["autorizado_por"])
        if not exito:
            return {"ok": False, "codigo": 404, "error": "Usuario no encontrado."}

        return {"ok": True, "codigo": 200, "mensaje": f"Estado actualizado a {nuevo_estado}."}
    except ValueError as err:
        return {"ok": False, "codigo": 400, "error": str(err)}


@proteger_inicio_modulo("usuarios")
def modificar_estado_vinculo_empresa(sesion: dict, datos: dict) -> dict:
    if not isinstance(datos, dict):
        return {"ok": False, "codigo": 400, "error": "Datos invalidos."}

    try:
        id_vinculo = str(datos.get("id_usuario_empresa", "")).strip()
        nuevo_estado = str(datos.get("estado", "")).strip().upper()

        exito = cambiar_estado_asignacion(id_vinculo, nuevo_estado)
        if not exito:
            return {"ok": False, "codigo": 404, "error": "Asignacion usuario-empresa no encontrada."}

        return {"ok": True, "codigo": 200, "mensaje": f"Asignacion actualizada a {nuevo_estado}."}
    except ValueError as err:
        return {"ok": False, "codigo": 400, "error": str(err)}