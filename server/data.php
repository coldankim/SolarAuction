<?php
// 대시보드 데이터 (파이썬이 앱 폴더에 쓴 dash_data.json). 로그인(main)한 경우에만.
$APP = require __DIR__ . '/app_path.php';
require $APP . '/auth.php';
sa_require_api('main');
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
$f = $APP . '/dash_data.json';
if (is_file($f)) { readfile($f); } else { http_response_code(404); echo '{}'; }
