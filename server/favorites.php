<?php
// 찜 목록 화면. 대시보드와 같은 화면 파일을 쓰고, 화면이 주소(favorites.php)를 보고 찜 목록만 보여준다.
$APP = require __DIR__ . '/app_path.php';
require $APP . '/auth.php';
sa_require_page('main', '찜 목록');
header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store');
readfile($APP . '/view_index.html');
