"""腾讯 SkillHub 技能市场客户端：搜索 / 下载 / 安装技能包到 Agent 目录

腾讯 SkillHub（https://skillhub.cn）公开 API（无需鉴权）：
- 搜索：GET /api/skills?keyword=..&sortBy=score&pageSize=N
- 技能包下载：GET /api/v1/download?slug={slug}（302 -> zip）
技能包结构：SKILL.md（使用说明/指令）+ 可选 scripts/ 脚本
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import requests
from loguru import logger

API_BASE = "https://api.skillhub.cn"

SEARCH_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 30


class SkillHubError(Exception):
    pass


# 技能来源：SkillHub 社区/企业技能（source=community，含 enterprise）；ClawHub 技能
# 是独立来源池（source=clawhub），依赖 ClawHub 特定运行时，不适合本系统的对话 Agent。
# 因此搜索固定 source=community，天然排除 ClawHub 技能。
SEARCH_SOURCE = "community"


def search_skills(keyword: str, limit: int = 5) -> list[dict]:
    """在腾讯 SkillHub 搜索技能，返回精简字段列表（仅 SkillHub 社区/企业技能，排除 ClawHub）"""
    kw = (keyword or "").strip()
    if not kw:
        raise SkillHubError("搜索关键字不能为空")
    try:
        r = requests.get(
            f"{API_BASE}/api/skills",
            params={
                "keyword": kw,
                "sortBy": "score",
                "pageSize": max(1, int(limit)),
                "source": SEARCH_SOURCE,
            },
            timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise SkillHubError(f"SkillHub 搜索请求失败: {e}") from e
    data = r.json()
    if data.get("code") != 0:
        raise SkillHubError(f"SkillHub 返回异常: {data.get('message') or data.get('code')}")
    out = []
    for s in data.get("data", {}).get("skills", []) or []:
        ns = s.get("namespace") or {}
        out.append(
            {
                "slug": s.get("slug", ""),
                "name": s.get("name") or s.get("slug", ""),
                "description": s.get("description_zh") or s.get("description") or "",
                "category": s.get("category", ""),
                "downloads": s.get("downloads", 0),
                "tags": [t for t in (s.get("tags") or [])][:6],
                "namespace": ns.get("canonicalName", ""),
                "source": s.get("source", ""),
                "requires_api_key": (s.get("labels") or {}).get("requires_api_key", "false"),
            }
        )
    return out


def download_skill_zip(slug: str) -> bytes:
    """下载技能包 zip（跟随 302 重定向）"""
    s = (slug or "").strip()
    if not s:
        raise SkillHubError("技能 slug 不能为空")
    try:
        r = requests.get(
            f"{API_BASE}/api/v1/download",
            params={"slug": s},
            timeout=DOWNLOAD_TIMEOUT,
            allow_redirects=True,
        )
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise SkillHubError(f"技能包下载失败: {e}") from e
    if "zip" not in (r.headers.get("content-type") or ""):
        raise SkillHubError(f"技能 {s} 不存在或不是可下载的 zip 包")
    return r.content


def install_skill(slug: str, agent_skills_dir: Path) -> dict:
    """下载技能包并安全解压到 Agent 的技能目录，返回 SKILL.md 与文件清单"""
    s = (slug or "").strip().replace("/", "_").replace("\\", "_")
    if not s:
        raise SkillHubError("技能 slug 不能为空")
    content = download_skill_zip(slug)
    target = agent_skills_dir / s
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for member in zf.namelist():
                dest = (target / member).resolve()
                if not str(dest).startswith(str(target.resolve())):
                    logger.warning(f"SkillHub 技能包含非法路径，已跳过: {member}")
                    continue
                if member.endswith("/"):
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))
    except zipfile.BadZipFile as e:
        raise SkillHubError(f"技能包解压失败（非有效 zip）: {e}") from e

    files = [p.name for p in sorted(target.iterdir()) if p.is_file()]
    skill_md_path = target / "SKILL.md"
    skill_md = ""
    if skill_md_path.exists():
        skill_md = skill_md_path.read_text(encoding="utf-8", errors="ignore")
    return {
        "slug": s,
        "dir": str(target),
        "files": files,
        "skill_md": skill_md,
    }


def read_skill_md(agent_skills_dir: Path, slug: str, limit_chars: int = 4000) -> str:
    """读取已安装技能的 SKILL.md 内容（未安装返回空）"""
    s = (slug or "").strip().replace("/", "_").replace("\\", "_")
    if not s:
        return ""
    p = agent_skills_dir / s / "SKILL.md"
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="ignore")
    if len(text) > limit_chars:
        text = text[:limit_chars] + "\n...(内容过长已截断)..."
    return text


def skill_summary(skill_md: str, limit: int = 3000) -> str:
    """从 SKILL.md 提取给 LLM 的关键说明（去 YAML frontmatter，截断）"""
    if not skill_md:
        return ""
    body = skill_md
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
    body = body.strip()
    if len(body) > limit:
        body = body[:limit] + "\n...(内容过长已截断)..."
    return body
