from dataclasses import dataclass
from seguridad import ROLES_PERMITIDOS

ESTADOS_ASIGNACION = frozenset({"ACTIVO", "SUSPENDIDO"})


@dataclass(frozen=True)
class UsuarioEmpresaModelo:
    id_usuario_empresa: str
    id_usuario: str
    nit: str
    rol: str
    estado: str = "ACTIVO"

    def __post_init__(self):
        for nombre_campo, valor in self.__dict__.items():
            if not isinstance(valor, str):
                raise ValueError(f"El campo '{nombre_campo}' debe ser texto (str).")

        object.__setattr__(self, "id_usuario_empresa", self.id_usuario_empresa.strip())
        object.__setattr__(self, "id_usuario", self.id_usuario.strip())
        object.__setattr__(self, "nit", self.nit.strip())
        object.__setattr__(self, "rol", self.rol.strip().upper())
        object.__setattr__(self, "estado", self.estado.strip().upper())

        if not (1 <= len(self.id_usuario_empresa) <= 9):
            raise ValueError("id_usuario_empresa debe tener entre 1 y 9 caracteres.")

        if not (1 <= len(self.id_usuario) <= 9):
            raise ValueError("id_usuario debe tener entre 1 y 9 caracteres.")

        if not (1 <= len(self.nit) <= 11):
            raise ValueError("nit debe tener entre 1 y 11 caracteres.")

        if self.rol not in ROLES_PERMITIDOS:
            raise ValueError(f"Rol de empresa no permitido: {self.rol}")

        if self.estado not in ESTADOS_ASIGNACION:
            raise ValueError(f"Estado de asignacion no permitido: {self.estado}")