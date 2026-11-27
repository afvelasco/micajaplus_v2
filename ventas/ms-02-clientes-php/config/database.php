<?php
declare(strict_types=1);

class Database {
    private static string $host = "127.0.0.1";
    private static string $dbName = "mi_caja_plus_adso09";
    private static string $username = "root";
    private static string $password = "";

    public static function getConnection(): PDO {
        $dsn = "mysql:host=" . self::$host . ";dbname=" . self::$dbName . ";charset=utf8mb4";
        return new PDO($dsn, self::$username, self::$password, [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES   => false
        ]);
    }
}


