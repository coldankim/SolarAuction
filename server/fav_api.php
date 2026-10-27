<?php
// 찜 목록 (여러 기기에서 같이 보이도록 서버에 저장). 로그인(main) 필요.
//   GET                      → {"ok":true,"items":{"물건번호":{"at":"...","memo":"...","item":{찜할 때의 물건 정보}}}}
//   POST {"action":"add","item":{...}} / {"action":"remove","no":"..."} / {"action":"memo","no":"...","memo":"..."}
// 찜할 때 물건 정보를 같이 저장해 두어, 입찰이 끝나 목록에서 사라져도 찜 목록에서는 볼 수 있다.
$APP = require __DIR__ . '/app_path.php';
require $APP . '/auth.php';
sa_require_api('main');
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
$FILE = $APP . '/favorites.json';
date_default_timezone_set('Asia/Seoul');

function fav_out($favs) {
    echo json_encode(array('ok' => true, 'items' => (object)$favs), JSON_UNESCAPED_UNICODE); exit;
}
function fav_err($code, $msg) {
    http_response_code($code); echo json_encode(array('ok' => false, 'error' => $msg), JSON_UNESCAPED_UNICODE); exit;
}

$fp = fopen($FILE, 'c+');
if (!$fp) { fav_err(500, '찜 목록 파일을 열 수 없습니다'); }
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    flock($fp, LOCK_SH);
    $favs = json_decode(stream_get_contents($fp), true);
    flock($fp, LOCK_UN); fclose($fp);
    fav_out(is_array($favs) ? $favs : array());
}

$raw = file_get_contents('php://input');
if (strlen($raw) > 30000) { fav_err(400, '값이 너무 큽니다'); }
$in = json_decode($raw, true);
$act = is_array($in) && isset($in['action']) ? (string)$in['action'] : '';
flock($fp, LOCK_EX);
$favs = json_decode(stream_get_contents($fp), true);
if (!is_array($favs)) { $favs = array(); }

if ($act === 'add') {
    $it = isset($in['item']) && is_array($in['item']) ? $in['item'] : null;
    $no = $it && isset($it['no']) ? (string)$it['no'] : '';
    if ($no === '' || strlen($no) > 60) { fav_err(400, '물건 번호가 없습니다'); }
    if (!isset($favs[$no]) && count($favs) >= 500) { fav_err(400, '찜은 500건까지 가능합니다'); }
    $favs[$no] = array(
        'at' => isset($favs[$no]['at']) ? $favs[$no]['at'] : date('Y-m-d H:i'),
        'memo' => isset($favs[$no]['memo']) ? $favs[$no]['memo'] : '',
        'item' => $it,
    );
} elseif ($act === 'remove' || $act === 'memo') {
    $no = isset($in['no']) ? (string)$in['no'] : '';
    if (!isset($favs[$no])) { fav_err(404, '찜 목록에 없는 물건입니다'); }
    if ($act === 'remove') { unset($favs[$no]); }
    else { $favs[$no]['memo'] = mb_substr(isset($in['memo']) ? trim((string)$in['memo']) : '', 0, 500, 'UTF-8'); }
} else {
    fav_err(400, '알 수 없는 요청입니다');
}
ftruncate($fp, 0); rewind($fp);
fwrite($fp, json_encode((object)$favs, JSON_UNESCAPED_UNICODE));
fflush($fp); flock($fp, LOCK_UN); fclose($fp);
fav_out($favs);
