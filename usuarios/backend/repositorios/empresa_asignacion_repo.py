from mysql.connector import Error as MySQLError
from configuracion import obtener_conexion
from modelos import UsuarioEmpresaModelo, ESTADOS_ASIGNACION


def asignar_usuario_a_empresa(asignacion: UsuarioEmpresaModelo) -> bool:
    if not isinstance(asignacion, UsuarioEmpresaModelo):
        raise TypeError("Se requiere una instancia valida de UsuarioEmpresaModelo.")

    sql = """
        INSERT INTO usuarios_empresas (
            id_usuario_empresa, id_usuario, nit, rol, estado
        ) VALUES (%s, %s, %s, %s, %s)
    """
    parametros = (
        asignacion.id_usuario_empresa,
        asignacion.id_usuario,
        asignacion.nit,
        asignacion.rol,
        asignacion.estado
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


def listar_empresas_de_usuario(id_usuario: str, solo_activas: bool = True) -> list[dict]:
    if not isinstance(id_usuario, str):
        return []
    id_limpio = id_usuario.strip()
    if not (1 <= len(id_limpio) <= 9):
        return []

    if solo_activas:
        sql = """
            SELECT ue.id_usuario_empresa,
                   ue.id_usuario,
                   ue.nit,
                   ue.rol AS rol_en_empresa,
                   ue.estado AS estado_asignacion,
                   ue.fecha_de_asignacion,
                   e.nombre_comercial,
                   e.razon_social
            FROM usuarios_empresas ue
            INNER JOIN empresas e ON ue.nit = e.nit
            WHERE ue.id_usuario = %s AND ue.estado = 'ACTIVO' AND e.estado = 'ACTIVO'
            ORDER BY e.nombre_comercial ASC
        """
    else:
        sql = """
            SELECT ue.id_usuario_empresa,
                   ue.id_usuario,
                   ue.nit,
                   ue.rol AS rol_en_empresa,
                   ue.estado AS estado_asignacion,
                   ue.fecha_de_asignacion,
                   e.nombre_comercial,
                   e.razon_social
            FROM usuarios_empresas ue
            INNER JOIN empresas e ON ue.nit = e.nit
            WHERE ue.id_usuario = %s
            ORDER BY e.nombre_comercial ASC
        """

    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)
        cursor.execute(sql, (id_limpio,))
        return cursor.fetchall()
    except MySQLError:
        # Fallback si la tabla empresas aun no esta cruzada, consulta directo en usuarios_empresas
        try:
            if cursor is not None:
                cursor.close()
            cursor = conexion.cursor(dictionary=True)
            sql_simple = """
                SELECT id_usuario_empresa,
                       id_usuario,
                       nit,
                       rol AS rol_en_empresa,
                       estado AS estado_asignacion
                FROM usuarios_empresas
                WHERE id_usuario = %s AND estado = 'ACTIVO'
            """
            cursor.execute(sql_simple, (id_limpio,))
            return cursor.fetchall()
        except MySQLError:
            return []
    finally:
        if cursor is not None:
            cursor.close()
        conexion.close()


def verificar_vinculo_activo(id_usuario: str, nit: str) -> dict | None:
    if not (isinstance(id_usuario, str) and isinstance(nit, str)):
        return None
    id_limpio = id_usuario.strip()
    nit_limpio = nit.strip()
    if not (1 <= len(id_limpio) <= 9) or not (1 <= len(nit_limpio) <= 11):
        return None

    sql = """
        SELECT id_usuario_empresa, id_usuario, nit, rol, estado
        FROM usuarios_empresas
        WHERE id_usuario = %s AND nit = %s AND estado = 'ACTIVO'
        LIMIT 1
    """
    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)
        cursor.execute(sql, (id_limpio, nit_limpio))
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        conexion.close()


def cambiar_estado_asignacion(id_usuario_empresa: str, nuevo_estado: str) -> bool:
    if not (isinstance(id_usuario_empresa, str) and isinstance(nuevo_estado, str)):
        raise ValueError("Los parametros deben ser texto (str).")

    id_limpio = id_usuario_empresa.strip()
    estado_limpio = nuevo_estado.strip().upper()

    if not (1 <= len(id_limpio) <= 9):
        raise ValueError("id_usuario_empresa invalido.")
    if estado_limpio not in ESTADOS_ASIGNACION:
        raise ValueError(f"Estado de asignacion no permitido: {nuevo_estado}")

    sql = """
        UPDATE usuarios_empresas
        SET estado = %s
        WHERE id_usuario_empresa = %s
    """
    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor()
        cursor.execute(sql, (estado_limpio, id_limpio))
        conexion.commit()
        return cursor.rowcount > 0
    except Exception:
        conexion.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        conexion.close()