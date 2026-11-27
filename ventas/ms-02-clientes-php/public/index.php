<?php
declare(strict_types=1);

// 1. Configuración de cabeceras CORS y respuesta JSON
header("Access-Control-Allow-Origin: *");
header("Content-Type: application/json; charset=UTF-8");
header("Access-Control-Allow-Methods: GET, POST, PUT, PATCH, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type, Authorization, X-Usuario-Id, X-Usuario-Rol, X-Nit-Empresa, X-Autorizado-Por");

// Responder de inmediato a las peticiones preflight (OPTIONS) de los navegadores
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

require_once __DIR__ . '/../config/database.php';
require_once __DIR__ . '/../config/auth_agw.php';
require_once __DIR__ . '/../controllers/ClienteController.php';

try {
    $db = Database::getConnection();
    $controller = new ClienteController($db);

    $metodo = $_SERVER['REQUEST_METHOD'];
    $uri = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);

    // Normalizar la ruta por si el proyecto corre dentro de una subcarpeta en XAMPP/Laragon
    if (($pos = strpos($uri, '/api/v1/clientes')) !== false) {
        $ruta = substr($uri, $pos);
    } else {
        $ruta = $uri;
    }
    $ruta = rtrim($ruta, '/');

    // Decodificar el cuerpo JSON cuando se envían datos por POST, PUT o PATCH
    $rawInput = file_get_contents('php://input');
    $body = json_decode($rawInput ?: '{}', true);
    if (!is_array($body)) {
        $body = [];
    }

    // Roles con permiso de creación y edición según el ENUM de la tabla `usuarios`
    $rolesEscritura = ['FACTURADOR', 'VENDEDORES', 'CONTADOR', 'AUXILIAR'];

    // =========================================================================
    // DEFINICIÓN DE ENDPOINTS DEL MICROSERVICIO MS-02 (ms-clientes)
    // =========================================================================

    // 1. Búsqueda exacta por documento: GET /api/v1/clientes/buscar?numero_identificacion=1115089123
    if ($metodo === 'GET' && $ruta === '/api/v1/clientes/buscar') {
        $documento = trim($_GET['numero_identificacion'] ?? $_GET['documento'] ?? '');
        if ($documento === '') {
            http_response_code(400);
            echo json_encode(["error" => "Debe proporcionar el parámetro 'numero_identificacion' en la URL."]);
            exit;
        }
        $controller->buscarPorDocumento($documento);
        exit;
    }

    // 2. Listado general paginado y filtrado: GET /api/v1/clientes
    if ($metodo === 'GET' && $ruta === '/api/v1/clientes') {
        $controller->listarClientes($_GET);
        exit;
    }

    // 3. Crear cliente nuevo: POST /api/v1/clientes (también soporta /api/v1/clientes/crear)
    if ($metodo === 'POST' && ($ruta === '/api/v1/clientes' || $ruta === '/api/v1/clientes/crear')) {
        $sesion = exigirSesionAgw("ventas");
        $controller->crearCliente($body, $sesion);
        exit;
    }

    // 4. Cambiar estado (ACTIVO / INACTIVO): PATCH /api/v1/clientes/{numero_identificacion}/estado
    if ($metodo === 'PATCH' && preg_match('#^/api/v1/clientes/([^/]+)/estado$#', $ruta, $matches)) {
        $sesion = exigirSesionAgw("ventas");
        $numeroIdentificacion = urldecode($matches[1]);
        $controller->cambiarEstadoCliente($numeroIdentificacion, $body, $sesion);
        exit;
    }

    // 5. Actualizar cliente: PUT /api/v1/clientes/{numero_identificacion} (soporta /actualizar opcional)
    if ($metodo === 'PUT' && preg_match('#^/api/v1/clientes/([^/]+)(?:/actualizar)?$#', $ruta, $matches)) {
        $sesion = exigirSesionAgw("ventas");
        $numeroIdentificacion = urldecode($matches[1]);
        $controller->actualizarCliente($numeroIdentificacion, $body, $sesion);
        exit;
    }

    // 6. Consultar cliente individual por llave primaria: GET /api/v1/clientes/{numero_identificacion}
    if ($metodo === 'GET' && preg_match('#^/api/v1/clientes/([^/]+)$#', $ruta, $matches)) {
        $numeroIdentificacion = urldecode($matches[1]);
        $controller->obtenerPorDocumento($numeroIdentificacion);
        exit;
    }

    // Respuesta 404 si la ruta no existe
    http_response_code(404);
    echo json_encode([
        "error" => "Ruta no encontrada en ms-clientes",
        "metodo" => $metodo,
        "ruta" => $ruta
    ]);

} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode([
        "error" => "Error en la base de datos mi_caja_plus_adso09",
        "detalle" => $e->getMessage()
    ]);
} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode([
        "error" => "Error interno del servidor en ms-clientes",
        "detalle" => $e->getMessage()
    ]);
}