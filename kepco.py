"""
한전 전력데이터 개방포털 - 분산전원연계정보(계통 여유용량) 조회

주소(법정동 시도/시군구 코드 + 읍면동/리/지번)로 변전소·주변압기·배전선로(DL) 여유용량(kW)을 조회한다.
한전 데이터는 지번 단위로 없는 경우가 많아서 지번 → 리 → 읍면동 순서로 넓혀 가며 조회하고,
어느 단위에서 찾았는지(level)를 함께 돌려준다.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

DGEN_URL = "https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do"
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kepco_cache.json")
CACHE_TTL = 20 * 3600  # 같은 주소는 하루 한 번만 조회
MIN_INTERVAL = 1.0  # 초. 한전 API 연속 호출 제한 대응

LEVEL_NAME = {"jibun": "지번", "li": "리 단위", "emd": "읍면동 단위"}


class KepcoError(Exception):
    pass


class Kepco:
    def __init__(self, api_key):
        self.api_key = api_key
        self.cache = {}
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, encoding="utf-8") as f:
                self.cache = json.load(f)
        self.calls = 0
        self._last = 0.0

    def save(self):
        now = time.time()
        self.cache = {k: v for k, v in self.cache.items() if now - v["t"] < CACHE_TTL}
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)

    def _query(self, metro, city, emd, li=None, jibun=None):
        """한 번 조회. 데이터 없으면 [] (한전은 404로 응답), 통신 오류면 KepcoError."""
        key = "|".join([metro, city, emd, li or "", jibun or ""])
        hit = self.cache.get(key)
        if hit and time.time() - hit["t"] < CACHE_TTL:
            return hit["rows"]
        params = {"metroCd": metro, "cityCd": city, "addrLidong": emd,
                  "apiKey": self.api_key, "returnType": "json"}
        if li:
            params["addrLi"] = li
        if jibun:
            params["addrJibun"] = jibun
        url = f"{DGEN_URL}?{urllib.parse.urlencode(params)}"
        rows = self._get(url)
        self.cache[key] = {"t": time.time(), "rows": rows}
        return rows

    def _get(self, url):
        # 한전은 연속 호출이 빠르면 401을 돌려준다 → 호출 간격을 두고, 401이면 쉬었다가 재시도
        for wait in (0, 5, 15, 30):
            if wait:
                time.sleep(wait)
            gap = MIN_INTERVAL - (time.time() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.time()
            self.calls += 1
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    return json.loads(r.read().decode("utf-8")).get("data") or []
            except urllib.error.HTTPError as e:
                if e.code == 404:  # 해당 주소 데이터 없음
                    return []
                if e.code != 401:
                    raise KepcoError(f"한전 API 오류 {e.code}: {e.read()[:200]!r}")
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                raise KepcoError(f"한전 API 접속 실패: {e}")
        raise KepcoError("한전 API 401 반복 (호출 제한 또는 인증키 확인 필요)")

    def lookup(self, metro, city, emd, li=None, jibun=None):
        """지번 → 리 → 읍면동 순서로 조회. 반환: (level, rows) 또는 (None, [])"""
        # 시가 구로 나뉜 곳(예: 화성시 591/593)은 한전이 옛 시 코드(590)를 쓰는 경우가 있어 둘 다 시도
        cities = [city] + ([city[:2] + "0"] if city[2] != "0" else [])
        # 한전은 옛 행정구역 이름을 쓰는 곳이 있다 (예: 향남읍 → 향남면)
        emds = [emd] + ([emd[:-1] + "면"] if emd.endswith("읍") else [])
        steps = []
        if jibun:
            steps.append(("jibun", li, jibun))
        if li:
            steps.append(("li", li, None))
        steps.append(("emd", None, None))
        for level, l, j in steps:
            for c in cities:
                for e in emds:
                    rows = self._query(metro, c, e, l, j)
                    if rows:
                        return level, rows
        return None, []

    def city_code(self, sido, sgg):
        """PNU가 없는 물건용: 시도명/시군구명 → 법정동 (시도코드, 시군구코드). 못 찾으면 None."""
        codes = self.cache.get("__codes__")
        if not codes or time.time() - codes["t"] > 30 * 86400:
            base = "https://bigdata.kepco.co.kr/openapi/v1/commonCode.do"
            rows = {}
            for ty in ("lglDngMetroCd", "lglDngCityCd"):
                q = urllib.parse.urlencode({"codeTy": ty, "apiKey": self.api_key, "returnType": "json"})
                rows[ty] = self._get(f"{base}?{q}")
            codes = {"t": time.time(), "rows": rows}
            self.cache["__codes__"] = codes
        metro = next((r["code"] for r in codes["rows"]["lglDngMetroCd"] if r.get("codeNm") == sido), None)
        if not metro:
            return None
        cands = [r for r in codes["rows"]["lglDngCityCd"] if r.get("uppoCd") == metro]
        for name in (sgg, (sgg or "").split(" ")[0]):  # '화성시 만세구' 없으면 '화성시'
            hit = next((r["code"] for r in cands if r.get("codeNm") == name), None)
            if hit:
                return metro, hit
        return None


def to_kw(v):
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def summarize(level, rows, min_kw, good_kw):
    """rows(선로별) → 판정. 한 선로의 가용량 = min(변전소, 변압기, DL 여유)."""
    if not rows:
        return {"grade": "none", "label": "계통 조회불가"}
    lines = []
    for r in rows:
        v1, v2, v3 = to_kw(r.get("vol1")), to_kw(r.get("vol2")), to_kw(r.get("vol3"))
        lines.append({"avail": min(v1, v2, v3), "subst": r.get("substNm"), "dl": r.get("dlNm"),
                      "vol1": v1, "vol2": v2, "vol3": v3})
    lines.sort(key=lambda x: x["avail"], reverse=True)
    best, worst = lines[0], lines[-1]
    ok = sum(1 for x in lines if x["avail"] >= min_kw)
    if best["avail"] >= good_kw:
        grade, label = "good", f"계통 {good_kw}kW+"
    elif best["avail"] >= min_kw:
        grade, label = "ok", f"계통 {min_kw}kW+"
    else:
        grade, label = "fail", "계통 부족"
    return {
        "grade": grade, "label": label, "level": level, "levelName": LEVEL_NAME.get(level, ""),
        "best": best["avail"], "worst": worst["avail"], "lines": len(lines), "okLines": ok,
        "subst": best["subst"], "dl": best["dl"],
        "vol": [best["vol1"], best["vol2"], best["vol3"]],
    }


ADDR_RE = re.compile(r"(\S+[읍면동가])\s+(?:(\S+리)\s+)?(산?\s?\d+(?:-\d+)?)")


def parse_address(name, emd_hint=None):
    """온비드 물건명(주소)에서 읍면동, 리, 지번 추출. 산 지번은 한전 지번 조회가 안 되므로 None."""
    m = ADDR_RE.search(name or "")
    if not m:
        return emd_hint, None, None
    emd, li, jibun = m.groups()
    jibun = jibun.replace(" ", "")
    if jibun.startswith("산"):
        jibun = None  # 산65와 65는 다른 필지라 '산'만 떼고 조회하면 엉뚱한 땅이 됨
    return emd or emd_hint, li, jibun
