<?php
// research 페이지용: 주소 → 태양광 적합성 조사 (서버의 research.py 실행)
//   GET q=주소 [&pnu=후보 필지번호]
// 같은 주소는 하루 동안 결과 재사용, IP당 분당 6회 제한 (공개 페이지라 API 남용 방지)
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
@set_time_limit(120);
$APP = __DIR__ . '/_app';
$q = isset($_GET['q']) ? trim((string)$_GET['q']) : '';
$pnu = isset($_GET['pnu']) ? preg_replace('/\D/', '', (string)$_GET['pnu']) : '';
if ($q === '' || strlen($q) > 300) { http_response_code(400); echo '{"ok":false,"error":"주소를 입력하세요"}'; exit; }

$cdir = $APP . '/research_cache';
if (!is_dir($cdir)) { @mkdir($cdir, 0755, true); }
$key = md5($q . '|' . $pnu);
$cf = $cdir . '/' . $key . '.php';  // .php + 첫 줄 exit: 웹으로 열어도 내용이 보이지 않게
if (is_file($cf) && time() - filemtime($cf) < 86400) {
    echo substr(file_get_contents($cf), strlen("<?php exit; ?>\n")); exit;
}

// 분당 6회 제한
$ip = preg_replace('/[^0-9a-fA-F:.]/', '', $_SERVER['REMOTE_ADDR']);
$rf = $cdir . '/rate_' . md5($ip) . '.php';
$now = time();
$hits = array();
if (is_file($rf)) {
    $hits = array_filter(explode(',', substr(file_get_contents($rf), strlen("<?php exit; ?>\n"))), function ($t) use ($now) { return $t && $now - intval($t) < 60; });
}
if (count($hits) >= 6) { http_response_code(429); echo '{"ok":false,"error":"잠시 후 다시 시도하세요 (분당 6회)"}'; exit; }
$hits[] = $now;
file_put_contents($rf, "<?php exit; ?>\n" . implode(',', $hits));

$cmd = 'cd ' . escapeshellarg($APP) . ' && PYTHONIOENCODING=utf-8 LANG=ko_KR.UTF-8 timeout 100 python3 research.py '
     . escapeshellarg($q) . ($pnu ? ' ' . escapeshellarg($pnu) : '') . ' 2>/dev/null';
$out = shell_exec($cmd);
$json = $out ? trim($out) : '';
$d = json_decode($json, true);
if (!is_array($d)) { http_response_code(502); echo '{"ok":false,"error":"조사 중 오류가 발생했습니다. 잠시 후 다시 시도하세요."}'; exit; }
if (!empty($d['ok'])) { file_put_contents($cf, "<?php exit; ?>\n" . $json); }
echo $json;
