<?php
// 페이지 비밀번호 (영역별: main = 대시보드 등, research = 주소 조사)
// 로그인하면 서명된 쿠키를 30일간 준다. 비밀번호는 해시로만 보관(web_secrets.php), 틀리면 IP당 10분에 5회까지.
// 이 파일은 함수만 정의하므로 웹으로 직접 열어도 아무것도 나오지 않는다.

function sa_secrets() {
    static $s = null;
    if ($s === null) { $s = require __DIR__ . '/web_secrets.php'; }
    return $s;
}

function sa_sign($area, $exp) {
    $s = sa_secrets();
    return hash_hmac('sha256', $area . '|' . $exp, $s['cookie_key']);
}

function sa_ok($area) {
    $c = isset($_COOKIE['sa_' . $area]) ? (string)$_COOKIE['sa_' . $area] : '';
    $p = explode('.', $c);
    if (count($p) !== 2 || !ctype_digit($p[0]) || intval($p[0]) < time()) { return false; }
    return hash_equals(sa_sign($area, $p[0]), $p[1]);
}

function sa_rate_file() {
    $dir = __DIR__ . '/auth_rate';
    if (!is_dir($dir)) { @mkdir($dir, 0755, true); }
    $ip = preg_replace('/[^0-9a-fA-F:.]/', '', isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '');
    return $dir . '/' . md5($ip) . '.php';
}

function sa_fails() {
    $f = sa_rate_file();
    if (!is_file($f)) { return array(); }
    $now = time();
    return array_values(array_filter(explode(',', substr(file_get_contents($f), strlen("<?php exit; ?>\n"))),
        function ($t) use ($now) { return $t && $now - intval($t) < 600; }));
}

function sa_try($area, $pw) {
    $fails = sa_fails();
    if (count($fails) >= 5) { return '비밀번호를 여러 번 틀렸습니다. 10분 뒤 다시 시도하세요.'; }
    $s = sa_secrets();
    $pg = isset($s['pages'][$area]) ? $s['pages'][$area] : null;
    if ($pg && hash_equals($pg['hash'], hash('sha256', $pg['salt'] . $pw))) {
        $exp = time() + 30 * 86400;
        $secure = !empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off';
        setcookie('sa_' . $area, $exp . '.' . sa_sign($area, $exp), $exp, '/; SameSite=Lax', '', $secure, true);
        return true;
    }
    $fails[] = time();
    file_put_contents(sa_rate_file(), "<?php exit; ?>\n" . implode(',', $fails));
    return '비밀번호가 맞지 않습니다.';
}

function sa_logout($area) {
    setcookie('sa_' . $area, '', time() - 3600, '/; SameSite=Lax', '', false, true);
}

// API용: 로그인 안 했으면 401 JSON
function sa_require_api($area) {
    if (!sa_ok($area)) {
        header('Content-Type: application/json; charset=utf-8');
        http_response_code(401);
        echo '{"ok":false,"error":"로그인이 필요합니다","login":true}';
        exit;
    }
}

// 페이지용: 로그인 안 했으면 비밀번호 입력 화면을 보여주고 끝낸다
function sa_require_page($area, $title) {
    $self = strtok($_SERVER['REQUEST_URI'], '?');
    if (isset($_GET['logout'])) { sa_logout($area); header('Location: ' . $self); exit; }
    if (sa_ok($area)) { return; }
    $err = '';
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['pw'])) {
        $r = sa_try($area, (string)$_POST['pw']);
        if ($r === true) {
            $qs = isset($_SERVER['QUERY_STRING']) && $_SERVER['QUERY_STRING'] !== '' ? '?' . $_SERVER['QUERY_STRING'] : '';
            header('Location: ' . $self . $qs); exit;
        }
        $err = $r;
    }
    header('Content-Type: text/html; charset=utf-8');
    header('Cache-Control: no-store');
    $t = htmlspecialchars($title, ENT_QUOTES, 'UTF-8');
    $e = htmlspecialchars($err, ENT_QUOTES, 'UTF-8');
    echo <<<HTML
<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{$t} · 태양광 경매 공매 알림</title><meta name="robots" content="noindex">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap">
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px; color: #e6ebf3;
         background: radial-gradient(1200px 600px at 20% -10%, #24324d 0%, transparent 60%), linear-gradient(180deg, #111827, #172033);
         font: 15px/1.5 "Noto Sans KR", -apple-system, "Segoe UI", "Malgun Gothic", sans-serif; }
  .box { width: min(380px, 100%); background: rgba(255,255,255,.05); border: 1px solid #243049; border-radius: 16px; padding: 28px 24px;
         box-shadow: 0 20px 60px rgba(0,0,0,.35); }
  .logo { width: 46px; height: 46px; border-radius: 12px; display: grid; place-items: center; margin-bottom: 14px;
          background: radial-gradient(circle at 35% 30%, #ffd08a, #F7931E 60%, #c65f06); box-shadow: 0 0 22px rgba(247,147,30,.4); }
  h1 { margin: 0; font-size: 19px; letter-spacing: -.3px; }
  p { margin: 4px 0 18px; color: #95a1b5; font-size: 13.5px; }
  label { display: block; font-size: 12.5px; color: #95a1b5; margin-bottom: 6px; }
  input { width: 100%; font: inherit; font-size: 18px; letter-spacing: 4px; padding: 12px 14px; border-radius: 10px; border: 1px solid #2c3a57;
          background: #0f1623; color: #fff; }
  input:focus { outline: 2px solid rgba(247,147,30,.5); border-color: #F7931E; }
  button { width: 100%; margin-top: 12px; font: inherit; font-weight: 700; padding: 12px; border: 0; border-radius: 10px; background: #E3740F; color: #fff; cursor: pointer; }
  button:hover { filter: brightness(1.08); }
  .err { color: #ffaaa2; font-size: 13px; margin-top: 10px; min-height: 18px; }
</style></head>
<body><form class="box" method="post" autocomplete="off">
  <div class="logo" aria-hidden="true"><svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg></div>
  <h1>{$t}</h1><p>태양광 경매 공매 알림 · 비밀번호를 입력하세요</p>
  <label for="pw">비밀번호</label>
  <input id="pw" name="pw" type="password" inputmode="numeric" autofocus required>
  <button type="submit">들어가기</button>
  <div class="err" role="alert">{$e}</div>
</form></body></html>
HTML;
    exit;
}
