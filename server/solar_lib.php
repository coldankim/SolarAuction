<?php
// 태양광 판정 엔진 (PHP판). 호스팅이 파이썬 실행을 막아(2026.10.22~) research.py·kepco.py·landuse.py·setback.py 를 옮겼다.
// 결과 형식은 research.py 와 같아서 research 화면이 그대로 쓴다. 지도(map.php)의 필지 경계·필지 판정도 여기서 한다.
// 파이썬 쪽 규칙(지목·용도 판정 목록, 조례 찾기, 이격 판정)을 바꾸면 여기도 같이 고쳐야 한다.
// 앱 폴더에 두고 require 한다 (웹으로 직접 열 수 없음).

class SolarErr extends Exception {}

define('SL_DATA_URL', 'https://api.vworld.kr/req/data');
define('SL_SEARCH_URL', 'https://api.vworld.kr/req/search');
define('SL_LANDUSE_URL', 'https://api.vworld.kr/ned/data/getLandUseAttr');
define('SL_KEPCO', 'https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do');
define('SL_MAX_M', 200);      // 시행령 상한 (주거지)
define('SL_MIN_HOUSES', 5);
define('SL_LINK_M', 70);      // 주택 중심 간 이 거리 이내면 같은 주거지
define('SL_MAX_BLDG', 3000);  // 주변 건물이 이보다 많으면 도심으로 보고 이격을 재지 않음

// ---------------------------------------------------------------- 설정·캐시

function sl_app() { static $a; if (!$a) { $a = require __DIR__ . '/../app_path.php'; } return $a; }

function sl_read_env($path) {
    $out = array();
    if (!is_file($path)) return $out;
    foreach (preg_split('/\r?\n/', file_get_contents($path)) as $ln) {
        $ln = trim($ln);
        if ($ln === '' || $ln[0] === '#' || strpos($ln, '<?php') === 0 || strpos($ln, '=') === false) continue;
        list($k, $v) = explode('=', $ln, 2);
        $out[trim($k)] = trim($v);
    }
    return $out;
}

function sl_env() {
    static $e;
    if ($e) return $e;
    $app = sl_app();
    $e = sl_read_env($app . '/config.env');
    if (is_file($app . '/settings.json')) {
        $s = json_decode(file_get_contents($app . '/settings.json'), true);
        if (is_array($s)) foreach ($s as $k => $v) if ($v !== null && !is_array($v)) $e[$k] = (string)$v;
    }
    $e = array_merge($e, sl_read_env($app . '/.env.php'));
    return $e;
}

// 파일 캐시: 앱 폴더/pcache/<종류>/<md5>.php (첫 줄 exit 로 웹에서 내용이 안 보이게)
function sl_cache_get($kind, $key, $ttl) {
    $f = sl_app() . "/pcache/$kind/" . md5($key) . '.php';
    if (!is_file($f) || time() - filemtime($f) > $ttl) return null;
    $v = json_decode(substr(file_get_contents($f), 15), true);
    return $v === null ? null : $v;
}
function sl_cache_put($kind, $key, $val) {
    $d = sl_app() . "/pcache/$kind";
    if (!is_dir($d)) @mkdir($d, 0755, true);
    file_put_contents("$d/" . md5($key) . '.php', "<?php exit; ?>\n" . json_encode($val, JSON_UNESCAPED_UNICODE), LOCK_EX);
}

// ---------------------------------------------------------------- HTTP

function sl_http($url, $params, $accept, $waits) {
    $u = $url . '?' . http_build_query($params);
    $last = '';
    foreach ($waits as $w) {
        if ($w) sleep($w);
        $c = curl_init($u);
        curl_setopt_array($c, array(CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 25, CURLOPT_CONNECTTIMEOUT => 10,
            CURLOPT_HTTPHEADER => array('Accept: ' . $accept), CURLOPT_USERAGENT => 'SolarAuction/1.0'));
        $body = curl_exec($c);
        $code = curl_getinfo($c, CURLINFO_HTTP_CODE);
        $err = curl_error($c);
        curl_close($c);
        if ($body === false || $code == 0) { $last = $err; continue; }
        if (in_array($code, array(401, 429, 502, 503, 504))) { $last = "HTTP $code"; continue; }
        return array($code, $body);
    }
    throw new SolarErr("접속 실패: $last");
}

function sl_vw($url, $params) {
    $e = sl_env();
    $params['key'] = $e['VWORLD_API_KEY'];
    $params['domain'] = isset($e['VWORLD_DOMAIN']) ? $e['VWORLD_DOMAIN'] : 'coldankim.github.io';
    list($code, $body) = sl_http($url, $params, 'application/json', array(0, 2, 5));
    if ($code != 200) throw new SolarErr("브이월드 오류 HTTP $code");
    $d = json_decode($body, true);
    if (!is_array($d)) throw new SolarErr('브이월드 응답 해석 실패');
    return $d;
}

// 브이월드 2D 데이터 (GetFeature). 반환: [features, 전체 건수]
function sl_features($params, $pages = 1) {
    $feats = array();
    $total = 0;
    for ($page = 1; $page <= $pages; $page++) {
        $d = sl_vw(SL_DATA_URL, array_merge(array('service' => 'data', 'request' => 'GetFeature', 'format' => 'json',
            'errorformat' => 'json', 'crs' => 'EPSG:4326', 'size' => 1000, 'page' => $page), $params));
        $r = isset($d['response']) ? $d['response'] : array();
        $st = isset($r['status']) ? $r['status'] : '';
        if ($st === 'NOT_FOUND') break;
        if ($st !== 'OK') throw new SolarErr('브이월드 데이터 오류: ' . $st);
        $total = isset($r['record']['total']) ? intval($r['record']['total']) : 0;
        $got = isset($r['result']['featureCollection']['features']) ? $r['result']['featureCollection']['features'] : array();
        $feats = array_merge($feats, $got);
        if (count($got) < 1000) break;
    }
    return array($feats, $total);
}

// ---------------------------------------------------------------- 필지·주소

$GLOBALS['SL_JIMOK'] = array('전' => '전', '답' => '답', '과' => '과수원', '목' => '목장용지', '임' => '임야', '광' => '광천지',
    '염' => '염전', '대' => '대지', '장' => '공장용지', '학' => '학교용지', '차' => '주차장', '주' => '주유소용지', '창' => '창고용지',
    '도' => '도로', '철' => '철도용지', '제' => '제방', '천' => '하천', '구' => '구거', '유' => '유지', '양' => '양어장',
    '수' => '수도용지', '공' => '공원', '체' => '체육용지', '원' => '유원지', '종' => '종교용지', '사' => '사적지',
    '묘' => '묘지', '잡' => '잡종지');
$GLOBALS['SL_GOOD_JIMOK'] = array('전', '답', '과수원', '목장용지', '대지', '공장용지', '창고용지', '잡종지', '염전');

function sl_jimok($jibun) {
    $last = mb_substr($jibun, -1, 1, 'UTF-8');
    if ($last === '' || ctype_digit($last)) return '-';
    return isset($GLOBALS['SL_JIMOK'][$last]) ? $GLOBALS['SL_JIMOK'][$last] : $last;
}

// 필지 경계·속성 (30일 캐시). 반환: {pnu, addr, jibun, jimok, jiga, jigaYear, rings:[[[lon,lat]..]..]} 또는 null
function sl_parcel($pnu) {
    $hit = sl_cache_get('parcel', $pnu, 30 * 86400);
    if ($hit !== null) return $hit ?: null;
    list($fs) = sl_features(array('data' => 'LP_PA_CBND_BUBUN', 'attrFilter' => "pnu:=:$pnu", 'geometry' => 'true', 'attribute' => 'true'));
    $res = false;
    if ($fs) {
        $p = $fs[0]['properties'];
        $rings = array();
        foreach ($fs as $f) $rings = array_merge($rings, sl_rings(isset($f['geometry']) ? $f['geometry'] : null));
        $jibun = (string)(isset($p['jibun']) ? $p['jibun'] : '');
        $res = array('pnu' => $pnu, 'addr' => isset($p['addr']) ? $p['addr'] : '', 'jibun' => $jibun, 'jimok' => sl_jimok($jibun),
            'jiga' => isset($p['jiga']) ? $p['jiga'] : null, 'jigaYear' => isset($p['gosi_year']) ? $p['gosi_year'] : null, 'rings' => $rings);
    }
    sl_cache_put('parcel', $pnu, $res);
    return $res ?: null;
}

// 좌표의 필지 PNU
function sl_pnu_at($lat, $lon) {
    list($fs) = sl_features(array('data' => 'LP_PA_CBND_BUBUN', 'geomFilter' => sprintf('POINT(%.7f %.7f)', $lon, $lat),
        'geometry' => 'false', 'attribute' => 'true', 'size' => 1));
    return $fs ? $fs[0]['properties']['pnu'] : null;
}

// 지도 칸(0.005도, 약 500m) 하나의 필지 경계. 1,000필지가 넘으면 도심으로 보고 그리지 않는다.
function sl_cell($cx, $cy) {
    $key = "$cx,$cy";
    $hit = sl_cache_get('cell5', $key, 30 * 86400);
    if ($hit !== null) return $hit;
    $x0 = $cx * 0.005; $y0 = $cy * 0.005;
    list($fs, $total) = sl_features(array('data' => 'LP_PA_CBND_BUBUN', 'geometry' => 'true', 'attribute' => 'true',
        'geomFilter' => sprintf('BOX(%.4f,%.4f,%.4f,%.4f)', $x0, $y0, $x0 + 0.005, $y0 + 0.005)));
    if ($total > 1000) {
        $res = array('dense' => true, 'n' => $total);
    } else {
        $out = array();
        foreach ($fs as $f) {
            $rings = array();
            foreach (sl_rings(isset($f['geometry']) ? $f['geometry'] : null) as $r) {
                $rings[] = array_map(function ($p) { return array(round($p[0], 5), round($p[1], 5)); }, $r);  // 약 1m
            }
            $p = $f['properties'];
            $out[] = array('p' => $p['pnu'], 'j' => isset($p['jibun']) ? $p['jibun'] : '', 'r' => $rings);
        }
        $res = array('dense' => false, 'f' => $out);
    }
    sl_cache_put('cell5', $key, $res);
    return $res;
}

function sl_search($q) {
    $out = array();
    foreach (array('parcel', 'road') as $cat) {
        $d = sl_vw(SL_SEARCH_URL, array('service' => 'search', 'request' => 'search', 'version' => '2.0', 'size' => 6, 'page' => 1,
            'query' => $q, 'type' => 'address', 'category' => $cat, 'format' => 'json', 'errorformat' => 'json', 'crs' => 'EPSG:4326'));
        $r = isset($d['response']) ? $d['response'] : array();
        $items = (isset($r['status']) && $r['status'] === 'OK' && isset($r['result']['items'])) ? $r['result']['items'] : array();
        foreach ($items as $it) {
            $a = isset($it['address']) ? $it['address'] : array();
            $pt = isset($it['point']) ? $it['point'] : array();
            $out[] = array('pnu' => $cat === 'parcel' ? $it['id'] : null,
                'addr' => !empty($a['parcel']) ? $a['parcel'] : (isset($a['road']) ? $a['road'] : ''),
                'road' => isset($a['road']) ? $a['road'] : null, 'x' => isset($pt['x']) ? $pt['x'] : null,
                'y' => isset($pt['y']) ? $pt['y'] : null, 'cat' => $cat);
        }
        if ($out) break;
    }
    return $out;
}

// '경기도 화성시 만세구 송산면 고포리 74-40' → [시도, 시군구, 읍면동, 리, 지번(산이면 null)]
function sl_split_addr($addr) {
    $t = preg_split('/\s+/u', trim($addr));
    $sido = $t ? array_shift($t) : '';
    $sgg = array();
    while ($t && preg_match('/(시|군|구)$/u', $t[0]) && !preg_match('/(읍|면|동|가|리)$/u', $t[0])) $sgg[] = array_shift($t);
    $emd = null; $li = null; $jibun = null;
    if (preg_match('/(\S+[읍면동가])\s+(?:(\S+리)\s+)?(산?\s?\d+(?:-\d+)?)/u', $addr, $m)) {
        $emd = $m[1]; $li = isset($m[2]) && $m[2] !== '' ? $m[2] : null;
        $jibun = str_replace(' ', '', $m[3]);
        if (mb_substr($jibun, 0, 1, 'UTF-8') === '산') $jibun = null;  // 산65와 65는 다른 필지
    }
    return array($sido, implode(' ', $sgg), $emd, $li, $jibun);
}

// ---------------------------------------------------------------- 한전 계통

function sl_kw($v) { return intval(floatval(str_replace(',', '', (string)$v))); }

function sl_kepco_query($metro, $city, $emd, $li, $jibun) {
    $key = implode('|', array($metro, $city, $emd, $li, $jibun));
    $hit = sl_cache_get('kepco', $key, 20 * 3600);
    if ($hit !== null) return $hit;
    static $last = 0;
    $gap = 1.0 - (microtime(true) - $last);  // 한전은 연속 호출이 빠르면 끊는다
    if ($gap > 0) usleep(intval($gap * 1e6));
    $last = microtime(true);
    $e = sl_env();
    $p = array('metroCd' => $metro, 'cityCd' => $city, 'addrLidong' => $emd, 'apiKey' => $e['KEPCO_API_KEY'], 'returnType' => 'json');
    if ($li) $p['addrLi'] = $li;
    if ($jibun) $p['addrJibun'] = $jibun;
    list($code, $body) = sl_http(SL_KEPCO, $p, '*/*', array(0, 3, 8));
    if ($code == 404) { $rows = array(); }
    elseif ($code != 200) throw new SolarErr("한전 API 오류 $code");
    else {
        $d = json_decode($body, true);
        if (!is_array($d)) throw new SolarErr('한전 API 응답 해석 실패');
        $rows = isset($d['data']) && $d['data'] ? $d['data'] : array();
    }
    sl_cache_put('kepco', $key, $rows);
    return $rows;
}

// 지번 → 리 → 읍면동 순서로 넓혀 가며 조회. 반환: [level, rows]
function sl_kepco_lookup($metro, $city, $emd, $li, $jibun) {
    $cities = array($city);
    if ($city[2] !== '0') $cities[] = substr($city, 0, 2) . '0';  // 구로 나뉜 시는 옛 시 코드도
    $emds = array($emd);
    if (preg_match('/읍$/u', $emd)) $emds[] = mb_substr($emd, 0, -1, 'UTF-8') . '면';
    $steps = array();
    if ($jibun) $steps[] = array('jibun', $li, $jibun);
    if ($li) $steps[] = array('li', $li, null);
    $steps[] = array('emd', null, null);
    foreach ($steps as $s) foreach ($cities as $c) foreach ($emds as $e) {
        $rows = sl_kepco_query($metro, $c, $e, $s[1], $s[2]);
        if ($rows) return array($s[0], $rows);
    }
    return array(null, array());
}

function sl_kepco_summary($level, $rows, $min, $good) {
    if (!$rows) return array('grade' => 'none', 'label' => '계통 조회불가');
    $lines = array();
    foreach ($rows as $r) {
        $v = array(sl_kw(@$r['vol1']), sl_kw(@$r['vol2']), sl_kw(@$r['vol3']));
        $lines[] = array('avail' => min($v), 'subst' => @$r['substNm'], 'dl' => @$r['dlNm'], 'vol' => $v);
    }
    usort($lines, function ($a, $b) { return $b['avail'] - $a['avail']; });
    $best = $lines[0]; $worst = $lines[count($lines) - 1];
    $ok = count(array_filter($lines, function ($x) use ($min) { return $x['avail'] >= $min; }));
    if ($best['avail'] >= $good) { $g = 'good'; $l = "계통 {$good}kW+"; }
    elseif ($best['avail'] >= $min) { $g = 'ok'; $l = "계통 {$min}kW+"; }
    else { $g = 'fail'; $l = '계통 부족'; }
    $names = array('jibun' => '지번', 'li' => '리 단위', 'emd' => '읍면동 단위');
    return array('grade' => $g, 'label' => $l, 'level' => $level, 'levelName' => isset($names[$level]) ? $names[$level] : '',
        'best' => $best['avail'], 'worst' => $worst['avail'], 'lines' => count($lines), 'okLines' => $ok,
        'subst' => $best['subst'], 'dl' => $best['dl'], 'vol' => $best['vol'], 'lineList' => array_slice($lines, 0, 8));
}

// ---------------------------------------------------------------- 용도지역 (landuse.py 와 같은 목록)

$GLOBALS['SL_EXCLUDE'] = array('개발제한구역', '자연환경보전지역', '공익용산지', '농업진흥구역', '상수원보호구역', '문화유산구역', '문화재구역',
    '문화유산보호구역', '문화재보호구역', '습지보호지역', '생태ㆍ경관보전지역', '생태·경관보전지역', '야생생물보호구역', '백두대간보호지역');
$GLOBALS['SL_COND'] = array('임업용산지', '농업보호구역', '보전관리지역', '보전녹지지역', '생산녹지지역', '자연녹지지역', '농림지역', '비행안전',
    '군사시설보호', '통제보호구역', '제한보호구역', '개발행위허가제한지역', '역사문화환경보존지역', '경관지구', '수변구역', '주거지역',
    '상업지역', '공업지역', '도시개발구역', '택지개발지구', '공공주택지구', '하천구역', '소하천구역', '토석채취제한지역', '특별대책지역');
$GLOBALS['SL_FAV'] = array('계획관리지역', '생산관리지역');

function sl_zones($pnu) {
    $hit = sl_cache_get('zones', $pnu, 7 * 86400);
    if ($hit !== null) return $hit;
    $d = sl_vw(SL_LANDUSE_URL, array('pnu' => $pnu, 'format' => 'json', 'numOfRows' => 100, 'pageNo' => 1));
    $body = isset($d['landUses']) ? $d['landUses'] : (isset($d['response']) ? $d['response'] : array());
    $code = isset($body['resultCode']) ? $body['resultCode'] : '';
    if ($code && !in_array($code, array('OK', 'NO_DATA'))) throw new SolarErr("브이월드 오류: $code");
    $fields = isset($body['field']) ? $body['field'] : array();
    if (isset($fields['prposAreaDstrcCodeNm'])) $fields = array($fields);
    $zones = array();
    foreach ($fields as $f) $zones[] = array('name' => @$f['prposAreaDstrcCodeNm'], 'code' => @$f['prposAreaDstrcCode'], 'rel' => @$f['cnflcAtNm']);
    sl_cache_put('zones', $pnu, $zones);
    return $zones;
}

function sl_has_any($name, $list) { foreach ($list as $k) if (strpos($name, $k) !== false) return true; return false; }

function sl_screen($zones) {
    if (!$zones) return array('grade' => 'none', 'label' => '용도 조회불가', 'zones' => array());
    $names = array();
    foreach ($zones as $z) if (@$z['rel'] !== '접함' && !empty($z['name'])) $names[$z['name']] = 1;
    $names = array_keys($names);
    $excl = array_values(array_filter($names, function ($n) { return sl_has_any($n, $GLOBALS['SL_EXCLUDE']); }));
    $cond = array_values(array_filter($names, function ($n) use ($excl) { return !in_array($n, $excl) && sl_has_any($n, $GLOBALS['SL_COND']); }));
    $fav = array_values(array_intersect($names, $GLOBALS['SL_FAV']));
    if ($excl) { $g = 'exclude'; $l = '1차 제외'; } elseif ($cond) { $g = 'cond'; $l = '조건부'; } else { $g = 'pass'; $l = '1차 통과'; }
    return array('grade' => $g, 'label' => $l, 'reasons' => $excl ? $excl : $cond, 'favorable' => $fav, 'zones' => $names);
}

// ---------------------------------------------------------------- 이격 (setback.py 와 같은 규칙)

function sl_rules() { static $r; if ($r === null) { $r = json_decode(file_get_contents(sl_app() . '/setback_rules.json'), true) ?: array(); } return $r; }

$GLOBALS['SL_SIDO_ALIAS'] = array('전라남도' => '전남광주통합특별시', '광주광역시' => '전남광주통합특별시', '강원도' => '강원특별자치도', '전라북도' => '전북특별자치도');

function sl_find_rule($sido, $sgg) {
    $rules = sl_rules();
    $alias = isset($GLOBALS['SL_SIDO_ALIAS'][$sido]) ? $GLOBALS['SL_SIDO_ALIAS'][$sido] : null;
    $t = explode(' ', (string)$sgg);
    $sg = $t[0];
    foreach (array($sido, $alias) as $sd) if ($sd && $sg && isset($rules["$sd $sg"])) return array("$sd $sg", $rules["$sd $sg"]);
    if ($sg && !preg_match('/구$/u', $sg)) {  // 시군 이름이 전국에서 하나뿐이면 (자치구는 다른 광역시와 섞이므로 제외)
        $hits = array();
        foreach ($rules as $k => $v) { $p = explode(' ', $k); if (count($p) > 1 && end($p) === $sg) $hits[] = $k; }
        if (count($hits) == 1) return array($hits[0], $rules[$hits[0]]);
    }
    $sds = array($sido, $alias);
    if (($alias ?: $sido) === '전남광주통합특별시') $sds[] = ($sido === '광주광역시' || preg_match('/구$/u', $sg)) ? '(구)광주광역시' : '(구)전라남도';
    foreach ($sds as $sd) if ($sd && isset($rules[$sd])) return array($sd, $rules[$sd]);
    return array(trim("$sido $sg"), null);
}

function sl_tiers_str($tiers) { return implode(' / ', array_map(function ($t) { return ($t['houses'] ?: 1) . '호↑ ' . $t['m'] . 'm'; }, $tiers)); }

function sl_rule_for($sido, $sgg) {
    list($key, $r) = sl_find_rule($sido, $sgg);
    if (!$r || $r['tiers'] === null) {
        return array('limit' => SL_MAX_M, 'houses' => SL_MIN_HOUSES, 'tiers' => array(array('houses' => SL_MIN_HOUSES, 'm' => SL_MAX_M)),
            'basis' => '조례 미확인 → 시행령 상한(5호 이상 200m) 적용', 'key' => $key);
    }
    $eff = array();
    foreach ($r['tiers'] as $t) {
        $h = max(intval($t['houses'] ?: 1), SL_MIN_HOUSES);
        $eff[$h] = max(isset($eff[$h]) ? $eff[$h] : 0, min(intval($t['m']), SL_MAX_M));
    }
    ksort($eff);
    $tiers = array();
    foreach ($eff as $h => $_) {
        $m = 0;
        foreach ($eff as $hh => $v) if ($hh <= $h) $m = max($m, $v);
        if (!$tiers || $m > $tiers[count($tiers) - 1]['m']) $tiers[] = array('houses' => $h, 'm' => $m);
    }
    if (!$tiers) return array('limit' => 0, 'houses' => null, 'tiers' => array(), 'basis' => $r['status'] ?: '조례에 주거 이격 기준 없음', 'key' => $key);
    $raw = sl_tiers_str($r['tiers']); $capped = sl_tiers_str($tiers);
    return array('limit' => max(array_column($tiers, 'm')), 'houses' => min(array_column($tiers, 'houses')), 'tiers' => $tiers,
        'basis' => "조례 $raw" . ($raw === $capped ? '' : " → 시행령 적용 $capped"), 'key' => $key);
}

function sl_rule_before($sido, $sgg) {
    list($key, $r) = sl_find_rule($sido, $sgg);
    if (!$r || $r['tiers'] === null) return null;
    $tiers = array_map(function ($t) { return array('houses' => max(intval($t['houses'] ?: 1), 1), 'm' => intval($t['m'])); }, $r['tiers']);
    $roads = isset($r['roads']) ? $r['roads'] : array();
    $rs = implode(', ', array_map(function ($x) { return $x['m'] ? "{$x['kind']} {$x['m']}m" : $x['kind']; }, $roads));
    return array('limit' => $tiers ? max(array_column($tiers, 'm')) : 0, 'houses' => $tiers ? min(array_column($tiers, 'houses')) : null,
        'tiers' => $tiers, 'roads' => $roads, 'key' => $key,
        'basis' => '9.18 이전 조례 원문: ' . (sl_tiers_str($tiers) ?: '주거 이격 없음') . ($roads ? " · 도로 $rs" : ''),
        'uncertain' => !empty($r['amendedAfterDecree']));
}

function sl_ord_summary($sido, $sgg) {
    list($key, $r) = sl_find_rule($sido, $sgg);
    $r = $r ?: array();
    $ch = isset($r['change']) ? $r['change'] : array();
    return array('key' => $key, 'status' => @$r['status'] ?: '조례 미확인', 'others' => (string)@$r['others'], 'note' => (string)@$r['note'],
        'url' => @$r['source'], 'enforce' => @$r['enforce'], 'change' => isset($ch['items']) ? $ch['items'] : array(),
        'relaxed' => isset($ch['relaxed']) ? $ch['relaxed'] : null, 'amendedAfterDecree' => !empty($r['amendedAfterDecree']));
}

// 기하: 필지 중심 기준 평면(m) 좌표
function sl_rings($g) {
    if (!$g) return array();
    $c = isset($g['coordinates']) ? $g['coordinates'] : array();
    if ($g['type'] === 'Polygon') return $c ? array($c[0]) : array();
    if ($g['type'] === 'MultiPolygon') { $o = array(); foreach ($c as $poly) if ($poly) $o[] = $poly[0]; return $o; }
    return array();
}
function sl_proj($p, $o) { return array(($p[0] - $o[0]) * $o[2], ($p[1] - $o[1]) * 110540); }
function sl_seg_dist($p, $a, $b) {
    $dx = $b[0] - $a[0]; $dy = $b[1] - $a[1];
    if ($dx == 0 && $dy == 0) return hypot($p[0] - $a[0], $p[1] - $a[1]);
    $t = max(0, min(1, (($p[0] - $a[0]) * $dx + ($p[1] - $a[1]) * $dy) / ($dx * $dx + $dy * $dy)));
    return hypot($p[0] - ($a[0] + $t * $dx), $p[1] - ($a[1] + $t * $dy));
}
function sl_inside($p, $ring) {
    $in = false; $n = count($ring);
    for ($i = 0; $i < $n; $i++) {
        $a = $ring[$i]; $b = $ring[($i + 1) % $n];
        if ((($a[1] > $p[1]) != ($b[1] > $p[1])) && $p[0] < ($b[0] - $a[0]) * ($p[1] - $a[1]) / ($b[1] - $a[1] + 1e-12) + $a[0]) $in = !$in;
    }
    return $in;
}
function sl_dist_parcel($p, $rings) {
    foreach ($rings as $r) if (sl_inside($p, $r)) return 0.0;
    $d = INF;
    foreach ($rings as $r) { $n = count($r); for ($i = 0; $i < $n; $i++) $d = min($d, sl_seg_dist($p, $r[$i], $r[($i + 1) % $n])); }
    return $d;
}
function sl_line_parcel($line, $rings) {
    if (!$line) return INF;
    $d = INF;
    foreach ($line as $p) $d = min($d, sl_dist_parcel($p, $rings));
    $n = count($line);
    foreach ($rings as $r) foreach ($r as $q) for ($i = 0; $i < $n - 1; $i++) $d = min($d, sl_seg_dist($q, $line[$i], $line[$i + 1]));
    return $d;
}

// 주택 무리(주거지) 판정 — setback._judge 와 같음. houses: [[x, y, 필지까지 거리]...]
// 주택 중심 간 70m 이내를 같은 주거지로 묶는다. houses: [[x, y, ...]...] (m) → [[번호...]...]
function sl_groups($houses) {
    $n = count($houses);
    $parent = range(0, max(0, $n - 1));
    $find = function ($i) use (&$parent) { while ($parent[$i] != $i) { $parent[$i] = $parent[$parent[$i]]; $i = $parent[$i]; } return $i; };
    $grid = array();  // 70m 칸으로 나눠 이웃 칸만 비교
    foreach ($houses as $i => $h) $grid[floor($h[0] / SL_LINK_M) . ',' . floor($h[1] / SL_LINK_M)][] = $i;
    foreach ($houses as $i => $h) {
        $gx = floor($h[0] / SL_LINK_M); $gy = floor($h[1] / SL_LINK_M);
        for ($dx = -1; $dx <= 1; $dx++) for ($dy = -1; $dy <= 1; $dy++) {
            $k = ($gx + $dx) . ',' . ($gy + $dy);
            if (!isset($grid[$k])) continue;
            foreach ($grid[$k] as $j) if ($j > $i && hypot($h[0] - $houses[$j][0], $h[1] - $houses[$j][1]) <= SL_LINK_M) $parent[$find($i)] = $find($j);
        }
    }
    $groups = array();
    for ($i = 0; $i < $n; $i++) $groups[$find($i)][] = $i;
    return $groups;
}
function sl_judge($houses, $rule) {
    $tiers = $rule['tiers'] ?: array(array('houses' => $rule['houses'], 'm' => $rule['limit']));
    $groups = sl_groups($houses);
    $near = null;
    foreach ($houses as $h) $near = $near === null ? $h[2] : min($near, $h[2]);
    $base = array('limit' => $rule['limit'], 'houses' => $rule['houses'], 'basis' => $rule['basis'], 'nearestHouse' => $near === null ? null : round($near));
    $worst = null; $best = null;
    foreach ($groups as $idx) {
        $d = INF; foreach ($idx as $i) $d = min($d, $houses[$i][2]);
        $cnt = count($idx);
        foreach ($tiers as $t) {
            if ($cnt < $t['houses']) continue;
            if ($best === null || $d < $best[0]) $best = array($d, $cnt, $t['m']);
            if ($d < $t['m'] && ($worst === null || $t['m'] - $d > $worst[0])) $worst = array($t['m'] - $d, $d, $cnt, $t['m']);
        }
    }
    if ($worst) return array_merge($base, array('grade' => 'violate', 'label' => '이격 미달 추정', 'nearest' => round($worst[1]), 'cluster' => $worst[2],
        'why' => "{$worst[2]}호 주거지 " . round($worst[1]) . "m < 기준 {$worst[3]}m"));
    if ($best === null) return array_merge($base, array('grade' => 'ok', 'label' => '이격 충족 추정', 'why' => "주변에 {$rule['houses']}호 이상 주거지 없음"));
    return array_merge($base, array('grade' => 'ok', 'label' => '이격 충족 추정', 'nearest' => round($best[0]), 'cluster' => $best[1],
        'why' => "{$best[1]}호 주거지 " . round($best[0]) . "m ≥ 기준 {$best[2]}m"));
}

// 조례 도로 종류 → [도로등급 판별 함수 또는 null, 측정 못 하는 종류가 섞였는지] (setback.road_match 와 같음)
function sl_road_match($kind) {
    $missing = (bool)preg_match('/농어촌도로|면도|리도|농도/u', $kind);
    if (preg_match('/(?<!농어촌)(?<!고속)도로|교통시설/u', $kind)) return array(function ($r) { return true; }, $missing);
    $spec = array();
    if (strpos($kind, '고속') !== false) $spec[] = '고속';
    if (preg_match('/(?<!고속)국도/u', $kind)) $spec[] = '국도';
    if (strpos($kind, '지방도') !== false) { $spec[] = '지방도'; $spec[] = '광역시도'; }
    if (preg_match('/군도|(?<!광역)시도/u', $kind)) $spec[] = '군도';
    if (!$spec) return array(null, $missing);
    return array(function ($r) use ($spec) { foreach ($spec as $s) if (strpos($r, $s) !== false) return true; return false; }, $missing);
}

function sl_houses($o, $prings, $buf) {
    list($blds) = sl_features(array('data' => 'LT_C_BLDGINFO', 'geomFilter' => sprintf('POINT(%.7f %.7f)', $o[0], $o[1]),
        'buffer' => $buf, 'geometry' => 'true', 'attribute' => 'true'), 3);
    $houses = array();
    foreach ($blds as $b) {
        $use = (string)@$b['properties']['usability'];
        if (substr($use, 0, 2) !== '01' && substr($use, 0, 2) !== '02') continue;  // 01 단독주택, 02 공동주택
        $pts = array();
        foreach (sl_rings(@$b['geometry']) as $r) foreach ($r as $p) $pts[] = sl_proj($p, $o);
        if (!$pts) continue;
        $cx = array_sum(array_column($pts, 0)) / count($pts); $cy = array_sum(array_column($pts, 1)) / count($pts);
        $d = INF; foreach ($pts as $p) $d = min($d, sl_dist_parcel($p, $prings));
        $houses[] = array($cx, $cy, $d);
    }
    return array($houses, count($blds) >= SL_MAX_BLDG);
}

function sl_roads($o, $prings, $reach, $roads) {
    $want = array(); $far = 0;
    foreach ($roads as $r) { $m = sl_road_match($r['kind']); $want[] = array($r, $m); if ($m[0] && $r['m']) $far = max($far, $r['m']); }
    $links = array();
    if ($far) {
        list($fs) = sl_features(array('data' => 'LT_L_MOCTLINK', 'geomFilter' => sprintf('POINT(%.7f %.7f)', $o[0], $o[1]),
            'buffer' => intval(min($far + $reach + 50, 2000)), 'geometry' => 'true', 'attribute' => 'true'), 3);
        foreach ($fs as $f) {
            $g = @$f['geometry'];
            $lines = $g['type'] === 'LineString' ? array($g['coordinates']) : $g['coordinates'];
            $pl = array();
            foreach ($lines as $ln) $pl[] = array_map(function ($p) use ($o) { return sl_proj($p, $o); }, $ln);
            $links[] = array((string)@$f['properties']['rd_rank_h'], $pl);
        }
    }
    $out = array();
    foreach ($want as $w) {
        list($r, list($ok, $missing)) = $w;
        $row = array('kind' => $r['kind'], 'm' => $r['m']);
        if (!$r['m'] || !$ok) {
            $out[] = array_merge($row, array('d' => null, 'grade' => 'unknown', 'why' => !$r['m'] ? '거리 확인 필요' : '농어촌도로·면도 등은 지도 데이터에 없어 측정 못 함'));
            continue;
        }
        $d = INF;
        foreach ($links as $l) if ($ok($l[0])) foreach ($l[1] as $ln) $d = min($d, sl_line_parcel($ln, $prings));
        $row['d'] = is_infinite($d) ? null : round($d);
        $row['grade'] = !is_infinite($d) && $d < $r['m'] ? 'violate' : 'ok';
        if ($missing) $row['why'] = '농어촌도로·면도는 측정 못 함 (나머지 도로만 잼)';
        $out[] = $row;
    }
    return $out;
}

function sl_change($now, $before, $uncertain) {
    $n = @$now['grade'];
    if (!in_array($n, array('ok', 'violate', 'norule'))) return array('code' => 'unknown', 'label' => '전후 비교 불가');
    $reasons = array(); $unk = false;
    if (@$before['grade'] === 'violate') $reasons[] = '주거 ' . $before['why'];
    foreach ($before['roads'] as $r) {
        if ($r['grade'] === 'violate') $reasons[] = "{$r['kind']} {$r['d']}m < {$r['m']}m";
        if ($r['grade'] === 'unknown' || !empty($r['why'])) $unk = true;
    }
    if ($n === 'violate') return array('code' => 'still', 'label' => '여전히 이격 미달', 'why' => implode(' · ', $reasons));
    if ($reasons) return array('code' => 'new', 'label' => '9.18로 새로 가능', 'why' => '이전 기준 미달: ' . implode(' · ', $reasons), 'uncertain' => (bool)$uncertain);
    if ($unk || !empty($before['cut'])) return array('code' => 'ok', 'label' => '원래 가능 추정', 'why' => '일부 도로는 측정 못 함', 'partial' => true);
    return array('code' => 'ok', 'label' => '원래 가능');
}

// 필지 이격 측정 (지금 기준 + 9.18 이전 기준). 도심(주변 건물 3,000동 이상)은 재지 않는다.
function sl_measure($parcel, $rule, $before) {
    $sig = function ($ts) { return implode(',', array_map(function ($t) { return "{$t['houses']}:{$t['m']}"; }, $ts)); };
    $key = $parcel['pnu'] . '|' . $sig($rule['tiers']) . ($before ? '|b' . $sig($before['tiers']) . '|' .
        implode(',', array_map(function ($r) { return "{$r['kind']}:{$r['m']}"; }, $before['roads'])) : '');
    $hit = sl_cache_get('setback', $key, 30 * 86400);
    if ($hit !== null) return $hit;
    $rings = $parcel['rings'];
    $n = 0; $sx = 0; $sy = 0;
    foreach ($rings as $r) foreach ($r as $p) { $sx += $p[0]; $sy += $p[1]; $n++; }
    $o = array($sx / $n, $sy / $n, 111320 * cos(deg2rad($sy / $n)));
    $prings = array();
    foreach ($rings as $r) $prings[] = array_map(function ($p) use ($o) { return sl_proj($p, $o); }, $r);
    $reach = 0;
    foreach ($prings as $r) foreach ($r as $p) $reach = max($reach, hypot($p[0], $p[1]));
    $buf = intval(min($rule['limit'] + $reach + 300, 2000));
    $far = intval(min(max($buf, ($before ? $before['limit'] : 0) + $reach + 300), 2000));
    list($houses, $cut) = sl_houses($o, $prings, $far);
    $cutFar = $cut;  // 넓은 범위가 잘렸으면 이전 기준 판정은 일부 주택이 빠진 추정
    if ($cut && $far > $buf) list($houses2, $cut) = sl_houses($o, $prings, $buf);
    if ($cut) {
        $res = array('grade' => 'urban', 'label' => '도심이라 측정 안 함', 'why' => '주변 건물이 너무 많아(3,000동 이상) 이격을 재지 않습니다', 'basis' => $rule['basis']);
    } else {
        $near = isset($houses2) ? $houses2 : $houses;
        $res = $rule['limit'] ? sl_judge($near, $rule) : array('grade' => 'norule', 'label' => '이격 기준 없음', 'basis' => $rule['basis']);
        if ($before) {
            $b = $before['tiers'] ? sl_judge($houses, $before) : array('grade' => 'ok', 'why' => '주거 이격 없음');
            $b = array_intersect_key($b, array_flip(array('grade', 'why', 'nearest', 'cluster')));
            $b['roads'] = sl_roads($o, $prings, $reach, $before['roads']);
            $b['basis'] = $before['basis'];
            if ($cutFar) $b['cut'] = true;
            $res['before'] = $b;
            $res['change'] = sl_change($res, $b, $before['uncertain']);
        }
    }
    $res['loc'] = array(round($o[1], 6), round($o[0], 6));
    sl_cache_put('setback', $key, $res);
    return $res;
}

// ---------------------------------------------------------------- 종합 판정 (research.py 와 같은 결과 형식)

function sl_verdict($parts) {
    $bad = array(); $cond = array();
    $j = @$parts['parcel']['jimok'];
    if ($j === '임야') $bad[] = '지목이 임야 (산지 규제: 20년 뒤 원상복구·지목변경 불가·REC 가중치 0.5)';
    elseif ($j && $j !== '-' && !in_array($j, $GLOBALS['SL_GOOD_JIMOK'])) $bad[] = "지목이 $j";
    $g = @$parts['grid']['grade'];
    if ($g === 'fail') $bad[] = '계통 여유 부족'; elseif (in_array($g, array('none', 'error', null), true)) $cond[] = '계통 정보 없음';
    $l = @$parts['landuse']['grade'];
    if ($l === 'exclude') $bad[] = '용도지역 1차 제외'; elseif (in_array($l, array('cond', 'none', 'error', null), true)) $cond[] = '용도지역 확인 필요';
    $s = @$parts['setback']['grade'];
    if ($s === 'violate') $bad[] = '주거지 이격 미달 추정'; elseif (in_array($s, array('none', 'error', 'urban'), true)) $cond[] = '이격 측정 못 함';
    if ($bad) return array('grade' => 'bad', 'label' => '부적합 가능성 높음', 'why' => array_merge($bad, $cond));
    if ($cond) return array('grade' => 'cond', 'label' => '조건부 검토 필요', 'why' => $cond);
    return array('grade' => 'good', 'label' => '1차 검토상 적합 가능', 'why' => array('계통·용도·이격 1차 기준 통과'));
}

// 주소(q) 또는 필지번호(pnu)로 조사
function sl_research($q, $pick = null, $pnu = null) {
    @set_time_limit(150);
    $cands = array(); $c = array('road' => null, 'x' => null, 'y' => null, 'addr' => '');
    if (!$pnu) {
        $cands = sl_search($q);
        if (!$cands) return array('ok' => false, 'error' => '주소를 찾지 못했습니다. 시군구·읍면동·지번까지 입력해 보세요.');
        $c = $cands[0];
        foreach ($cands as $x) if ($pick && $x['pnu'] === $pick) $c = $x;
        if (!$c['pnu']) {  // 도로명으로 찾았으면 지번 검색으로 PNU
            $s2 = sl_search($c['addr']);
            foreach ($s2 as $x) if ($x['pnu']) { $c['pnu'] = $x['pnu']; break; }
        }
        if (!$c['pnu']) return array('ok' => false, 'error' => '필지 번호를 찾지 못했습니다. 지번 주소로 입력해 보세요.', 'cands' => $cands);
        $pnu = $c['pnu'];
    }
    $parcel = sl_parcel($pnu);
    if (!$parcel) return array('ok' => false, 'error' => '필지 정보를 찾지 못했습니다.');
    $addr = $parcel['addr'] ?: $c['addr'];
    list($sido, $sgg, $emd, $li, $jibun) = sl_split_addr($addr);
    $e = sl_env();
    $min = intval(isset($e['SOLAR_MIN_KW']) ? $e['SOLAR_MIN_KW'] : 100) ?: 100;
    $good = intval(isset($e['SOLAR_GOOD_KW']) ? $e['SOLAR_GOOD_KW'] : 300) ?: 300;

    try {
        list($level, $rows) = $emd ? sl_kepco_lookup(substr($pnu, 0, 2), substr($pnu, 2, 3), $emd, $li, $pnu[10] === '1' ? $jibun : null) : array(null, array());
        $grid = sl_kepco_summary($level, $rows, $min, $good);
    } catch (SolarErr $ex) {
        $grid = array('grade' => 'error', 'label' => '계통 조회 실패', 'error' => $ex->getMessage());
    }
    try { $land = sl_screen(sl_zones($pnu)); }
    catch (SolarErr $ex) { $land = array('grade' => 'error', 'label' => '용도 조회 실패', 'error' => $ex->getMessage(), 'zones' => array()); }

    $rule = sl_rule_for($sido, $sgg);
    $before = sl_rule_before($sido, $sgg);
    if ($before && !$before['tiers'] && !$before['roads']) $before = null;
    if (!$rule['limit'] && !$before) {
        $sb = array('grade' => 'norule', 'label' => '이격 기준 없음', 'basis' => $rule['basis']);
    } else {
        try { $sb = sl_measure($parcel, $rule, $before); }
        catch (SolarErr $ex) { $sb = array('grade' => 'error', 'label' => '이격 측정 실패', 'error' => $ex->getMessage()); }
    }
    $sb['ord'] = sl_ord_summary($sido, $sgg);

    $pinfo = $parcel; unset($pinfo['rings']);
    $parts = array('parcel' => $pinfo, 'grid' => $grid, 'landuse' => $land, 'setback' => $sb);
    $cl = array();
    foreach ($cands as $x) if ($x['pnu'] && count($cl) < 6) $cl[] = array('pnu' => $x['pnu'], 'addr' => $x['addr']);
    $loc = isset($sb['loc']) ? $sb['loc'] : null;
    return array_merge(array('ok' => true, 'query' => $q, 'addr' => $addr, 'road' => $c['road'],
        'x' => $c['x'] ?: ($loc ? $loc[1] : null), 'y' => $c['y'] ?: ($loc ? $loc[0] : null),
        'sido' => $sido, 'sgg' => $sgg, 'emd' => $emd, 'li' => $li, 'jibun' => $jibun, 'minKw' => $min, 'goodKw' => $good),
        $parts, array('verdict' => sl_verdict($parts), 'cands' => $cl, 'rings' => $parcel['rings']));
}

// ---------------------------------------------------------------- 리·동 가능 범위 (지도)
// 리(동) 경계 안을 20m 안팎 칸으로 나눠, 칸마다 주거 이격(9.18 전후)과 9.18 이전 도로 이격을 만족하는지 칠한다.
// 칸 값: 0 경계 밖 · 1 지금도 이격 미달 · 2 9.18로 새로 가능 · 3 원래 가능 (이전 기준을 모르면 1/3 만)
define('SL_ZONE_BLDG', 8000);  // 리 둘레 건물이 이보다 많으면 도심으로 보고 칠하지 않음

function sl_dp($pts, $tol) {  // Douglas-Peucker (반복형)
    $n = count($pts);
    if ($n < 4) return $pts;
    $keep = array_fill(0, $n, false); $keep[0] = $keep[$n - 1] = true;
    $stack = array(array(0, $n - 1));
    while ($stack) {
        list($a, $b) = array_pop($stack);
        $best = 0; $bi = -1;
        for ($i = $a + 1; $i < $b; $i++) { $d = sl_seg_dist($pts[$i], $pts[$a], $pts[$b]); if ($d > $best) { $best = $d; $bi = $i; } }
        if ($bi > 0 && $best > $tol) { $keep[$bi] = true; $stack[] = array($a, $bi); $stack[] = array($bi, $b); }
    }
    $o = array(); foreach ($pts as $i => $p) if ($keep[$i]) $o[] = $p;
    return $o;
}
// 리·동 경계 (0.05도 칸 단위, 화면 표시용으로 간략화). 읍·면은 리로 나뉘어 있어 동(가)만 넣는다
function sl_areas($tx, $ty) {
    $key = "$tx,$ty";
    $hit = sl_cache_get('area', $key, 90 * 86400);
    if ($hit !== null) return $hit;
    $box = sprintf('BOX(%.2f,%.2f,%.2f,%.2f)', $tx * 0.05, $ty * 0.05, ($tx + 1) * 0.05, ($ty + 1) * 0.05);
    $out = array();
    foreach (array(array('LT_C_ADRI_INFO', 'li_cd'), array('LT_C_ADEMD_INFO', 'emd_cd')) as $L) {
        list($fs) = sl_features(array('data' => $L[0], 'geomFilter' => $box, 'geometry' => 'true', 'attribute' => 'true'), 2);
        foreach ($fs as $f) {
            $p = $f['properties'];
            if ($L[1] === 'emd_cd' && !preg_match('/(동|가)$/u', $p['full_nm'])) continue;
            $rings = array();
            foreach (sl_rings(@$f['geometry']) as $r) {
                $r = array_map(function ($q) { return array(round($q[0], 5), round($q[1], 5)); }, sl_dp($r, 0.00003));  // 약 3m
                if (count($r) >= 4) $rings[] = $r;
            }
            if ($rings) $out[] = array('c' => $p[$L[1]], 'n' => $p['full_nm'], 'r' => $rings);
        }
    }
    sl_cache_put('area', $key, $out);
    return $out;
}
function sl_area_geom($cd) {
    $hit = sl_cache_get('areag', $cd, 90 * 86400);
    if ($hit !== null) return $hit;
    $ri = strlen($cd) == 10;
    list($fs) = sl_features(array('data' => $ri ? 'LT_C_ADRI_INFO' : 'LT_C_ADEMD_INFO', 'geomFilter' => 'BOX(124,33,132,39)',
        'attrFilter' => ($ri ? 'li_cd' : 'emd_cd') . ":=:$cd", 'geometry' => 'true', 'attribute' => 'true'));
    if (!$fs) return null;
    $res = array('n' => $fs[0]['properties']['full_nm'], 'r' => sl_rings($fs[0]['geometry']));
    sl_cache_put('areag', $cd, $res);
    return $res;
}
// 상자 범위의 건물·도로를 전부 받는다. $max 개를 넘으면 null (도심)
function sl_box_features($data, $box, $max) {
    $p = array('data' => $data, 'geomFilter' => $box, 'geometry' => 'true', 'attribute' => 'true');
    list($fs, $total) = sl_features($p, 1);
    if ($total > $max) return null;
    for ($page = 2; ($page - 1) * 1000 < $total; $page++) {
        $d = sl_vw(SL_DATA_URL, array_merge(array('service' => 'data', 'request' => 'GetFeature', 'format' => 'json',
            'errorformat' => 'json', 'crs' => 'EPSG:4326', 'size' => 1000, 'page' => $page), $p));
        $got = @$d['response']['result']['featureCollection']['features'];
        if (!$got) break;
        $fs = array_merge($fs, $got);
    }
    return $fs;
}
// 격자 문자열에 원(중심 cx,cy, 반지름 R 칸)을 '1'로 찍는다
function sl_stamp(&$s, $w, $h, $cx, $cy, $R) {
    $y0 = max(0, (int)floor($cy - $R)); $y1 = min($h - 1, (int)ceil($cy + $R));
    for ($j = $y0; $j <= $y1; $j++) {
        $dy = $j + 0.5 - $cy;
        if (abs($dy) > $R) continue;
        $hw = sqrt($R * $R - $dy * $dy);
        $i0 = max(0, (int)ceil($cx - $hw - 0.5)); $i1 = min($w - 1, (int)floor($cx + $hw - 0.5));
        $row = $j * $w;
        for ($i = $i0; $i <= $i1; $i++) $s[$row + $i] = '1';
    }
}
function sl_zone($cd) {
    $a = sl_area_geom($cd);
    if (!$a) return array('ok' => false, 'error' => '경계를 찾지 못했습니다');
    $t = explode(' ', $a['n']);
    $sido = $t[0]; $sgg = isset($t[1]) ? $t[1] : '';
    if (isset($t[2]) && preg_match('/시$/u', $sgg) && preg_match('/구$/u', $t[2])) $sgg .= ' ' . $t[2];
    $rule = sl_rule_for($sido, $sgg);
    $before = sl_rule_before($sido, $sgg);
    if ($before && !$before['tiers'] && !$before['roads']) $before = null;
    $sig = function ($ts) { return implode(',', array_map(function ($t) { return "{$t['houses']}:{$t['m']}"; }, $ts)); };
    $key = "$cd|" . $sig($rule['tiers']) . ($before ? '|' . $sig($before['tiers']) . '|' .
        implode(',', array_map(function ($r) { return "{$r['kind']}:{$r['m']}"; }, $before['roads'])) : '');
    $hit = sl_cache_get('zone', $key, 30 * 86400);
    if ($hit !== null) return $hit;

    $minx = INF; $miny = INF; $maxx = -INF; $maxy = -INF;
    foreach ($a['r'] as $r) foreach ($r as $p) { $minx = min($minx, $p[0]); $maxx = max($maxx, $p[0]); $miny = min($miny, $p[1]); $maxy = max($maxy, $p[1]); }
    $mx = 111320 * cos(deg2rad(($miny + $maxy) / 2));
    $ext = max(($maxx - $minx) * $mx, ($maxy - $miny) * 110540);
    $cs = max(20, (int)ceil($ext / 220 / 5) * 5);  // 칸 크기(m): 한 변 220칸 이내
    $dlon = $cs / $mx; $dlat = $cs / 110540;
    $w = (int)ceil(($maxx - $minx) / $dlon); $h = (int)ceil(($maxy - $miny) / $dlat);
    $base = array('ok' => true, 'cd' => $cd, 'n' => $a['n'], 'key' => $rule['key'], 'now' => $rule['basis'],
        'before' => $before ? $before['basis'] : null, 'uncertain' => $before ? $before['uncertain'] : false);
    // 경계 안 (가로줄마다 경계와 만나는 점 사이를 채움)
    $in = str_repeat('0', $w * $h);
    for ($j = 0; $j < $h; $j++) {
        $lat = $maxy - ($j + 0.5) * $dlat; $xs = array();
        foreach ($a['r'] as $r) {
            $n = count($r);
            for ($k = 0; $k < $n; $k++) {
                $p = $r[$k]; $q = $r[($k + 1) % $n];
                if (($p[1] > $lat) != ($q[1] > $lat)) $xs[] = $p[0] + ($lat - $p[1]) * ($q[0] - $p[0]) / ($q[1] - $p[1]);
            }
        }
        sort($xs);
        for ($k = 0; $k + 1 < count($xs); $k += 2) {
            $i0 = max(0, (int)ceil(($xs[$k] - $minx) / $dlon - 0.5)); $i1 = min($w - 1, (int)floor(($xs[$k + 1] - $minx) / $dlon - 0.5));
            for ($i = $i0; $i <= $i1; $i++) $in[$j * $w + $i] = '1';
        }
    }
    $toG = function ($p) use ($minx, $maxy, $dlon, $dlat) { return array(($p[0] - $minx) / $dlon, ($maxy - $p[1]) / $dlat); };
    // 주택 (01 단독, 02 공동). 이전 기준 거리까지 넉넉히 받는다
    $far = max($rule['limit'], $before ? $before['limit'] : 0);
    $bn = str_repeat('0', $w * $h); $bb = $bn;
    $nh = 0; $roadNote = null;
    if ($far) {
        $mg = min($far + 40, 1000);
        $box = sprintf('BOX(%.6f,%.6f,%.6f,%.6f)', $minx - $mg / $mx, $miny - $mg / 110540, $maxx + $mg / $mx, $maxy + $mg / 110540);
        $blds = sl_box_features('LT_C_BLDGINFO', $box, SL_ZONE_BLDG);
        if ($blds === null) {
            $res = array_merge($base, array('urban' => true));
            sl_cache_put('zone', $key, $res);
            return $res;
        }
        $houses = array();
        foreach ($blds as $b) {
            $use = substr((string)@$b['properties']['usability'], 0, 2);
            if ($use !== '01' && $use !== '02') continue;
            $pts = array();
            foreach (sl_rings(@$b['geometry']) as $r) foreach ($r as $p) $pts[] = $toG($p);
            if (!$pts) continue;
            $cx = array_sum(array_column($pts, 0)) / count($pts); $cy = array_sum(array_column($pts, 1)) / count($pts);
            $rad = 0; foreach ($pts as $p) $rad = max($rad, hypot($p[0] - $cx, $p[1] - $cy));
            $houses[] = array($cx * $cs, $cy * $cs, $rad);  // 주거지 묶기는 m 단위, 건물 반지름은 칸 단위
        }
        $nh = count($houses);
        $mOf = function ($tiers, $cnt) { $m = 0; foreach ($tiers as $t) if ($cnt >= $t['houses']) $m = max($m, $t['m']); return $m; };
        foreach (sl_groups($houses) as $idx) {
            $cnt = count($idx);
            $m1 = $rule['limit'] ? $mOf($rule['tiers'], $cnt) : 0;
            $m0 = $before ? $mOf($before['tiers'], $cnt) : 0;
            foreach ($idx as $i) {
                $hh = $houses[$i];
                if ($m1) sl_stamp($bn, $w, $h, $hh[0] / $cs, $hh[1] / $cs, $m1 / $cs + $hh[2]);
                if ($m0) sl_stamp($bb, $w, $h, $hh[0] / $cs, $hh[1] / $cs, $m0 / $cs + $hh[2]);
            }
        }
    }
    // 9.18 이전 도로 이격 (국도·지방도 등 표준노드링크에 있는 도로만)
    if ($before && $before['roads']) {
        $want = array(); $rf = 0;
        foreach ($before['roads'] as $r) {
            list($ok, $missing) = sl_road_match($r['kind']);
            if ($missing || !$ok || !$r['m']) $roadNote = '농어촌도로·면도 등 지도에 없는 도로와 거리가 없는 도로 기준은 빠졌습니다';
            if ($ok && $r['m']) { $want[] = array($ok, $r['m']); $rf = max($rf, $r['m']); }
        }
        if ($want) {
            $mg = min($rf, 1000);
            $box = sprintf('BOX(%.6f,%.6f,%.6f,%.6f)', $minx - $mg / $mx, $miny - $mg / 110540, $maxx + $mg / $mx, $maxy + $mg / 110540);
            $links = sl_box_features('LT_L_MOCTLINK', $box, 6000) ?: array();
            foreach ($links as $f) {
                $rank = (string)@$f['properties']['rd_rank_h'];
                $m = 0; foreach ($want as $wv) if (call_user_func($wv[0], $rank)) $m = max($m, $wv[1]);
                if (!$m) continue;
                $g = @$f['geometry'];
                $lines = $g['type'] === 'LineString' ? array($g['coordinates']) : $g['coordinates'];
                $R = $m / $cs; $step = max(1, $R / 8); $done = array();
                foreach ($lines as $ln) {
                    for ($k = 0; $k + 1 < count($ln); $k++) {
                        $p = $toG($ln[$k]); $q = $toG($ln[$k + 1]);
                        $ns = max(1, (int)ceil(hypot($q[0] - $p[0], $q[1] - $p[1]) / $step));
                        for ($u = 0; $u <= $ns; $u++) {
                            $x = $p[0] + ($q[0] - $p[0]) * $u / $ns; $y = $p[1] + ($q[1] - $p[1]) * $u / $ns;
                            if ($x < -$R || $y < -$R || $x > $w + $R || $y > $h + $R) continue;
                            $dk = round($x) . ',' . round($y);
                            if (isset($done[$dk])) continue;
                            $done[$dk] = 1;
                            sl_stamp($bb, $w, $h, $x, $y, $R);
                        }
                    }
                }
            }
        }
    }
    $g = ''; $cnt = array(0, 0, 0, 0);
    for ($k = 0, $N = $w * $h; $k < $N; $k++) {
        $v = $in[$k] === '0' ? 0 : ($bn[$k] === '1' ? 1 : ($before && $bb[$k] === '1' ? 2 : 3));
        $g .= $v; $cnt[$v]++;
    }
    $ha = $cs * $cs / 10000;
    $res = array_merge($base, array('urban' => false, 'cs' => $cs, 'x0' => round($minx, 7), 'y1' => round($maxy, 7),
        'dlon' => $dlon, 'dlat' => $dlat, 'w' => $w, 'h' => $h, 'g' => $g, 'houses' => $nh, 'roadNote' => $roadNote,
        'ha' => array('blocked' => round($cnt[1] * $ha, 1), 'new' => round($cnt[2] * $ha, 1), 'ok' => round($cnt[3] * $ha, 1))));
    sl_cache_put('zone', $key, $res);
    return $res;
}
// 리(동)의 한전 계통 여유 (한전 자료가 대부분 리 단위라 번지마다 따로 묻지 않는다, 20시간 보관)
function sl_zone_grid($cd, $n) {
    $t = explode(' ', $n);
    $last = end($t);
    $li = preg_match('/리$/u', $last) ? $last : null;
    $emd = $li ? $t[count($t) - 2] : $last;
    $e = sl_env();
    $min = intval(isset($e['SOLAR_MIN_KW']) ? $e['SOLAR_MIN_KW'] : 100) ?: 100;
    $good = intval(isset($e['SOLAR_GOOD_KW']) ? $e['SOLAR_GOOD_KW'] : 300) ?: 300;
    try {
        list($level, $rows) = sl_kepco_lookup(substr($cd, 0, 2), substr($cd, 2, 3), $emd, $li, null);
        $g = sl_kepco_summary($level, $rows, $min, $good);
    } catch (SolarErr $ex) {
        $g = array('grade' => 'error', 'label' => '계통 조회 실패');
    }
    unset($g['lineList']);
    return array_merge($g, array('minKw' => $min, 'goodKw' => $good));
}
