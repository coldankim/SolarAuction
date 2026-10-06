<?php
// 조회 조건(settings.json) 읽기/저장. 페이지 로그인(main) 필요, 저장은 설정 비밀번호도 맞아야 한다.
// POST JSON: {"password": "...", "settings": {...}, "runNow": true}
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
$APP = require __DIR__ . '/app_path.php';
require $APP . '/auth.php';
sa_require_api('main');
$FILE = $APP . '/settings.json';
$KEYS = array('REGIONS', 'PVCT_TRGT_YN', 'DSPS_MTHOD_CD', 'PRPT_DIV_CD', 'SOLAR_MIN_KW', 'SOLAR_GOOD_KW');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    echo is_file($FILE) ? file_get_contents($FILE) : '{}'; exit;
}

$S = require $APP . '/web_secrets.php';
$in = json_decode(file_get_contents('php://input'), true);
$pw = is_array($in) && isset($in['password']) ? (string)$in['password'] : '';
if (!hash_equals($S['settings_hash'], hash('sha256', $S['settings_salt'] . $pw))) {
    sleep(1);  // 비밀번호 대입 속도 늦추기
    http_response_code(401); echo '{"error":"비밀번호가 틀렸습니다"}'; exit;
}
$st = isset($in['settings']) && is_array($in['settings']) ? $in['settings'] : null;
if (!$st) { http_response_code(400); echo '{"error":"설정 값이 없습니다"}'; exit; }

$out = array();
foreach ($KEYS as $k) {
    $v = isset($st[$k]) ? trim((string)$st[$k]) : '';
    if (strlen($v) > 1500) { http_response_code(400); echo '{"error":"값이 너무 깁니다"}'; exit; }
    $out[$k] = $v;
}
if ($out['REGIONS'] === '') { http_response_code(400); echo '{"error":"관심 지역을 입력하세요"}'; exit; }
if (!preg_match('/^\d+$/', $out['SOLAR_MIN_KW']) || !preg_match('/^\d+$/', $out['SOLAR_GOOD_KW'])) {
    http_response_code(400); echo '{"error":"계통 기준은 숫자로 입력하세요"}'; exit;
}
if (!in_array($out['PVCT_TRGT_YN'], array('N', 'Y', 'N,Y'), true)) $out['PVCT_TRGT_YN'] = 'N';
if (!in_array($out['DSPS_MTHOD_CD'], array('0001', '0002', ''), true)) $out['DSPS_MTHOD_CD'] = '0001';
if (!preg_match('/^(\d{4})?(,\d{4})*$/', $out['PRPT_DIV_CD'])) $out['PRPT_DIV_CD'] = '';

$tmp = $FILE . '.tmp';
file_put_contents($tmp, json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . "\n");
rename($tmp, $FILE);

$ran = false;
if (!empty($in['runNow'])) {
    exec('cd ' . escapeshellarg($APP) . ' && PYTHONIOENCODING=utf-8 LANG=ko_KR.UTF-8 python3 -u onbid_alert.py >> logs/stdout.log 2>&1 &');
    $ran = true;
}
echo json_encode(array('ok' => true, 'settings' => $out, 'started' => $ran), JSON_UNESCAPED_UNICODE);
