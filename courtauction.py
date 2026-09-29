"""
법원경매정보(courtauction.go.kr) 부동산 물건 조회

공식 API가 없어 사이트 화면이 쓰는 JSON 요청을 그대로 사용한다 (로그인 불필요).
사이트 부담을 줄이기 위해: 요청 간 2초 간격, 결과는 COURT_REFRESH_HOURS 동안 재사용, 관심 지역만 조회.
이용약관상 개인의 입찰 참고용으로만 쓰고, 화면에는 출처(법원경매정보)를 표시한다.

조회 결과는 온비드 물건과 같은 모양(dict 키)으로 바꿔서 기존 판정·알림·화면 흐름을 그대로 탄다.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = "https://www.courtauction.go.kr/pgj"
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "court_cache.json")
HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36",
    "Referer": "https://www.courtauction.go.kr/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ151F00.xml",
    "SC-Pgm-Id": "PGJ151M01",
}
PAGE_SIZE = 50  # 사이트 최대
GAP = 2.0       # 요청 간격(초)

SEARCH_KEYS = (
    "rletDspslSpcCondCd bidDvsCd mvprpRletDvsCd cortAuctnSrchCondCd rprsAdongSdCd rprsAdongSggCd "
    "rprsAdongEmdCd rdnmSdCd rdnmSggCd rdnmNo mvprpDspslPlcAdongSdCd mvprpDspslPlcAdongSggCd "
    "mvprpDspslPlcAdongEmdCd rdDspslPlcAdongSdCd rdDspslPlcAdongSggCd rdDspslPlcAdongEmdCd cortOfcCd "
    "jdbnCd execrOfcDvsCd lclDspslGdsLstUsgCd mclDspslGdsLstUsgCd sclDspslGdsLstUsgCd cortAuctnMbrsId "
    "aeeEvlAmtMin aeeEvlAmtMax lwsDspslPrcRateMin lwsDspslPrcRateMax flbdNcntMin flbdNcntMax "
    "objctArDtsMin objctArDtsMax mvprpArtclKndCd mvprpArtclNm mvprpAtchmPlcTypCd notifyLoc lafjOrderBy "
    "pgmId csNo cortStDvs statNum bidBgngYmd bidEndYmd dspslDxdyYmd fstDspslHm scndDspslHm thrdDspslHm "
    "fothDspslHm dspslPlcNm lwsDspslPrcMin lwsDspslPrcMax grbxTypCd gdsVendNm fuelKndCd carMdyrMax "
    "carMdyrMin carMdlNm sideDvsCd"
).split()

# 용도 중분류 코드 → 온비드와 같은 이름 (태양광 대상 판정에 사용)
MCLS_NAME = {"10100": "토지", "20100": "주거용건물", "21100": "상가용및업무용건물", "22100": "산업용및기타특수용건물"}


class CourtError(Exception):
    pass


class Court:
    def __init__(self):
        self._last = 0.0
        self.calls = 0

    def _post(self, path, body):
        gap = GAP - (time.time() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.time()
        self.calls += 1
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"), headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise CourtError(f"법원경매 접속 실패: {e}")
        data = d.get("data") or {}
        if data.get("ipcheck") is False or "차단" in str(d.get("message")):
            raise CourtError(f"법원경매 접속 차단: {d.get('message')}")
        return data

    def region_codes(self, sido, sgg):
        """시도명/시군구명 → [(시도코드, 시군구코드|'')]. 공고 중인 지역만 목록에 나온다.
        '화성시'는 '화성시', '화성시 만세구'처럼 같은 이름으로 시작하는 코드를 모두 포함."""
        base = {"pbancMidYn": "Y", "srchDvsCd": "B", "pbancDvsCd": "FB"}
        sds = self._post("/pgj002/selectAdongSdLst.on", base).get("adongSdLst") or []
        sd = next((x["code"] for x in sds if x.get("name") == sido), None)
        if not sd:
            return []  # 해당 시도에 공고 중인 물건 없음
        if not sgg:
            return [(sd, "")]
        sggs = self._post("/pgj002/selectAdongSggLst.on", {**base, "adongSdCd": sd}).get("adongSggLst") or []
        codes = sorted({x["code"] for x in sggs if (x.get("name") or "").split(" ")[0] == sgg.split(" ")[0]
                        and (x.get("name") == sgg or " " not in sgg)})
        return [(sd, c) for c in codes]

    def search(self, sd, sgg):
        """부동산 매각 물건 전체(모든 페이지)."""
        info = {k: "" for k in SEARCH_KEYS}
        info.update(mvprpRletDvsCd="00031R", cortAuctnSrchCondCd="0004601", rprsAdongSdCd=sd,
                    rprsAdongSggCd=sgg, pgmId="PGJ151M01", cortStDvs="2")
        rows, page = [], 1
        while True:
            body = {"dma_pageInfo": {"pageNo": page, "pageSize": PAGE_SIZE, "bfPageNo": "", "startRowNo": 1,
                                     "totalCnt": "", "totalYn": "Y", "groupTotalCount": ""},
                    "dma_srchGdsDtlSrchInfo": info}
            data = self._post("/pgjsearch/searchControllerMain.on", body)
            got = data.get("dlt_srchResult") or []
            rows.extend(got)
            total = int((data.get("dma_pageInfo") or {}).get("totalCnt") or 0)
            if not got or page * PAGE_SIZE >= total or page >= 40:
                break
            page += 1
        return rows


def _area(s):
    m = re.search(r"([\d,.]+)\s*㎡", s or "")
    try:
        return float(m.group(1).replace(",", "")) if m else None
    except ValueError:
        return None


def to_items(rows):
    """법원경매 행(목적물 단위) → 온비드 모양 물건(매물 단위: 법원+사건번호+매물번호)."""
    items = {}
    for x in sorted(rows, key=lambda r: (r.get("docid") or "", int(r.get("mokmulSer") or 0))):
        key = f"C{x.get('boCd')}{x.get('saNo')}-{x.get('maemulSer')}"
        if key in items:
            continue  # 같은 매물의 두 번째 이후 목적물(건물 등)은 첫 목적물 주소로 대표
        if (x.get("lclsUtilCd") or "")[:1] not in ("1", "2"):
            continue  # 자동차·중기 등 부동산 아닌 것 제외
        mcls = x.get("mclsUtilCd") or ""
        land = mcls == "10100"
        usage = (x.get("jimokList") or x.get("dspslUsgNm")) if land else x.get("dspslUsgNm")
        day, hh = x.get("maeGiil") or "", (x.get("maeHh1") or "").zfill(4)
        items[key] = {
            "source": "court",
            "onbidCltrno": key,
            "cltrMngNo": f"{x.get('srnSaNo')} [{x.get('maemulSer')}]",
            "onbidCltrNm": f"{(x.get('printSt') or '').strip()} {usage or ''}".strip(),
            "cltrUsgMclsCtgrNm": MCLS_NAME.get(mcls, x.get("dspslUsgNm") or ""),
            "cltrUsgSclsCtgrNm": usage,
            "prptDivNm": "법원경매",
            "lowstBidPrcIndctCont": x.get("minmaePrice"),
            "apslEvlAmt": x.get("gamevalAmt"),
            "cltrBidBgngDt": day + hh if day else None,
            "cltrBidEndDt": day + hh if day else None,
            "usbdNft": int(x.get("yuchalCnt") or 0),
            "pbctStatNm": "매각기일 " + (f"{day[4:6]}.{day[6:8]}" if len(day) == 8 else ""),
            "orgNm": f"{x.get('jiwonNm') or ''} {x.get('jpDeptNm') or ''} {x.get('tel') or ''}".strip(),
            "lctnSdnm": x.get("hjguSido"),
            "lctnSggnm": x.get("bgPlaceSigu") or x.get("hjguSigu"),
            "lctnEmdNm": x.get("hjguDong"),
            "landSqms": _area(x.get("areaList")) if land else None,
            "bldSqms": None,
            "alcYn": "Y" if "지분" in (x.get("mulBigo") or "") else "N",
            "ltnoPnu": None,
            "note": (x.get("mulBigo") or "").strip()[:80],
        }
    return items


def fetch_regions(regions, refresh_hours, log):
    """관심 지역 전체 조회. 최근 조회 결과가 refresh_hours 이내면 재사용 (사이트 부담 최소화)."""
    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    sig = json.dumps(regions, ensure_ascii=False)
    if cache.get("regions") == sig and time.time() - cache.get("t", 0) < refresh_hours * 3600:
        log(f"법원경매: {int((time.time() - cache['t']) / 60)}분 전 조회 결과 재사용 ({len(cache['items'])}건)")
        return cache["items"]
    court = Court()
    items = {}
    for sido, sgg in regions:
        codes = court.region_codes(sido, sgg)
        rows = []
        for sd, sg in codes:
            rows.extend(court.search(sd, sg))
        got = to_items(rows)
        items.update(got)
        log(f"법원경매 조회 {sido} {sgg or '(전체)'}: {len(rows)}행 → {len(got)}건 (요청 {court.calls}회)")
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"t": time.time(), "regions": sig, "items": items}, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)
    return items
