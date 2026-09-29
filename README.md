# AI 学习工作台

个人 AI 学习工作台，集成 RAG 智能问答、AI 日报、GitHub 周榜、知识库管理、技能管理、学习路径等功能。

## 技术栈

### 前端
| 技术 | 版本 | 说明 |
|------|------|------|
| React | 19 | UI 框架 |
| Vite | 8 | 构建工具 |
| Ant Design | 6 | UI 组件库 |
| zustand | 4 | 状态管理 |
| React Router | 6 | 路由管理 |
| React Markdown | 9 | Markdown 渲染 |
| SSE (EventSource) | - | 流式输出 |

### 后端
| 技术 | 版本 | 说明 |
|------|------|------|
| FastAPI | 0.115 | Web 框架 |
| SQLAlchemy | 2.0 | ORM |
| Pydantic | 2 | 数据校验 |
| httpx | 0.27 | HTTP 客户端 |
| Uvicorn | 0.32 | ASGI 服务器 |

### 数据层
| 技术 | 版本 | 说明 |
|------|------|------|
| PostgreSQL | 16 | 关系型数据库 |
| pgvector | 0.7 | 向量扩展 |
| Redis | 7 | 缓存 |
| MinIO | 最新 | 对象存储 |

### AI 模型
| 模型 | 用途 | 部署方式 |
|------|------|----------|
| bge-m3 | Embedding | 本地 GPU |
| bge-reranker-v2-m3 | Reranker | 本地 GPU |
| qwen-plus | LLM 对话 | DashScope API |

## 系统架构

```
┌─────────────┐     SSE/REST      ┌─────────────┐
│   前端      │ ◄──────────────► │   后端      │
│  React +    │                   │  FastAPI    │
│  AntD       │                   │             │
└─────────────┘                   └──────┬──────┘
                                          │
        ┌─────────────┬─────────────┬────┴────┬─────────────┐
        ▼             ▼             ▼         ▼             ▼
   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐
   │PostgreSQL│  │  Redis  │  │  MinIO  │  │bge-m3   │  │ qwen    │
   │ +pgvector│  │         │  │         │  │Reranker │  │ DashScope│
   └─────────┘  └─────────┘  └─────────┘  └─────────┘  └─────────┘
```

### 数据流

**RAG 问答流程：**
1. 用户输入问题 → 前端 SSE 连接后端
2. 问题 Embedding → bge-m3
3. 向量检索 → pgvector 相似度匹配
4. Reranker 重排序 → bge-reranker-v2-m3
5. 上下文拼接 → Prompt 构建
6. LLM 流式生成 → qwen-plus
7. SSE 流式返回 → 前端逐字渲染

## 项目结构

```
ai-workbench/
├── backend/
│   ├── app/
│   │   ├── api/                 # 路由层
│   │   │   ├── auth.py          # 认证接口
│   │   │   ├── chat.py          # 对话接口
│   │   │   ├── documents.py     # 文档管理接口
│   │   │   ├── news.py          # AI 日报/GitHub 接口
│   │   │   ├── skills.py        # 技能管理接口
│   │   │   ├── learning.py      # 学习路径接口
│   │   │   ├── travel.py       # 旅游规划接口
│   │   │   ├── recommend.py     # 推荐接口
│   │   │   └── review.py       # 复习接口
│   │   ├── services/            # 业务逻辑层
│   │   │   ├── chunker.py       # 文档分块
│   │   │   ├── embedding.py     # Embedding 服务
│   │   │   ├── reranker.py      # Reranker 服务
│   │   │   ├── retriever.py     # 向量检索
│   │   │   ├── llm.py           # LLM 调用
│   │   │   ├── parser.py        # 文档解析
│   │   │   └── news.py          # 日报生成
│   │   ├── models/              # 数据模型
│   │   │   ├── document.py      # 文档模型
│   │   │   └── user.py          # 用户模型
│   │   ├── utils/              # 工具类
│   │   │   └── logger.py        # 日志工具
│   │   ├── config.py            # 配置管理
│   │   ├── database.py          # 数据库连接
│   │   └── main.py             # 应用入口
│   ├── alembic/                # 数据库迁移
│   ├── init.sql                # 初始化 SQL
│   ├── requirements.txt        # Python 依赖
│   └── Dockerfile
│
├── frontend/
│   ├── src/
│   │   ├── pages/              # 页面组件
│   │   │   ├── ChatPage.jsx        # 智能问答
│   │   │   ├── DashboardPage.jsx   # 工作台总览
│   │   │   ├── NewsPage.jsx         # AI 日报
│   │   │   ├── GitHubPage.jsx      # GitHub 周榜
│   │   │   ├── SkillsPage.jsx       # 技能管理
│   │   │   ├── LearningPage.jsx     # 学习路径
│   │   │   ├── TravelPage.jsx      # 旅游规划
│   │   │   └── DocsPage.jsx          # 文档管理
│   │   ├── components/          # 公共组件
│   │   │   ├── ChatInput.jsx       # 聊天输入框
│   │   │   ├── MemoMessageBubble.jsx # 消息气泡
│   │   │   └── MarkdownComponents.jsx # Markdown 组件
│   │   ├── store/              # 状态管理
│   │   │   └── useAppStore.js     # zustand store
│   │   ├── App.jsx             # 主应用
│   │   ├── App.css             # 全局样式
│   │   ├── main.jsx           # 入口
│   │   └── ErrorBoundary.jsx   # 错误边界
│   ├── package.json
│   ├── vite.config.js
│   └── Dockerfile
│
├── models/                     # 本地模型
│   └── bge-m3/                 # Embedding 模型
├── docker-compose.yml          # Docker 编排
└── README.md
```

## 环境准备

### 系统要求
- Windows 10/11 或 Linux
- Python 3.11
- Node.js 18+
- CUDA 12.1（可选，GPU 加速）
- Docker & Docker Compose

### 第一步：创建 Python 环境
```bash
conda create -n ai-workbench python=3.11 -y
conda activate ai-workbench
```

### 第二步：安装 PyTorch（GPU 版）
```bash
# CUDA 12.1
pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121

# CPU 版（无 GPU）
pip install torch==2.5.1
```

### 第三步：安装后端依赖
```bash
cd backend
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 第四步：安装前端依赖
```bash
cd frontend
npm install
```

### 第五步：配置环境变量
创建 `backend/.env` 文件：
```env
# 数据库
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ai_workbench

# Redis
REDIS_URL=redis://localhost:6379/0

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=ai-workbench

# LLM API
DASHSCOPE_API_KEY=your_dashscope_api_key

# 文档解析
MINERU_API_KEY=your_mineru_api_key

# 前端 API 地址
VITE_API_BASE=http://localhost:8000
```

## 启动服务

### 第一步：启动基础服务（Docker）
```bash
docker-compose up -d
```
启动以下服务：
- PostgreSQL 16（端口 5432）
- Redis 7（端口 6379）
- MinIO（端口 9000）

### 第二步：初始化数据库
```bash
cd backend
psql -U postgres -d ai_workbench -f init.sql
```

### 第三步：启动后端
```bash
cd backend
conda activate ai-workbench
uvicorn app.main:app --reload --port 8000
```

后端地址：http://localhost:8000
API 文档：http://localhost:8000/docs

### 第四步：启动前端
```bash
cd frontend
npm run dev
```

前端地址：http://localhost:5174（5173 被占用时自动切换）

## 数据库表设计

| 表名 | 说明 | 主要字段 |
|------|------|----------|
| `users` | 用户表 | id, username, email, password_hash |
| `documents` | 文档元数据 | id, title, source, chunk_count, uploaded_at |
| `chunks` | 文档分块 | id, document_id, content, embedding(vector), chunk_index |
| `chat_sessions` | 对话会话 | id, user_id, title, created_at, updated_at |
| `chat_messages` | 对话消息 | id, session_id, role, content, sources, created_at |
| `daily_news` | 每日日报 | id, date, summary, created_at |
| `news_tasks` | 日报生成任务 | id, status, summary, error |
| `github_projects` | GitHub 项目缓存 | name, detail, created_at |
| `github_teardowns` | AI 拆解缓存 | name, detail, created_at |
| `github_trending_cache` | 周榜缓存 | cache_date, data(JSON) |
| `skills` | 技能表 | id, name, category, goal, progress, status |
| `skill_tasks` | 技能子任务 | id, skill_id, title, completed, sort_order |
| `skill_resources` | 学习资源 | id, skill_id, url, title |
| `learning_paths` | 学习路径 | id, goal, nodes(JSON), completed_nodes(JSON), created_at |
| `travel_plans` | 旅游规划 | id, destination, origin, days, plan_data(JSON), created_at |
| `review_items` | 复习项 | id, title, review_count, last_reviewed |
| `pdf_tasks` | PDF 异步任务 | id, status, chunks, error |

## 核心模块设计

### 1. RAG 智能问答模块

**流程：**
```
用户问题 → Embedding → 向量检索 → Reranker → LLM 生成 → SSE 流式返回
```

**关键文件：**
- `backend/app/api/chat.py` - 对话 API
- `backend/app/services/retriever.py` - 向量检索
- `backend/app/services/embedding.py` - Embedding 服务
- `backend/app/services/reranker.py` - Reranker 服务
- `frontend/src/pages/ChatPage.jsx` - 聊天页面
- `frontend/src/components/MemoMessageBubble.jsx` - 消息气泡

**特性：**
- SSE 流式输出，逐字渲染
- 多轮对话记忆
- 引用来源展示（折叠式）
- 右侧对话目录导航
- 首次对话自动生成标题

### 2. 知识库管理模块

**流程：**
```
文件上传 → 异步解析 → 文档分块 → Embedding → 向量存储
```

**关键文件：**
- `backend/app/api/documents.py` - 文档 API
- `backend/app/services/chunker.py` - 文档分块
- `backend/app/services/parser.py` - 文档解析
- `frontend/src/pages/DocsPage.jsx` - 文档管理页面

**特性：**
- 支持 .md / .txt / .ipynb / .pdf 格式
- PDF 异步解析任务
- 文档搜索/重命名/下载/删除
- 知识块数量统计

### 3. AI 日报模块

**流程：**
```
定时触发 → RSS 抓取 → LLM 摘要 → 存储 → 前端展示
```

**关键文件：**
- `backend/app/api/news.py` - 日报 API
- `backend/app/services/news.py` - 日报生成服务
- `frontend/src/pages/NewsPage.jsx` - 日报页面

**特性：**
- 异步生成任务
- 生成/复制/导出 Markdown
- 已读打卡
- 缓存机制

### 4. GitHub 周榜模块

**流程：**
```
页面加载 → 查缓存 → 无缓存 → GitHub API → LLM 翻译 → 存缓存
```

**关键文件：**
- `backend/app/api/news.py` - GitHub API
- `frontend/src/pages/GitHubPage.jsx` - 周榜页面

**特性：**
- 内存缓存（当天有效）
- 排名徽章（金/银/铜）
- 项目搜索
- 项目介绍 + AI 拆解
- 强制刷新按钮

### 5. 技能管理模块

**流程：**
```
创建技能 → AI 拆解子任务 → 完成子任务 → 进度更新
```

**关键文件：**
- `backend/app/api/skills.py` - 技能 API
- `frontend/src/pages/SkillsPage.jsx` - 技能管理页面

**特性：**
- 技能 CRUD
- AI 一键拆解子任务
- 进度条展示
- 状态管理（待学习/学习中/已掌握）

### 6. 学习路径模块

**流程：**
```
输入目标 → LLM 生成路径 → 分阶段展示 → 加入技能库
```

**关键文件：**
- `backend/app/api/learning.py` - 学习路径 API
- `frontend/src/pages/LearningPage.jsx` - 学习路径页面

**特性：**
- 分阶段路径生成
- 任务完成状态（持久化）
- 加入技能库
- 整体进度统计
- AI 帮教（解释学习任务）

### 7. 旅游规划模块

**流程：**
```
输入参数 → 并发获取（天气+景点+酒店+交通） → LLM 生成规划 → SSE 流式返回
```

**关键文件：**
- `backend/app/api/travel.py` - 旅游规划 API
- `frontend/src/pages/TravelPage.jsx` - 旅游规划页面

**特性：**
- 高德地图 API（天气、景点 POI、酒店 POI）
- 聚合数据火车票查询
- 每日天气卡片（温度、风力、降水概率、AI 穿搭建议）
- 行程时间轴（每日时间轴 + AI 点评）
- 酒店推荐卡片（评分、价格、位置）
- 交通方案对比（车次、时间、价格、推荐理由）
- 预算统计（交通/住宿/餐饮/门票/总计）

## API 接口

### 对话相关
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat/stream` | SSE 流式对话 |
| GET | `/api/chat/sessions` | 会话列表 |
| GET | `/api/chat/sessions/:id/messages` | 历史消息 |
| PUT | `/api/chat/sessions/:id` | 更新会话标题 |
| DELETE | `/api/chat/sessions/:id` | 删除会话 |

### 文档相关
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/documents/list` | 文档列表 |
| POST | `/api/documents/upload` | 上传文档 |
| POST | `/api/documents/upload-async` | 异步上传 PDF |
| GET | `/api/documents/:id/preview` | 预览文档 |
| GET | `/api/documents/:id/download` | 下载文档 |
| PUT | `/api/documents/:id` | 重命名文档 |
| DELETE | `/api/documents/:id` | 删除文档 |

### 日报/GitHub 相关
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/news/today` | 今日日报 |
| POST | `/api/news/generate` | 生成日报 |
| GET | `/api/news/task/:task_id` | 查询任务状态 |
| GET | `/api/news/github-trending` | GitHub 周榜 |
| GET | `/api/news/github-search` | GitHub 搜索 |
| GET | `/api/news/github-project-detail` | 项目介绍 |
| GET | `/api/news/github-project-teardown` | AI 拆解 |

### 技能相关
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/skills` | 技能列表 |
| POST | `/api/skills` | 创建技能 |
| PUT | `/api/skills/:id` | 更新技能 |
| DELETE | `/api/skills/:id` | 删除技能 |

### 学习路径相关
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/learning` | 学习路径列表 |
| POST | `/api/learning/generate` | 生成学习路径 |
| PUT | `/api/learning/:id/node` | 更新节点完成状态 |
| DELETE | `/api/learning/:id` | 删除学习路径 |
| POST | `/api/learning/ai-tutor` | AI 帮教（SSE 流式） |

### 旅游规划相关
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/travel/plan` | 生成旅游规划（SSE 流式） |

## 前端架构

### 状态管理（zustand）

```javascript
// store/useAppStore.js
{
  // 认证
  token, setToken,
  user, setUser,

  // 页面导航
  page, setPage,
  collapsed, setCollapsed,

  // 对话
  messages, setMessages,
  sessionId, setSessionId,
  loadingSession, setLoadingSession,
  sessionsList, setSessionsList,
}
```

### 路由结构

```
/           → DashboardPage（工作台总览）
/chat       → ChatPage（智能问答）
/news       → NewsPage（AI 日报）
/github     → GitHubPage（GitHub 周榜）
/skills     → SkillsPage（技能管理）
/learning   → LearningPage（学习路径）
/travel     → TravelPage（旅游规划）
/docs       → DocsPage（文档管理）
```

### UI 设计规范

**配色方案（DeepSeek 风格冷色调）：**

| 变量 | 浅色 | 深色 | 说明 |
|------|------|------|------|
| `--bg-primary` | #F9FAFB | #111827 | 主背景 |
| `--bg-secondary` | #FFFFFF | #1F2937 | 卡片背景 |
| `--bg-hover` | #F3F4F6 | #374151 | 悬浮背景 |
| `--text-primary` | #111827 | #F9FAFB | 主文字 |
| `--text-secondary` | #4B5563 | #D1D5DB | 次要文字 |
| `--text-tertiary` | #9CA3AF | #6B7280 | 辅助文字 |
| `--border-color` | #E5E7EB | #374151 | 边框 |
| `--accent-color` | #4D6BFE | #6366F1 | 主色 |
| `--accent-bg` | #EEF2FF | #312E81 | 主色背景 |

**字体层级：**
- 页面标题：20px / 600 / #111827
- 卡片标题：15px / 600
- 正文：14px / 1.6 行高
- 辅助文字：12px / #9CA3AF

**设计特点：**
- 极简紧凑，高信息密度
- 隐藏滚动条
- 细边框卡片 + 悬浮阴影
- 左右分栏布局（侧边栏 260px）
- 聊天内容区 max-width 900px 居中
- 深色/浅色主题切换

## 配置信息

### 后端配置（`backend/app/config.py`）

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `DATABASE_URL` | postgresql://... | 数据库连接 |
| `REDIS_URL` | redis://localhost | Redis 连接 |
| `MINIO_ENDPOINT` | localhost:9000 | MinIO 地址 |
| `DASHSCOPE_API_KEY` | - | 通义千问 API Key |
| `MINERU_API_KEY` | - | MinerU 解析 API Key |
| `AMAP_KEY` | - | 高德地图 API Key（天气、POI 搜索） |
| `JUHE_TRAIN_KEY` | - | 聚合数据火车票 API Key |

### 前端配置（`frontend/.env`）

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `VITE_API_BASE` | http://localhost:8000 | 后端 API 地址 |

## 开发指南

### 常用命令

| 命令 | 说明 |
|------|------|
| `uvicorn app.main:app --reload --port 8000` | 后端热重载 |
| `npm run dev` | 前端热重载 |
| `docker-compose up -d` | 启动 PG/Redis/MinIO |
| `alembic upgrade head` | 数据库迁移 |

### 调试技巧

- 后端 API 文档：http://localhost:8000/docs
- 前端调试：浏览器 DevTools → Network → SSE 请求
- 数据库查看：pgAdmin / DBeaver 连接 PostgreSQL

## 功能清单

### 已完成
- [x] RAG 智能问答（SSE 流式输出）
- [x] 多轮对话记忆（PostgreSQL 存储）
- [x] 对话历史管理（列表/重命名/删除）
- [x] 右侧对话目录导航
- [x] 知识库管理（上传/列表/搜索/重命名/下载/删除）
- [x] PDF 异步解析任务
- [x] AI 日报（LLM 摘要 + 导出 Markdown）
- [x] GitHub 周榜（内存缓存 + 排名徽章）
- [x] GitHub 项目搜索
- [x] 项目介绍 + AI 拆解（数据库缓存）
- [x] 技能管理（CRUD + AI 拆解子任务）
- [x] 学习路径（分阶段生成 + 加入技能库 + 完成状态持久化 + AI 帮教）
- [x] 旅游规划（天气+景点+酒店+交通+行程时间轴）
- [x] Dashboard 总览（统计卡片 + 最近动态）
- [x] 深色/浅色主题切换
- [x] 国际化（中/英切换）
- [x] 侧边栏折叠/展开

### 待优化
- [ ] 数据备份脚本
- [ ] 旅游规划历史记录保存
- [ ] 更多模型支持
- [ ] 用户权限管理

## License

MIT
