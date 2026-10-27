<?php
// 시군 지도 화면. ?data=1 이면 지도 데이터(sgg_map.json: 시군구 경계 + 조례 9.18 전후 비교)를 내보낸다.
$APP = require __DIR__ . '/app_path.php';
require $APP . '/auth.php';
if (isset($_GET['data'])) {
    sa_require_api('main');
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: private, max-age=3600');
    $f = $APP . '/sgg_map.json';
    if (is_file($f)) { readfile($f); } else { http_response_code(404); echo '{}'; }
    exit;
}
sa_require_page('main', '시군 지도');
header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store');
readfile($APP . '/view_map.html');
