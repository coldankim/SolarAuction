<?php
// 대시보드 화면. 비밀번호(main)를 통과해야 보여준다. 화면 파일은 앱 폴더에 둔다.
$APP = require __DIR__ . '/app_path.php';
require $APP . '/auth.php';
sa_require_page('main', '대시보드');
header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store');
readfile($APP . '/view_index.html');
