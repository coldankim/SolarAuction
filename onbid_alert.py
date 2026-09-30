"""
온비드 부동산 공매 신규 물건 → 카카오톡 "나에게 보내기" 알림 + 대시보드 데이터(docs/data.json)

GitHub Actions에서 정기 실행되며, 로컬 PC에서도 그대로 실행할 수 있다.
  python onbid_alert.py --dry-run     조회만 하고 결과를 화면에 출력 (카톡 X, 기록 X)
  python onbid_alert.py --init        현재 물건을 전부 '이미 알림'으로 기록만
  python onbid_alert.py               신규 물건만 카톡 전송 + 기록
  python onbid_alert.py --test-kakao  카카오 토큰 갱신 + 테스트 메시지 1건
  python onbid_alert.py --auth CODE   인가코드로 refresh_token 새로 발급 (로컬 전용)

설정 읽는 순서 (뒤가 우선): config.env(조회 조건, 공개) → .env 또는 API_key.env(키, 로컬) → 환경변수(Actions Secrets)
외부 패키지 없이 파이썬 기본 라이브러리만 사용한다.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import courtauction
import kepco
import landuse
import setback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.env")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")  # 조회 지역·태양광 기준 (화면에서 수정)
STATE_FILE = os.path.join(BASE_DIR, "state.json")        # 지난 실행의 조회 지역 (지역 추가 감지용)
SEEN_FILE = os.path.join(BASE_DIR, "seen_items.json")
DATA_FILE = os.path.join(BASE_DIR, "docs", "data.json")
LOG_FILE = os.path.join(BASE_DIR, "onbid_alert.log")
NEW_TOKEN_FILE = os.path.join(BASE_DIR, "new_refresh_token.txt")  # Actions에서 Secret 교체용 (커밋 금지)

ONBID_URL = "https://apis.data.go.kr/B010003/OnbidRlstListSrvc2/getRlstCltrList2"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

# 재산유형 전체 (압류/국유/기타일반/공유/금융권담보/유입/수탁/공공개발/파산)
ALL_PRPT = "0007,0010,0005,0002,0003,0006,0008,0011,0013"
ENV_KEYS = [
    "KAKAO_REST_API_KEY", "KAKAO_CLIENT_SECRET", "KAKAO_REFRESH_TOKEN", "ONBID_SERVICE_KEY",
    "REGIONS", "PVCT_TRGT_YN", "DSPS_MTHOD_CD", "PRPT_DIV_CD", "MAX_NOTIFY", "LINK_URL",
    "KAKAO_REDIRECT_URI", "KEPCO_API_KEY", "SOLAR_MIN_KW", "SOLAR_GOOD_KW",
    "VWORLD_API_KEY", "VWORLD_DOMAIN", "VWORLD_PROXY_URL", "VWORLD_PROXY_TOKEN",
    "COURT_ENABLED", "COURT_REFRESH_HOURS", "SOLAR_TIME_BUDGET_MIN", "SOLAR_DATA_FILE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "KAKAO_ENABLED",
]
KST = timezone(timedelta(hours=9))
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


class AlertError(Exception):
    pass


WARNINGS = []  # 실행은 계속되지만 화면에 남길 경고 (예: 법원경매 접속 실패)


def now_kst():
    return datetime.now(KST)


def log(msg):
    line = f"[{now_kst():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    if not IN_ACTIONS:
        try:
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 2_000_000:  # 2MB 넘으면 뒤쪽 절반만 남김
                with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                    keep = f.read()[-1_000_000:]
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.write(keep)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def acquire_lock():
    """같은 서버에서 실행이 겹치지 않게 잠금 (리눅스 서버용, 윈도우에서는 생략). 이미 실행 중이면 None."""
    try:
        import fcntl
    except ImportError:
        return True
    fh = open(os.path.join(BASE_DIR, "run.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


# ---------------------------------------------------------------- 설정

def find_secret_file():
    # .env.php: 서버용. 첫 줄 <?php exit; ?> 덕분에 웹으로 열려도 내용이 보이지 않는다
    for name in (".env.php", ".env", "API_key.env"):
        p = os.path.join(BASE_DIR, name)
        if os.path.exists(p):
            return p
    return None


def read_env_file(path):
    env = {}
    if not path or not os.path.exists(path):
        return env
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load_settings():
    secret_path = find_secret_file()
    env = read_env_file(CONFIG_FILE)
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            env.update({k: str(v) for k, v in json.load(f).items() if v is not None})
    env.update(read_env_file(secret_path))
    for k in ENV_KEYS:
        if os.environ.get(k):
            env[k] = os.environ[k]
    missing = [k for k in ENV_KEYS[:4] + ["KEPCO_API_KEY"] if not env.get(k)]
    if missing:
        raise AlertError(f"키가 없습니다: {', '.join(missing)} (로컬은 API_key.env, Actions는 Secrets 확인)")
    return env, secret_path


def update_env(path, key, value):
    """env 파일에서 key 한 줄만 교체(없으면 추가). 다른 줄/주석은 그대로 유지."""
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)  # 쓰는 도중 끊겨도 원본이 깨지지 않게


def save_refresh_token(secret_path, new_rt):
    if IN_ACTIONS:
        # 로그에 찍히지 않게 가리고, 다음 워크플로 단계가 이 파일로 Secret을 교체한다
        print(f"::add-mask::{new_rt}")
        with open(NEW_TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(new_rt)
        log("카카오 refresh_token 갱신됨 → 워크플로에서 Secret 교체 예정")
    else:
        update_env(secret_path, "KAKAO_REFRESH_TOKEN", new_rt)
        log(f"카카오 refresh_token 갱신됨 → {os.path.basename(secret_path)}에 저장")


# ---------------------------------------------------------------- HTTP

def http(url, data=None, headers=None, timeout=30):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    for wait in (0, 10, 30):  # 시간 초과·연결 끊김은 잠시 뒤 재시도 (HTTP 응답은 그대로 돌려줌)
        if wait:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            err = e
    raise AlertError(f"접속 실패: {url.split('?')[0]} ({err})")


FORM = {"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"}


# ---------------------------------------------------------------- 카카오

def kakao_refresh(env, secret_path):
    """refresh_token으로 access_token 갱신. 새 refresh_token이 오면 교체 저장."""
    status, text = http(KAKAO_TOKEN_URL, {
        "grant_type": "refresh_token",
        "client_id": env["KAKAO_REST_API_KEY"],
        "client_secret": env.get("KAKAO_CLIENT_SECRET", ""),
        "refresh_token": env["KAKAO_REFRESH_TOKEN"],
    }, FORM)
    data = json.loads(text) if text.startswith("{") else {}
    if status != 200 or "access_token" not in data:
        raise AlertError(
            f"카카오 토큰 갱신 실패 ({status}): {text[:200]} "
            "- refresh_token 만료/오류. 로컬에서 --auth 로 재발급 후 Secret을 교체하세요."
        )
    new_rt = data.get("refresh_token")
    if new_rt and new_rt != env["KAKAO_REFRESH_TOKEN"]:
        save_refresh_token(secret_path, new_rt)
        env["KAKAO_REFRESH_TOKEN"] = new_rt
    return data["access_token"]


def kakao_auth_code(env, secret_path, code):
    """인가코드 → 토큰 발급 (refresh_token 만료 시 재발급용, 로컬 전용)."""
    status, text = http(KAKAO_TOKEN_URL, {
        "grant_type": "authorization_code",
        "client_id": env["KAKAO_REST_API_KEY"],
        "client_secret": env.get("KAKAO_CLIENT_SECRET", ""),
        "redirect_uri": env.get("KAKAO_REDIRECT_URI", "https://localhost"),
        "code": code,
    }, FORM)
    data = json.loads(text) if text.startswith("{") else {}
    if "refresh_token" not in data:
        raise AlertError(f"인가코드 교환 실패 ({status}): {text}")
    update_env(secret_path, "KAKAO_REFRESH_TOKEN", data["refresh_token"])
    log(f"새 refresh_token 저장 완료 → {os.path.basename(secret_path)} "
        "(GitHub Secret KAKAO_REFRESH_TOKEN도 같은 값으로 교체하세요)")


def kakao_send(access_token, text, link_url):
    template = {
        "object_type": "text",
        "text": text[:200],  # 카카오 텍스트 템플릿 최대 200자
        "link": {"web_url": link_url, "mobile_web_url": link_url},
        "button_title": "목록 보기",
    }
    status, body = http(KAKAO_MEMO_URL,
                        {"template_object": json.dumps(template, ensure_ascii=False)},
                        {"Authorization": f"Bearer {access_token}", **FORM})
    if status != 200:
        raise AlertError(f"카카오 전송 실패 ({status}): {body[:200]}")


# ---------------------------------------------------------------- 텔레그램

def telegram_send(env, text, link_url, detail_url=None, detail_label="상세 보기"):
    """텔레그램 봇으로 전송 (버튼: 원문 상세 + 목록). 실패하면 AlertError."""
    url = f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/sendMessage"
    payload = {"chat_id": env["TELEGRAM_CHAT_ID"], "text": text, "disable_web_page_preview": "true"}
    buttons = [{"text": detail_label, "url": detail_url}] if detail_url else []
    if link_url:
        buttons.append({"text": "목록 보기", "url": link_url})
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": [buttons]})
    status, body = http(url, payload)
    if status != 200:
        raise AlertError(f"텔레그램 전송 실패 ({status}): {body[:200]}")


class Notifier:
    """설정된 채널(카카오, 텔레그램) 모두로 보낸다. 한 곳이라도 성공하면 성공으로 본다."""

    def __init__(self, env, secret_path):
        self.env, self.secret_path = env, secret_path
        self.link = env.get("LINK_URL") or "https://www.onbid.co.kr"
        self.kakao_on = (env.get("KAKAO_ENABLED") or "Y").upper() != "N"
        self.tg_on = bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"))
        self._kakao_token = None
        self.errors = []
        if not (self.kakao_on or self.tg_on):
            raise AlertError("알림 채널이 없습니다 (카카오/텔레그램 설정 확인)")

    def send(self, text, it=None):
        ok = False
        if self.kakao_on:
            try:
                if self._kakao_token is None:
                    try:
                        self._kakao_token = kakao_refresh(self.env, self.secret_path)
                    except AlertError:
                        self.kakao_on = False  # 토큰 갱신 실패 → 이번 실행은 텔레그램만
                        raise
                kakao_send(self._kakao_token, text, self.link)
                ok = True
            except AlertError as e:
                self._fail(str(e))
        if self.tg_on:
            try:
                d = detail_link(it) if it else None
                label = "사건검색 열기" if it and it.get("source") == "court" else "상세 보기"
                telegram_send(self.env, text, self.link, d, label)
                ok = True
            except AlertError as e:
                self._fail(str(e))
        if not ok:
            raise AlertError(" / ".join(self.errors[-2:]) or "알림 전송 실패")

    def _fail(self, msg):
        log(msg)
        if msg not in self.errors:
            self.errors.append(msg)


# ---------------------------------------------------------------- 온비드

def parse_regions(s):
    """'경기도:화성시; 경기도:평택시; 서울특별시' → [(시도, 시군구|None), ...]"""
    out = []
    for part in s.replace("\n", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        sido, _, sgg = part.partition(":")
        out.append((sido.strip(), sgg.strip() or None))
    return out


def onbid_fetch(env, sido, sgg):
    """지역 조건으로 입찰중/입찰예정 부동산 물건 전체(모든 페이지) 조회."""
    items = []
    key = env["ONBID_SERVICE_KEY"]
    # 포털의 Encoding 키(%포함)는 그대로, Decoding 키는 인코딩해서 사용
    key_q = key if "%" in key else urllib.parse.quote(key, safe="")
    pvct_list = [x.strip() for x in env.get("PVCT_TRGT_YN", "N").split(",") if x.strip()]
    for pvct in pvct_list:
        page = 1
        while True:
            params = {
                "pageNo": page, "numOfRows": 100, "resultType": "json",
                "prptDivCd": env.get("PRPT_DIV_CD") or ALL_PRPT,
                "pvctTrgtYn": pvct,
                "lctnSdnm": sido,
            }
            if sgg:
                params["lctnSggnm"] = sgg
            if env.get("DSPS_MTHOD_CD"):
                params["dspsMthodCd"] = env["DSPS_MTHOD_CD"]
            url = f"{ONBID_URL}?serviceKey={key_q}&{urllib.parse.urlencode(params)}"
            status, text = http(url, timeout=60)  # 공공데이터포털이 가끔 수십 초씩 느려짐
            try:
                data = json.loads(text)
            except ValueError:
                raise AlertError(f"온비드 응답 해석 실패 ({status}): {text[:300]}")
            header = data.get("header") or data.get("result") or {}
            code = header.get("resultCode")
            if code == "03":  # NODATA
                break
            if code != "00":
                raise AlertError(f"온비드 오류 ({status}): {text[:300]}")
            body = data.get("body", {})
            got = (body.get("items") or {}).get("item") or []
            if isinstance(got, dict):
                got = [got]
            items.extend(got)
            total = int(body.get("totalCount") or 0)
            if page * 100 >= total or not got:
                break
            page += 1
    return items


def collect(env):
    """모든 관심 지역 조회 → 물건번호별로 가장 가까운 회차 1행만 남김."""
    regions = parse_regions(env.get("REGIONS", ""))
    if not regions:
        raise AlertError("config.env에 REGIONS를 설정하세요. 예) REGIONS=경기도:화성시;경기도:평택시")
    now_key = now_kst().strftime("%Y%m%d%H%M")

    def round_key(it):
        b = str(it.get("cltrBidBgngDt") or "999999999999")
        if b >= now_key:
            return (0, b)  # 앞으로 열릴 회차 우선, 그중 가장 이른 것
        return (1, f"{999999999999 - int(b):012d}" if b.isdigit() else b)  # 없으면 가장 최근 회차

    found = {}
    for sido, sgg in regions:
        rows = onbid_fetch(env, sido, sgg)
        log(f"조회 {sido} {sgg or '(전체)'}: {len(rows)}행(회차 포함)")
        for it in rows:
            no = str(it.get("onbidCltrno"))
            if no not in found or round_key(it) < round_key(found[no]):
                found[no] = it
    if (env.get("COURT_ENABLED") or "Y").upper() != "N":
        try:
            found.update(courtauction.fetch_regions(regions, float(env.get("COURT_REFRESH_HOURS") or 10), log))
        except courtauction.CourtError as e:  # 법원경매가 막혀도 온비드 알림은 계속
            log(str(e))
            WARNINGS.append(str(e))
    return found


def to_int(v):
    try:
        return int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def won(v):
    n = to_int(v)
    return f"{n:,}원" if n is not None else str(v or "-")


def dt(s):
    s = str(s or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}" if len(s) >= 12 else s


ONBID_DTL = "https://www.onbid.co.kr/op/cltrpbancinf/cltrdtl/CltrDtlController/mvmnCltrDtl.do"
COURT_CASE_SRCH = "https://www.courtauction.go.kr/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ159M00.xml"


def detail_link(it):
    """물건 원문 링크. 온비드는 상세화면 직접, 법원경매는 상세 직접 링크가 없어 경매사건검색 화면."""
    if it.get("source") == "court":
        return COURT_CASE_SRCH
    q = {"cltrPrptDivCd": it.get("prptDivCd"), "onbidCltrno": it.get("onbidCltrno"),
         "onbidPbancNo": it.get("onbidPbancNo"), "pbctNo": it.get("pbctNo"), "pbctCdtnNo": it.get("pbctCdtnNo")}
    if not all(q.values()):
        return None
    return f"{ONBID_DTL}?{urllib.parse.urlencode(q)}"


def source_name(it):
    return "법원경매" if it.get("source") == "court" else "온비드"


def format_item(it, solar=None, title=None):
    title = title or f"{source_name(it)} 신규"
    name = (it.get("onbidCltrNm") or "").strip()
    if len(name) > 45:
        name = name[:44] + "…"
    lines = [
        f"[{title}] {it.get('cltrUsgSclsCtgrNm') or ''}",
        name,
        f"최저 {won(it.get('lowstBidPrcIndctCont'))} / 감정 {won(it.get('apslEvlAmt'))}",
        f"입찰 {dt(it.get('cltrBidBgngDt'))[2:]} ~ {dt(it.get('cltrBidEndDt'))[2:]}",
    ]
    if solar and solar.get("best") is not None:
        lines.append(f"{solar['type']} · {solar['label']} (여유 {solar['best']:,}kW, {solar['levelName']})")
        lu = solar.get("landuse") or {}
        if lu.get("grade") in ("pass", "cond", "none", "error"):
            why = ", ".join((lu.get("reasons") or lu.get("favorable") or [])[:2])
            lines.append(f"용도 {lu['label']}" + (f": {why}" if why else ""))
        sb = solar.get("setback") or {}
        if sb.get("grade") in ("ok", "violate"):
            lines.append(f"{sb['label']}: {sb.get('why', '')}")
    lines.append(f"{'사건번호' if it.get('source') == 'court' else '관리번호'} {it.get('cltrMngNo')}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 태양광 판정

SOLAR_PASS = ("good", "ok")


def solar_ok(sol):
    """알림/화면 '통과' 기준: 계통 여유 통과 + 용도지역 1차 제외 아님."""
    return bool(sol) and sol.get("grade") in SOLAR_PASS and (sol.get("landuse") or {}).get("grade") != "exclude"


NON_SOLAR_JIMOK = ("도로", "구거", "하천", "유지", "제방", "묘지", "철도용지", "수도용지", "공원", "학교용지")


def solar_type(it):
    """태양광 대상 여부: 토지 / 지붕형(공장·창고). 대상 아니면 None."""
    if it.get("cltrUsgMclsCtgrNm") == "토지":
        if (it.get("cltrUsgSclsCtgrNm") or "") in NON_SOLAR_JIMOK:
            return None
        return "토지"
    scls = it.get("cltrUsgSclsCtgrNm") or ""
    if "공장" in scls or "창고" in scls:
        return "지붕형"
    return None


def evaluate_solar(env, kp, it):
    """대상 물건의 한전 계통 여유용량 판정. 대상 아니면 None, 한전 오류면 grade='error'."""
    typ = solar_type(it)
    if not typ:
        return None
    min_kw = int(env.get("SOLAR_MIN_KW") or 100)
    good_kw = int(env.get("SOLAR_GOOD_KW") or 300)
    pnu = str(it.get("ltnoPnu") or "")
    if len(pnu) >= 5 and pnu[:5].isdigit():
        codes = (pnu[:2], pnu[2:5])
    else:
        codes = kp.city_code(it.get("lctnSdnm"), it.get("lctnSggnm"))
    emd, li, jibun = kepco.parse_address(it.get("onbidCltrNm"), it.get("lctnEmdNm"))
    if not codes or not emd:
        res = {"grade": "none", "label": "계통 조회불가"}
    else:
        level, rows = kp.lookup(codes[0], codes[1], emd, li, jibun)
        res = kepco.summarize(level, rows, min_kw, good_kw)
    res["type"] = typ
    return res


def evaluate_all(env, found):
    """대상 물건 전체 판정 → {물건번호: 결과}. 한전이 계속 실패하면 나머지는 error로 두고 다음 실행에 재시도."""
    # 중계 서버는 GitHub Actions에서만 사용 (국내 PC에서는 한전·브이월드 직접 호출)
    proxy = env.get("VWORLD_PROXY_URL") if IN_ACTIONS and env.get("VWORLD_PROXY_TOKEN") else None
    kp = kepco.Kepco(env["KEPCO_API_KEY"], proxy, env.get("VWORLD_PROXY_TOKEN"))
    lu = landuse.Landuse(env["VWORLD_API_KEY"], env.get("VWORLD_DOMAIN") or "coldankim.github.io",
                         proxy, env.get("VWORLD_PROXY_TOKEN")) if env.get("VWORLD_API_KEY") else None
    sb = setback.Setback(lu) if lu else None
    lu_fails = 0
    out, fails = {}, 0
    # 한 실행에서 조회에 쓰는 시간 상한 (GitHub Actions 제한시간 안에 끝내기 위해). 남은 건 다음 실행에서 이어서
    deadline = time.time() + float(env.get("SOLAR_TIME_BUDGET_MIN") or 12) * 60
    done = 0
    for no, it in found.items():
        if not solar_type(it):
            continue
        done += 1
        if done % 15 == 0:  # 중간 저장: 중간에 끊겨도 조회한 만큼은 남도록
            kp.save()
            if lu:
                lu.save()
                sb.save()
        over = time.time() > deadline
        if fails >= 3 or over:  # 한전이 계속 실패하거나 시간이 다 되면 나머지는 다음 실행에서 이어서
            out[no] = {"grade": "error", "label": "계통 조회 대기", "type": solar_type(it)}
        else:
            try:
                out[no] = evaluate_solar(env, kp, it)
                fails = 0
            except kepco.KepcoError as e:
                fails += 1
                log(f"한전 조회 실패: {e}")
                out[no] = {"grade": "error", "label": "계통 조회실패", "type": solar_type(it)}
        # 토지만 용도지역 1차 스크리닝 (지붕형은 기존 건물 위라 해당 없음)
        if out[no]["type"] != "토지":
            out[no]["landuse"] = {"grade": "skip", "label": "", "zones": []}
        elif not lu or lu_fails >= 3 or over:
            out[no]["landuse"] = {"grade": "error", "label": "용도 조회 대기", "zones": []}
        else:
            try:
                pnu = landuse.resolve_pnu(lu, it.get("ltnoPnu"), it.get("onbidCltrNm"))
                out[no]["landuse"] = landuse.screen(lu.zones(pnu)) if pnu else                     {"grade": "none", "label": "용도 조회불가", "zones": []}
                out[no]["landuse"]["pnu"] = pnu
                lu_fails = 0
            except landuse.LanduseError as e:
                lu_fails += 1
                log(f"브이월드 조회 실패: {e}")
                out[no]["landuse"] = {"grade": "error", "label": "용도 조회실패", "zones": []}
        out[no]["setback"] = check_setback(sb, it, out[no], over or lu_fails >= 3)
    kp.save()
    if lu:
        lu.save()
        sb.save()
    from collections import Counter
    log(f"태양광 대상 {len(out)}건 판정 (한전 호출 {kp.calls}회, 브이월드 호출 {lu.calls if lu else 0}회): "
        + ", ".join(f"{k} {v}" for k, v in Counter(r["label"] for r in out.values()).items())
        + " / 용도: " + ", ".join(f"{k or '지붕형'} {v}" for k, v in
                                 Counter(r["landuse"]["label"] for r in out.values()).items())
        + " / 이격: " + ", ".join(f"{k} {v}" for k, v in
                                 Counter((r.get("setback") or {}).get("label", "-") for r in out.values()).items()))
    return out


def check_setback(sb, it, sol, skip):
    """이격거리(주거지) 추정. 지붕형은 면제, 계통·용도에서 이미 탈락한 물건은 측정 생략."""
    if sol.get("type") != "토지":
        return {"grade": "exempt", "label": "이격 면제(지붕형)"}
    if not solar_ok(sol):
        return None
    rule = setback.rule_for(sb.rules if sb else setback.load_rules(), it.get("lctnSdnm"), it.get("lctnSggnm"))
    if not rule["limit"]:
        return {"grade": "norule", "label": "이격 기준 없음", "basis": rule["basis"]}
    pnu = (sol.get("landuse") or {}).get("pnu")
    if not sb or skip:
        return {"grade": "error", "label": "이격 확인 대기"}
    if not pnu:
        return {"grade": "none", "label": "이격 측정불가", "why": "필지 번호 없음"}
    try:
        return sb.measure(pnu, rule)
    except setback.SetbackError as e:
        log(f"이격 측정 실패: {e}")
        return {"grade": "error", "label": "이격 확인 대기"}


def keep_previous(solar, seen):
    """이번에 조회 실패한 부분은 지난번 판정으로 채운다."""
    for no, sol in solar.items():
        prev = (seen.get(no) or {}).get("solar") or {}
        if (sol.get("setback") or {}).get("grade") == "error" and (prev.get("setback") or {}).get("grade") not in (None, "error"):
            sol["setback"] = prev["setback"]
        if sol["landuse"]["grade"] == "error" and prev.get("landuse", {}).get("grade") not in (None, "error"):
            sol["landuse"] = prev["landuse"]


# ---------------------------------------------------------------- 기록 / 대시보드

def read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj, indent=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def is_notified(s):
    return bool(s.get("notified"))


def write_dashboard(env, found, seen, run):
    """docs/data.json: 현재 물건 목록 + 최근 실행 기록 (GitHub Pages 화면이 읽음)."""
    prev = read_json(DATA_FILE, {})
    runs = ([run] + prev.get("runs", []))[:200]
    items = prev.get("items", [])
    if found is not None:  # 조회 실패한 실행이면 이전 목록 유지
        items = []
        for no, it in found.items():
            s = seen.get(no, {})
            items.append({
                "no": no,
                "mngNo": it.get("cltrMngNo"),
                "name": (it.get("onbidCltrNm") or "").strip(),
                "usage": it.get("cltrUsgSclsCtgrNm") or it.get("cltrUsgMclsCtgrNm"),
                "prpt": it.get("prptDivNm"),
                "sido": it.get("lctnSdnm"), "sgg": it.get("lctnSggnm"), "emd": it.get("lctnEmdNm"),
                "lowest": to_int(it.get("lowstBidPrcIndctCont")),
                "lowestText": it.get("lowstBidPrcIndctCont"),
                "apsl": to_int(it.get("apslEvlAmt")),
                "begin": it.get("cltrBidBgngDt"), "end": it.get("cltrBidEndDt"),
                "usbd": it.get("usbdNft") or 0,
                "status": it.get("pbctStatNm"),
                "org": it.get("orgNm"),
                "land": it.get("landSqms"), "bld": it.get("bldSqms"),
                "share": it.get("alcYn") == "Y",
                "thumb": it.get("thnlImgUrlAdr"),
                "firstSeen": s.get("at"),
                "notified": is_notified(s),
                "notifiedAt": s.get("notifiedAt"),
                "solar": s.get("solar"),
                "source": it.get("source") or "onbid",
                "note": it.get("note"),
                "link": detail_link(it),
                "court": (it.get("orgNm") or "").split(" ")[0] if it.get("source") == "court" else None,
            })
        items.sort(key=lambda x: (x.get("firstSeen") or "", x.get("begin") or ""), reverse=True)
    write_json(DATA_FILE, {
        "updated": run["at"],
        "regions": env.get("REGIONS", ""),
        "settings": read_json(SETTINGS_FILE, {}),
        "solarMinKw": int(env.get("SOLAR_MIN_KW") or 100),
        "solarGoodKw": int(env.get("SOLAR_GOOD_KW") or 300),
        "items": items,
        "runs": runs,
    })


# ---------------------------------------------------------------- 실행

def in_region(it, region):
    sido, sgg = region
    return it.get("lctnSdnm") == sido and (not sgg or (it.get("lctnSggnm") or "").startswith(sgg))


def silent_new_region_items(env, found, seen):
    """설정에서 새로 추가된 지역의 기존 물건은 알림 없이 기록 (지역 추가 때 알림 폭탄 방지)."""
    cur = parse_regions(env.get("REGIONS", ""))
    prev = [tuple(r) for r in read_json(STATE_FILE, {}).get("regions", [])]
    write_json(STATE_FILE, {"regions": cur})
    added = [r for r in cur if r not in prev] if prev else []
    if not added:
        return set()
    silent = {no for no, it in found.items() if no not in seen
              and any(in_region(it, r) for r in added) and not any(in_region(it, r) for r in prev)}
    log(f"새로 추가된 지역 {added}: 기존 물건 {len(silent)}건은 알림 없이 기록")
    return silent


def pick_targets(found, seen, solar):
    """알림 대상 결정.
    - 처음 보는 물건: 태양광 대상 + 계통 통과면 알림
    - 이미 본 물건: 알림 보낸 적 없고, 지난번엔 부족/조회불가였는데 이번에 통과로 바뀌면 알림 (여유용량 생김)
    - 처음 봤을 때 조회가 밀려(대기) 판정을 못 했던 새 물건: 나중에 통과로 판정되면 신규로 알림
    """
    out = []
    for no, it in found.items():
        sol = solar.get(no)
        if not solar_ok(sol):
            continue
        s = seen.get(no)
        if s is None:
            out.append((no, it, None))
            continue
        if is_notified(s):
            continue
        prev = s.get("solar") or {}
        if prev.get("grade") in ("fail", "none"):
            out.append((no, it, f"{source_name(it)} 계통 여유 생김"))
        elif not s.get("init") and not solar_ok(prev):
            out.append((no, it, None))
    return out


def record(seen, found, solar, stamp, init=False):
    """처음 본 물건 기록 + 태양광 판정 결과 갱신 (한전 오류면 이전 판정 유지)."""
    for no, it in found.items():
        s = seen.get(no)
        if s is None:
            s = seen[no] = {"name": (it.get("onbidCltrNm") or "").strip(), "at": stamp}
            if init:
                s["init"] = True
        sol = solar.get(no)
        if sol and sol["grade"] != "error":
            s["solar"] = sol


def run_alert(env, secret_path, mode):
    """mode: run / init. (found, seen, result) 반환."""
    found = collect(env)
    seen = read_json(SEEN_FILE, None)
    first_time = seen is None
    seen = seen or {}
    solar = evaluate_all(env, found)
    keep_previous(solar, seen)
    new_cnt = sum(1 for no in found if no not in seen)
    stamp = now_kst().strftime("%Y-%m-%d %H:%M")
    silent = silent_new_region_items(env, found, seen)
    if silent:
        record(seen, {no: found[no] for no in silent}, solar, stamp, init=True)
    targets = [] if (mode == "init" or first_time) else pick_targets(found, seen, solar)
    log(f"전체 {len(found)}건 / 신규 {new_cnt}건 / 알림 대상(태양광 통과) {len(targets)}건")
    result = {"total": len(found), "new": new_cnt, "solar": len(solar),
              "pass": sum(1 for r in solar.values() if solar_ok(r)),
              "targets": len(targets), "sent": 0}
    if WARNINGS:
        result["error"] = " / ".join(WARNINGS)
    err = sum(1 for r in solar.values() if r["grade"] == "error")
    lu_err = sum(1 for r in solar.values() if r["landuse"]["grade"] == "error")
    if err or lu_err:  # 실행은 계속하되 화면에 경고로 남김 (해당 물건은 이전 판정 유지, 다음 실행에 재시도)
        result["error"] = " / ".join(x for x in (result.get("error", ""),
                                                  f"한전 계통 조회 못함 {err}건" if err else "",
                                                  f"브이월드 용도 조회 못함 {lu_err}건" if lu_err else "") if x)
        result["error"] += " (지난 판정 유지, 다음 실행에서 자동 재시도)"

    # 첫 실행 / init: 알림 없이 기록만 (알림 폭탄 방지)
    if mode == "init" or first_time:
        record(seen, found, solar, stamp, init=True)
        write_json(SEEN_FILE, seen)
        log(f"초기화: {new_cnt}건을 알림 없이 기록")
        result["mode"] = "init"
        return found, seen, result

    try:
        if targets:
            nt = Notifier(env, secret_path)
            limit = int(env.get("MAX_NOTIFY") or 20)
            for no, it, title in targets[:limit]:
                nt.send(format_item(it, solar[no], title), it)
                seen.setdefault(no, {"name": (it.get("onbidCltrNm") or "").strip(), "at": stamp})
                seen[no].update(notified=True, notifiedAt=stamp)
                seen[no].pop("init", None)
                result["sent"] += 1
            rest = len(targets) - result["sent"]
            if rest > 0:
                nt.send(f"[공매 알림] 태양광 조건 통과 물건이 {rest}건 더 있습니다. 다음 실행 때 이어서 보냅니다.")
            if nt.errors:  # 한쪽 채널만 실패한 경우 화면에 경고로 남김
                WARNINGS.extend(nt.errors)
    finally:
        # 알림 못 보낸 통과 물건은 '처음 보는 물건'으로 남겨 다음 실행 때 재시도
        pending = {no for no, _, _ in targets if not is_notified(seen.get(no, {}))}
        record(seen, {no: it for no, it in found.items() if no not in pending}, solar, stamp)
        write_json(SEEN_FILE, seen)
    log(f"알림 전송 {result['sent']}건")
    return found, seen, result


def print_telegram_chats(env):
    tok = env.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        raise AlertError("TELEGRAM_BOT_TOKEN이 없습니다")
    status, body = http(f"https://api.telegram.org/bot{tok}/getUpdates")
    chats = {}
    for u in (json.loads(body).get("result") or []) if status == 200 else []:
        c = (u.get("message") or u.get("my_chat_member") or {}).get("chat") or {}
        if c.get("id"):
            chats[c["id"]] = c.get("title") or c.get("first_name") or c.get("username")
    if not chats:
        raise AlertError(f"봇에게 온 메시지가 없습니다. 텔레그램에서 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요. ({status})")
    for cid, name in chats.items():
        print(f"TELEGRAM_CHAT_ID={cid}   ({name})")


def main():
    ap = argparse.ArgumentParser(description="온비드 신규 공매물건 카톡 알림")
    ap.add_argument("--dry-run", action="store_true", help="조회만 (전송/기록 안 함)")
    ap.add_argument("--init", action="store_true", help="현재 물건을 알림 없이 기록만")
    ap.add_argument("--test-kakao", action="store_true", help="카카오 테스트 메시지 전송")
    ap.add_argument("--auth", metavar="CODE", help="카카오 인가코드로 refresh_token 재발급")
    ap.add_argument("--telegram-chat-id", action="store_true", help="봇에게 온 메시지로 텔레그램 chat_id 확인")
    args = ap.parse_args()

    try:
        env, secret_path = load_settings()
    except AlertError as e:
        sys.exit(str(e))
    # 서버(아이비호스팅)에서는 화면 데이터를 웹 폴더에 바로 쓴다 (예: SOLAR_DATA_FILE=../public_html/data.json)
    global DATA_FILE
    if env.get("SOLAR_DATA_FILE"):
        DATA_FILE = os.path.normpath(os.path.join(BASE_DIR, env["SOLAR_DATA_FILE"]))

    try:
        if args.auth:
            return kakao_auth_code(env, secret_path, args.auth)
        if args.test_kakao:  # 이름은 유지, 설정된 모든 채널(카카오·텔레그램)로 테스트
            nt = Notifier(env, secret_path)
            nt.send("공매 알림 테스트 메시지입니다.")
            return log("테스트 메시지 전송 완료" + (f" (일부 실패: {' / '.join(nt.errors)})" if nt.errors else ""))
        if args.telegram_chat_id:
            return print_telegram_chats(env)
        if args.dry_run:
            found = collect(env)
            seen = read_json(SEEN_FILE, {})
            solar = evaluate_all(env, found)
            keep_previous(solar, seen)
            targets = pick_targets(found, seen, solar)
            log(f"전체 {len(found)}건 / 알림 대상 {len(targets)}건")
            for no, it, title in targets[:30]:
                print("-" * 40)
                print(format_item(it, solar[no], title))
            return
    except AlertError as e:
        sys.exit(str(e))

    # 일반 실행 / init: 성공이든 실패든 실행 기록을 대시보드에 남긴다
    lock = acquire_lock()
    if lock is None:
        log("이미 실행 중이라 이번 실행은 건너뜀")
        return
    mode = "init" if args.init else "run"
    run = {"at": now_kst().strftime("%Y-%m-%d %H:%M"), "mode": mode}
    found = seen = None
    error = None
    try:
        found, seen, result = run_alert(env, secret_path, mode)
        run.update(result)
    except AlertError as e:
        error = str(e)
    except Exception as e:  # 예상 못한 오류도 기록은 남긴다
        error = f"{type(e).__name__}: {e}"
    if error:
        run["error"] = error
        log(f"오류: {error}")
    if seen is None:
        seen = read_json(SEEN_FILE, {})
    write_dashboard(env, found, seen, run)
    if error:
        sys.exit(1)


if __name__ == "__main__":
    main()
