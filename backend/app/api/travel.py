"""旅游规划 API"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.services.llm import chat_stream
from app.utils.logger import get_logger
import httpx
import json
import asyncio
from datetime import datetime, timedelta

logger = get_logger("travel")
router = APIRouter(prefix="/api/travel", tags=["travel"])

# API Keys（从环境变量读取，这里先写死，后续移到配置）
AMAP_KEY = "8ba7a12e124c6aae89b41ffc389bb81d"
JUHE_TRAIN_KEY = "627685f1cc61524443ba06ebfbe52e4a"
APIHZ_ID = "10021238"  # 接口盒子用户ID
APIHZ_KEY = "c6a8c18abd776dc6352d3c2e48c968d7"

# 火车票 API 限流（接口盒子官方要求：10次/分钟，每日无上限）
_last_train_call_time = 0
TRAIN_RATE_LIMIT = 3.0  # 秒（稍微激进一点，提升体验）


class TravelPlanRequest(BaseModel):
    destination: str
    origin: str = ""
    start_date: str = ""
    days: int = 3
    people: int = 2
    budget: str = "中等"
    preferences: str = "自然风光+人文历史"
    crowd: str = "情侣"
    diet: str = ""
    pace: str = "适中"
    time_pref: str = "不限"


# ========== 高德 API ==========


async def get_amap_weather(city: str):
    """获取城市天气"""
    try:
        async with httpx.AsyncClient() as client:
            # 先获取城市 adcode
            geo_res = await client.get(
                "https://restapi.amap.com/v3/geocode/geo", params={"key": AMAP_KEY, "address": city}
            )
            geo_data = geo_res.json()
            if geo_data.get("status") != "1" or not geo_data.get("geocodes"):
                return {"error": "城市定位失败"}

            adcode = geo_data["geocodes"][0]["adcode"]

            # 获取天气预报
            weather_res = await client.get(
                "https://restapi.amap.com/v3/weather/weatherInfo",
                params={
                    "key": AMAP_KEY,
                    "city": adcode,
                    "extensions": "all",  # 7天预报
                },
            )
            weather_data = weather_res.json()
            if weather_data.get("status") == "1" and weather_data.get("forecasts"):
                return weather_data["forecasts"][0]["casts"]
            return {"error": "天气获取失败"}
    except Exception as e:
        logger.error(f"天气获取失败: {e}")
        return {"error": str(e)}


async def get_amap_pois(city: str, keyword: str, types: str = "", limit: int = 10):
    """获取 POI 列表"""
    try:
        async with httpx.AsyncClient() as client:
            # 先获取城市 adcode
            geo_res = await client.get(
                "https://restapi.amap.com/v3/geocode/geo", params={"key": AMAP_KEY, "address": city}
            )
            geo_data = geo_res.json()
            if geo_data.get("status") != "1" or not geo_data.get("geocodes"):
                return []

            city_code = geo_data["geocodes"][0].get("citycode", "010")

            poi_res = await client.get(
                "https://restapi.amap.com/v3/place/text",
                params={
                    "key": AMAP_KEY,
                    "city": city_code,
                    "keywords": keyword,
                    "types": types,
                    "citylimit": "true",
                    "offset": limit,
                    "page": 1,
                },
            )
            poi_data = poi_res.json()
            if poi_data.get("status") == "1":
                return poi_data.get("pois", [])
            return []
    except Exception as e:
        logger.error(f"POI 获取失败: {e}")
        return []


# ========== 聚合数据火车票 API ==========


async def get_train_tickets(from_city: str, to_city: str, date: str, time_pref: str = "不限"):
    """获取火车票余票（接口盒子API）"""
    global _last_train_call_time

    # 限流检查
    now = asyncio.get_event_loop().time()
    elapsed = now - _last_train_call_time
    if elapsed < TRAIN_RATE_LIMIT:
        wait_time = TRAIN_RATE_LIMIT - elapsed
        logger.info(f"火车票 API 限流，等待 {wait_time:.2f} 秒")
        await asyncio.sleep(wait_time)

    _last_train_call_time = asyncio.get_event_loop().time()

    try:
        # 解析日期
        dt = datetime.strptime(date, "%Y-%m-%d")
        year = dt.year
        month = dt.month
        day = dt.day

        async with httpx.AsyncClient() as client:
            params = {
                "id": APIHZ_ID,
                "key": APIHZ_KEY,
                "add": from_city,
                "end": to_city,
                "y": year,
                "m": month,
                "d": day,
            }

            res = await client.get("https://cn.apihz.cn/api/12306/api.php", params=params)
            data = res.json()
            # 调试用：打印原始返回报文（截取前 500 字符，防止日志过长）
            raw_str = str(data)
            logger.info(f"接口盒子原始返回: {raw_str[:500]}{'...' if len(raw_str) > 500 else ''}")

            # 接口盒子返回格式：{"code": 200, "msg": "查询成功。", "datas": [...]}
            # 注意：字段是 datas，不是 data！
            trains_raw = data.get("datas") or data.get("data") or []
            logger.info(f"解析到车次数量: {len(trains_raw)}")

            if isinstance(data, dict) and data.get("code") == 200 and trains_raw:
                trains = []
                for item in trains_raw:
                    # 估算价格（根据座位类型）
                    price = 200  # 默认二等座价格
                    remaining = "有票"

                    has_ticket = False
                    for seat in item.get("seats", []):
                        stock = seat.get("stock", 0)
                        # stock=-1 表示有票，stock>0 表示剩余票数
                        if stock == -1 or stock > 0:
                            has_ticket = True
                            seat_type = seat.get("type", "")
                            if "二等座" in seat_type or "硬座" in seat_type:
                                price = 200
                                remaining = "有票"
                            elif "一等座" in seat_type:
                                price = 350
                            elif "商务座" in seat_type or "软卧" in seat_type:
                                price = 500
                            break

                    if not has_ticket:
                        remaining = "无票"

                    # 根据车次类型微调价格
                    train_no = item.get("train_number", "")
                    if train_no.startswith("G"):
                        price = int(price * 1.2)  # 高铁稍贵
                    elif train_no.startswith("K") or train_no.startswith("T"):
                        price = int(price * 0.6)  # 普快便宜

                    trains.append(
                        {
                            "train_no": train_no,
                            "departure_time": item.get("depart_time", ""),
                            "arrival_time": item.get("arrive_time", ""),
                            "duration": item.get("duration", ""),
                            "price": price,
                            "remaining": remaining,
                            "departure_station": item.get("depart_name", ""),
                            "arrival_station": item.get("arrive_name", ""),
                        }
                    )

                logger.info(f"接口盒子返回车次数量: {len(trains)}")
                return trains

            logger.warning(f"火车票查询失败: {data.get('msg', 'unknown') if isinstance(data, dict) else '格式错误'}")
            return []
    except Exception as e:
        logger.error(f"火车票查询失败: {e}")
        return []


def filter_by_time_pref(trains: list, time_pref: str):
    """根据时间偏好过滤车次"""
    if time_pref == "不限":
        return trains

    def get_hour(train):
        # 兼容两种字段名
        time_str = train.get("departure_time") or train.get("start_time") or "00:00"
        try:
            return int(time_str.split(":")[0])
        except Exception:
            return 12

    if time_pref == "早上出发":
        return [t for t in trains if 6 <= get_hour(t) <= 11]
    elif time_pref == "下午出发":
        return [t for t in trains if 12 <= get_hour(t) <= 17]
    elif time_pref == "晚上出发":
        return [t for t in trains if 18 <= get_hour(t) <= 23]
    return trains


def get_duration_minutes(train):
    """获取车程分钟数"""
    try:
        duration = train.get("duration", "00:00")
        parts = duration.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return 9999
    except Exception:
        return 9999


def get_price(train):
    """获取二等座价格"""
    try:
        # 兼容两种格式：prices 数组 或 单个 price 字段
        if train.get("price"):
            return int(train.get("price", 0))
        prices = train.get("prices", [])
        for p in prices:
            if p.get("seat_name") == "二等座":
                return int(p.get("price", 0))
        # 没有二等座就取第一个有价格的
        if prices:
            return int(prices[0].get("price", 0))
        return 500
    except Exception:
        return 500


def get_remaining_tickets(train):
    """获取余票数量"""
    try:
        # 兼容两种格式
        if train.get("remaining"):
            return train.get("remaining", "有票")
        prices = train.get("prices", [])
        for p in prices:
            if p.get("seat_name") == "二等座":
                num = p.get("num", "无")
                if num == "有":
                    return "充足"
                elif num == "无":
                    return "无票"
                else:
                    return f"余票{num}张"
        return "充足"
    except Exception:
        return "充足"


def recommend_trains(trains: list, time_pref: str = "不限"):
    """智能推荐车次：最快、性价比最高、最早"""
    if not trains:
        return []

    # 过滤掉无效车次（无价格信息）
    valid_trains = [t for t in trains if t.get("price") and t.get("price", 0) > 0]

    if not valid_trains:
        # 如果没有价格信息，就用所有车次
        valid_trains = trains

    # 时间偏好过滤
    filtered = filter_by_time_pref(valid_trains, time_pref)
    if not filtered:
        filtered = valid_trains

    recommendations = []

    # 1. 最快的车次（车程最短）
    fastest = sorted(filtered, key=get_duration_minutes)[0]
    fastest["recommend_tag"] = "最快"
    recommendations.append(fastest)

    # 2. 性价比最高（价格最低且车程不太长）
    def cost_per_hour(train):
        price = get_price(train)
        duration = get_duration_minutes(train) / 60
        if duration == 0:
            return 9999
        return price / duration

    best_value = sorted(filtered, key=cost_per_hour)[0]
    if best_value != fastest:
        best_value["recommend_tag"] = "性价比最高"
        recommendations.append(best_value)

    # 3. 最早出发的车次
    earliest = sorted(filtered, key=lambda t: t.get("departure_time", "23:59"))[0]
    if earliest not in recommendations:
        earliest["recommend_tag"] = "最早"
        recommendations.append(earliest)

    return recommendations[:3]


def format_train_info(train, crowd=""):
    """格式化车次信息"""
    price = get_price(train)
    train_no = train.get("train_no", "")
    train_type = "高铁" if train_no.startswith("G") else ("动车" if train_no.startswith("D") else "火车")
    duration_raw = train.get("duration", "")
    duration_formatted = format_duration(duration_raw)

    return {
        "type": train_type,
        "name": train_no,
        "time": f"{train.get('departure_time', '')}-{train.get('arrival_time', '')}",
        "duration": duration_formatted,
        "price": price,
        "remaining": get_remaining_tickets(train),
        "recommend_tag": train.get("recommend_tag", ""),
        "recommend_reason": get_recommend_reason(train, crowd),
    }


def format_duration(duration_str):
    """将 14:38 格式化为 14小时38分"""
    try:
        parts = duration_str.split(":")
        if len(parts) == 2:
            hours = int(parts[0])
            mins = int(parts[1])
            if hours == 0:
                return f"{mins}分钟"
            elif mins == 0:
                return f"{hours}小时"
            else:
                return f"{hours}小时{mins}分"
        return duration_str
    except Exception:
        return duration_str


def get_recommend_reason(train, crowd=""):
    """生成推荐理由（结合出行人群和时长）"""
    tag = train.get("recommend_tag", "")
    duration = get_duration_minutes(train)
    price = get_price(train)
    departure_time = train.get("departure_time") or train.get("start_time") or ""

    hours = duration // 60
    mins = duration % 60

    # 带父母场景：时长过长给警示
    if crowd == "父母" and duration > 900:  # 15小时以上
        return f"⚠️ 全程 {hours}小时{mins}分，带父母出行极度疲劳，强烈建议优先选择高铁/飞机"

    # 短时长：正面建议
    if duration < 360:  # 6小时以内
        base = f"🚄 全程仅需 {hours}小时{mins}分，节省宝贵游玩时间"
    elif duration > 720:  # 12小时以上
        base = "🚄 夜间直达，可节省一晚住宿费用，但请注意休息"
    else:
        base = f"🚄 全程 {hours}小时{mins}分，时间适中"

    if tag == "最快":
        return base
    elif tag == "性价比最高":
        if price > 0:
            return f"✅ 二等座 ¥{price}，性价比出色，适合预算敏感的出行"
        return "✅ 性价比出色，票价请查询12306"
    elif tag == "最早":
        return f"🌅 {departure_time} 出发，下午即可到达开始游玩"
    return base


# ========== 生成旅游规划 ==========


@router.post("/plan")
async def generate_travel_plan(req: TravelPlanRequest):
    """生成旅游规划（SSE 流式）"""
    logger.info(f"生成旅游规划: {req.destination}, {req.days}天")

    async def generate():
        # 第一步：所有任务并发执行，哪个先完成先返回哪个
        yield 'data: {"step": "start", "message": "正在并行查询..."}\n\n'

        # 外部变量存储结果
        outbound_info = []
        inbound_info = []
        transport_status = "empty"
        transport_message = ""
        hotels_info = []
        weather_info = []
        attractions = []

        # 定义所有任务
        async def query_transport():
            """查询交通"""
            nonlocal outbound_info, inbound_info, transport_status, transport_message
            trains = []
            return_trains = []

            if req.origin and req.start_date:
                train_tasks = [
                    get_train_tickets(req.origin, req.destination, req.start_date, req.time_pref),
                ]

                try:
                    start_dt = datetime.strptime(req.start_date, "%Y-%m-%d")
                    return_date = (start_dt + timedelta(days=req.days - 1)).strftime("%Y-%m-%d")
                    train_tasks.append(get_train_tickets(req.destination, req.origin, return_date, req.time_pref))
                except Exception:
                    pass

                train_results = await asyncio.gather(*train_tasks)
                trains = train_results[0] if train_results else []
                return_trains = train_results[1] if len(train_results) > 1 else []

            recommended_outbound = recommend_trains(trains, req.time_pref)
            outbound_info = [format_train_info(t, req.crowd) for t in recommended_outbound]

            recommended_return = recommend_trains(return_trains, req.time_pref)
            inbound_info = [format_train_info(t, req.crowd) for t in recommended_return]

            transport_status = "success"
            transport_message = ""
            if not outbound_info and req.origin and req.start_date:
                transport_status = "empty"
                transport_message = "未查询到合适的车次，建议调整出发日期或出行时间偏好"

        async def query_hotels():
            """查询酒店"""
            nonlocal hotels_info
            logger.info(f"开始查询酒店: 目的地={req.destination}")
            hotels = await get_amap_pois(req.destination, "酒店", "100000", 15)
            logger.info(f"高德API返回酒店数量: {len(hotels)}")

            hotels_info = []
            for h in hotels[:4]:
                rating = h.get("biz_ext", {}).get("rating", "4.5")
                try:
                    rating = float(rating)
                except Exception:
                    rating = 4.5

                price = 200
                if rating >= 4.8:
                    price = 600
                elif rating >= 4.6:
                    price = 400
                elif rating >= 4.4:
                    price = 250
                else:
                    price = 150

                room_type = "大床房" if req.crowd in ["情侣", "独自"] else "双床房"

                hotels_info.append(
                    {
                        "name": h.get("name", ""),
                        "rating": rating,
                        "price": price,
                        "location": h.get("address", ""),
                        "room_type": room_type,
                        "tags": ["免费 WiFi", "24小时前台", "停车场"],
                        "recommend_reason": f"评分 {rating} 分，{h.get('address', '位置便利')}，性价比出色",
                        "image": (h.get("photos") and h.get("photos")[0].get("url", "")) or "",
                    }
                )
                logger.info(
                    f"  酒店: {h.get('name', '')} - 图片: {(h.get('photos') and h.get('photos')[0].get('url', '')) or '无'}"
                )

        async def query_weather_and_attractions():
            """查询天气和景点"""
            nonlocal weather_info, attractions
            weather_task = get_amap_weather(req.destination)
            attractions_task = get_amap_pois(req.destination, "景点|风景区", "110000", 15)

            weather_raw, attractions = await asyncio.gather(weather_task, attractions_task)

            if isinstance(weather_raw, list):
                for w in weather_raw[: req.days]:
                    weather_desc = w.get("dayweather", "")
                    temp_low = int(w.get("nighttemp", 20))
                    temp_high = int(w.get("daytemp", 30))
                    wind = w.get("daypower", "")

                    # 判断是否极端天气
                    is_extreme = "雷" in weather_desc or "暴雨" in weather_desc or temp_high > 35 or temp_low < 0

                    # 生成具体的穿搭建议（结合用户画像）
                    outfit_parts = []
                    if temp_high > 30:
                        outfit_parts.append("天气炎热，建议穿短袖、短裤等清凉衣物")
                    elif temp_high > 25:
                        outfit_parts.append("天气温暖，建议穿短袖或薄长袖")
                    elif temp_high > 18:
                        outfit_parts.append("天气凉爽，建议穿薄外套或长袖")
                    elif temp_high > 10:
                        outfit_parts.append("天气较冷，建议穿厚外套或夹克")
                    else:
                        outfit_parts.append("天气寒冷，建议穿羽绒服等保暖衣物")

                    if "雨" in weather_desc:
                        outfit_parts.append("有降雨，记得带雨伞")
                    if "雷" in weather_desc:
                        outfit_parts.append("有雷雨，尽量减少户外活动")
                    if "雪" in weather_desc:
                        outfit_parts.append("有降雪，注意防滑保暖")

                    # 结合用户画像
                    if req.crowd == "亲子" or req.crowd == "父母":
                        outfit_parts.append("老人/小孩注意增减衣物")

                    outfit_advice = "，".join(outfit_parts) + "。"

                    # 估算紫外线和空气质量
                    uv_index = (
                        "强" if temp_high > 30 and "晴" in weather_desc else ("中等" if "晴" in weather_desc else "弱")
                    )
                    air_quality = "优" if "风" in wind else "良"

                    weather_info.append(
                        {
                            "date": w.get("date", ""),
                            "day": f"Day {len(weather_info) + 1}",
                            "weather": weather_desc,
                            "temp_low": temp_low,
                            "temp_high": temp_high,
                            "wind": wind,
                            "precipitation": "20%" if "晴" in weather_desc else "60%",
                            "outfit_advice": outfit_advice,
                            "warning": is_extreme,
                            "uv_index": uv_index,
                            "air_quality": air_quality,
                        }
                    )

        # 并发执行所有任务
        transport_task = asyncio.create_task(query_transport())
        hotels_task = asyncio.create_task(query_hotels())
        weather_attractions_task = asyncio.create_task(query_weather_and_attractions())

        # 使用 as_completed 哪个先完成先返回
        pending = {transport_task, hotels_task, weather_attractions_task}

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                if task == transport_task:
                    # 交通先完成
                    yield f"data: {json.dumps({'step': 'transport_ready', 'outbound': outbound_info, 'inbound': inbound_info, 'status': transport_status, 'message': transport_message}, ensure_ascii=False)}\n\n"

                elif task == hotels_task:
                    # 酒店先完成
                    yield f"data: {json.dumps({'step': 'hotels_ready', 'hotels': hotels_info}, ensure_ascii=False)}\n\n"

                elif task == weather_attractions_task:
                    # 天气和景点完成
                    yield f"data: {json.dumps({'step': 'weather_ready', 'weather': weather_info}, ensure_ascii=False)}\n\n"

        # 第三步：AI 生成行程
        yield "data: {\"step\": 'loading_itinerary', \"message\": 'AI 正在生成行程...'}\n\n"

        # 构建 Prompt
        attractions_info = "\n".join(
            [
                f"- {a.get('name', '')}: {a.get('address', '')}, 评分: {a.get('biz_ext', {}).get('rating', '暂无')}"
                for a in attractions[:10]
            ]
        )

        weather_prompt_info = (
            "\n".join(
                [
                    f"- {w.get('date', '')}: {w.get('weather', '')}, {w.get('temp_low', '')}°C ~ {w.get('temp_high', '')}°C, 风力: {w.get('wind', '')}"
                    for w in weather_info[: req.days]
                ]
            )
            if weather_info
            else "暂无天气数据"
        )

        prompt = f"""你是一个资深的旅游规划师。请为用户生成一份详细的旅游规划。

## 基本信息
- 目的地：{req.destination}
- 出发地：{req.origin or "未指定"}
- 出行日期：{req.start_date or "未指定"}
- 天数：{req.days}天
- 人数：{req.people}人
- 预算：{req.budget}
- 偏好：{req.preferences}
- 出行人群：{req.crowd}
- 饮食习惯：{req.diet or "无特殊要求"}
- 行程节奏：{req.pace}

## 实时天气
{weather_prompt_info}

## 景点推荐（来自高德地图）
{attractions_info or "暂无景点数据"}

## 输出要求
请严格按照以下 JSON 格式输出，不要输出任何其他文字：

{{
  "weather": [
    {{
      "date": "10-15",
      "day": "Day 1",
      "weather": "晴",
      "temp_low": 18,
      "temp_high": 28,
      "wind": "微风 2级",
      "precipitation": "20%",
      "outfit_advice": "早晚温差大，建议带薄外套；紫外线强，注意防晒。",
      "warning": false
    }}
  ],
  "itinerary": [
    {{
      "day": 1,
      "title": "第一天：初识目的地",
      "schedule": [
        {{
          "time": "09:00",
          "title": "景点名称",
          "duration": "2小时",
          "type": "scenic",
          "ai_tip": "建议9点前去，避开人流高峰"
        }}
      ]
    }}
  ],
  "tips": [
    "注意事项1",
    "注意事项2"
  ],
  "budget_summary": {{
    "transport": 1100,
    "hotel": 1350,
    "food": 800,
    "attraction": 300,
    "total": 3550
  }}
}}
"""

        # 流式生成，先收集完整内容
        full_content = ""
        for token in chat_stream(prompt):
            full_content += token

        # 清理 markdown 代码块
        cleaned = full_content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            plan_data = json.loads(cleaned)
            # 合并所有数据
            plan_data["transport"] = {
                "outbound": outbound_info,
                "inbound": inbound_info,
                "status": transport_status,
                "message": transport_message,
            }
            plan_data["hotels"] = hotels_info
            plan_data["weather"] = weather_info
            yield f"data: {json.dumps({'step': 'done', 'plan': plan_data}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"JSON 解析失败: {e}")
            logger.error(f"原始内容: {cleaned[:1000]}")
            # 兜底返回一个默认的行程数据
            fallback_plan = {
                "itinerary": [
                    {
                        "day": 1,
                        "title": f"第一天：初识{req.destination}",
                        "schedule": [
                            {
                                "time": "09:00",
                                "title": "抵达目的地",
                                "duration": "1小时",
                                "type": "transport",
                                "ai_tip": "建议提前查好交通路线",
                            },
                            {
                                "time": "10:30",
                                "title": "游览核心景点",
                                "duration": "3小时",
                                "type": "scenic",
                                "ai_tip": "建议9点前去，避开人流高峰",
                            },
                            {
                                "time": "14:00",
                                "title": "品尝当地美食",
                                "duration": "2小时",
                                "type": "food",
                                "ai_tip": "推荐尝试当地特色菜",
                            },
                        ],
                    }
                ]
                * req.days,
                "budget_summary": {
                    "transport": 1000,
                    "hotel": hotels_info[0]["price"] * req.days if hotels_info else 1000,
                    "food": 500 * req.days,
                    "attraction": 300,
                    "total": (
                        1000 + (hotels_info[0]["price"] * req.days if hotels_info else 1000) + 500 * req.days + 300
                    ),
                },
                "tips": ["请提前查看天气情况，带好相应衣物", "建议提前预订酒店和门票", "注意保管好个人财物"],
                "transport": {
                    "outbound": outbound_info,
                    "inbound": inbound_info,
                    "status": transport_status,
                    "message": transport_message,
                },
                "hotels": hotels_info,
                "weather": weather_info,
            }
            yield f"data: {json.dumps({'step': 'done', 'plan': fallback_plan}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


class TransportRequest(BaseModel):
    destination: str
    origin: str = ""
    start_date: str = ""
    days: int = 3
    time_pref: str = "不限"


@router.post("/transport")
async def query_transport(req: TransportRequest):
    """单独查询交通方案"""
    logger.info(f"查询交通方案: {req.origin} -> {req.destination}")

    if not req.origin or not req.start_date:
        return {"outbound": [], "inbound": [], "status": "empty", "message": "请填写出发地和出发日期"}

    try:
        # 并发查询去程和返程
        train_tasks = [
            get_train_tickets(req.origin, req.destination, req.start_date, req.time_pref),
        ]

        try:
            start_dt = datetime.strptime(req.start_date, "%Y-%m-%d")
            return_date = (start_dt + timedelta(days=req.days - 1)).strftime("%Y-%m-%d")
            train_tasks.append(get_train_tickets(req.destination, req.origin, return_date, req.time_pref))
        except Exception:
            pass

        train_results = await asyncio.gather(*train_tasks)
        trains = train_results[0] if train_results else []
        return_trains = train_results[1] if len(train_results) > 1 else []

        # 智能推荐
        recommended_outbound = recommend_trains(trains, req.time_pref)
        outbound_info = [format_train_info(t) for t in recommended_outbound]

        recommended_return = recommend_trains(return_trains, req.time_pref)
        inbound_info = [format_train_info(t) for t in recommended_return]

        status = "success" if outbound_info else "empty"
        message = "" if outbound_info else "未查询到合适的车次，建议调整出发日期或出行时间偏好"

        return {"outbound": outbound_info, "inbound": inbound_info, "status": status, "message": message}
    except Exception as e:
        logger.error(f"交通查询失败: {e}")
        return {"outbound": [], "inbound": [], "status": "error", "message": f"查询失败: {str(e)}"}


class HotelRequest(BaseModel):
    destination: str
    crowd: str = "情侣"
    days: int = 3
    people: int = 2
    sort_by: str = "智能推荐"
    price_range: str = "不限"
    location: str = ""
    hotel_type: str = "智能推荐"


@router.post("/hotels")
async def query_hotels(req: HotelRequest):
    """单独查询酒店"""
    try:
        logger.info(
            f"查询酒店: 目的地={req.destination}, 位置筛选={req.location}, 价格={req.price_range}, 排序={req.sort_by}, 类型={req.hotel_type}, 人数={req.people}"
        )

        # 根据类型选择搜索关键词
        if req.hotel_type == "民宿":
            search_keyword = f"{req.location} 民宿 公寓" if req.location else "民宿 公寓"
        elif req.hotel_type == "青年旅舍":
            search_keyword = f"{req.location} 青年旅舍" if req.location else "青年旅舍"
        elif req.hotel_type == "酒店":
            search_keyword = f"{req.location} 酒店" if req.location else "酒店"
        else:
            # 智能推荐：人数多自动推荐民宿
            if req.people >= 4 or req.crowd == "父母":
                search_keyword = f"{req.location} 民宿 酒店" if req.location else "民宿 酒店"
            else:
                search_keyword = f"{req.location} 酒店" if req.location else "酒店"

        hotels = await get_amap_pois(req.destination, search_keyword, "100000", 20)
        logger.info(f"高德API返回酒店数量: {len(hotels)}")
        for i, h in enumerate(hotels[:5]):
            logger.info(f"  酒店{i + 1}: {h.get('name', '')} - {h.get('address', '')}")

        hotels_info = []
        for h in hotels[:8]:
            rating = h.get("biz_ext", {}).get("rating", "4.5")
            try:
                rating = float(rating)
            except Exception:
                rating = 4.5

            price = 200
            if rating >= 4.8:
                price = 600
            elif rating >= 4.6:
                price = 400
            elif rating >= 4.4:
                price = 250
            else:
                price = 150

            # 判断是否为民宿
            hotel_name = h.get("name", "")
            is_homestay = any(keyword in hotel_name for keyword in ["民宿", "公寓", "客栈", "别墅", "度假"])

            # 根据人数和类型调整房型
            if is_homestay:
                if req.people >= 6:
                    room_type = "整套房源"
                    bedrooms = "三室一厅"
                    has_kitchen = True
                elif req.people >= 4:
                    room_type = "整套房源"
                    bedrooms = "两室一厅"
                    has_kitchen = True
                else:
                    room_type = "整套房源"
                    bedrooms = "一室一厅"
                    has_kitchen = True
            else:
                if req.crowd in ["情侣", "独自"]:
                    room_type = "大床房"
                elif req.people >= 4:
                    room_type = "双床房"
                else:
                    room_type = "双床房"
                bedrooms = None
                has_kitchen = False

            # 民宿价格调整
            if is_homestay:
                price = int(price * 1.5)  # 民宿整租价格稍高，但人均更便宜

            hotels_info.append(
                {
                    "name": h.get("name", ""),
                    "rating": rating,
                    "price": price,
                    "location": h.get("address", ""),
                    "room_type": room_type,
                    "is_homestay": is_homestay,
                    "bedrooms": bedrooms,
                    "has_kitchen": has_kitchen,
                    "tags": ["免费 WiFi", "24小时前台", "停车场"]
                    if not is_homestay
                    else ["免费 WiFi", "厨房", "洗衣机"],
                    "recommend_reason": f"评分 {rating} 分，{h.get('address', '位置便利')}，性价比出色",
                    "image": (h.get("photos") and h.get("photos")[0].get("url", "")) or "",
                }
            )
            logger.info(f"  图片: {(h.get('photos') and h.get('photos')[0].get('url', '')) or '无图片'}")
            logger.info(f"  photos 原始数据: {h.get('photos', '无 photos 字段')}")

        # 价格筛选
        if req.price_range == "0-200":
            hotels_info = [h for h in hotels_info if h["price"] <= 200]
        elif req.price_range == "200-500":
            hotels_info = [h for h in hotels_info if 200 < h["price"] <= 500]
        elif req.price_range == "500+":
            hotels_info = [h for h in hotels_info if h["price"] > 500]

        # 位置筛选（已经直接用位置作为关键词查询了，这里再做一次兜底）
        if req.location:
            hotels_info = [h for h in hotels_info if req.location in h["location"] or req.location in h["name"]]

        # 排序
        if req.sort_by == "价格最低":
            hotels_info.sort(key=lambda x: x["price"])
        elif req.sort_by == "评分最高":
            hotels_info.sort(key=lambda x: x["rating"], reverse=True)
        else:
            # 智能推荐：默认按评分从高到低排序
            hotels_info.sort(key=lambda x: x["rating"], reverse=True)

        logger.info(f"筛选后酒店数量: {len(hotels_info)}")
        return {"hotels": hotels_info}
    except Exception as e:
        logger.error(f"酒店查询失败: {e}")
        return {"hotels": []}


class RegenerateDayRequest(BaseModel):
    destination: str
    day: int
    custom_place: str = ""
    current_schedule: list = []


@router.post("/regenerate-day")
async def regenerate_day_itinerary(req: RegenerateDayRequest):
    """重新生成某一天的行程（支持指定地点）"""
    logger.info(f"重新生成第 {req.day} 天行程，指定地点: {req.custom_place}")

    try:
        # 先查询景点
        attractions = await get_amap_pois(req.destination, "景点|风景区", "110000", 10)
        attractions_info = "\n".join(
            [
                f"- {a.get('name', '')}: {a.get('address', '')}, 评分: {a.get('biz_ext', {}).get('rating', '暂无')}"
                for a in attractions[:8]
            ]
        )

        # 当前行程信息
        current_schedule_info = (
            "\n".join([f"- {item.get('time', '')} {item.get('title', '')}" for item in req.current_schedule])
            if req.current_schedule
            else "暂无"
        )

        prompt = f"""你是一个资深的旅游规划师。请为用户重新生成第 {req.day} 天的行程。

## 目的地
{req.destination}

## 用户指定的核心地点（必须包含）
{req.custom_place or "无"}

## 当前行程（供参考，可以调整）
{current_schedule_info}

## 景点推荐（来自高德地图）
{attractions_info or "暂无景点数据"}

## 输出要求
请严格按照以下 JSON 格式输出，不要输出任何其他文字：

{{
  "day": {req.day},
  "title": "第{req.day}天：行程主题",
  "schedule": [
    {{
      "time": "09:00",
      "title": "景点名称",
      "duration": "2小时",
      "type": "scenic",
      "ai_tip": "建议9点前去，避开人流高峰",
      "is_custom": false
    }}
  ]
}}

注意：
- 如果用户指定了地点，必须把它加入行程，并设置 is_custom 为 true
- 如果距离其他景点较远，在 ai_tip 里提示
- type 只能是 scenic（景点）、food（美食）、transport（交通）、hotel（住宿）、rest（休息）
"""

        # 流式生成，先收集完整内容
        full_content = ""
        for token in chat_stream(prompt):
            full_content += token

        # 清理 markdown 代码块
        cleaned = full_content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            day_data = json.loads(cleaned)
            logger.info(f"重新生成第 {req.day} 天行程成功，共 {len(day_data.get('schedule', []))} 个景点")
            return {"success": True, "day": day_data}
        except Exception as e:
            logger.error(f"JSON 解析失败: {e}")
            return {"success": False, "message": "生成失败，请重试"}

    except Exception as e:
        logger.error(f"重新生成行程失败: {e}")
        return {"success": False, "message": str(e)}
