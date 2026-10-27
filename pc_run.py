"""
내 PC에서 수집·알림 실행 (아이비호스팅이 2026.10.22부터 PHP의 프로그램 실행(exec)을 막아 서버에서 못 돌리게 됨)

  python pc_run.py            설정 받기 → onbid_alert.py 실행 → 결과를 서버(대시보드)로 올리기
  python pc_run.py --pull     처음 한 번: 서버의 기록·캐시·카카오 토큰을 PC로 가져오기 (PC 것을 덮어씀)
  python pc_run.py --no-run   실행 없이 올리기만 (점검용)

Windows 작업 스케줄러가 2시간마다 부른다 (pc_task_install.ps1).
웹 화면에서 바꾼 조회 설정(settings.json)은 실행 때마다 서버에서 받아 온다.
실행 기록: logs/pc_run.log
"""
import ftplib
import io
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from deploy_server import read_env  # noqa: E402

SITE = "https://solarnitice.ivyro.net/"
DATA_FILE = "dash_data.json"  # 대시보드 데이터 (서버 앱 폴더의 같은 이름 파일로 올림)
STATE = ["seen_items.json", "state.json", "kepco_cache.json", "landuse_cache.json", "court_cache.json",
         "setback_cache.json"]
UPLOAD = [DATA_FILE] + STATE + ["onbid_alert.log"]
LOG = os.path.join(BASE, "logs", "pc_run.log")
RUN_TIMEOUT = 40 * 60


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        print(line, flush=True)
    except Exception:
        pass


def rotate_log():
    if os.path.exists(LOG) and os.path.getsize(LOG) > 2_000_000:
        os.replace(LOG, LOG + ".1")


class Ftp:
    def __init__(self):
        fe = read_env(os.path.join(BASE, "ftp.env"))
        ke = read_env(os.path.join(BASE, "API_key.env"))
        self.app = "/public_html/_app_" + ke["APP_DIR_KEY"]
        for i in range(3):
            try:
                self.f = ftplib.FTP(fe["FTP_HOST"], timeout=60)
                self.f.login(fe["FTP_USER"], fe["FTP_PASS"])
                self.f.encoding = "utf-8"
                return
            except (OSError, ftplib.Error) as e:
                if i == 2:
                    raise
                log(f"FTP 접속 재시도 ({e})")
                time.sleep(20)

    def get(self, name):
        buf = io.BytesIO()
        try:
            self.f.retrbinary(f"RETR {self.app}/{name}", buf.write)
        except ftplib.error_perm:
            return None
        return buf.getvalue()

    def put(self, name, data):
        # 임시 이름으로 올린 뒤 바꿔치기 → 화면이 반쯤 올라간 파일을 읽지 않게
        self.f.storbinary(f"STOR {self.app}/{name}.up", io.BytesIO(data))
        try:
            self.f.delete(f"{self.app}/{name}")
        except ftplib.error_perm:
            pass
        self.f.rename(f"{self.app}/{name}.up", f"{self.app}/{name}")

    def close(self):
        try:
            self.f.quit()
        except Exception:
            pass


def set_local_secret(key, val):
    """API_key.env 의 한 줄만 바꾼다 (값은 로그에 남기지 않음)."""
    p = os.path.join(BASE, "API_key.env")
    lines = open(p, encoding="utf-8-sig").read().splitlines()
    out, done = [], False
    for ln in lines:
        if ln.split("=", 1)[0].strip() == key:
            out.append(f"{key}={val}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"{key}={val}")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(p + ".tmp", p)


def pull_all(ftp):
    for name in STATE + ["settings.json"]:
        b = ftp.get(name)
        if b:
            open(os.path.join(BASE, name), "wb").write(b)
            log(f"받음 {name}")
    b = ftp.get(DATA_FILE)
    if b:
        open(os.path.join(BASE, DATA_FILE), "wb").write(b)
        log(f"받음 {DATA_FILE}")
    # 서버가 마지막으로 갱신해 둔 카카오 refresh_token (서버에서 돌 때 바뀌었을 수 있음)
    b = ftp.get(".env.php")
    if b:
        for ln in b.decode("utf-8").splitlines():
            if ln.startswith("KAKAO_REFRESH_TOKEN="):
                set_local_secret("KAKAO_REFRESH_TOKEN", ln.split("=", 1)[1].strip())
                log("카카오 토큰을 서버 것으로 맞춤")


def lock():
    """같은 PC에서 겹쳐 실행되지 않게 (앞 실행이 길어질 때)."""
    import msvcrt
    fh = open(os.path.join(BASE, "pc_run.lock"), "a+")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return None
    return fh


def main():
    rotate_log()
    fh = lock()
    if not fh:
        return log("이미 실행 중이라 건너뜀")
    args = sys.argv[1:]
    ftp = Ftp()
    try:
        if "--pull" in args:
            pull_all(ftp)
            return log("서버 기록 가져오기 완료")
        b = ftp.get("settings.json")  # 웹 화면에서 바꾼 조회 설정
        if b:
            open(os.path.join(BASE, "settings.json"), "wb").write(b)
    finally:
        ftp.close()  # 실행이 길어 FTP 연결이 끊기므로 닫았다가 다시 연다

    if "--no-run" not in args:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                   SOLAR_DATA_FILE=DATA_FILE, LINK_URL=SITE)
        log("수집 시작")
        t0 = time.time()
        with open(os.path.join(BASE, "logs", "pc_stdout.log"), "a", encoding="utf-8") as out:
            try:
                rc = subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), "-u", "onbid_alert.py"],
                                    cwd=BASE, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=RUN_TIMEOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode
            except subprocess.TimeoutExpired:
                rc = "시간 초과"
        log(f"수집 끝 (결과 {rc}, {int(time.time() - t0)}초)")

    ftp = Ftp()
    try:
        for name in UPLOAD:
            p = os.path.join(BASE, name)
            if os.path.exists(p):
                ftp.put(name, open(p, "rb").read())
        log("서버로 올림: " + ", ".join(n for n in UPLOAD if os.path.exists(os.path.join(BASE, n))))
    finally:
        ftp.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 작업 스케줄러에서는 화면이 없으니 로그로 남긴다
        log(f"오류: {type(e).__name__}: {e}")
        sys.exit(1)
