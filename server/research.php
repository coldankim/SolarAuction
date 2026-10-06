<?php
// 주소 조사 화면. 비밀번호(research)를 통과해야 보여준다. 조사 API는 research_api.php
$APP = require __DIR__ . '/app_path.php';
require $APP . '/auth.php';
sa_require_page('research', '주소 조사');
header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store');
readfile($APP . '/view_research.html');
