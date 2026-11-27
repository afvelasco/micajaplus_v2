<?php
// ms-02-clientes-php/config/auth_agw.php

const URL_AGW_USUARIOS = "http://127.0.0.1:8001/agw/verificar-acceso";

function consultarAgwUsuarios(string $token, string $modulo, string $nit = ""): array {
    $cuerpo = json_encode([
        "modulo" => $modulo,
        "nit"    => $nit,
        "token"  => $token
    ], JSON_UNESCAPED_UNICODE);

    $ch = curl_init(URL_AGW_USUARIOS);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, "POST");
    curl_setopt($ch, CURLOPT_POSTFIELDS, $cuerpo);
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        "Content-Type: application/json; charset=utf-8",
        "Authorization: Bearer " . $token
    ]);
    curl_setopt($ch, CURLOPT_TIMEOUT, 5);

    $respuesta = curl_exec($ch);
    curl_close($ch);

    if ($respuesta === false) {
        return [
            "ok"          => false,
            "codigo"      => 503,
            "error"       => "El AGW de Usuarios (puerto 8000) no responde.",
            "redirigir_a" => "/index.html"
        ];
    }

    return json_decode($respuesta, true) ?? [
        "ok"     => false,
        "codigo" => 500,
        "error"  => "Respuesta inválida del AGW de Usuarios."
    ];
}

function exigirSesionAgw(string $nombreModulo = "ventas", string $nit = ""): array {
    $headers = function_exists('getallheaders') ? getallheaders() : [];
    $authHeader = $_SERVER['HTTP_AUTHORIZATION'] ?? $headers['Authorization'] ?? $headers['authorization'] ?? '';

    $token = '';
    if (preg_match('/Bearer\s+(\S+)/i', $authHeader, $matches)) {
        $token = urldecode(trim($matches[1]));
    }

    // Si no pasaron NIT explícito, verificar si viene en header o query param
    if ($nit === '') {
        $nit = trim($_SERVER['HTTP_X_NIT_EMPRESA'] ?? $_GET['nit_empresa'] ?? $_GET['nit'] ?? '');
    }

    $autorizacion = consultarAgwUsuarios($token, $nombreModulo, $nit);

    if (!($autorizacion['ok'] ?? false)) {
        http_response_code((int)($autorizacion['codigo'] ?? 401));
        echo json_encode($autorizacion, JSON_UNESCAPED_UNICODE);
        exit;
    }

    // Mapear las llaves exactas que retorna autorizar_peticion_agw() en servidor_agw.py
    $autorizacion['nit_empresa']    = $autorizacion['nit_autorizado'] ?? $nit;
    $autorizacion['autorizado_por'] = $autorizacion['nombre'] ?? $autorizacion['id_usuario'] ?? 'sistema';

    return $autorizacion;
}