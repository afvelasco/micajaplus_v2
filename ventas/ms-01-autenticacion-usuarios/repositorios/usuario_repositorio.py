from configuracion import obtener_conexion
from modelos import UsuarioModelo, ESTADOS_USUARIO


def obtener_por_id(id_usuario: str) -> dict | None:
    if not isinstance(id_usuario, str):
        return None
    id_limpio = id_usuario.strip()
    if not (1 <= len(id_limpio) <= 9):
        return None

    sql = """
        SELECT id_usuario, nombre, identificacion, correo_electronico,
            contrasena, rol, estado, marca_tiempo, autorizado_por
        FROM usuarios
        WHERE id_usuario = %s
        LIMIT 1
    """
    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)
        cursor.execute(sql, (id_limpio,))
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        conexion.close()


def obtener_por_correo(correo_electronico: str) -> dict | None:
    if not isinstance(correo_electronico, str):
        return None
    correo_limpio = correo_electronico.strip().lower()
    if not (3 <= len(correo_limpio) <= 255):
        return None

    sql = """
        SELECT id_usuario, nombre, identificacion, correo_electronico,
            contrasena, rol, estado, marca_tiempo, autorizado_por
        FROM usuarios
        WHERE correo_electronico = %s
        LIMIT 1
    """
    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)
        cursor.execute(sql, (correo_limpio,))
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        conexion.close()


def obtener_por_identificacion(identificacion: str) -> dict | None:
    if not isinstance(identificacion, str):
        return None
    ident_limpia = identificacion.strip()
    if not (1 <= len(ident_limpia) <= 15):
        return None

    sql = """
        SELECT id_usuario, nombre, identificacion, correo_electronico,
            contrasena, rol, estado, marca_tiempo, autorizado_por
        FROM usuarios
        WHERE identificacion = %s
        LIMIT 1
    """
    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)
        cursor.execute(sql, (ident_limpia,))
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        conexion.close()



def crear_usuario(usuario: UsuarioModelo) -> bool:
    if not isinstance(usuario, UsuarioModelo):
        raise TypeError("Se requiere una instancia valida de UsuarioModelo.")

    sql = """
        INSERT INTO usuarios (
            id_usuario, nombre, identificacion, correo_electronico,
            contrasena, rol, estado, autorizado_por
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    parametros = (
        usuario.id_usuario,
        usuario.nombre,
        usuario.identificacion,
        usuario.correo_electronico,
        usuario.contrasena,
        usuario.rol,
        usuario.estado,
        usuario.autorizado_por
    )

    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor()
        cursor.execute(sql, parametros)
        conexion.commit()
        return cursor.rowcount == 1
    except Exception:
        conexion.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        conexion.close()


def actualizar_contrasena(id_usuario: str, nuevo_hash: str, autorizado_por: str) -> bool:
    if not (isinstance(id_usuario, str) and isinstance(nuevo_hash, str) and isinstance(autorizado_por, str)):
        raise ValueError("Todos los parametros deben ser texto (str).")

    id_limpio = id_usuario.strip()
    autor_limpio = autorizado_por.strip()

    if not (1 <= len(id_limpio) <= 9):
        raise ValueError("id_usuario invalido.")
    if not (1 <= len(nuevo_hash) <= 500):
        raise ValueError("El nuevo hash de contrasena no es valido.")
    if not (1 <= len(autor_limpio) <= 346):
        raise ValueError("autorizado_por invalido.")

    sql = """
        UPDATE usuarios
        SET contrasena = %s, autorizado_por = %s
        WHERE id_usuario = %s
    """
    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor()
        cursor.execute(sql, (nuevo_hash, autor_limpio, id_limpio))
        conexion.commit()
        return cursor.rowcount > 0
    except Exception:
        conexion.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        conexion.close()


def cambiar_estado_usuario(id_usuario: str, nuevo_estado: str, autorizado_por: str) -> bool:
    if not (isinstance(id_usuario, str) and isinstance(nuevo_estado, str) and isinstance(autorizado_por, str)):
        raise ValueError("Todos los parametros deben ser texto (str).")

    id_limpio = id_usuario.strip()
    estado_limpio = nuevo_estado.strip().upper()
    autor_limpio = autorizado_por.strip()

    if not (1 <= len(id_limpio) <= 9):
        raise ValueError("id_usuario invalido.")
    if estado_limpio not in ESTADOS_USUARIO:
        raise ValueError(f"Estado no permitido: {nuevo_estado}")
    if not (1 <= len(autor_limpio) <= 346):
        raise ValueError("autorizado_por invalido.")

    sql = """
        UPDATE usuarios
        SET estado = %s, autorizado_por = %s
        WHERE id_usuario = %s
    """
    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor()
        cursor.execute(sql, (estado_limpio, autor_limpio, id_limpio))
        conexion.commit()
        return cursor.rowcount > 0
    except Exception:
        conexion.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        conexion.close()