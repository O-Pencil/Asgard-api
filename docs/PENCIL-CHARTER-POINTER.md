# Pencil 生态发展路线 — Charter Pointer

> **生态发展路线唯一源头**：[nanoPencil/docs/pencil-platform-charter.md](https://github.com/O-Pencil/nanoPencil/blob/main/docs/pencil-platform-charter.md)
>
> 本文是 **Asgard-api** 在 Pencil 生态中的 pointer 文档。所有生态级事实以 charter 为准；本文只描述 Asgard-api 本仓的角色 + 边界 + 跨项目事实查表入口。

## 1. 本仓在 Pencil 生态中的位置

Asgard-api 是 **Asgard Platform 的后端服务**，是 Pencil 生态 4 项目中 Asgard Platform 的 **API 实现层**（charter §3 / §4）。

技术栈：FastAPI + SQLAlchemy（async）+ PostgreSQL。

**所属 monorepo**：`O-Pencil/Asgard-platform`（作为 `packages/api` 子模块）。

**与 Pencil-Agent-Gateway 的关系**：Asgard-api 通过 HTTP 代理调用 Gateway，**不** import Gateway 代码（charter §3 责任边界）。Asgard-api 处理用户系统、API Key、PencilAgent CRUD、用量记录、计费策略；Gateway 处理 Agent 引擎托管 + OpenAI 兼容 HTTP serving。

## 2. 本仓承载的核心能力

阶段三已完成（charter §6）：

- `PencilAgentBackend` service：PencilAgent CRUD + Gateway sync + usage logging
- `SINGLE_USER_MODE`：JWT + APIKey 双重鉴权
- OpenAI 兼容 `/v1/chat/completions` 透传 + 用量回写
- 用量统计与配额检查接口

## 3. 本仓在 charter §7 各工作线中的角色

charter §7 列出阶段四的 6 条工作线。Asgard-api 直接承载其中：

| 工作线 | 是否承载 | 备注 |
|---|---|---|
| A 工具回传 v0.2 | ⚪ 不参与 | Gateway + nano-pencil + editor 主导；Asgard 作为代理透传 |
| **B 计费与用量闭环** | ✅ **主要承载** | 用量 API、配额检查、计费策略实现 |
| **C 容器隔离与编排** | ✅ 部分承载 | 与运维协同，提供编排所需的 Agent 元数据 API |
| D Soul/Memory 配置 UI | ⚪ 配套数据 API | UI 由 Asgard-web 主导；本仓提供 CRUD 接口 |
| E Channel 拆仓 | ⚪ 不参与 | — |
| F Rust 性能层 | ⚪ 不参与 | — |

详细里程碑见 charter §7.2 / §7.3。

## 4. 跨项目事实查表入口

| 想找什么 | 去 charter 哪一节 |
|---|---|
| 4 项目拓扑与依赖关系 | §2 |
| 各项目责任边界（含 Asgard vs Gateway 划分） | §3 |
| 术语表（PencilAgent / Pencil / AgentInstance 等） | §4 |
| 协议策略（HTTP+SSE 主线 / ACP 本地 / PCP 内部） | §5 |
| 阶段叙事（一→四） | §6 |
| 跨项目工作线 / 里程碑 | §7 |
| 跨项目决策记录 | §8 |
| 文档维护机制 | §10 |

## 5. charter 失同步时怎么办

发现 charter 跟实际不符时，**不要修改本仓 pointer**，而是按 charter §10.1 流程：

1. 直接在 `O-Pencil/nanoPencil` 仓库提 PR 改 charter（源头修），或开 issue 说明问题
2. charter 改动会通过 `nanoPencil/.github/workflows/charter-sync-notify.yml` 自动通知本仓
3. 收到 charter-sync issue 后，本仓再决定是否需要刷新本 pointer

**本仓 pointer 只在以下情况修改**：
- charter §3 关于 Asgard 责任边界的描述变化
- §7 工作线 B / C / D 里 Asgard-api 承载范围的变化
- 本仓与 Gateway 的接口契约结构性变化

详见 charter §10.1（修改流程）、§10.2（防止重复）、§10.3（同步检测自动化）。
