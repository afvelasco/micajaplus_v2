from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import traceback
from pathlib import Path
from urllib.parse import unquote, urlparse
from mysql.connector import Error as MySQLError
from configuracion import iniciar_conexion_request, cerrar_conexion_request
from servicios import (
    autenticar_usuario_login,
    solicitar_recuperacion_clave,
    solicitar_codigo_recuperacion,
    verificar_codigo_recuperacion,
    confirmar_nueva_clave,
    autorizar_peticion_agw,
    registrar_nuevo_usuario,
    vincular_usuario_empresa,
    modificar_estado_usuario,
    modificar_estado_vinculo_empresa
)

# Carpeta del frontend, relativa a este archivo
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "model"

MAX_BYTES_PETICION = 8192


class ManejadorAGWUsuarios(BaseHTTPRequestHandler):

    def _enviar_cabeceras_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _responder_json(self, resultado: dict) -> None:
        copia_respuesta = dict(resultado)
        codigo_http = int(copia_respuesta.pop("codigo", 200))
        payload = json.dumps(copia_respuesta, default=str, ensure_ascii=False).encode("utf-8")

        self.send_response(codigo_http)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._enviar_cabeceras_cors()
        self.end_headers()
        self.wfile.write(payload)

    def _leer_cuerpo_json(self) -> dict | None:
        try:
            largo = int(self.headers.get("Content-Length", 0))
            if largo <= 0 or largo > MAX_BYTES_PETICION:
                return None
            crudo = self.rfile.read(largo).decode("utf-8")
            datos = json.loads(crudo)
            return datos if isinstance(datos, dict) else None
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _extraer_token_header(self, cuerpo: dict | None = None) -> str:
        if isinstance(cuerpo, dict) and isinstance(cuerpo.get("token"), str) and cuerpo["token"].strip():
            return unquote(cuerpo["token"].strip())

        cabecera = self.headers.get("Authorization", "")
        if not isinstance(cabecera, str):
            return ""
        limpio = cabecera.removeprefix("Bearer ").strip()
        try:
            limpio = limpio.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return unquote(limpio)

    def do_GET(self) -> None:
        ruta = urlparse(self.path).path.rstrip("/")
        if ruta.startswith("/agw/") or ruta.startswith("/usuarios/"):
            self._responder_json({
                "ok": False,
                "codigo": 405,
                "error": "Este endpoint solo acepta POST."
            })
            return

        ruta_relativa = urlparse(self.path).path.lstrip("/")
        if not ruta_relativa:
            ruta_relativa = "index.html"

        ruta_archivo = (_FRONTEND_DIR / ruta_relativa).resolve()

        try:
            ruta_archivo.relative_to(_FRONTEND_DIR)
        except ValueError:
            self.send_response(403)
            self.end_headers()
            return

        if not ruta_archivo.is_file():
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Archivo no encontrado.")
            return

        mime, _ = mimetypes.guess_type(str(ruta_archivo))
        mime = mime or "application/octet-stream"
        contenido = ruta_archivo.read_bytes()

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(contenido)))
        self._enviar_cabeceras_cors()
        self.end_headers()
        self.wfile.write(contenido)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._enviar_cabeceras_cors()
        self.end_headers()

    def do_POST(self) -> None:
        cuerpo = self._leer_cuerpo_json()
        if cuerpo is None:
            self._responder_json({
                "ok": False,
                "codigo": 400,
                "error": "Cuerpo JSON invalido, vacio o excede 8 KB."
            })
            return

        ruta = urlparse(self.path).path.rstrip("/")

        try:
            # abre la conexion a la BD para este request
            iniciar_conexion_request()

            # login - devuelve token WEB o MOVIL
            if ruta == "/agw/login":
                identificacion = str(cuerpo.get("identificacion") or cuerpo.get("credencial") or "")
                contrasena = str(cuerpo.get("contrasena", ""))
                plataforma = str(cuerpo.get("plataforma", "WEB"))
                resultado = autenticar_usuario_login(identificacion, contrasena, plataforma)
                self._responder_json(resultado)

            # recuperar - envia un enlace con token
            elif ruta == "/agw/recuperar":
                correo = str(cuerpo.get("correo_electronico", ""))
                url_base = str(cuerpo.get("url_base", ""))
                resultado = solicitar_recuperacion_clave(correo, url_base)
                self._responder_json(resultado)

            # enviar codigo de 4 digitos
            elif ruta == "/agw/enviar-codigo":
                correo = str(cuerpo.get("correo_electronico", ""))
                resultado = solicitar_codigo_recuperacion(correo)
                self._responder_json(resultado)

            # verificar el codigo
            elif ruta == "/agw/verificar-codigo":
                correo = str(cuerpo.get("correo_electronico", ""))
                codigo = str(cuerpo.get("codigo", ""))
                resultado = verificar_codigo_recuperacion(correo, codigo)
                self._responder_json(resultado)

            # cambiar la contrasena con el token de recuperacion
            elif ruta == "/agw/nueva-clave":
                token_rec = unquote(str(cuerpo.get("token_recuperacion", "")).strip())
                nueva_clave = str(cuerpo.get("nueva_contrasena", ""))
                resultado = confirmar_nueva_clave(token_rec, nueva_clave)
                self._responder_json(resultado)

            # verificar acceso a un modulo
            elif ruta == "/agw/verificar-acceso":
                token = self._extraer_token_header(cuerpo)
                modulo = str(cuerpo.get("modulo", ""))
                nit = str(cuerpo.get("nit", ""))
                resultado = autorizar_peticion_agw(token, modulo, nit)
                self._responder_json(resultado)

            # endpoints de administracion (solo CONTADOR)
            elif ruta == "/usuarios/crear":
                token = self._extraer_token_header(cuerpo)
                resultado = registrar_nuevo_usuario(token, datos=cuerpo)
                self._responder_json(resultado)

            elif ruta == "/usuarios/vincular-empresa":
                token = self._extraer_token_header(cuerpo)
                resultado = vincular_usuario_empresa(token, datos=cuerpo)
                self._responder_json(resultado)

            elif ruta == "/usuarios/estado":
                token = self._extraer_token_header(cuerpo)
                resultado = modificar_estado_usuario(token, datos=cuerpo)
                self._responder_json(resultado)

            elif ruta == "/usuarios/estado-vinculo":
                token = self._extraer_token_header(cuerpo)
                resultado = modificar_estado_vinculo_empresa(token, datos=cuerpo)
                self._responder_json(resultado)

            else:
                self._responder_json({
                    "ok": False,
                    "codigo": 404,
                    "error": "El recurso solicitado no existe."
                })

        except MySQLError:
            traceback.print_exc()
            self._responder_json({
                "ok": False,
                "codigo": 500,
                "error": "No fue posible completar la solicitud. Inténtalo nuevamente."
            })
        except Exception:
            traceback.print_exc()
            self._responder_json({
                "ok": False,
                "codigo": 500,
                "error": "No fue posible completar la solicitud. Inténtalo nuevamente."
            })
        finally:
            # cierra la conexion del request
            cerrar_conexion_request()


if __name__ == "__main__":
    host = "0.0.0.0"
    puerto = 25569
    servidor = ThreadingHTTPServer((host, puerto), ManejadorAGWUsuarios)
    print(f"Servidor AGW de Usuarios activo en http://{host}:{puerto}")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nApagando el Servidor AGW de forma segura...")
        servidor.server_close()