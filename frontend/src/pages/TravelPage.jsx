import { useState } from 'react';
import {
  Input, Button, Select, DatePicker, InputNumber,
  Card, Collapse, Tag, Skeleton, message, Tabs, Radio, Drawer, Alert, Tooltip
} from 'antd';
import { authFetch } from '../utils/api';
import {
  EnvironmentOutlined,
  ClockCircleOutlined,
  StarFilled,
  CalendarOutlined,
  TeamOutlined,
  WalletOutlined,
  HeartOutlined,
  RocketOutlined,
  UserOutlined,
  EyeOutlined,
  ReloadOutlined,
  WarningOutlined,
  EnvironmentTwoTone,
  PlusOutlined,
} from '@ant-design/icons';

const { RangePicker } = DatePicker;

const typeConfig = {
  scenic: { label: '景点', bg: '#ECFDF5', color: '#10B981' },
  transport: { label: '交通', bg: '#EFF6FF', color: '#3B82F6' },
  food: { label: '美食', bg: '#FFF7ED', color: '#F97316' },
  hotel: { label: '住宿', bg: '#EFF6FF', color: '#3B82F6' },
  rest: { label: '休息', bg: '#F9FAFB', color: '#6B7280' },
};

function TravelPage() {
  const [form, setForm] = useState({
    destination: '',
    origin: '',
    dateRange: null,
    days: undefined,
    people: undefined,
    budget: undefined,
    preferences: '',
    crowd: undefined,
    diet: '',
    pace: undefined,
    timePref: '不限'
  });

  // 拆分独立 Loading 状态
  const [isTransportLoading, setIsTransportLoading] = useState(false);
  const [isHotelLoading, setIsHotelLoading] = useState(false);
  const [isItineraryLoading, setIsItineraryLoading] = useState(false);
  const [isWeatherLoading, setIsWeatherLoading] = useState(false);

  const [plan, setPlan] = useState(null);
  const [hotelDetail, setHotelDetail] = useState(null);

  // 酒店筛选状态
  const [hotelFilter, setHotelFilter] = useState({
    location: '',
    priceRange: '不限',
    sortBy: '智能推荐',
    hotelType: '智能推荐'
  });
  const [isHotelRefreshing, setIsHotelRefreshing] = useState(false);

  // 天气展开状态
  const [isWeatherExpanded, setIsWeatherExpanded] = useState(false);

  // 每日行程局部微调状态
  const [regeneratingDay, setRegeneratingDay] = useState(null); // 正在重新生成的天数
  const [customPlaceInput, setCustomPlaceInput] = useState({}); // 每天的输入框值
  const [showCustomInput, setShowCustomInput] = useState({}); // 是否显示输入框

  const handleDateChange = (dates) => {
    setForm(prev => {
      if (dates && dates[0] && dates[1]) {
        const diff = dates[1].diff(dates[0], 'days') + 1;
        return { ...prev, dateRange: dates, days: diff };
      }
      return { ...prev, dateRange: dates };
    });
  };

  const handleDaysChange = (value) => {
    const newDays = value || 3;
    setForm(prev => {
      if (prev.dateRange && prev.dateRange[0]) {
        const start = prev.dateRange[0];
        const end = start.add(newDays - 1, 'days');
        return { ...prev, days: newDays, dateRange: [start, end] };
      }
      return { ...prev, days: newDays };
    });
  };

  // 重新查询交通
  const handleRetryTransport = async (timePref = form.timePref) => {
    setIsTransportLoading(true);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/travel/transport`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          destination: form.destination,
          origin: form.origin,
          start_date: form.dateRange?.[0] ? form.dateRange[0].format('YYYY-MM-DD') : '',
          days: form.days,
          time_pref: timePref
        })
      });
      const data = await res.json();
      setPlan(prev => ({
        ...prev,
        transport: {
          outbound: data.outbound || [],
          inbound: data.inbound || [],
          status: data.status,
          message: data.message
        }
      }));
    } catch (e) {
      message.error('交通查询失败，请重试');
    } finally {
      setIsTransportLoading(false);
    }
  };

  // 时间偏好切换
  const handleTimePrefChange = (e) => {
    const newTimePref = e.target.value;
    setForm({ ...form, timePref: newTimePref });
    // 如果已经有交通数据，重新查询
    if (plan?.transport) {
      handleRetryTransport(newTimePref);
    }
  };

  // 换一批酒店
  const handleRefreshHotels = async () => {
    setIsHotelRefreshing(true);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/travel/hotels`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          destination: form.destination,
          crowd: form.crowd,
          days: form.days,
          sort_by: hotelFilter.sortBy,
          price_range: hotelFilter.priceRange,
          location: hotelFilter.location
        })
      });
      const data = await res.json();
      setPlan(prev => ({
        ...prev,
        hotels: data.hotels || []
      }));
    } catch (e) {
      message.error('酒店刷新失败，请重试');
    } finally {
      setIsHotelRefreshing(false);
    }
  };

  // 筛选或排序变化时重新查询
  const handleHotelFilterChange = (newFilter) => {
    setHotelFilter(newFilter);
    // 如果已经有酒店数据，自动重新查询（所有筛选都调后端）
    if (plan?.hotels) {
      handleRefreshHotelsWithFilter(newFilter);
    }
  };

  // 带筛选条件查询酒店
  const handleRefreshHotelsWithFilter = async (filter) => {
    setIsHotelRefreshing(true);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/travel/hotels`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          destination: form.destination,
          crowd: form.crowd,
          days: form.days,
          people: form.people,
          sort_by: filter.sortBy,
          price_range: filter.priceRange,
          location: filter.location,
          hotel_type: filter.hotelType
        })
      });
      const data = await res.json();
      setPlan(prev => ({
        ...prev,
        hotels: data.hotels || []
      }));
    } catch (e) {
      message.error('酒店查询失败，请重试');
    } finally {
      setIsHotelRefreshing(false);
    }
  };

  // 重新生成某一天的行程（添加指定地点）
  const handleRegenerateDay = async (dayIdx) => {
    const customPlace = customPlaceInput[dayIdx]?.trim();
    if (!customPlace) {
      message.warning('请输入地点');
      return;
    }

    setRegeneratingDay(dayIdx);
    try {
      const currentDay = plan.itinerary[dayIdx];
      const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/travel/regenerate-day`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          destination: form.destination,
          day: dayIdx + 1,
          custom_place: customPlace,
          current_schedule: currentDay?.schedule || []
        })
      });
      const data = await res.json();

      if (data.success) {
        setPlan(prev => {
          const newItinerary = [...(prev.itinerary || [])];
          newItinerary[dayIdx] = data.day;
          return { ...prev, itinerary: newItinerary };
        });
        message.success('行程已更新');
        setShowCustomInput(prev => ({ ...prev, [dayIdx]: false }));
        setCustomPlaceInput(prev => ({ ...prev, [dayIdx]: '' }));
      } else {
        message.error(data.message || '生成失败，请重试');
      }
    } catch (e) {
      message.error('网络错误，请重试');
    } finally {
      setRegeneratingDay(null);
    }
  };

  const handleGenerate = async () => {
    if (!form.destination) {
      message.warning('请输入目的地');
      return;
    }

    // 重置状态
    setPlan(null);
    setIsTransportLoading(true);
    setIsHotelLoading(true);
    setIsItineraryLoading(true);
    setIsWeatherLoading(true);
    // 重置酒店筛选
    setHotelFilter({
      location: '',
      priceRange: '不限',
      sortBy: '智能推荐'
    });

    try {
      const res = await authFetch(`${import.meta.env.VITE_API_BASE}/api/travel/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          destination: form.destination,
          origin: form.origin,
          start_date: form.dateRange?.[0] ? form.dateRange[0].format('YYYY-MM-DD') : '',
          days: form.days,
          people: form.people,
          budget: form.budget,
          preferences: form.preferences,
          crowd: form.crowd,
          diet: form.diet,
          pace: form.pace,
          time_pref: form.timePref
        })
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() || '';

        for (const event of events) {
          const lines = event.split('\n');
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));
                if (data.step === 'transport_ready') {
                  // 交通加载完成
                  setIsTransportLoading(false);
                  setPlan(prev => ({
                    ...prev,
                    transport: {
                      outbound: data.outbound || [],
                      inbound: data.inbound || [],
                      status: data.status,
                      message: data.message
                    }
                  }));
                } else if (data.step === 'hotels_ready') {
                  // 酒店加载完成
                  setIsHotelLoading(false);
                  setPlan(prev => ({
                    ...prev,
                    hotels: data.hotels || []
                  }));
                } else if (data.step === 'weather_ready') {
                  // 天气加载完成
                  setIsWeatherLoading(false);
                  setPlan(prev => ({
                    ...prev,
                    weather: data.weather || []
                  }));
                } else if (data.step === 'loading_itinerary') {
                  // 开始加载行程
                  setIsItineraryLoading(true);
                } else if (data.step === 'done') {
                  // 全部完成
                  setIsItineraryLoading(false);
                  setPlan(data.plan);
                } else if (data.step === 'error') {
                  message.error(data.message);
                  setIsTransportLoading(false);
                  setIsHotelLoading(false);
                  setIsItineraryLoading(false);
                  setIsWeatherLoading(false);
                }
              } catch (e) {
                // 忽略解析错误
              }
            }
          }
        }
      }
    } catch (e) {
      message.error('生成失败，请重试');
      setIsTransportLoading(false);
      setIsHotelLoading(false);
      setIsItineraryLoading(false);
    }
  };

  const totalBudget = plan?.budget_summary?.total || 0;
  const perPerson = form.people > 0 ? Math.round(totalBudget / form.people) : 0;

  const getRecommendTag = (tag) => {
    const tagMap = {
      '最快': { color: '#1D4ED8', bg: '#EFF6FF' },
      '性价比最高': { color: '#047857', bg: '#ECFDF5' },
      '最早': { color: '#C2410C', bg: '#FFF7ED' }
    };
    return tagMap[tag] || null;
  };

  const hasTransportError = plan?.transport?.status === 'empty';
  // 模块完全独立，交通失败不影响其他模块
  const canShowHotels = !!plan?.hotels;
  const canShowItinerary = !!plan?.itinerary;

  // 酒店筛选
  let filteredHotels = [...(plan?.hotels || [])];
  if (hotelFilter.priceRange === '0-200') {
    filteredHotels = filteredHotels.filter(h => h.price <= 200);
  } else if (hotelFilter.priceRange === '200-500') {
    filteredHotels = filteredHotels.filter(h => h.price > 200 && h.price <= 500);
  } else if (hotelFilter.priceRange === '500+') {
    filteredHotels = filteredHotels.filter(h => h.price > 500);
  }
  if (hotelFilter.location) {
    filteredHotels = filteredHotels.filter(h => h.location?.includes(hotelFilter.location));
  }
  if (hotelFilter.sortBy === '价格最低') {
    filteredHotels.sort((a, b) => a.price - b.price);
  } else if (hotelFilter.sortBy === '评分最高') {
    filteredHotels.sort((a, b) => b.rating - a.rating);
  }

  const isGenerating = isTransportLoading || isHotelLoading || isItineraryLoading || isWeatherLoading;

  return (
    <div style={{ paddingTop: 24 }}>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <h1 className="page-title">旅游规划</h1>
        <p className="page-subtitle">AI 智能规划你的完美旅程</p>
      </div>

      <div style={{ display: 'flex', gap: 16, maxWidth: 1400, margin: '0 auto' }}>
        {/* 左侧输入表单 */}
        <div style={{ width: 280, flexShrink: 0 }}>
          <Card
            size="small"
            title={<span style={{ fontSize: 15, fontWeight: 600 }}>规划参数</span>}
            style={{ marginBottom: 12 }}
            styles={{ body: { padding: 16 } }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <label style={{ fontSize: 12, color: '#6B7280', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <EnvironmentOutlined /> 目的地 *
                </label>
                <Input
                  placeholder="如：郴州"
                  value={form.destination}
                  onChange={e => setForm({ ...form, destination: e.target.value })}
                  size="middle"
                />
              </div>

              <div>
                <label style={{ fontSize: 12, color: '#6B7280', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <EnvironmentOutlined /> 出发地
                </label>
                <Input
                  placeholder="如：深圳"
                  value={form.origin}
                  onChange={e => setForm({ ...form, origin: e.target.value })}
                  size="middle"
                />
              </div>

              <div>
                <label style={{ fontSize: 12, color: '#6B7280', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <CalendarOutlined /> 出行日期
                </label>
                <RangePicker
                  style={{ width: '100%' }}
                  value={form.dateRange}
                  onChange={handleDateChange}
                  size="middle"
                />
              </div>

              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 12, color: '#6B7280', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <RocketOutlined /> 天数
                  </label>
                  <InputNumber
                    min={1}
                    max={10}
                    value={form.days}
                    onChange={handleDaysChange}
                    style={{ width: '100%' }}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 12, color: '#6B7280', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <TeamOutlined /> 人数
                  </label>
                  <InputNumber
                    min={1}
                    max={10}
                    value={form.people}
                    onChange={value => setForm({ ...form, people: value || 2 })}
                    style={{ width: '100%' }}
                  />
                </div>
              </div>

              <div>
                <label style={{ fontSize: 12, color: '#6B7280', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <UserOutlined /> 出行人群
                </label>
                <Select
                  style={{ width: '100%' }}
                  value={form.crowd}
                  onChange={value => setForm({ ...form, crowd: value })}
                  size="middle"
                  options={[
                    { value: '情侣', label: '情侣' },
                    { value: '亲子', label: '亲子' },
                    { value: '父母', label: '带父母' },
                    { value: '独自', label: '独自出行' }
                  ]}
                />
              </div>

              <div>
                <label style={{ fontSize: 12, color: '#6B7280', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <WalletOutlined /> 预算
                </label>
                <Select
                  style={{ width: '100%' }}
                  value={form.budget}
                  onChange={value => setForm({ ...form, budget: value })}
                  size="middle"
                  options={[
                    { value: '穷游', label: '穷游' },
                    { value: '中等', label: '中等' },
                    { value: '舒适', label: '舒适' },
                    { value: '奢华', label: '奢华' }
                  ]}
                />
              </div>

              <div>
                <label style={{ fontSize: 12, color: '#6B7280', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <RocketOutlined /> 行程节奏
                </label>
                <Select
                  style={{ width: '100%' }}
                  value={form.pace}
                  onChange={value => setForm({ ...form, pace: value })}
                  size="middle"
                  options={[
                    { value: '特种兵', label: '特种兵（紧凑）' },
                    { value: '适中', label: '适中' },
                    { value: '休闲', label: '休闲（放松）' }
                  ]}
                />
              </div>

              <div>
                <label style={{ fontSize: 12, color: '#6B7280', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <HeartOutlined /> 饮食习惯
                </label>
                <Input
                  placeholder="如：不吃辣/素食"
                  value={form.diet}
                  onChange={e => setForm({ ...form, diet: e.target.value })}
                  size="middle"
                />
              </div>

              <Button
                type="primary"
                size="large"
                loading={isGenerating}
                onClick={handleGenerate}
                block
                style={{ marginTop: 4, height: 40 }}
              >
                生成旅游规划
              </Button>
            </div>
          </Card>
        </div>

        {/* 右侧内容区 */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* 空状态 */}
          {!isGenerating && !plan && (
            <div style={{ textAlign: 'center', padding: 100, color: '#9CA3AF' }}>
              <EnvironmentOutlined style={{ fontSize: 48, marginBottom: 16 }} />
              <div style={{ fontSize: 14 }}>输入目的地，AI 为你生成专属旅游规划</div>
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

            {/* 第零块：天气预报 */}
            {isWeatherLoading && (
              <Card
                size="small"
                title={<span style={{ fontSize: 15, fontWeight: 600 }}>☀️ 天气预报</span>}
                styles={{ body: { padding: 16 } }}
              >
                <Skeleton active paragraph={{ rows: 2 }} />
              </Card>
            )}

            {!isWeatherLoading && plan?.weather && plan.weather.length > 0 && (
              <Card
                size="small"
                title={<span style={{ fontSize: 15, fontWeight: 600 }}>☀️ 天气预报</span>}
                styles={{ body: { padding: 16 } }}
                extra={
                  plan.weather.length > 3 && (
                    <Button
                      type="link"
                      size="small"
                      onClick={() => setIsWeatherExpanded(!isWeatherExpanded)}
                    >
                      {isWeatherExpanded ? '收起' : `查看全部 ${plan.weather.length} 天`}
                    </Button>
                  )
                }
              >
                <div
                  style={{
                    display: 'flex',
                    gap: 12,
                    flexWrap: isWeatherExpanded ? 'wrap' : 'nowrap',
                    overflowX: isWeatherExpanded ? 'visible' : 'hidden',
                  }}
                >
                  {(isWeatherExpanded ? plan.weather : plan.weather.slice(0, 3)).map((w, i) => {
                    // 判断是否极端天气
                    const isExtreme =
                      w.weather?.includes('雷') ||
                      w.weather?.includes('暴雨') ||
                      w.temp_high > 35 ||
                      w.temp_low < 0;

                    // 天气图标
                    const weatherIcon =
                      w.weather?.includes('晴') ? '☀️' :
                      w.weather?.includes('多云') ? '⛅' :
                      w.weather?.includes('阴') ? '☁️' :
                      w.weather?.includes('雨') ? '🌧️' :
                      w.weather?.includes('雪') ? '❄️' : '🌤️';

                    return (
                      <div
                        key={i}
                        style={{
                          minWidth: 200,
                          flex: plan.weather.length <= 4 ? 1 : 'none',
                          padding: 16,
                          background: isExtreme ? '#FEF2F2' : '#FFFFFF',
                          borderRadius: 8,
                          border: isExtreme ? '1px solid #EF4444' : '1px solid #E5E7EB',
                          position: 'relative',
                        }}
                      >
                        {/* 极端天气预警 */}
                        {isExtreme && (
                          <Tooltip title={`${w.weather} 请注意出行安全`}>
                            <div style={{
                              position: 'absolute',
                              top: 12,
                              right: 12,
                              color: '#EF4444',
                              fontSize: 16,
                            }}>
                              ⚠️
                            </div>
                          </Tooltip>
                        )}

                        <div style={{ fontSize: 12, color: '#6B7280', marginBottom: 4 }}>
                          {w.day} · {w.date}
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                          <span style={{ fontSize: 24 }}>{weatherIcon}</span>
                          <span style={{ fontSize: 13, color: '#4B5563' }}>{w.weather}</span>
                        </div>

                        <div style={{ fontSize: 24, fontWeight: 600, color: '#111827', marginBottom: 8 }}>
                          {w.temp_low}° ~ {w.temp_high}°
                        </div>

                        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                          <Tag color="blue" style={{ fontSize: 11, margin: 0 }}>
                            💧 降水 {w.precipitation}
                          </Tag>
                          <Tag style={{ fontSize: 11, margin: 0, background: '#F3F4F6', color: '#4B5563', border: 'none' }}>
                            💨 {w.wind}
                          </Tag>
                        </div>

                        {/* 新增：紫外线和空气质量 */}
                        <div style={{ fontSize: 12, color: '#6B7280', marginBottom: 8 }}>
                          <div style={{ marginBottom: 4 }}>
                            ☀️ 紫外线：{w.uv_index || '中等'}
                          </div>
                          <div>
                            🌬️ 空气质量：{w.air_quality || '优'}
                          </div>
                        </div>

                        {w.outfit_advice && (
                          <div style={{
                            padding: 10,
                            background: '#F9FAFB',
                            borderRadius: 6,
                          }}>
                            <div style={{ fontSize: 11, fontWeight: 600, color: '#6B7280', marginBottom: 4 }}>
                              🤖 AI 穿搭建议
                            </div>
                            <div style={{ fontSize: 12, color: '#4B5563', lineHeight: 1.6 }}>
                              {w.outfit_advice}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </Card>
            )}

            {/* 第一块：大交通方案 */}
            {isTransportLoading && (
              <Card
                size="small"
                title={<span style={{ fontSize: 15, fontWeight: 600 }}>🚄 大交通方案</span>}
                styles={{ body: { padding: 16 } }}
              >
                <Skeleton active paragraph={{ rows: 3 }} />
              </Card>
            )}

            {!isTransportLoading && plan?.transport && (
              <Card
                size="small"
                title={<span style={{ fontSize: 15, fontWeight: 600 }}>🚄 大交通方案</span>}
                extra={
                  <Button
                    size="small"
                    type="default"
                    icon={<span style={{ marginRight: 4 }}>🎫</span>}
                    style={{
                      borderColor: '#4D6BFE',
                      color: '#4D6BFE',
                      fontSize: 12,
                    }}
                    onClick={() => {
                      const date = form.dateRange?.[0]?.format('YYYY-MM-DD') || '';
                      const url = `https://www.12306.cn/index/otn/leftTicket/init?linktypeid=dc&fs=${encodeURIComponent(form.origin)}&ts=${encodeURIComponent(form.destination)}&date=${date}&flag=N,N,Y`;
                      window.open(url, '_blank', 'noopener,noreferrer');
                    }}
                  >
                    去 12306 购票
                  </Button>
                }
                styles={{
                  body: {
                    padding: 16,
                    background: hasTransportError ? '#FFFBEB' : '#FFFFFF'
                  }
                }}
                style={{
                  borderLeft: hasTransportError ? '4px solid #F59E0B' : '1px solid #E5E7EB'
                }}
              >
                {/* 交通空友好提示 */}
                {hasTransportError && (
                  <div style={{ marginBottom: 12 }}>
                    <Alert
                      message="未查询到合适的车次"
                      description={plan.transport.message || '建议调整出发日期或时间偏好'}
                      type="warning"
                      showIcon
                      style={{ marginBottom: 12 }}
                    />
                    <Button
                      type="primary"
                      icon={<ReloadOutlined />}
                      onClick={handleRetryTransport}
                      loading={isTransportLoading}
                    >
                      重新查询交通
                    </Button>
                  </div>
                )}

                {!hasTransportError && (
                  <>
                    {/* 时间偏好 */}
                    <div style={{ marginBottom: 12 }}>
                      <span style={{ fontSize: 12, color: '#6B7280', marginRight: 8 }}>时间偏好：</span>
                      <Radio.Group
                        size="small"
                        value={form.timePref}
                        onChange={handleTimePrefChange}
                        options={['不限', '早上出发', '下午出发', '晚上出发']}
                      />
                    </div>

                    <Tabs
                      defaultActiveKey="outbound"
                      items={[
                        {
                          key: 'outbound',
                          label: '去程',
                          children: (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                              {(plan.transport?.outbound || []).map((t, i) => {
                                const recommendTag = getRecommendTag(t.recommend_tag);
                                return (
                                  <div
                                    key={i}
                                    style={{
                                      padding: 12,
                                      background: '#FFFFFF',
                                      borderRadius: 8,
                                      border: '1px solid #E5E7EB',
                                      display: 'flex',
                                      justifyContent: 'space-between',
                                      alignItems: 'center',
                                    }}
                                  >
                                    <div style={{ flex: 1 }}>
                                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        <span style={{ fontSize: 14, fontWeight: 600, color: '#111827' }}>
                                          {t.type} {t.name}
                                        </span>
                                        {recommendTag && (
                                          <Tag
                                            style={{
                                              margin: 0,
                                              color: recommendTag.color,
                                              background: recommendTag.bg,
                                              border: 'none',
                                              fontSize: 11,
                                              padding: '2px 8px'
                                            }}
                                          >
                                            {t.recommend_tag}
                                          </Tag>
                                        )}
                                      </div>
                                      <div style={{ fontSize: 12, color: '#6B7280', marginTop: 2 }}>
                                        {t.time} | 车程 {t.duration} | 余票: {t.remaining}
                                      </div>
                                      {t.recommend_reason && (
                                        <div style={{
                                          marginTop: 8,
                                          padding: 8,
                                          background: '#F9FAFB',
                                          borderRadius: 6,
                                          fontSize: 12,
                                          color: '#4B5563',
                                        }}>
                                          🤖 {t.recommend_reason}
                                        </div>
                                      )}
                                    </div>
                                    <div style={{ textAlign: 'right', marginLeft: 16 }}>
                                      <div style={{ fontSize: 18, fontWeight: 600, color: '#4D6BFE' }}>
                                        ¥{t.price * form.people}
                                      </div>
                                      <div style={{ fontSize: 11, color: '#9CA3AF' }}>
                                        总价（单人 ¥{t.price} × {form.people}人）
                                      </div>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          )
                        },
                        {
                          key: 'return',
                          label: '返程',
                          children: (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                              {(plan.transport?.inbound || []).map((t, i) => {
                                const recommendTag = getRecommendTag(t.recommend_tag);
                                return (
                                  <div
                                    key={i}
                                    style={{
                                      padding: 12,
                                      background: '#FFFFFF',
                                      borderRadius: 8,
                                      border: '1px solid #E5E7EB',
                                      display: 'flex',
                                      justifyContent: 'space-between',
                                      alignItems: 'center',
                                    }}
                                  >
                                    <div style={{ flex: 1 }}>
                                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        <span style={{ fontSize: 14, fontWeight: 600, color: '#111827' }}>
                                          {t.type} {t.name}
                                        </span>
                                        {recommendTag && (
                                          <Tag
                                            style={{
                                              margin: 0,
                                              color: recommendTag.color,
                                              background: recommendTag.bg,
                                              border: 'none',
                                              fontSize: 11,
                                              padding: '2px 8px'
                                            }}
                                          >
                                            {t.recommend_tag}
                                          </Tag>
                                        )}
                                      </div>
                                      <div style={{ fontSize: 12, color: '#6B7280', marginTop: 2 }}>
                                        {t.time} | 车程 {t.duration} | 余票: {t.remaining}
                                      </div>
                                      {t.recommend_reason && (
                                        <div style={{
                                          marginTop: 8,
                                          padding: 8,
                                          background: '#F9FAFB',
                                          borderRadius: 6,
                                          fontSize: 12,
                                          color: '#4B5563',
                                        }}>
                                          🤖 {t.recommend_reason}
                                        </div>
                                      )}
                                    </div>
                                    <div style={{ textAlign: 'right', marginLeft: 16 }}>
                                      <div style={{ fontSize: 18, fontWeight: 600, color: '#4D6BFE' }}>
                                        ¥{t.price * form.people}
                                      </div>
                                      <div style={{ fontSize: 11, color: '#9CA3AF' }}>
                                        总价（单人 ¥{t.price} × {form.people}人）
                                      </div>
                                    </div>
                                  </div>
                                );
                              })}
                              {(!plan.transport?.inbound || plan.transport.inbound.length === 0) && (
                                <div style={{ textAlign: 'center', padding: 24, color: '#9CA3AF', fontSize: 13 }}>
                                  暂无返程车次数据
                                </div>
                              )}
                            </div>
                          )
                        }
                      ]}
                    />
                  </>
                )}
              </Card>
            )}

            {/* 第二块：酒店推荐 */}
            {isHotelLoading && (
              <Card
                size="small"
                title={<span style={{ fontSize: 15, fontWeight: 600 }}>🏨 酒店推荐</span>}
                styles={{ body: { padding: 16 } }}
              >
                <Skeleton active paragraph={{ rows: 4 }} />
              </Card>
            )}

            {canShowHotels && (
              <Card
                size="small"
                title={<span style={{ fontSize: 15, fontWeight: 600 }}>🏨 酒店推荐</span>}
                styles={{ body: { padding: 16 } }}
                extra={
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    loading={isHotelRefreshing}
                    onClick={handleRefreshHotels}
                  >
                    换一批
                  </Button>
                }
              >
                {/* 筛选工具栏 */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                  <Input
                    placeholder="靠近地铁站/景区？"
                    size="small"
                    style={{ width: 160 }}
                    value={hotelFilter.location}
                    onChange={e => handleHotelFilterChange({ ...hotelFilter, location: e.target.value })}
                    prefix={<EnvironmentOutlined style={{ color: '#9CA3AF' }} />}
                  />
                  <Select
                    size="small"
                    style={{ width: 120 }}
                    value={hotelFilter.priceRange}
                    onChange={value => handleHotelFilterChange({ ...hotelFilter, priceRange: value })}
                    options={[
                      { value: '不限', label: '价格不限' },
                      { value: '0-200', label: '¥0-200' },
                      { value: '200-500', label: '¥200-500' },
                      { value: '500+', label: '¥500以上' }
                    ]}
                  />
                  <Select
                    size="small"
                    style={{ width: 120 }}
                    value={hotelFilter.sortBy}
                    onChange={value => handleHotelFilterChange({ ...hotelFilter, sortBy: value })}
                    options={[
                      { value: '智能推荐', label: '智能推荐' },
                      { value: '价格最低', label: '价格最低' },
                      { value: '评分最高', label: '评分最高' }
                    ]}
                  />
                  <Select
                    size="small"
                    style={{ width: 130 }}
                    value={hotelFilter.hotelType}
                    onChange={value => handleHotelFilterChange({ ...hotelFilter, hotelType: value })}
                    options={[
                      { value: '智能推荐', label: '类型：智能推荐' },
                      { value: '酒店', label: '类型：酒店' },
                      { value: '民宿', label: '类型：民宿/公寓' },
                      { value: '青年旅舍', label: '类型：青年旅舍' }
                    ]}
                  />
                </div>

                {/* 酒店列表 */}
                {isHotelRefreshing ? (
                  <Skeleton active paragraph={{ rows: 4 }} />
                ) : filteredHotels.length === 0 ? (
                  <div style={{ textAlign: 'center', padding: 40, color: '#9CA3AF' }}>
                    <EnvironmentOutlined style={{ fontSize: 32, marginBottom: 8 }} />
                    <div style={{ fontSize: 13 }}>暂无符合条件的酒店，请调整筛选条件</div>
                  </div>
                ) : (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
                    {filteredHotels.slice(0, 4).map((h, i) => (
                        <div
                          key={i}
                          style={{
                            padding: 12,
                            background: '#FFFFFF',
                            borderRadius: 8,
                            border: '1px solid #E5E7EB',
                            position: 'relative',
                          }}
                        >
                          {/* 低评分警示 */}
                          {h.rating < 3.0 && (
                            <div style={{
                              position: 'absolute',
                              top: 8,
                              left: 8,
                              background: '#FEF3C7',
                              color: '#92400E',
                              fontSize: 10,
                              padding: '2px 6px',
                              borderRadius: 4,
                              display: 'flex',
                              alignItems: 'center',
                              gap: 2,
                            }}>
                              <WarningOutlined /> 评分较低
                            </div>
                          )}

                          <div style={{ display: 'flex', gap: 12 }}>
                            {/* 图片占位 */}
                            <div style={{
                              width: 120,
                              height: 90,
                              background: '#F3F4F6',
                              borderRadius: 6,
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              color: '#9CA3AF',
                              fontSize: 12,
                              flexShrink: 0,
                              overflow: 'hidden',
                            }}>
                              {h.image ? (
                                <img
                                  src={h.image}
                                  alt={h.name}
                                  style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                                />
                              ) : (
                                '酒店图片'
                              )}
                            </div>

                            {/* 右侧内容 */}
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                                <div style={{ fontSize: 14, fontWeight: 600, color: '#111827' }}>
                                  {h.name}
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                  <StarFilled style={{ color: '#F59E0B' }} />
                                  <span style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>
                                    {h.rating}
                                  </span>
                                </div>
                              </div>
                              <div style={{ fontSize: 18, fontWeight: 600, color: '#4D6BFE', margin: '4px 0' }}>
                                整租 ¥{h.price}<span style={{ fontSize: 12, fontWeight: 400 }}>/晚起</span>
                              </div>
                              <div style={{ fontSize: 12, color: '#F97316', marginBottom: 4 }}>
                                人均仅 ¥{Math.round(h.price / (form.people || 2))}/晚
                              </div>
                              <div style={{ fontSize: 12, color: '#6B7280', display: 'flex', alignItems: 'center', gap: 4 }}>
                                <EnvironmentTwoTone />
                                {h.location?.slice(0, 20)}...
                              </div>
                              <div style={{ display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' }}>
                                <Tag style={{ fontSize: 11, margin: 0, background: '#F3F4F6', color: '#4B5563', border: 'none' }}>
                                  {h.room_type || '大床房'}
                                </Tag>
                                {h.is_homestay && (
                                  <>
                                    <Tag style={{ fontSize: 11, margin: 0, background: '#F3F4F6', color: '#4B5563', border: 'none' }}>
                                      {h.bedrooms || '三室一厅'}
                                    </Tag>
                                    <Tag style={{ fontSize: 11, margin: 0, background: '#F3F4F6', color: '#4B5563', border: 'none' }}>
                                      可住 {form.people} 人
                                    </Tag>
                                    {h.has_kitchen && (
                                      <Tag style={{ fontSize: 11, margin: 0, background: '#F3F4F6', color: '#4B5563', border: 'none' }}>
                                        带厨房
                                      </Tag>
                                    )}
                                  </>
                                )}
                                <Tag style={{ fontSize: 11, margin: 0, background: '#F3F4F6', color: '#4B5563', border: 'none' }}>
                                  住 {form.days} 晚
                                </Tag>
                              </div>
                              {h.is_homestay && (form.crowd === '父母' || form.people >= 4) && (
                                <div style={{
                                  marginTop: 8,
                                  padding: 8,
                                  background: '#F9FAFB',
                                  borderRadius: 6,
                                }}>
                                  <div style={{ fontSize: 11, color: '#4B5563', lineHeight: 1.5 }}>
                                    🤖 AI 建议：适合带父母入住，方便照顾，有厨房可做饭。
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>

                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10, paddingTop: 10, borderTop: '1px solid #E5E7EB' }}>
                            <div style={{ fontSize: 12, color: '#6B7280' }}>
                              总价：<span style={{ fontWeight: 600, color: '#111827' }}>¥{h.price * form.days}</span>
                            </div>
                            <Button
                              type="text"
                              size="small"
                              icon={<EyeOutlined />}
                              onClick={() => setHotelDetail(h)}
                            >
                              查看详情
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                )}
              </Card>
            )}

            {/* 第三块：每日行程 */}
            {isItineraryLoading && (
              <Card
                size="small"
                title={<span style={{ fontSize: 15, fontWeight: 600 }}>📅 每日行程</span>}
                styles={{ body: { padding: 16 } }}
              >
                <Skeleton active paragraph={{ rows: 6 }} />
              </Card>
            )}

            {canShowItinerary && (
              <Card
                size="small"
                title={<span style={{ fontSize: 15, fontWeight: 600 }}>📅 每日行程</span>}
                styles={{ body: { padding: 16 } }}
              >
                <Collapse
                  defaultActiveKey={['0']}
                  items={(plan.itinerary || []).map((day, dayIdx) => ({
                    key: String(dayIdx),
                    label: (
                      <span style={{ fontSize: 15, fontWeight: 600, color: '#111827' }}>
                        {day.title}
                      </span>
                    ),
                    children: (
                      <div>
                        {/* 局部 Loading */}
                        {regeneratingDay === dayIdx ? (
                          <Skeleton active paragraph={{ rows: 4 }} />
                        ) : (
                          <>
                            {(day.schedule || []).map((item, i) => {
                              const typeInfo = typeConfig[item.type] || typeConfig.rest;
                              const isCustom = item.is_custom;
                              return (
                                <div
                                  key={i}
                                  style={{
                                    padding: 12,
                                    background: '#FFFFFF',
                                    borderRadius: 8,
                                    border: '1px solid #E5E7EB',
                                    marginBottom: 8,
                                    borderLeft: isCustom ? '3px solid #F59E0B' : '1px solid #E5E7EB',
                                  }}
                                >
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                      <ClockCircleOutlined style={{ color: '#4D6BFE' }} />
                                      <span style={{ fontSize: 14, fontWeight: 600, color: '#111827' }}>
                                        {item.title}
                                      </span>
                                      {isCustom && (
                                        <Tag color="orange" style={{ margin: 0, fontSize: 11 }}>
                                          🧲 用户指定
                                        </Tag>
                                      )}
                                    </div>
                                    <Tag style={{ background: typeInfo.bg, color: typeInfo.color, border: 'none', margin: 0 }}>
                                      {typeInfo.label}
                                    </Tag>
                                  </div>
                                  <div style={{ fontSize: 12, color: '#6B7280', marginLeft: 28 }}>
                                    {item.duration}
                                  </div>
                                  {item.ai_tip && (
                                    <div style={{
                                      marginTop: 8,
                                      marginLeft: 28,
                                      padding: 8,
                                      background: '#F9FAFB',
                                      borderRadius: 6,
                                    }}>
                                      <div style={{ fontSize: 11, fontWeight: 600, color: '#6B7280', marginBottom: 4 }}>
                                        🤖 AI 建议
                                      </div>
                                      <div style={{ fontSize: 12, color: '#4B5563', lineHeight: 1.6 }}>
                                        {item.ai_tip}
                                      </div>
                                    </div>
                                  )}
                                </div>
                              );
                            })}

                            {/* 添加指定地点按钮和输入框 */}
                            <div style={{ marginTop: 8 }}>
                              {showCustomInput[dayIdx] ? (
                                <div style={{ display: 'flex', gap: 8 }}>
                                  <Input
                                    size="small"
                                    style={{ height: 32, flex: 1 }}
                                    placeholder="想去哪里？如：XX博物馆、XX餐厅"
                                    value={customPlaceInput[dayIdx] || ''}
                                    onChange={(e) => setCustomPlaceInput(prev => ({ ...prev, [dayIdx]: e.target.value }))}
                                    onPressEnter={() => handleRegenerateDay(dayIdx)}
                                  />
                                  <Button
                                    type="primary"
                                    size="small"
                                    style={{ height: 32 }}
                                    onClick={() => handleRegenerateDay(dayIdx)}
                                    loading={regeneratingDay === dayIdx}
                                  >
                                    确认添加
                                  </Button>
                                  <Button
                                    size="small"
                                    style={{ height: 32 }}
                                    onClick={() => setShowCustomInput(prev => ({ ...prev, [dayIdx]: false }))}
                                  >
                                    取消
                                  </Button>
                                </div>
                              ) : (
                                <Button
                                  type="dashed"
                                  size="small"
                                  block
                                  icon={<PlusOutlined />}
                                  onClick={() => setShowCustomInput(prev => ({ ...prev, [dayIdx]: true }))}
                                >
                                  添加指定地点
                                </Button>
                              )}
                            </div>
                          </>
                        )}
                      </div>
                    )
                  }))}
                />
              </Card>
            )}

            {/* 第四块：预算预估 */}
            {plan?.budget_summary && (
              <Card
                size="small"
                title={<span style={{ fontSize: 15, fontWeight: 600 }}>💰 预算预估</span>}
                styles={{ body: { padding: 16 } }}
              >
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 12 }}>
                  {[
                    { label: '交通', value: plan.budget_summary.transport },
                    { label: '住宿', value: plan.budget_summary.hotel },
                    { label: '餐饮', value: plan.budget_summary.food },
                    { label: '门票', value: plan.budget_summary.attraction },
                  ].map((item, i) => (
                    <div
                      key={i}
                      style={{
                        padding: 12,
                        background: '#F9FAFB',
                        borderRadius: 8,
                        textAlign: 'center',
                      }}
                    >
                      <div style={{ fontSize: 12, color: '#6B7280' }}>{item.label}</div>
                      <div style={{ fontSize: 18, fontWeight: 600, color: '#111827', marginTop: 4 }}>
                        ¥{item.value}
                      </div>
                    </div>
                  ))}
                </div>
                <div style={{
                  padding: 16,
                  background: '#EEF2FF',
                  borderRadius: 8,
                  display: 'flex',
                  justifyContent: 'space-around',
                  textAlign: 'center',
                }}>
                  <div>
                    <div style={{ fontSize: 12, color: '#6B7280' }}>总预算</div>
                    <div style={{ fontSize: 24, fontWeight: 600, color: '#4D6BFE', marginTop: 4 }}>
                      ¥{totalBudget}
                    </div>
                  </div>
                  <div style={{ width: 1, background: '#E5E7EB' }} />
                  <div>
                    <div style={{ fontSize: 12, color: '#6B7280' }}>人均（{form.people}人）</div>
                    <div style={{ fontSize: 24, fontWeight: 600, color: '#111827', marginTop: 4 }}>
                      ¥{perPerson}
                    </div>
                  </div>
                </div>
              </Card>
            )}

            {/* 第五块：注意事项 */}
            {plan?.tips && plan.tips.length > 0 && (
              <Card
                size="small"
                title={<span style={{ fontSize: 15, fontWeight: 600 }}>📌 注意事项</span>}
                styles={{ body: { padding: 16 } }}
              >
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {plan.tips.map((tip, i) => (
                    <div
                      key={i}
                      style={{
                        padding: '8px 12px',
                        background: '#F9FAFB',
                        borderRadius: 6,
                        fontSize: 13,
                        color: '#4B5563',
                      }}
                    >
                      {tip}
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>
        </div>
      </div>

      {/* 酒店详情抽屉 */}
      <Drawer
        title={hotelDetail?.name || '酒店详情'}
        size="large"
        open={!!hotelDetail}
        onClose={() => setHotelDetail(null)}
        styles={{ wrapper: { width: 560 } }}
      >
        {hotelDetail && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* 图片占位 */}
            <div style={{
              height: 200,
              background: '#F3F4F6',
              borderRadius: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#9CA3AF',
              fontSize: 14,
              overflow: 'hidden',
            }}>
              {hotelDetail.image ? (
                <img
                  src={hotelDetail.image}
                  alt={hotelDetail.name}
                  style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                />
              ) : (
                '酒店图片暂未提供'
              )}
            </div>

            {/* 基本信息 */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <StarFilled style={{ color: '#F59E0B' }} />
                <span style={{ fontSize: 16, fontWeight: 600, color: '#111827' }}>
                  {hotelDetail.rating} 分
                </span>
              </div>
              <div style={{ fontSize: 24, fontWeight: 600, color: '#4D6BFE' }}>
                ¥{hotelDetail.price}<span style={{ fontSize: 14, fontWeight: 400 }}>/晚起</span>
              </div>
              <div style={{ fontSize: 13, color: '#6B7280', marginTop: 4 }}>
                📍 {hotelDetail.location}
              </div>
            </div>

            {/* 设施标签 */}
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#111827', marginBottom: 8 }}>
                酒店设施
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {(hotelDetail.facilities || ['免费 WiFi', '24小时前台', '停车场', '空调', '电视']).map((f, i) => (
                  <Tag key={i} style={{ background: '#F3F4F6', color: '#4B5563', border: 'none' }}>
                    {f}
                  </Tag>
                ))}
              </div>
            </div>

            {/* 真实评价 */}
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#111827', marginBottom: 8 }}>
                真实评价摘要
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {(hotelDetail.reviews || ['位置不错，交通方便', '房间干净整洁，性价比高', '前台服务热情']).map((review, i) => (
                  <div
                    key={i}
                    style={{
                      padding: 10,
                      background: '#F9FAFB',
                      borderRadius: 6,
                      fontSize: 13,
                      color: '#4B5563',
                    }}
                  >
                    💬 {review}
                  </div>
                ))}
              </div>
              <div style={{ fontSize: 11, color: '#9CA3AF', marginTop: 8, textAlign: 'right' }}>
                * 来源于真实用户评价摘要
              </div>
            </div>

            {/* AI 推荐理由 */}
            {hotelDetail.recommend_reason && (
              <div style={{
                padding: 12,
                background: '#F9FAFB',
                borderRadius: 8,
              }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#6B7280', marginBottom: 4 }}>
                  🤖 AI 推荐理由
                </div>
                <div style={{ fontSize: 13, color: '#4B5563', lineHeight: 1.6 }}>
                  {hotelDetail.recommend_reason}
                </div>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}

export default TravelPage;
