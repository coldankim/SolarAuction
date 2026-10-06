"""
아이비호스팅 배포 (FTP)

  python deploy_server.py              코드·화면만 배포 (서버의 설정·기록·캐시는 그대로 둠)
  python deploy_server.py --with-data  처음 옮길 때: 로컬의 설정·기록·캐시도 함께 올림 (서버 것을 덮어씀)

서버 구조 (FTP 기준)
  /public_html/_app_<무작위>/  파이썬 코드, 설정, 기록, 캐시, 비밀값(.env.php, web_secrets.php), 화면 파일, 화면 데이터
                      (FTP 최상위에는 쓰기 권한이 없고 이 호스팅은 .htaccess도 무시한다 → 폴더 이름을 추측할 수 없게 해서 막는다.
                       폴더 이름은 API_key.env의 APP_DIR_KEY, 서버에서는 app_path.php가 알려준다)
  /public_html/       index.php·research.php(비밀번호 확인 후 화면), data.php, research_api.php, settings.php, trigger.php, style.css

페이지 비밀번호: API_key.env의 PAGE_PW_MAIN(대시보드 등), PAGE_PW_RESEARCH(주소 조사). 바꾼 뒤 다시 배포하면 적용.
필요: ftp.env (FTP_HOST/USER/PASS), API_key.env (키). TRIGGER_TOKEN·SETTINGS_PASSWORD 등이 없으면 만들어 API_key.env에 추가.
"""
import ftplib
import hashlib
import io
import os
import secrets
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
CODE = ["onbid_alert.py", "kepco.py", "landuse.py", "courtauction.py", "setback.py", "research.py", "config.env",
        "setback_rules.json"]
DATA = ["settings.json", "seen_items.json", "state.json", "kepco_cache.json", "landuse_cache.json",
        "court_cache.json", "setback_cache.json"]
SECRET_KEYS = ["KAKAO_REST_API_KEY", "KAKAO_CLIENT_SECRET", "KAKAO_REFRESH_TOKEN", "ONBID_SERVICE_KEY",
               "KEPCO_API_KEY", "VWORLD_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
SITE = "https://solarnitice.ivyro.net/"
OLD_APP = "/public_html/_app"  # 예전 위치 (이름이 알려져 있어 옮김)
HTACCESS = """# 이 폴더는 웹에서 접근 금지 (코드·키·기록 보관)
<IfModule mod_authz_core.c>
Require all denied
</IfModule>
<IfModule !mod_authz_core.c>
Order allow,deny
Deny from all
</IfModule>
"""


def read_env(path):
    env = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def ensure_secret(path, env, key, maker):
    if env.get(key):
        return env[key]
    val = maker()
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n{key}={val}\n")
    env[key] = val
    print(f"{key}를 새로 만들어 {os.path.basename(path)}에 추가했습니다")
    return val


def main():
    with_data = "--with-data" in sys.argv
    fe = read_env(os.path.join(BASE, "ftp.env"))
    kpath = os.path.join(BASE, "API_key.env")
    ke = read_env(kpath)
    trig = ensure_secret(kpath, ke, "TRIGGER_TOKEN", lambda: secrets.token_hex(24))
    pw = ensure_secret(kpath, ke, "SETTINGS_PASSWORD", lambda: secrets.token_urlsafe(9))
    cookie_key = ensure_secret(kpath, ke, "COOKIE_KEY", lambda: secrets.token_hex(32))
    app_name = "_app_" + ensure_secret(kpath, ke, "APP_DIR_KEY", lambda: secrets.token_hex(12))
    APP = "/public_html/" + app_name
    pages = {}
    for area, key in (("main", "PAGE_PW_MAIN"), ("research", "PAGE_PW_RESEARCH")):
        if not ke.get(key):
            raise SystemExit(f"중단: API_key.env에 {key}(페이지 비밀번호)가 없습니다")
        psalt = secrets.token_hex(8)
        pages[area] = (psalt, hashlib.sha256((psalt + ke[key]).encode()).hexdigest())

    # 서버용 비밀값 파일.
    # 이 호스팅은 .htaccess를 무시해서 폴더 차단이 안 된다 → 비밀값은 .php 파일로 두고 첫 줄에서 실행을 끝낸다
    # (웹으로 열면 빈 화면, 파이썬은 '='가 없는 첫 줄을 건너뛰고 읽음)
    server_env = "<?php exit; ?>\n" + "\n".join(f"{k}={ke[k]}" for k in SECRET_KEYS if ke.get(k)) + "\n"
    server_env += "# 서버 전용\nSOLAR_DATA_FILE=dash_data.json\nLINK_URL=" + SITE + "\n"
    salt = secrets.token_hex(8)
    web_secrets = ("<?php\n// 자동 생성 (deploy_server.py). 웹 접근이 차단된 _app 폴더에 둔다.\nreturn array(\n"
                   f"  'trigger_token' => '{trig}',\n  'settings_salt' => '{salt}',\n"
                   f"  'settings_hash' => '{hashlib.sha256((salt + pw).encode()).hexdigest()}',\n"
                   f"  'cookie_key' => '{cookie_key}',\n  'pages' => array(\n"
                   + "".join(f"    '{a}' => array('salt' => '{sl}', 'hash' => '{h}'),\n" for a, (sl, h) in pages.items())
                   + "  ),\n);\n")

    f = ftplib.FTP(fe["FTP_HOST"], timeout=60)
    f.login(fe["FTP_USER"], fe["FTP_PASS"])
    f.encoding = "utf-8"
    pub = set(os.path.basename(x) for x in f.nlst("/public_html"))
    if app_name not in pub and os.path.basename(OLD_APP) in pub:
        f.rename(OLD_APP, APP)  # 설정·기록·캐시를 그대로 가지고 이름만 바꾼다
        print("  → 앱 폴더 이름 변경")
    for d in (APP, APP + "/logs"):
        try:
            f.mkd(d)
        except ftplib.error_perm:
            pass
    existing = set(os.path.basename(x) for x in f.nlst(APP))

    def put(local_bytes, remote):
        # 안전장치: 비밀값이 들어갈 수 있는 파일은 첫 줄에서 실행이 끝나는 .php 형태만 허용
        base = os.path.basename(remote)
        if base.startswith(".env") and not (base.endswith(".php") and local_bytes.startswith(b"<?php exit; ?>")):
            raise SystemExit(f"중단: {remote} 는 웹에서 그대로 보일 수 있어 올리지 않습니다")
        f.storbinary(f"STOR {remote}", io.BytesIO(local_bytes))
        print("  ↑", remote)

    # 예전 배포가 남긴 평문 비밀 파일이 있으면 지운다
    for old in (APP + "/.env",):
        try:
            f.delete(old)
            print("  ✕", old)
        except ftplib.error_perm:
            pass
    put(HTACCESS.encode("utf-8"), APP + "/.htaccess")  # 이 호스팅은 무시하지만, 지원하는 서버로 옮길 때를 위해 둠
    for name in CODE:
        put(open(os.path.join(BASE, name), "rb").read(), f"{APP}/{name}")
    for name in DATA:
        p = os.path.join(BASE, name)
        if os.path.exists(p) and (with_data or name not in existing):
            put(open(p, "rb").read(), f"{APP}/{name}")
    put(server_env.encode("utf-8"), APP + "/.env.php")
    put(web_secrets.encode("utf-8"), APP + "/web_secrets.php")

    put(open(os.path.join(BASE, "server", "auth.php"), "rb").read(), APP + "/auth.php")
    for name in ("index.html", "research.html"):  # 화면은 앱 폴더에 두고 PHP가 비밀번호 확인 후 내보낸다
        put(open(os.path.join(BASE, "docs", name), "rb").read(), f"{APP}/view_{name}")

    put(f"<?php return __DIR__ . '/{app_name}';\n".encode("utf-8"), "/public_html/app_path.php")
    put(open(os.path.join(BASE, "docs", "style.css"), "rb").read(), "/public_html/style.css")
    for name in ("trigger.php", "settings.php", "index.php", "research.php", "research_api.php", "data.php"):
        put(open(os.path.join(BASE, "server", name), "rb").read(), f"/public_html/{name}")
    # 화면 데이터: 예전 공개 파일(data.json)이 있으면 앱 폴더로 옮긴다
    if with_data:
        put(open(os.path.join(BASE, "docs", "data.json"), "rb").read(), APP + "/dash_data.json")
    elif "dash_data.json" not in existing:
        buf = io.BytesIO()
        if "data.json" in pub:
            f.retrbinary("RETR /public_html/data.json", buf.write)
        put(buf.getvalue() or open(os.path.join(BASE, "docs", "data.json"), "rb").read(), APP + "/dash_data.json")
    for old in ("data.json", "index.html"):  # 비밀번호 없이 열리던 예전 공개 파일
        if old in pub:
            f.delete("/public_html/" + old)
            print("  ✕ /public_html/" + old)
    put(b'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=research.php"><a href="research.php">research</a>\n',
        "/public_html/research.html")  # 예전 주소로 들어오면 새 주소로
    f.quit()
    print("배포 완료:", SITE)


if __name__ == "__main__":
    main()
