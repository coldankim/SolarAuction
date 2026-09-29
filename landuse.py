"""
브이월드 토지이용계획(용도지역·지구) 조회 + 태양광 1차 스크리닝

- 온비드 PNU는 11번째 자리(산 여부)가 0=일반/1=산 이라 표준(1=일반/2=산)으로 바꿔서 조회한다.
- 여러 필지 묶음 물건(지번 0000)은 주소 검색으로 첫 필지 PNU를 찾는다.
- 판정은 '원천적으로 불가능한 땅만 1차로 걸러내는' 보수적 기준이다. 조례(이격거리 등)는 사람이 최종 확인.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

LANDUSE_URL = "https://api.vworld.kr/ned/data/getLandUseAttr"
SEARCH_URL = "https://api.vworld.kr/req/search"
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "landuse_cache.json")
CACHE_TTL = 7 * 86400  # 용도지역은 자주 안 바뀌므로 일주일 보관

# ---- 판정 규칙 (지역지구명에 아래 글자가 들어가면 적용. 필요하면 여기만 고치면 됨) ----
# 태양광 개발이 원천적으로 불가능하거나 사실상 불가능한 곳 → 1차 제외
EXCLUDE = [
    "개발제한구역", "자연환경보전지역", "공익용산지", "농업진흥구역",
    "상수원보호구역", "문화유산구역", "문화재구역", "문화유산보호구역", "문화재보호구역",
    "습지보호지역", "생태ㆍ경관보전지역", "생태·경관보전지역", "야생생물보호구역",
    "백두대간보호지역",
]
# 가능은 하지만 허가·협의·면적 제한 등 확인이 필요한 곳 → 조건부 (알림은 보냄)
CONDITIONAL = [
    "임업용산지", "농업보호구역", "보전관리지역", "보전녹지지역", "생산녹지지역", "자연녹지지역",
    "농림지역", "비행안전", "군사시설보호", "통제보호구역", "제한보호구역",
    "개발행위허가제한지역", "역사문화환경보존지역", "경관지구", "수변구역",
    "주거지역", "상업지역", "공업지역", "도시개발구역", "택지개발지구", "공공주택지구",
    "하천구역", "소하천구역", "토석채취제한지역", "특별대책지역",
]
# 태양광 입지로 무난한 용도지역 (표시용)
FAVORABLE = ["계획관리지역", "생산관리지역"]


class LanduseError(Exception):
    pass


def to_std_pnu(pnu):
    """온비드 PNU(산 여부 0/1) → 표준 PNU(1/2)."""
    pnu = str(pnu or "")
    if len(pnu) != 19 or not pnu.isdigit():
        return None
    return pnu[:10] + {"0": "1", "1": "2"}.get(pnu[10], pnu[10]) + pnu[11:]


class Landuse:
    def __init__(self, api_key, domain, proxy_url=None, proxy_token=None):
        self.key = api_key
        self.domain = domain
        # 해외 서버(GitHub Actions)는 브이월드가 막혀 있어 국내 호스팅의 중계 파일(relay/vworld.php)을 거친다
        self.proxy_url = proxy_url
        self.proxy_token = proxy_token
        self.cache = {}
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, encoding="utf-8") as f:
                self.cache = json.load(f)
        self.calls = 0

    def save(self):
        now = time.time()
        self.cache = {k: v for k, v in self.cache.items() if now - v["t"] < CACHE_TTL}
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)

    def _get(self, url, params):
        params = {**params, "key": self.key, "domain": self.domain}
        if self.proxy_url:
            path = url.split("api.vworld.kr/", 1)[1]
            params = {"path": path, "token": self.proxy_token, **params}
            url = self.proxy_url
        # 호스팅 보안 필터가 Accept 헤더 없는 요청을 막으므로 명시한다
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                     headers={"Accept": "application/json", "User-Agent": "SolarAuction/1.0"})
        last = ""
        for wait in (0, 5, 15):  # 연결 거부·끊김은 잠시 뒤 재시도
            if wait:
                time.sleep(wait)
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code not in (429, 502, 503, 504):
                    raise LanduseError(f"브이월드 오류 HTTP {e.code}")
                last = f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = str(e)
            except ValueError as e:
                raise LanduseError(f"브이월드 응답 해석 실패: {e}")
        raise LanduseError(f"브이월드 접속 실패 (재시도 3회): {last}")

    def zones(self, pnu):
        """표준 PNU → [{name, code, rel}] (rel: 포함/저촉/접함)."""
        hit = self.cache.get(pnu)
        if hit and time.time() - hit["t"] < CACHE_TTL:
            return hit["zones"]
        d = self._get(LANDUSE_URL, {"pnu": pnu, "format": "json", "numOfRows": 100, "pageNo": 1})
        body = d.get("landUses") or d.get("response") or {}
        code = body.get("resultCode")
        if code and code not in ("", "OK", "NO_DATA"):
            raise LanduseError(f"브이월드 오류: {code} {body.get('resultMsg')}")
        fields = body.get("field") or []
        if isinstance(fields, dict):
            fields = [fields]
        zones = [{"name": f.get("prposAreaDstrcCodeNm"), "code": f.get("prposAreaDstrcCode"),
                  "rel": f.get("cnflcAtNm")} for f in fields]
        self.cache[pnu] = {"t": time.time(), "zones": zones}
        return zones

    def pnu_by_address(self, address):
        """주소 → 표준 PNU (브이월드 주소 검색, 첫 결과)."""
        key = "addr|" + address
        hit = self.cache.get(key)
        if hit and time.time() - hit["t"] < CACHE_TTL:
            return hit["pnu"]
        d = self._get(SEARCH_URL, {"service": "search", "request": "search", "version": "2.0",
                                   "size": 1, "page": 1, "query": address, "type": "address",
                                   "category": "parcel", "format": "json", "errorformat": "json"})
        r = d.get("response") or {}
        items = ((r.get("result") or {}).get("items") or []) if r.get("status") == "OK" else []
        pnu = items[0].get("id") if items else None
        self.cache[key] = {"t": time.time(), "pnu": pnu}
        return pnu


FIRST_PARCEL_RE = re.compile(r"^(.*?\S+[읍면동가리]\s+산?\s?\d+(?:-\d+)?)")


def resolve_pnu(lu, onbid_pnu, name):
    """온비드 물건 → 조회할 표준 PNU. 지번이 0000(여러 필지)이면 주소 첫 필지로 검색."""
    std = to_std_pnu(onbid_pnu)
    if std and std[11:] != "00000000":
        return std
    m = FIRST_PARCEL_RE.match((name or "").strip())
    return lu.pnu_by_address(m.group(1)) if m else None


def screen(zones):
    """용도지역·지구 목록 → 1차 스크리닝 결과. '접함'은 필지 경계에 닿기만 한 것이라 판정에서 뺀다."""
    if not zones:
        return {"grade": "none", "label": "용도 조회불가", "zones": []}
    inside = [z for z in zones if z.get("rel") != "접함" and z.get("name")]
    names = list(dict.fromkeys(z["name"] for z in inside))
    excl = [n for n in names if any(k in n for k in EXCLUDE)]
    cond = [n for n in names if n not in excl and any(k in n for k in CONDITIONAL)]
    fav = [n for n in names if n in FAVORABLE]
    if excl:
        grade, label = "exclude", "1차 제외"
    elif cond:
        grade, label = "cond", "조건부"
    else:
        grade, label = "pass", "1차 통과"
    return {"grade": grade, "label": label, "reasons": excl or cond, "favorable": fav, "zones": names}
