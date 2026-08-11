"""牛客网面经 Spider。

改造后的核心逻辑：
- 搜索牛客面经帖子（search API）
- 抓取 Feed / Discuss 帖子正文
- 保留 uuid / content_id 作为 source_id

本模块仅负责 HTTP 层面的搜索与抓取，不涉及 MCP 协议、缓存或业务理解。

改造要点：
- 去除 CLI argparse、config.json、output JSON 文件交换
- 以 max_results 替代 max_pages 作为主控参数（hard cap 20）
- 单篇帖子失败不中断整批抓取
- 瞬时网络错误最多重试一次
- 统一使用 logging 输出到 stderr
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

# ====================== API 常量 ======================

#搜索接口
SEARCH_API = "https://gw-c.nowcoder.com/api/sparta/pc/search"
#讨论详情接口
DISCUSS_API = "https://gw-c.nowcoder.com/api/sparta/detail/content-data/detail"
#动态详情接口，正则提取标题+正文
FEED_URL = "https://www.nowcoder.com/feed/main/detail"
#固定查询页数（2 页足够覆盖最新面经，减少发现帖子量 → 减少抓取耗时）
SEARCH_PAGE_LIMIT = 2

HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

# ====================== HTML → 纯文本 ======================

def html_to_text(html: str) -> str:
    """将 HTML 转为纯文本，去除标签和脚本。"""
    if not html:
        return ""
    html = re.sub(
        r"<script[^>]*>.*?</script>", "", html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(
        r"<style[^>]*>.*?</style>", "", html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(
        r"</?(p|div|br|h[1-6]|li|tr)[^>]*>", "\n", html,
        flags=re.IGNORECASE,
    )
    html = re.sub(r"<[^>]+>", "", html)
    for old, new in [
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
        ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
        ("\xa0", " "),
    ]:
        html = html.replace(old, new)
    html = re.sub(r"\n\s*\n", "\n\n", html)
    return html.strip()


# ====================== 内部模型 ======================

@dataclass(frozen=True, slots=True)
class RawNowcoderPost:
    """牛客 Spider 返回的原始帖子数据。

    这是 Spider 的内部模型，后续由 MCP Server / Provider
    转换为领域模型 `RawInterviewExperience`。
    """

    source_id: str       # 牛客 uuid（Feed）或 content_id（Discuss）
    source_type: str     # "feed" / "discuss"
    title: str = ""
    content: str = ""
    url: str = ""
    matched_query: str = ""


@dataclass(frozen=True, slots=True)
class SpiderResult:
    """Spider 搜索 + 抓取的完整结果。"""

    posts: list[RawNowcoderPost] = field(default_factory=list)
    discovered_count: int = 0   # 搜索发现的总帖子数（去重后）
    collected_count: int = 0    # 成功抓取的帖子数（= len(posts)）
    failed_count: int = 0       # 抓取失败的帖子数

    @property
    def partial(self) -> bool:
        """部分失败：有失败但仍有成功数据可消费。"""
        return self.failed_count > 0 and self.collected_count > 0


# ====================== Spider ======================

class NowcoderSpider:
    """牛客网面经 Spider。

    负责：
    1. 搜索牛客面经帖子（search API）
    2. 逐篇拉取正文（Feed / Discuss API）
    3. len(collected) >= max_results 时提前停止
    4. 单篇失败容错 + 瞬时网络错误最多一次重试，失败就降级
    """

    def __init__(
        self,
        max_results: int = 10,
        request_delay: float = 0.5,
        request_timeout: int = 10,
        *,
        max_fetch_attempts: int | None = None,
    ) -> None:
        self._max_pages = SEARCH_PAGE_LIMIT
        self._max_results = min(max(1, max_results), 20)  # hard cap: 20
        # 最多尝试抓取 N 篇帖子（含失败），默认 max_results × 2.5
        self._max_fetch_attempts = max_fetch_attempts or max(1, int(self._max_results * 2.5))
        self._request_delay = request_delay
        self._request_timeout = request_timeout
        self._session = requests.Session()
        self._session.headers.update(HEADERS)

    # ---- 主入口 ----

    def search_and_collect(self, queries: list[str]) -> SpiderResult:
        """搜索并抓取面经正文。

        Args:
            queries: 搜索关键词列表，例如 ``["字节跳动 Agent开发 面经"]``。
        Returns:
            SpiderResult 包含所有成功抓取的帖子及统计信息。
        """

        # 搜索去重
        all_hits: dict[str, dict] = {}
        for q in queries:
            #帖子原信息列表
            hits = self._search(q)
            for hit in hits:
                key = hit["source_id"]
                #去重
                if key and key not in all_hits:
                    all_hits[key] = hit
            if len(queries) > 1:
                time.sleep(1)  # 多关键词间短暂间隔

        discovered = len(all_hits)
        logger.info(
            "NowcoderSpider: %d queries, discovered %d unique posts",
            len(queries), discovered,
        )

        # 逐篇抓取正文
        collected: list[RawNowcoderPost] = []
        failed = 0
        for attempts, (_key, hit) in enumerate(all_hits.items(), start=1):

            # 抓取尝试次数达到硬上限时停止（防止大量无效帖子拖垮超时）
            if attempts > self._max_fetch_attempts:
                logger.info(
                    "NowcoderSpider: reached max_fetch_attempts=%d (collected=%d, failed=%d), stop fetching",
                    self._max_fetch_attempts, len(collected), failed,
                )
                break

            #获取单篇帖子
            post = self._fetch_one(hit)

            #正文不为空+长度足够有效
            if post is not None and len(post.content) > 80:
                collected.append(post)
                #超出范围
                if len(collected) >= self._max_results:
                    logger.info(
                        "NowcoderSpider: reached max_results=%d, stop fetching",
                        self._max_results,
                    )
                    break
            #正文不符合要求
            else:
                failed += 1

            time.sleep(self._request_delay)

        logger.info(
            "NowcoderSpider: collected=%d, failed=%d, discovered=%d",
            len(collected), failed, discovered,
        )

        return SpiderResult(
            posts=collected,
            discovered_count=discovered,
            collected_count=len(collected),
            failed_count=failed,
        )

    # ---- 搜索 ----

    def _search(self, query: str) -> list[dict]:
        """搜索牛客面经，返回帖子元信息列表。

        Returns:
            list of dicts，每个 dict 包含:
            source_id, source_type, title, matched_query
        """
        results: list[dict] = []
        seen: set[str] = set()

        for page in range(1, self._max_pages + 1):
            try:
                payload = {
                    "type": "all",
                    "query": query,
                    "page": page,
                    #818是后台数据库面经tag的主键ID，通过逆向前端搜索请求发现的固定值
                    "tag": [{"name": "面经", "id": 818, "count": None}],
                    #按照最新发布时间排序
                    "order": "create",
                    "gioParams": {
                        "searchFrom_var": "顶部导航栏", #来源页面
                        "searchEnter_var": "主站",  #入口站点
                    },
                }
                response = self._session.post(
                    SEARCH_API, json=payload,
                    timeout=self._request_timeout,
                )
                data = response.json()

                if not data.get("success"):
                    logger.warning(
                        "NowcoderSpider search page=%d: API returned success=false", page,
                    )
                    break

                records = data.get("data", {}).get("records", [])
                if not records:
                    break

                for r in records:
                    rc_type = r.get("rc_type", 0)
                    rd = r.get("data", {})

                    if rc_type == 201:  # Feed（动态）类型
                        md = rd.get("momentData", {})
                        if md:
                            uid = md.get("uuid", "")
                            #去重
                            if uid and uid not in seen:
                                seen.add(uid)
                                results.append({
                                    "source_id": uid,
                                    "source_type": "feed",
                                    "title": md.get("title", ""),
                                    "matched_query": query,
                                })

                    elif rc_type == 207:  # Discuss（讨论）类型
                        cd = rd.get("contentData", {})
                        if cd:
                            cid = str(cd.get("id", ""))
                            #去重
                            if cid and cid not in seen:
                                seen.add(cid)
                                results.append({
                                    "source_id": cid,
                                    "source_type": "discuss",
                                    "title": cd.get("title", ""),
                                    "matched_query": query,
                                })

                # 判断是否还有下一页
                total_page = data.get("data", {}).get("totalPage", 1)
                if total_page <= 0 or page >= total_page:
                    break

                time.sleep(self._request_delay)

            except Exception:
                logger.exception("NowcoderSpider search page=%d error", page)
                break

        return results

    # ---- 单篇抓取 ----

    def _fetch_one(self, hit: dict) -> RawNowcoderPost | None:
        """根据搜索命中抓取单篇帖子正文。

        瞬时网络错误最多重试一次；其他错误（内容不存在等）
        不重试直接返回 None。
        """
        source_type = hit["source_type"]
        source_id = hit["source_id"]

        #fetch discuss/feed类型，对应不同函数
        fetch_func: Callable[[str], RawNowcoderPost | None]
        fetch_func = self._fetch_feed if source_type == "feed" else self._fetch_discuss

        # 第一次尝试
        try:
            post = fetch_func(source_id)
            if post is not None:
                return post
        except (requests.ConnectionError, requests.Timeout):
            # 瞬时网络错误：重试一次
            logger.debug(
                "NowcoderSpider: transient error for %s %s, retrying...",
                source_type, source_id,
            )
            time.sleep(0.5)
            # 第二次尝试
            try:
                return fetch_func(source_id)
            # 第二次尝试还出错就结束
            except Exception:
                logger.exception(
                    "NowcoderSpider: retry failed for %s %s",
                    source_type, source_id,
                )
                return None
        except Exception:
            logger.exception(
                "NowcoderSpider: fetch error for %s %s",
                source_type, source_id,
            )
            return None

        return None

    def _fetch_feed(self, uuid: str) -> RawNowcoderPost | None:
        """抓取 Feed 类型帖子正文。"""
        resp = self._session.get(
            f"{FEED_URL}/{uuid}",
            timeout=self._request_timeout,
        )
        html = resp.text

        #帖子不存在
        if "内容不存在" in html:
            logger.debug("NowcoderSpider: feed %s not found (content deleted)", uuid)
            return None

        # 提取标题
        title_m = re.search(r'"title":"([^"]+)"', html)
        title = title_m.group(1) if title_m else ""

        # 提取正文
        content = ""
        cm = re.search(
            r'<div[^>]*class="[^"]*feed-content-text[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL | re.IGNORECASE,
        )
        if cm:
            content = html_to_text(cm.group(1))

        # Fallback路径提取正文: 从 JSON 字段提取 content
        if not content:
            for m in re.findall(r'"content":"([^"]{100,})"', html):
                content = (
                    m.replace("\\n", "\n")
                    .replace("\\u002F", "/")
                    .replace("\\t", "\t")
                )
                break

        return RawNowcoderPost(
            source_id=uuid,
            source_type="feed",
            title=title,
            content=content,
            url=f"{FEED_URL}/{uuid}",
        )

    def _fetch_discuss(self, content_id: str) -> RawNowcoderPost | None:
        """抓取 Discuss 类型帖子正文。"""
        resp = self._session.get(
            f"{DISCUSS_API}/{content_id}",
            timeout=self._request_timeout,
        )
        data = resp.json()

        if not data.get("success"):
            logger.debug("NowcoderSpider: discuss %s not found", content_id)
            return None

        cd = data.get("data", {})
        rich = cd.get("richText", "") or cd.get("content", "")

        return RawNowcoderPost(
            source_id=content_id,
            source_type="discuss",
            title=cd.get("title", ""),
            content=html_to_text(rich),
            url=f"https://www.nowcoder.com/discuss/{content_id}",
        )


__all__ = [
    "NowcoderSpider",
    "RawNowcoderPost",
    "SpiderResult",
    "html_to_text",
]
