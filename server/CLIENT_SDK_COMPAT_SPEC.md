# Mem0 开源服务端兼容官方客户端 SDK 二次开发规格说明书

## 一、开发背景与目标

### 1.1 核心问题
当前 Mem0 开源服务端（`server/main.py`）提供的是一套简化的 RESTful API（如 `/memories`、`/search`），而官方商业客户端 SDK（如 Node.js 的 `mem0ai` npm 包、Python 的 `mem0ai` client 以及各类 Agent 扩展插件 `@mem0/pi-agent-plugin`）是直接针对 Mem0 Platform 云服务规范设计的。两者在以下方面存在**严重协议脱节**：
1. **探活握手**：客户端初始化与每次请求前强制调用 `GET /v1/ping/`，开源版无此路由直接报 404；
2. **记忆写入**：客户端调用 `POST /v3/memories/add/`，且携带 `app_id`、`run_id`、`custom_categories` 等多租户字段；
3. **列表检索**：客户端 `getAll()` 发起的是 `POST /v3/memories/`（非 GET），且期望返回分页包裹对象 `{ results, count }`；
4. **单条修改**：客户端调用 `PUT /v1/memories/{id}/`；
5. **鉴权请求头**：官方 SDK 默认发送 `Authorization: Token <key>`（非 Bearer）。

### 1.2 二次开发目标
在开源版 `server/` 中新增 **兼容路由层（API v1/v3 Compatibility Layer）**，使任何使用官方 SDK 的客户端只需配置：
```bash
export MEM0_API_URL="http://your-server-ip:8888"
export MEM0_API_KEY="your-api-key"
```
即可**零改动、零补丁**直接连接自建开源服务端，且完整支持持久化记忆、语义召回、多租户项目隔离与会话过滤。

---

## 二、待实现接口清单与契约规格

### 1. 探活握手接口
- **方法与路径**：`GET /v1/ping/`
- **鉴权**：允许 `Authorization: Token <key>`、`Bearer <key>` 或未启用鉴权时放行
- **说明**：官方 SDK 每次执行操作前检查 `if (this.telemetryId === "") await this.ping()`。若返回非 ok 或 userEmail 为空，客户端会直接中断操作并抛出 `APIError`。
- **响应契约（JSON）**：
  ```json
  {
    "status": "ok",
    "message": "pong",
    "orgId": "org_self_hosted",
    "projectId": "proj_self_hosted",
    "userEmail": "admin@localhost"
  }
  ```

---

### 2. 记忆写入接口
- **方法与路径**：`POST /v3/memories/add/`
- **请求体规格（JSON）**：
  ```json
  {
    "messages": [
      { "role": "user", "content": "用户发言内容" },
      { "role": "assistant", "content": "助手回复内容" }
    ],
    "user_id": "用户唯一标识",
    "agent_id": "可选 Agent 标识",
    "app_id": "项目标识（如 Git Root）",
    "run_id": "会话 ID",
    "metadata": {},
    "infer": true,
    "custom_categories": []
  }
  ```
- **核心逻辑要求**：
  1. 将 `app_id`、`run_id` 自动合并进 `metadata`，确保存储到底层向量库与关系库；
  2. 支持传入纯字符串（当 `messages` 为文本时的兼容）；
  3. **限流容灾保护**：若底层 LLM 触发限流（429 / `provider_rate_limited`），可提供重试机制或自动降级为 `infer: false` 存储纯文本，避免直接向客户端抛 502。
- **响应契约（JSON）**：
  ```json
  {
    "results": [
      {
        "id": "mem_uuid",
        "memory": "提炼后的记忆内容",
        "event": "ADD"
      }
    ]
  }
  ```

---

### 3. 记忆列表查询接口
- **方法与路径**：`POST /v3/memories/`
- **Query 参数**：`page`（默认 1）、`page_size`（默认 100）
- **请求体规格（JSON）**：
  ```json
  {
    "filters": {
      "user_id": "...",
      "agent_id": "...",
      "app_id": "...",
      "run_id": "..."
    }
  }
  ```
- **核心逻辑要求**：
  1. 调用底层 `Memory.get_all()`；
  2. 若底层仅支持按 `user_id` 查询，需在内存中二次过滤 `metadata.app_id` 与 `metadata.run_id`；
  3. 按照客户端要求的格式包装返回结果。
- **响应契约（JSON）**：
  ```json
  {
    "results": [
      {
        "id": "mem_uuid",
        "memory": "记忆内容",
        "user_id": "...",
        "metadata": {
          "app_id": "...",
          "run_id": "..."
        },
        "created_at": "2026-10-21T00:00:00Z",
        "updated_at": "2026-10-21T00:00:00Z"
      }
    ],
    "count": 1,
    "next": null,
    "previous": null
  }
  ```

---

### 4. 语义检索接口
- **方法与路径**：`POST /v3/memories/search/`
- **请求体规格（JSON）**：
  ```json
  {
    "query": "搜索关键字",
    "output_format": "v1.1",
    "threshold": 0.3,
    "filters": {
      "user_id": "...",
      "app_id": "..."
    }
  }
  ```
- **核心逻辑要求**：
  1. 调用底层 `Memory.search()` 进行向量相似度匹配；
  2. 按照 `threshold` 过滤分数低于阈值的无关条目；
  3. 返回列表保持 `score` / `similarity` 与 `metadata`。
- **响应契约（JSON）**：
  ```json
  {
    "results": [
      {
        "id": "mem_uuid",
        "memory": "相关记忆文本",
        "score": 0.88,
        "metadata": {
          "app_id": "..."
        }
      }
    ]
  }
  ```

---

### 5. 单条记忆修改与删除
- **修改接口**：`PUT /v1/memories/{memory_id}/`
  - 请求体：`{ "text": "新文本", "metadata": {} }`
  - 核心逻辑：调用 `Memory.update(memory_id, data=text)`
  - 响应：`{ "message": "Memory updated successfully" }`
- **删除单条**：`DELETE /v1/memories/{memory_id}/`
  - 核心逻辑：调用 `Memory.delete(memory_id)`
  - 响应：`{ "message": "Memory deleted successfully" }`
- **批量删除**：`DELETE /v1/memories/`
  - Query 参数：`?user_id=...&app_id=...`
  - 核心逻辑：调用 `Memory.delete_all(...)`

---

## 三、文件设计与实现结构

```text
server/
├── routers/
│   ├── compat_v3.py        <-- [核心新增] 包含上述 /v1 与 /v3 契约端点
│   ├── api_keys.py
│   ├── auth.py
│   └── entities.py
├── main.py                 <-- [挂载点] app.include_router(compat_router)
├── errors.py               <-- [异常拦截] 捕获 upstream rate limit
├── test_compat_api.py      <-- [联调测试] 端到端请求校验脚本
└── CLIENT_SDK_COMPAT_SPEC.md <-- 本规格文档
```
