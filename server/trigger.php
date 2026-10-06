<?php
// 실행 신호: GitHub Actions(또는 사람이)가 부르면 서버의 파이썬 작업을 백그라운드로 시작한다.
// 토큰이 맞을 때만 동작. 호스팅에 cron이 없어서 이 방식을 쓴다.
//   ?token=...&mode=run|init|test|dry   → 백그라운드 실행
//   ?token=...&mode=check               → 파이썬·모듈 점검 결과 바로 반환
//   ?token=...&mode=log                 → 최근 실행 로그
header('Content-Type: application/json; charset=utf-8');
$APP = require __DIR__ . '/app_path.php';
$S = require $APP . '/web_secrets.php';

$token = isset($_REQUEST['token']) ? (string)$_REQUEST['token'] : '';
if (!hash_equals($S['trigger_token'], $token)) {
    http_response_code(403); echo '{"error":"forbidden"}'; exit;
}
$mode = isset($_REQUEST['mode']) ? (string)$_REQUEST['mode'] : 'run';
$ARGS = array('run' => '', 'init' => '--init', 'test' => '--test-kakao', 'dry' => '--dry-run');
$py = 'cd ' . escapeshellarg($APP) . ' && PYTHONIOENCODING=utf-8 LANG=ko_KR.UTF-8 python3 -u onbid_alert.py';

if ($mode === 'check') {
    $out = shell_exec('cd ' . escapeshellarg($APP) . ' && python3 -V 2>&1 && PYTHONIOENCODING=utf-8 python3 -c "import onbid_alert, kepco, landuse, setback, courtauction; print(\'modules ok\')" 2>&1');
    echo json_encode(array('ok' => true, 'out' => $out), JSON_UNESCAPED_UNICODE); exit;
}
if ($mode === 'log') {
    $f = $APP . '/onbid_alert.log';
    $lines = is_file($f) ? array_slice(file($f), -80) : array();
    echo json_encode(array('ok' => true, 'log' => implode('', $lines)), JSON_UNESCAPED_UNICODE); exit;
}
if (!isset($ARGS[$mode])) {
    http_response_code(400); echo '{"error":"bad mode"}'; exit;
}
exec($py . ' ' . $ARGS[$mode] . ' >> logs/stdout.log 2>&1 &');
echo json_encode(array('ok' => true, 'started' => $mode, 'at' => date('c')));
