<?php
// 시군 지도 화면 + 지도용 데이터 (로그인(main)한 경우에만)
//   ?data=1            시군 지도 데이터 (sgg_map.json: 시군구 경계 + 조례 9.18 전후 비교)
//   ?cells=x,y;x,y…    필지 경계 (0.005도 칸 단위, 한 번에 16칸까지. 1,000필지 넘는 칸은 도심으로 보고 생략)
//   ?at=위도,경도       그 좌표의 필지 판정 (지목·계통·용도·이격·9.18 전후) — solar_lib.php
//   ?areas=x,y;…       리·동 경계 (0.05도 칸 단위, 한 번에 8칸까지)
//   ?zone=리·동코드      그 리(동) 안의 이격 가능 범위 격자 (9.18 전후)
$APP = require __DIR__ . '/app_path.php';
require $APP . '/auth.php';

function map_json($v, $code = 200) {
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($v, JSON_UNESCAPED_UNICODE);
    exit;
}

if (isset($_GET['data'])) {
    sa_require_api('main');
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: private, max-age=3600');
    if (extension_loaded('zlib')) ob_start('ob_gzhandler');
    $f = $APP . '/sgg_map.json';
    if (is_file($f)) { readfile($f); } else { http_response_code(404); echo '{}'; }
    exit;
}
if (isset($_GET['cells']) || isset($_GET['at']) || isset($_GET['areas']) || isset($_GET['zone'])) {
    sa_require_api('main');
    header('Cache-Control: no-store');
    if (extension_loaded('zlib')) ob_start('ob_gzhandler');  // 필지 경계가 커서 압축 (호스팅이 자동 압축 안 함)
    require $APP . '/solar_lib.php';
    @set_time_limit(150);
    try {
        if (isset($_GET['cells'])) {
            $out = array();
            foreach (array_slice(explode(';', (string)$_GET['cells']), 0, 16) as $c) {
                if (!preg_match('/^(\d{5}),(\d{4})$/', $c, $m)) continue;  // 한국 범위 칸 번호 (경도/0.005, 위도/0.005)
                $out[$c] = sl_cell(intval($m[1]), intval($m[2]));
            }
            map_json(array('ok' => true, 'cells' => $out));
        }
        if (isset($_GET['areas'])) {
            $out = array();
            foreach (array_slice(explode(';', (string)$_GET['areas']), 0, 8) as $c) {
                if (!preg_match('/^(2\d{3}),(\d{3})$/', $c, $m)) continue;  // 경도/0.05, 위도/0.05
                $out[$c] = sl_areas(intval($m[1]), intval($m[2]));
            }
            map_json(array('ok' => true, 'areas' => $out));
        }
        if (isset($_GET['zone'])) {
            if (!preg_match('/^\d{8}(\d{2})?$/', (string)$_GET['zone'])) map_json(array('ok' => false, 'error' => '코드 오류'), 400);
            map_json(sl_zone((string)$_GET['zone']));
        }
        if (!preg_match('/^(3[3-9]\.\d+),(12[4-9]\.\d+|13[0-2]\.\d+)$/', (string)$_GET['at'], $m)) map_json(array('ok' => false, 'error' => '좌표 오류'), 400);
        $pnu = sl_pnu_at(floatval($m[1]), floatval($m[2]));
        if (!$pnu) map_json(array('ok' => false, 'error' => '이 위치에 필지가 없습니다 (바다·하천 등)'));
        $hit = sl_cache_get('judge', $pnu, 86400);  // 같은 필지는 하루 동안 결과 재사용
        if ($hit) map_json($hit);
        $d = sl_research('', null, $pnu);
        if (!empty($d['ok'])) sl_cache_put('judge', $pnu, $d);
        map_json($d);
    } catch (Exception $e) {
        map_json(array('ok' => false, 'error' => '조회 오류: ' . $e->getMessage()), 502);
    }
}
sa_require_page('main', '시군 지도');
header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store');
readfile($APP . '/view_map.html');
