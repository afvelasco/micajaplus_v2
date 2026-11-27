<?php
declare(strict_types=1);

class ClienteController {
    private PDO $db;

    // Valores exactos permitidos por los ENUM de la tabla `clientes` en mi_caja_plus_adso09
    private array $tiposDocValidos = ['NIT', 'CC', 'CE', 'PEP', 'PAS'];
    private array $regimenesValidos = ['ORDINARIO', 'SIMPLE_RST', 'RTE_ESAL'];
    private array $estadosValidos = ['ACTIVO', 'INACTIVO'];

    public function __construct(PDO $db) {
        $this->db = $db;
    }

    /**
     * 1. Búsqueda inmediata por documento para el Punto de Venta
     * Endpoint: GET /api/v1/clientes/buscar?numero_identificacion={documento}
     */
    public function buscarPorDocumento(string $documento): void {
        $stmt = $this->db->prepare("
            SELECT 
                tipo_identificacion, numero_identificacion, nombre_cliente, 
                razon_social_sociedades, regimen_fiscal_tributario, pais, 
                departamento, ciudad, direccion, telefono_cliente, email, 
                representante_legal, telefono_representante_legal, estado,
                marca_tiempo, autorizado_por
            FROM clientes
            WHERE numero_identificacion = ?
            LIMIT 1
        ");
        $stmt->execute([$documento]);
        $cliente = $stmt->fetch();

        if ($cliente) {
            http_response_code(200);
            echo json_encode([
                "existe" => true,
                "requiere_creacion" => false,
                "cliente" => $cliente
            ]);
        } else {
            http_response_code(404);
            echo json_encode([
                "existe" => false,
                "requiere_creacion" => true,
                "numero_identificacion_buscado" => $documento,
                "mensaje" => "El cliente no se encuentra registrado. Debe crearse antes de registrar la venta."
            ]);
        }
    }

    /**
     * 2. Listado general paginado y filtrado multicriterio para la vista de Clientes
     * Endpoint: GET /api/v1/clientes?pagina=1&limite=20&estado=ACTIVO&termino=texto
     */
    public function listarClientes(array $query): void {
        $pagina = max(1, (int)($query['pagina'] ?? 1));
        $limite = max(1, min(100, (int)($query['limite'] ?? 20)));
        $offset = ($pagina - 1) * $limite;

        $estado = strtoupper(trim($query['estado'] ?? 'ACTIVO'));
        if (!in_array($estado, $this->estadosValidos, true)) {
            $estado = 'ACTIVO';
        }

        $terminoLimpio = trim($query['termino'] ?? '');
        $terminoLike = "%" . $terminoLimpio . "%";

        // Contar total de registros para que el Frontend arme el paginador
        $stmtCount = $this->db->prepare("
            SELECT COUNT(*) AS total
            FROM clientes
            WHERE estado = ?
            AND (numero_identificacion LIKE ? OR nombre_cliente LIKE ? OR email LIKE ? OR ciudad LIKE ?)
        ");
        $stmtCount->execute([$estado, $terminoLike, $terminoLike, $terminoLike, $terminoLike]);
        $totalRegistros = (int)$stmtCount->fetch()['total'];

        // Obtener los registros de la página solicitada
        $stmt = $this->db->prepare("
            SELECT 
                tipo_identificacion, numero_identificacion, nombre_cliente, 
                razon_social_sociedades, regimen_fiscal_tributario, pais,
                departamento, ciudad, direccion, telefono_cliente, email, 
                representante_legal, telefono_representante_legal,
                estado, marca_tiempo, autorizado_por
            FROM clientes
            WHERE estado = ?
            AND (numero_identificacion LIKE ? OR nombre_cliente LIKE ? OR email LIKE ? OR ciudad LIKE ?)
            ORDER BY marca_tiempo DESC
            LIMIT ? OFFSET ?
        ");
        $stmt->bindValue(1, $estado, PDO::PARAM_STR);
        $stmt->bindValue(2, $terminoLike, PDO::PARAM_STR);
        $stmt->bindValue(3, $terminoLike, PDO::PARAM_STR);
        $stmt->bindValue(4, $terminoLike, PDO::PARAM_STR);
        $stmt->bindValue(5, $terminoLike, PDO::PARAM_STR);
        $stmt->bindValue(6, $limite, PDO::PARAM_INT);
        $stmt->bindValue(7, $offset, PDO::PARAM_INT);
        $stmt->execute();

        http_response_code(200);
        echo json_encode([
            "pagina_actual" => $pagina,
            "limite_por_pagina" => $limite,
            "total_registros" => $totalRegistros,
            "total_paginas" => (int)ceil($totalRegistros / $limite),
            "datos" => $stmt->fetchAll()
        ]);
    }

    /**
     * 3. Obtener ficha técnica completa de un cliente por su llave primaria
     * Endpoint: GET /api/v1/clientes/{numero_identificacion}
     */
    public function obtenerPorDocumento(string $documento): void {
        $stmt = $this->db->prepare("
            SELECT 
                tipo_identificacion, numero_identificacion, nombre_cliente, 
                razon_social_sociedades, regimen_fiscal_tributario, pais, 
                departamento, ciudad, direccion, telefono_cliente, email, 
                representante_legal, telefono_representante_legal, estado,
                marca_tiempo, autorizado_por
            FROM clientes
            WHERE numero_identificacion = ?
            LIMIT 1
        ");
        $stmt->execute([$documento]);
        $cliente = $stmt->fetch();

        if (!$cliente) {
            http_response_code(404);
            echo json_encode(["error" => "No existe un cliente con el documento '$documento'."]);
            return;
        }

        http_response_code(200);
        echo json_encode($cliente);
    }

    /**
     * 4. Crear un nuevo cliente
     * Endpoint: POST /api/v1/clientes
     */
    public function crearCliente(array $data, array $sesion): void {
        $errorValidacion = $this->validarDatosCliente($data, true);
        if ($errorValidacion !== null) {
            http_response_code(400);
            echo json_encode(["error" => $errorValidacion]);
            return;
        }

        $numDoc = substr(trim((string)$data['numero_identificacion']), 0, 15);

        // Verificar si ya existe para dar un mensaje claro antes de lanzar excepción SQL
        $stmtExiste = $this->db->prepare("SELECT numero_identificacion FROM clientes WHERE numero_identificacion = ? LIMIT 1");
        $stmtExiste->execute([$numDoc]);
        if ($stmtExiste->fetch()) {
            http_response_code(409);
            echo json_encode([
                "error" => "Cliente duplicado",
                "mensaje" => "Ya existe un cliente registrado con el número de identificación '$numDoc'."
            ]);
            return;
        }

        $stmt = $this->db->prepare("
            INSERT INTO clientes (
                tipo_identificacion, numero_identificacion, nombre_cliente, 
                razon_social_sociedades, regimen_fiscal_tributario, pais, 
                departamento, ciudad, direccion, telefono_cliente, email, 
                representante_legal, telefono_representante_legal, estado, autorizado_por
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVO', ?)
        ");

        $nombre = trim($data['nombre_cliente']);
        $telefono = substr(trim((string)$data['telefono_cliente']), 0, 10);

        $stmt->execute([
            strtoupper(trim($data['tipo_identificacion'])),
            $numDoc,
            $nombre,
            trim($data['razon_social_sociedades'] ?? $nombre),
            strtoupper(trim($data['regimen_fiscal_tributario'])),
            trim($data['pais'] ?? 'Colombia'),
            trim($data['departamento']),
            trim($data['ciudad']),
            trim($data['direccion']),
            $telefono,
            trim($data['email']),
            trim($data['representante_legal'] ?? $nombre),
            substr(trim((string)($data['telefono_representante_legal'] ?? $telefono)), 0, 10),
            $sesion['autorizado_por']
        ]);

        http_response_code(201);
        echo json_encode([
            "mensaje" => "Cliente creado exitosamente",
            "numero_identificacion" => $numDoc,
            "autorizado_por" => $sesion['autorizado_por']
        ]);
    }

    /**
     * 5. Actualizar los datos de un cliente existente
     * Endpoint: PUT /api/v1/clientes/{numero_identificacion}
     */
    public function actualizarCliente(string $documento, array $data, array $sesion): void {
        $errorValidacion = $this->validarDatosCliente($data, false);
        if ($errorValidacion !== null) {
            http_response_code(400);
            echo json_encode(["error" => $errorValidacion]);
            return;
        }

        $stmtExiste = $this->db->prepare("SELECT numero_identificacion FROM clientes WHERE numero_identificacion = ? LIMIT 1");
        $stmtExiste->execute([$documento]);
        if (!$stmtExiste->fetch()) {
            http_response_code(404);
            echo json_encode(["error" => "No se encontró el cliente con documento '$documento' para actualizar."]);
            return;
        }

        $nombre = trim($data['nombre_cliente']);
        $telefono = substr(trim((string)$data['telefono_cliente']), 0, 10);

        $stmt = $this->db->prepare("
            UPDATE clientes
            SET 
                tipo_identificacion = ?,
                nombre_cliente = ?,
                razon_social_sociedades = ?,
                regimen_fiscal_tributario = ?,
                pais = ?,
                departamento = ?,
                ciudad = ?,
                direccion = ?,
                telefono_cliente = ?,
                email = ?,
                representante_legal = ?,
                telefono_representante_legal = ?,
                autorizado_por = ?
            WHERE numero_identificacion = ?
        ");

        $stmt->execute([
            strtoupper(trim($data['tipo_identificacion'])),
            $nombre,
            trim($data['razon_social_sociedades'] ?? $nombre),
            strtoupper(trim($data['regimen_fiscal_tributario'])),
            trim($data['pais'] ?? 'Colombia'),
            trim($data['departamento']),
            trim($data['ciudad']),
            trim($data['direccion']),
            $telefono,
            trim($data['email']),
            trim($data['representante_legal'] ?? $nombre),
            substr(trim((string)($data['telefono_representante_legal'] ?? $telefono)), 0, 10),
            $sesion['autorizado_por'],
            $documento
        ]);

        http_response_code(200);
        echo json_encode([
            "mensaje" => "Cliente actualizado correctamente",
            "numero_identificacion" => $documento,
            "autorizado_por" => $sesion['autorizado_por']
        ]);
    }

    /**
     * 6. Activar o Inactivar un cliente (Borrado lógico)
     * Endpoint: PATCH /api/v1/clientes/{numero_identificacion}/estado
     */
    public function cambiarEstadoCliente(string $documento, array $data, array $sesion): void {
        $nuevoEstado = strtoupper(trim($data['estado'] ?? ''));
        if (!in_array($nuevoEstado, $this->estadosValidos, true)) {
            http_response_code(400);
            echo json_encode(["error" => "El campo 'estado' es obligatorio y debe ser 'ACTIVO' o 'INACTIVO'."]);
            return;
        }

        $stmt = $this->db->prepare("
            UPDATE clientes
            SET estado = ?, autorizado_por = ?
            WHERE numero_identificacion = ?
        ");
        $stmt->execute([$nuevoEstado, $sesion['autorizado_por'], $documento]);

        http_response_code(200);
        echo json_encode([
            "mensaje" => "Estado del cliente actualizado exitosamente",
            "numero_identificacion" => $documento,
            "estado" => $nuevoEstado,
            "autorizado_por" => $sesion['autorizado_por']
        ]);
    }

    /**
     * Validador interno de campos obligatorios y listas ENUM de MySQL
     */
    private function validarDatosCliente(array $data, bool $validarDocumento): ?string {
        $camposObligatorios = [
            'tipo_identificacion', 'nombre_cliente', 'regimen_fiscal_tributario',
            'departamento', 'ciudad', 'direccion', 'telefono_cliente', 'email'
        ];
        if ($validarDocumento) {
            $camposObligatorios[] = 'numero_identificacion';
        }

        foreach ($camposObligatorios as $campo) {
            if (!isset($data[$campo]) || trim((string)$data[$campo]) === '') {
                return "El campo '$campo' es obligatorio.";
            }
        }

        $tipoDoc = strtoupper(trim($data['tipo_identificacion']));
        if (!in_array($tipoDoc, $this->tiposDocValidos, true)) {
            return "Valor inválido para 'tipo_identificacion'. Permitidos: " . implode(', ', $this->tiposDocValidos);
        }

        $regimen = strtoupper(trim($data['regimen_fiscal_tributario']));
        if (!in_array($regimen, $this->regimenesValidos, true)) {
            return "Valor inválido para 'regimen_fiscal_tributario'. Permitidos: " . implode(', ', $this->regimenesValidos);
        }

        if (!filter_var(trim($data['email']), FILTER_VALIDATE_EMAIL)) {
            return "El formato del correo electrónico ('email') no es válido.";
        }

        return null;
    }
}