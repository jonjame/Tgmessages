<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');

require_once __DIR__ . '/../vendor/autoload.php';

use App\Database;

$pdo = Database::get();

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    $stmt = $pdo->query("SELECT id, phone, name, status, created_at FROM accounts ORDER BY id DESC");
    $accounts = $stmt->fetchAll(PDO::FETCH_ASSOC);
    echo json_encode(['accounts' => $accounts]);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'DELETE') {
    $id = (int) ($_GET['id'] ?? 0);
    if ($id) {
        $pdo->prepare("DELETE FROM accounts WHERE id = ?")->execute([$id]);
    }
    echo json_encode(['ok' => true]);
    exit;
}

http_response_code(405);
echo json_encode(['error' => 'Method not allowed']);
