"""Official SSE/SZSE daily PCF, retaining dates and distinct cash items."""

import math
import re
from xml.etree import ElementTree

import pandas as pd
import requests

from simtradedata.fetchers.base_fetcher import BaseFetcher

INFO_FIELDS = [
    "report_unit", "cash_balance", "max_cash_ratio", "nav_percu", "nav_pre",
    "allot_max", "redeem_max", "publish",
]


def parse_pcf(content: bytes, symbol: str) -> pd.DataFrame:
    code, market = symbol.split(".")
    szse = market == "SZ"
    root = ElementTree.fromstring(content)
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    if root.findtext("SecurityID" if szse else "FundInstrumentID") != code:
        raise ValueError("PCF fund code differs from request")

    def number(node, tag, required=False):
        text = node.findtext(tag)
        if text is None or not text.strip():
            if required:
                raise ValueError(f"Missing PCF field: {tag}")
            return None
        value = float(text)
        if not math.isfinite(value):
            raise ValueError(f"Non-finite PCF field: {tag}")
        return value

    def date(tag):
        text = root.findtext(tag, "")
        if not re.fullmatch(r"\d{8}", text):
            raise ValueError(f"Invalid PCF date: {tag}")
        return pd.to_datetime(text, format="%Y%m%d").strftime("%Y-%m-%d")

    info = {"etf_code": symbol, "date": date("TradingDay"), "pre_date": date("PreTradingDay")}
    if info["pre_date"] >= info["date"]:
        raise ValueError("PCF prior trading date must precede trading date")
    mapping = {
        "report_unit": "CreationRedemptionUnit", "nav_percu": "NAVperCU", "nav_pre": "NAV",
        "max_cash_ratio": "MaxCashRatio", "allot_max": "CreationLimit", "redeem_max": "RedemptionLimit",
        "cash_balance": "CashComponent" if szse else "PreCashComponent",
        "estimated_cash_component": "EstimateCashComponent" if szse else "EstimatedCashComponent",
    }
    for field, tag in mapping.items():
        value = number(root, tag, required=field in {"report_unit", "nav_percu", "nav_pre"})
        if value is not None:
            info[field] = value
    if info["report_unit"] <= 0 or not info["report_unit"].is_integer() or min(info["nav_percu"], info["nav_pre"]) <= 0:
        raise ValueError("Invalid PCF unit or net asset value")
    if "max_cash_ratio" in info and not 0 <= info["max_cash_ratio"] <= 1:
        raise ValueError("Invalid PCF cash substitution ratio")
    publish = root.findtext("Publish" if szse else "PublishIOPVFlag")
    if publish in {"Y", "N", "0", "1"}:
        info["publish"] = int(publish in {"Y", "1"})

    rows = []
    path = "./Components/Component" if szse else "./ComponentList/Component"
    for component in root.findall(path):
        security = component.findtext("UnderlyingSecurityID" if szse else "InstrumentID", "").strip()
        source = component.findtext("UnderlyingSecurityIDSource" if szse else "UnderlyingSecurityID", "").strip()
        if not security or not source:
            raise ValueError("PCF component lacks code or market")
        quantity = number(component, "ComponentShare" if szse else "Quantity", required=True)
        if quantity < 0:
            raise ValueError("Negative PCF component quantity")
        row = {**info, "code": security, "market_code": source,
               "security_name": component.findtext("UnderlyingSymbol" if szse else "InstrumentName", ""),
               "code_num": quantity, "is_cash_component": szse and security == "159900"}
        suffix = {"101": "SS", "102": "SZ"}.get(source)
        row["symbol"] = f"{security}.{suffix}" if suffix and re.fullmatch(r"\d{6}", security) and not row["is_cash_component"] else None
        row["substitution_flag"] = component.findtext("SubstituteFlag" if szse else "SubstitutionFlag", "")
        for field, tag in {
            "creation_premium_ratio": "PremiumRatio" if szse else "CreationPremiumRate",
            "redemption_discount_ratio": "DiscountRatio" if szse else "RedemptionDiscountRate",
            "creation_cash": "CreationCashSubstitute" if szse else "SubstitutionCashAmount",
            "redemption_cash": "RedemptionCashSubstitute" if szse else "SubstitutionCashAmount",
        }.items():
            value = number(component, tag)
            if value is not None:
                row[field] = value
        rows.append(row)
    count = number(root, "TotalRecordNum" if szse else "RecordNumber", required=True)
    if not rows or count != len(rows):
        raise ValueError("PCF component count mismatch")
    result = pd.DataFrame(rows)
    if result.duplicated(["code", "market_code"]).any():
        raise ValueError("Duplicate PCF component")
    return result


class EtfPcfFetcher(BaseFetcher):
    source_name = "etf_pcf"

    def __init__(self, timeout=20):
        super().__init__()
        self._timeout = timeout
        self._session = None

    def _do_login(self):
        self._session = requests.Session()

    def _do_logout(self):
        self._session.close()
        self._session = None

    def _get(self, url, **kwargs):
        response = self._session.get(url, timeout=self._timeout, **kwargs)
        response.raise_for_status()
        return response

    def fetch_pcf(self, symbol, date=None):
        if not re.fullmatch(r"(?:5\d{5}\.SS|15\d{4}\.SZ)", symbol):
            raise ValueError("PCF requires a Shanghai or Shenzhen ETF code")
        today = pd.Timestamp.now(tz="Asia/Shanghai").date()
        requested = pd.Timestamp(date).date() if date is not None else None
        if requested is not None and requested > today:
            raise ValueError("Cannot request future PCF")
        self.login()
        result = self._make_request(self._download, symbol, requested, today)
        return result if result is not None else pd.DataFrame()

    def _download(self, symbol, requested, today):
        code, market = symbol.split(".")
        if market == "SZ":
            end = requested or today
            index = self._get(
                "https://www.szse.cn/api/report/ShowReport/data",
                params={"SHOWTYPE": "JSON", "CATALOGID": "sgshqd", "TABKEY": "tab1", "txtJCorDH": code,
                        "txtStart": (end - pd.Timedelta(days=365)).isoformat(), "txtEnd": end.isoformat()},
                headers={"Referer": "https://www.szse.cn/"},
            ).json()
            dates = [value for value in re.findall(rf"pcf_{code}_(\d{{8}})", str(index))
                     if value <= end.strftime("%Y%m%d")]
            if not dates:
                raise ValueError("No PCF announcement for requested ETF/date")
            content = self._get(f"https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_{code}_{max(dates)}.xml").content
        else:
            content = self._get(
                "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do",
                params={"fundCode": code}, headers={"Referer": "https://www.sse.com.cn/"},
            ).content
        result = parse_pcf(content, symbol)
        actual = pd.Timestamp(result.iloc[0]["date"]).date()
        if actual > today or (requested is not None and actual != requested):
            raise ValueError("PCF trading date differs from requested date")
        return result
