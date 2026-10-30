from __future__ import annotations

import asyncio
import os
import sys

import config
from watch_cards import cards_from_bilibili, cards_from_douyin, cards_from_xhs, classify_failure, xhs_creator


PLATFORMS = (
    ("xiaohongshu", "xhs"),
    ("douyin", "dy"),
    ("bilibili", "bili"),
)
GAP_SECONDS = 8
# 每个平台只保留一个爬虫和它打开的 Chrome。下一轮复用，避免再启动浏览器去抢登录目录。
_crawlers: dict = {}


def _clear_proxy_env() -> None:
    for key in (
        "ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "SOCKS_PROXY", "socks_proxy", "ALL_PROXY",
    ):
        os.environ.pop(key, None)


def _grpc():
    try:
        import grpc
    except ImportError as exc:
        raise SystemExit("采集进程需要 grpcio：在 MediaCrawler 目录执行 uv add grpcio protobuf") from exc
    from watchpb import watch_pb2

    address = os.environ.get("WATCH_GRPC_ADDR", "127.0.0.1:19090")
    token = os.environ.get("WATCH_INGEST_TOKEN", "")
    if not token:
        raise SystemExit("请设置 WATCH_INGEST_TOKEN")
    channel = grpc.insecure_channel(address)
    metadata = (("authorization", f"Bearer {token}"),)

    def call(method: str, request, response):
        return channel.unary_unary(
            f"/watch.v1.CreatorWatch/{method}",
            request_serializer=request.SerializeToString,
            response_deserializer=response.FromString,
        )

    return watch_pb2, metadata, {
        "PullTargets": call("PullTargets", watch_pb2.PullTargetsRequest, watch_pb2.PullTargetsResponse),
        "ReportItems": call("ReportItems", watch_pb2.ReportItemsRequest, watch_pb2.ReportItemsResponse),
        "ReportHealth": call("ReportHealth", watch_pb2.ReportHealthRequest, watch_pb2.ReportHealthResponse),
    }


async def _fetch(crawler, platform: str, target) -> list[dict]:
    if platform == "bilibili":
        payload = await crawler.bili_client.get_creator_videos(str(target.external_id), 1, 30)
        return cards_from_bilibili(payload)
    if platform == "douyin":
        payload = await crawler.dy_client.get_user_aweme_posts(target.external_id, "")
        return cards_from_douyin(payload)
    user_id, token, source = xhs_creator(target.profile_url)
    if not user_id or not token or not source:
        raise RuntimeError("xsec_token expired")
    payload = await crawler.xhs_client.get_notes_by_creator(user_id, "", page_size=20, xsec_token=token, xsec_source=source)
    return cards_from_xhs(payload, target.profile_url)


def _driver_disconnected(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        mark in text
        for mark in ("connection closed", "pipe closed", "target closed", "browser has been closed")
    )


async def _forget_watch_browser(crawler) -> None:
    """丢掉已经断开的 Playwright 通道。Chrome 进程还在的话，下一轮只重新连接。"""
    crawler.browser_context = None
    crawler.context_page = None
    crawler.cdp_manager = None
    playwright = getattr(crawler, "_watch_playwright", None)
    crawler._watch_playwright = None
    if playwright is None:
        return
    try:
        await playwright.stop()
    except Exception as exc:
        print(f"[watch] 关闭失效的浏览器控制通道: {exc}", file=sys.stderr)


def _keep_process_alive() -> None:
    """登录失败在 MediaCrawler 里会调用 sys.exit()。监听进程只停当前平台。"""
    os.environ["WATCH_MODE"] = "1"

    def _exit(code=0):
        raise RuntimeError(f"login required ({code})")

    sys.exit = _exit


async def _pass_platform(pb2, metadata, rpcs, saas_platform: str, crawler_platform: str) -> None:
    from main import CrawlerFactory

    pulled = rpcs["PullTargets"](
        pb2.PullTargetsRequest(platform=saas_platform, limit=5, worker_id=f"mediacrawler-{saas_platform}"),
        metadata=metadata,
        timeout=30,
    )
    if not pulled.targets:
        print(f"[watch] {saas_platform} 没有到期的创作者，不打开浏览器", file=sys.stderr)
        return

    config.PLATFORM = crawler_platform
    config.CRAWLER_TYPE = "watch"
    config.ENABLE_GET_COMMENTS = False
    config.ENABLE_GET_SUB_COMMENTS = False
    config.ENABLE_GET_MEDIA = False
    crawler = _crawlers.get(crawler_platform)
    if crawler is None:
        crawler = CrawlerFactory.create_crawler(crawler_platform)
        crawler.watch_mode = True
        _crawlers[crawler_platform] = crawler

    async def watch_run():
        for target in pulled.targets:
            try:
                cards = await _fetch(crawler, saas_platform, target)
            except Exception as exc:
                code = classify_failure(saas_platform, str(exc))
                if code:
                    rpcs["ReportHealth"](
                        pb2.ReportHealthRequest(platform=saas_platform, creator_id=target.creator_id, code=code, message=str(exc)[:500]),
                        metadata=metadata,
                        timeout=30,
                    )
                    if code != "token_expired":
                        return
                    continue
                print(f"[watch] {saas_platform} {target.external_id} transient: {exc}", file=sys.stderr)
                continue
            items = [
                pb2.ContentItem(
                    content_external_id=card["content_external_id"],
                    title=card["title"],
                    url=card["url"],
                    published_at_unix=int(card["published_at_unix"] or 0),
                )
                for card in cards
            ]
            rpcs["ReportItems"](
                pb2.ReportItemsRequest(
                    creator_id=target.creator_id,
                    platform=target.platform or saas_platform,
                    external_id=target.external_id,
                    worker_id=f"mediacrawler-{saas_platform}",
                    items=items,
                ),
                metadata=metadata,
                timeout=30,
            )
            await asyncio.sleep(GAP_SECONDS)

    crawler.watch_run = watch_run
    try:
        await crawler.start()
    except Exception as exc:
        if _driver_disconnected(exc):
            print(f"[watch] {saas_platform} 浏览器控制通道已断开，重新连接原窗口", file=sys.stderr)
            await _forget_watch_browser(crawler)
            try:
                await crawler.start()
                return
            except Exception as retry_exc:
                exc = retry_exc
        code = classify_failure(saas_platform, str(exc))
        if code is None and ("login required" in str(exc).lower() or "登录" in str(exc)):
            code = "login_required"
        if code is None:
            print(f"[watch] {saas_platform} did not start: {exc}", file=sys.stderr)
            return
        rpcs["ReportHealth"](
            pb2.ReportHealthRequest(platform=saas_platform, code=code, message=str(exc)[:500]),
            metadata=metadata,
            timeout=30,
        )


async def main() -> None:
    _clear_proxy_env()
    _keep_process_alive()
    pb2, metadata, rpcs = _grpc()
    while True:
        for saas_platform, crawler_platform in PLATFORMS:
            try:
                await _pass_platform(pb2, metadata, rpcs, saas_platform, crawler_platform)
            except Exception as exc:
                print(f"[watch] {saas_platform} pass failed: {exc}", file=sys.stderr)
            await asyncio.sleep(GAP_SECONDS)
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
