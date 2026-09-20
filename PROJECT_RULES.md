# AI 学习工作台 - 项目开发规范
本文档为项目开发硬约束，所有改动必须遵守。

## 一、Git 与代码提交
改文件前必须告知改了什么（文件名 + 改动点），不静默改。

清数据 / 删表 / 删库前必须先问用户同意 + 备份。

部署方式变更（如 Docker 起/停、中间件单独部署）必须先问用户决策。

提交信息遵循 feat:、fix:、docs:、refactor: 等规范前缀。

## 二、数据库规范
### 2.1 表设计规范
所有业务表必须包含以下字段：

```sql
id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
is_deleted BOOLEAN DEFAULT false,
created_at TIMESTAMP DEFAULT NOW(),
updated_at TIMESTAMP DEFAULT NOW(),
deleted_at TIMESTAMP
```
- 逻辑删除：所有删除操作都是 UPDATE is_deleted = true，不物理删除。

- 查询过滤：所有 SELECT 必须带 WHERE is_deleted = false。

- 外键：用 UUID，不用自增 ID。

### 2.2 变更同步
新增表 / 加字段 必须同步到 init.sql。

不直接 ALTER TABLE 线上库，先改模型，再写迁移 SQL（推荐引入 Alembic）。

数据库改动后主动告知用户字段名和类型。

### 2.3 现有表清单
表名	说明
documents	文档元数据
chunks	文档分片（pgvector）
chat_sessions	对话会话
chat_messages	对话消息
chat_shares	对话分享
skills	技能
skill_tasks	技能子任务
learning_paths	学习路径
users	用户
pdf_tasks	PDF 异步任务
news	AI 日报

## 三、后端规范（FastAPI）
### 3.1 日志与异常
所有业务接口必须打日志，用 from app.utils.logger import get_logger。

日志级别：INFO 正常流程，WARNING 业务异常，ERROR 系统错误。

关键节点：入参、调用 LLM、数据库写入、外部 API 调用。

必须使用 try/except 包裹，配合 db.rollback() 保证数据一致性。

### 3.2 API 路由与异步任务
路由前缀统一 /api/{模块}。

返回格式：成功 {"status": "ok"} 或数据对象；失败 {"error": "..."}。

长耗时任务（如 PDF 解析、日报生成）必须采用 异步任务 + 轮询 模式（参照 pdf_tasks 表状态轮询）。

### 3.3 LLM 流式输出规范
必须使用 SSE (Server-Sent Events) 返回流式数据，禁止前端长时间白屏等待。

必须实现 SSE 心跳保活机制（如每 15 秒推送 event: ping），防止前端因超时而断开连接。

在 Prompt 中必须严格约束输出格式（例如：要求代码块使用标准换行符，严禁将多行代码挤在同一行）。

对于超长文本/代码块，必须采用 流式分片渲染（Uncommitted / Committed 机制），未闭合的代码块用 <pre> 兜底，严禁出现半截代码解析错乱。

### 3.4 配置
环境变量从 .env 读取，不硬编码。

敏感信息（API Key、密码）不提交到 Git。

## 四、前端规范（React + Ant Design）
### 4.1 主题 Token 体系（对标 DeepSeek）
采用三层 CSS 变量（Token）体系，严禁在组件中硬编码颜色、圆角、阴影。

#### 1. 静态尺度层（基础值）
仅用于定义全局常量，不直接在业务组件中使用。

```css
:root {
  --ds-static-color-brown: #8b6f47;
  --ds-static-color-beige: #faf8f5;
  --ds-static-radius: 12px;
  --ds-static-shadow: 0 4px 12px rgba(0,0,0,0.05);
}
```
#### 2. 语义别名层（核心映射）
将静态值映射为具有业务语义的变量，主题切换的本质就是动态修改这层变量。

```css
:root, [data-theme='light'] {
  --ds-alias-bg: var(--ds-static-color-beige);
  --ds-alias-primary: var(--ds-static-color-brown);
  --ds-alias-card-bg: #ffffff;
  --ds-alias-card-border: #f0ebe3;
  --ds-alias-text-primary: #2c2c2c;
  --ds-alias-text-secondary: #999999;
  --ds-alias-hover-shadow: var(--ds-static-shadow);
}
```
#### 3. 特定用途层
用于极其具体的场景，如滚动条颜色。

```css
:root {
  --ds-scrollbar-thumb: #ddd;
}
```
### 4.2 主题运行时（Theme Runtime）
主题类型：必须支持 light（浅色）、dark（深色）、system（跟随系统）三种模式。

实现机制：使用 Zustand 创建 useTheme 全局状态；切换主题时，动态修改 document.documentElement 的 data-theme 属性。

系统跟随：利用 window.matchMedia('(prefers-color-scheme: dark)') 监听系统主题变化。

持久化：主题偏好必须存入 localStorage，并在 index.html 的 <head> 中加入同步脚本，在页面加载前应用主题，绝对禁止出现刷新页面时的“白屏闪烁”。

Ant Design 适配：必须配置 <ConfigProvider theme={...}>，将 Ant Design 的默认算法（theme.darkAlgorithm / theme.defaultAlgorithm）与自定义 Token 结合。

### 4.3 组件与布局规范
【最高优先级】结构布局优先：当 Ant Design 默认组件无法实现复杂居中、Flex/Grid 布局时，允许使用原生 <div> 结合内联样式或 CSS 模块实现，不强制使用 Ant Design 组件。允许为了布局而在 App.css 中写全局类名。

布局隔离：主内容区使用 Layout.Content 嵌套 div 进行动态 maxWidth 控制，避免 Antd 默认 Flex 属性与 width: 100% 冲突导致“居中失效”。

组件拆分：严禁将多个页面写在 App.jsx 中。必须拆分为 src/pages/ 和 src/components/，App.jsx 只负责路由与全局布局。

性能隔离：高频组件（如聊天输入框）必须使用 React.memo 包裹，状态必须内部消化，严禁输入一个字导致整个页面重渲染。

### 4.4 状态管理
状态分层：

全局状态（用户信息、主题、会话列表）使用 Zustand 管理。

局部 UI 状态（如弹窗开关、输入框内容）使用 useState。

接口反馈：接口数据加载后必须主动告知用户成功/失败（如使用 Antd message）。

状态隔离：严禁在 async 函数中直接使用外层闭包状态，必须通过 useRef 或函数式更新处理。

### 4.5 Markdown 渲染规范
引入 react-syntax-highlighter 做代码高亮，oneDark 主题。

必须为代码块添加 一键复制 按钮，并处理代码块超出容器时的横向滚动（overflow-x: auto，white-space: pre）。

列表渲染必须交由原生 CSS 控制（list-style: decimal/disc），严禁使用内联样式覆盖 display 属性，防止排版错乱。

## 五、部署规范
PostgreSQL / Redis / MinIO 用 Docker Compose 启动。

前后端本地跑（不在 Docker 里跑，因为 embedding 慢）。

一键启动：docker compose up -d（中间件）+ 本地 uvicorn + npm run dev。

生产环境必须全容器化，通过 Nginx 反向代理，并配置 SSL。

六、安全规范
JWT 鉴权，24 小时过期。

不回显 token / 密码 / API Key。

CORS 配置明确白名单，不配置 *。

所有用户输入必须经过 Pydantic 校验，防止提示词注入和 XSS 攻击。