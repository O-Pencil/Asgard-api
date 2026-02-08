# Asgard Agent 集成平台 - 研发任务拆解

## 项目现状

### 前端原型完成度
| 模块 | 完成度 | 状态 |
|------|--------|------|
| Console 控制台 | 80% | UI 完整，静态数据 |
| Agent 市场 | 55% | 布局完整，筛选/搜索逻辑缺失 |
| Layout 布局 | 90% | 结构清晰 |
| 整体架构 | - | 需升级路由和状态管理 |

### 当前问题
- 搜索功能未实现
- 多选筛选逻辑缺失（能力标签/Context/价格）
- 无后端服务
- 无数据库
- 无 API 网关
- Agent 引擎未实现

---

## 研发任务拆解

### 第一阶段：基础设施搭建 (Foundation)

#### 1.1 后端项目初始化
- [ ] 创建 Python + FastAPI 项目
- [ ] 配置 Pydantic 模型
- [ ] 设置 ESLint/Black 格式化
- [ ] 配置环境变量管理 (.env)
- [ ] Docker 化准备

#### 1.2 数据库设计
- [ ] 用户表 (users)
- [ ] API Key 表 (api_keys)
- [ ] Agent 表 (agents)
- [ ] 调用记录表 (usage_logs)
- [ ] 余额记录表 (balances)

#### 1.3 前端架构升级
- [ ] 升级路由：useState → React Router DOM
- [ ] 引入状态管理：Zustand
- [ ] 引入数据请求：TanStack Query
- [ ] 添加页面：Agent 详情页、文档中心、用户设置页

---

### 第二阶段：后端核心服务 (Core Backend)

#### 2.1 认证鉴权系统
- [ ] 用户注册/登录 API (JWT)
- [ ] Asgard Key 生成（UUID + 单向哈希存储）
- [ ] Key 验证中间件
- [ ] IP 白名单功能

#### 2.2 API 网关
- [ ] 统一入口 `/v1/chat/completions`
- [ ] 兼容 OpenAI 协议格式
- [ ] 请求路由逻辑（根据 model 字段路由到 Agent）
- [ ] SSE 流式输出支持

#### 2.3 Agent 管理 API
- [ ] Agent 列表查询
- [ ] Agent 启用/停用
- [ ] Agent 详情获取

#### 2.4 用量统计 API
- [ ] Token 计数服务
- [ ] 调用记录存储
- [ ] 日/周/月统计数据聚合
- [ ] CSV 导出功能

---

### 第三阶段：前端功能完善 (Frontend)

#### 3.1 Agent 市场功能
- [ ] 搜索功能（名称/能力关键词）
- [ ] 多选筛选（能力标签、Context Window、价格区间）
- [ ] 排序功能（最新、热度、价格）
- [ ] Agent 详情页
- [ ] 启用/停用交互

#### 3.2 控制台功能
- [ ] API Key 管理（全功能：创建、复制、轮换、禁用、删除）
- [ ] 集成指南（完善配置模板）
- [ ] 用量图表（接入 Recharts）
- [ ] 余额实时显示

#### 3.3 用户系统
- [ ] 用户登录/注册页面
- [ ] 余额展示与充值入口
- [ ] 个人设置

---

### 第四阶段：Agent 引擎实现 (Agent Engine)

#### 4.1 Agent 基础框架
- [ ] Agent 接口定义
- [ ] 消息格式标准化
- [ ] Context 管理
- [ ] 流式响应输出器

#### 4.2 最小可行 Agent（MVP 验证）
- [ ] 代码重构 Agent（开发类）
- [ ] 韩寒风格 Agent（写作类）

---

### 第五阶段：集成与工具 (Integration)

#### 5.1 CLI 工具
- [ ] 本地文件流交互
- [ ] 认证配置
- [ ] Agent 调用命令

#### 5.2 第三方集成
- [ ] Cursor 配置模板
- [ ] VS Code (Continue) 配置模板
- [ ] Notion/飞书集成说明
- [ ] Raycast 集成说明

---

## 依赖关系图

```
Phase 1: 基础设施
    ↓
Phase 2: 后端核心 ←→ Phase 3: 前端完善（并行）
    ↓
Phase 4: Agent 引擎（依赖 Phase 2 网关）
    ↓
Phase 5: CLI + 第三方集成
```

---

## 建议执行顺序（优先级）

1. **P0**：后端网关 + Agent 基础框架（核心价值）
2. **P1**：数据库 + 认证 + 前端控制台
3. **P2**：Agent 市场完善
4. **P3**：最小可行 Agent 实现
5. **P4**：CLI + 第三方集成

---

## 技术选型

| 模块 | 推荐方案 |
|------|----------|
| 后端框架 | Python + FastAPI |
| 数据库 | PostgreSQL |
| ORM | SQLAlchemy |
| 前端路由 | React Router DOM |
| 状态管理 | Zustand |
| 数据请求 | TanStack Query |
| 图表 | Recharts |
| 部署 | Docker |

---

## 仓库结构建议

```
asgard-platform/
├── asgard-web/          # 前端 (当前 asgard-prd 迁移)
├── asgard-api/          # 后端 API 服务
├── asgard-agent/        # Agent 引擎实现
├── asgard-cli/         # CLI 工具
└── docs/               # 文档
```
