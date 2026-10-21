"""
API v1/v3 Compatibility Router for Official Mem0 Client SDKs

本路由模块专为对齐官方闭源商业云平台 (Mem0 Platform) 的通信契约而设计。
官方客户端 SDK (如 Node.js 的 mem0ai、Python 的 mem0ai Client 以及 @mem0/pi-agent-plugin)
在默认情况下均面向 /v1/ping/、/v3/memories/add/、/v3/memories/ 等云端路由发起请求。
通过在此处提供平替接口，允许官方原版客户端一字不改直接接入自建开源服务端。
"""

import logging
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from server_state import get_memory_instance

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Client SDK Compatibility"])


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic 契约模型 (与官方客户端 SDK 传输结构严格一致)
# ─────────────────────────────────────────────────────────────────────────────

class PingResponse(BaseModel):
    """
    探活响应结构。
    官方客户端在 SDK 构造函数或调用前会检查 telemetryId 是否为空，
    必须返回 status='ok' 和非空的 userEmail，否则客户端判定握手失败并中止执行。
    """
    status: str = "ok"
    message: str = "pong"
    orgId: str = "org_self_hosted"
    projectId: str = "proj_self_hosted"
    userEmail: str = "admin@localhost"


class MessageItem(BaseModel):
    role: str
    content: str


class AddMemoryV3Request(BaseModel):
    """
    记忆写入请求体。
    客户端可能传入多轮对话列表 messages，或者单段文本字符串。
    同时携带项目作用域 (app_id) 与会话作用域 (run_id)。
    """
    messages: Optional[Union[List[MessageItem], List[Dict[str, Any]], str]] = None
    text: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    app_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    infer: Optional[bool] = True
    custom_categories: Optional[List[Any]] = None


class SearchMemoryV3Request(BaseModel):
    """
    语义检索请求体。
    官方客户端 search() 发送嵌套的 filters 以及 output_format/threshold 参数。
    """
    query: str
    output_format: Optional[str] = "v1.1"
    threshold: Optional[float] = 0.3
    filters: Optional[Dict[str, Any]] = Field(default_factory=dict)
    metadata: Optional[Dict[str, Any]] = None


class ListMemoriesV3Request(BaseModel):
    """
    记忆列表查询请求体。
    官方客户端 getAll() 使用 POST /v3/memories/ 并通过请求体传入 filters 过滤。
    """
    filters: Optional[Dict[str, Any]] = Field(default_factory=dict)


class UpdateMemoryV1Request(BaseModel):
    text: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


# ─────────────────────────────────────────────────────────────────────────────
# 路由实现
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/v1/ping/", response_model=PingResponse, summary="官方客户端连接探活握手")
async def ping(authorization: Optional[str] = Header(None)) -> PingResponse:
    """
    响应官方 SDK 的探活探测。
    返回虚拟的组织与用户标识，让客户端清空未连接标志，顺利进入后续业务接口。
    """
    return PingResponse()


@router.post("/v3/memories/add/", summary="官方客户端记忆写入与对话提取")
async def add_memory_v3(payload: AddMemoryV3Request) -> Dict[str, Any]:
    """
    处理客户端的记忆添加或对话自动捕获 (auto-capture)。
    需要将 app_id、run_id 编织进 metadata，以便多项目、多会话隔离。
    """
    m = get_memory_instance()
    if not m:
        raise HTTPException(status_code=503, detail="Memory engine not initialized")

    # 规范化消息内容
    content: Any = payload.messages
    if not content and payload.text:
        content = payload.text
    if not content:
        raise HTTPException(status_code=400, detail="Missing messages or text in request body")

    # 转换 Pydantic 消息对象为标准 dict 列表供底层的 LLM 提取处理
    if isinstance(content, list):
        serialized_messages = []
        for item in content:
            if hasattr(item, "model_dump"):
                serialized_messages.append(item.model_dump())
            elif isinstance(item, dict):
                serialized_messages.append(item)
            else:
                serialized_messages.append({"role": "user", "content": str(item)})
        content = serialized_messages

    # 合并作用域标识至 metadata，实现项目与会话维度的数据切分
    final_metadata = dict(payload.metadata or {})
    if payload.app_id:
        final_metadata["app_id"] = payload.app_id
        final_metadata["appId"] = payload.app_id
    if payload.run_id:
        final_metadata["run_id"] = payload.run_id
        final_metadata["runId"] = payload.run_id

    try:
        # 调用底层开源 SDK 的 add 方法
        # 若底层大模型限流，此处可根据架构策略进行重试或降级为 infer=False
        result = m.add(
            content,
            user_id=payload.user_id,
            agent_id=payload.agent_id,
            run_id=payload.run_id,
            metadata=final_metadata,
            infer=payload.infer if payload.infer is not None else True,
        )
        
        # 兼容官方客户端期望的包装格式
        if isinstance(result, dict) and "results" in result:
            return result
        elif isinstance(result, list):
            return {"results": result}
        return {"results": [result]}
    except Exception as e:
        logger.exception("Failed to add memory in v3 compatibility router: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/memories/", summary="官方客户端按条件批量检索记忆列表")
async def list_memories_v3(
    payload: ListMemoriesV3Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
) -> Dict[str, Any]:
    """
    兼容官方客户端 getAll() 发送的 POST 检索列表请求。
    """
    m = get_memory_instance()
    if not m:
        raise HTTPException(status_code=503, detail="Memory engine not initialized")

    filters = payload.filters or {}
    user_id = filters.get("user_id")
    agent_id = filters.get("agent_id")
    run_id = filters.get("run_id")
    app_id = filters.get("app_id")

    try:
        raw_list = m.get_all(user_id=user_id, agent_id=agent_id, run_id=run_id)
        memories = raw_list if isinstance(raw_list, list) else (raw_list.get("results") or [])

        # 如果指定了项目标识，在内存中完成二次过滤，避免不同 Git 项目记忆互相干扰
        if app_id:
            memories = [
                mem for mem in memories
                if (mem.get("metadata", {}).get("app_id") == app_id or
                    mem.get("metadata", {}).get("appId") == app_id)
            ]

        total = len(memories)
        start = (page - 1) * page_size
        end = start + page_size
        paged_results = memories[start:end]

        return {
            "results": paged_results,
            "count": total,
            "next": None,
            "previous": None,
        }
    except Exception as e:
        logger.exception("Failed to list memories in v3 compatibility router: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/memories/search/", summary="官方客户端语义检索")
async def search_memories_v3(payload: SearchMemoryV3Request) -> Dict[str, Any]:
    """
    处理客户端的高维向量语义检索。
    """
    m = get_memory_instance()
    if not m:
        raise HTTPException(status_code=503, detail="Memory engine not initialized")

    filters = payload.filters or {}
    user_id = filters.get("user_id")
    agent_id = filters.get("agent_id")
    run_id = filters.get("run_id")
    app_id = filters.get("app_id")

    try:
        # 调用底层向量检索能力
        search_res = m.search(
            query=payload.query,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            limit=10,
        )

        results = search_res if isinstance(search_res, list) else (search_res.get("results") or [])

        # 项目维度过滤
        if app_id:
            results = [
                item for item in results
                if (item.get("metadata", {}).get("app_id") == app_id or
                    item.get("metadata", {}).get("appId") == app_id)
            ]

        # 相似度阈值过滤，剔除低相关性噪声
        if payload.threshold is not None:
            results = [
                item for item in results
                if item.get("score", item.get("similarity", 1.0)) >= payload.threshold
            ]

        return {"results": results}
    except Exception as e:
        logger.exception("Failed to search memories in v3 compatibility router: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/v1/memories/{memory_id}/", summary="修改单条记忆内容")
async def update_memory_v1(memory_id: str, payload: UpdateMemoryV1Request) -> Dict[str, Any]:
    m = get_memory_instance()
    if not m:
        raise HTTPException(status_code=503, detail="Memory engine not initialized")

    try:
        res = m.update(memory_id=memory_id, data=payload.text)
        return {"message": "Memory updated successfully", "result": res}
    except Exception as e:
        logger.exception("Failed to update memory %s: %s", memory_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/v1/memories/{memory_id}/", summary="按 ID 删除单条记忆")
async def delete_memory_v1(memory_id: str) -> Dict[str, Any]:
    m = get_memory_instance()
    if not m:
        raise HTTPException(status_code=503, detail="Memory engine not initialized")

    try:
        m.delete(memory_id=memory_id)
        return {"message": "Memory deleted successfully"}
    except Exception as e:
        logger.exception("Failed to delete memory %s: %s", memory_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/v1/memories/", summary="按条件批量删除记忆")
async def delete_all_memories_v1(
    user_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
) -> Dict[str, Any]:
    m = get_memory_instance()
    if not m:
        raise HTTPException(status_code=503, detail="Memory engine not initialized")

    if not any([user_id, agent_id, run_id]):
        raise HTTPException(status_code=400, detail="At least one identifier (user_id/agent_id/run_id) is required")

    try:
        m.delete_all(user_id=user_id, agent_id=agent_id, run_id=run_id)
        return {"message": "All matching memories deleted successfully"}
    except Exception as e:
        logger.exception("Failed to delete all memories: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
