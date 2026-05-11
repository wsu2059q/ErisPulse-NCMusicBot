import re
import asyncio
import aiohttp
from typing import List, Optional
from datetime import datetime

from ErisPulse import sdk
from ErisPulse.Core.Bases import BaseModule
from ErisPulse.Core.Event import command


class Main(BaseModule):
    def __init__(self):
        self.sdk = sdk
        self.logger = sdk.logger.get_child("MusicBot")
        self._config = self._load_config()
        self._session: Optional[aiohttp.ClientSession] = None

    @staticmethod
    def get_load_strategy():
        from ErisPulse.loaders import ModuleLoadStrategy
        return ModuleLoadStrategy(lazy_load=False, priority=10)

    def _load_config(self) -> dict:
        config = self.sdk.config.getConfig("MusicBot")
        if not config:
            default = {
                "api_base_url": "http://localhost:3000",
                "search_limit": 30,
            }
            self.sdk.config.setConfig("MusicBot", default)
            self.logger.warning("已创建默认配置，请根据需要修改 config.toml 中的 [MusicBot] 段")
            return default
        return config

    @property
    def _base_url(self) -> str:
        return self._config.get("api_base_url", "http://localhost:3000").rstrip("/")

    @property
    def _search_limit(self) -> int:
        return int(self._config.get("search_limit", 30))

    async def on_load(self, event):
        self._session = aiohttp.ClientSession()

        @command("music", aliases=["点歌", "音乐"], help="搜索并点歌")
        async def music_cmd(evt):
            await self._handle_music(evt)

        @command("playlist", aliases=["歌单"], help="搜索歌单")
        async def playlist_cmd(evt):
            await self._handle_playlist(evt)

        self.logger.info("MusicBot 模块已加载")

    async def on_unload(self, event):
        if self._session and not self._session.closed:
            await self._session.close()
        self.logger.info("MusicBot 模块已卸载")

    async def _request(self, path: str, params: dict = None) -> Optional[dict]:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.error(f"API 请求失败: {url} status={resp.status}")
                    return None
                return await resp.json()
        except asyncio.TimeoutError:
            self.logger.error(f"API 请求超时: {url}")
            return None
        except aiohttp.ClientError as e:
            self.logger.error(f"API 请求错误: {e}")
            return None

    async def _search_songs(self, keywords: str, limit: int = None, offset: int = 0) -> List[dict]:
        data = await self._request("/search", {
            "keywords": keywords,
            "limit": limit or self._search_limit,
            "offset": offset,
        })
        if data and data.get("code") == 200:
            return data.get("result", {}).get("songs", [])
        return []

    async def _search_playlists(self, keywords: str, limit: int = None, offset: int = 0) -> List[dict]:
        data = await self._request("/search", {
            "keywords": keywords,
            "limit": limit or self._search_limit,
            "offset": offset,
            "type": 1000,
        })
        if data and data.get("code") == 200:
            return data.get("result", {}).get("playlists", [])
        return []

    async def _get_song_url(self, song_id: int) -> Optional[str]:
        data = await self._request("/song/url/v1", {"id": song_id, "level": "exhigh"})
        if data and data.get("code") == 200:
            songs = data.get("data", [])
            if songs:
                return songs[0].get("url")
        return None

    async def _get_song_detail(self, song_id: int) -> dict:
        data = await self._request("/song/detail", {"ids": song_id})
        if data and data.get("code") == 200:
            songs = data.get("songs", [])
            return songs[0] if songs else {}
        return {}

    async def _get_comment_total(self, song_id: int) -> int:
        data = await self._request("/comment/music", {"id": song_id, "limit": 1})
        if data:
            total = data.get("total")
            if isinstance(total, int):
                return total
        return 0

    async def _get_playlist_detail(self, playlist_id: int) -> dict:
        data = await self._request("/playlist/detail", {"id": playlist_id})
        if data and data.get("code") == 200:
            return data.get("playlist", {})
        return {}

    async def _get_playlist_tracks(self, playlist_id: int, limit: int = None, offset: int = 0) -> List[dict]:
        data = await self._request("/playlist/track/all", {
            "id": playlist_id,
            "limit": limit or self._search_limit,
            "offset": offset,
        })
        if data:
            songs = data.get("songs")
            if isinstance(songs, list):
                return songs
        return []

    @staticmethod
    def _format_artist_names(artists) -> str:
        if not artists:
            return "未知"
        return "/".join(a.get("name", "") for a in artists if isinstance(a, dict))

    def _format_song_list(self, songs: List[dict], page: int) -> str:
        lines = [f"共 {len(songs)} 条结果（第 {page} 页），请回复数字选择歌曲"]
        for i, song in enumerate(songs, 1):
            name = song.get("name", "未知")
            artists = song.get("artists") or song.get("ar") or []
            lines.append(f"{i}. {name} - {self._format_artist_names(artists)}")
        lines.append("\n回复 \"列表 N\" 翻页")
        return "\n".join(lines)

    def _format_playlist_list(self, playlists: List[dict], page: int) -> str:
        lines = [f"共 {len(playlists)} 条结果（第 {page} 页），请回复数字选择歌单"]
        for i, pl in enumerate(playlists, 1):
            name = pl.get("name", "未知")
            creator = pl.get("creator") or {}
            nickname = creator.get("nickname", "") if isinstance(creator, dict) else ""
            count = pl.get("trackCount")
            count_str = f" ({count}首)" if isinstance(count, int) else ""
            lines.append(f"{i}. {name} - {nickname}{count_str}")
        lines.append("\n回复 \"列表 N\" 翻页")
        return "\n".join(lines)

    async def _format_song_detail(self, song_id: int, basic: dict = None) -> str:
        detail = await self._get_song_detail(song_id)
        if not detail:
            detail = basic or {}
        comment_total = await self._get_comment_total(song_id)

        name = detail.get("name", "")
        main_title = detail.get("mainTitle", "")
        artists = detail.get("ar") or detail.get("artists") or []
        album = detail.get("al") or detail.get("album") or {}
        album_name = album.get("name", "") if isinstance(album, dict) else ""
        cover_url = album.get("picUrl", "") if isinstance(album, dict) else ""
        pop = detail.get("pop")
        pop_str = str(int(pop)) if isinstance(pop, (int, float)) else ""

        lines = [f"歌名：{name}"]
        if main_title:
            lines.append(f"主标题：{main_title}")
        lines.append(f"歌手：{self._format_artist_names(artists)}")
        if album_name:
            lines.append(f"专辑：{album_name}")
        if pop_str:
            lines.append(f"热度：{pop_str}")
        lines.append(f"评论数：{comment_total}")
        if cover_url:
            lines.append(f"封面：{cover_url}")
        return "\n".join(lines)

    @staticmethod
    def _format_playlist_detail(detail: dict) -> str:
        name = detail.get("name", "")
        creator = detail.get("creator") or {}
        nickname = creator.get("nickname", "") if isinstance(creator, dict) else ""
        create_time = detail.get("createTime")
        create_str = ""
        if create_time:
            try:
                create_str = datetime.fromtimestamp(int(create_time) / 1000).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        cover_url = detail.get("coverImgUrl", "")

        lines = [f"歌单：{name}", f"创建者：{nickname}"]
        if create_str:
            lines.append(f"创建时间：{create_str}")
        if cover_url:
            lines.append(f"封面：{cover_url}")
        lines.append("\n回复 Y 查看歌单内歌曲，N 取消")
        return "\n".join(lines)

    async def _send_audio(self, event, song_id: int, song_name: str):
        url = await self._get_song_url(song_id)
        if not url:
            await event.reply("获取歌曲链接失败，可能需要 VIP 或歌曲已下架")
            return

        platform = event.get_platform()
        try:
            send_methods = self.sdk.adapter.list_sends(platform)
        except Exception:
            send_methods = []

        if "Voice" in send_methods:
            try:
                await event.reply(url, method="Voice")
                return
            except Exception as e:
                self.logger.warning(f"语音发送失败，降级为文本: {e}")

        await event.reply(f"{song_name}\n{url}")

    async def _handle_music(self, event):
        try:
            args = event.get_command_args()
            if not args:
                await event.reply("请输入歌曲名称，例如：/music 周杰伦")
                return

            keywords = " ".join(args)
            await event.reply(f"正在搜索：{keywords}...")

            songs = await self._search_songs(keywords)
            if not songs:
                await event.reply("未找到相关歌曲")
                return

            page = 1
            offset = 0
            while True:
                if page > 1:
                    songs = await self._search_songs(keywords, offset=offset)
                    if not songs:
                        await event.reply("没有更多结果了")
                        break

                await event.reply(self._format_song_list(songs, page))

                reply = await event.wait_reply(timeout=120)
                if reply is None:
                    await event.reply("选择超时，已取消")
                    break

                msg = reply.get_text().strip()
                page_match = re.match(r"^列表\s*(\d+)$", msg)
                if page_match:
                    page = int(page_match.group(1))
                    offset = (page - 1) * self._search_limit
                    continue

                if msg.isdigit():
                    choice = int(msg)
                    if 1 <= choice <= len(songs):
                        selected = songs[choice - 1]
                        song_id = selected.get("id")
                        song_name = selected.get("name", "未知")
                        await event.reply(await self._format_song_detail(song_id, selected))
                        await self._send_audio(event, song_id, song_name)
                        break
                    else:
                        await event.reply(f"无效选择，请输入 1-{len(songs)} 之间的数字")
                        continue
                else:
                    break
        except Exception as e:
            self.logger.error(f"点歌处理失败: {e}")
            await event.reply("处理失败，请稍后重试")

    async def _handle_playlist(self, event):
        try:
            args = event.get_command_args()
            if not args:
                await event.reply("请输入歌单关键词，例如：/playlist 华语流行")
                return

            keywords = " ".join(args)
            await event.reply(f"正在搜索歌单：{keywords}...")

            playlists = await self._search_playlists(keywords)
            if not playlists:
                await event.reply("未找到相关歌单")
                return

            page = 1
            offset = 0
            while True:
                if page > 1:
                    playlists = await self._search_playlists(keywords, offset=offset)
                    if not playlists:
                        await event.reply("没有更多结果了")
                        break

                await event.reply(self._format_playlist_list(playlists, page))

                reply = await event.wait_reply(timeout=120)
                if reply is None:
                    await event.reply("选择超时，已取消")
                    break

                msg = reply.get_text().strip()
                page_match = re.match(r"^列表\s*(\d+)$", msg)
                if page_match:
                    page = int(page_match.group(1))
                    offset = (page - 1) * self._search_limit
                    continue

                if msg.isdigit():
                    choice = int(msg)
                    if 1 <= choice <= len(playlists):
                        await self._handle_playlist_selected(event, playlists[choice - 1])
                        break
                    else:
                        await event.reply(f"无效选择，请输入 1-{len(playlists)}")
                        continue
                else:
                    break
        except Exception as e:
            self.logger.error(f"歌单处理失败: {e}")
            await event.reply("处理失败，请稍后重试")

    async def _handle_playlist_selected(self, event, selected: dict):
        try:
            playlist_id = selected.get("id")
            if not playlist_id:
                await event.reply("歌单信息异常")
                return

            detail = await self._get_playlist_detail(int(playlist_id))
            if detail:
                await event.reply(self._format_playlist_detail(detail))
            else:
                await event.reply(f"歌单：{selected.get('name', '未知')}\n\n回复 Y 查看歌曲，N 取消")

            confirm = await event.wait_reply(timeout=60)
            if confirm is None:
                await event.reply("操作超时")
                return

            if confirm.get_text().strip().upper() != "Y":
                await event.reply("已取消")
                return

            playlist_name = detail.get("name") or selected.get("name", "")
            tracks = await self._get_playlist_tracks(int(playlist_id))
            if not tracks:
                await event.reply("无法获取歌单曲目")
                return

            track_page = 1
            track_offset = 0
            while True:
                if track_page > 1:
                    tracks = await self._get_playlist_tracks(int(playlist_id), offset=track_offset)
                    if not tracks:
                        await event.reply("没有更多歌曲了")
                        break

                track_text = f"歌单：{playlist_name}\n共 {len(tracks)} 首（第 {track_page} 页），请回复数字选择歌曲\n\n"
                for i, song in enumerate(tracks, 1):
                    name = song.get("name", "未知")
                    artists = song.get("artists") or song.get("ar") or []
                    track_text += f"{i}. {name} - {self._format_artist_names(artists)}\n"
                track_text += "\n回复 \"列表 N\" 翻页"

                await event.reply(track_text)

                track_reply = await event.wait_reply(timeout=120)
                if track_reply is None:
                    await event.reply("选择超时，已取消")
                    break

                track_msg = track_reply.get_text().strip()
                tp_match = re.match(r"^列表\s*(\d+)$", track_msg)
                if tp_match:
                    track_page = int(tp_match.group(1))
                    track_offset = (track_page - 1) * self._search_limit
                    continue

                if track_msg.isdigit():
                    tc = int(track_msg)
                    if 1 <= tc <= len(tracks):
                        song = tracks[tc - 1]
                        song_id = song.get("id")
                        song_name = song.get("name", "未知")
                        await event.reply(await self._format_song_detail(song_id, song))
                        await self._send_audio(event, song_id, song_name)
                        break
                    else:
                        await event.reply(f"无效选择，请输入 1-{len(tracks)}")
                        continue
                else:
                    break
        except Exception as e:
            self.logger.error(f"歌单详情处理失败: {e}")
            await event.reply("处理失败，请稍后重试")
