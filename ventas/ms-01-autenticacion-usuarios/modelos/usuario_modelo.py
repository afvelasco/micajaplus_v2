import re
from dataclasses import dataclass
from seguridad import ROLES_PERMITIDOS

ESTADOS_USUARIO = frozenset({"ACTIVO", "INACTIVO"})
_REGEX_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class UsuarioModelo:
    id_usuario: str
    nombre: str
    identificacion: str
    correo_electronico: str
    contrasena: str
    rol: str
    estado: str = "ACTIVO"
    autorizado_por: str = "sistema"

    def __post_init__(self):
        for nombre_campo, valor in self.__dict__.items():
            if not isinstance(valor, str):
                raise ValueError(f"El campo '{nombre_campo}' debe ser texto (str).")

        object.__setattr__(self, "id_usuario", self.id_usuario.strip())
        object.__setattr__(self, "nombre", self.nombre.strip())
        object.__setattr__(self, "identificacion", self.identificacion.strip())
        object.__setattr__(self, "correo_electronico", self.correo_electronico.strip().lower())
        object.__setattr__(self, "rol", self.rol.strip().upper())
        object.__setattr__(self, "estado", self.estado.strip().upper())
        object.__setattr__(self, "autorizado_por", self.autorizado_por.strip())

        if not (6 <= len(self.id_usuario) <= 9):
            raise ValueError("id_usuario debe tener entre 6 y 9 caracteres.")

        if not (15 <= len(self.nombre) <= 346):
            raise ValueError("nombre debe tener entre 15 y 346 caracteres.")

        if not (6 <= len(self.identificacion) <= 15):
            raise ValueError("identificacion debe tener entre 6 y 15 caracteres.")

        if not (50 <= len(self.correo_electronico) <= 255) or not _REGEX_CORREO.match(self.correo_electronico):
            raise ValueError("correo_electronico invalido o excede 255 caracteres.")

        if not (20 <= len(self.contrasena) <= 500):
            raise ValueError("El hash de contrasena debe tener entre 20 y 500 caracteres.")

        if self.rol not in ROLES_PERMITIDOS:
            raise ValueError(f"Rol no permitido en la BD: {self.rol}")

        if self.estado not in ESTADOS_USUARIO:
            raise ValueError(f"Estado no permitido en la BD: {self.estado}")

        if not (1 <= len(self.autorizado_por) <= 346):
            raise ValueError("autorizado_por debe tener entre 1 y 346 caracteres.")