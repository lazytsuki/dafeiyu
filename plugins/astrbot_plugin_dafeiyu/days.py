"""日子：今天是几号、星期几、农历哪天、节气节日、外面天气怎么样。

只是把日历和窗外的样子告诉它，提不提、在不在意由它自己。
农历、节气、节日用 lunar_python（纯 Python，没装就只说公历和星期）。
天气用 Open-Meteo（免费、不要 Key）：https://open-meteo.com/en/docs ；
配置里填地名和经纬度，没填经纬度就试着按地名查一次坐标（查不到就不说天气）。
"""

import datetime as dt

import aiohttp

FORECAST = "https://api.open-meteo.com/v1/forecast"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_TTL = 3600  # 天气多久重新查一次（秒）
TERM_NEAR = 3  # 节气前后几天内提一句

# WMO 天气代码 → 中文：https://open-meteo.com/en/docs 页底的 WMO Weather interpretation codes
WMO = {
    0: "晴", 1: "大致晴", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
}


def _lunar(day: dt.date) -> tuple[str, list[str]]:
    """(农历日期, 今天和附近的节气节日)。没装 lunar_python 就是 ("", [])。"""
    try:
        from lunar_python import Solar
    except ImportError:
        return "", []
    solar = Solar.fromYmd(day.year, day.month, day.day)
    lunar = solar.getLunar()
    marks = list(lunar.getFestivals()) + list(solar.getFestivals())
    if lunar.getJieQi():
        marks.append(f"{lunar.getJieQi()}（节气）")
    else:
        nxt = lunar.getNextJieQi()
        prv = lunar.getPrevJieQi()
        to_next = _days_between(day, nxt.getSolar())
        since = _days_between(prv.getSolar(), day)
        if 0 < to_next <= TERM_NEAR:
            marks.append(f"再过 {to_next} 天是{nxt.getName()}")
        elif 0 < since <= TERM_NEAR:
            marks.append(f"{since} 天前是{prv.getName()}")
    # 明天的节日也提一句（比如明天就是除夕）
    tom = Solar.fromYmd(day.year, day.month, day.day).next(1)
    for f in list(tom.getLunar().getFestivals()) + list(tom.getFestivals()):
        marks.append(f"明天是{f}")
    return f"农历{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}", marks


def _days_between(a, b) -> int:
    """a、b 可以是 date 或 lunar_python 的 Solar。"""
    conv = lambda x: x if isinstance(x, dt.date) else dt.date(x.getYear(), x.getMonth(), x.getDay())
    return (conv(b) - conv(a)).days


def calendar_line(now: dt.datetime) -> str:
    """「10月31日 周六，农历九月廿一；万圣节前夜」这样的一句。"""
    lunar, marks = _lunar(now.date())
    out = f"{now.month}月{now.day}日 周{'一二三四五六日'[now.weekday()]}"
    if lunar:
        out += f"，{lunar}"
    if marks:
        out += "；" + "、".join(dict.fromkeys(marks))
    return out


def parse_coords(text: str) -> tuple[float, float] | None:
    """「30.26,120.14」→ (纬度, 经度)；填得不对就是 None。"""
    try:
        lat, lon = (float(x) for x in text.replace("，", ",").split(","))
    except (ValueError, AttributeError):
        return None
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


async def geocode(name: str) -> tuple[float, float] | None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(GEOCODE, params={"name": name, "count": 1, "language": "zh"}) as r:
            data = await r.json(content_type=None)
    hit = (data.get("results") or [None])[0]
    return (hit["latitude"], hit["longitude"]) if hit else None


async def fetch_weather(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
        "current": "temperature_2m,apparent_temperature,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(FORECAST, params=params) as r:
            r.raise_for_status()
            return await r.json(content_type=None)


def weather_line(place: str, data: dict) -> str:
    """「杭州现在晴，23°C；今天 15~24°C；明天小雨，降水概率 70%」这样的一句。认不出来就是空串。"""
    try:
        cur, daily = data["current"], data["daily"]
        now = f"{WMO.get(cur['weather_code'], '')}，{round(cur['temperature_2m'])}°C"
        feel = cur.get("apparent_temperature")
        if feel is not None and abs(feel - cur["temperature_2m"]) >= 3:
            now += f"（体感 {round(feel)}°C）"
        out = f"{place}现在{now}；今天 {round(daily['temperature_2m_min'][0])}~{round(daily['temperature_2m_max'][0])}°C"
        if len(daily["time"]) > 1:
            out += f"；明天{WMO.get(daily['weather_code'][1], '')}，{round(daily['temperature_2m_min'][1])}~{round(daily['temperature_2m_max'][1])}°C"
            rain = daily.get("precipitation_probability_max", [None, None])[1]
            if rain is not None and rain >= 40:
                out += f"，降水概率 {rain}%"
        return out
    except (KeyError, IndexError, TypeError):
        return ""
